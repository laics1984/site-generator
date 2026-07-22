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

import asyncio
from dataclasses import dataclass, field
from time import perf_counter
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
from app.models.design_manifest import (
    DesignDecision,
    DesignManifest,
    FooterArchetype,
    HeaderArchetype,
)
from app.services.design_brain import DesignRecipe, generate_site_design_recipe
from app.services.design_director import (
    compose_design_manifest,
    record_manifest_choices,
)
from app.services.hero_director import (
    IMAGELESS_HERO_IDS,
    HeroDirective,
    plan_site_heroes,
)
from app.services.header_footer import build_footer, build_header
from app.services.image_match import SlotUsage
from app.services.media import ImageIntent, ImageResolver
from app.services.timing import log_elapsed, stage
from app.services.pexels import PhotoResult
from app.config import settings
from app.services.section_content import (
    _FULLBLEED_HERO_IDS,
    _PAGE_BG,
    _SURFACE_BG,
    Band,
    SectionVisualInput,
    _has_real_photo,
    apply_about_zigzag,
    apply_childcare_heading_colors,
    apply_childcare_pastel_rhythm,
    apply_luminance_rhythm,
    apply_section_rhythm,
    assign_visual_policies,
    block_to_section,
    enforce_text_contrast,
    hero_scroll_anchor,
    section_visual_input_for,
    style_whatsapp_links,
)
from app.services.template_filler import fill_template
from app.services.image_styling import washed_photo_background
from app.services.theme import (
    _adjust_lightness,
    _contrast,
    _ensure_contrast_against,
    _hex_to_rgb,
    _hls_to_rgb,
    _relative_luminance,
    _rgb_to_hex,
    _rgb_to_hls,
    band_colors,
    build_theme,
    color_family_name,
    resolve_color_scheme,
)
# Pure style/CSS helpers live in a sibling module; re-imported here so the public
# path app.services.schema_builder.<name> is unchanged for callers and tests.
from app.services.style_tokens import (
    StyleTokens,
    _fluid,
    _fluid_heading,
    _hairline,
    _muted,
    apply_section_decoration,
    glass_card_styles,
    grain_data_uri,
    make_style_tokens,
    mesh_gradient,
    section_background_image,
    shadow,
)


# --- render context -------------------------------------------------------------
# StyleTokens + the pure style helpers below live in services/style_tokens.py
# (imported above). RenderContext and the section/hero builders stay here.


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
    industry: str | None = None  # site industry — steers per-industry render passes (e.g. childcare hero colours)
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
    # Brand-stable seed for template-variety rotation (block_to_section's
    # variety_seed). Empty → legacy deterministic order.
    variety_seed: str = ""


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


def modernize_sections(
    sections: list[BuilderElement], theme: ThemeTokens, industry: str | None = None
) -> None:
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

    # Clean-UI policy: texture (mesh/grain) is an ACCENT, not a blanket. At most
    # ONE plain section per page carries it — the first eligible band — so a page
    # reads calm rather than uniformly noisy. Flat moods decorate nothing. Every
    # other plain section is tagged "flat" so the builder's per-section control and
    # the divider pass both see an honest, flat value.
    #
    # A shaped divider must sit only against solid colour, so sections that will
    # neighbour one are excluded from the accent here (apply_section_dividers also
    # flattens them as a safety net) — the accent then lands on a non-neighbour
    # band instead of being textured now and stripped later.
    divider_neighbours = _shaped_divider_neighbours(
        sections, getattr(theme, "mood", None), industry
    )
    decorated = False
    for idx, section in enumerate(sections):
        st = section.styles
        has_fill = bool(st.get("backgroundImage") or st.get("background"))
        is_plain = st.get("backgroundColor") in (
            surface_hex, _SURFACE_BG, page_hex, _PAGE_BG,
        )
        effective = getattr(section, "backgroundTexture", None) or strategy
        will_decorate = (
            is_plain
            and not has_fill
            and not decorated
            and idx not in divider_neighbours
            and effective != "flat"
            and section_background_image(theme, effective) is not None
        )

        headings: list[tuple[BuilderElement, float]] = []
        _walk_modernize(
            section,
            boost=boost,
            # Glass and texture never share a surface — frosted cards over a
            # mesh/grain band read busy. The accent section keeps flat cards.
            use_glass=use_glass and not will_decorate,
            shadow_scale=shadow_scale,
            theme=theme,
            headings=headings,
        )

        # The mood's display face goes on the section's largest heading (its H1).
        if display_font and headings:
            lead = max(headings, key=lambda pair: pair[1])[0]
            lead.styles["fontFamily"] = display_font

        if is_plain and not has_fill:
            if will_decorate:
                section.backgroundTexture = effective
                apply_section_decoration(st, theme, effective)
                decorated = True
            else:
                section.backgroundTexture = "flat"


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

# Industry overrides mood: a childcare site must keep soft flowing seams
# whatever mood the LLM picked (peak/slant read sharp; the brief wants curves).
_DIVIDER_SHAPE_BY_INDUSTRY: dict[str, str | None] = {"childcare": "wave"}

_DEFAULT_DIVIDER_COLOR = "var(--builder-page-background, #ffffff)"


def _divider_shape(mood: BrandMood | None, industry: str | None) -> str | None:
    """The shaped-edge to use for this site: the industry's signature shape when
    it has one, else the mood's (None → no divider)."""
    norm = (industry or "").strip().lower()
    if norm in _DIVIDER_SHAPE_BY_INDUSTRY:
        return _DIVIDER_SHAPE_BY_INDUSTRY[norm]
    return _DIVIDER_SHAPE_BY_MOOD.get(mood or "modern")


def _shaped_divider_plan(
    sections: list[BuilderElement], mood: BrandMood | None, industry: str | None = None
) -> list[tuple[int, str, int]]:
    """The shaped-divider boundaries a page will get: ``(carrier_index, side,
    revealed_index)`` where side is "bottom"/"top". Empty when the resolved
    shape (industry override, else mood) is None or there are fewer than two
    sections.

    Single source of truth for both apply_section_dividers and the modernize
    pass's "keep divider neighbours flat" rule, so the two never disagree about
    which sections border a shaped seam."""
    shape = _divider_shape(mood, industry)
    if shape is None or len(sections) < 2:
        return []

    plan: list[tuple[int, str, int]] = []
    hero_boundary: int | None = None
    if sections[0].name.startswith("Hero") and sections[0].divider is None:
        plan.append((0, "bottom", 1))
        hero_boundary = 0

    for i in range(len(sections) - 1, 0, -1):
        if not sections[i].name.startswith("CTA"):
            continue
        if sections[i].divider is not None:
            break
        if hero_boundary is not None and i - 1 == hero_boundary:
            # Same boundary the hero's bottom edge already claimed (a hero
            # immediately followed by a CTA) — don't double up on one seam.
            break
        plan.append((i, "top", i - 1))
        break
    return plan


