"""
Optional OCR pass: which images carry words — or a code — in their pixels.

A full-bleed background has the section's headline drawn over it. An image that
is ITSELF a headline — the source's own hero graphic, a promo banner, a price
list — puts two sets of words in the same space, and no scrim fixes that
because the problem is the wording, not the contrast. Every other slot in the
catalog crops its image, which cuts the same words off instead. So the flags
produced here decide where an image may go, and which images the site must show
whole (see image_match.must_show_whole and services/legible_images.py).

A QR code is read from the same decoded frame (services/qr_codes.py). It is
machine-readable text: a code covers almost none of the frame in OCR terms
(watr.org.my's measured 3.6%, its caption line only), so without its own reading
a donation code passed as a photograph and was stretched behind a headline.

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

Bounded and optional throughout: the prefetch is capped at
`settings.ocr_max_images`, results are cached by URL for the process lifetime,
and every entry point is a no-op when the package isn't installed or the setting
is off. Never load-bearing — a failure just leaves the flags unset, which is
exactly the pre-OCR behaviour.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import binascii
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any

from app.config import settings
from app.models.content_blocks import ImageMetadata
from app.services import qr_codes

logger = logging.getLogger(__name__)


# Fraction of the frame covered by detected text boxes, above which the image
# counts as carrying its own wording. Benchmarked at 512px input: clean
# photographs top out around 3%, text-bearing images start around 10%. 6% sits
# between the two with ~2x margin on each side.
#
# The bias this threshold was originally set with — "nearer the clean ceiling,
# because a missed banner is a bad hero while a false positive only costs one
# photo its cropped slots" — no longer describes what a false positive costs.
# Since services/legible_images.py, `has_text` also PLACES a poster section, so
# a wrong yes writes a section the source never had. Coverage stayed where it
# is and the per-box floor below carries the correction, because the two
# failures are different shapes: coverage answers "how much of the frame",
# confidence answers "is this writing at all".
_COVERAGE_THRESHOLD = 0.06

# Per-box confidence and length floors. A single stray glyph read out of leaf
# litter is noise, not wording — and so is a screenful of code photographed on
# a laptop, which the detector boxes just as densely. The recognizer's
# confidence is what tells the two apart: it says whether the glyphs READ as
# text. Measured on the poster set and the false positive that reached a
# generated About page: every box on the laptop photo scored 0.53–0.79 (its
# transcription was "prpladod,st -parentlode…"), every box on a real poster,
# banner or flyer 0.93–1.00. The floor sits in the gap; at 0.5 the laptop
# cleared the coverage threshold and shipped as a poster, twice.
_MIN_BOX_CONFIDENCE = 0.8
_MIN_BOX_CHARS = 2

# Roles the PREFETCH spends nothing on. A portrait or decoration can reach no
# slot this pass protects, a logo is the brand mark rather than content, and a
# gallery cell is replayed verbatim by its own rack (with a lightbox to read it
# whole). `verify_many` screens whatever it is handed, whatever its role.
_SKIP_ROLES = frozenset({"portrait", "decoration", "logo", "gallery"})


@dataclass(frozen=True)
class PixelReading:
    """What one image says in its pixels."""

    has_text: bool
    qr_payload: str | None
    # sha256 of the payload as read, so the same picture behind two URLs is
    # recognised as one (image_urls.image_identity). None only for a reading
    # built without bytes.
    content_hash: str | None = None


# Process-lifetime cache: {url: reading}. Regenerations commonly reuse the same
# scrape, and OCR is the expensive part. Bounded (FIFO) like the vision cache.
_TEXT_CACHE: dict[str, PixelReading] = {}
_TEXT_CACHE_MAX = 512

# The detector is built once (model load is ~350ms) and reused. None until the
# first call; False means "unavailable, stop trying".
_ENGINE: Any = None
_ENGINE_TRIED = False


def _cache(url: str, reading: PixelReading) -> None:
    _TEXT_CACHE[url] = reading
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
        # WARNING, not info: this silently disables the whole text-bearing-
        # photo veto for the process. A missing wheel here is easy to ship by
        # accident — e.g. requirements.txt gained the dependency but the
        # Docker image wasn't rebuilt — and nothing else signals the gap.
        logger.warning(
            "OCR text detection unavailable (rapidocr-onnxruntime not importable); "
            "hero backgrounds fall back to vision/naming signals only"
        )
        _ENGINE = None
    return _ENGINE


def ocr_engine_available() -> bool:
    """Whether the OCR pass can actually run right now (setting on, wheel
    importable, model loaded). For observability (see /health/ocr) — callers
    outside this module should use this instead of reaching into `_engine()`.
    """
    return ocr_enabled() and _engine() is not None


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


def _coverage(boxes: Any, width: int, height: int) -> float:
    """Fraction of the frame the confident detected text boxes cover.

    What the words say is still irrelevant — a misread headline is as much a
    headline as a correctly read one — but HOW WELL they read is not. The
    recognizer's confidence is the one signal that separates writing from
    texture that merely boxes like writing (see `_MIN_BOX_CONFIDENCE`), so a
    box it could not resolve into characters contributes no coverage.
    """
    covered = 0.0
    for box, text, confidence in boxes or []:
        if confidence < _MIN_BOX_CONFIDENCE or len(str(text).strip()) < _MIN_BOX_CHARS:
            continue
        xs = [point[0] for point in box]
        ys = [point[1] for point in box]
        covered += (max(xs) - min(xs)) * (max(ys) - min(ys))
    return covered / (width * height)


def read_pixels(payload: bytes | str) -> PixelReading | None:
    """Read one image's words and code from a single decode. None if unreadable."""
    engine = _engine()
    if engine is None:
        return None
    try:
        pixels = _decode(payload)
        if pixels is None:
            return None
        boxes, _elapsed = engine(pixels)
    except Exception:  # noqa: BLE001 — undecodable payload, model hiccup
        return None
    height, width = pixels.shape[:2]
    if height <= 0 or width <= 0:
        return None
    return PixelReading(
        has_text=_coverage(boxes, width, height) >= _COVERAGE_THRESHOLD,
        qr_payload=qr_codes.decode(pixels),
        content_hash=_content_hash(payload),
    )


