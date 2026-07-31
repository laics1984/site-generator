"""
Optional OCR pass: which scraped images have words baked into their pixels.

A full-bleed background has the section's headline drawn over it. An image that
is ITSELF a headline — the source's own hero graphic, a promo banner, a price
list — puts two sets of words in the same space, and no scrim fixes that
because the problem is the wording, not the contrast. Such an image is still
fine as a featured image or an untitled card, so the flag produced here bars
one slot, not the image (see image_match.bears_text).

Why OCR and not pixel statistics: text over a photograph is not separable by
edge/contrast heuristics. Measured on 10 real photographs plus 5 text-bearing
images, row-energy spikiness gave clean photos 0.08-0.24 and text-bearing
0.18-0.71, and an ink-concentration measure gave 0.00-0.54 vs 0.44-0.93 — both
overlapping, i.e. no usable threshold. Text-region coverage from an OCR
detector gave 0.00-3.1% vs 10.5-16.3%, which separates cleanly.

Why not the vision model: it works too (`vision_has_text`), but the vision pass
is deliberately serialized AFTER content generation — two models on one GPU
thrash. This pass is pure CPU, so it rides the existing prefetch window
alongside the content LLM and costs roughly nothing in wall time.

Bounded and optional throughout: capped at `settings.ocr_max_images`, results
cached by URL for the process lifetime, and a no-op returning {} when the
package isn't installed or the setting is off. Never load-bearing — a failure
just leaves the flag unset, which is exactly the pre-OCR behaviour.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from io import BytesIO
from typing import Any

from app.config import settings
from app.models.content_blocks import ImageMetadata

logger = logging.getLogger(__name__)


# Fraction of the frame covered by detected text boxes, above which the image
# counts as carrying its own wording. Benchmarked at 512px input: clean
# photographs top out around 3%, text-bearing images start around 10%. 6% sits
# between the two with ~2x margin on each side — deliberately nearer the clean
# ceiling, because a missed banner is a bad hero while a false positive only
# costs one photo its background slot.
_COVERAGE_THRESHOLD = 0.06

# Per-box confidence and length floors. A single stray glyph read out of leaf
# litter is noise, not wording.
_MIN_BOX_CONFIDENCE = 0.5
_MIN_BOX_CHARS = 2

# Roles that can never reach a full-bleed background anyway (existing gates in
# media/image_match already exclude them), so screening them is pure waste.
_SKIP_ROLES = frozenset({"portrait", "decoration", "logo", "gallery"})

# Process-lifetime cache: {url: has_text}. Regenerations commonly reuse the same
# scrape, and OCR is the expensive part. Bounded (FIFO) like the vision cache.
_TEXT_CACHE: dict[str, bool] = {}
_TEXT_CACHE_MAX = 512

# The detector is built once (model load is ~350ms) and reused. None until the
# first call; False means "unavailable, stop trying".
_ENGINE: Any = None
_ENGINE_TRIED = False


def _cache(url: str, has_text: bool) -> None:
    _TEXT_CACHE[url] = has_text
    while len(_TEXT_CACHE) > _TEXT_CACHE_MAX:
        _TEXT_CACHE.pop(next(iter(_TEXT_CACHE)), None)


def ocr_enabled() -> bool:
    """Whether the pass should run at all. Cheap — no import, no model load."""
    return bool(settings.ocr_text_detection_enabled)


def _engine() -> Any:
    """The shared detector, or None when the package isn't installed.

    Imported lazily: rapidocr pulls onnxruntime + opencv, and a deployment that
    leaves the pass off must not pay that import cost (or require the wheels at
    all — `pip install -r requirements.txt` is the only thing that adds them).
    """
    global _ENGINE, _ENGINE_TRIED
    if _ENGINE_TRIED:
        return _ENGINE
    _ENGINE_TRIED = True
    try:
        from rapidocr_onnxruntime import RapidOCR

        _ENGINE = RapidOCR()
    except Exception:  # noqa: BLE001 — missing wheel, bad model, anything
        logger.info(
            "OCR text detection unavailable (rapidocr-onnxruntime not importable); "
            "hero backgrounds fall back to vision/naming signals"
        )
        _ENGINE = None
    return _ENGINE


def _decode(payload: bytes | str) -> Any:
    """Bytes or base64-JPEG → an RGB numpy array the detector accepts."""
    import numpy as np
    from PIL import Image

    raw = payload
    if isinstance(payload, str):
        try:
            raw = base64.b64decode(payload, validate=False)
        except (ValueError, binascii.Error):
            return None
    with Image.open(BytesIO(raw)) as img:
        rgb = img.convert("RGB")
        # Matches the vision prefetch's thumbnail, so prefetched payloads are
        # reused as-is. Smaller inputs also *improve* separation: incidental
        # small text (a logo on a shirt) drops below the detector's floor while
        # a headline stays well above it.
        rgb.thumbnail((settings.ocr_input_px, settings.ocr_input_px))
        return np.array(rgb)


def text_coverage(payload: bytes | str) -> float | None:
    """Fraction of the frame covered by detected text. None if undetectable.

    Detection only — what the words SAY is irrelevant, so a misread is
    harmless; all that matters is that glyphs are there and how much room they
    take. That is also the robust half of OCR, which is why a wrong
    transcription never becomes a wrong decision here.
    """
    engine = _engine()
    if engine is None:
        return None
    try:
        arr = _decode(payload)
        if arr is None:
            return None
        result, _elapsed = engine(arr)
    except Exception:  # noqa: BLE001 — undecodable payload, model hiccup
        return None
    if not result:
        return 0.0
    height, width = arr.shape[:2]
    if height <= 0 or width <= 0:
        return None
    covered = 0.0
    for box, text, confidence in result:
        if confidence < _MIN_BOX_CONFIDENCE:
            continue
        if len(str(text).strip()) < _MIN_BOX_CHARS:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        covered += (max(xs) - min(xs)) * (max(ys) - min(ys))
    return covered / (width * height)


def _candidates(metadata: list[ImageMetadata], max_images: int) -> list[ImageMetadata]:
    """The images worth screening, most background-plausible first.

    Only images that could actually reach a full-bleed slot are worth an
    inference: a headshot or a 40px icon is barred from backgrounds by other
    gates long before this flag is read.
    """
    eligible = [
        item
        for item in metadata
        if item.url
        and item.role not in _SKIP_ROLES
        and item.url not in _TEXT_CACHE
    ]
    # The source's own CSS backgrounds and hero-intent images are the ones that
    # actually compete for a hero background, so they get the budget first.
    def priority(item: ImageMetadata) -> tuple[int, int]:
        background_ish = (
            item.source_usage == "css_background" or item.role in ("hero", "background")
        )
        return (0 if background_ish else 1, 0 if item.intent == "hero" else 1)

    eligible.sort(key=priority)
    return eligible[: max(0, max_images)]


async def prefetch_text_flags(
    metadata: list[ImageMetadata],
    *,
    prefetched: dict[str, str] | None = None,
    max_images: int | None = None,
) -> dict[str, bool]:
    """Stamp `ocr_has_text` on up to `max_images` scraped images.

    SOURCE IMAGES ONLY. Stock photography is not screened: Pexels ships
    photographs, not posters, and a baked-in headline is a property of a site's
    own artwork. The parameter type carries that — stock results are
    `PhotoResult`, never `ImageMetadata`.

    `prefetched`: {url: base64_jpeg} already downloaded by
    image_vision.prefetch_image_pool. Reused when present, so with the vision
    pass on this costs no extra network at all.

    Returns {url: has_text} for what it managed to judge. Mutates `metadata` in
    place. Safe to run concurrently with the content-generation LLM call: it is
    pure CPU, and the LLM owns the GPU.
    """
    if not ocr_enabled():
        return {}
    limit = settings.ocr_max_images if max_images is None else max_images
    targets = _candidates(metadata, limit)
    by_url = {item.url: item for item in metadata if item.url}

    # Anything already judged this process is free.
    flags = {url: _TEXT_CACHE[url] for url in by_url if url in _TEXT_CACHE}
    if targets and _engine() is not None:
        payloads = await _payloads(targets, prefetched or {})
        if payloads:
            # One thread for the whole batch, not one per image: onnxruntime
            # already saturates every core per inference, so a pool only adds
            # contention (measured 30 images: 18.9s serial vs 21.3s on two
            # workers). This keeps the event loop free without fighting itself.
            flags.update(await asyncio.to_thread(_judge_batch, payloads))

    for url, has_text in flags.items():
        item = by_url.get(url)
        if item is not None:
            item.ocr_has_text = has_text
    return flags


async def verify_one(meta: ImageMetadata) -> bool:
    """Screen a SINGLE image on demand and stamp it. Returns its text flag.

    The prefetch above is a warm-up over a capped sample; on a large multi-page
    scrape most of the pool never fits that budget. This is the authoritative
    check, called at the moment a background slot is about to accept an image —
    so what gets screened is always what would actually be used, not a guess
    about what might be.

    Free when the prefetch already covered the URL (process-lifetime cache) and
    a no-op when the pass is off, so the common paths cost nothing.
    """
    if not meta.url:
        return False
    if meta.ocr_has_text is not None:
        return meta.ocr_has_text
    cached = _TEXT_CACHE.get(meta.url)
    if cached is not None:
        meta.ocr_has_text = cached
        return cached
    if not ocr_enabled() or _engine() is None:
        return False
    payloads = await _payloads([meta], {})
    if not payloads:
        return False
    flags = await asyncio.to_thread(_judge_batch, payloads)
    has_text = flags.get(meta.url, False)
    meta.ocr_has_text = has_text
    return has_text


async def _payloads(
    targets: list[ImageMetadata], prefetched: dict[str, str]
) -> list[tuple[str, bytes | str]]:
    """(url, image payload) for each target, reusing prefetched downloads."""
    from app.services.image_vision import _fetch_image_bytes  # lazy: import chain

    out: list[tuple[str, bytes | str]] = []
    missing: list[str] = []
    for item in targets:
        payload = prefetched.get(item.url)
        if payload is not None:
            out.append((item.url, payload))
        else:
            missing.append(item.url)
    if missing:
        import httpx

        sem = asyncio.Semaphore(settings.ocr_fetch_concurrency)

        async def one(url: str, client: httpx.AsyncClient):
            async with sem:
                return url, await _fetch_image_bytes(url, client=client)

        async with httpx.AsyncClient(
            timeout=settings.vision_fetch_timeout_seconds, follow_redirects=True
        ) as client:
            fetched = await asyncio.gather(*(one(url, client) for url in missing))
        out.extend((url, raw) for url, raw in fetched if raw is not None)
    return out


def _judge_batch(payloads: list[tuple[str, bytes | str]]) -> dict[str, bool]:
    """Sync/CPU on purpose — the caller runs it via asyncio.to_thread."""
    flags: dict[str, bool] = {}
    for url, payload in payloads:
        coverage = text_coverage(payload)
        if coverage is None:
            continue
        has_text = coverage >= _COVERAGE_THRESHOLD
        flags[url] = has_text
        _cache(url, has_text)
        if has_text:
            logger.debug(
                "OCR: %s carries text (%.1f%% coverage) — barred from background slots",
                url[:120], coverage * 100,
            )
    return flags
