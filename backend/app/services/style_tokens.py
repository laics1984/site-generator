"""Pure style/CSS helpers for the schema builder.

Extracted from ``schema_builder`` to keep that module focused on tree assembly.
Everything here is a leaf: it depends only on the ``ThemeTokens`` model, the
colour math in ``services.theme``, and stdlib — never on the section/hero
builders — so it carries no import cycle. ``schema_builder`` re-imports these
names, so the public path ``app.services.schema_builder.<name>`` is unchanged.

All helpers return plain CSS *value* strings (or flat style dicts) that flow
straight through the BuilderElement ``styles`` channel into both the builder
editor and the webtree-public renderer — no schema or renderer change needed.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from typing import Any

from app.models.brand import ThemeTokens
from app.services.design_schemes import DesignScheme, for_theme
from app.services.theme import (
    _adjust_lightness,
    _ensure_contrast_against,
    _hex_to_rgb,
    _hls_to_rgb,
    _relative_luminance,
    _rgb_to_hex,
    _rgb_to_hls,
)


@dataclass(frozen=True)
class TypeRamp:
    """Heading/copy size ceilings in px, before the fluid ``clamp()``.

    These were seven literals inline in ``make_style_tokens`` and therefore
    identical on every site this generator has ever produced. As data they can
    be scaled per design scheme; the defaults ARE the historical values, so a
    neutral scheme reproduces the old output exactly.
    """

    xl: float = 56.0
    lg: float = 44.0
    md: float = 32.0
    mobile: float = 28.0
    subhead: float = 19.0
    body: float = 16.0
    eyebrow: float = 13.0


@dataclass(frozen=True)
class SpacingScale:
    """Card/button/measure metrics in px. Same story as ``TypeRamp``.

    Deliberately absent: ``gap`` on section shells and grids, and ``minHeight``
    on feature/service/testimonial cards. The renderers pin both by node NAME
    with ``!important`` (see design_schemes.RENDERER_PINNED_* and
    webtree-public/lib/responsiveRuntime.ts), so a value here would lose on the
    published site while winning in neither editor — a divergence, not a knob.
    """

    card_padding: float = 28.0
    card_gap: float = 12.0
    button_padding_y: float = 13.0
    button_padding_x: float = 26.0
    ghost_padding_y: float = 12.0
    ghost_padding_x: float = 22.0
    subhead_measure: float = 640.0


def type_ramp_for(scheme: DesignScheme) -> TypeRamp:
    """The scheme's type ramp. Only the display tiers move — body copy and the
    eyebrow stay at their readable sizes whatever the scheme's ambition, because
    a 1.55 ratio applied to 16px body text is a legibility regression, not a
    design. The ratio reaches display type through ``boost`` instead."""
    return TypeRamp()


def spacing_scale_for(scheme: DesignScheme) -> SpacingScale:
    """The scheme's spacing metrics. ``card_padding_scale`` moves the card's own
    breathing room; the measure moves with it so a roomier card doesn't end up
    with a line length the eye can't track back from."""
    base = SpacingScale()
    cps = scheme.card_padding_scale
    if cps == 1.0:
        return base
    return SpacingScale(
        card_padding=round(base.card_padding * cps),
        card_gap=round(base.card_gap * min(cps, 1.35)),
        button_padding_y=base.button_padding_y,
        button_padding_x=base.button_padding_x,
        ghost_padding_y=base.ghost_padding_y,
        ghost_padding_x=base.ghost_padding_x,
        subhead_measure=base.subhead_measure,
    )


def tracking_ramp(base: str) -> tuple[str, str, str]:
    """(xl, lg, md) letter-spacing from one value, keeping today's graded ramp.

    The historical trio was -0.02em / -0.015em / -0.01em — exactly 1.0 / 0.75 /
    0.5 of the first — so passing the default reproduces it to the character.
    A value this can't parse is passed through unchanged on all three tiers,
    which is wrong-looking rather than crashing.
    """
    try:
        value = float(base.strip().removesuffix("em"))
    except (AttributeError, ValueError):
        return (base, base, base)
    return tuple(f"{round(value * r, 4):g}em" for r in (1.0, 0.75, 0.5))  # type: ignore[return-value]


