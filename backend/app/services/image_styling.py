"""
Deterministic helpers for photo backgrounds: adaptive dark-overlay intensity
(so a dark photo isn't crushed and a bright one stays legible), brand-tinted
overlays (so any photo harmonises with the theme), and a colour-harmony gate
for un-overlaid (split) imagery.

Pure math — no LLM, no network — so it's unit-testable in isolation. Luminance
is read from a photo's average colour (Pexels returns `avg_color` for free;
scraped images can be sampled with Pillow upstream).
"""

from __future__ import annotations

import colorsys


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _srgb_to_linear(channel: int) -> float:
    c = channel / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance in [0,1] (0 = black, 1 = white)."""
    r, g, b = _hex_to_rgb(hex_color)
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def overlay_alpha(avg_hex: str, *, min_alpha: float = 0.30, max_alpha: float = 0.62) -> float:
    """Pick the dark-overlay opacity from the photo's average luminance.

    A dark photo already provides contrast for white text → light overlay
    (≈min_alpha, preserving the image); a bright/busy photo needs more → up to
    max_alpha. Linear in luminance, clamped.
    """
    lum = relative_luminance(avg_hex)
    return round(min_alpha + (max_alpha - min_alpha) * max(0.0, min(1.0, lum)), 2)


def brand_overlay_gradient(secondary_hex: str, primary_hex: str, alpha: float) -> str:
    """A brand-tinted dark overlay layer (CSS gradient string, no image).

    Tinting the overlay toward the brand colours makes ANY photo harmonise with
    the theme — the modern duotone/brand-wash technique. Slightly lighter on the
    primary end so the brand hue reads without washing out the photo.
    """
    sr, sg, sb = _hex_to_rgb(secondary_hex)
    pr, pg, pb = _hex_to_rgb(primary_hex)
    a2 = round(alpha * 0.82, 2)
    return (
        f"linear-gradient(135deg, rgba({sr},{sg},{sb},{alpha}), "
        f"rgba({pr},{pg},{pb},{a2}))"
    )


def photo_background(avg_hex: str | None, url: str, secondary_hex: str, primary_hex: str) -> str:
    """Full `background-image` value: brand-tinted adaptive overlay over the photo.

    Falls back to a fixed mid overlay when the average colour is unknown.
    """
    alpha = overlay_alpha(avg_hex) if avg_hex else 0.55
    return f"{brand_overlay_gradient(secondary_hex, primary_hex, alpha)}, url('{url}')"


def _hue_sat(hex_color: str) -> tuple[float, float]:
    r, g, b = (c / 255.0 for c in _hex_to_rgb(hex_color))
    h, _l, s = colorsys.rgb_to_hls(r, g, b)
    return h * 360.0, s


def colors_harmonize(
    image_avg_hex: str, theme_primary_hex: str, *, max_hue_delta: float = 45.0, neutral_sat: float = 0.18
) -> bool:
    """Whether an un-overlaid image's colour sits comfortably with the theme.

    A near-neutral (low-saturation) image always harmonises. Otherwise its hue
    must be within `max_hue_delta` degrees of the theme's primary hue.
    """
    img_hue, img_sat = _hue_sat(image_avg_hex)
    if img_sat < neutral_sat:
        return True
    theme_hue, _ = _hue_sat(theme_primary_hex)
    delta = abs(((img_hue - theme_hue + 180.0) % 360.0) - 180.0)
    return delta <= max_hue_delta
