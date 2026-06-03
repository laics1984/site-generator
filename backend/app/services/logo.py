"""
Logo handling: extract a brand palette from an uploaded logo image, or fetch
the source site's favicon / og:image when scraping a URL.

Palette extraction strategy (Pillow median-cut quantisation):
1. Open the image, convert to RGBA so we can drop transparent pixels (logos
   almost always have transparent backgrounds — counting them as "white"
   would bias every palette toward white).
2. Resize to a working width of 256px for speed.
3. Drop any pixel with alpha < 50, or that's near-white / near-black (these
   are background and outline, not brand colors).
4. Quantise the remaining pixels into N buckets (default 6) and read the
   palette.
5. Score each bucket by its "brand-ness": penalise greys (low saturation)
   and extreme lightness, reward saturated mid-luminance colors.
6. Return ordered hex strings.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass

import httpx
from PIL import Image

logger = logging.getLogger(__name__)


@dataclass
class LogoExtraction:
    """Result of extracting brand info from a logo image."""

    palette: list[str]            # hex strings, most "brand-y" first
    seed_hex: str | None          # the top recommended primary color
    logo_data_url: str            # the original logo as base64 data URL


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _luminance(r: int, g: int, b: int) -> float:
    # Simple perceptual luminance for filtering near-white / near-black.
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255


def _saturation(r: int, g: int, b: int) -> float:
    rf, gf, bf = r / 255, g / 255, b / 255
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    return 0.0 if mx == 0 else (mx - mn) / mx


def _score(hex_color: str) -> float:
    """
    Higher = better candidate for "brand color".
    Reward saturated, mid-luminance colors. Penalise greys and extremes.
    """
    r, g, b = _hex_to_rgb(hex_color)
    sat = _saturation(r, g, b)
    lum = _luminance(r, g, b)
    # Bell curve around lum=0.5: 1 at 0.5, 0 at extremes.
    lum_score = 1 - abs(lum - 0.5) * 2
    # Saturated colors are interesting; flat greys are not.
    return sat * 0.7 + lum_score * 0.3


def extract_palette_from_image_bytes(
    image_bytes: bytes, *, max_colors: int = 6
) -> LogoExtraction:
    """
    Run the extraction described in the module docstring on raw image bytes.
    Always returns a LogoExtraction — falls back to a sensible default if
    the image has no usable colors.
    """
    img = Image.open(io.BytesIO(image_bytes))
    img = img.convert("RGBA")

    # Resize for speed (logos rarely benefit from full-res analysis).
    w, h = img.size
    if w > 256:
        ratio = 256 / w
        img = img.resize((256, int(h * ratio)), Image.Resampling.LANCZOS)

    # Filter pixels: drop transparent, near-white, near-black.
    pixels = list(img.getdata())
    keep_rgb: list[tuple[int, int, int]] = []
    for r, g, b, a in pixels:
        if a < 50:
            continue
        lum = _luminance(r, g, b)
        sat = _saturation(r, g, b)
        if lum > 0.95 and sat < 0.15:  # near-white
            continue
        if lum < 0.05:  # near-black
            continue
        keep_rgb.append((r, g, b))

    if not keep_rgb:
        # Logo was just white/black — return a neutral default.
        return LogoExtraction(
            palette=["#2563eb"],
            seed_hex="#2563eb",
            logo_data_url=_to_data_url(image_bytes, img.format or "PNG"),
        )

    # Quantise via median cut.
    flat = Image.new("RGB", (len(keep_rgb), 1))
    flat.putdata(keep_rgb)
    quantised = flat.quantize(colors=max_colors, method=Image.Quantize.MEDIANCUT)
    palette_data = quantised.getpalette() or []
    color_counts = quantised.getcolors() or []
    counts_by_index = {idx: count for count, idx in color_counts}

    candidates: list[tuple[float, int, str]] = []
    for i in range(max_colors):
        r, g, b = palette_data[i * 3 : i * 3 + 3]
        if (r, g, b) == (0, 0, 0):
            continue
        hex_color = _rgb_to_hex(r, g, b)
        score = _score(hex_color)
        count = counts_by_index.get(i, 0)
        # Combine intrinsic brand-ness with dominance.
        combined = score * 0.7 + (count / max(1, len(keep_rgb))) * 0.3
        candidates.append((combined, count, hex_color))

    candidates.sort(reverse=True)
    palette = [c[2] for c in candidates]
    seed = palette[0] if palette else "#2563eb"

    return LogoExtraction(
        palette=palette,
        seed_hex=seed,
        logo_data_url=_to_data_url(image_bytes, img.format or "PNG"),
    )


def _to_data_url(image_bytes: bytes, image_format: str) -> str:
    fmt = image_format.lower()
    mime = "image/png" if fmt == "png" else f"image/{fmt}"
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"


async def fetch_logo_from_url(url: str) -> bytes | None:
    """
    Best-effort fetch of a logo by trying common locations:
    /favicon.ico, /apple-touch-icon.png, then the og:image of the homepage.

    Returns the raw bytes of the first one that succeeds, or None.
    """
    base = url.rstrip("/")
    candidates = [
        f"{base}/apple-touch-icon.png",
        f"{base}/apple-touch-icon-precomposed.png",
        f"{base}/favicon-192x192.png",
        f"{base}/favicon-96x96.png",
        f"{base}/favicon.png",
        f"{base}/favicon.ico",
    ]
    async with httpx.AsyncClient(
        timeout=10.0, follow_redirects=True, headers={"User-Agent": "WebtreeSiteGen/0.1"}
    ) as client:
        for candidate in candidates:
            try:
                r = await client.get(candidate)
                if r.status_code == 200 and r.content:
                    ctype = r.headers.get("content-type", "")
                    if "image" in ctype or candidate.endswith((".ico", ".png", ".jpg")):
                        logger.info("Fetched logo from %s", candidate)
                        return r.content
            except httpx.HTTPError as exc:
                logger.debug("Logo candidate failed %s: %s", candidate, exc)
                continue
    return None