def card_surface(
    theme: ThemeTokens, scheme: DesignScheme, spacing: SpacingScale
) -> dict[str, Any]:
    """The card surface for the scheme's treatment.

    Each treatment is *safe by construction* on any band, because the fill and
    the frame are decided together rather than independently:

    * ``bordered`` — hairline, no lift. The frame does the separating.
    * ``elevated`` — no frame, the shadow does it.
    * ``flat``     — neither, and no fill either: the card dissolves into the
      band and the grid gap separates. Safe precisely BECAUSE there is no
      surface — a filled card with no frame is the one combination that can go
      invisible against a matching band.
    * ``glass``    — frosted pane (needs a backdrop worth blurring; the
      one-accent-per-page texture rule in ``modernize_sections`` keeps glass and
      texture off the same surface).
    * ``outline``  — a deliberate 2px rule in the brand ink, no lift.

    Text colour is not set here: ``section_content.enforce_text_contrast`` runs
    over the finished tree and flips ink per resolved band, so a transparent
    card on a dark band still reads.
    """
    palette = theme.palette
    radius = f"{max(8, theme.buttons.radius + 4)}px"
    padding = f"{spacing.card_padding:.0f}px"
    gap = f"{spacing.card_gap:.0f}px"
    treatment = scheme.card_treatment
    if treatment == "inherit":
        # Key order matters: these styles are serialized into the BuilderElement
        # JSON, so the legacy branch keeps the historical insertion order and
        # the neutral scheme's output stays byte-identical, not merely equal.
        return {
            "padding": padding,
            "borderRadius": radius,
            "backgroundColor": palette.background,
            "border": f"1px solid {_hairline(palette.secondary)}",
            "gap": gap,
            "boxShadow": shadow(getattr(theme, "shadow_scale", "soft")),
        }
    base: dict[str, Any] = {"padding": padding, "borderRadius": radius, "gap": gap}
    if treatment == "bordered":
        return {
            **base,
            "backgroundColor": palette.background,
            "border": f"1px solid {_hairline(palette.secondary, alpha=0.14)}",
            "boxShadow": "none",
        }
    if treatment == "elevated":
        return {
            **base,
            "backgroundColor": palette.background,
            "border": "none",
            "boxShadow": shadow(getattr(theme, "shadow_scale", "elevated")),
        }
    if treatment == "flat":
        return {
            **base,
            "backgroundColor": "transparent",
            "border": "none",
            "boxShadow": "none",
        }
    if treatment == "outline":
        return {
            **base,
            "backgroundColor": "transparent",
            "border": f"2px solid {_hairline(palette.secondary, alpha=0.22)}",
            "boxShadow": "none",
        }
    # "glass" — the frosted pane owns its own fill/border/shadow.
    return {**base, **glass_card_styles(theme)}


def eyebrow_styles(
    theme: ThemeTokens, scheme: DesignScheme, ramp: TypeRamp
) -> dict[str, Any]:
    """The small label above a section heading, per the scheme's treatment.

    All six are pure presentation — none changes what the label says, so the
    treatment can vary freely without touching content or grounding. ``hidden``
    sets ``display: none`` rather than dropping the node, so the builder's user
    can bring it back and the tree stays comparable across schemes.
    """
    typo = theme.typography
    accent = emphasis_ink(theme)
    caps: dict[str, Any] = {
        "fontFamily": typo.body_font,
        "fontSize": f"{ramp.eyebrow:.0f}px",
        "fontWeight": 600,
        "letterSpacing": "0.14em",
        "textTransform": "uppercase",
        "color": accent,
        "margin": "0",
    }
    treatment = scheme.eyebrow_treatment
    if treatment in ("inherit", "caps-tracked"):
        return caps
    if treatment == "sentence":
        return {
            **caps,
            "fontSize": f"{ramp.eyebrow + 1:.0f}px",
            "letterSpacing": "0",
            "textTransform": "none",
            "color": meta_ink(theme),
        }
    if treatment == "rule":
        return {
            **caps,
            # currentColor, not the accent hex: the rule has to track whatever
            # ink this label ends up with, and that is not knowable here. The
            # accent is AA-corrected against the PAGE background, but a section
            # can resolve to the opposite band, and `enforce_text_contrast`
            # fixes `color` per band afterwards — it does not know about
            # borders. Inheriting means the rule is corrected for free; a hex
            # would have been a pale rule on a pale band on half the sites.
            "borderLeft": "3px solid currentColor",
            "paddingLeft": "10px",
            "display": "inline-flex",
            "alignItems": "center",
            "alignSelf": "flex-start",
        }
    if treatment == "chip":
        return {
            **caps,
            # A translucent tint rather than a solid fill, for the same reason:
            # 13% of the accent over a light band is a pale wash and over a dark
            # one a faint lift, so the chip reads on either without knowing
            # which it landed on. Its own ink is corrected per band downstream.
            "backgroundColor": _hairline(theme.palette.accent, alpha=0.13),
            "paddingTop": "6px",
            "paddingBottom": "6px",
            "paddingLeft": "13px",
            "paddingRight": "13px",
            "borderRadius": "999px",
            "display": "inline-flex",
            "alignItems": "center",
            "alignSelf": "flex-start",
        }
    if treatment == "underline":
        return {
            **caps,
            "borderBottom": "2px solid currentColor",  # see "rule" above
            "paddingBottom": "6px",
            "display": "inline-block",
            "alignSelf": "flex-start",
        }
    return {**caps, "display": "none"}


