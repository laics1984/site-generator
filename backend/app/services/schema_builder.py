"""
Deterministic mapping from semantic ContentBlocks → BuilderElement trees,
themed against a ThemeTokens (palette / typography / radius / page tokens).

The output is a complete site:
  - body sections per page (this file)
  - shared header + footer (services/header_footer.py)
  - BuilderStyles payload (theme.to_builder_styles()) for webtree theming

All colours, radii, fonts, and section backgrounds flow from the ThemeTokens —
no hardcoded brand colours. UI/UX methodology baked in:

  - 60-30-10 colour application via section_rotation (background / surface)
  - Type scale 1.250 (major third) — headline 48px → 38.4 → 30.7 …
  - WCAG-compliant text colour picked per section background
  - 8px spatial grid (paddings/gaps in multiples of 4)
  - Touch-friendly buttons: 44px+ height, theme.buttons.radius
  - Cards: consistent radius / shadow / padding per mood
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from app.models.brand import BrandIdentity, BrandMood, ThemeTokens
from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    BuilderElementMotion,
    GeneratedPage,
    GeneratedSite,
    PageNode,
    PageSeo,
    ResponsiveStyles,
    SectionDivider,
    SectionDividerEdge,
)
from app.models.content_blocks import (
    AboutBlock,
    AwardsBlock,
    ClientsBlock,
    ContactBlock,
    ContentBlock,
    CtaBlock,
    FaqBlock,
    FeaturesBlock,
    GalleryBlock,
    HeroBlock,
    ImageMetadata,
    LinkBarBlock,
    MenuBlock,
    PagePlan,
    PricingBlock,
    ProcessBlock,
    ServicesBlock,
    SitePlan,
    StatsBlock,
    TeamBlock,
    TestimonialsBlock,
    TimelineBlock,
)
from app.services.design_brain import DesignRecipe, generate_design_recipe
from app.services.header_footer import build_footer, build_header
from app.services.media import ImageResolver
from app.services.pexels import PhotoResult
from app.config import settings
from app.services.section_content import (
    _PAGE_BG,
    _SURFACE_BG,
    Band,
    SectionVisualInput,
    apply_luminance_rhythm,
    apply_section_rhythm,
    assign_visual_policies,
    block_to_section,
    enforce_text_contrast,
    hero_scroll_anchor,
    section_visual_input_for,
)
from app.services.template_filler import fill_template
from app.services.theme import _adjust_lightness, build_theme, resolve_color_scheme


# --- render context -------------------------------------------------------------


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


@dataclass
class ChildPageRef:
    """Lightweight handle to a sub-page so listing blocks can cross-link to it."""

    slug: str
    title: str
    page_type: str


@dataclass
class RenderContext:
    theme: ThemeTokens
    resolver: ImageResolver
    styles: StyleTokens
    section_index: int = 0  # incremented as we lay sections down — drives rotation
    current_page_slug: str | None = None  # slug of the page being rendered
    current_parent_slug: str | None = None  # set when rendering a sub-page (for breadcrumbs)
    children_by_parent: dict[str, list[ChildPageRef]] = field(default_factory=dict)
    page_title_by_slug: dict[str, str] = field(default_factory=dict)
    # Images the source placed on the page currently being rendered. The resolver
    # ranks these ahead of the site-wide pool so each page's hero/sections use
    # their OWN photos, not the biggest image found anywhere. Empty for sources
    # without per-page placement (then resolution stays pool-wide as before).
    page_images: list[ImageMetadata] = field(default_factory=list)
    # Transient: the band of the FIRST image resolved for the current section,
    # captured by block_to_element's resolve_image closure and read by
    # plan_to_site to feed the luminance pass. Reset per block. (Phase 4b)
    section_image_band: Band | None = None


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


# --- 2025/26 modern style vocabulary --------------------------------------------
#
# All helpers return plain CSS *value* strings (or flat style dicts) so they flow
# straight through the BuilderElement `styles` channel into both the builder
# editor and the webtree-public renderer — no schema or renderer change needed.
# (Mesh gradients / grain are safe on sections+containers thanks to the
# `isPhotoSource` gate in webtree-public/lib/backgroundPhoto.ts.)


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
        f"radial-gradient(at 8% 12%, {_hairline(p, 0.34)} 0px, transparent 46%), "
        f"radial-gradient(at 92% 8%, {_hairline(glow, 0.26)} 0px, transparent 44%), "
        f"radial-gradient(at 74% 82%, {_hairline(p, 0.20)} 0px, transparent 48%), "
        f"radial-gradient(at 20% 96%, {_hairline(glow, 0.15)} 0px, transparent 46%)"
    )


def grain_data_uri(opacity: float = 0.55) -> str:
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


# --- generation-time modernization pass -----------------------------------------
#
# Real pages are built by the catalogue path (fill_template), whose templates use
# literal px sizes and almost no shadows/glass/mesh. Rather than rewrite the 223KB
# catalogue or change the BuilderStyles contract, we apply the 2025/26 treatments
# deterministically over the assembled BuilderElement tree, keyed off the catalogue's
# consistent element names ("Heading" / "*Card" / surface sections). Pure + in-place;
# covers catalogue AND legacy sections uniformly. Per-mood, driven by ThemeTokens.


def _parse_px(value: Any) -> float | None:
    """Numeric px from a CSS size value, or None if not a plain px length."""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        v = value.strip()
        if v.endswith("px"):
            try:
                return float(v[:-2])
            except ValueError:
                return None
    return None


def _walk_modernize(
    node: BuilderElement,
    *,
    boost: float,
    use_glass: bool,
    shadow_scale: str,
    theme: ThemeTokens,
    headings: list[tuple[BuilderElement, float]],
) -> None:
    styles = node.styles
    name = (node.name or "").strip()

    if node.type == "text":
        fs = styles.get("fontSize")
        already_fluid = isinstance(fs, str) and ("clamp(" in fs or "var(" in fs)
        if not already_fluid:
            px = _parse_px(fs)
            # Headings (named or visually large) become fluid; eyebrows / body left.
            if px is not None and (name == "Heading" or px >= 24):
                ceiling = px * boost
                styles["fontSize"] = _fluid(ceiling * 0.62, ceiling)
                headings.append((node, px))
                if px >= 34:  # large display type: tighten if the template didn't
                    styles.setdefault("lineHeight", "1.08")
                    styles.setdefault("letterSpacing", "-0.02em")

    elif (
        node.type in ("container", "2Col", "3Col")
        and name.lower().endswith("card")
        # Only enhance a container that is ALREADY a styled card. Catalogue cards
        # nest a bare wrapper "*Card" around the real styled "*Card" (and the
        # contact form's "*Card" wraps a self-styled form), so framing a bare
        # wrapper would draw a second border around the real card. Requiring
        # existing card styling targets the real card and skips wrappers.
        and any(k in styles for k in ("borderRadius", "border", "backgroundColor"))
    ):
        styles["boxShadow"] = shadow(shadow_scale)
        styles.setdefault("borderRadius", f"{max(8, theme.buttons.radius + 4)}px")
        if use_glass:
            glass = glass_card_styles(theme)
            # Layer the frosted surface; keep the template's own padding/gap/radius.
            for key in ("backgroundColor", "backdropFilter", "WebkitBackdropFilter", "border"):
                styles[key] = glass[key]

    content = node.content
    if isinstance(content, list):
        for child in content:
            _walk_modernize(
                child,
                boost=boost,
                use_glass=use_glass,
                shadow_scale=shadow_scale,
                theme=theme,
                headings=headings,
            )


def modernize_sections(sections: list[BuilderElement], theme: ThemeTokens) -> None:
    """Apply per-mood 2025/26 treatments to an assembled page's section list,
    in place. Idempotent: skips already-fluid type and sections that already
    carry a background image."""
    boost = getattr(theme, "type_scale_ratio", 1.25) / 1.25
    display_font = getattr(theme, "display_font", None)
    use_glass = getattr(theme, "use_glass", False)
    shadow_scale = getattr(theme, "shadow_scale", "soft")
    strategy = getattr(theme, "background_strategy", "flat")
    surface_hex = theme.palette.surface
    page_hex = theme.page.background

    for idx, section in enumerate(sections):
        headings: list[tuple[BuilderElement, float]] = []
        _walk_modernize(
            section,
            boost=boost,
            use_glass=use_glass,
            shadow_scale=shadow_scale,
            theme=theme,
            headings=headings,
        )

        # The mood's display face goes on the section's largest heading (its H1).
        if display_font and headings:
            lead = max(headings, key=lambda pair: pair[1])[0]
            lead.styles["fontFamily"] = display_font

        # Atmospheric mesh/grain on plain (non-photo, non-brand-fill) sections —
        # both the surface-tinted band AND the flat page-background band, so
        # decoration isn't confined to one in every three sections (the
        # rotation's surface slot). Dark/primary CTA bands carry their own
        # strong fill and are excluded by the backgroundColor check below.
        # Tags the resolved strategy on `backgroundTexture` regardless of
        # outcome (incl. "flat") so the builder's per-section override control
        # has an explicit value to show, and so a divider revealing this
        # section can copy it onto the divider's fill instead of falling back
        # to a flat cut — see apply_section_dividers, which runs after this
        # pass specifically so it can read this tag.
        st = section.styles
        has_fill = bool(st.get("backgroundImage") or st.get("background"))
        is_plain = st.get("backgroundColor") in (
            surface_hex, _SURFACE_BG, page_hex, _PAGE_BG,
        )
        if is_plain and not has_fill:
            effective = getattr(section, "backgroundTexture", None) or strategy
            section.backgroundTexture = effective
            if effective != "flat":
                apply_section_decoration(st, theme, effective)


# --- motion -----------------------------------------------------------------

# Industries where a visual/brand-led hero (full-bleed photo or, lacking one,
# an atmospheric WebGL backdrop) reads on-brand. Narrower than the planner's
# hero-layout guidance (which also names hospitality/travel/fitness) because
# IndustryCategoryLiteral only has these three buckets for that end of the
# spectrum — saas/professional-services/consultancy stay static by default.
_VISUAL_LED_INDUSTRIES = frozenset({"restaurant", "agency", "ecommerce"})

# Grid container types the public renderer staggers (motionRuntime.ts applies
# a container's own preset to its direct children when `stagger` is set).
_STAGGER_GRID_TYPES = frozenset({"3Col", "2Col"})


def apply_motion(
    sections: list[BuilderElement],
    *,
    industry: str | None,
    hero_has_photo: bool,
) -> None:
    """Tag the assembled page's sections with declarative entrance/backdrop
    motion (BuilderElementMotion). Most catalog templates already author their
    own motion (rise + stagger on grids, occasional aurora/silk hero/CTA
    backdrops) directly in section_catalog.json — this pass only fills gaps:
    a runtime-only hero backdrop decision the static catalog can't make
    (depends on whether THIS site's hero actually resolved a photo and what
    industry it's in), plus a calm fallback reveal for any section/group a
    template left unset (notably the legacy hand-rolled builders below, which
    predate the catalog and carry no motion of their own). Per-element
    `intensity` is left unset so it inherits the site-wide default the theme
    already derives from mood (ThemeTokens.motion_intensity /
    MOOD_MOTION_INTENSITY, see brand.py). Idempotent throughout: never
    overwrites a `.motion` a template already set. Mutates in place."""
    for section in sections:
        if section.motion is None and section.name.startswith("Hero"):
            _apply_hero_motion(section, industry=industry, hero_has_photo=hero_has_photo)
        _apply_content_group_motion(section)


def _apply_hero_motion(
    section: BuilderElement, *, industry: str | None, hero_has_photo: bool
) -> None:
    """Backdrop motion on the hero's own section element (gsap/webgl tiers
    bypass the entrance-reveal viewport gate, so they're the only presets
    worth setting on above-the-fold content)."""
    if hero_has_photo:
        section.motion = BuilderElementMotion(preset="parallax-drift")
    elif industry in _VISUAL_LED_INDUSTRIES:
        section.motion = BuilderElementMotion(preset="aurora")


def _apply_content_group_motion(section: BuilderElement) -> None:
    """Give each of a section's top-level layout groups (its header, each card
    row, each half of a two-column split) a calm `rise` entrance; grid rows
    additionally stagger so cards cascade rather than popping in as one block.
    Only touches groups that don't already carry motion — for catalog-built
    sections that's usually none (the template already set it), so this is
    mainly what gives the legacy hand-rolled builders (pricing/team/gallery/
    menu/process today) any motion at all."""
    groups = section.content if isinstance(section.content, list) else []
    for group in groups:
        if not isinstance(group, BuilderElement) or group.motion is not None:
            continue
        if group.type in _STAGGER_GRID_TYPES:
            group.motion = BuilderElementMotion(preset="rise", stagger=0.08)
        else:
            group.motion = BuilderElementMotion(preset="rise")


# --- heading alignment --------------------------------------------------------

# Moods that read more confidently with left-aligned, editorial-style section
# headers instead of the centered "brochure" default. No-op for other moods.
_LEFT_ALIGN_MOODS = frozenset({"modern", "technical", "editorial"})

# Top-level group names catalog templates use for a section's intro/heading
# block (inconsistent across templates — "Section Intro" vs "Section Header"
# — so we match both). Single-group sections (hero/about/cta/testimonials/
# contact, whose only top-level child is e.g. "Hero Columns" or "Quote Block")
# have no group with these names and are naturally left untouched: their
# heading lives one level deeper than this pass looks, by design — hero has
# its own template-driven layout variety, About is already asymmetric via its
# two-column split, and CTA reads fine centered.
_HEADER_GROUP_NAMES = frozenset({"Section Intro", "Section Header", "Section header"})


def apply_heading_alignment(sections: list[BuilderElement], mood: BrandMood | None) -> None:
    """Left-align a section's header/intro group for moods that read better
    asymmetric. No-op for other moods — today's centered look stays
    byte-identical. Mutates in place."""
    if (mood or "modern") not in _LEFT_ALIGN_MOODS:
        return
    for section in sections:
        groups = section.content if isinstance(section.content, list) else []
        for group in groups:
            if isinstance(group, BuilderElement) and group.name in _HEADER_GROUP_NAMES:
                _left_align_header_group(group)


def _left_align_header_group(group: BuilderElement) -> None:
    """Flip a header/intro group from centered to left-aligned: the group's
    own `alignItems`, plus each direct text child's `textAlign`, stripping the
    auto-margins some subheads use to stay centered at their capped width."""
    styles = dict(group.styles or {})
    if styles.get("alignItems") == "center":
        styles["alignItems"] = "flex-start"
    group.styles = styles
    children = group.content
    if not isinstance(children, list):
        return
    for child in children:
        if not isinstance(child, BuilderElement) or child.type != "text":
            continue
        cstyles = dict(child.styles or {})
        if cstyles.get("textAlign") == "center":
            cstyles["textAlign"] = "left"
        if cstyles.get("marginLeft") == "auto":
            cstyles.pop("marginLeft", None)
        if cstyles.get("marginRight") == "auto":
            cstyles.pop("marginRight", None)
        child.styles = cstyles


# --- section dividers ---------------------------------------------------------

# Shape per mood (None → no divider; today's plain edges stay byte-identical).
# Luxury/technical skip it entirely — a shaped edge reads as decoration their
# minimal/flat design language deliberately avoids.
_DIVIDER_SHAPE_BY_MOOD: dict[str, str | None] = {
    "modern": "curve",
    "luxury": None,
    "friendly": "wave",
    "technical": None,
    "editorial": "slant",
    "playful": "peak",
}

_DEFAULT_DIVIDER_COLOR = "var(--builder-page-background, #ffffff)"


def apply_section_dividers(sections: list[BuilderElement], mood: BrandMood | None) -> None:
    """Add a shaped edge at the hero→content and content→CTA handoffs — the two
    highest-impact, lowest-noise places for one (see SECTION_DIVIDER system,
    section-divider.ts). Deliberately NOT applied at every section boundary:
    one per page reads as a designed accent, one at every boundary reads as
    wallpaper. No-op for moods with no shape (luxury/technical) or single-
    section pages. Never overwrites a divider a section already carries.

    Must run after modernize_sections (see build_site) — it reads each
    section's resolved `backgroundTexture` tag to carry a matching grain/mesh
    layer onto the divider's fill, instead of the flat cut a plain `color`
    alone would produce against a textured section. Mutates in place."""
    shape = _DIVIDER_SHAPE_BY_MOOD.get(mood or "modern")
    if shape is None or len(sections) < 2:
        return

    hero_boundary: int | None = None
    if sections[0].name.startswith("Hero") and sections[0].divider is None:
        next_bg = _section_edge_color(sections[1])
        sections[0].divider = SectionDivider(
            bottom=SectionDividerEdge(
                shape=shape, color=next_bg, texture=_section_edge_texture(sections[1])
            )
        )
        hero_boundary = 0

    for i in range(len(sections) - 1, 0, -1):
        if not sections[i].name.startswith("CTA"):
            continue
        if sections[i].divider is not None:
            break
        if hero_boundary is not None and i - 1 == hero_boundary:
            # Same boundary the hero's bottom edge already claimed (a hero
            # immediately followed by a CTA, nothing in between) — don't
            # double up on one seam.
            break
        prev_bg = _section_edge_color(sections[i - 1])
        sections[i].divider = SectionDivider(
            top=SectionDividerEdge(
                shape=shape, color=prev_bg, texture=_section_edge_texture(sections[i - 1])
            )
        )
        break


def _section_edge_color(section: BuilderElement) -> str:
    """The color a divider should be filled with to read as "revealing" this
    section: its own flat backgroundColor when it has one, else the page
    background token (covers photo/gradient-background sections, which the
    divider still renders correctly over per the SECTION_DIVIDER contract)."""
    styles = section.styles or {}
    bg = styles.get("backgroundColor")
    return bg if isinstance(bg, str) and bg else _DEFAULT_DIVIDER_COLOR


def _section_edge_texture(section: BuilderElement) -> str | None:
    """The texture (if any) the revealed section settled on via its
    `backgroundTexture` tag, so the divider's fill can layer a matching
    grain/mesh on top of `color` instead of a flat cut. None for sections
    that stayed flat or never got a tag (e.g. photo backgrounds)."""
    texture = getattr(section, "backgroundTexture", None)
    return texture if texture and texture != "flat" else None


# --- low-level factories --------------------------------------------------------


def _uid() -> str:
    return str(uuid4())


def _text(
    inner: str,
    *,
    name: str = "Text",
    styles: dict[str, Any] | None = None,
    mobile: dict[str, Any] | None = None,
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name=name,
        type="text",
        styles={"width": "100%", **(styles or {})},
        content=BuilderElementContent(innerText=inner),
        responsiveStyles=ResponsiveStyles(mobile=mobile) if mobile else None,
    )


def _link(
    label: str,
    href: str,
    *,
    ctx: RenderContext,
    primary: bool = True,
    extra: dict[str, Any] | None = None,
) -> BuilderElement:
    base = (
        ctx.styles.primary_button_styles if primary else ctx.styles.secondary_button_styles
    )
    return BuilderElement(
        id=_uid(),
        name="Link",
        type="link",
        styles={**base, **(extra or {})},
        content=BuilderElementContent(innerText=label, href=href),
    )


def _container(
    children: list[BuilderElement],
    *,
    name: str = "Container",
    styles: dict[str, Any] | None = None,
    mobile: dict[str, Any] | None = None,
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name=name,
        type="container",
        styles={
            "display": "flex",
            "flexDirection": "column",
            "width": "100%",
            **(styles or {}),
        },
        content=children,
        responsiveStyles=ResponsiveStyles(mobile=mobile) if mobile else None,
    )


def _two_col(
    left: list[BuilderElement],
    right: list[BuilderElement],
    *,
    styles: dict[str, Any] | None = None,
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name="Two Columns",
        type="2Col",
        # The builder owns the grid for column-layout types (2Col/3Col): it
        # applies `grid-template-columns: repeat(n, minmax(0,1fr))` internally
        # and stacks to 1 column on mobile. Emitting `display`/`gridTemplateColumns`
        # here leaks them onto the element's outer wrapper, producing a *nested*
        # grid that collapses every column to half width. Carry only gap/width.
        styles={
            "gap": "48px",
            "width": "100%",
            **(styles or {}),
        },
        content=[
            _container(left, name="Column", styles={"gap": "16px"}),
            _container(right, name="Column", styles={"gap": "16px"}),
        ],
        responsiveStyles=ResponsiveStyles(mobile={"gap": "24px"}),
    )


def _three_col(
    columns: list[list[BuilderElement]], *, styles: dict[str, Any] | None = None
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name="Three Columns",
        type="3Col",
        # Grid is owned by the builder for column-layout types — see _two_col.
        styles={
            "gap": "32px",
            "width": "100%",
            **(styles or {}),
        },
        content=[
            _container(col, name="Column", styles={"gap": "12px"}) for col in columns
        ],
    )


def _humanize_slug(slug_part: str) -> str:
    """`"web-design"` → `"Web Design"`. Used for breadcrumb labels."""
    return " ".join(
        w.capitalize() for w in slug_part.replace("/", " ").replace("-", " ").split() if w
    )


def _build_breadcrumb(page_plan: PagePlan, ctx: RenderContext) -> BuilderElement:
    """Build a "Home › Parent › Current" trail at the top of sub-pages.

    Walks the slug segments to construct each intermediate link. Uses
    ``ctx.page_title_by_slug`` for known pages, falls back to humanizing the
    segment text otherwise. Themed muted text with a subtle separator.
    """
    segments = page_plan.slug.split("/") if page_plan.slug else []
    crumbs: list[BuilderElement] = []

    sep_style = {
        "color": _muted(ctx.theme.palette.secondary),
        "padding": "0 8px",
    }
    link_style = {
        "color": _muted(ctx.theme.palette.secondary),
        "textDecoration": "none",
        "fontSize": "13px",
        "fontFamily": ctx.theme.typography.body_font,
    }
    current_style = {
        "color": ctx.theme.palette.secondary,
        "fontSize": "13px",
        "fontWeight": 600,
        "fontFamily": ctx.theme.typography.body_font,
    }

    # Home root
    crumbs.append(
        BuilderElement(
            id=_uid(),
            name="Crumb",
            type="link",
            styles=link_style,
            content=BuilderElementContent(innerText="Home", href="/"),
        )
    )

    accumulated: list[str] = []
    for i, segment in enumerate(segments):
        accumulated.append(segment)
        accumulated_slug = "/".join(accumulated)
        # Separator
        crumbs.append(
            BuilderElement(
                id=_uid(),
                name="Crumb separator",
                type="text",
                styles=sep_style,
                content=BuilderElementContent(innerText="›"),
            )
        )
        label = ctx.page_title_by_slug.get(accumulated_slug) or _humanize_slug(segment)
        is_current = i == len(segments) - 1
        if is_current:
            crumbs.append(
                BuilderElement(
                    id=_uid(),
                    name="Crumb current",
                    type="text",
                    styles=current_style,
                    content=BuilderElementContent(innerText=label),
                )
            )
        else:
            crumbs.append(
                BuilderElement(
                    id=_uid(),
                    name="Crumb",
                    type="link",
                    styles=link_style,
                    content=BuilderElementContent(
                        innerText=label, href=f"/{accumulated_slug}"
                    ),
                )
            )

    return BuilderElement(
        id=_uid(),
        name="Breadcrumb",
        type="container",
        styles={
            "display": "flex",
            "flexWrap": "wrap",
            "alignItems": "center",
            "padding": "16px 32px 0",
            "maxWidth": "1200px",
            "margin": "0 auto",
            "width": "100%",
            "backgroundColor": ctx.theme.palette.background,
        },
        content=crumbs,
        responsiveStyles=ResponsiveStyles(mobile={"padding": "12px 16px 0"}),
    )


def _section(
    ctx: RenderContext,
    children: list[BuilderElement],
    *,
    name: str,
    surface_override: str | None = None,
    background_image: str | None = None,
    overlay: str | None = None,
    extra_styles: dict[str, Any] | None = None,
    inverted: bool = False,
) -> BuilderElement:
    """
    Builds a section. Background defaults to the current rotation slot for the
    site (background / surface), giving rhythm without the user thinking about it.
    Set surface_override to force a specific background color (e.g. inverted CTA).
    """
    palette = ctx.theme.palette
    rotation = ctx.theme.section_rotation
    slot = rotation[ctx.section_index % len(rotation)] if rotation else "background"
    default_bg = {
        "background": palette.background,
        "surface": palette.surface,
        "primary": palette.primary,
    }.get(slot, palette.background)
    bg = surface_override or default_bg

    styles: dict[str, Any] = {
        "display": "flex",
        "flexDirection": "column",
        "width": "100%",
        "paddingTop": "96px",
        "paddingBottom": "96px",
        "paddingLeft": "24px",
        "paddingRight": "24px",
        "alignItems": "center",
        "position": "relative",
        "backgroundColor": bg,
        "fontFamily": ctx.theme.typography.body_font,
        **(extra_styles or {}),
    }
    if background_image:
        layers = []
        if overlay:
            layers.append(f"linear-gradient({overlay}, {overlay})")
        layers.append(f"url('{background_image}')")
        styles["backgroundImage"] = ", ".join(layers)
        styles["backgroundSize"] = "cover"
        styles["backgroundPosition"] = "center"
        styles["backgroundRepeat"] = "no-repeat"
    elif not inverted and slot == "surface":
        # Decorative aurora/grain on the tinted (~30%) sections only — adds depth
        # and rhythm without touching clean white sections, photo sections, or
        # inverted CTAs. These are gradients / data-URIs, so the renderer's
        # isPhotoSource gate keeps them out of the photo-layer pipeline.
        apply_section_decoration(styles, ctx.theme)

    inner = _container(
        children,
        name="Section content",
        styles={
            "maxWidth": f"{ctx.theme.page.max_width}px",
            "width": "100%",
            "gap": "32px",
            "alignItems": "stretch",
            "position": "relative",
            "zIndex": 1,
        },
    )

    ctx.section_index += 1
    return BuilderElement(
        id=_uid(),
        name=name,
        # The builder has no renderer for the legacy "section" type — it falls
        # through to the default empty branch and renders nothing. Sections are
        # plain full-width containers as far as the builder is concerned.
        type="container",
        styles=styles,
        content=[inner],
        responsiveStyles=ResponsiveStyles(
            mobile={"paddingTop": "56px", "paddingBottom": "56px"}
        ),
    )


def _image_from_photo(
    photo: PhotoResult,
    *,
    name: str = "Image",
    aspect_ratio: str = "4 / 3",
    border_radius: str = "24px",
    extra_styles: dict[str, Any] | None = None,
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name=name,
        type="image",
        styles={
            "width": "100%",
            "height": "auto",
            "borderRadius": border_radius,
            "objectFit": "cover",
            "aspectRatio": aspect_ratio,
            **(extra_styles or {}),
        },
        content=BuilderElementContent(src=photo.url, alt=photo.alt),
    )


# --- section builders -----------------------------------------------------------


async def _build_hero(block: HeroBlock, ctx: RenderContext) -> BuilderElement:
    photo = await ctx.resolver.resolve(
        block.image_query,
        intent="hero",
        alt_fallback=block.image_alt or block.headline,
    )
    if block.layout == "background":
        return _build_hero_background(block, photo, ctx)
    return _build_hero_split(block, photo, ctx)


def _build_hero_split(
    block: HeroBlock, photo: PhotoResult, ctx: RenderContext
) -> BuilderElement:
    s = ctx.styles
    left: list[BuilderElement] = []
    if block.eyebrow:
        left.append(_text(block.eyebrow, name="Eyebrow", styles=s.eyebrow))
    left.append(
        _text(
            block.headline,
            name="Headline",
            styles=s.heading_xl,
            mobile=s.heading_mobile,
        )
    )
    if block.subheadline:
        left.append(_text(block.subheadline, name="Subheadline", styles=s.subhead))

    cta_row_children: list[BuilderElement] = [
        _link(block.primary_cta_label, block.primary_cta_href, ctx=ctx, primary=True)
    ]
    if block.secondary_cta_label:
        cta_row_children.append(
            _link(
                block.secondary_cta_label,
                block.secondary_cta_href or "#",
                ctx=ctx,
                primary=False,
            )
        )
    left.append(
        _container(
            cta_row_children,
            name="CTA Row",
            styles={"flexDirection": "row", "gap": "12px", "flexWrap": "wrap"},
        )
    )

    right = [
        _image_from_photo(
            photo,
            name="Hero Image",
            aspect_ratio="4 / 3",
            border_radius=f"{ctx.theme.buttons.radius + 12}px",
        )
    ]

    return _section(ctx, [_two_col(left, right)], name="Hero")


def _build_hero_background(
    block: HeroBlock, photo: PhotoResult, ctx: RenderContext
) -> BuilderElement:
    s = ctx.styles
    inner: list[BuilderElement] = []
    if block.eyebrow:
        inner.append(
            _text(
                block.eyebrow,
                name="Eyebrow",
                styles={
                    **s.eyebrow,
                    "color": "rgba(255,255,255,0.92)",
                    "textAlign": "center",
                },
            )
        )
    inner.append(
        _text(
            block.headline,
            name="Headline",
            styles={
                **s.heading_xl,
                "color": "#ffffff",
                "textAlign": "center",
                "textShadow": "0 2px 16px rgba(0,0,0,0.35)",
            },
            mobile={"fontSize": "36px"},
        )
    )
    if block.subheadline:
        inner.append(
            _text(
                block.subheadline,
                name="Subheadline",
                styles={
                    **s.subhead,
                    "color": "rgba(255,255,255,0.92)",
                    "textAlign": "center",
                    "marginLeft": "auto",
                    "marginRight": "auto",
                },
            )
        )
    cta_children = [
        _link(block.primary_cta_label, block.primary_cta_href, ctx=ctx, primary=True)
    ]
    if block.secondary_cta_label:
        cta_children.append(
            _link(
                block.secondary_cta_label,
                block.secondary_cta_href or "#",
                ctx=ctx,
                primary=False,
                extra={
                    "color": "#ffffff",
                    "backgroundColor": "rgba(255,255,255,0.10)",
                    "border": "1px solid rgba(255,255,255,0.45)",
                },
            )
        )
    inner.append(
        _container(
            cta_children,
            name="CTA Row",
            styles={
                "flexDirection": "row",
                "gap": "12px",
                "flexWrap": "wrap",
                "justifyContent": "center",
            },
        )
    )

    return _section(
        ctx,
        inner,
        name="Hero",
        background_image=photo.url,
        overlay="rgba(15, 23, 42, 0.55)",
        extra_styles={
            "paddingTop": "140px",
            "paddingBottom": "140px",
            # Site-wide var so the builder's "Hero height" toggle (Styles tab)
            # can resize this hero; 560px is the fallback when unset. Keep in
            # lockstep with the builder catalog's "Hero - Background" section
            # and toBuilderCssVars/webtree-public lib/styles.ts.
            "minHeight": "var(--builder-hero-min-height, 560px)",
            "justifyContent": "center",
        },
    )


async def _build_features(
    block: FeaturesBlock, ctx: RenderContext
) -> BuilderElement:
    s = ctx.styles
    head_children: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles={**s.heading_md, "textAlign": "center"},
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head_children.append(
            _text(
                block.subheading,
                name="Subheading",
                styles={**s.subhead, "textAlign": "center", "maxWidth": "720px"},
            )
        )
    header = _container(
        head_children,
        name="Section header",
        styles={"alignItems": "center", "gap": "12px"},
    )

    cards = [
        [
            _text(
                item.title,
                name="Feature title",
                styles={
                    "fontFamily": ctx.theme.typography.heading_font,
                    "fontSize": "20px",
                    "fontWeight": 600,
                    "color": ctx.theme.palette.secondary,
                    "margin": "0",
                },
            ),
            _text(item.description, name="Feature body", styles=s.body),
        ]
        for item in block.items
    ]

    rows: list[BuilderElement] = []
    triplet: list[list[BuilderElement]] = []
    for card in cards:
        triplet.append(card)
        if len(triplet) == 3:
            rows.append(_three_col(triplet, styles={"gap": "24px"}))
            triplet = []
    if triplet:
        if len(triplet) == 2:
            rows.append(_two_col(triplet[0], triplet[1], styles={"gap": "24px"}))
        else:
            rows.append(_container(triplet[0], name="Feature card", styles=s.cards))

    _apply_card_styles(rows, s.cards)
    return _section(ctx, [header, *rows], name="Features")


def _match_child_by_title(
    item_title: str, children: list[ChildPageRef], min_score: float = 0.55
) -> ChildPageRef | None:
    """Find the child page whose title is the closest match for an item title.

    Used by listing blocks (services, gallery) on parent pages so each item
    can deep-link to its detail sub-page instead of bouncing to #contact.
    Returns None if nothing crosses the similarity threshold.
    """
    from difflib import SequenceMatcher

    item_low = item_title.lower().strip()
    if not item_low or not children:
        return None
    best: tuple[float, ChildPageRef | None] = (0.0, None)
    for child in children:
        child_low = child.title.lower().strip()
        if not child_low:
            continue
        # Strong-substring match dominates ratio comparison.
        if item_low in child_low or child_low in item_low:
            score = min(len(item_low), len(child_low)) / max(
                len(item_low), len(child_low)
            )
        else:
            score = SequenceMatcher(None, item_low, child_low).ratio()
        if score > best[0]:
            best = (score, child)
    return best[1] if best[0] >= min_score else None


async def _build_services(
    block: ServicesBlock, ctx: RenderContext
) -> BuilderElement:
    s = ctx.styles
    head_children: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles=s.heading_md,
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head_children.append(_text(block.subheading, name="Subheading", styles=s.subhead))

    # If the current page has child sub-pages, try to link each service item
    # to its matching detail page.
    children = ctx.children_by_parent.get(ctx.current_page_slug or "", [])

    cards: list[list[BuilderElement]] = []
    for item in block.items:
        card: list[BuilderElement] = [
            _text(
                item.title,
                name="Service title",
                styles={
                    "fontFamily": ctx.theme.typography.heading_font,
                    "fontSize": "22px",
                    "fontWeight": 600,
                    "color": ctx.theme.palette.secondary,
                    "margin": "0",
                },
            ),
            _text(item.description, name="Service body", styles=s.body),
        ]
        matched = _match_child_by_title(item.title, children)
        if matched is not None:
            # Cross-link to the sub-page. Override whatever cta_href the LLM gave.
            card.append(
                _link(
                    item.cta_label or "Learn more",
                    f"/{matched.slug}",
                    ctx=ctx,
                    primary=False,
                    extra={"alignSelf": "flex-start"},
                )
            )
        elif item.cta_label:
            card.append(
                _link(
                    item.cta_label,
                    item.cta_href or "#contact",
                    ctx=ctx,
                    primary=False,
                    extra={"alignSelf": "flex-start"},
                )
            )
        cards.append(card)

    rows: list[BuilderElement] = []
    pair: list[list[BuilderElement]] = []
    for card in cards:
        pair.append(card)
        if len(pair) == 2:
            rows.append(_two_col(pair[0], pair[1], styles={"gap": "24px"}))
            pair = []
    if pair:
        rows.append(_container(pair[0], name="Service card", styles=s.cards))

    _apply_card_styles(rows, s.cards)
    return _section(
        ctx,
        [_container(head_children, name="Section header", styles={"gap": "12px"}), *rows],
        name="Services",
    )


async def _build_testimonials(
    block: TestimonialsBlock, ctx: RenderContext
) -> BuilderElement:
    s = ctx.styles
    header = _text(
        block.heading,
        name="Heading",
        styles={**s.heading_md, "textAlign": "center"},
        mobile=s.heading_mobile,
    )

    cards: list[list[BuilderElement]] = []
    for item in block.items:
        card: list[BuilderElement] = [
            _text(
                f"“{item.quote}”",
                name="Quote",
                styles={
                    "fontFamily": ctx.theme.typography.body_font,
                    "fontSize": "18px",
                    "lineHeight": "1.6",
                    "color": ctx.theme.palette.secondary,
                    "margin": "0",
                },
            )
        ]
        row_children: list[BuilderElement] = []
        if item.avatar_query:
            avatar = await ctx.resolver.resolve(
                item.avatar_query, intent="avatar", alt_fallback=item.author
            )
            row_children.append(
                _image_from_photo(
                    avatar,
                    name="Avatar",
                    aspect_ratio="1 / 1",
                    border_radius="9999px",
                    extra_styles={
                        "width": "48px",
                        "height": "48px",
                        "flexShrink": 0,
                    },
                )
            )
        row_children.append(
            _text(
                item.author + (f", {item.role}" if item.role else ""),
                name="Attribution",
                styles={
                    "fontFamily": ctx.theme.typography.body_font,
                    "fontSize": "14px",
                    "color": _muted(ctx.theme.palette.secondary),
                    "margin": "0",
                    "fontWeight": 600,
                },
            )
        )
        card.append(
            _container(
                row_children,
                name="Author row",
                styles={
                    "flexDirection": "row",
                    "gap": "12px",
                    "alignItems": "center",
                },
            )
        )
        cards.append(card)

    rows: list[BuilderElement] = []
    triplet: list[list[BuilderElement]] = []
    for card in cards:
        triplet.append(card)
        if len(triplet) == 3:
            rows.append(_three_col(triplet, styles={"gap": "24px"}))
            triplet = []
    if triplet:
        if len(triplet) == 2:
            rows.append(_two_col(triplet[0], triplet[1], styles={"gap": "24px"}))
        else:
            rows.append(_container(triplet[0], name="Testimonial card", styles=s.cards))

    _apply_card_styles(rows, s.cards)
    return _section(
        ctx,
        [header, *rows],
        name="Testimonials",
        surface_override=ctx.theme.palette.surface,
    )


async def _build_about(block: AboutBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    photo = await ctx.resolver.resolve(
        block.image_query,
        intent="about",
        alt_fallback=block.image_alt or block.heading,
    )
    left = [
        _text(block.heading, name="Heading", styles=s.heading_lg, mobile=s.heading_mobile),
        _text(block.body, name="Body", styles={**s.body, "fontSize": "17px"}),
    ]
    right = [
        _image_from_photo(
            photo,
            name="About Image",
            aspect_ratio="1 / 1",
            border_radius=f"{ctx.theme.buttons.radius + 12}px",
        )
    ]
    return _section(ctx, [_two_col(left, right)], name="About")


async def _build_faq(block: FaqBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    items = [
        _container(
            [
                _text(
                    item.question,
                    name="Question",
                    styles={
                        "fontFamily": ctx.theme.typography.heading_font,
                        "fontSize": "18px",
                        "fontWeight": 600,
                        "color": ctx.theme.palette.secondary,
                        "margin": "0",
                    },
                ),
                _text(item.answer, name="Answer", styles=s.body),
            ],
            name="FAQ item",
            styles={**s.cards, "gap": "8px"},
        )
        for item in block.items
    ]
    header = _text(
        block.heading,
        name="Heading",
        styles={**s.heading_md, "textAlign": "center"},
        mobile=s.heading_mobile,
    )
    return _section(
        ctx,
        [
            header,
            _container(
                items,
                name="FAQ list",
                styles={"gap": "16px", "maxWidth": "820px", "width": "100%"},
            ),
        ],
        name="FAQ",
        extra_styles={"alignItems": "center"},
    )


async def _build_cta(block: CtaBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    photo = await ctx.resolver.resolve(
        block.background_query, intent="cta_bg", alt_fallback=block.headline
    )

    children: list[BuilderElement] = [
        _text(
            block.headline,
            name="Headline",
            styles={
                **s.heading_lg,
                "color": "#ffffff",
                "textAlign": "center",
                "textShadow": "0 2px 16px rgba(0,0,0,0.35)",
            },
            mobile=s.heading_mobile,
        )
    ]
    if block.subheadline:
        children.append(
            _text(
                block.subheadline,
                name="Subheadline",
                styles={
                    **s.subhead,
                    "color": "rgba(255,255,255,0.92)",
                    "textAlign": "center",
                    "marginLeft": "auto",
                    "marginRight": "auto",
                },
            )
        )
    children.append(
        _container(
            [
                _link(
                    block.cta_label,
                    block.cta_href,
                    ctx=ctx,
                    primary=True,
                    extra={
                        "backgroundColor": "#ffffff",
                        "color": ctx.theme.palette.primary,
                    },
                )
            ],
            name="CTA Row",
            styles={"flexDirection": "row", "justifyContent": "center"},
        )
    )
    return _section(
        ctx,
        children,
        name="CTA",
        background_image=photo.url,
        overlay="rgba(15, 23, 42, 0.6)",
        extra_styles={
            "alignItems": "center",
            "paddingTop": "120px",
            "paddingBottom": "120px",
        },
    )


async def _build_contact(
    block: ContactBlock, ctx: RenderContext
) -> BuilderElement:
    s = ctx.styles
    info: list[BuilderElement] = [
        _text(block.heading, name="Heading", styles=s.heading_md, mobile=s.heading_mobile)
    ]
    if block.subheading:
        info.append(_text(block.subheading, name="Subheading", styles=s.subhead))
    if block.email:
        info.append(
            _text(
                f"Email: {block.email}",
                name="Email",
                styles={**s.body, "fontWeight": 600},
            )
        )
    if block.phone:
        info.append(
            _text(
                f"Phone: {block.phone}",
                name="Phone",
                styles={**s.body, "fontWeight": 600},
            )
        )

    form = BuilderElement(
        id=_uid(),
        name="Contact Form",
        type="contactForm",
        styles={**s.cards, "padding": "32px", "width": "100%"},
        content=BuilderElementContent(),
    )

    return _section(
        ctx,
        [_two_col(info, [form], styles={"alignItems": "start"})],
        name="Contact",
    )


async def _build_pricing(block: PricingBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    palette = ctx.theme.palette
    head: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles={**s.heading_md, "textAlign": "center"},
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head.append(
            _text(
                block.subheading,
                name="Subheading",
                styles={**s.subhead, "textAlign": "center", "marginLeft": "auto", "marginRight": "auto"},
            )
        )
    header = _container(head, name="Section header", styles={"alignItems": "center", "gap": "12px"})

    tier_cards: list[list[BuilderElement]] = []
    for tier in block.tiers:
        children: list[BuilderElement] = []
        if tier.highlighted:
            children.append(
                _text(
                    "Most popular",
                    name="Badge",
                    styles={
                        "fontFamily": ctx.theme.typography.body_font,
                        "fontSize": "11px",
                        "fontWeight": 700,
                        "letterSpacing": "0.08em",
                        "textTransform": "uppercase",
                        "color": palette.primary,
                        "margin": "0 0 8px 0",
                    },
                )
            )
        children.append(
            _text(
                tier.name,
                name="Tier name",
                styles={
                    "fontFamily": ctx.theme.typography.heading_font,
                    "fontSize": "20px",
                    "fontWeight": 600,
                    "color": palette.secondary,
                    "margin": "0",
                },
            )
        )
        children.append(
            _text(
                tier.price,
                name="Price",
                styles={
                    "fontFamily": ctx.theme.typography.heading_font,
                    "fontSize": "36px",
                    "fontWeight": 700,
                    "color": palette.secondary,
                    "margin": "8px 0",
                },
            )
        )
        if tier.description:
            children.append(_text(tier.description, name="Tier desc", styles={**s.body, "fontSize": "14px"}))
        if tier.features:
            children.append(
                _container(
                    [
                        _text(
                            f"✓ {feat}",
                            name="Feature",
                            styles={
                                **s.body,
                                "fontSize": "14px",
                                "padding": "4px 0",
                            },
                        )
                        for feat in tier.features
                    ],
                    name="Feature list",
                    styles={"gap": "0", "marginTop": "12px"},
                )
            )
        children.append(
            _link(
                tier.cta_label,
                tier.cta_href,
                ctx=ctx,
                primary=tier.highlighted,
                extra={"marginTop": "20px", "alignSelf": "stretch"},
            )
        )
        tier_cards.append(children)

    # Lay tiers in a grid
    n = len(tier_cards)
    if n == 2:
        grid = _two_col(tier_cards[0], tier_cards[1], styles={"gap": "24px", "alignItems": "stretch"})
    elif n == 3:
        grid = _three_col(tier_cards, styles={"gap": "24px", "alignItems": "stretch"})
    else:
        # 4+ tiers. The builder's 3Col is locked to 3 columns, so a 4-up row
        # can't be a column-layout type. Use a plain flex container (which the
        # builder renders faithfully) with wrapping cards instead.
        grid = BuilderElement(
            id=_uid(),
            name="Pricing grid",
            type="container",
            styles={
                "display": "flex",
                "flexDirection": "row",
                "flexWrap": "wrap",
                "gap": "24px",
                "width": "100%",
                "alignItems": "stretch",
            },
            content=[
                _container(
                    card,
                    name="Column",
                    styles={"gap": "8px", "flex": "1 1 220px", "minWidth": "0"},
                )
                for card in tier_cards
            ],
            responsiveStyles=ResponsiveStyles(mobile={"flexDirection": "column"}),
        )

    _apply_card_styles([grid], s.cards)
    # Highlight the recommended tier
    if isinstance(grid.content, list):
        for col, tier in zip(grid.content, block.tiers):
            if tier.highlighted:
                col.styles = {
                    **col.styles,
                    "border": f"2px solid {palette.primary}",
                    "boxShadow": f"0 8px 24px {_hairline(palette.primary, 0.18)}",
                }

    return _section(ctx, [header, grid], name="Pricing")


async def _build_team(block: TeamBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    head: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles={**s.heading_md, "textAlign": "center"},
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head.append(_text(block.subheading, name="Subheading", styles={**s.subhead, "textAlign": "center", "maxWidth": "640px"}))
    header = _container(head, name="Section header", styles={"alignItems": "center", "gap": "12px"})

    member_cards: list[list[BuilderElement]] = []
    for member in block.members:
        if member.photo_url:
            photo = PhotoResult(
                url=member.photo_url,
                alt=member.photo_alt or member.name,
                photographer=None,
                photographer_url=None,
                source="scraped",
            )
        else:
            photo = await ctx.resolver.resolve(
                member.photo_query, intent="avatar", alt_fallback=member.name
            )
        card: list[BuilderElement] = [
            _image_from_photo(
                photo,
                name="Member photo",
                aspect_ratio="1 / 1",
                border_radius="9999px",
                extra_styles={
                    "width": "112px",
                    "height": "112px",
                    "alignSelf": "center",
                    "marginBottom": "12px",
                    "overflow": "hidden",
                    "border": "3px solid rgba(255,255,255,0.96)",
                    "boxShadow": (
                        f"0 0 0 4px {_hairline(ctx.theme.palette.primary, 0.10)}, "
                        "0 14px 30px rgba(15,23,42,0.18)"
                    ),
                },
            ),
            _text(
                member.name,
                name="Member name",
                styles={
                    "fontFamily": ctx.theme.typography.heading_font,
                    "fontSize": "20px",
                    "lineHeight": "1.25",
                    "fontWeight": 800,
                    "color": ctx.theme.palette.secondary,
                    "margin": "0",
                    "textAlign": "center",
                },
            ),
            _text(
                member.role,
                name="Member role",
                styles={
                    **s.body,
                    "fontSize": "12px",
                    "lineHeight": "1.35",
                    "color": ctx.theme.palette.primary,
                    "textAlign": "center",
                    "fontWeight": 800,
                    "letterSpacing": "0.08em",
                    "textTransform": "uppercase",
                },
            ),
        ]
        bio = getattr(member, "bio", None) or getattr(member, "description", None)
        if bio:
            card.append(
                _text(
                    bio,
                    name="Member bio",
                    styles={
                        **s.body,
                        "fontSize": "14px",
                        "lineHeight": "1.65",
                        "textAlign": "center",
                        "marginTop": "8px",
                        "maxWidth": "280px",
                    },
                )
            )
        member_cards.append(card)

    # Lay out 3 per row
    rows: list[BuilderElement] = []
    triplet: list[list[BuilderElement]] = []
    for card in member_cards:
        triplet.append(card)
        if len(triplet) == 3:
            rows.append(_three_col(triplet, styles={"gap": "24px"}))
            triplet = []
    if triplet:
        if len(triplet) == 2:
            rows.append(_two_col(triplet[0], triplet[1], styles={"gap": "24px"}))
        else:
            rows.append(_container(triplet[0], name="Member card", styles=s.cards))

    _apply_card_styles(
        rows,
        {
            **s.cards,
            "alignItems": "stretch",
            "gap": "8px",
            "padding": "34px 28px 30px",
            "borderRadius": "24px",
            "backgroundColor": "rgba(255,255,255,0.94)",
            "border": "1px solid rgba(148,163,184,0.18)",
            "boxShadow": "0 16px 40px rgba(15,23,42,0.08)",
        },
    )
    return _section(ctx, [header, *rows], name="Team")


async def _build_gallery(block: GalleryBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    head: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles={**s.heading_md, "textAlign": "center"},
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head.append(_text(block.subheading, name="Subheading", styles={**s.subhead, "textAlign": "center"}))
    header = _container(head, name="Section header", styles={"alignItems": "center", "gap": "12px"})

    children = ctx.children_by_parent.get(ctx.current_page_slug or "", [])

    tiles: list[BuilderElement] = []
    for item in block.items:
        photo = await ctx.resolver.resolve(item.image_query, intent="generic", alt_fallback=item.title or item.image_query)
        tile = _image_from_photo(
            photo,
            name="Gallery item",
            aspect_ratio="4 / 3",
            border_radius=f"{ctx.theme.buttons.radius + 4}px",
        )
        # If we have child case-study/portfolio pages, wrap each tile in a link
        # to its matching detail page. Match by tile title (LLM fills this in
        # for case studies; falls through silently otherwise).
        matched = (
            _match_child_by_title(item.title or "", children) if item.title else None
        )
        if matched is not None:
            tile = BuilderElement(
                id=_uid(),
                name="Gallery link",
                type="link",
                styles={"display": "block", "textDecoration": "none"},
                content=BuilderElementContent(
                    href=f"/{matched.slug}",
                    ariaLabel=f"View {matched.title}",
                ),
            )
            # Nest the image inside the link container by re-emitting under it.
            # The webtree builder treats link.content as a leaf; for visual
            # wrapping we use a container with onclick-like href via aria.
            tile = BuilderElement(
                id=_uid(),
                name="Gallery tile",
                type="container",
                styles={
                    "position": "relative",
                    "borderRadius": f"{ctx.theme.buttons.radius + 4}px",
                    "overflow": "hidden",
                    "cursor": "pointer",
                },
                content=[
                    _image_from_photo(
                        photo,
                        name="Gallery item image",
                        aspect_ratio="4 / 3",
                        border_radius=f"{ctx.theme.buttons.radius + 4}px",
                    ),
                    _link(
                        matched.title,
                        f"/{matched.slug}",
                        ctx=ctx,
                        primary=False,
                        extra={
                            "position": "absolute",
                            "left": "16px",
                            "bottom": "16px",
                            "backgroundColor": "rgba(0,0,0,0.55)",
                            "color": "#ffffff",
                            "padding": "8px 14px",
                            "borderRadius": f"{ctx.theme.buttons.radius}px",
                        },
                    ),
                ],
            )
        tiles.append(tile)

    # Grid is owned by the builder for column-layout types — see _two_col.
    grid = BuilderElement(
        id=_uid(),
        name="Gallery grid",
        type="3Col",
        styles={
            "gap": "16px",
            "width": "100%",
        },
        content=tiles,
        responsiveStyles=ResponsiveStyles(mobile={"gap": "12px"}),
    )

    return _section(ctx, [header, grid], name="Gallery")


async def _build_menu(block: MenuBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    head: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles={**s.heading_md, "textAlign": "center"},
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head.append(_text(block.subheading, name="Subheading", styles={**s.subhead, "textAlign": "center"}))
    header = _container(head, name="Section header", styles={"alignItems": "center", "gap": "12px"})

    cat_blocks: list[BuilderElement] = []
    for category in block.categories:
        item_children: list[BuilderElement] = []
        for item in category.items:
            row_children: list[BuilderElement] = [
                _container(
                    [
                        _text(
                            item.name,
                            name="Item name",
                            styles={
                                "fontFamily": ctx.theme.typography.heading_font,
                                "fontSize": "17px",
                                "fontWeight": 600,
                                "color": ctx.theme.palette.secondary,
                                "margin": "0",
                            },
                        ),
                        *([
                            _text(
                                item.description,
                                name="Item desc",
                                styles={**s.body, "fontSize": "14px", "margin": "2px 0 0 0"},
                            )
                        ] if item.description else []),
                    ],
                    name="Item info",
                    styles={"flex": "1", "gap": "0"},
                ),
            ]
            if item.price:
                row_children.append(
                    _text(
                        item.price,
                        name="Item price",
                        styles={
                            "fontFamily": ctx.theme.typography.heading_font,
                            "fontSize": "16px",
                            "fontWeight": 600,
                            "color": ctx.theme.palette.primary,
                            "whiteSpace": "nowrap",
                            "marginLeft": "16px",
                        },
                    )
                )
            item_children.append(
                _container(
                    row_children,
                    name="Menu item",
                    styles={
                        "flexDirection": "row",
                        "justifyContent": "space-between",
                        "alignItems": "flex-start",
                        "paddingTop": "12px",
                        "paddingBottom": "12px",
                        "borderBottom": f"1px solid {_hairline(ctx.theme.palette.secondary, 0.10)}",
                    },
                )
            )
        cat_blocks.append(
            _container(
                [
                    _text(
                        category.name,
                        name="Category name",
                        styles={
                            "fontFamily": ctx.theme.typography.heading_font,
                            "fontSize": "22px",
                            "fontWeight": 700,
                            "color": ctx.theme.palette.secondary,
                            "margin": "0 0 12px 0",
                            "paddingBottom": "8px",
                            "borderBottom": f"2px solid {ctx.theme.palette.primary}",
                        },
                    ),
                    *item_children,
                ],
                name="Menu category",
                styles={"gap": "0", "marginBottom": "32px"},
            )
        )

    return _section(
        ctx,
        [
            header,
            _container(
                cat_blocks,
                name="Menu list",
                styles={"maxWidth": "720px", "width": "100%", "alignSelf": "center", "gap": "0"},
            ),
        ],
        name="Menu",
    )


async def _build_process(block: ProcessBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    head: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles={**s.heading_md, "textAlign": "center"},
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head.append(_text(block.subheading, name="Subheading", styles={**s.subhead, "textAlign": "center"}))
    header = _container(head, name="Section header", styles={"alignItems": "center", "gap": "12px"})

    steps_cards: list[list[BuilderElement]] = []
    for index, step in enumerate(block.steps, start=1):
        card: list[BuilderElement] = [
            _container(
                [
                    _text(
                        f"{index:02d}",
                        name="Step number",
                        styles={
                            "fontFamily": ctx.theme.typography.heading_font,
                            "fontSize": "13px",
                            "fontWeight": 700,
                            "letterSpacing": "0.06em",
                            "color": ctx.theme.palette.primary,
                            "margin": "0 0 8px 0",
                        },
                    ),
                ],
                name="Step number wrap",
                styles={"width": "auto"},
            ),
            _text(
                step.title,
                name="Step title",
                styles={
                    "fontFamily": ctx.theme.typography.heading_font,
                    "fontSize": "20px",
                    "fontWeight": 600,
                    "color": ctx.theme.palette.secondary,
                    "margin": "0",
                },
            ),
            _text(step.description, name="Step body", styles=s.body),
        ]
        steps_cards.append(card)

    rows: list[BuilderElement] = []
    triplet: list[list[BuilderElement]] = []
    for card in steps_cards:
        triplet.append(card)
        if len(triplet) == 3:
            rows.append(_three_col(triplet, styles={"gap": "24px"}))
            triplet = []
    if triplet:
        if len(triplet) == 2:
            rows.append(_two_col(triplet[0], triplet[1], styles={"gap": "24px"}))
        else:
            rows.append(_container(triplet[0], name="Step card", styles=s.cards))

    _apply_card_styles(rows, s.cards)
    return _section(ctx, [header, *rows], name="Process")


def _apply_card_styles(rows: list[BuilderElement], card_styles: dict[str, Any]) -> None:
    """Walk a row of columns and apply card styling to every Column container."""

    def visit(node: BuilderElement) -> None:
        if isinstance(node.content, list):
            for child in node.content:
                if child.name == "Column":
                    child.styles = {**card_styles, **child.styles}
                visit(child)

    for row in rows:
        visit(row)


async def _build_linkbar(block: LinkBarBlock, ctx: RenderContext) -> BuilderElement:
    """Slim announcement strap — accent-colored bar with a label + inline links.

    Mirrors the source-site pattern it was detected from (release banners,
    promo bars). Always renders on the brand primary; the rhythm passes skip
    it because it carries its own backgroundColor.
    """
    text_color = ctx.theme.buttons.text
    row: list[BuilderElement] = []
    if block.label:
        row.append(
            _text(
                block.label,
                name="Strap label",
                styles={
                    "width": "auto",
                    "fontWeight": 700,
                    "fontSize": "14px",
                    "color": text_color,
                    "margin": "0",
                },
            )
        )
    for link in block.links:
        row.append(
            BuilderElement(
                id=_uid(),
                name="Strap link",
                type="link",
                styles={
                    "fontWeight": 600,
                    "fontSize": "14px",
                    "color": text_color,
                    "textDecoration": "underline",
                    "textUnderlineOffset": "3px",
                },
                content=BuilderElementContent(innerText=link.label, href=link.href),
            )
        )
    return _section(
        ctx,
        [
            _container(
                row,
                name="Announcement bar",
                styles={
                    "flexDirection": "row",
                    "flexWrap": "wrap",
                    "alignItems": "center",
                    "justifyContent": "center",
                    "gap": "20px",
                },
            )
        ],
        name="Linkbar",
        surface_override=ctx.theme.palette.primary,
        extra_styles={"paddingTop": "14px", "paddingBottom": "14px"},
    )


async def _build_timeline(block: TimelineBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    head: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles={**s.heading_md, "textAlign": "center"},
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head.append(_text(block.subheading, name="Subheading", styles={**s.subhead, "textAlign": "center"}))
    header = _container(head, name="Section header", styles={"alignItems": "center", "gap": "12px"})

    entries: list[BuilderElement] = []
    for item in block.items:
        lines: list[BuilderElement] = [
            _text(
                item.year,
                name="Timeline year",
                styles={
                    "fontFamily": ctx.theme.typography.heading_font,
                    "fontSize": "14px",
                    "fontWeight": 700,
                    "letterSpacing": "0.06em",
                    "color": ctx.theme.palette.primary,
                    "margin": "0",
                },
            ),
            _text(
                item.title,
                name="Timeline title",
                styles={
                    "fontFamily": ctx.theme.typography.heading_font,
                    "fontSize": "20px",
                    "fontWeight": 700,
                    "color": ctx.theme.palette.secondary,
                    "margin": "0",
                },
            ),
        ]
        if item.description:
            lines.append(_text(item.description, name="Timeline body", styles=s.body))
        entries.append(
            _container(
                lines,
                name="Timeline entry",
                styles={
                    "gap": "6px",
                    "padding": "20px 0",
                    "borderBottom": f"1px solid {_hairline(ctx.theme.palette.secondary, 0.12)}",
                },
            )
        )
    list_wrap = _container(
        entries,
        name="Timeline list",
        styles={"gap": "0", "maxWidth": "720px", "marginLeft": "auto", "marginRight": "auto"},
    )
    return _section(ctx, [header, list_wrap], name="Timeline")


async def _build_awards(block: AwardsBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    head: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles={**s.heading_md, "textAlign": "center"},
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head.append(_text(block.subheading, name="Subheading", styles={**s.subhead, "textAlign": "center"}))
    header = _container(head, name="Section header", styles={"alignItems": "center", "gap": "12px"})

    cards: list[list[BuilderElement]] = []
    for item in block.items:
        card: list[BuilderElement] = [
            _text(
                item.title,
                name="Award title",
                styles={
                    "fontFamily": ctx.theme.typography.heading_font,
                    "fontSize": "18px",
                    "fontWeight": 700,
                    "color": ctx.theme.palette.secondary,
                    "margin": "0",
                    "textAlign": "center",
                },
            )
        ]
        meta = " · ".join(p for p in (item.issuer, item.year) if p)
        if meta:
            card.append(
                _text(
                    meta,
                    name="Award meta",
                    styles={
                        **s.body,
                        "fontSize": "13px",
                        "textAlign": "center",
                        "color": ctx.theme.palette.primary,
                        "fontWeight": 700,
                        "letterSpacing": "0.04em",
                        "textTransform": "uppercase",
                    },
                )
            )
        cards.append(card)

    rows: list[BuilderElement] = []
    triplet: list[list[BuilderElement]] = []
    for card in cards:
        triplet.append(card)
        if len(triplet) == 3:
            rows.append(_three_col(triplet, styles={"gap": "24px"}))
            triplet = []
    if triplet:
        if len(triplet) == 2:
            rows.append(_two_col(triplet[0], triplet[1], styles={"gap": "24px"}))
        else:
            rows.append(_container(triplet[0], name="Award card", styles=s.cards))

    _apply_card_styles(
        rows,
        {
            **s.cards,
            "alignItems": "center",
            "gap": "8px",
            "padding": "28px 24px",
            "borderRadius": "20px",
            "backgroundColor": "rgba(255,255,255,0.94)",
            "border": "1px solid rgba(148,163,184,0.18)",
        },
    )
    return _section(ctx, [header, *rows], name="Awards")


async def _build_clients(block: ClientsBlock, ctx: RenderContext) -> BuilderElement:
    """Logo wall of named clients/customers/partners.

    Deliberately typographic rather than image-resolved: ``logo_query`` has no
    real brand-mark source today (Pexels/scraped pool would return an unrelated
    stock photo mislabeled as a logo), so each name renders as a wordmark chip
    instead of a fabricated logo image.
    """
    s = ctx.styles
    head: list[BuilderElement] = [
        _text(
            block.heading,
            name="Heading",
            styles={**s.heading_md, "textAlign": "center"},
            mobile=s.heading_mobile,
        )
    ]
    if block.subheading:
        head.append(_text(block.subheading, name="Subheading", styles={**s.subhead, "textAlign": "center"}))
    header = _container(head, name="Section header", styles={"alignItems": "center", "gap": "12px"})

    chips = [
        _text(
            item.name,
            name="Client name",
            styles={
                "fontFamily": ctx.theme.typography.heading_font,
                "fontSize": "16px",
                "fontWeight": 700,
                "letterSpacing": "0.02em",
                "color": ctx.theme.palette.secondary,
                "textAlign": "center",
                "padding": "16px 22px",
                "border": f"1px solid {_hairline(ctx.theme.palette.secondary, 0.16)}",
                "borderRadius": "12px",
                "backgroundColor": "rgba(255,255,255,0.7)",
                "width": "auto",
            },
        )
        for item in block.items
    ]
    wall = _container(
        chips,
        name="Client wall",
        styles={"flexDirection": "row", "flexWrap": "wrap", "justifyContent": "center", "gap": "12px"},
    )
    return _section(ctx, [header, wall], name="Clients")


async def _build_stats(block: StatsBlock, ctx: RenderContext) -> BuilderElement:
    s = ctx.styles
    sections: list[BuilderElement] = []
    if block.heading:
        sections.append(
            _container(
                [_text(block.heading, name="Heading", styles={**s.heading_md, "textAlign": "center"}, mobile=s.heading_mobile)],
                name="Section header",
                styles={"alignItems": "center"},
            )
        )

    stat_cells = [
        _container(
            [
                _text(
                    item.value,
                    name="Stat value",
                    styles={
                        "fontFamily": ctx.theme.typography.heading_font,
                        "fontSize": "40px",
                        "fontWeight": 800,
                        "color": ctx.theme.palette.primary,
                        "margin": "0",
                        "textAlign": "center",
                    },
                ),
                _text(
                    item.label,
                    name="Stat label",
                    styles={
                        **s.body,
                        "fontSize": "13px",
                        "textAlign": "center",
                        "letterSpacing": "0.04em",
                        "textTransform": "uppercase",
                        "fontWeight": 700,
                    },
                ),
            ],
            name="Stat",
            styles={"alignItems": "center", "gap": "4px", "flex": "1 1 140px"},
        )
        for item in block.items
    ]
    sections.append(
        _container(
            stat_cells,
            name="Stats row",
            styles={"flexDirection": "row", "flexWrap": "wrap", "justifyContent": "center", "gap": "32px"},
        )
    )
    return _section(ctx, sections, name="Stats")


# --- dispatch + top-level entry -------------------------------------------------


_DISPATCH = {
    "hero": _build_hero,
    "features": _build_features,
    "services": _build_services,
    "testimonials": _build_testimonials,
    "about": _build_about,
    "faq": _build_faq,
    "cta": _build_cta,
    "contact": _build_contact,
    "pricing": _build_pricing,
    "team": _build_team,
    "gallery": _build_gallery,
    "menu": _build_menu,
    "process": _build_process,
    "linkbar": _build_linkbar,
    "timeline": _build_timeline,
    "awards": _build_awards,
    "clients": _build_clients,
    "stats": _build_stats,
}


# Image-resolution intent per block kind (catalog path). Drives the resolver's
# scraped-vs-Pexels choice (e.g. cta_bg + avatars skip the scraped pool and use
# atmospheric Pexels imagery).
_IMAGE_INTENT = {"hero": "hero", "about": "about", "cta": "cta_bg", "team": "avatar"}

# Photo sources that count as a "genuine" hero image. A `placeholder` result is
# the resolver's last-resort on-brand gradient — it means no scraped/document
# match AND no stock photo — so we render the brand gradient header instead of
# stretching a decorative gradient full-bleed behind text. See
# _apply_hero_photo_policy.
_GENUINE_PHOTO_SOURCES = frozenset({"scraped", "pexels"})


async def _apply_hero_photo_policy(block: HeroBlock, ctx: RenderContext) -> PhotoResult:
    """Resolve the hero image once and normalise the block to ONE consistent
    treatment across every page.

    The planner LLM decides each page's hero ``image_query`` / ``layout``
    independently, so some heroes came back full-bleed with a photo and others
    image-less (a short gradient) on the same site. This re-decides it
    deterministically from the resolver's *actual* result:

      * a genuine photo (a matching scraped/document image, or a stock photo when
        Pexels is configured) -> full-bleed background hero;
      * only a Picsum placeholder available (no scraped match, no stock) -> drop
        ``image_query`` so selection lands on the gradient header.

    Returns the resolved photo so the caller can reuse it without re-picking
    (which would consume a different pooled image for the same slot).
    """
    photo = await ctx.resolver.resolve(
        block.image_query,
        intent="hero",
        alt_fallback=block.image_alt or block.headline,
        prefer=ctx.page_images,
    )
    if photo.source in _GENUINE_PHOTO_SOURCES:
        block.layout = "background"
        # The background variant is only feasible with an image slot; if the LLM
        # left image_query blank, give it one so selection keeps the photo.
        if not (block.image_query or "").strip():
            block.image_query = block.image_alt or block.headline or "brand photo"
    else:
        block.image_query = None
    return photo


async def block_to_element(
    block: ContentBlock,
    ctx: RenderContext,
    *,
    is_homepage: bool = True,
    hero_scroll_target_kind: str | None = None,
    explicit_template_id: str | None = None,
) -> BuilderElement:
    # Heroes get one consistent treatment site-wide (see _apply_hero_photo_policy).
    # The photo is resolved up front so the gradient-vs-photo choice can key off
    # the resolver's real source; the closure below reuses it (no second pick).
    hero_photo: PhotoResult | None = None
    if block.kind == "hero":
        hero_photo = await _apply_hero_photo_policy(block, ctx)

    # Catalogue path: for section types that have shared builder templates, the
    # LLM-mapped content fills a chosen template (selection by feasibility +
    # preference). Theme flows via CSS vars + builderStyles — no inline colours.
    # `explicit_template_id` (the design-brain's pick, if any) is only used when
    # it actually belongs to this block's section type AND is feasible for its
    # content — select_template() enforces both, silently falling back to the
    # deterministic mood order otherwise (see app/services/design_brain.py).
    mapped = block_to_section(
        block,
        mood=ctx.theme.mood,
        is_homepage=is_homepage,
        hero_scroll_target_kind=hero_scroll_target_kind,
        explicit_id=explicit_template_id,
    )
    if mapped is not None:
        template, content = mapped
        intent = _IMAGE_INTENT.get(block.kind, "generic")

        async def resolve_image(query: str) -> tuple[str, str | None]:
            # Reuse the hero photo already resolved by the policy above; for every
            # other slot, resolve normally.
            photo = (
                hero_photo
                if hero_photo is not None
                else await ctx.resolver.resolve(
                    query, intent=intent, alt_fallback=query, prefer=ctx.page_images
                )
            )
            # Capture the FIRST resolved image's band as the section's featured
            # image for the luminance pass (Phase 4b). Later images (e.g. grid
            # items) don't override the dominant one.
            if ctx.section_image_band is None and photo.band is not None:
                ctx.section_image_band = photo.band
            return photo.url, photo.avg_color

        return await fill_template(
            template,
            content,
            resolve_image=resolve_image,
            # Empty content: the builder's contact-form renderer applies its own
            # field defaults, so the generator need not embed them.
            content_factories={"contactFormDefault": lambda: {}},
            # Brand-tinted, luminance-adaptive overlay for photo backgrounds.
            theme={
                "primary": ctx.theme.palette.primary,
                "secondary": ctx.theme.palette.secondary,
            },
        )

    # Legacy path: section kinds without a shared template yet (pricing, team,
    # gallery, menu, process) still use the hand-rolled builders.
    builder = _DISPATCH.get(block.kind)
    if not builder:
        raise ValueError(f"No schema builder registered for block kind: {block.kind}")
    return await builder(block, ctx)  # type: ignore[arg-type]


def _has_link_to(element: BuilderElement, href: str) -> bool:
    """True if any leaf link in ``element``'s subtree points at ``href``."""
    content = element.content
    if isinstance(content, list):
        return any(_has_link_to(child, href) for child in content)
    return getattr(content, "href", None) == href


