"""Bounded CPU checks and caption hints; never invokes a generative model."""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Literal
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import settings
from app.models.content_blocks import ImageMetadata
from app.models.facebook_photos import FacebookPhotoReview
from app.services.facebook_photo_profiles import PROFILES, PhotoProfile, profile_for
from app.services.image_graphics import screen_source_images_for_graphics
from app.services.image_urls import photo_identity
from app.services.image_vision import _fetch_image_bytes, _thumbnail_hash
from app.services.photo_quality import photo_quality
from app.services.text_detection import prefetch_text_flags


def _profile(meta: ImageMetadata) -> PhotoProfile:
    return profile_for(meta.selection_context) or PROFILES["facebook"]


@dataclass(frozen=True)
class PixelChecks:
    width: int
    height: int
    fingerprint: str | None
    content_hash: str
    has_text: bool | None
    ocr_text: str
    qr_payload: str | None
    is_graphic: bool | None
    quality: float | None = None

    @property
    def complete(self) -> bool:
        return self.has_text is not None and self.is_graphic is not None


# Keyed by the rendition, not the link: Facebook re-signs every photo URL on
# each read, so a URL key missed on every re-read of the same Page.
_PIXEL_CACHE: dict[str, PixelChecks] = {}
_PIXEL_CACHE_MAX = 256


def _cache_key(url: str) -> str:
    """One rendition of one picture: the filename names the picture, `stp` the
    rendition. The picture alone is not enough — a square crop and its
    portrait original share a filename and measure differently."""
    identity = photo_identity(url)
    if identity is None:
        return url
    return f"{identity}|{parse_qs(urlparse(url).query).get('stp', [''])[0]}"


def _matches(text: str, phrase: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(phrase) + r"(?!\w)", text) is not None


def caption_hints(caption: str, profile: PhotoProfile) -> tuple[list[str], bool]:
    """(subjects the caption names, whether it allows a hero). Caption hints,
    not assertions about image contents: the page-planning model receives the
    caption itself and decides the final section/ref."""
    text = " ".join(re.findall(r"\w+", caption.lower()))
    subjects = [subject.name for subject in profile.subject_hints
                if any(_matches(text, phrase) for phrase in subject.phrases)]
    excluded = any(_matches(text, phrase) for phrase in profile.hero_exclusions)
    if not profile.hero_requires_caption:
        return subjects[:8], not excluded
    activity = any(subject not in profile.setting_subjects for subject in subjects)
    return subjects[:8], activity and not excluded


def orientation_of(meta: ImageMetadata) -> Literal["landscape", "portrait", "square"]:
    """The photo's shape from its measured size; square when unmeasured."""
    if meta.width and meta.height and meta.width != meta.height:
        return "landscape" if meta.width > meta.height else "portrait"
    return "square"


def _dimensions(raw: bytes) -> tuple[int, int] | None:
    from PIL import Image

    try:
        with Image.open(BytesIO(raw)) as image:
            image.verify()
            return image.size
    except Exception:
        return None


def _stamp(meta: ImageMetadata, checks: PixelChecks) -> None:
    meta.width, meta.height = checks.width, checks.height
    meta.facebook_photo_hash = checks.fingerprint
    meta.content_hash = checks.content_hash
    meta.ocr_has_text, meta.qr_payload = checks.has_text, checks.qr_payload
    meta.ocr_text = checks.ocr_text
    meta.quality = checks.quality
    if checks.is_graphic:
        meta.role = "logo"


async def assess_photo_metadata(metadata: list[ImageMetadata]) -> None:
    """One download per uncached photo, reused for dimensions/hash/OCR/graphics."""
    # Caption-relevant activities get the limited pixel-check budget first.
    contexts = {meta.url: caption_hints(meta.caption, _profile(meta)) for meta in metadata}
    selected = sorted(metadata, key=lambda meta: (
        contexts[meta.url][1], (meta.width or 0) * (meta.height or 0),
    ), reverse=True)[:settings.facebook_photo_max_images]
    pending = []
    checks_by_url: dict[str, PixelChecks] = {}
    for meta in selected:
        checks = _PIXEL_CACHE.get(_cache_key(meta.url))
        if checks:
            checks_by_url[meta.url] = checks
            _stamp(meta, checks)
        else:
            pending.append(meta)

    sem = asyncio.Semaphore(settings.ocr_fetch_concurrency)
    async def fetch(meta: ImageMetadata, client: httpx.AsyncClient):
        async with sem:
            raw = await _fetch_image_bytes(meta.url, client=client)
        size = await asyncio.to_thread(_dimensions, raw) if raw else None
        return meta, raw, size

    async with httpx.AsyncClient(
        timeout=settings.vision_fetch_timeout_seconds, follow_redirects=True
    ) as client:
        fetched = await asyncio.gather(*(fetch(meta, client) for meta in pending))
    decoded = [(meta, raw, size) for meta, raw, size in fetched if raw and size]
    # Facebook's initial gallery role is provisional, not a measured logo wall.
    # Screen copies as content so the shared warm-up's gallery exemption cannot
    # skip assessment; stamp only measured results back onto the source below.
    pool = [meta.model_copy(update={"role": "content"}) if meta.role == "gallery" else meta
            for meta, _, _ in decoded]
    screened = {meta.url: meta for meta in pool}
    originals = {meta.url: raw for meta, raw, _ in decoded}
    # Only successfully downloaded images enter the shared screens: failures
    # are not immediately fetched again, and the event loop stays responsive.
    _, graphics = await asyncio.gather(
        prefetch_text_flags(pool, prefetched=originals, max_images=len(pool)),
        screen_source_images_for_graphics(pool, prefetched=originals, max_images=len(pool)),
    )
    for meta, raw, size in decoded:
        checks = PixelChecks(
            *size, await asyncio.to_thread(_thumbnail_hash, raw),
            hashlib.sha256(raw).hexdigest(), screened[meta.url].ocr_has_text,
            screened[meta.url].ocr_text, screened[meta.url].qr_payload, graphics.get(meta.url),
            await asyncio.to_thread(photo_quality, raw),
        )
        checks_by_url[meta.url] = checks
        _stamp(meta, checks)
        if checks.complete:
            _PIXEL_CACHE[_cache_key(meta.url)] = checks
            while len(_PIXEL_CACHE) > _PIXEL_CACHE_MAX:
                _PIXEL_CACHE.pop(next(iter(_PIXEL_CACHE)))

    for meta in metadata:
        if meta.facebook_photo_review and meta.facebook_photo_review.method == "vision":
            continue  # retain stronger existing rejections/identity evidence
        profile = _profile(meta)
        categories, hero_context = contexts[meta.url]
        checks = checks_by_url.get(meta.url)
        sections = ["gallery"]
        if any(category not in profile.setting_subjects for category in categories):
            sections += profile.activity_sections
        if any(category in profile.setting_subjects for category in categories):
            sections.append("facilities")
        meta.facebook_photo_review = FacebookPhotoReview(
            method="metadata", pixel_checks_complete=bool(checks and checks.complete),
            scores=None,  # text/pixel checks cannot measure aesthetic qualities
            caption_hero_context=hero_context, classification=categories,
            recommended_sections=sections,
            orientation=orientation_of(meta),
            decision="accept" if checks and checks.complete else "backup",
            reason="measured pixels and caption context" if checks and checks.complete
                   else "pixel checks incomplete; supporting-photo backup only",
        )
