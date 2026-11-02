"""
Opt-in vision pass: caption + classify scraped images with a multimodal
Ollama model so the matcher can rank them by what they actually depict.

Why: scraped images routinely arrive with hashed CDN filenames and empty alt
text — zero lexical evidence — so authentic photos lose to stock even when
they're the perfect fit. One bounded annotation pass fixes that and enables:

- lexical matching via `vision_caption` (image_match folds it into scoring);
- a hero-pin veto for promotional banners / screenshots / graphics;
- profile-photo verification (single-person portrait vs. logo or group shot).

Bounded by design: at most `settings.vision_max_images` images per
generation, one LLM call per image, results cached by URL for the process
lifetime. Enabled only when `settings.llm_vision_model` is set — without
it `annotate_image_pool` returns {} without any I/O. Every per-image failure
(download, decode, LLM) is swallowed: annotation is an enhancement, never
load-bearing.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import time
from io import BytesIO
from typing import TYPE_CHECKING, Literal

import httpx
from pydantic import BaseModel, Field
from pydantic.json_schema import SkipJsonSchema

from app.config import settings
from app.models.content_blocks import ImageMetadata
from app.models.facebook_photos import FacebookPhotoReview
from app.services.asset_fetch import asset_client, get_asset
from app.services.url_guard import is_public_url

if TYPE_CHECKING:
    from app.services.llm import LlmClient

logger = logging.getLogger(__name__)


VisionKind = Literal["photo", "logo", "banner", "screenshot", "graphic", "map", "other"]


class VisionAnnotation(BaseModel):
    """What the vision model saw in one image."""

    caption: str = ""
    kind: VisionKind = "other"
    people_count: int = Field(default=0, ge=0)
    is_portrait: bool = False
    # Words burned into the pixels — a headline, a tagline, a paragraph, a price
    # list. Such an image can never be a hero BACKGROUND: we lay our own
    # headline over it and the two collide into unreadable mush. It is still
    # perfectly good as a featured image or an untitled card, where nothing is
    # drawn on top. Distinct from `kind`: a real photograph of a shopfront can
    # carry a signboard full of text, and a plain illustration can carry none.
    has_text: bool = False
    facebook_photo_review: FacebookPhotoReview | None = None
    # Internal measurement, omitted from the LLM's requested JSON schema.
    photo_hash: SkipJsonSchema[str | None] = None


_JUDGE_SYSTEM = """You describe one website image for an image-matching system.
Reply ONLY with a single JSON object:
{"caption": "...", "kind": "photo|logo|banner|screenshot|graphic|map|other", "people_count": N, "is_portrait": true|false, "has_text": true|false}
- caption: one factual sentence describing subjects, setting and activity. No style commentary.
- kind: photo = a real photograph; logo = a brand mark; banner = promotional graphic with overlaid text; screenshot = software UI capture; graphic = illustration/icon/pattern; map = a map.
- people_count: number of visible people (0 if none).
- is_portrait: true only when a single person's face or head-and-shoulders is the main subject.
- has_text: true when READABLE words are part of the image — a headline, tagline, sentence, price or menu list, or a sign whose wording you can read. Judge the pixels, not the subject: a photo of a cafe whose menu board is legible is true; a photo of a cafe with no legible wording is false. Ignore tiny watermarks and photographer credits.
Do not explain. Do not return anything except the JSON object."""

# Vision input is downscaled to this box before base64 — classification and a
# one-line caption don't need more pixels, and it keeps the payload small.
_THUMBNAIL_PX = 512

# Process-lifetime cache: annotating the same URL twice is pure waste, and
# regenerations commonly reuse the same scrape. Bounded (FIFO) so a long-lived
# server processing many distinct images can't grow it without limit.
_ANNOTATION_CACHE: dict[str, VisionAnnotation] = {}
_ANNOTATION_CACHE_MAX = 512


def _reviewer_prompt(selection_context: str | None) -> str | None:
    """The photo profile's own reviewer prompt, when it has one."""
    from app.services.facebook_photo_profiles import profile_for

    profile = profile_for(selection_context)
    return profile.vision_prompt if profile else None


def _annotation_key(url: str, meta: ImageMetadata | None) -> str:
    # A generic annotation must never satisfy a profile's stronger review.
    context = meta.selection_context if meta else None
    return f"{context}:{url}" if _reviewer_prompt(context) else url


def _cache_annotation(url: str, annotation: VisionAnnotation) -> None:
    _ANNOTATION_CACHE[url] = annotation
    while len(_ANNOTATION_CACHE) > _ANNOTATION_CACHE_MAX:
        # dict preserves insertion order → first key is the oldest.
        _ANNOTATION_CACHE.pop(next(iter(_ANNOTATION_CACHE)), None)