def _shaped_divider_neighbours(
    sections: list[BuilderElement], mood: BrandMood | None, industry: str | None = None
) -> set[int]:
    """Indices of every section that will border a shaped divider (the section
    carrying the edge plus the one it reveals)."""
    out: set[int] = set()
    for carrier, _side, revealed in _shaped_divider_plan(sections, mood, industry):
        out.add(carrier)
        out.add(revealed)
    return out


def apply_section_dividers(
    sections: list[BuilderElement],
    mood: BrandMood | None,
    industry: str | None = None,
) -> None:
    """Add a shaped edge at the hero→content and content→CTA handoffs — the two
    highest-impact, lowest-noise places for one (see SECTION_DIVIDER system,
    section-divider.ts). Deliberately NOT applied at every section boundary:
    one per page reads as a designed accent, one at every boundary reads as
    wallpaper. No-op for moods with no shape (luxury/technical) or single-
    section pages. Never overwrites a divider a section already carries.

    A shaped seam must read against SOLID colour, so both sections bordering it
    are flattened (any decorative mesh/grain dropped) and the edge fill is the
    neighbour's plain background colour — no texture is carried onto the seam.
    Runs after modernize_sections (which already steers the texture accent away
    from these neighbours; this is the enforcing safety net). Mutates in place."""
    shape = _divider_shape(mood, industry)
    for carrier, side, revealed in _shaped_divider_plan(sections, mood, industry):
        # Shaped edges sit only against solid colour on both sides.
        _flatten_section_texture(sections[carrier])
        _flatten_section_texture(sections[revealed])
        edge = SectionDividerEdge(
            shape=shape, color=_section_edge_color(sections[revealed])
        )
        sections[carrier].divider = (
            SectionDivider(bottom=edge) if side == "bottom" else SectionDivider(top=edge)
        )


def _section_edge_color(section: BuilderElement) -> str:
    """The color a divider should be filled with to read as "revealing" this
    section: its own flat backgroundColor when it has one, else the page
    background token (covers photo/gradient-background sections, which the
    divider still renders correctly over per the SECTION_DIVIDER contract)."""
    styles = section.styles or {}
    bg = styles.get("backgroundColor")
    return bg if isinstance(bg, str) and bg else _DEFAULT_DIVIDER_COLOR


def _flatten_section_texture(section: BuilderElement) -> None:
    """Force a section to a solid (flat) background: drop any decorative mesh/grain
    layer (the backgroundImage + tiling props apply_section_decoration adds) and
    tag it flat. Photo/gradient fills and the solid backgroundColor are left
    untouched — only decoration, identified by a non-flat backgroundTexture tag,
    is removed. Mutates in place."""
    if getattr(section, "backgroundTexture", None) in (None, "flat"):
        section.backgroundTexture = "flat"
        return
    styles = dict(section.styles or {})
    for key in ("backgroundImage", "backgroundRepeat", "backgroundPosition", "backgroundSize"):
        styles.pop(key, None)
    section.styles = styles
    section.backgroundTexture = "flat"


def _children(el: BuilderElement) -> list[BuilderElement]:
    """A section element's child elements (``content`` holds either a leaf
    BuilderElementContent or a list of child BuilderElements)."""
    content = el.content
    return [c for c in content if isinstance(c, BuilderElement)] if isinstance(content, list) else []


def _subtree_has_real_photo(el: BuilderElement) -> bool:
    """True if this element or any descendant paints a genuine (non-`data:`) photo."""
    if _has_real_photo(el.styles or {}):
        return True
    return any(_subtree_has_real_photo(c) for c in _children(el))


def _subtree_has_gradient_texture(el: BuilderElement) -> bool:
    """True if this element or any descendant paints a gradient or `data:` texture."""
    styles = el.styles or {}
    for key in ("background", "backgroundImage"):
        v = styles.get(key)
        if isinstance(v, str) and ("gradient(" in v or "data:image" in v):
            return True
    return any(_subtree_has_gradient_texture(c) for c in _children(el))


def _is_pure_gradient_texture(section: BuilderElement) -> bool:
    """True if a section reads as a *pure* gradient/mesh/grain/abstract-texture
    band — i.e. it (or a descendant, e.g. a full-bleed CTA banner whose gradient
    sits on an inner wrapper) carries a gradient/texture fill and NO genuine photo.
    A real photo under a brand-overlay gradient does NOT count; photos are exempt
    from the one-texture-per-page budget."""
    if getattr(section, "backgroundTexture", None) not in (None, "flat"):
        return True  # decorative mesh/grain accent
    if _subtree_has_real_photo(section):
        return False
    return _subtree_has_gradient_texture(section)


def _flatten_to_solid_band(section: BuilderElement, theme: ThemeTokens) -> None:
    """Repaint a pure gradient/texture section as a solid on-brand band: strip the
    gradient/texture fills throughout its subtree (leaving any photo subtree alone)
    and give the section a flat surface colour with contrast-correct default text.
    Nested text that was tuned for the old gradient is renormalised against the new
    band by the later enforce_text_contrast pass. Mutates in place."""
    bg, text = band_colors(theme.palette, "light")  # calm surface band, dark text

    def strip(el: BuilderElement, *, is_root: bool) -> None:
        styles = dict(el.styles or {})
        if not _has_real_photo(styles):
            for key in (
                "background", "backgroundImage",
                "backgroundRepeat", "backgroundPosition", "backgroundSize",
            ):
                styles.pop(key, None)
        if is_root:
            styles["backgroundColor"] = bg
            styles["color"] = text
        el.styles = styles
        for child in _children(el):
            strip(child, is_root=False)

    strip(section, is_root=True)
    section.backgroundTexture = "flat"


def cap_gradient_textures(sections: list[BuilderElement], theme: ThemeTokens) -> None:
    """Enforce at most ONE pure gradient/texture section per page (a gradient
    hero/CTA, a mesh/grain accent, or an abstract-texture wash with no real photo).

    Keeps the FIRST such section as the page's single texture accent and flattens
    every later one to a solid on-brand band (`_flatten_to_solid_band`). Photos —
    full-bleed featured heroes, photo CTAs, colour-matched abstract washes that
    resolved a real Pexels image — are never touched. Runs after modernize_sections
    (which adds the lone mesh/grain accent) so it sees every texture source at once.
    Mutates in place."""
    seen = False
    for section in sections:
        if not _is_pure_gradient_texture(section):
            continue
        if not seen:
            seen = True
            continue
        _flatten_to_solid_band(section, theme)


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


