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
from typing import Literal


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


# Luminance midpoint that splits the light vs dark band. Matches the white /
# near-black flip in theme._text_for_background, so a section's band and its font
# colour always agree. See SECTION_VISUAL_POLICY_SPEC.md §4.3/§6.
BAND_LUMINANCE_THRESHOLD = 0.5


def band_for_luminance(
    lum: float, *, threshold: float = BAND_LUMINANCE_THRESHOLD
) -> Literal["light", "dark"]:
    """Classify a WCAG relative luminance into a luminance band."""
    return "light" if lum >= threshold else "dark"


def band_for_color(
    avg_hex: str, *, threshold: float = BAND_LUMINANCE_THRESHOLD
) -> Literal["light", "dark"]:
    """Classify a dominant/average colour hex into a luminance band."""
    return band_for_luminance(relative_luminance(avg_hex), threshold=threshold)


# Full-frame cast strength. This layer covers the WHOLE photo, including the
# parts no text sits on, so it is kept faint on purpose: its job is to bind the
# photo to the brand, not to dim it. Legibility is bought separately, by the
# centre scrim below, where the copy actually is — a uniform sheet heavy enough
# for a bright photo's headline also flattens the 70% of the frame that has
# nothing on it, which is what makes a hero read as a colour block with a
# picture buried in it rather than as a photograph.
_CAST_MIN_ALPHA = 0.14  # already-dark photo: barely a tint
_CAST_MAX_ALPHA = 0.34  # bright/busy photo: still see-through


def overlay_alpha(
    avg_hex: str, *, min_alpha: float = _CAST_MIN_ALPHA, max_alpha: float = _CAST_MAX_ALPHA
) -> float:
    """Pick the dark-overlay opacity from the photo's average luminance.

    A dark photo already provides contrast for white text → light overlay
    (≈min_alpha, preserving the image); a bright/busy photo needs more → up to
    max_alpha. Linear in luminance, clamped.
    """
    lum = relative_luminance(avg_hex)
    return round(min_alpha + (max_alpha - min_alpha) * max(0.0, min(1.0, lum)), 2)


# How far the overlay's BRAND end is pulled toward the ink end before it is
# painted. A saturated primary composited at ~0.5 alpha does not tint a photo,
# it repaints it: hue information in the pixels underneath is replaced, so a
# guitar, a classroom and a plate of food all arrive as the same flat sheet of
# brand colour — duotone applied as a default rather than chosen. Darkening the
# same pixels preserves their hue relationships, so mixing the primary toward
# the ink keeps the photograph legible AS a photograph while the gradient still
# runs visibly on-brand. Alpha is deliberately NOT lowered to achieve this: the
# white headline's contrast comes from that alpha.
_BRAND_END_INK_MIX = 0.55


def _mix(a_hex: str, b_hex: str, t: float) -> tuple[int, int, int]:
    """`a` moved `t` of the way toward `b` in sRGB."""
    ar, ag, ab = _hex_to_rgb(a_hex)
    br, bg, bb = _hex_to_rgb(b_hex)
    return (
        round(ar + (br - ar) * t),
        round(ag + (bg - ag) * t),
        round(ab + (bb - ab) * t),
    )


def brand_overlay_gradient(secondary_hex: str, primary_hex: str, alpha: float) -> str:
    """A brand-tinted dark overlay layer (CSS gradient string, no image).

    Tinting the overlay toward the brand colours makes any photo harmonise with
    the theme, but the tint is a cast, not a repaint: the brand end is mixed
    toward the ink first (see `_BRAND_END_INK_MIX`), so the photo keeps its own
    colours instead of arriving as a flat sheet of the brand hue.
    """
    sr, sg, sb = _hex_to_rgb(secondary_hex)
    pr, pg, pb = _mix(primary_hex, secondary_hex, _BRAND_END_INK_MIX)
    a2 = round(alpha * 0.82, 2)
    return (
        f"linear-gradient(135deg, rgba({sr},{sg},{sb},{alpha}), "
        f"rgba({pr},{pg},{pb},{a2}))"
    )


