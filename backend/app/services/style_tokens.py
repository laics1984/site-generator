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
from app.services.theme import _adjust_lightness


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

    @property
    def cards(self) -> dict[str, Any]:
        """Card surface to use: frosted glass when the mood enables it, else the
        standard opaque card. A fresh copy each access so per-card overrides
        (spreads like ``{**s.cards, "padding": "32px"}``) never mutate shared state."""
        return dict(self.glass_card or self.card)


def make_style_tokens(theme: ThemeTokens) -> StyleTokens:
    palette = theme.palette
    typo = theme.typography

    # Fluid type: ceilings scale with the mood's type-scale ratio (1.25 = the
    # previous fixed look), and every tier is a clamp() so it breathes across
    # viewports without per-breakpoint overrides. Display tiers may use a
    # distinct display_font (e.g. Fraunces/Playfair) when the mood sets one.
    boost = getattr(theme, "type_scale_ratio", 1.25) / 1.25
    display_font = getattr(theme, "display_font", None) or typo.heading_font

    heading_xl = {
        "fontFamily": display_font,
        "fontSize": _fluid_heading(56, boost),
        "fontWeight": 700,
        "lineHeight": "1.05",
        "color": palette.secondary,
        "margin": "0",
        "letterSpacing": "-0.02em",
    }
    heading_lg = {
        "fontFamily": display_font,
        "fontSize": _fluid_heading(44, boost),
        "fontWeight": 700,
        "lineHeight": "1.1",
        "color": palette.secondary,
        "margin": "0",
        "letterSpacing": "-0.015em",
    }
    heading_md = {
        "fontFamily": typo.heading_font,
        "fontSize": _fluid_heading(32, boost),
        "fontWeight": 700,
        "lineHeight": "1.15",
        "color": palette.secondary,
        "margin": "0",
        "letterSpacing": "-0.01em",
    }
    heading_mobile = {"fontSize": "28px"}
    subhead = {
        "fontFamily": typo.body_font,
        "fontSize": "19px",
        "lineHeight": "1.55",
        "color": _muted(palette.secondary),
        "margin": "0",
        "maxWidth": "640px",
    }
    eyebrow = {
        "fontFamily": typo.body_font,
        "fontSize": "13px",
        "fontWeight": 600,
        "letterSpacing": "0.14em",
        "textTransform": "uppercase",
        "color": palette.primary,
        "margin": "0",
    }
    body = {
        "fontFamily": typo.body_font,
        "fontSize": "16px",
        "lineHeight": "1.65",
        "color": _muted(palette.secondary),
        "margin": "0",
    }
    card = {
        "padding": "28px",
        "borderRadius": f"{max(8, theme.buttons.radius + 4)}px",
        "backgroundColor": palette.background,
        "border": f"1px solid {_hairline(palette.secondary)}",
        "gap": "12px",
        "boxShadow": shadow(getattr(theme, "shadow_scale", "soft")),
    }
    primary_button = {
        "color": theme.buttons.text,
        "backgroundColor": theme.buttons.background,
        "paddingTop": "13px",
        "paddingBottom": "13px",
        "paddingLeft": "26px",
        "paddingRight": "26px",
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
        "color": palette.secondary,
        "backgroundColor": "transparent",
        "paddingTop": "12px",
        "paddingBottom": "12px",
        "paddingLeft": "22px",
        "paddingRight": "22px",
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
