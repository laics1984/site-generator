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
    from app.services.llm import OllamaClient

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
# regenerations commonly reuse the same scrape.
_ANNOTATION_CACHE: dict[str, VisionAnnotation] = {}


def vision_enabled() -> bool:
    return bool(settings.ollama_vision_model)


async def annotate_image_pool(
    metadata: list[ImageMetadata],
    extra_urls: list[str] | None = None,
    *,
    llm: OllamaClient | None = None,
    max_images: int | None = None,
) -> dict[str, VisionAnnotation]:
    """Annotate up to `max_images` images; returns {url: annotation}.

    `metadata` entries that get annotated are also enriched in place
    (vision_caption / vision_kind / vision_people / vision_portrait), so the
    pool handed to the resolver carries the captions with no extra plumbing.
    `extra_urls` (e.g. profile portraits not in the pool) are annotated too
    and appear only in the returned map.
    """
    if not vision_enabled():
        return {}

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

    annotations: dict[str, VisionAnnotation] = {}
    client = llm
    for url in ordered_urls:
        if len(annotations) >= max(0, limit):
            break
        annotation = _ANNOTATION_CACHE.get(url)
        if annotation is None:
            if client is None:
                from app.services.llm import get_llm  # lazy: heavy import chain

                client = get_llm(model=settings.ollama_vision_model)
            annotation = await _annotate_one(url, client)
            if annotation is None:
                continue
            _ANNOTATION_CACHE[url] = annotation
        annotations[url] = annotation
        item = by_url.get(url)
        if item is not None:
            item.vision_caption = annotation.caption or None
            item.vision_kind = annotation.kind
            item.vision_people = annotation.people_count
            item.vision_portrait = annotation.is_portrait

    return annotations


async def _annotate_one(url: str, llm: OllamaClient) -> VisionAnnotation | None:
    """Fetch + downscale + judge one image. None on any failure."""
    image_b64 = await _fetch_image_b64(url)
    if image_b64 is None:
        return None

    from app.services.llm import LlmError  # lazy: heavy import chain

    try:
        return await llm.chat_json(
            system_prompt=_JUDGE_SYSTEM,
            user_prompt="Describe this image.",
            schema=VisionAnnotation,
            temperature=0.0,
            num_ctx=2048,
            images=[image_b64],
        )
    except LlmError as exc:
        logger.warning("Vision annotation failed for %s: %s", url[:120], exc)
        return None


async def _fetch_image_b64(url: str) -> str | None:
    """Download (or decode a data: URL), downscale, return base64 JPEG.

    None for anything that can't become a bitmap (SVG, HTML error pages,
    oversized files, network failures) — the caller just skips the image.
    """
    raw = await _fetch_image_bytes(url)
    if raw is None:
        return None
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


async def _fetch_image_bytes(url: str) -> bytes | None:
    if url.startswith("data:"):
        try:
            _, _, payload = url.partition(",")
            return base64.b64decode(payload, validate=False)
        except (ValueError, binascii.Error):
            return None
    if not url.startswith(("http://", "https://")):
        return None
    try:
        async with httpx.AsyncClient(
            timeout=settings.vision_fetch_timeout_seconds, follow_redirects=True
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except httpx.HTTPError:
        logger.debug("Vision image fetch failed: %s", url[:120])
        return None
    body = response.content
    if not body or len(body) > settings.vision_image_max_bytes:
        return None
    return body