# Max concurrent vision annotations. Bounds the parallel image downloads; the
# Ollama vision calls themselves still serialize server-side (one model slot on
# a single GPU by default), so this stays gentle on a 16GB M1.
_VISION_CONCURRENCY = 3


def vision_enabled() -> bool:
    from app.services.llm import active_vision_model  # lazy: heavy import chain

    return bool(active_vision_model())


def _vision_candidates(
    metadata: list[ImageMetadata],
    extra_urls: list[str] | None,
    max_images: int | None,
) -> tuple[list[str], dict[str, ImageMetadata]]:
    """The first `limit` distinct URLs the vision pass will judge, plus a
    url→metadata map for in-place enrichment. Shared by prefetch + annotate so
    both phases agree on the candidate set."""
    limit = max_images if max_images is not None else settings.vision_max_images
    by_url: dict[str, ImageMetadata] = {}
    ordered_urls: list[str] = []
    for item in metadata:
        if item.url and item.url not in by_url:
            by_url[item.url] = item
            ordered_urls.append(item.url)
    for url in extra_urls or []:
        if url and url not in by_url and url not in ordered_urls:
            ordered_urls.append(url)
    return ordered_urls[: max(0, limit)], by_url


async def prefetch_image_pool(
    metadata: list[ImageMetadata],
    extra_urls: list[str] | None = None,
    *,
    max_images: int | None = None,
) -> dict[str, str]:
    """Download + downscale the images the vision pass will judge; returns
    {url: base64_jpeg}. Pure network/CPU — safe to run concurrently with the
    content-generation LLM call (which owns the GPU), so the downloads come
    off the critical path. Pass the result to `annotate_image_pool` via
    `prefetched=`. URLs already in the annotation cache are skipped."""
    if not vision_enabled():
        return {}
    candidates, by_url = _vision_candidates(metadata, extra_urls, max_images)
    to_fetch = [u for u in candidates if _annotation_key(u, by_url.get(u)) not in _ANNOTATION_CACHE
                and not (by_url.get(u) and by_url[u].facebook_photo_review is not None)]
    if not to_fetch:
        return {}

    sem = asyncio.Semaphore(_VISION_CONCURRENCY)

    async def _one(url: str, http_client: httpx.AsyncClient) -> tuple[str, str | None]:
        async with sem:
            return url, await _fetch_image_b64(url, client=http_client)

    async with httpx.AsyncClient(
        timeout=settings.vision_fetch_timeout_seconds, follow_redirects=True
    ) as http_client:
        fetched = await asyncio.gather(*(_one(url, http_client) for url in to_fetch))
    return {url: b64 for url, b64 in fetched if b64 is not None}


