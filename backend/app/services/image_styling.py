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
import re
from dataclasses import dataclass
from typing import Literal


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return "#{:02x}{:02x}{:02x}".format(
        max(0, min(255, r)), max(0, min(255, g)), max(0, min(255, b))
    )


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
# keeps the photograph almost untouched. With nothing measured it is a multiple
# of the cast alpha (the frame-average ramp below); with the photo measured it is
# SOLVED for the copy — see `_copy_scrim`.
#
# The ratio is chosen so that cast and scrim COMPOUND to the pre-split single
# sheet (0.30→0.62 on the same luminance ramp) behind the copy, while everything
# outside the scrim is roughly half as covered as before. It is also the floor of
# the solve: measuring never makes a scrim lighter than this ramp did.
_TEXT_SCRIM_RATIO = 1.25
# The scrim fades off its plateau to half-ish strength, then to nothing, over
# these spans (fractions of the gradient ray beyond the plateau).
_SCRIM_FALL_MID = 0.22
_SCRIM_FALL_END = 0.44
_SCRIM_MID_SHARE = 0.55

# Where the copy block sits, which is the only thing the scrim geometry needs to
# know. `center` is the historical treatment; the directional variants let the
# open side of the frame keep full saturation. `bottom` is a photo TILE's copy
# (a bento card sets its title along the bottom edge), never a hero composition.
HeroAnchor = Literal["center", "left", "bottom-left", "bottom"]

# The box the copy occupies, per anchor, as (x0, y0, x1, y1) fractions of the
# frame. Read off the layouts that put copy there: a centred hero's 760px column
# on a 1280px frame; an anchored hero's 620px column off its 80px gutter; a
# bento tile's bottom-aligned title and description. Two things answer to it,
# so they cannot disagree: the scrim holds full strength across it (its
# plateau), and the photo is judged inside it (`PhotoLuminance.behind`).
COPY_ZONES: dict[HeroAnchor, tuple[float, float, float, float]] = {
    "center": (0.2, 0.3, 0.8, 0.7),
    "left": (0.06, 0.3, 0.54, 0.7),
    "bottom-left": (0.06, 0.5, 0.54, 0.92),
    "bottom": (0.0, 0.55, 1.0, 1.0),
}
# The centred scrim's ellipse, as fractions of the frame's width and height.
_RADIAL_RADII = (1.15, 0.88)


def _pct(fraction: float) -> str:
    return f"{round(fraction * 100)}%"


def _scrim_ramp(rgb: tuple[int, int, int], peak: float, plateau: float) -> str:
    """The stops of one scrim layer: full strength across the copy (the
    plateau), then the historical fall-off to nothing."""
    r, g, b = rgb
    mid = round(peak * _SCRIM_MID_SHARE, 2)
    return (
        f"rgba({r},{g},{b},{peak}) {_pct(plateau)}, "
        f"rgba({r},{g},{b},{mid}) {_pct(plateau + _SCRIM_FALL_MID)}, "
        f"rgba({r},{g},{b},0) {_pct(plateau + _SCRIM_FALL_END)}"
    )


def text_scrim_gradient(
    secondary_hex: str,
    alpha: float,
    *,
    anchor: HeroAnchor = "center",
    scrim_alpha: float | None = None,
) -> str:
    """A legibility scrim weighted toward the copy (CSS gradient, no image).

    ``scrim_alpha`` is the strength the copy needs (`_copy_scrim` solves it);
    absent, it is the frame-average ramp off the cast ``alpha``. It holds flat
    across the anchor's whole `COPY_ZONES` box and only then falls off, so the
    last word of a line gets the backdrop its first word got — a scrim that
    peaked at one point would have to be darker there to be enough at the edge.

    `center` is the weakest composition as art direction: a centre-weighted
    scrim dims the middle of the frame, which is where a photograph's subject
    almost always is. The directional variants run the darkness off the edge
    the copy is anchored to, so the subject stays vivid on the open side.
    """
    rgb = _hex_to_rgb(secondary_hex)
    a = scrim_alpha if scrim_alpha is not None else round(alpha * _TEXT_SCRIM_RATIO, 2)
    x0, y0, x1, y1 = COPY_ZONES[anchor]
    if anchor == "left":
        return f"linear-gradient(to right, {_scrim_ramp(rgb, a, x1)})"
    if anchor == "bottom":
        return f"linear-gradient(to top, {_scrim_ramp(rgb, a, 1 - y0)})"
    if anchor == "bottom-left":
        # Two axes: the copy sits in the corner, so darkness has to fall off
        # both upward and rightward or the headline's top line loses its
        # backdrop. Split the alpha between them so they compound to `a` across
        # the copy rather than doubling it. Rounded UP, not to nearest: two
        # rounded-down halves compound to less than `a`.
        half = math.ceil((1 - (1 - a) ** 0.5) * 100) / 100
        return (
            f"linear-gradient(to top, {_scrim_ramp(rgb, half, 1 - y0)}), "
            f"linear-gradient(to right, {_scrim_ramp(rgb, half, x1)})"
        )
    rx, ry = _RADIAL_RADII
    plateau = math.hypot((x1 - 0.5) / rx, (y1 - 0.5) / ry)
    return (
        f"radial-gradient({_pct(rx)} {_pct(ry)} at 50% 50%, "
        f"{_scrim_ramp(rgb, a, plateau)})"
    )


