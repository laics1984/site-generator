"""
Graphic screening: tell a GRAPHIC from a PHOTOGRAPH by reading the pixels.

`image_evidence.classify_role` measures the layout box, and geometry cannot
answer this question — a wordmark rendered at 384x88 measures exactly like a
small photograph, which is why `ImageRole` has always had a ``"logo"`` value
that `classify_role` itself can never return.

Structural markup usually settles it first (`logo_extraction.brand_mark_urls`,
stamped by the scraper). But a site can state nothing at all. A coming-soon
splash page whose only image is

    <img class="w-96 mb-10 inline-block" src="/webtree_greenwhite.png">

has no ``<header>``, no ``<nav>``, no ``alt``, no home-link wrapper and no
"logo" anywhere in the src or class. Every structural signal is absent, so the
site's own wordmark entered the photo pool as ``role="content"`` and won the
About slot through `media`'s page-local size fallback — it was the largest
image on the page, because it was the only one.

The pixels are not ambiguous. Two measurements, and BOTH must agree:

  * TRANSPARENCY — the share of the frame that is fully clear. A photograph is
    an opaque rectangle; an authored mark is mostly nothing.
  * FLATNESS — distinct colours as a share of the visible pixels. A wordmark
    uses a handful of flat colours; a photograph has thousands, and a gradient
    between every pair of them.

**Transparency alone is not enough, and that is the entire reason flatness is
here.** A product cutout on a transparent background is a real photograph that
a product page must stay free to use. Measured on the 96x96 sample this module
takes:

    webtree wordmark      62.5% clear     2.4% distinct  -> graphic
    pexels photograph      0.0% clear    67.6% distinct  -> photo
    photograph, cut out   64.0% clear    79.6% distinct  -> photo

Both margins are roughly 10x, so the thresholds below read a real gap rather
than being tuned to a sample.

The verdict is written as ``role = "logo"``, which is what makes this pass cost
nothing downstream: four gates already veto that role
(`source_router._UNPROMPTABLE_ROLES`, `image_match._EXCLUDED_ROLES`, `media`'s
page-local size fallback, `image_refs._unfit_for_kind`). Both roles are
measurements — one of the layout box, one of the pixels — so this is the same
kind of answer `classify_role` gives, from the evidence it cannot see.

A grid cell is never screened. A partner/award wall IS the section's content,
and those tiles are transparent flat graphics by definition — the same
exception `classify_role` and `logo_extraction._in_logo_wall` already make.

Like OCR and the vision judge, an enhancement and never a gate: any failure
leaves the pool exactly as it was.
"""

from __future__ import annotations

import asyncio
import logging
from io import BytesIO

import httpx

from app.config import settings
from app.models.content_blocks import ImageMetadata

logger = logging.getLogger(__name__)

# Roles this pass must not touch. "gallery"/"portrait" are measured GRID cells —
# a logo wall is the section's content, and its tiles are exactly what this
# module would otherwise flag. "logo"/"decoration" are already decided.
_SKIP_ROLES = frozenset({"gallery", "portrait", "logo", "decoration"})

# Sample resolution. 96x96 is enough to measure both shares stably and small
# enough that the colour set stays cheap; the same order as image_sampling's
# 16px colour thumbnail, one step up because flatness needs more cells to count.
_SAMPLE_PX = 96

# An alpha byte under this reads as fully clear. Not 0: PNG encoders leave
# 1-2 of dither at a mark's antialiased edge.
_CLEAR_ALPHA = 16
# ...and over this a pixel counts as visible, so antialiased edge pixels are
# excluded from the colour count rather than inflating it.
_OPAQUE_ALPHA = 128

# Measured gap: wordmark 62.5% clear / 2.4% distinct, photographs 0-64% clear
# but 68-80% distinct. Both thresholds sit in open space between the two.
_MIN_CLEAR_SHARE = 0.25
_MAX_DISTINCT_SHARE = 0.15

# Below this many visible pixels the colour ratio stops meaning anything (a
# 20-pixel sliver is trivially "flat"), so the image is left alone.
_MIN_VISIBLE_PX = 256

# Process-lifetime cache: {url: is_graphic}. Regenerations commonly reuse the
# same scrape, and the download is the expensive part. Bounded (FIFO) like the
# vision and OCR caches.
_GRAPHIC_CACHE: dict[str, bool] = {}
_GRAPHIC_CACHE_MAX = 512


def graphic_detection_enabled() -> bool:
    """Whether the pass should run at all. Cheap — no import, no download."""
    return bool(settings.graphic_detection_enabled)