def _cms_list_element(source: str) -> BuilderElement:
    """The builder's dynamic articlesList / eventsList element.

    Content and styles mirror the builder's own defaults exactly
    (builder/src/lib/cms-content-types.ts createDefaultCmsListContent and
    builder/src/lib/cms-list-element.ts) so the element edits identically to
    one dragged in by hand. The list renders the CMS collection at view time —
    entries come from the content-migration push, not from this schema.
    """
    is_events = source == "events"
    side_pad = "max(80px, calc((100% - var(--builder-page-max-width, 1280px)) / 2))"
    return BuilderElement(
        id=_uid(),
        name="Events List" if is_events else "Articles List",
        type="eventsList" if is_events else "articlesList",
        styles={
            "width": "100%",
            "paddingTop": "72px",
            "paddingRight": side_pad,
            "paddingBottom": "72px",
            "paddingLeft": side_pad,
        },
        responsiveStyles=ResponsiveStyles(
            mobile={
                "paddingTop": "52px",
                "paddingRight": "20px",
                "paddingBottom": "60px",
                "paddingLeft": "20px",
            },
            tablet={
                "paddingTop": "64px",
                "paddingRight": "32px",
                "paddingBottom": "64px",
                "paddingLeft": "32px",
            },
        ),
        content=BuilderElementContent(
            source=source,
            heading="Upcoming Events" if is_events else "Latest Articles",
            headingMode="static",
            archiveTitlePrefix="",
            archiveTitleSuffix="",
            showHeading=True,
            description="",
            showDescription=False,
            layout="grid",
            itemCount=6,
            filter={"mode": "all", "taxonomyType": None, "taxonomySlug": None},
            pagination={"enabled": True, "style": "numbered", "showSummary": True},
            categorySlug=None,
            selectionMode="auto",
            manualIds=[],
            showImage=True,
            showExcerpt=True,
            showMeta=True,
            showAuthor=False,
            showCategory=False,
        ),
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


def _first_content_section(page: GeneratedPage) -> BuilderElement | None:
    """The page's first real section, skipping a sub-page's leading breadcrumb —
    the section the header floats over. Mirrors webtree-public's
    findFirstNonBreadcrumbNode so overlay gating agrees across renderers."""
    for el in page.body_schema.elements:
        if getattr(el, "name", None) == "Breadcrumb":
            continue
        return el
    return None


def _extract_og_image_safe(elements: list[BuilderElement]) -> str | None:
    try:
        from app.services.seo import extract_og_image

        return extract_og_image(elements)
    except Exception:  # noqa: BLE001
        return None


def _breadcrumb_slug_chain(
    slug: str, title_map: dict[str, str]
) -> list[tuple[str, str]]:
    try:
        from app.services.seo import breadcrumb_slug_chain

        return breadcrumb_slug_chain(slug, title_map)
    except Exception:  # noqa: BLE001
        return [("", "Home")]


def _build_structured_safe(
    *,
    page_plan: "PagePlan",
    site_name: str,
    brand_name: str,
    logo_url: str | None,
    industry_category: str | None,
    contact: dict[str, str] | None,
    breadcrumb_slugs: list[tuple[str, str]],
) -> list[dict[str, Any]] | None:
    try:
        from app.services.seo import build_structured_data

        return build_structured_data(
            page_slug=page_plan.slug,
            page_title=page_plan.title,
            page_description=page_plan.description,
            page_type=page_plan.page_type,
            is_homepage=page_plan.is_homepage,
            site_name=site_name,
            brand_name=brand_name,
            logo_url=logo_url,
            industry_category=industry_category,
            contact=contact,
            blocks=page_plan.blocks,
            breadcrumb_slugs=breadcrumb_slugs,
        )
    except Exception:  # noqa: BLE001
        return None


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
    # Decorative mesh/grain is applied centrally in modernize_sections (the
    # single authority for the one-accent-per-page clean-UI policy), which runs
    # over the assembled tree — so this builder no longer decorates surface
    # bands itself (that produced texture on every surface section).

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


# Effective backdrop for contrast math on photo heroes: the legibility scrim
# rgba(15,23,42,0.55) composited over a mid-grey photo (#808080). Real photos
# vary, but text also carries a shadow; this target keeps every ink very light
# without demanding the impossible (even pure white only reaches ~4:1 against
# the scrim over a fully white photo).
_SCRIM_COMPOSITE_BG = "#424651"

# Childcare hero title inks: bright, cheerful, but all LIGHT enough to read on
# the neutral scrim with the hero's text-shadow — so the title is multi-coloured
# (butter/coral/sky/mint/lavender) instead of just white + the one theme hue.
# Order is (lead line, accent phrase, eyebrow); the rest cycle for extra lines.
_CHILDCARE_HERO_INKS: tuple[str, ...] = (
    "#FDE68A",  # butter — lead line
    "#FCA5A5",  # coral — accent phrase
    "#BAE6FD",  # sky — eyebrow
    "#A7F3D0",  # mint
    "#DDD6FE",  # lavender
)

# A neutral (not theme-tinted) scrim for childcare photo heroes: the brief wants
# the real photo colours, not a brand-colour wash. Light enough to keep the
# photo vivid, dark enough that the light-but-vivid title inks stay legible.
_CHILDCARE_HERO_SCRIM = "linear-gradient(rgba(15,23,42,0.28), rgba(15,23,42,0.48))"


def _split_headline(headline: str, accent: str | None) -> tuple[str, str] | None:
    """Split a hero headline into (lead, accent tail) when `accent` is a real,
    proper trailing phrase of `headline` (case-insensitive, whitespace-tolerant).
    Returns None — render the headline unchanged — for anything else, so a
    hallucinated or mid-sentence accent is always a safe no-op."""
    if not accent:
        return None
    head = " ".join((headline or "").split())
    tail = " ".join(accent.split())
    if not head or not tail:
        return None
    if not head.lower().endswith(tail.lower()):
        return None
    lead = head[: len(head) - len(tail)].rstrip()
    if not lead:  # accent == whole headline → nothing to contrast against
        return None
    return lead, head[len(head) - len(tail):]


def _midpoint_word_split(headline: str) -> tuple[str, str] | None:
    """Split `headline` into two halves at the space nearest the middle — used to
    two-tone a childcare title when no explicit accent phrase was given. None when
    there's no interior space to break on (a single word stays one colour)."""
    head = " ".join((headline or "").split())
    spaces = [i for i, ch in enumerate(head) if ch == " "]
    if not spaces:
        return None
    mid = len(head) / 2
    at = min(spaces, key=lambda s: abs(s - mid))
    return head[:at], head[at + 1:]


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


def _hero_accent_styles(base: dict, ctx: RenderContext, *, on_photo: bool) -> dict:
    """Styles for the highlighted headline line: brand accent colour lifted to
    AA contrast against the actual surface; luxury/editorial moods get the 2026
    italic display treatment (the display serif is already the heading font)."""
    surface = _SCRIM_COMPOSITE_BG if on_photo else ctx.theme.palette.background
    color = _accent_ink_for(surface, ctx.theme.palette.accent)
    styles = {**base, "color": color}
    if getattr(ctx.theme, "mood", None) in ("luxury", "editorial"):
        styles["fontStyle"] = "italic"
    return styles


def _headline_lines(
    block: HeroBlock,
    ctx: RenderContext,
    *,
    lead_styles: dict,
    mobile: dict | None,
    on_photo: bool,
    centered: bool = False,
) -> BuilderElement:
    """The hero headline: a single text element, or — when headline_accent
    validates — a two-line stack whose trailing phrase carries the accent
    colour. Lines stay separate block-level text elements so the builder can
    edit each as plain text (no HTML inside innerText — the editor would show
    literal tags)."""
    split = _split_headline(block.headline, block.headline_accent)
    if split is None:
        return _text(block.headline, name="Headline", styles=lead_styles, mobile=mobile)
    lead, accent = split
    accent_styles = _hero_accent_styles(lead_styles, ctx, on_photo=on_photo)
    container_styles: dict = {"flexDirection": "column", "gap": "0px", "width": "100%"}
    if centered:
        container_styles["alignItems"] = "center"
    return _container(
        [
            _text(lead, name="Headline", styles=lead_styles, mobile=mobile),
            _text(accent, name="Headline accent", styles=accent_styles, mobile=mobile),
        ],
        name="Headline group",
        styles=container_styles,
    )


def _photo_hero_ink(ctx: RenderContext) -> str:
    """Near-white headline ink for photo heroes, tinted by the brand instead of
    hardcoded #ffffff: the palette surface lifted until it clears the scrim
    backdrop by a wide margin (light-scheme surfaces usually already do)."""
    return _ensure_contrast_against(
        _SCRIM_COMPOSITE_BG, ctx.theme.palette.surface, min_ratio=8.0
    )


def _mix_hex(a: str, b: str, t: float = 0.5) -> str:
    ra, ga, ba = _hex_to_rgb(a)
    rb, gb, bb = _hex_to_rgb(b)
    return _rgb_to_hex(
        round(ra + (rb - ra) * t), round(ga + (gb - ga) * t), round(ba + (bb - ba) * t)
    )


def _hero_surface_for_template(template_id: str, ctx: RenderContext) -> str:
    """The colour the hero's text actually sits on, per template family — the
    contrast target for accent/ink corrections."""
    if template_id in _FULLBLEED_HERO_IDS:
        return _SCRIM_COMPOSITE_BG  # photo behind a dark scrim
    if template_id == "hero-gradient":
        # The catalog gradient runs secondary → primary; the centred heading
        # sits around the midpoint. Targeting primary alone over-lightens the
        # accent into near-white (losing the highlight).
        return _mix_hex(ctx.theme.palette.secondary, ctx.theme.palette.primary)
    return ctx.theme.palette.background  # split/editorial/minimal sit on page bg


def _find_text_by_inner(
    el: BuilderElement, needle: str
) -> tuple[BuilderElement, list[BuilderElement], int] | None:
    """Depth-first: the text element whose innerText equals `needle`, plus its
    parent list and index so it can be restyled or replaced in place."""
    content = el.content
    if isinstance(content, list):
        for i, child in enumerate(content):
            cc = child.content
            if (
                child.type == "text"
                and not isinstance(cc, list)
                and getattr(cc, "innerText", None) == needle
            ):
                return child, content, i
            found = _find_text_by_inner(child, needle)
            if found is not None:
                return found
    return None


def _apply_hero_typography(
    element: BuilderElement,
    block: HeroBlock,
    ctx: RenderContext,
    template_id: str,
) -> None:
    """Post-fill hero typography (catalogue path):

    - `headline_accent`: split the headline text node into a two-line stack —
      lead line keeps the template's styling, the trailing phrase becomes its
      own line in the brand accent (contrast-corrected against the template's
      real surface; italic display treatment for luxury/editorial moods).
      Separate block-level text elements, NOT HTML in innerText — the builder
      editor renders innerText literally.
    - Full-bleed photo heroes: headline ink becomes a brand-tinted near-white
      (was hardcoded white in the catalog) and the eyebrow carries the lifted
      brand accent, both AA against the scrim-composited backdrop.
    - Childcare photo heroes: the title is multi-coloured instead — butter lead,
      coral accent, sky eyebrow (all light, legible on the neutral scrim), and a
      plain headline is split at its midpoint so it still carries two colours.
    """
    surface = _hero_surface_for_template(template_id, ctx)
    on_photo = template_id in _FULLBLEED_HERO_IDS
    childcare = on_photo and ctx.industry == "childcare"

    lead_ink = _CHILDCARE_HERO_INKS[0] if childcare else _photo_hero_ink(ctx)
    eyebrow_ink = (
        _CHILDCARE_HERO_INKS[2]
        if childcare
        else _accent_ink_for(_SCRIM_COMPOSITE_BG, ctx.theme.palette.accent)
    )

    if on_photo:
        found = _find_text_by_inner(element, block.headline)
        if found is not None:
            node, _, _ = found
            node.styles = {**(node.styles or {}), "color": lead_ink}
        if block.eyebrow:
            found = _find_text_by_inner(element, block.eyebrow)
            if found is not None:
                node, _, _ = found
                node.styles = {**(node.styles or {}), "color": eyebrow_ink}

    split = _split_headline(block.headline, block.headline_accent)
    if split is None and childcare:
        # No explicit accent phrase → split the title so it still reads in two
        # cheerful colours rather than one flat butter line.
        split = _midpoint_word_split(block.headline)
    if split is None:
        return
    found = _find_text_by_inner(element, block.headline)
    if found is None:
        return
    node, parent, index = found
    lead, accent = split

    accent_color = (
        _CHILDCARE_HERO_INKS[1]
        if childcare
        else _accent_ink_for(surface, ctx.theme.palette.accent)
    )
    accent_styles: dict[str, Any] = {**(node.styles or {}), "color": accent_color}
    if getattr(ctx.theme, "mood", None) in ("luxury", "editorial"):
        accent_styles["fontStyle"] = "italic"

    lead_el = node.model_copy(deep=True)
    lead_el.id = str(uuid4())
    lead_el.content = BuilderElementContent(innerText=lead)

    accent_el = node.model_copy(deep=True)
    accent_el.id = str(uuid4())
    accent_el.name = f"{node.name} accent"
    accent_el.styles = accent_styles
    accent_el.content = BuilderElementContent(innerText=accent)

    group_styles: dict[str, Any] = {
        "flexDirection": "column",
        "gap": "0px",
        "width": "100%",
    }
    if (node.styles or {}).get("textAlign") == "center":
        group_styles["alignItems"] = "center"
    parent[index] = BuilderElement(
        id=str(uuid4()),
        name=f"{node.name} group",
        type="container",
        styles=group_styles,
        content=[lead_el, accent_el],
    )


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
        _headline_lines(
            block,
            ctx,
            lead_styles=s.heading_xl,
            mobile=s.heading_mobile,
            on_photo=False,
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
                    # The eyebrow is the accent voice on photo heroes — the
                    # brand accent as a hue-true pastel, AA against the scrim
                    # backdrop (was a flat near-white).
                    "color": _accent_ink_for(
                        _SCRIM_COMPOSITE_BG, ctx.theme.palette.accent
                    ),
                    "textAlign": "center",
                },
            )
        )
    inner.append(
        _headline_lines(
            block,
            ctx,
            lead_styles={
                **s.heading_xl,
                # Brand-tinted near-white instead of hardcoded #ffffff.
                "color": _photo_hero_ink(ctx),
                "textAlign": "center",
                "textShadow": "0 2px 16px rgba(0,0,0,0.35)",
            },
            mobile={"fontSize": "36px"},
            on_photo=True,
            centered=True,
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
                # Brand-tinted near-white over the CTA scrim (same ink as the
                # photo hero) instead of hardcoded #ffffff.
                "color": _photo_hero_ink(ctx),
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
                        # Scraped directory bios pack credentials / specialties /
                        # address as newline-separated facts — keep the breaks.
                        "whiteSpace": "pre-line",
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


def _abstract_theme_query(ctx: RenderContext) -> str:
    """A deterministic, theme-coloured abstract stock query (e.g. "abstract blue
    gradient mesh texture") for a hero's atmospheric background. The colour word
    is the brand primary's nearest Tailwind family, so the background reads
    on-theme; "gradient mesh" steers Pexels toward soft aurora/mesh abstracts
    rather than busy photographic textures."""
    family = color_family_name(ctx.theme.palette.primary)
    return f"abstract {family} gradient mesh texture"


# Moods whose audience (SaaS / fintech / tech for `modern`; engineering / B2B /
# dev-tools for `technical`) reads a split hero — a featured shot beside the copy
# — as on-brand. Every other mood leads with a full-bleed photo. The lean is soft:
# an explicit planner `layout="background"` still wins (see _apply_hero_photo_policy).
SPLIT_INCLINED_MOODS = frozenset({"modern", "technical"})


async def _apply_hero_directive(
    block: HeroBlock, ctx: RenderContext, directive: HeroDirective
) -> tuple[PhotoResult | None, PhotoResult | None]:
    """Resolve hero imagery for a hero-director directive (see hero_director.py).

    The directive fixes the template/layout per page; this resolves the imagery
    that layout needs, honouring source provenance:

      * imageless template -> no photo at all (don't burn a scraped image on a
        slot that never renders it);
      * full-bleed background -> resolve with slot_usage="background" so an
        image the SOURCE used as a CSS background wins the slot; degrade to the
        colour-matched abstract, then the gradient hero, when nothing genuine
        resolves;
      * split/editorial -> resolve the side image with slot_usage="inline"
        (a source CSS background must never be cropped into an <img>), plus the
        abstract wash when the directive asks for one. No genuine photo ->
        degrade to the imageless path so the template falls back gracefully.
    """
    abstract_query = _abstract_theme_query(ctx)
    primary_hex = ctx.theme.palette.primary
    block.layout = directive.layout

    if directive.template_id in IMAGELESS_HERO_IDS:
        block.image_query = None
        return None, None

    if directive.layout == "background":
        featured = await ctx.resolver.resolve(
            block.image_query,
            intent="hero",
            alt_fallback=block.image_alt or block.headline,
            prefer=ctx.page_images,
            slot_usage="background",
            pinned_url=block.image_url,
        )
        if featured.source in _GENUINE_PHOTO_SOURCES:
            if not (block.image_query or "").strip():
                block.image_query = block.image_alt or block.headline or "brand photo"
            return featured, None
        abstract = await ctx.resolver.resolve_abstract_bg(
            abstract_query, color_target_hex=primary_hex, intent="hero"
        )
        if abstract is not None:
            block.image_query = abstract_query
            return abstract, None
        block.layout = "split"  # compact gradient hero, not an empty full-bleed
        block.image_query = None
        return None, None

    featured = await ctx.resolver.resolve(
        block.image_query,
        intent="hero",
        alt_fallback=block.image_alt or block.headline,
        prefer=ctx.page_images,
        slot_usage="inline",
        pinned_url=block.image_url,
    )
    if featured.source not in _GENUINE_PHOTO_SOURCES:
        # No real side image -> let select_template fall back to an imageless
        # variant instead of framing a placeholder gradient as a photo.
        block.image_query = None
        return None, None
    if not (block.image_query or "").strip():
        block.image_query = block.image_alt or block.headline or "brand photo"
    washed_bg = (
        await ctx.resolver.resolve_abstract_bg(
            abstract_query, color_target_hex=primary_hex, intent="cta_bg"
        )
        if directive.wants_wash
        else None
    )
    return featured, washed_bg


async def _apply_hero_photo_policy(
    block: HeroBlock, ctx: RenderContext, directive: HeroDirective | None = None
) -> tuple[PhotoResult | None, PhotoResult | None]:
    """Resolve the hero imagery and normalise the block's layout.

    With a hero-director `directive` (the plan_to_site path), the per-page
    art direction wins — see _apply_hero_directive. Without one (legacy/direct
    callers), photos lead. Decided deterministically from the resolver's
    *actual* results:

      * genuine featured photo + SaaS/professional mood (and the planner didn't
        force a full-bleed) -> SPLIT hero: the photo fills the column AND a
        colour-matched abstract photo washes the section (second tuple element);
      * genuine featured photo, any other mood -> full-bleed FEATURED photo, no
        wash (the photo-forward default);
      * no featured photo -> full-bleed background using a colour-matched abstract
        photo (never split);
      * nothing resolves -> gradient hero (``image_query`` dropped).

    The abstract background is the Pexels candidate whose dominant colour sits
    closest to the theme primary (see ImageResolver.resolve_abstract_bg), so an
    abstract wash always reads on-brand rather than as a random texture.

    Returns ``(image_slot_photo, washed_bg_photo | None)``: the first is reused by
    the caller for the template's image slot (no second pick); the second, when
    set, is painted behind the split section by ``_apply_hero_washed_background``.
    """
    if directive is not None:
        return await _apply_hero_directive(block, ctx, directive)
    featured = await ctx.resolver.resolve(
        block.image_query,
        intent="hero",
        alt_fallback=block.image_alt or block.headline,
        prefer=ctx.page_images,
        pinned_url=block.image_url,
    )
    abstract_query = _abstract_theme_query(ctx)
    primary_hex = ctx.theme.palette.primary

    if featured.source in _GENUINE_PHOTO_SOURCES:
        if not (block.image_query or "").strip():
            block.image_query = block.image_alt or block.headline or "brand photo"
        mood = getattr(ctx.theme, "mood", None)
        # Split only for split-inclined moods, and only when the planner didn't
        # explicitly ask for a full-bleed photo (default layout is "split").
        if mood in SPLIT_INCLINED_MOODS and block.layout != "background":
            block.layout = "split"
            washed_bg = await ctx.resolver.resolve_abstract_bg(
                abstract_query, color_target_hex=primary_hex, intent="cta_bg"
            )
            return featured, washed_bg
        # Photo-forward: the featured photo is the full-bleed hero, no wash.
        block.layout = "background"
        return featured, None

    # No featured photo -> full-screen background using a colour-matched abstract.
    abstract = await ctx.resolver.resolve_abstract_bg(
        abstract_query, color_target_hex=primary_hex, intent="hero"
    )
    if abstract is not None:
        block.layout = "background"
        block.image_query = abstract_query
        return abstract, None

    # Nothing usable -> gradient hero (unchanged).
    block.image_query = None
    return featured, None


def _apply_hero_washed_background(
    element: BuilderElement, bg: PhotoResult, ctx: RenderContext
) -> None:
    """Paint a split hero's whole section with an abstract photo under a
    scheme-aware brand wash. Drops the template's static ``background`` shorthand,
    which would otherwise override ``backgroundImage``. Mutates in place."""
    styles = dict(element.styles or {})
    styles.pop("background", None)
    styles["backgroundImage"] = washed_photo_background(
        bg.url,
        scheme=getattr(ctx.theme, "color_scheme", "light"),
        surface_hex=ctx.theme.palette.surface,
        secondary_hex=ctx.theme.palette.secondary,
        primary_hex=ctx.theme.palette.primary,
    )
    styles["backgroundSize"] = "cover"
    styles["backgroundPosition"] = "center"
    styles["backgroundRepeat"] = "no-repeat"
    element.styles = styles


async def block_to_element(
    block: ContentBlock,
    ctx: RenderContext,
    *,
    is_homepage: bool = True,
    hero_scroll_target_kind: str | None = None,
    explicit_template_id: str | None = None,
    hero_directive: HeroDirective | None = None,
) -> BuilderElement:
    # Heroes are art-directed per page (see hero_director.plan_site_heroes);
    # direct callers without a directive fall back to the legacy site-wide
    # policy. The photo is resolved up front so the gradient-vs-photo choice can
    # key off the resolver's real source; the closure below reuses it (no
    # second pick).
    hero_photo: PhotoResult | None = None
    hero_washed_bg: PhotoResult | None = None
    if block.kind == "hero":
        hero_photo, hero_washed_bg = await _apply_hero_photo_policy(
            block, ctx, hero_directive
        )
        if hero_directive is not None:
            explicit_template_id = hero_directive.template_id

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
        variety_seed=ctx.variety_seed or None,
    )
    if mapped is not None:
        template, content = mapped
        intent = _IMAGE_INTENT.get(block.kind, "generic")
        # These kinds render their catalog image slots as in-flow <img>-style
        # visuals — a photo the source used as a CSS background must not be
        # cropped into them (CTA backgrounds resolve via intent="cta_bg" and
        # never touch the scraped pool).
        slot_usage: SlotUsage = (
            "inline"
            if block.kind in {"hero", "about", "features", "services", "team", "gallery"}
            else "any"
        )

        # A block-level scraped photo the LLM bound via image_ref. Safe to pin
        # for the block's single featured/background slot (about, cta) — kinds
        # whose templates carry per-item image slots (gallery, team) bind their
        # items in the content mapper instead, so this stays None for those.
        block_pinned = (
            getattr(block, "image_url", None) if block.kind in {"about", "cta"} else None
        )

        async def resolve_image(query: str) -> tuple[str, str | None]:
            # Reuse the hero photo already resolved by the policy above; for every
            # other slot, resolve normally.
            photo = (
                hero_photo
                if hero_photo is not None
                else await ctx.resolver.resolve(
                    query,
                    intent=intent,
                    alt_fallback=query,
                    prefer=ctx.page_images,
                    slot_usage=slot_usage,
                    pinned_url=block_pinned,
                )
            )
            # Capture the FIRST resolved image's band as the section's featured
            # image for the luminance pass (Phase 4b). Later images (e.g. grid
            # items) don't override the dominant one.
            if ctx.section_image_band is None and photo.band is not None:
                ctx.section_image_band = photo.band
            return photo.url, photo.avg_color

        element = await fill_template(
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
        # Split hero: wash the whole section with the abstract theme background.
        if hero_washed_bg is not None:
            _apply_hero_washed_background(element, hero_washed_bg, ctx)
        # Childcare: drop the brand-colour tint over the hero photo — the brief
        # wants the real image colours, not a theme wash. Swap the fill's
        # brand-tinted overlay for a neutral, lighter scrim (still enough to keep
        # the light multi-colour title legible).
        if (
            block.kind == "hero"
            and ctx.industry == "childcare"
            and template["id"] in _FULLBLEED_HERO_IDS
            and hero_photo is not None
        ):
            element.styles = {
                **(element.styles or {}),
                "backgroundImage": f"{_CHILDCARE_HERO_SCRIM}, url('{hero_photo.url}')",
            }
        # Hero typography pass: accent headline line + photo-hero ink retint.
        if block.kind == "hero":
            _apply_hero_typography(element, block, ctx, template_id=template["id"])
        # Full-bleed heroes carry a dark legibility overlay, so a transparent
        # header with white ink is readable over them. Mark the section: the
        # public renderer only runs the header's transparent phase on pages
        # whose FIRST section carries this (BuilderElement allows extra fields;
        # webtree-public reads it via getNodeField). Gate on a GENUINE photo
        # background — a hero that fell back to the gradient placeholder isn't a
        # dark full-bleed image, so the header must stay solid (white nav ink
        # would be unreadable over a light gradient).
        if (
            block.kind == "hero"
            and template["id"] in _FULLBLEED_HERO_IDS
            and hero_photo is not None
            and hero_photo.source in _GENUINE_PHOTO_SOURCES
        ):
            element.headerOverlaySafe = True
        return element

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


def _harvest_image_slots(plan: SitePlan) -> list[tuple[str | None, ImageIntent]]:
    """Best-effort (query, intent) slots the render will request from Pexels.

    Used to pre-warm the stock-photo cache concurrently before the serial
    render (see ImageResolver.prewarm_stock). Covers the explicit query fields
    on blocks and their nested items; the intents mirror the render's own
    resolve() calls so the pre-warmed cache keys match. Missing a slot is
    harmless — it just resolves live during render, as today.
    """
    slots: list[tuple[str | None, ImageIntent]] = []
    for page in plan.pages:
        for block in page.blocks:
            block_q = getattr(block, "image_query", None)
            if block_q:
                slots.append((block_q, _IMAGE_INTENT.get(block.kind, "generic")))
            bg_q = getattr(block, "background_query", None)
            if bg_q:
                slots.append((bg_q, "cta_bg"))
            for items_attr in ("items", "members"):
                for item in getattr(block, items_attr, None) or []:
                    item_q = getattr(item, "image_query", None)
                    if item_q:
                        slots.append((item_q, "generic"))
                    avatar_q = getattr(item, "photo_query", None) or getattr(
                        item, "avatar_query", None
                    )
                    if avatar_q:
                        slots.append((avatar_q, "avatar"))
    return slots


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
    reserved_image_urls: set[str] | None = None,
    header_override: HeaderArchetype | None = None,
    footer_override: FooterArchetype | None = None,
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
        from app.services.diversity import recent_choices

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
                None,
                effective_brand.color_scheme,
                effective_brand.logo_is_light,
                industry=plan.industry_category,
            ),
            # Diversity: steer the curated pick off palettes recent sites used
            # (rotates within the fit group only; fail-open empty set).
            avoid_palettes=await recent_choices(
                "palette", site_key=effective_brand.name or plan.site_name
            ),
        )

    # Design manifest: the recorded chrome/decision layer composed BEFORE any
    # page is built (see services/design_director.py). Disabled → the default
    # manifest, which is exactly the legacy classic-header/mega-footer chrome.
    # The kill switch is absolute: when the engine is off, an explicit archetype
    # override is ignored too (no archetype rendering happens at all).
    if settings.design_engine_enabled:
        manifest = await compose_design_manifest(
            brand_name=effective_brand.name or plan.site_name,
            mood=effective_brand.mood,
            industry=plan.industry_category,
            color_scheme=getattr(theme, "color_scheme", "light"),
            header_override=header_override,
            footer_override=footer_override,
        )
    else:
        manifest = DesignManifest(seed=effective_brand.name or plan.site_name)

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
    # Photos already bound to specific sections by the image_ref pass must not
    # be re-picked by free ranking for other slots (pinned resolution itself
    # bypasses the used-set, so the owning slot still renders them).
    if reserved_image_urls:
        resolver.mark_used(reserved_image_urls)

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

    # Two independent warm-ups, run concurrently (they share no data):
    # - Stock prewarm fills the per-query Pexels cache for every image slot the
    #   (serial) render below will request, so image resolution stops paying a
    #   network round-trip per slot. Output-identical — selection/dedup/order
    #   are unchanged; this only pre-fetches into the shared per-query cache.
    # - The design-brain pass picks a template variant per section so sites of
    #   the same mood/industry stop converging on one identical layout. ONE
    #   call for the whole site; a failed/disabled call returns an empty
    #   recipe, so every per-page lookup below yields None and selection falls
    #   back to the deterministic mood-ordered choice.
    async def _prewarm() -> None:
        with stage("stock_prewarm"):
            await resolver.prewarm_stock(_harvest_image_slots(plan))

    async def _design_recipe():
        with stage("design_recipe"):
            return await generate_site_design_recipe(
                mood=effective_brand.mood,
                industry=plan.industry_category,
                pages=[[b.kind for b in p.blocks] for p in plan.pages],
            )

    _, site_design_recipe = await asyncio.gather(_prewarm(), _design_recipe())

    # Heroes are art-directed deterministically, per page, OUTSIDE the design
    # brain (which used to force one identical hero on every page): homepage
    # leads with the mood's signature treatment — full-bleed when the source
    # itself led with a CSS background image — and interiors rotate within a
    # mood-approved set. See services/hero_director.py.
    hero_directives = plan_site_heroes(
        plan.pages,
        mood=effective_brand.mood,
        industry=plan.industry_category,
        has_source_background=resolver.strongest_source_background() is not None,
        seed=effective_brand.name or plan.site_name,
    )

    # Fold the remaining design decisions into the manifest so it is the ONE
    # complete audit record: theme language, per-page hero art direction, and
    # the design-brain's section picks. Recording only — the deciders above
    # stay the source of the choices; the manifest is where they are explained.
    if settings.design_engine_enabled:
        manifest.decisions.append(
            DesignDecision(
                area="palette",
                # Curated slug when a curated path was taken (real, steerable
                # identity for the diversity history); primary hex otherwise.
                choice=getattr(theme, "palette_slug", None) or theme.palette.primary,
                rationale=(
                    "build_theme palette (design-language LLM pick upstream when "
                    "enabled, else deterministic industry/hue/seed selection; "
                    "curated picks steer off recently-used palettes)"
                ),
                confidence=0.7,
            )
        )
        manifest.decisions.append(
            DesignDecision(
                area="typography",
                choice=theme.typography.heading_font.split(",")[0].strip("'\" "),
                rationale="build_theme font pairing (same upstream pass as palette)",
                confidence=0.7,
            )
        )
        for _slug, _directive in hero_directives.items():
            _page_plan = next((p for p in plan.pages if p.slug == _slug), None)
            _is_home = bool(_page_plan and (_page_plan.is_homepage or _page_plan.page_type == "home"))
            manifest.decisions.append(
                DesignDecision(
                    # "hero-homepage" is a diversity-recorded area (see
                    # design_director._RECORDED_AREAS); interior heroes are
                    # audit-only.
                    area="hero-homepage" if _is_home else f"hero:{_slug}",
                    choice=_directive.template_id,
                    rationale=(
                        "hero director site-wide full-bleed policy"
                        if settings.hero_fullbleed_all_pages
                        else "hero director per-page rotation (mood/industry spec)"
                    ),
                    confidence=0.8,
                )
            )
        for _choice in site_design_recipe.sections:
            if _choice.template_id:
                manifest.decisions.append(
                    DesignDecision(
                        area=f"section:{_choice.page_index}:{_choice.section_index}",
                        choice=_choice.template_id,
                        rationale="design-brain LLM pick (feasibility-gated downstream)",
                        confidence=0.65,
                    )
                )

    # Build each page's body
    pages: list[GeneratedPage] = []
    _render_start = perf_counter()
    for page_index, page_plan in enumerate(plan.pages):
        # Each page resets section_index so rotation starts fresh per page.
        ctx = RenderContext(
            theme=theme,
            resolver=resolver,
            styles=styles,
            industry=plan.industry_category,
            current_page_slug=page_plan.slug,
            current_parent_slug=page_plan.parent_slug,
            children_by_parent=children_by_parent,
            page_title_by_slug=page_title_by_slug,
            page_images=(page_images or {}).get(page_plan.slug, []),
            variety_seed=manifest.seed if settings.design_engine_enabled else "",
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
        # This page's slice of the batched site recipe (computed once above).
        design_recipe: DesignRecipe = site_design_recipe.recipe_for(page_index)
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
                hero_directive=(
                    hero_directives.get(page_plan.slug)
                    if block.kind == "hero"
                    else None
                ),
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
        # Story pages: alternate the image side of consecutive about splits so
        # the photos zigzag down the page instead of stacking on one side.
        apply_about_zigzag(elements)
        apply_luminance_rhythm(elements, visual_inputs, theme.palette)
        # Legacy color-blocking rhythm: alternates the remaining (non-policy)
        # plain sections between page-bg and surface tint. Sections the luminance
        # pass already filled carry their own backgroundColor and are skipped.
        apply_section_rhythm(elements)
        # 2025/26 modernization: fluid type, card depth/glass, atmospheric
        # surface backgrounds — applied per-mood over the assembled sections.
        # Runs BEFORE apply_section_dividers and already steers the texture
        # accent away from sections that will border a shaped divider.
        modernize_sections(elements, theme, plan.industry_category)
        # One gradient/texture per page: keep the first pure gradient/mesh/grain/
        # abstract-texture section, flatten the rest to solid on-brand bands. Runs
        # after modernize_sections (the lone mesh/grain accent is now present) and
        # before dividers so a shaped seam reads against the final solid colour.
        cap_gradient_textures(elements, theme)
        # Childcare: recolour flat sections through the cheerful pastel set
        # (cream/sky/mint/peach/lavender/butter) so the page reads multi-coloured
        # rather than one hue plus a dark band. Runs after the rhythm/modernize
        # passes (overrides their flat colours) and before dividers (so seam
        # fills read the final pastels). Photo/gradient sections are untouched.
        if (
            plan.industry_category == "childcare"
            and getattr(theme, "color_scheme", "light") != "dark"
        ):
            apply_childcare_pastel_rhythm(elements, theme.palette.text)
            # Each pastel section's title gets its own vivid colour (runs after
            # the pastel pass so its dark-ink recolour doesn't overwrite them).
            apply_childcare_heading_colors(elements)
        # Shaped section dividers — fill colour reads each neighbour's solid
        # backgroundColor; both neighbours are flattened so a shaped seam always
        # sits against solid colour (never a mesh/grain band).
        apply_section_dividers(elements, effective_brand.mood, plan.industry_category)
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
        # Blog / events pages render their content dynamically: the builder's
        # articlesList / eventsList element lists the CMS collection. Appended
        # after every styling pass so the rhythm/motion passes never restyle it.
        if page_plan.page_type == "blog":
            elements.append(_cms_list_element("articles"))
        elif page_plan.page_type == "events":
            elements.append(_cms_list_element("events"))
        og_image = _extract_og_image_safe(elements) if settings.seo_enabled else None
        structured = None
        if settings.seo_enabled and settings.seo_structured_data_enabled:
            bc_chain = _breadcrumb_slug_chain(page_plan.slug, page_title_by_slug)
            structured = _build_structured_safe(
                page_plan=page_plan,
                site_name=plan.site_name,
                brand_name=effective_brand.name or plan.site_name,
                logo_url=effective_brand.logo_url,
                industry_category=plan.industry_category,
                contact=contact,
                breadcrumb_slugs=bc_chain,
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
                    canonical=f"/{page_plan.slug}" if page_plan.slug else "/" if settings.seo_enabled else None,
                    ogTitle=page_plan.seo_title,
                    ogDescription=page_plan.seo_description,
                    ogImage=og_image,
                    twitterCard=("summary_large_image" if og_image else "summary") if settings.seo_enabled else None,
                    structuredData=structured,
                ),
                parent_slug=page_plan.parent_slug,
                nav_rank=page_plan.nav_rank,
                from_source=page_plan.from_source,
            )
        )
    log_elapsed("page_render", _render_start)

    page_tree = _build_page_tree(pages)
    nav_items = _nav_items_from_pages(pages)
    footer_nav = list(nav_items)
    if extra_footer_nav:
        footer_nav.extend(extra_footer_nav)
    primary_cta = ("Get in touch", "#contact")

    # Transparent floating header (white nav ink), solidifying on scroll —
    # default on; the setting is a kill switch. It engages ONLY over a genuinely
    # dark, full-bleed hero: block_to_element stamps `headerOverlaySafe` on such
    # heroes (their baked-in dark scrim keeps white ink readable). Keying the
    # site-wide flag off the homepage's ACTUAL rendered first section — not the
    # pre-render hero directive — means a hero that fell back to a light layout
    # (e.g. no photo resolved) keeps the SOLID header with its dark theme ink
    # instead of unreadable white-on-light. Rule: white nav ⇒ dark hero; dark
    # nav ⇒ light hero. Interior pages are gated per-page by each renderer via
    # the same marker (webtree-public lib/headerOverlay; builder Editor.tsx).
    home_page = next((p for p in pages if p.is_homepage), None)
    home_first_section = _first_content_section(home_page) if home_page else None
    header_overlay = bool(
        settings.header_overlay_enabled
        and home_first_section is not None
        and getattr(home_first_section, "headerOverlaySafe", False) is True
        # All current archetypes can overlay; the flag stays gated per-archetype
        # for future non-overlay chrome. Self-chrome archetypes (floating pill)
        # overlay WITHOUT the transparent phase: their nodes carry no ink
        # markers and the layout payload emits revealBackgroundOnScroll: false
        # (see SELF_CHROME_HEADERS + menu_builder.build_layout_payload).
        and manifest.header_overlay_capable
    )
    header = build_header(
        effective_brand,
        theme,
        nav_items=nav_items,
        primary_cta=primary_cta,
        page_tree=page_tree,
        overlay=header_overlay,
        industry=plan.industry_category,
        archetype=manifest.header_archetype,
        social_links=social_links,
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
        archetype=manifest.footer_archetype,
        primary_cta=primary_cta,
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

    # WhatsApp chat links render as the first-class green pill (body sections
    # only — header/footer chrome keeps its own compact link treatments). After
    # the contrast pass, which must not retint the white-on-green ink.
    for _page in pages:
        style_whatsapp_links(_page.body_schema.elements)

    # The manifest rides builderStyles into the CMS (flexible JSON — the same
    # channel googleFonts/brandMood use, no migration). The builder's
    # normalizeBuilderStyles carries `designManifest` through edit/save
    # round-trips as an opaque record; both renderers ignore it.
    builder_styles = theme.to_builder_styles()
    if settings.design_engine_enabled:
        builder_styles["designManifest"] = manifest.model_dump()

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
        builder_styles=builder_styles,
        google_fonts=theme.typography.google_fonts,
        brand=effective_brand,
        design_manifest=manifest.model_dump(),
        header_schema=header,
        footer_schema=footer,
        header_overlay=header_overlay,
    )

    # Feed the diversity history so the NEXT generation rotates away from this
    # site's chrome picks (fail-open; see services/diversity.py).
    if settings.design_engine_enabled:
        await record_manifest_choices(manifest)

    # Advisory UX/accessibility + SEO audit. Logged only; never mutates or
    # blocks generation.
    try:
        import logging

        from app.services.ux_audit import audit_seo, audit_site, summarize

        findings = audit_site(site)
        if settings.seo_audit_enabled:
            findings += audit_seo(site)
        if findings:
            logging.getLogger(__name__).info(
                "UX+SEO audit: %d finding(s) %s", len(findings), summarize(findings)
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