def _nav_items_from_pages(pages: list[GeneratedPage]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for page in pages:
        if page.is_homepage:
            items.insert(0, ("Home", "/"))
        else:
            items.append((page.title, f"/{page.slug}"))
    return items


async def plan_to_site(
    plan: SitePlan,
    *,
    brand: BrandIdentity | None = None,
    theme: ThemeTokens | None = None,
    scraped_images: list[str] | None = None,
    scraped_metadata: list[ImageMetadata] | None = None,
    page_images: dict[str, list[ImageMetadata]] | None = None,
    contact: dict[str, str] | None = None,
    extra_footer_nav: list[tuple[str, str]] | None = None,
    market_cue: str | None = None,
    place_cue: str | None = None,
    social_links: list[tuple[str, str]] | None = None,
) -> GeneratedSite:
    """
    Build a complete, themed site from a SitePlan.

    - `brand` supplies logo + brand mood. If None, we derive a minimal one
      from the plan (name = plan.site_name).
    - `theme` overrides automatic theme generation. If None, we build one
      from brand.extracted_palette[0] (if any) + brand.mood.
    - `scraped_metadata` carries rich per-image hints (alt, intent, dims) so
      the resolver can match scraped images intelligently against each slot's
      image_query instead of round-robin. Falls back to `scraped_images` if
      only bare URLs are available.
    - `extra_footer_nav` lets the caller add pages that aren't in plan.pages
      yet (e.g. legal pages that will be appended after this call) so they
      still appear in the footer navigation.
    """
    effective_brand = brand or BrandIdentity(
        name=plan.site_name,
        tagline=plan.tagline,
        mood="modern",
    )
    if theme is None:
        seed = (
            effective_brand.extracted_palette[0]
            if effective_brand.extracted_palette
            else plan.primary_color_hint
        )
        theme = build_theme(
            seed,
            mood=effective_brand.mood or "modern",
            # Stable per-site seed → same-mood sites get distinct-but-on-brand
            # type from the mood's font pool, while one brand stays idempotent.
            font_seed=effective_brand.name,
            # Industry steers the pick toward the best-fitting pairing in the pool
            # (e.g. a wellness site → calmer faces); the seed only breaks ties.
            industry=plan.industry_category,
            # "auto": brands with a real logo hue keep the brand-driven Tailwind
            # snap; brands with no usable hue get a curated industry palette
            # instead of the generic-blue fallback.
            palette_mode="auto",
            color_scheme=resolve_color_scheme(
                None, effective_brand.color_scheme, effective_brand.logo_is_light
            ),
        )

    styles = make_style_tokens(theme)
    resolver = ImageResolver(
        scraped_images=scraped_images,
        scraped_metadata=scraped_metadata,
        market_cue=market_cue,
        industry_category=plan.industry_category,
        place_cue=place_cue,
        primary_hex=theme.palette.primary,
        secondary_hex=theme.palette.secondary,
    )

    # Pre-compute parent/child relationships so listing blocks (services /
    # gallery) on a parent page can cross-link to detail sub-pages.
    children_by_parent: dict[str, list[ChildPageRef]] = {}
    page_title_by_slug: dict[str, str] = {}
    for pp in plan.pages:
        page_title_by_slug[pp.slug] = pp.title
        parent = pp.parent_slug
        if parent is None:
            continue
        children_by_parent.setdefault(parent, []).append(
            ChildPageRef(slug=pp.slug, title=pp.title, page_type=pp.page_type)
        )

    # Build each page's body
    pages: list[GeneratedPage] = []
    for page_plan in plan.pages:
        # Each page resets section_index so rotation starts fresh per page.
        ctx = RenderContext(
            theme=theme,
            resolver=resolver,
            styles=styles,
            current_page_slug=page_plan.slug,
            current_parent_slug=page_plan.parent_slug,
            children_by_parent=children_by_parent,
            page_title_by_slug=page_title_by_slug,
            page_images=(page_images or {}).get(page_plan.slug, []),
        )
        # Phase 5 activation (gated): deterministically assign visual_policy per
        # the §5 matrix so the luminance pass engages. Off by default — when the
        # flag is unset, no block gets a policy and output stays byte-identical.
        if settings.luminance_rhythm_enabled:
            assign_visual_policies(page_plan.blocks)

        elements: list[BuilderElement] = []
        # Inputs to the luminance pass, built in lockstep with `elements` so they
        # align index-for-index (SECTION_VISUAL_POLICY_SPEC.md).
        visual_inputs: list[SectionVisualInput] = []
        # Interior-page hero CTA policy: an interior hero either drops its CTA
        # (compact variants) or becomes a scroll cue to this page's FIRST content
        # section (full-bleed variants). Precompute that target so the hero's CTA
        # can anchor to it; the matching anchor id is stamped on the target's
        # section element once we know the hero actually emitted the cue.
        content_kinds = [b.kind for b in page_plan.blocks if b.kind != "hero"]
        hero_target_kind = content_kinds[0] if content_kinds else None
        hero_element: BuilderElement | None = None
        target_element: BuilderElement | None = None
        # Design-brain pass: picks a template variant per section so sites of
        # the same mood/industry stop converging on one identical layout. A
        # failed/disabled call returns an empty recipe — every lookup below
        # then yields None and selection falls back to today's deterministic
        # mood-ordered choice, unchanged.
        design_recipe: DesignRecipe = await generate_design_recipe(
            mood=effective_brand.mood,
            industry=plan.industry_category,
            section_kinds=[b.kind for b in page_plan.blocks],
        )
        # Sub-pages get a breadcrumb prepended above the hero — a non-participant.
        if page_plan.parent_slug is not None:
            elements.append(_build_breadcrumb(page_plan, ctx))
            visual_inputs.append(SectionVisualInput())
        for block_index, block in enumerate(page_plan.blocks):
            # Reset the per-section capture, render, then read the band of the
            # section's featured image (Phase 4b) into the pass input.
            ctx.section_image_band = None
            element = await block_to_element(
                block,
                ctx,
                is_homepage=page_plan.is_homepage,
                hero_scroll_target_kind=hero_target_kind,
                explicit_template_id=design_recipe.template_for(block_index),
            )
            elements.append(element)
            if hero_element is None and block.kind == "hero":
                hero_element = element
            # Remember the first content section so its element can carry the
            # hero's scroll anchor if the hero ended up full-bleed.
            if target_element is None and block.kind == hero_target_kind:
                target_element = element
            visual_inputs.append(
                section_visual_input_for(block, image_band=ctx.section_image_band)
            )
        # If the hero rendered a scroll cue, tag its target section with a
        # matching anchor so the href resolves (webtree-public renders `anchorId`
        # as the HTML id; the global `scroll-behavior: smooth` does the rest).
        if hero_target_kind and hero_element is not None and target_element is not None:
            anchor = hero_scroll_anchor(hero_target_kind)
            if _has_link_to(hero_element, f"#{anchor}"):
                target_element.anchorId = anchor
        apply_luminance_rhythm(elements, visual_inputs, theme.palette)
        # Legacy color-blocking rhythm: alternates the remaining (non-policy)
        # plain sections between page-bg and surface tint. Sections the luminance
        # pass already filled carry their own backgroundColor and are skipped.
        apply_section_rhythm(elements)
        # 2025/26 modernization: fluid type, card depth/glass, atmospheric
        # surface backgrounds — applied per-mood over the assembled sections.
        # Runs BEFORE apply_section_dividers, which reads each section's
        # resolved `backgroundTexture` tag (set here) to carry a matching
        # grain/mesh layer onto a divider that reveals it.
        modernize_sections(elements, theme)
        # Shaped section dividers — color reads each section's backgroundColor
        # (modernization never changes it, only adds an overlay) and texture
        # reads the backgroundTexture tag modernize_sections just set.
        apply_section_dividers(elements, effective_brand.mood)
        # Asymmetric headers + scroll/backdrop motion — applied last so they
        # read the final band/background each section landed on.
        apply_heading_alignment(elements, effective_brand.mood)
        apply_motion(
            elements,
            industry=plan.industry_category,
            hero_has_photo=bool(
                hero_element and (hero_element.styles or {}).get("backgroundImage")
            ),
        )
        pages.append(
            GeneratedPage(
                slug=page_plan.slug,
                title=page_plan.title,
                description=page_plan.description,
                is_homepage=page_plan.is_homepage,
                body_schema=BodySchema(elements=elements),
                seo=PageSeo(
                    title=page_plan.seo_title,
                    description=page_plan.seo_description,
                    keywords=page_plan.seo_keywords or None,
                    ogTitle=page_plan.seo_title,
                    ogDescription=page_plan.seo_description,
                ),
                parent_slug=page_plan.parent_slug,
                nav_rank=page_plan.nav_rank,
                from_source=page_plan.from_source,
            )
        )

    page_tree = _build_page_tree(pages)
    nav_items = _nav_items_from_pages(pages)
    footer_nav = list(nav_items)
    if extra_footer_nav:
        footer_nav.extend(extra_footer_nav)
    primary_cta = ("Get in touch", "#contact")

    header = build_header(
        effective_brand,
        theme,
        nav_items=nav_items,
        primary_cta=primary_cta,
        page_tree=page_tree,
    )
    footer = build_footer(
        effective_brand,
        theme,
        nav_items=footer_nav,
        contact=contact,
        media_credits=resolver.attributions,
        page_tree=page_tree,
        extra_legal_nav=extra_footer_nav,
        social_links=social_links,
    )

    # Symmetric contrast safety net (both schemes): catalogue sections hard-code a
    # single dark text token (often `secondary`) that vanishes on a same-luminance
    # band — dark-on-dark (dark scheme / dark CTA bands) or light-on-light. Retarget
    # those to the band's correct foreground, keyed off the resolved band luminance.
    for _page in pages:
        enforce_text_contrast(_page.body_schema.elements, theme)
    for _chrome in (header, footer):
        if isinstance(_chrome, BuilderElement):
            enforce_text_contrast([_chrome], theme)

    site = GeneratedSite(
        site_name=plan.site_name,
        tagline=plan.tagline,
        primary_color=theme.palette.primary,
        secondary_color=theme.palette.secondary,
        pages=pages,
        page_tree=page_tree,
        media_credits=resolver.attributions,
        social_links=social_links or [],
        theme=theme,
        builder_styles=theme.to_builder_styles(),
        google_fonts=theme.typography.google_fonts,
        brand=effective_brand,
        header_schema=header,
        footer_schema=footer,
    )

    # Advisory UX/accessibility audit (alt text, contrast, font size, …). Logged
    # only; never mutates or blocks generation.
    try:
        import logging

        from app.services.ux_audit import audit_site, summarize

        findings = audit_site(site)
        if findings:
            logging.getLogger(__name__).info(
                "UX audit: %d finding(s) %s", len(findings), summarize(findings)
            )
    except Exception:  # noqa: BLE001 — an advisory check must never break a build
        pass

    return site


def _build_page_tree(pages: list[GeneratedPage]) -> list[PageNode]:
    """Materialize the parent/child hierarchy as a list of PageNode trees.

    Homepage first, then top-level pages in their natural order. Children
    nest under their parent_slug match; orphans (whose parent wasn't generated)
    are surfaced at the top level.
    """
    nodes_by_slug: dict[str, PageNode] = {}
    for p in pages:
        nodes_by_slug[p.slug] = PageNode(
            slug=p.slug,
            title=p.title,
            is_homepage=p.is_homepage,
            nav_rank=p.nav_rank,
            from_source=p.from_source,
        )

    roots: list[PageNode] = []
    for p in pages:
        node = nodes_by_slug[p.slug]
        if p.parent_slug and p.parent_slug in nodes_by_slug:
            nodes_by_slug[p.parent_slug].children.append(node)
        else:
            roots.append(node)
    # Homepage first, then source-nav order (unranked pages after ranked ones,
    # alphabetical within each group).
    roots.sort(
        key=lambda n: (
            not n.is_homepage,
            n.nav_rank is None,
            n.nav_rank if n.nav_rank is not None else 0,
            n.slug,
        )
    )
    return roots