def _cache(url: str, is_graphic: bool) -> None:
    _GRAPHIC_CACHE[url] = is_graphic
    while len(_GRAPHIC_CACHE) > _GRAPHIC_CACHE_MAX:
        _GRAPHIC_CACHE.pop(next(iter(_GRAPHIC_CACHE)), None)


def measure(raw: bytes, url: str = "") -> tuple[float, float] | None:
    """(clear_share, distinct_share) for one image. None when undecodable.

    Sync/CPU on purpose — callers run it via ``asyncio.to_thread``.
    """
    try:
        from PIL import Image

        with Image.open(BytesIO(raw)) as img:
            pixels = list(img.convert("RGBA").resize((_SAMPLE_PX, _SAMPLE_PX)).getdata())
    except Exception:  # noqa: BLE001 — any undecodable payload is just skipped
        logger.debug("Could not decode image for graphic screening: %s", url[:120])
        return None
    if not pixels:
        return None

    clear = sum(1 for p in pixels if p[3] < _CLEAR_ALPHA)
    visible = [p[:3] for p in pixels if p[3] >= _OPAQUE_ALPHA]
    if len(visible) < _MIN_VISIBLE_PX:
        return None
    return clear / len(pixels), len(set(visible)) / len(visible)


def is_flat_graphic(clear_share: float, distinct_share: float) -> bool:
    """Whether the two measurements agree that this is an authored mark.

    BOTH conditions, never either alone: transparency says "authored, not
    photographed", flatness is what separates a wordmark from a photograph
    someone cut out of its background.
    """
    return clear_share >= _MIN_CLEAR_SHARE and distinct_share <= _MAX_DISTINCT_SHARE


def _candidates(metadata: list[ImageMetadata], max_images: int) -> list[ImageMetadata]:
    """Pool images worth screening, most decisive first.

    Ordered by the slots a graphic does most damage in: `PRIMARY_INTENTS`
    (hero/about) are the large featured slots where a blown-up wordmark reads
    as an outright failure.
    """
    eligible = [
        item
        for item in metadata
        if item.url and item.role not in _SKIP_ROLES and item.url not in _GRAPHIC_CACHE
    ]
    eligible.sort(key=lambda item: item.intent not in ("hero", "about"))
    return eligible[:max_images]


async def screen_source_images_for_graphics(
    metadata: list[ImageMetadata], *, max_images: int | None = None
) -> dict[str, bool]:
    """Stamp ``role="logo"`` on pool images the pixels prove are flat graphics.

    SOURCE IMAGES ONLY. Stock photography is never screened — Pexels ships
    photographs, and stock results are never `ImageMetadata` in the first place.

    Mutates ``metadata`` in place and returns {url: is_graphic} for what it
    managed to judge. Safe to run alongside the content-generation LLM call:
    it is network plus a little CPU, and the LLM owns the GPU.

    It does its OWN downloads rather than reusing `image_vision`'s prefetched
    payloads, which cannot serve this measurement: `_downscale_to_b64` does
    ``convert("RGB")`` and re-encodes as JPEG, so the alpha channel — half the
    evidence — is gone by the time it reaches the cache.
    """
    if not graphic_detection_enabled():
        return {}
    limit = settings.graphic_max_images if max_images is None else max_images
    by_url = {item.url: item for item in metadata if item.url}
    verdicts = {url: _GRAPHIC_CACHE[url] for url in by_url if url in _GRAPHIC_CACHE}

    targets = _candidates(metadata, limit)
    if targets:
        # Lazy: keeps this module out of the vision pass's import chain, the
        # same arrangement image_sampling uses.
        from app.services.image_vision import _fetch_image_bytes

        sem = asyncio.Semaphore(settings.graphic_fetch_concurrency)

        async def _one(item: ImageMetadata, client: httpx.AsyncClient) -> None:
            async with sem:
                raw = await _fetch_image_bytes(item.url, client=client)
            if raw is None:
                return
            measured = await asyncio.to_thread(measure, raw, item.url)
            if measured is None:
                return
            verdicts[item.url] = is_flat_graphic(*measured)

        async with httpx.AsyncClient(
            timeout=settings.vision_fetch_timeout_seconds, follow_redirects=True
        ) as client:
            await asyncio.gather(*(_one(item, client) for item in targets))

    flagged = 0
    for url, is_graphic in verdicts.items():
        _cache(url, is_graphic)
        item = by_url.get(url)
        if item is None or not is_graphic or item.role in _SKIP_ROLES:
            continue
        logger.info(
            "Pixels say %s is a flat graphic, not a photograph — withdrawing it "
            "from the photo pool (was role=%s)",
            url[:120],
            item.role,
        )
        item.role = "logo"
        flagged += 1
    if flagged:
        logger.info("Graphic screen withdrew %d image(s) from the photo pool", flagged)
    return verdicts