async def annotate_image_pool(
    metadata: list[ImageMetadata],
    extra_urls: list[str] | None = None,
    *,
    llm: LlmClient | None = None,
    max_images: int | None = None,
    prefetched: dict[str, str] | None = None,
    enabled: bool | None = None,
) -> dict[str, VisionAnnotation]:
    """Annotate up to `max_images` images; returns {url: annotation}.

    `enabled` overrides the local-vision-model gate: a caller that brings its
    own multimodal client (the Claude model picked in the UI) enables the pass
    for that call alone, and the default (`LLM_VISION_MODEL`) is unchanged.

    `metadata` entries that get annotated are also enriched in place
    (vision_caption / vision_kind / vision_people / vision_portrait), so the
    pool handed to the resolver carries the captions with no extra plumbing.
    `extra_urls` (e.g. profile portraits not in the pool) are annotated too
    and appear only in the returned map. `prefetched` supplies already
    downloaded {url: base64} payloads (from `prefetch_image_pool`); anything
    missing from it is downloaded here as before.
    """
    if not (vision_enabled() if enabled is None else enabled):
        return {}

    # Cap candidates up front (the first `limit` distinct URLs), then resolve
    # them concurrently. Downloads overlap; the Ollama vision calls queue on the
    # single model slot, so we stay within a 16GB M1's budget. Per-URL failures
    # are swallowed, so we may end with fewer than `limit` annotations.
    candidates, by_url = _vision_candidates(metadata, extra_urls, max_images)
    if not candidates:
        return {}

    client = llm
    if client is None and any(
        _annotation_key(u, by_url.get(u)) not in _ANNOTATION_CACHE
        and not (by_url.get(u) and by_url[u].facebook_photo_review is not None)
        for u in candidates
    ):
        from app.services.llm import active_vision_model, get_llm  # lazy: heavy import chain

        client = get_llm(model=active_vision_model())

    sem = asyncio.Semaphore(_VISION_CONCURRENCY)

    async def _resolve(
        url: str, http_client: httpx.AsyncClient
    ) -> tuple[str, VisionAnnotation | None]:
        meta = by_url.get(url)
        key = _annotation_key(url, meta)
        cached = _ANNOTATION_CACHE.get(key)
        if meta and meta.facebook_photo_review is not None:
            # Reviews survive source-preview persistence and round-tripping.
            return url, VisionAnnotation(
                caption=meta.vision_caption or "", kind=meta.vision_kind or "other",
                people_count=meta.vision_people or 0,
                is_portrait=bool(meta.vision_portrait), has_text=bool(meta.vision_has_text),
                facebook_photo_review=meta.facebook_photo_review,
                photo_hash=meta.facebook_photo_hash,
            )
        if cached is not None:
            return url, cached
        async with sem:
            annotation = await _annotate_one(
                url,
                client,  # type: ignore[arg-type]
                http_client,
                image_b64=(prefetched or {}).get(url),
                selection_context=meta.selection_context if meta else None,
            )
        if annotation is not None:
            _cache_annotation(key, annotation)
        return url, annotation

    async with httpx.AsyncClient(
        timeout=settings.vision_fetch_timeout_seconds, follow_redirects=True
    ) as http_client:
        resolved = await asyncio.gather(
            *(_resolve(url, http_client) for url in candidates)
        )

    annotations: dict[str, VisionAnnotation] = {}
    for url, annotation in resolved:
        if annotation is None:
            continue
        annotations[url] = annotation
        item = by_url.get(url)
        if item is not None:
            item.vision_caption = annotation.caption or None
            item.vision_kind = annotation.kind
            item.vision_people = annotation.people_count
            item.vision_portrait = annotation.is_portrait
            item.vision_has_text = annotation.has_text
            item.facebook_photo_review = annotation.facebook_photo_review
            item.facebook_photo_hash = annotation.photo_hash

    return annotations


async def _annotate_one(
    url: str,
    llm: LlmClient,
    http_client: httpx.AsyncClient | None = None,
    *,
    image_b64: str | None = None,
    selection_context: str | None = None,
) -> VisionAnnotation | None:
    """Fetch + downscale + judge one image. None on any failure.
    A prefetched `image_b64` skips the download entirely."""
    if image_b64 is None:
        image_b64 = await _fetch_image_b64(url, client=http_client)
    if image_b64 is None:
        return None

    from app.services.llm import LlmError  # lazy: heavy import chain

    reviewer = _reviewer_prompt(selection_context)
    try:
        annotation = await llm.chat_json(
            system_prompt=reviewer or _JUDGE_SYSTEM,
            user_prompt="Describe this image.",
            schema=VisionAnnotation,
            temperature=settings.judge_temperature,
            images=[image_b64],
        )
        annotation.photo_hash = (
            await asyncio.to_thread(_thumbnail_hash, image_b64) if reviewer else None
        )
        return annotation
    except LlmError as exc:
        logger.warning("Vision annotation failed for %s: %s", url[:120], exc)
        return None


def _thumbnail_hash(payload: bytes | str) -> str | None:
    """Conservative resized/burst-photo fingerprint using already downloaded pixels."""
    from PIL import Image

    try:
        raw = payload if isinstance(payload, bytes) else base64.b64decode(payload)
        with Image.open(BytesIO(raw)) as image:
            pixels = list(image.convert("L").resize((9, 8), Image.Resampling.LANCZOS).getdata())
        if max(pixels) - min(pixels) < 12:
            return None  # nearly flat artwork is not reliable duplicate evidence
        bits = [pixels[y * 9 + x] > pixels[y * 9 + x + 1] for y in range(8) for x in range(8)]
        return f"{sum(int(bit) << i for i, bit in enumerate(bits)):016x}"
    except Exception:
        return None


async def _fetch_image_b64(
    url: str, client: httpx.AsyncClient | None = None
) -> str | None:
    """Download (or decode a data: URL), downscale, return base64 JPEG.

    None for anything that can't become a bitmap (SVG, HTML error pages,
    oversized files, network failures) — the caller just skips the image.

    `client`: a shared AsyncClient to reuse (connection pooling) when several
    images are fetched at once; opens a per-call client when None.
    """
    raw = await _fetch_image_bytes(url, client=client)
    if raw is None:
        return None
    # PIL decode/thumbnail is CPU-bound — thread it off so concurrent prefetch
    # downloads keep flowing while one image re-encodes.
    return await asyncio.to_thread(_downscale_to_b64, raw, url)