@dataclass
class StyleTokens:
    """Per-theme style namespace used by all section builders."""

    theme: ThemeTokens
    heading_xl: dict[str, Any]
    heading_lg: dict[str, Any]
    heading_md: dict[str, Any]
    heading_mobile: dict[str, Any]
    subhead: dict[str, Any]
    eyebrow: dict[str, Any]
    body: dict[str, Any]
    card: dict[str, Any]
    primary_button_styles: dict[str, Any]
    secondary_button_styles: dict[str, Any]
    glass_card: dict[str, Any] | None = None
    # The design scheme this vocabulary was built under. Section builders read
    # it off here rather than re-resolving it, so there is one lookup per site.
    scheme: DesignScheme | None = None

    @property
    def cards(self) -> dict[str, Any]:
        """Card surface to use: frosted glass when the mood enables it, else the
        standard opaque card. A fresh copy each access so per-card overrides
        (spreads like ``{**s.cards, "padding": "32px"}``) never mutate shared state."""
        return dict(self.glass_card or self.card)


def make_style_tokens(theme: ThemeTokens) -> StyleTokens:
    palette = theme.palette
    typo = theme.typography
    # The scheme rides on the theme (ThemeTokens.design_scheme), so this is the
    # only place the whole style vocabulary needs to look it up. A theme built
    # with the kill switch off yields the neutral scheme, whose every branch
    # below is the pre-scheme literal.
    scheme = for_theme(theme)
    ramp = type_ramp_for(scheme)
    spacing = spacing_scale_for(scheme)
    track_xl, track_lg, track_md = tracking_ramp(scheme.heading_tracking or "-0.02em")
    heading_weight = scheme.heading_weight or 700
    # Body/heading ink: `secondary` in the light scheme (a dark neutral tuned
    # for white/surface backgrounds), but in the dark scheme `secondary` is
    # itself one of the darkest tokens (the CTA-band colour) — using it as text
    # ink there put near-black copy on a near-black card. `text` is the token
    # every palette constructor WCAG-guards against `background` specifically
    # for this job (see ColorPalette docstring), so the dark scheme reads from
    # it instead.
    ink = palette.text if getattr(theme, "color_scheme", "light") == "dark" else palette.secondary

    # Fluid type: ceilings scale with the mood's type-scale ratio (1.25 = the
    # previous fixed look), and every tier is a clamp() so it breathes across
    # viewports without per-breakpoint overrides. Display tiers may use a
    # distinct display_font (e.g. Fraunces/Playfair) when the mood sets one.
    boost = getattr(theme, "type_scale_ratio", 1.25) / 1.25
    display_font = getattr(theme, "display_font", None) or typo.heading_font

    heading_xl = {
        "fontFamily": display_font,
        "fontSize": _fluid_heading(ramp.xl, boost),
        "fontWeight": heading_weight,
        "lineHeight": "1.05",
        "color": ink,
        "margin": "0",
        "letterSpacing": track_xl,
    }
    heading_lg = {
        "fontFamily": display_font,
        "fontSize": _fluid_heading(ramp.lg, boost),
        "fontWeight": heading_weight,
        "lineHeight": "1.1",
        "color": ink,
        "margin": "0",
        "letterSpacing": track_lg,
    }
    heading_md = {
        "fontFamily": typo.heading_font,
        "fontSize": _fluid_heading(ramp.md, boost),
        "fontWeight": heading_weight,
        "lineHeight": "1.15",
        "color": ink,
        "margin": "0",
        "letterSpacing": track_md,
    }
    heading_mobile = {"fontSize": f"{ramp.mobile:.0f}px"}
    subhead = {
        "fontFamily": typo.body_font,
        "fontSize": f"{ramp.subhead:.0f}px",
        "lineHeight": "1.55",
        "color": _muted(ink),
        "margin": "0",
        "maxWidth": f"{spacing.subhead_measure:.0f}px",
    }
    eyebrow = eyebrow_styles(theme, scheme, ramp)
    body = {
        "fontFamily": typo.body_font,
        "fontSize": f"{ramp.body:.0f}px",
        "lineHeight": "1.65",
        "color": _muted(ink),
        "margin": "0",
    }
    card = card_surface(theme, scheme, spacing)
    primary_button = {
        "color": theme.buttons.text,
        "backgroundColor": theme.buttons.background,
        "paddingTop": f"{spacing.button_padding_y:.0f}px",
        "paddingBottom": f"{spacing.button_padding_y:.0f}px",
        "paddingLeft": f"{spacing.button_padding_x:.0f}px",
        "paddingRight": f"{spacing.button_padding_x:.0f}px",
        "borderRadius": f"{theme.buttons.radius}px",
        "textDecoration": "none",
        "display": "inline-flex",
        "alignItems": "center",
        "justifyContent": "center",
        "fontWeight": 600,
        "fontFamily": typo.body_font,
        "fontSize": "15px",
        "minHeight": "44px",
        "transition": "transform 120ms ease, opacity 120ms ease",
    }
    secondary_button = {
        "color": ink,
        "backgroundColor": "transparent",
        "paddingTop": f"{spacing.ghost_padding_y:.0f}px",
        "paddingBottom": f"{spacing.ghost_padding_y:.0f}px",
        "paddingLeft": f"{spacing.ghost_padding_x:.0f}px",
        "paddingRight": f"{spacing.ghost_padding_x:.0f}px",
        "borderRadius": f"{theme.buttons.radius}px",
        "border": f"1px solid {_hairline(palette.secondary, alpha=0.18)}",
        "textDecoration": "none",
        "display": "inline-flex",
        "alignItems": "center",
        "justifyContent": "center",
        "fontWeight": 600,
        "fontFamily": typo.body_font,
        "fontSize": "15px",
        "minHeight": "44px",
    }

    return StyleTokens(
        theme=theme,
        heading_xl=heading_xl,
        heading_lg=heading_lg,
        heading_md=heading_md,
        heading_mobile=heading_mobile,
        subhead=subhead,
        eyebrow=eyebrow,
        body=body,
        card=card,
        primary_button_styles=primary_button,
        secondary_button_styles=secondary_button,
        glass_card=glass_card_styles(theme) if getattr(theme, "use_glass", False) else None,
        scheme=scheme,
    )