def _content_hash(payload: bytes | str) -> str | None:
    """sha256 of the image bytes — of the decoded bytes for a base64 payload,
    so a prefetched image and a freshly downloaded one hash the same."""
    raw: bytes
    if isinstance(payload, str):
        try:
            raw = base64.b64decode(payload, validate=False)
        except (ValueError, binascii.Error):
            return None
    else:
        raw = payload
    return hashlib.sha256(raw).hexdigest()


def _candidates(metadata: list[ImageMetadata], max_images: int) -> list[ImageMetadata]:
    """The images worth a prefetch inference, most background-plausible first.

    A headshot or a 40px icon is barred from every slot by other gates long
    before these flags are read, so it is not worth one.
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


def _stamp(metadata: list[ImageMetadata], readings: dict[str, PixelReading]) -> None:
    for item in metadata:
        reading = readings.get(item.url)
        if reading is not None:
            item.ocr_has_text = reading.has_text
            item.qr_payload = reading.qr_payload
            item.content_hash = reading.content_hash


async def prefetch_text_flags(
    metadata: list[ImageMetadata],
    *,
    prefetched: dict[str, str] | None = None,
    max_images: int | None = None,
) -> dict[str, PixelReading]:
    """Stamp `ocr_has_text` and `qr_payload` on up to `max_images` scraped images.

    SOURCE IMAGES ONLY — this is the scraped pool's warm-up, and its whole
    point is riding the prefetch window for free. Stock candidates aren't known
    yet at this point (their queries come out of content generation, which is
    the thing this runs alongside), so they are screened on demand at pick time
    instead: media.ImageResolver._first_text_free → verify_url.

    `prefetched`: {url: base64_jpeg} already downloaded by
    image_vision.prefetch_image_pool. Reused when present, so with the vision
    pass on this costs no extra network at all.

    Returns {url: reading} for what it managed to judge. Mutates `metadata` in
    place. Safe to run concurrently with the content-generation LLM call: it is
    pure CPU, and the LLM owns the GPU.
    """
    if not ocr_enabled():
        return {}
    limit = settings.ocr_max_images if max_images is None else max_images
    # Anything already judged this process is free, whatever the cap.
    judged = [item.url for item in metadata if item.url in _TEXT_CACHE]
    targets = [item.url for item in _candidates(metadata, limit)]
    readings = await _screen([*judged, *targets], prefetched or {})
    _stamp(metadata, readings)
    return readings


async def verify_many(metas: list[ImageMetadata]) -> None:
    """Screen every not-yet-screened image in `metas`, in one batch, and stamp it.

    The prefetch above is a warm-up over a capped sample; on a large multi-page
    scrape most of the pool never fits that budget. This is the authoritative
    check, called at the moment an image is about to fill a slot — so what gets
    screened is always what would actually be used, not a guess about what
    might be. No cap and no role filter: the caller already chose these.

    Free for anything the prefetch covered (process-lifetime cache), and a no-op
    when the pass is off.
    """
    unscreened = [meta for meta in metas if meta.url and meta.ocr_has_text is None]
    if unscreened and ocr_enabled():
        _stamp(unscreened, await _screen([meta.url for meta in unscreened], {}))


async def verify_one(meta: ImageMetadata) -> None:
    """`verify_many` for the single image a slot is about to accept."""
    await verify_many([meta])


async def verify_url(url: str) -> bool:
    """Whether a bare URL carries text — the same check, minus the metadata.

    STOCK PHOTOGRAPHY GOES THROUGH HERE. The module originally screened source
    images only, on the reasoning that "Pexels ships photographs, not posters".
    That held for the artwork-shaped failure (a promo banner, a price list) but
    not for the one that actually reaches production: photographs OF text.
    A stock query resolves "sheet music" to printed notation, "conference talk"
    to a slide wall, "documents on a laptop" to a screenful of words — genuine
    photographs, every one, and every one unusable behind a headline. Nothing
    upstream can catch these either, because the query that produced them is
    perfectly on-topic; only the pixels give it away.

    Stock photos have no `ImageMetadata` to stamp (they are `pexels.PhotoResult`)
    so the URL cache is the whole memo — which is enough, since a rejected
    candidate is simply not chosen rather than annotated.

    Returns False whenever the pass can't judge (off, no wheel, undecodable):
    never block a slot on a screen that didn't run.
    """
    if not url or not ocr_enabled():
        return False
    reading = (await _screen([url], {})).get(url)
    return reading is not None and reading.has_text


async def _screen(urls: list[str], prefetched: dict[str, str]) -> dict[str, PixelReading]:
    """Readings for `urls`: cached ones free, the rest read in one batch."""
    unique = list(dict.fromkeys(url for url in urls if url))
    readings = {url: _TEXT_CACHE[url] for url in unique if url in _TEXT_CACHE}
    unread = [url for url in unique if url not in readings]
    if unread and _engine() is not None:
        payloads = await _payloads(unread, prefetched)
        if payloads:
            # One thread for the whole batch, not one per image: onnxruntime
            # already saturates every core per inference, so a pool only adds
            # contention (measured 30 images: 18.9s serial vs 21.3s on two
            # workers). This keeps the event loop free without fighting itself.
            readings.update(await asyncio.to_thread(_judge_batch, payloads))
    return readings


async def _payloads(
    urls: list[str], prefetched: dict[str, str]
) -> list[tuple[str, bytes | str]]:
    """(url, image payload) for each URL, reusing prefetched downloads and
    dropping the ones that don't fetch."""
    out: list[tuple[str, bytes | str]] = [
        (url, prefetched[url]) for url in urls if url in prefetched
    ]
    missing = [url for url in urls if url not in prefetched]
    if not missing:
        return out
    import httpx

    from app.services.image_vision import _fetch_image_bytes  # lazy: import chain

    sem = asyncio.Semaphore(settings.ocr_fetch_concurrency)

    async def one(url: str, client: httpx.AsyncClient):
        async with sem:
            return url, await _fetch_image_bytes(url, client=client)

    async with httpx.AsyncClient(
        timeout=settings.vision_fetch_timeout_seconds, follow_redirects=True
    ) as client:
        fetched = await asyncio.gather(*(one(url, client) for url in missing))
    return out + [(url, raw) for url, raw in fetched if raw is not None]


def _judge_batch(payloads: list[tuple[str, bytes | str]]) -> dict[str, PixelReading]:
    """Sync/CPU on purpose — the caller runs it via asyncio.to_thread."""
    readings: dict[str, PixelReading] = {}
    for url, payload in payloads:
        reading = read_pixels(payload)
        if reading is None:
            continue
        readings[url] = reading
        _cache(url, reading)
        if reading.has_text or reading.qr_payload:
            logger.debug(
                "Pixels: %s carries %s — shown whole, never cropped or overprinted",
                url[:120],
                "a QR code" if reading.qr_payload else "its own text",
            )
    return readings
