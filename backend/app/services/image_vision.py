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
lifetime. Enabled only when `settings.ollama_vision_model` is set — without
it `annotate_image_pool` returns {} without any I/O. Every per-image failure
(download, decode, LLM) is swallowed: annotation is an enhancement, never
load-bearing.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from io import BytesIO
from typing import TYPE_CHECKING, Literal

import httpx
from pydantic import BaseModel, Field

from app.config import settings
from app.models.content_blocks import ImageMetadata

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


_JUDGE_SYSTEM = """You describe one website image for an image-matching system.
Reply ONLY with a single JSON object:
{"caption": "...", "kind": "photo|logo|banner|screenshot|graphic|map|other", "people_count": N, "is_portrait": true|false}
- caption: one factual sentence describing subjects, setting and activity. No style commentary.
- kind: photo = a real photograph; logo = a brand mark; banner = promotional graphic with overlaid text; screenshot = software UI capture; graphic = illustration/icon/pattern; map = a map.
- people_count: number of visible people (0 if none).
- is_portrait: true only when a single person's face or head-and-shoulders is the main subject.
Do not explain. Do not return anything except the JSON object."""

# Vision input is downscaled to this box before base64 — classification and a
# one-line caption don't need more pixels, and it keeps the payload small.
_THUMBNAIL_PX = 512

# Process-lifetime cache: annotating the same URL twice is pure waste, and
# regenerations commonly reuse the same scrape. Bounded (FIFO) so a long-lived
# server processing many distinct images can't grow it without limit.
_ANNOTATION_CACHE: dict[str, VisionAnnotation] = {}
_ANNOTATION_CACHE_MAX = 512


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
    candidates, _ = _vision_candidates(metadata, extra_urls, max_images)
    to_fetch = [u for u in candidates if u not in _ANNOTATION_CACHE]
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
) -> dict[str, VisionAnnotation]:
    """Annotate up to `max_images` images; returns {url: annotation}.

    `metadata` entries that get annotated are also enriched in place
    (vision_caption / vision_kind / vision_people / vision_portrait), so the
    pool handed to the resolver carries the captions with no extra plumbing.
    `extra_urls` (e.g. profile portraits not in the pool) are annotated too
    and appear only in the returned map. `prefetched` supplies already
    downloaded {url: base64} payloads (from `prefetch_image_pool`); anything
    missing from it is downloaded here as before.
    """
    if not vision_enabled():
        return {}

    # Cap candidates up front (the first `limit` distinct URLs), then resolve
    # them concurrently. Downloads overlap; the Ollama vision calls queue on the
    # single model slot, so we stay within a 16GB M1's budget. Per-URL failures
    # are swallowed, so we may end with fewer than `limit` annotations.
    candidates, by_url = _vision_candidates(metadata, extra_urls, max_images)
    if not candidates:
        return {}

    client = llm
    if client is None and any(u not in _ANNOTATION_CACHE for u in candidates):
        from app.services.llm import active_vision_model, get_llm  # lazy: heavy import chain

        client = get_llm(model=active_vision_model())

    sem = asyncio.Semaphore(_VISION_CONCURRENCY)

    async def _resolve(
        url: str, http_client: httpx.AsyncClient
    ) -> tuple[str, VisionAnnotation | None]:
        cached = _ANNOTATION_CACHE.get(url)
        if cached is not None:
            return url, cached
        async with sem:
            annotation = await _annotate_one(
                url,
                client,  # type: ignore[arg-type]
                http_client,
                image_b64=(prefetched or {}).get(url),
            )
        if annotation is not None:
            _cache_annotation(url, annotation)
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

    return annotations


async def _annotate_one(
    url: str,
    llm: LlmClient,
    http_client: httpx.AsyncClient | None = None,
    *,
    image_b64: str | None = None,
) -> VisionAnnotation | None:
    """Fetch + downscale + judge one image. None on any failure.
    A prefetched `image_b64` skips the download entirely."""
    if image_b64 is None:
        image_b64 = await _fetch_image_b64(url, client=http_client)
    if image_b64 is None:
        return None

    from app.services.llm import LlmError  # lazy: heavy import chain

    try:
        return await llm.chat_json(
            system_prompt=_JUDGE_SYSTEM,
            user_prompt="Describe this image.",
            schema=VisionAnnotation,
            temperature=settings.judge_temperature,
            num_ctx=settings.judge_num_ctx,
            images=[image_b64],
        )
    except LlmError as exc:
        logger.warning("Vision annotation failed for %s: %s", url[:120], exc)
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


async def _fetch_image_bytes(
    url: str, client: httpx.AsyncClient | None = None
) -> bytes | None:
    if url.startswith("data:"):
        try:
            _, _, payload = url.partition(",")
            return base64.b64decode(payload, validate=False)
        except (ValueError, binascii.Error):
            return None
    if not url.startswith(("http://", "https://")):
        return None
    try:
        if client is not None:
            response = await client.get(url)
            response.raise_for_status()
        else:
            async with httpx.AsyncClient(
                timeout=settings.vision_fetch_timeout_seconds, follow_redirects=True
            ) as owned:
                response = await owned.get(url)
                response.raise_for_status()
    except httpx.HTTPError:
        logger.debug("Vision image fetch failed: %s", url[:120])
        return None
    body = response.content
    if not body or len(body) > settings.vision_image_max_bytes:
        return None
    return body