def _muted(hex_color: str) -> str:
    """Return a slightly faded hex for body copy — keeps WCAG AA but reads softer."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    # Lift 25% toward neutral grey for readability without losing tone.
    nr = round(r + (90 - r) * 0.25)
    ng = round(g + (96 - g) * 0.25)
    nb = round(b + (110 - b) * 0.25)
    return f"#{nr:02x}{ng:02x}{nb:02x}"


def _accent_ink_for(surface: str, accent: str) -> str:
    """Accent ink that stays recognisably the accent hue on any surface.

    Light surfaces: darken the accent until AA. Dark surfaces (scrims, brand
    gradients): a plain AA lift can bleach the accent to pure white, erasing
    the highlight — re-emit it as a high-lightness pastel of the SAME hue
    first, then nudge for AA."""
    if _relative_luminance(surface) >= 0.5:
        return _ensure_contrast_against(surface, accent, min_ratio=4.5)
    h, _l, s = _rgb_to_hls(*_hex_to_rgb(accent))
    pastel = _rgb_to_hex(*_hls_to_rgb(h, 0.82, min(1.0, max(s, 0.55))))
    return _ensure_contrast_against(surface, pastel, min_ratio=4.5)


def emphasis_ink(theme: ThemeTokens, surface: str | None = None) -> str:
    """Colour for a decorative, non-CTA 'pop' element (eyebrows, badges, step
    numbers, stat big-numbers, single accent borders/glows) — always the brand
    accent, AA-corrected against `surface` (defaults to the page background).
    NOT for full section/card backgrounds — accent is banned there by
    SECTION_VISUAL_POLICY_SPEC §7; text/border/shadow-tint call sites only."""
    return _accent_ink_for(surface or theme.palette.background, theme.palette.accent)


def brand_ink(theme: ThemeTokens, surface: str | None = None) -> str:
    """Colour for text that should read as the brand PRIMARY (quiet CTA links,
    role/designation labels) — AA-corrected against `surface` (defaults to the
    page background), the same guarantee `emphasis_ink` gives the accent.

    `palette.primary` alone is not AA-safe as text: it is picked/curated for
    button fills and washes, where a 3:1-ish contrast against white is normal
    (large filled shape, not small text). Several curated palettes land as low
    as ~2.1:1 there (e.g. the Coworking/Studio amber) — reading as a washed-out
    near-invisible line when used as raw text, which `enforce_text_contrast`'s
    safety net deliberately leaves alone (brand colour is assumed intentional).
    Call sites that print `palette.primary` as a `color` must go through this
    instead, exactly as accent call sites go through `emphasis_ink`."""
    return _accent_ink_for(surface or theme.palette.background, theme.palette.primary)


def meta_ink(theme: ThemeTokens) -> str:
    """Colour for de-emphasised informational/meta text (role labels, prices,
    dates) that shouldn't carry brand colour — the same muted-secondary tone
    already used for body copy, so meta text reads calmly instead of as a
    miniature CTA."""
    palette = theme.palette
    ink = palette.text if getattr(theme, "color_scheme", "light") == "dark" else palette.secondary
    return _muted(ink)


def _hairline(hex_color: str, alpha: float = 0.10) -> str:
    """rgba border colour for subtle dividers."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"