# --- the copy scrim, solved for what is behind the copy ------------------------
#
# The scrim used to be sized from the photo's AVERAGE colour, and every ink on it
# was chosen against a constant assumed backdrop. Neither is what the copy sits
# on: stock photography puts its brightest region — a white wall, a white shirt,
# sky — in the middle of the frame, which is where the copy goes. Measured over
# 45 real stock heroes, 28 were too bright behind the copy for a white paragraph
# to reach 4.5:1 under the average-sized scrim.

# What the copy must clear: AA for body text. The hero's paragraph is the binding
# line — the headline is large, and carries a shadow.
COPY_MIN_CONTRAST = 4.5
# The solve aims this far above AA, for what the model leaves out: it reads a
# 24-cell grid of the photo, not the rendered cover-crop with its grain layer.
# Measured over 45 stock heroes in the preview renderer, the paragraph landed
# 0.05-0.25 under the solved ratio without it; with it, mean cover behind the
# copy rises 0.44 → 0.46 (the cap below still binds).
_COPY_SOLVE_MARGIN = 1.06
# The darkest the copy's backdrop is ever made — cast and scrim compounded. It is
# the single sheet every photo hero carried before the overlay was split into a
# faint cast and a copy scrim (0.30→0.62 on the luminance ramp), so measuring
# never darkens a hero past what this generator already shipped; and it is just
# enough for the palette's light ink on a pure-white region (~0.59).
_COPY_MAX_COVER = 0.62
# Which part of the copy zone has to read: the bright tail, not the average. A
# white shirt behind half a sentence makes the sentence unreadable whatever the
# frame averages to.
_COPY_ZONE_PERCENTILE = 0.9
# The backdrop assumed when nothing was measured — the legacy mid-grey photo.
_UNMEASURED_PHOTO_HEX = "#808080"


@dataclass(frozen=True)
class PhotoLuminance:
    """A photo's WCAG relative luminance on a coarse grid (row-major), as
    `image_sampling` read it off the pixels — enough to tell a bright centre
    from a bright corner, which is all the copy scrim needs to know."""

    cols: int
    rows: int
    cells: tuple[float, ...]

    @classmethod
    def uniform(cls, luminance: float) -> PhotoLuminance:
        """One value for the whole frame: a flat placeholder whose colour is
        known, or the assumption made for a photo that could not be read."""
        return cls(1, 1, (max(0.0, min(1.0, luminance)),))

    @classmethod
    def unreadable(cls) -> PhotoLuminance:
        """A real photo whose pixels could not be read. Assumed as bright as a
        photo can be: the one assumption legibility cannot be wrong under."""
        return cls.uniform(1.0)

    def behind(self, anchor: HeroAnchor) -> float:
        """The bright tail of the luminance inside the copy's zone."""
        x0, y0, x1, y1 = COPY_ZONES[anchor]
        cells = [
            self.cells[row * self.cols + col]
            for row in range(self.rows)
            for col in range(self.cols)
            if x0 <= (col + 0.5) / self.cols <= x1 and y0 <= (row + 0.5) / self.rows <= y1
        ]
        if not cells:  # a grid coarser than the zone: the whole frame stands in
            cells = list(self.cells)
        cells.sort()
        return cells[min(len(cells) - 1, int(len(cells) * _COPY_ZONE_PERCENTILE))]


