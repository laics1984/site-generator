"""
Pixel sampling for photos that arrive without colour metadata.

Pexels hands us `avg_color` for free, so `ImageResolver` can derive a luminance
band and an adaptive overlay alpha without touching a single pixel. Scraped
photos have no such field — `ImageMetadata.dominant_color` was declared for them
but never written — so every scraped hero fell back to `photo_background`'s
blind mid-cast: a dark interior shot and a bright beach got the identical wash.

This module closes that gap for the slots where it shows most — a photo painted
BEHIND copy (a full-bleed hero, a photo CTA, a photo tile) — by downloading the
photo once and reading four things off it:

  * `dominant_hex` — feeds the existing `overlay_alpha` ramp, so the cast
    adapts to the photograph instead of guessing.
  * `focal_y` — where the subject actually sits vertically, so a 100dvh hero
    crops around it instead of hard-centering and slicing off heads.
  * `luminance_map` — how bright the photo is WHERE, so the copy scrim is sized
    for the region the copy sits on, not the frame's average
    (`image_styling._copy_scrim`). Stock photography puts its brightest region
    in the middle of the frame, which is where the copy goes.
  * `hues` — the colour families the frame actually carries, so an abstract
    SOURCE background can be held to the theme (`image_abstract.theme_fits`).
    A median colour cannot answer that: a blue-and-orange texture has a brown
    median that matches neither.

Deliberately NOT a general pass over the image pool: one download per photo
painted behind copy. Everything degrades to None, and every caller must treat
None as "carry on exactly as before".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from io import BytesIO

import httpx

from app.config import settings
from app.services.image_styling import (
    PhotoLuminance,
    band_for_luminance,
    relative_luminance,
)

logger = logging.getLogger(__name__)

# Sampling resolutions. Colour is read from a 16px thumbnail (a median over 256
# cells is stable but still cheap); focal weighting from a 32-row greyscale,
# which is enough vertical resolution to tell "faces in the upper third" from
# "horizon across the middle" without paying for real saliency detection.
_COLOR_PX = 16
_FOCAL_ROWS = 32
# The luminance map's grid. 24 cells across puts ~14 inside a centred copy
# column: fine enough that a white shirt behind half a sentence registers, and
# each cell is still an area average, so a stray specular highlight does not.
_LUMINANCE_GRID = 24

# Clamp for the focal point. A background-position outside this band stops
# reading as framing and starts reading as a mistake — the top of the frame
# leaves the subject under the header, the bottom pushes it behind the copy.
_FOCAL_MIN = 0.25
_FOCAL_MAX = 0.65


@dataclass(frozen=True)
class PhotoSample:
    """What one pixel-read of a photo tells us about how to dress it."""

    dominant_hex: str
    luminance: float
    #: 0..1 down the frame, where the visually busiest band sits.
    focal_y: float
    #: How bright the photo is where: what the copy scrim is sized from.
    luminance_map: PhotoLuminance | None = None
    #: Hue (degrees) of every chromatic pixel of the colour read, and the share
    #: of pixels that carry a hue at all. See `_chromatic_hues`.
    hues: tuple[float, ...] = ()
    chromatic_share: float = 0.0

    @property
    def band(self) -> str:
        return band_for_luminance(self.luminance)


async def sample_photo(
    url: str, *, client: httpx.AsyncClient | None = None
) -> PhotoSample | None:
    """Download and measure one photo. None on any failure — never raises.

    Reuses `image_vision`'s fetcher, which already handles `data:` URLs, the
    SSRF guard for private hosts, and the byte-size cap. Bounded by
    `settings.photo_sample_timeout_seconds`: an unreachable host must cost a
    generation seconds, not the fetcher's full timeout.
    """
    if not settings.photo_sampling_enabled:
        return None
    try:
        return await asyncio.wait_for(
            _sample(url, client), timeout=settings.photo_sample_timeout_seconds
        )
    except (TimeoutError, asyncio.CancelledError):
        logger.debug("Photo sampling timed out for %s", url[:120])
        return None


async def _sample(url: str, client: httpx.AsyncClient | None) -> PhotoSample | None:
    # Imported here (not at module top) to keep this module free of the vision
    # pass's import chain — callers may have vision disabled entirely.
    from app.services.image_vision import _fetch_image_bytes

    raw = await _fetch_image_bytes(url, client=client)
    if raw is None:
        return None
    # PIL decode is CPU-bound; thread it off so concurrent downloads keep flowing.
    return await asyncio.to_thread(_measure, raw, url)


def _measure(raw: bytes, url: str = "") -> PhotoSample | None:
    """Decode + measure. Sync/CPU on purpose — callers use asyncio.to_thread."""
    try:
        from PIL import Image

        with Image.open(BytesIO(raw)) as img:
            rgb = img.convert("RGB")
            colour = rgb.resize((_COLOR_PX, _COLOR_PX))
            grey = rgb.convert("L").resize((_COLOR_PX, _FOCAL_ROWS))
            grid = rgb.resize((_LUMINANCE_GRID, _LUMINANCE_GRID), Image.BOX)
            pixels = list(colour.getdata())
            flat = list(grey.getdata())
            cells = list(grid.getdata())
            rows = [
                flat[r * _COLOR_PX : (r + 1) * _COLOR_PX] for r in range(_FOCAL_ROWS)
            ]
    except Exception:  # noqa: BLE001 — any undecodable payload is just skipped
        logger.debug("Could not decode image for sampling: %s", url[:120])
        return None

    if not pixels:
        return None

    dominant_hex = _median_hex(pixels)
    try:
        lum = relative_luminance(dominant_hex)
    except (ValueError, IndexError):
        return None
    hues, chromatic_share = _chromatic_hues(pixels)
    return PhotoSample(
        dominant_hex=dominant_hex,
        luminance=round(lum, 4),
        focal_y=_focal_y(rows),
        luminance_map=PhotoLuminance(
            _LUMINANCE_GRID,
            _LUMINANCE_GRID,
            tuple(round(relative_luminance("#%02x%02x%02x" % cell), 4) for cell in cells),
        ),
        hues=hues,
        chromatic_share=chromatic_share,
    )


def _chromatic_hues(pixels: list[tuple[int, int, int]]) -> tuple[tuple[float, ...], float]:
    """(hue of every chromatic pixel, share of pixels that are chromatic).

    "Chromatic" is `theme.has_brand_hue` — chroma, the one test the palette's
    own colours are judged by, so an image and a theme are measured alike.
    """
    from app.services.image_abstract import hue_of
    from app.services.theme import has_brand_hue

    if not pixels:
        return (), 0.0
    hexes = ["#%02x%02x%02x" % p for p in pixels]
    hues = tuple(round(hue_of(h), 1) for h in hexes if has_brand_hue(h))
    return hues, round(len(hues) / len(hexes), 4)


def _median_hex(pixels: list[tuple[int, int, int]]) -> str:
    """Channel-wise median of the sampled pixels, as a hex string.

    Median, not mean: a bright sky or a black border occupies a large share of
    a hero photo and would drag a mean far off what the frame actually reads
    as. The median lands on the body of the image.
    """
    mid = len(pixels) // 2
    channels = [sorted(p[c] for p in pixels)[mid] for c in range(3)]
    return "#" + "".join(f"{c:02x}" for c in channels)


def _focal_y(rows: list[list[int]]) -> float:
    """Vertical centre of visual interest, 0..1 down the frame.

    Detail, not brightness, marks the subject: skies, walls and blurred
    backgrounds are smooth, while faces, text and edges are not. Score each row
    by its horizontal gradient energy, keep the busiest quartile, and take their
    weighted centroid — enough to pull the crop toward people in the upper third
    of a photo and away from an empty foreground, without pretending to be
    saliency detection.

    Falls back to dead centre whenever the frame carries no usable structure
    (a flat colour field, a gradient), which is exactly the case where centring
    is already the right answer.
    """
    if not rows:
        return 0.5
    energies = [
        sum(abs(row[i + 1] - row[i]) for i in range(len(row) - 1)) if len(row) > 1 else 0
        for row in rows
    ]
    total = sum(energies)
    if total <= 0:
        return 0.5

    threshold = sorted(energies)[int(len(energies) * 0.75)]
    weighted = [(i, e) for i, e in enumerate(energies) if e >= threshold and e > 0]
    if not weighted:
        return 0.5

    weight_sum = sum(e for _, e in weighted)
    centroid = sum((i + 0.5) * e for i, e in weighted) / (weight_sum * len(rows))
    return round(min(_FOCAL_MAX, max(_FOCAL_MIN, centroid)), 4)