def _fluid(min_px: float, max_px: float) -> str:
    """A responsive `clamp()` font-size that scales with the viewport.

    Floor at `min_px` (mobile), ceiling at `max_px` (≈1280px wide). The middle
    term is vw-based: at a 1280px viewport, `vw * 12.8 ≈ px`, so we pick the vw
    that lands on `max_px` there and let clamp() hold the floor on small screens.
    """
    mid_vw = round(max_px / 12.8, 2)
    return f"clamp({round(min_px)}px, {mid_vw}vw, {round(max_px)}px)"


def _fluid_heading(max_px: float, boost: float, *, floor_ratio: float = 0.62) -> str:
    """Fluid size for a heading tier. `boost` scales the ceiling by the theme's
    type-scale ratio; the mobile floor is `floor_ratio` of the (boosted) ceiling."""
    ceiling = max_px * boost
    return _fluid(ceiling * floor_ratio, ceiling)


_SHADOWS: dict[str, str] = {
    "soft": "0 1px 2px rgba(15,23,42,0.06), 0 4px 12px rgba(15,23,42,0.05)",
    "elevated": "0 2px 4px rgba(15,23,42,0.06), 0 12px 28px rgba(15,23,42,0.10)",
    "dramatic": "0 4px 8px rgba(15,23,42,0.08), 0 24px 56px rgba(15,23,42,0.16)",
}


def shadow(scale: str) -> str:
    """Layered box-shadow string for the given depth (soft|elevated|dramatic)."""
    return _SHADOWS.get(scale, _SHADOWS["soft"])


def mesh_gradient(palette: Any) -> str:
    """A soft multi-stop 'aurora' mesh, as a `backgroundImage` value.

    Four offset radial hotspots anchored to the corners so it reads as intentional
    atmosphere, not a faint wash. Both colours stay inside the brand hue: the
    primary plus a lighter tint of it (rather than the split-complementary accent,
    which pairs a hue with its opposite and reads as a muddy clash — e.g. coral +
    mint-green). Monochromatic keeps the wash on-brand on any palette. Alphas stay
    moderate (≤0.34) so a colour wash over a light surface keeps its high luminance
    and dark body text stays WCAG-legible. Pure gradient (no url()), so
    webtree-public renders it in place rather than via the photo-layer pipeline.
    """
    p = palette.primary
    glow = _adjust_lightness(p, 0.18)  # lighter sibling of the brand hue
    return (
        f"radial-gradient(at 8% 12%, {_hairline(p, 0.22)} 0px, transparent 46%), "
        f"radial-gradient(at 92% 8%, {_hairline(glow, 0.16)} 0px, transparent 44%), "
        f"radial-gradient(at 74% 82%, {_hairline(p, 0.13)} 0px, transparent 48%), "
        f"radial-gradient(at 20% 96%, {_hairline(glow, 0.10)} 0px, transparent 46%)"
    )