def _grey_for_luminance(lum: float) -> str:
    """The neutral grey whose WCAG relative luminance is `lum`."""
    lum = max(0.0, min(1.0, lum))
    c = lum * 12.92 if lum <= 0.0031308 else 1.055 * lum ** (1 / 2.4) - 0.055
    v = round(c * 255)
    return _rgb_to_hex(v, v, v)


def _mix_hex(top_hex: str, alpha: float, bottom_hex: str) -> str:
    """`top_hex` at `alpha` over `bottom_hex`, blended in sRGB like CSS does."""
    return _rgb_to_hex(*_mix(bottom_hex, top_hex, alpha))


def _contrast_ratio(a_hex: str, b_hex: str) -> float:
    la, lb = relative_luminance(a_hex), relative_luminance(b_hex)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _cover_needed(photo_hex: str, scrim_hex: str, ink_hex: str, target: float) -> float:
    """The least compound cover of `scrim_hex` over `photo_hex` that lets
    `ink_hex` clear `target` — or 1.0 if no cover can."""
    if _contrast_ratio(ink_hex, photo_hex) >= target:
        return 0.0
    lo, hi = 0.0, 1.0
    for _ in range(24):
        mid = (lo + hi) / 2
        if _contrast_ratio(ink_hex, _mix_hex(scrim_hex, mid, photo_hex)) >= target:
            hi = mid
        else:
            lo = mid
    return hi


@dataclass(frozen=True)
class _CopyScrim:
    """One photo's overlay strengths and the backdrop they leave behind the copy."""

    cast: float  # whole-frame brand cast (and vignette base), wash-scaled
    scrim: float  # the copy scrim's strength across the copy zone
    surface_hex: str  # what the copy reads against: both, over the zone's bright tail


def _copy_scrim(
    avg_hex: str | None,
    secondary_hex: str,
    *,
    anchor: HeroAnchor,
    luminance: PhotoLuminance | None,
    ink_hex: str | None,
    wash_alpha_scale: float,
) -> _CopyScrim:
    """Size the copy scrim for the photo behind the copy, and say what it leaves.

    One home for both halves, because `photo_background` (which paints the
    scrim) and `photo_surface_hex` (which tells the ink pass what the copy sits
    on) must never disagree — the `_wash_base` rule for the split hero's wash.

    ``luminance`` None means nothing was measured (the PHOTO_SAMPLING_ENABLED
    kill switch): the frame-average ramp, exactly as before. Measured, the scrim
    is the least that lets ``ink_hex`` (the copy's light ink; absent, the
    lightest an ink can be) clear AA over the zone's bright tail — never lighter
    than the ramp, never past `_COPY_MAX_COVER`.
    """
    alpha = overlay_alpha(avg_hex) if avg_hex else 0.26
    cast = round(alpha * wash_alpha_scale, 2)
    scrim = round(alpha * _TEXT_SCRIM_RATIO, 2)
    if luminance is None:
        photo_hex = avg_hex or _UNMEASURED_PHOTO_HEX
    else:
        photo_hex = _grey_for_luminance(luminance.behind(anchor))
        need = min(
            _cover_needed(
                photo_hex, secondary_hex, ink_hex or _grey_for_luminance(1.0),
                COPY_MIN_CONTRAST * _COPY_SOLVE_MARGIN,
            ),
            _COPY_MAX_COVER,
        )
        solved = 1 - (1 - need) / (1 - cast) if cast < 1 else 0.0
        scrim = max(scrim, math.ceil(solved * 100) / 100)
    cover = 1 - (1 - cast) * (1 - scrim)
    return _CopyScrim(cast, scrim, _mix_hex(secondary_hex, cover, photo_hex))


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


# (alpha, stop%) of the bottom dissolve, as the literal strings the CSS carries.
# Shared by the writer and the matcher below so the two can never drift: if the
# ramp is retuned, `is_edge_fade_layer` keeps recognising it.
_EDGE_FADE_STOPS: tuple[tuple[str, int], ...] = (("0", 72), ("0.55", 90), ("1", 100))