# Centre scrim: the layer that actually buys legibility, concentrated where the
# copy sits (photo heroes and CTA bands centre their headline block) and faded
# out well before the edges, so the frame's corners keep the photograph almost
# untouched. Expressed as a multiple of the cast alpha, so a bright photo gets a
# stronger scrim on the same curve rather than a second hand-tuned ramp.
#
# The ratio is chosen so that cast and scrim COMPOUND to the pre-split single
# sheet (0.30→0.62 on the same luminance ramp) behind the copy: the headline
# lands on the same backdrop it always did — which is what keeps
# schema_builder._SCRIM_COMPOSITE_BG, and every ink derived from it, honest —
# while everything outside the scrim is roughly half as covered as before.
_TEXT_SCRIM_RATIO = 1.25
# Where the scrim starts easing off and where it reaches zero, as ellipse radii.
_TEXT_SCRIM_MID_STOP = "44%"
_TEXT_SCRIM_END_STOP = "78%"


def text_scrim_gradient(secondary_hex: str, alpha: float) -> str:
    """A centre-weighted legibility scrim (CSS gradient string, no image)."""
    r, g, b = _hex_to_rgb(secondary_hex)
    a = round(alpha * _TEXT_SCRIM_RATIO, 2)
    return (
        f"radial-gradient(115% 88% at 50% 50%, rgba({r},{g},{b},{a}), "
        f"rgba({r},{g},{b},{round(a * 0.55, 2)}) {_TEXT_SCRIM_MID_STOP}, "
        f"rgba({r},{g},{b},0) {_TEXT_SCRIM_END_STOP})"
    )


def photo_background(avg_hex: str | None, url: str, secondary_hex: str, primary_hex: str) -> str:
    """Full `background-image` value: photo, a faint brand cast over the whole
    frame, and a centre scrim behind the copy.

    Splitting the two is what lets the overlay be light without costing
    contrast: behind the headline the layers compound to roughly the old single
    sheet, while the rest of the frame carries only the cast and reads as a
    photograph. Falls back to a mid cast when the average colour is unknown.
    """
    alpha = overlay_alpha(avg_hex) if avg_hex else 0.26
    return (
        f"{text_scrim_gradient(secondary_hex, alpha)}, "
        f"{brand_overlay_gradient(secondary_hex, primary_hex, alpha)}, "
        f"url('{url}')"
    )


def washed_photo_background(
    url: str,
    *,
    scheme: str,
    surface_hex: str,
    secondary_hex: str,
    primary_hex: str,
) -> str:
    """Background-image value for a SPLIT hero: an abstract photo under a heavy,
    scheme-aware brand wash.

    Unlike `photo_background` (a darker overlay tuned for white text on a
    full-bleed hero), this keeps the section on the theme's own luminance so the
    overlaid copy keeps its normal theme text colour and stays legible. The photo
    reads as faint on-brand texture, not a focal image.

    Light scheme → near-opaque light surface wash + a faint brand tint (dark text
    stays legible). Dark scheme → near-opaque dark wash + a slightly stronger
    brand tint (light text stays legible).
    """
    pr, pg, pb = _hex_to_rgb(primary_hex)
    if scheme == "dark":
        br, bg, bb = _hex_to_rgb(secondary_hex)
        base_a, tint_a = 0.80, 0.18
    else:
        br, bg, bb = _hex_to_rgb(surface_hex)
        base_a, tint_a = 0.80, 0.10
    return (
        f"linear-gradient(135deg, rgba({br},{bg},{bb},{base_a}), "
        f"rgba({pr},{pg},{pb},{tint_a})), url('{url}')"
    )


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


def color_distance(image_avg_hex: str, theme_hex: str) -> float:
    """A 0..1 distance between an image's average colour and a theme colour.

    Used to rank abstract stock candidates so the one whose dominant colour sits
    CLOSEST to the theme wins (vs. a binary harmonise gate). Combines hue delta
    (the dominant signal — a clashing hue reads worst) with a smaller luminance
    delta so a same-hue-but-wrong-brightness wash is still mildly penalised.

    A near-neutral (low-saturation) image is treated as hue-agnostic — only its
    luminance distance counts — so a clean grey/white texture never loses to a
    saturated off-hue one just because grey has an arbitrary hue.
    """
    img_hue, img_sat = _hue_sat(image_avg_hex)
    theme_hue, _ = _hue_sat(theme_hex)
    if img_sat < 0.18:
        hue_term = 0.0
    else:
        # Normalised hue gap in [0,1] (180° apart = max).
        hue_term = abs(((img_hue - theme_hue + 180.0) % 360.0) - 180.0) / 180.0
    lum_term = abs(relative_luminance(image_avg_hex) - relative_luminance(theme_hex))
    return round(0.75 * hue_term + 0.25 * lum_term, 4)