def grain_data_uri(opacity: float = 0.20) -> str:
    """A tiny SVG fractal-noise grain texture as a `url(data:...)` value.

    Base64-encoded — the most portable form of an inline SVG data-URI (partial
    percent-encoding silently fails to parse in some browsers). It's a data-URI,
    so the renderers' `isPhotoSource` gate treats it as decoration, not a photo.

    Note: a few very strict Content-Security-Policies block data-URIs in CSS
    backgrounds; there the section simply falls back to its flat surface tint.
    """
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'>"
        "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' "
        "numOctaves='2' stitchTiles='stitch'/>"
        "<feColorMatrix type='saturate' values='0'/></filter>"
        f"<rect width='140' height='140' filter='url(#n)' opacity='{opacity}'/></svg>"
    )
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f'url("data:image/svg+xml;base64,{encoded}")'


def section_background_image(theme: ThemeTokens, strategy: str | None = None) -> str | None:
    """Compose the decorative section background for `strategy` (falls back to
    the theme's own strategy when omitted — a section's resolved override).

    Returns a `backgroundImage` value (mesh and/or grain) or None for flat.
    """
    strategy = strategy or getattr(theme, "background_strategy", "flat")
    layers: list[str] = []
    if "grain" in strategy:
        layers.append(grain_data_uri())
    if "mesh" in strategy:
        layers.append(mesh_gradient(theme.palette))
    return ", ".join(layers) if layers else None


def apply_section_decoration(
    styles: dict[str, Any], theme: ThemeTokens, strategy: str | None = None
) -> bool:
    """Set the decorative background (mesh/grain) on a section's style dict and
    the companion tiling props. Returns True if a decoration was applied.

    `strategy` lets a caller override the theme default per-section (the
    builder's per-section background-texture control); omit it to use the
    theme's own strategy.

    Section templates set ``backgroundRepeat: no-repeat`` + ``backgroundPosition:
    center`` for photo heroes — left as-is, the finite grain SVG would render as
    a single tile centered in the section. Force repeat/top-left so grain tiles
    across the whole band (gradient layers fill regardless)."""
    deco = section_background_image(theme, strategy)
    if not deco:
        return False
    styles["backgroundImage"] = deco
    styles["backgroundRepeat"] = "repeat"
    styles["backgroundPosition"] = "top left"
    styles["backgroundSize"] = "auto"
    return True


def glass_card_styles(theme: ThemeTokens) -> dict[str, Any]:
    """Frosted-glass card surface (backdrop-filter), scheme-aware so it reads as an
    elevated panel either way.

    Light scheme: a translucent white pane with a faint dark hairline. Dark scheme:
    a faint *light film* over the dark page (a white pane would composite to a
    washed-out grey island) with a light hairline and a deeper shadow; the catalogue
    card's dark text is then flipped to light by `enforce_text_contrast`. A no-blur
    fallback colour keeps it legible without backdrop-filter support."""
    palette = theme.palette
    blur = "blur(16px) saturate(140%)"
    if getattr(theme, "color_scheme", "light") == "dark":
        background = "rgba(255, 255, 255, 0.06)"
        border = _hairline("#ffffff", 0.14)
        box_shadow = "0 2px 4px rgba(0,0,0,0.24), 0 12px 28px rgba(0,0,0,0.36)"
    else:
        background = "rgba(255, 255, 255, 0.62)"
        border = _hairline(palette.secondary, 0.12)
        box_shadow = shadow(getattr(theme, "shadow_scale", "elevated"))
    return {
        "backgroundColor": background,
        "backdropFilter": blur,
        "WebkitBackdropFilter": blur,
        "border": f"1px solid {border}",
        "borderRadius": f"{max(12, theme.buttons.radius + 6)}px",
        "padding": "28px",
        "gap": "12px",
        "boxShadow": box_shadow,
    }