def edge_fade_gradient(page_bg_hex: str) -> str:
    """A short dissolve from the photo into the page background at the bottom.

    Without it a full-bleed hero ends on a ruled horizontal line where the
    photograph stops and the next section's flat colour starts — the single
    most common tell that a page was assembled from bands rather than designed.

    The caller passes the THEME page background, because at build time the
    section below this one does not exist yet. Once the page is assembled,
    schema_builder.retune_photo_edge_fades re-points (or drops) the layer using
    the surface that actually follows — see `is_edge_fade_layer`.
    """
    r, g, b = _hex_to_rgb(page_bg_hex)
    stops = ", ".join(f"rgba({r},{g},{b},{a}) {pos}%" for a, pos in _EDGE_FADE_STOPS)
    return f"linear-gradient(to bottom, {stops})"


_EDGE_FADE_RE = re.compile(
    r"^linear-gradient\(\s*to bottom\s*,\s*"
    + r"\s*,\s*".join(
        rf"rgba\(\s*\d+\s*,\s*\d+\s*,\s*\d+\s*,\s*{re.escape(alpha)}\s*\)\s*{pos}%"
        for alpha, pos in _EDGE_FADE_STOPS
    )
    + r"\s*\)$"
)


def is_edge_fade_layer(layer: str) -> bool:
    """True if one background layer is an `edge_fade_gradient` output, whatever
    colour it fades to.

    The composite's other layers are all radial, or `to bottom` ramps that stop
    short of full opacity, so this shape is unambiguous — nothing else in
    `photo_background` fades a solid colour all the way in at 100%.
    """
    return bool(_EDGE_FADE_RE.match(layer.strip()))


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
    wash_alpha_scale: float = 1.0,
    luminance: PhotoLuminance | None = None,
    ink_hex: str | None = None,
) -> dict[str, str]:
    """The full background style set for a photo hero, as CSS properties.

    Six layers, top to bottom: grain, an edge fade into the page, the copy
    scrim, a vignette, the brand cast, and the photograph. Splitting scrim from
    cast is what lets the overlay stay light without costing contrast — the
    scrim is as strong as THIS photo needs behind the copy (`_copy_scrim`),
    while the rest of the frame carries only the faint cast and reads as a
    photograph. The vignette and grain add depth on top of that; the fade ties
    the section to the one below it, and is left out wherever it would run under
    the copy (a bottom-anchored block), since it dissolves toward the page
    colour — the colour the copy's light ink cannot read on.

    ``luminance`` is the photo measured (`image_sampling`) and ``ink_hex`` the
    light ink the copy will wear; `photo_surface_hex` reports, from the same
    inputs, the backdrop this leaves behind the copy.

    Returns a dict rather than a string because the layers need DIFFERENT
    sizing: the grain is a repeating 140px tile while everything else covers the
    frame. A single `background-size: cover` (which is what the catalog sets)
    would stretch one grain cell across the whole hero. All four properties must
    be applied together, and they override the template's values rather than
    merging with them.

    Falls back to a mid cast when the average colour is unknown, and to no edge
    fade when the page background is unknown.

    ``wash_alpha_scale`` (default 1.0, a no-op) scales ONLY the vignette and
    brand cast — the two layers whose job is binding the photo to the brand,
    not legibility — so a caller can let a photo read as more vivid/true-to-
    colour. A measured scrim makes up whatever the lighter cast gives away.
    """
    # Lazy: style_tokens pulls in the theme chain, and this module is otherwise
    # a dependency-free leaf that the rest of the package imports freely.
    from app.services.style_tokens import grain_data_uri

    alpha = overlay_alpha(avg_hex) if avg_hex else 0.26
    plan = _copy_scrim(
        avg_hex, secondary_hex, anchor=anchor, luminance=luminance,
        ink_hex=ink_hex, wash_alpha_scale=wash_alpha_scale,
    )
    wash_alpha = plan.cast

    layers = [grain_data_uri(_GRAIN_OPACITY)]
    sizes = [f"{_GRAIN_TILE_PX}px {_GRAIN_TILE_PX}px"]
    repeats = ["repeat"]
    positions = ["0 0"]

    def cover(layer: str) -> None:
        layers.append(layer)
        sizes.append("cover")
        repeats.append("no-repeat")
        positions.append("center")

    if page_bg_hex and COPY_ZONES[anchor][3] <= _EDGE_FADE_STOPS[0][1] / 100:
        cover(edge_fade_gradient(page_bg_hex))
    # text_scrim_gradient may itself return two comma-joined layers (the
    # bottom-left anchor), so expand it rather than assuming one.
    for scrim_layer in _split_layers(
        text_scrim_gradient(secondary_hex, alpha, anchor=anchor, scrim_alpha=plan.scrim)
    ):
        cover(scrim_layer)
    cover(vignette_gradient(secondary_hex, wash_alpha))
    cover(brand_overlay_gradient(secondary_hex, primary_hex, wash_alpha))

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


