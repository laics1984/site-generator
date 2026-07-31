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
import math
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


# Text scrim: the layer that actually buys legibility, concentrated where the
# copy sits and faded out well before the far side, so the rest of the frame
# keeps the photograph almost untouched. Expressed as a multiple of the cast
# alpha, so a bright photo gets a stronger scrim on the same curve rather than a
# second hand-tuned ramp.
#
# The ratio is chosen so that cast and scrim COMPOUND to the pre-split single
# sheet (0.30→0.62 on the same luminance ramp) behind the copy: the headline
# lands on the same backdrop it always did — which is what keeps
# schema_builder._SCRIM_COMPOSITE_BG, and every ink derived from it, honest —
# while everything outside the scrim is roughly half as covered as before.
_TEXT_SCRIM_RATIO = 1.25
# Where the scrim starts easing off and where it reaches zero.
_TEXT_SCRIM_MID_STOP = "44%"
_TEXT_SCRIM_END_STOP = "78%"

# Where the hero's copy block sits, which is the only thing the scrim geometry
# needs to know. `center` is the historical treatment; the directional variants
# let the open side of the frame keep full saturation.
HeroAnchor = Literal["center", "left", "bottom-left"]


def text_scrim_gradient(
    secondary_hex: str, alpha: float, *, anchor: HeroAnchor = "center"
) -> str:
    """A legibility scrim weighted toward the copy (CSS gradient, no image).

    Peak alpha is identical across anchors — only the geometry moves — so the
    compounding invariant above holds whichever way the hero is composed.

    `center` keeps the original radial. It is the weakest of the three as art
    direction: a centre-weighted scrim dims the middle of the frame, which is
    where a photograph's subject almost always is, and leaves the corners
    bright. The directional variants run the darkness off the edge the copy is
    anchored to, so the subject stays vivid on the open side — the standard
    editorial treatment, and the reason a magazine cover doesn't look hazy.
    """
    r, g, b = _hex_to_rgb(secondary_hex)
    a = round(alpha * _TEXT_SCRIM_RATIO, 2)
    mid = round(a * 0.55, 2)
    if anchor == "left":
        return (
            f"linear-gradient(to right, rgba({r},{g},{b},{a}), "
            f"rgba({r},{g},{b},{mid}) {_TEXT_SCRIM_MID_STOP}, "
            f"rgba({r},{g},{b},0) {_TEXT_SCRIM_END_STOP})"
        )
    if anchor == "bottom-left":
        # Two axes: the copy sits in the corner, so darkness has to fall off
        # both upward and rightward or the headline's top line loses its
        # backdrop. Split the alpha between them so they compound to `a` in the
        # corner rather than doubling it. Rounded UP, not to nearest: two
        # rounded-down halves compound to less than `a`, which would quietly put
        # the corner below the legibility sheet every ink is derived from.
        half = math.ceil((1 - (1 - a) ** 0.5) * 100) / 100
        half_mid = round(half * 0.55, 2)
        return (
            f"linear-gradient(to top, rgba({r},{g},{b},{half}), "
            f"rgba({r},{g},{b},{half_mid}) 46%, rgba({r},{g},{b},0) 82%), "
            f"linear-gradient(to right, rgba({r},{g},{b},{half}), "
            f"rgba({r},{g},{b},{half_mid}) 46%, rgba({r},{g},{b},0) 82%)"
        )
    return (
        f"radial-gradient(115% 88% at 50% 50%, rgba({r},{g},{b},{a}), "
        f"rgba({r},{g},{b},{mid}) {_TEXT_SCRIM_MID_STOP}, "
        f"rgba({r},{g},{b},0) {_TEXT_SCRIM_END_STOP})"
    )


# Vignette strength, as a fraction of the cast alpha. Small on purpose: this is
# depth, not legibility. Anything heavier stops reading as a lens and starts
# reading as a black border.
_VIGNETTE_RATIO = 0.5


def vignette_gradient(secondary_hex: str, alpha: float) -> str:
    """Corner falloff — transparent through the middle, ink at the extremes.

    The inverse of the centre scrim, and the correct use of a radial here: it
    darkens only what the frame edges hold (usually nothing) and leaves the
    subject alone, which is what makes a flat composite read as a photograph
    with depth rather than a picture behind a sheet of colour.
    """
    r, g, b = _hex_to_rgb(secondary_hex)
    a = round(alpha * _VIGNETTE_RATIO, 2)
    return (
        f"radial-gradient(125% 105% at 50% 42%, rgba({r},{g},{b},0) 45%, "
        f"rgba({r},{g},{b},{a}) 100%)"
    )