def _downscale_to_b64(raw: bytes, url: str = "") -> str | None:
    """Decode + downscale + re-encode as base64 JPEG. Sync/CPU on purpose —
    callers run it via asyncio.to_thread."""
    try:
        # Pillow import is cheap (already a hard dependency via logo palette).
        from PIL import Image

        with Image.open(BytesIO(raw)) as img:
            img = img.convert("RGB")
            img.thumbnail((_THUMBNAIL_PX, _THUMBNAIL_PX))
            out = BytesIO()
            img.save(out, format="JPEG", quality=85)
    except Exception:  # noqa: BLE001 — any undecodable payload is just skipped
        logger.debug("Could not decode image for vision pass: %s", url[:120])
        return None
    return base64.b64encode(out.getvalue()).decode("ascii")


# Every pixel pass downloads through `_fetch_image_bytes` — the vision prefetch,
# OCR, the graphic screen, photo sampling, the Facebook pixel pass — and each
# used to download for itself, so one photo was asked of the business's server
# up to five times per generation. These hold one copy: finished downloads by
# URL (TTL, bounded by total size, LRU by dict order), and the download in
# progress, so passes running side by side share it rather than racing it.
# Only a body that fetched is kept; a failure is never remembered, so a later
# pass may still try, exactly as before.
_BYTES_CACHE: dict[str, tuple[float, bytes]] = {}
_BYTES_IN_FLIGHT: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Task[bytes | None]]] = {}
_bytes_cached_total = 0


def clear_image_bytes_cache() -> None:
    """Forget every cached download (tests / ops)."""
    global _bytes_cached_total
    _BYTES_CACHE.clear()
    _BYTES_IN_FLIGHT.clear()
    _bytes_cached_total = 0


def _cached_bytes(url: str) -> bytes | None:
    global _bytes_cached_total
    entry = _BYTES_CACHE.pop(url, None)
    if entry is None:
        return None
    stored_at, body = entry
    if time.monotonic() - stored_at >= settings.image_bytes_cache_ttl_seconds:
        _bytes_cached_total -= len(body)
        return None
    _BYTES_CACHE[url] = entry  # re-insert: dict order is recency
    return body


def _cache_bytes(url: str, body: bytes) -> None:
    global _bytes_cached_total
    budget = settings.image_bytes_cache_mb * 1_000_000
    if len(body) > budget:
        return
    previous = _BYTES_CACHE.pop(url, None)
    if previous is not None:
        _bytes_cached_total -= len(previous[1])
    _BYTES_CACHE[url] = (time.monotonic(), body)
    _bytes_cached_total += len(body)
    while _bytes_cached_total > budget and _BYTES_CACHE:
        _bytes_cached_total -= len(_BYTES_CACHE.pop(next(iter(_BYTES_CACHE)))[1])


async def _download(
    url: str, client: httpx.AsyncClient | None, budget: float | None
) -> bytes | None:
    try:
        response = await get_asset(client or asset_client(), url, budget=budget)
        response.raise_for_status()
    except (httpx.HTTPError, TimeoutError):
        logger.debug("Vision image fetch failed: %s", url[:120])
        return None
    body = response.content
    if not body or len(body) > settings.vision_image_max_bytes:
        return None
    _cache_bytes(url, body)
    return body


async def _fetch_image_bytes(
    url: str, client: httpx.AsyncClient | None = None, *, budget: float | None = None
) -> bytes | None:
    """An image's bytes, or None. Downloaded at most once at a time per URL and
    kept (`_BYTES_CACHE`). `budget` bounds the transfer, not the wait for a
    host's slot (asset_fetch.get_asset); a caller joining a download already
    under way waits for that one."""
    if url.startswith("data:"):
        try:
            _, _, payload = url.partition(",")
            return base64.b64decode(payload, validate=False)
        except (ValueError, binascii.Error):
            return None
    if not url.startswith(("http://", "https://")):
        return None
    cached = _cached_bytes(url)
    if cached is not None:
        return cached
    if not await is_public_url(url):
        logger.debug("Vision image fetch refused non-public URL: %s", url[:120])
        return None
    loop = asyncio.get_running_loop()
    pending = _BYTES_IN_FLIGHT.get(url)
    if pending is None or pending[0] is not loop:
        task = loop.create_task(_download(url, client, budget))
        _BYTES_IN_FLIGHT[url] = (loop, task)
        task.add_done_callback(
            lambda done, url=url: _BYTES_IN_FLIGHT.pop(url, None)
            if _BYTES_IN_FLIGHT.get(url, (None, None))[1] is done
            else None
        )
        pending = (loop, task)
    # Shielded: a caller under a deadline (photo sampling) that gives up must
    # not cancel the download every other pass is waiting on.
    return await asyncio.shield(pending[1])