def photo_surface_hex(
    avg_hex: str | None,
    secondary_hex: str,
    *,
    anchor: HeroAnchor = "center",
    luminance: PhotoLuminance | None = None,
    ink_hex: str | None = None,
    wash_alpha_scale: float = 1.0,
) -> str:
    """The flat colour the copy on a `photo_background` reads against — the
    cast and scrim it paints, over the bright tail of the photo behind the copy.

    Same inputs as `photo_background`, same plan (`_copy_scrim`), so the painted
    backdrop and the measured one cannot disagree. A painter records this on the
    element it paints (`BuilderElement.paint_surface`); the ink pass reads it."""
    return _copy_scrim(
        avg_hex, secondary_hex, anchor=anchor, luminance=luminance,
        ink_hex=ink_hex, wash_alpha_scale=wash_alpha_scale,
    ).surface_hex


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


# The SPLIT hero's wash (see `washed_photo_background`). Near-opaque on purpose:
# this overlay is not a legibility scrim over a focal photo, it is the section's
# surface, with the photo reading through as faint on-brand texture. At 0.80 the
# photo beneath can shift the surface by a fifth — never across the light/dark
# line — which is what lets `washed_surface_hex` measure the copy's real backdrop.
_WASH_BASE_ALPHA = 0.80
_WASH_TINT_ALPHA_LIGHT = 0.10
_WASH_TINT_ALPHA_DARK = 0.18


def _wash_base(scheme: str, surface_hex: str, secondary_hex: str) -> tuple[str, float]:
    """The wash's BASE stop — the colour the hero's copy actually sits over — as
    (hex, alpha). Light scheme washes with the theme surface, dark with its
    secondary; both at the same near-opaque alpha.

    One home for the pair, because `washed_photo_background` (which paints it)
    and `washed_surface_hex` (which measures it, to pick the ink printed on it)
    must never disagree — a wash whose measured surface differs from the painted
    one hands the copy exactly the wrong colour.
    """
    return (secondary_hex if scheme == "dark" else surface_hex), _WASH_BASE_ALPHA


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
    brand tint (light text stays legible) — which is only true of ink chosen for
    THAT surface, so the caller pairs this with `washed_surface_hex`.
    """
    pr, pg, pb = _hex_to_rgb(primary_hex)
    base_hex, base_a = _wash_base(scheme, surface_hex, secondary_hex)
    br, bg, bb = _hex_to_rgb(base_hex)
    tint_a = _WASH_TINT_ALPHA_DARK if scheme == "dark" else _WASH_TINT_ALPHA_LIGHT
    return (
        f"linear-gradient(135deg, rgba({br},{bg},{bb},{base_a}), "
        f"rgba({pr},{pg},{pb},{tint_a})), url('{url}')"
    )


def washed_surface_hex(
    *,
    scheme: str,
    surface_hex: str,
    secondary_hex: str,
    avg_hex: str | None = None,
) -> str:
    """The flat colour a washed hero's COPY sits on — the wash's base stop
    composited over the photo's average colour.

    `washed_photo_background` runs its gradient at 135deg, so the base (opaque)
    end is the top-left corner, which is where the copy column lives; the faint
    brand-tint end falls in the opposite corner, over the image column. So the
    copy's surface is the base stop, and the photo can only shift it by the
    remaining 20%.

    Unknown average colour → the base stop alone. That is the wash's own intent
    (light surface in a light scheme, dark in a dark one) and the photo it omits
    cannot move the result across the light/dark line at this alpha.
    """
    base_hex, base_a = _wash_base(scheme, surface_hex, secondary_hex)
    try:
        photo_rgb = _hex_to_rgb(avg_hex) if avg_hex else None
    except ValueError:  # an unparseable colour is the same as no colour
        photo_rgb = None
    if photo_rgb is None:
        return base_hex
    return _rgb_to_hex(
        *(
            round(base_a * b + (1.0 - base_a) * p)
            for b, p in zip(_hex_to_rgb(base_hex), photo_rgb)
        )
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