def edge_fade_gradient(page_bg_hex: str) -> str:
    """A short dissolve from the photo into the page background at the bottom.

    Without it a full-bleed hero ends on a ruled horizontal line where the
    photograph stops and the next section's flat colour starts — the single
    most common tell that a page was assembled from bands rather than designed.
    """
    r, g, b = _hex_to_rgb(page_bg_hex)
    return (
        f"linear-gradient(to bottom, rgba({r},{g},{b},0) 72%, "
        f"rgba({r},{g},{b},0.55) 90%, rgba({r},{g},{b},1) 100%)"
    )


# Grain sits over everything. Faint enough to be felt rather than seen: its job
# is to break up the smooth gradient ramps (which band visibly on wide, dark
# heroes) and give the composite a surface.
_GRAIN_OPACITY = 0.055
_GRAIN_TILE_PX = 140


def _focal_position(focal_y: float | None, anchor: HeroAnchor) -> str:
    """`background-position` for the photo layer.

    Two independent nudges. Vertically, sit on the measured subject band
    (services/image_sampling) instead of hard-centering, so a 100dvh crop of a
    landscape photo doesn't slice through faces. Horizontally, bias AWAY from
    the copy, so the subject lands in the open half of the frame rather than
    behind the headline.
    """
    y = "center" if focal_y is None else f"{round(focal_y * 100)}%"
    x = "68%" if anchor in ("left", "bottom-left") else "center"
    return f"{x} {y}"


def photo_background(
    avg_hex: str | None,
    url: str,
    secondary_hex: str,
    primary_hex: str,
    *,
    anchor: HeroAnchor = "center",
    focal_y: float | None = None,
    page_bg_hex: str | None = None,
) -> dict[str, str]:
    """The full background style set for a photo hero, as CSS properties.

    Six layers, top to bottom: grain, an edge fade into the page, the copy
    scrim, a vignette, the brand cast, and the photograph. Splitting scrim from
    cast is what lets the overlay stay light without costing contrast — behind
    the headline they compound to roughly the old single sheet, while the rest
    of the frame carries only the faint cast and reads as a photograph. The
    vignette and grain add depth on top of that; the fade ties the section to
    the one below it.

    Returns a dict rather than a string because the layers need DIFFERENT
    sizing: the grain is a repeating 140px tile while everything else covers the
    frame. A single `background-size: cover` (which is what the catalog sets)
    would stretch one grain cell across the whole hero. All four properties must
    be applied together, and they override the template's values rather than
    merging with them.

    Falls back to a mid cast when the average colour is unknown, and to no edge
    fade when the page background is unknown.
    """
    # Lazy: style_tokens pulls in the theme chain, and this module is otherwise
    # a dependency-free leaf that the rest of the package imports freely.
    from app.services.style_tokens import grain_data_uri

    alpha = overlay_alpha(avg_hex) if avg_hex else 0.26

    layers = [grain_data_uri(_GRAIN_OPACITY)]
    sizes = [f"{_GRAIN_TILE_PX}px {_GRAIN_TILE_PX}px"]
    repeats = ["repeat"]
    positions = ["0 0"]

    def cover(layer: str) -> None:
        layers.append(layer)
        sizes.append("cover")
        repeats.append("no-repeat")
        positions.append("center")

    if page_bg_hex:
        cover(edge_fade_gradient(page_bg_hex))
    # text_scrim_gradient may itself return two comma-joined layers (the
    # bottom-left anchor), so expand it rather than assuming one.
    for scrim_layer in _split_layers(text_scrim_gradient(secondary_hex, alpha, anchor=anchor)):
        cover(scrim_layer)
    cover(vignette_gradient(secondary_hex, alpha))
    cover(brand_overlay_gradient(secondary_hex, primary_hex, alpha))

    layers.append(f"url('{url}')")
    sizes.append("cover")
    repeats.append("no-repeat")
    positions.append(_focal_position(focal_y, anchor))

    return {
        "backgroundImage": ", ".join(layers),
        "backgroundSize": ", ".join(sizes),
        "backgroundRepeat": ", ".join(repeats),
        "backgroundPosition": ", ".join(positions),
    }


def _split_layers(css: str) -> list[str]:
    """Split a CSS layer list on top-level commas (ignoring those inside
    `rgba(...)` / gradient parens)."""
    out: list[str] = []
    depth = start = 0
    for i, ch in enumerate(css):
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            out.append(css[start:i].strip())
            start = i + 1
    out.append(css[start:].strip())
    return [layer for layer in out if layer]


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
