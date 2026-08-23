"""
Design schemes — the style-pack layer that keeps two same-mood sites apart.

The design engine already varied *chrome* (header/footer archetype) and *type*
(font pairing) per brand. Everything else that gives a page its geometry,
density and rhythm was a single hardcoded value per mood — often a single value
globally: the literal pixels in ``style_tokens.make_style_tokens`` were
identical on every site this generator has ever produced, and
``hero_fullbleed_all_pages`` collapsed nine hero templates down to one. Sites in
the same (mood, industry) cell therefore read as one template in different
colours.

A ``DesignScheme`` is a named bundle of those variables. Selection reuses the
machinery the chrome archetypes already use — fit list, seeded rotation,
diversity history (see ``services/diversity.py``) — so one brand regenerates
identically while a batch of ten diverges.

Two rules keep this maintainable:

* **This module is the only home for a scheme's values.** The per-mood tables it
  layers over (``theme.MOOD_SPECS``, ``section_content._MOOD_LAYOUT_PREFERENCE``,
  ``schema_builder._DIVIDER_SHAPE_BY_MOOD``, ``hero_director._MOOD_SPECS``,
  ``brand.MOOD_MOTION_INTENSITY``) are untouched and stay the fallback. Every
  field below defers with ``None`` / ``"inherit"`` / an empty tuple, which is
  what makes ``design_schemes_enabled=False`` a true no-op rather than a second
  code path.
* **Scales, not absolutes.** ``padding_scale`` and friends multiply the value a
  catalog template already chose. The catalog's root paddings vary on purpose
  (72px on 30 entries, 104px on 10, 128px, 140px…); replacing them with one
  number would flatten real composition. Scaling moves the whole page's density
  while preserving each template's intent.

Every value a scheme can express reaches the page through the existing wire:
inline ``BuilderElement.styles`` (an arbitrary CSS map) or a ``ThemeTokens``
field that is already emitted. **No renderer anywhere needs to learn anything.**
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.models.brand import BrandMood, MotionIntensity
from app.models.design_manifest import FooterArchetype, HeaderArchetype
from app.services.diversity import pick_diverse

# --- vocabularies -------------------------------------------------------------

# How a card surface is drawn. "inherit" keeps today's behaviour: a hairline
# border plus the theme's shadow scale, with frosted glass when the mood asks
# for it (theme.MoodSpec.use_glass).
CardTreatment = Literal[
    "inherit",
    "bordered",   # hairline border, no shadow — flat and quiet
    "elevated",   # no border, shadow carries the lift
    "flat",       # neither: the card is a tinted rectangle
    "glass",      # frosted pane (backdrop-filter)
    "outline",    # heavier 2px border in the brand ink, no shadow
]

# How the small label above a section heading is set. All six are pure style —
# none of them changes what the label says.
EyebrowTreatment = Literal[
    "inherit",
    "caps-tracked",  # today: 13px, 0.14em tracking, uppercase, accent ink
    "sentence",      # sentence case, normal tracking, muted ink
    "rule",          # short accent rule to the left of the label
    "chip",          # filled pill in a tint of the accent
    "underline",     # accent hairline beneath the label
    "hidden",        # the section leads with its heading alone
]

# Which heroes a site uses. "inherit" keeps the current global policy
# (settings.hero_fullbleed_all_pages). The floating-pill header overrides all
# of these — see hero_director.plan_site_heroes(force_background=…).
HeroPolicy = Literal[
    "inherit",
    "fullbleed-all",   # every page opens on a full-bleed photo hero
    "hero-led",        # homepage full-bleed, interiors take the mood rotation
    "editorial-mix",   # the mood rotation everywhere, homepage included
]

ShadowScale = Literal["soft", "elevated", "dramatic"]
BackgroundStrategy = Literal["flat", "mesh", "grain", "mesh+grain"]
SectionSlot = Literal["background", "surface", "primary"]
# The four shapes webtree-public/lib/sectionDivider.ts knows. A fifth would need
# renderer changes in all three repos, so the vocabulary is closed here too.
DividerShape = Literal["slant", "curve", "wave", "peak"]


# --- what the renderers reserve for themselves ---------------------------------
#
# webtree-public/lib/responsiveRuntime.ts:24-46 pins `gap` and card `minHeight`
# on these node NAMES via a generated stylesheet, with `!important`, at desktop
# and tablet (mobile returns {}). An inline value we write for the same property
# on the same node loses — silently, and only on the published site, since the
# preview and builder apply the same table. So the density pass must not touch
# these two properties on these names; it owns section VERTICAL padding, the
# measure, and card padding/radius/border/shadow instead, none of which that
# table mentions. Mirror kept in lockstep with the TS sets of the same names.
RENDERER_PINNED_GAP_NAMES: frozenset[str] = frozenset(
    {
        # GENERATED_SECTION_SHELL_NAMES
        "Hero - Modern Split",
        "About - Story Split",
        "Features - Card Grid",
        "Services - Offer Grid",
        "Testimonials - Quote Grid",
        "CTA - Banner",
        "FAQ - Stacked",
        "Contact - Split Form",
        # GENERATED_GRID_NAMES
        "Hero Columns",
        "About Columns",
        "Feature Grid",
        "Services Grid",
        "Testimonial Grid",
        "Contact Columns",
        # individually-named nodes
        "Section Intro",
        "About Highlights",
        "FAQ Stack",
    }
)

RENDERER_PINNED_MIN_HEIGHT_NAMES: frozenset[str] = frozenset(
    {"About Highlight", "Feature Card", "Service Card", "Testimonial Card"}
)


@dataclass(frozen=True)
class DesignScheme:
    """One visual language. Every field defers to the pre-scheme behaviour.

    ``moods`` and ``industries`` are hard gates in the section catalog's own
    vocabulary — empty means *neutral wildcard*, not *matches nothing*. A gate
    written in the wrong vocabulary is a silent off switch rather than a gate
    (see the ``profile-centered`` incident in CLAUDE.md), so
    ``test_design_schemes`` fails on any value that is not a real ``BrandMood``
    or ``IndustryCategory``.

    ``industry_affinity`` is the soft half: it never excludes, it only ranks a
    scheme ahead of the neutral ones for industries it was designed around. Hard
    industry gates are reserved for the few schemes that would be actively wrong
    elsewhere, because a gate removes candidates from a cell and cells need at
    least three.
    """

    slug: str
    label: str
    rationale: str

    moods: frozenset[BrandMood] = frozenset()
    industries: frozenset[str] = frozenset()
    industry_affinity: frozenset[str] = frozenset()

    # --- shape & surface ------------------------------------------------------
    radius_scale: float = 1.0
    card_treatment: CardTreatment = "inherit"
    shadow_scale: ShadowScale | None = None
    background_strategy: BackgroundStrategy | None = None
    use_glass: bool | None = None
    # "inherit" defers to _DIVIDER_SHAPE_BY_MOOD; None means "no divider".
    divider_shape: DividerShape | None | Literal["inherit"] = "inherit"
    divider_height: int | None = None  # clamped 8..400 by the renderers

    # --- spacing & scale ------------------------------------------------------
    padding_scale: float = 1.0
    card_padding_scale: float = 1.0
    container_max_width: int = 1280
    type_scale_ratio: float | None = None
    heading_weight: int | None = None
    heading_tracking: str | None = None
    eyebrow_treatment: EyebrowTreatment = "inherit"

    # --- layout composition ----------------------------------------------------
    layout_bias: tuple[str, ...] = ()
    hero_policy: HeroPolicy = "inherit"
    header_affinity: tuple[HeaderArchetype, ...] = ()
    footer_affinity: tuple[FooterArchetype, ...] = ()
    motion_intensity: MotionIntensity | None = None

    # --- colour expression -----------------------------------------------------
    section_rotation: tuple[SectionSlot, ...] = ()
    inverted_cta: bool | None = None

    @property
    def is_neutral(self) -> bool:
        """True for the identity scheme — every consumer takes its fallback."""
        return self.slug == NEUTRAL_SLUG


NEUTRAL_SLUG = "inherit"

# The identity scheme. Returned whenever schemes are disabled, a slug does not
# resolve, or a theme predates the feature. Deferring on every axis is what
# makes the kill switch byte-identical rather than merely "close".
NEUTRAL_SCHEME = DesignScheme(
    slug=NEUTRAL_SLUG,
    label="Inherit",
    rationale="design schemes disabled; every axis defers to the per-mood tables",
)


# --- the authored schemes -------------------------------------------------------
#
# Ordered best-first within the file; `candidates_for` preserves this order after
# its affinity sort, so declaration order is the tiebreaker taste lives in.
#
# Every scheme is a coherent brief, not a random point in the parameter space:
# density, shape, type and layout have to argue for the same thing or the page
# reads as noise. The comment on each says what it is FOR.

DESIGN_SCHEMES: tuple[DesignScheme, ...] = (
    DesignScheme(
        slug="swiss-quiet",
        label="Swiss Quiet",
        rationale=(
            "hard corners, hairline rules and generous air — the page is set, "
            "not decorated; credibility comes from restraint"
        ),
        moods=frozenset({"luxury", "technical", "editorial"}),
        radius_scale=0.25,
        card_treatment="bordered",
        shadow_scale="soft",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=1.25,
        card_padding_scale=1.15,
        container_max_width=1180,
        heading_weight=600,
        heading_tracking="-0.015em",
        eyebrow_treatment="rule",
        layout_bias=("minimal", "centered", "grid"),
        header_affinity=("minimal-line", "centered-stack"),
        footer_affinity=("minimal-centered", "editorial"),
        motion_intensity="subtle",
    ),
    DesignScheme(
        slug="editorial-broadsheet",
        label="Editorial Broadsheet",
        rationale=(
            "oversized display type on a narrow measure with a paper grain — "
            "reads as a feature article, so the copy carries the sell"
        ),
        moods=frozenset({"editorial", "luxury"}),
        industry_affinity=frozenset({"agency", "personal", "nonprofit"}),
        radius_scale=0.35,
        card_treatment="flat",
        shadow_scale="soft",
        background_strategy="grain",
        use_glass=False,
        divider_shape=None,
        padding_scale=1.3,
        card_padding_scale=1.1,
        container_max_width=1120,
        type_scale_ratio=1.46,
        heading_weight=600,
        heading_tracking="-0.025em",
        eyebrow_treatment="sentence",
        layout_bias=("editorial", "asymmetric", "narrative", "single"),
        hero_policy="editorial-mix",
        header_affinity=("minimal-line", "centered-stack"),
        footer_affinity=("editorial", "minimal-centered"),
        motion_intensity="subtle",
    ),
    DesignScheme(
        slug="soft-warm",
        label="Soft & Warm",
        rationale=(
            "big radii, lifted cards and a curved seam between bands — an "
            "approachable surface for brands people choose with their gut"
        ),
        moods=frozenset({"friendly", "playful"}),
        industry_affinity=frozenset({"childcare", "restaurant", "nonprofit"}),
        radius_scale=1.5,
        card_treatment="elevated",
        shadow_scale="elevated",
        background_strategy="flat",
        use_glass=False,
        divider_shape="wave",
        divider_height=64,
        padding_scale=1.1,
        card_padding_scale=1.15,
        container_max_width=1240,
        heading_weight=700,
        heading_tracking="-0.015em",
        eyebrow_treatment="chip",
        layout_bias=("grid", "split", "steps"),
        footer_affinity=("cta-banner", "mega"),
        motion_intensity="balanced",
    ),
    DesignScheme(
        slug="glass-lab",
        label="Glass Lab",
        rationale=(
            "frosted panes over an aurora mesh with modular bento blocks — the "
            "current product-site idiom, for brands selling software"
        ),
        moods=frozenset({"modern", "technical"}),
        industry_affinity=frozenset({"saas", "consultancy"}),
        radius_scale=1.15,
        card_treatment="glass",
        shadow_scale="elevated",
        background_strategy="mesh",
        use_glass=True,
        divider_shape=None,
        padding_scale=1.0,
        card_padding_scale=1.0,
        container_max_width=1280,
        type_scale_ratio=1.32,
        eyebrow_treatment="chip",
        layout_bias=("bento", "split", "grid"),
        header_affinity=("glass-blur", "floating-pill"),
        footer_affinity=("cta-banner", "minimal-centered"),
        motion_intensity="balanced",
    ),
    DesignScheme(
        slug="bold-block",
        label="Bold Block",
        rationale=(
            "full-strength colour bands, an inverted CTA and a hard peak seam — "
            "high-contrast blocks that push the eye down the page to the ask"
        ),
        moods=frozenset({"playful", "modern", "friendly"}),
        radius_scale=0.6,
        card_treatment="flat",
        # Soft, not dramatic: this scheme's cards carry no surface at all, so a
        # deep shadow scale would be a value it can never spend — and the few
        # places it IS spent (glass panes, the one textured band) should stay
        # quiet under bands this strong.
        shadow_scale="soft",
        background_strategy="flat",
        use_glass=False,
        divider_shape="peak",
        divider_height=72,
        padding_scale=1.15,
        card_padding_scale=1.2,
        container_max_width=1280,
        type_scale_ratio=1.42,
        heading_weight=800,
        heading_tracking="-0.03em",
        eyebrow_treatment="chip",
        layout_bias=("banner", "background", "grid", "bento"),
        section_rotation=("background", "surface", "background", "surface"),
        inverted_cta=True,
        footer_affinity=("cta-banner", "mega"),
        motion_intensity="expressive",
    ),
    DesignScheme(
        slug="dense-technical",
        label="Dense Technical",
        rationale=(
            "compact rhythm, flat surfaces and a wide measure — more substance "
            "above the fold for an audience that came to compare, not to browse"
        ),
        moods=frozenset({"technical", "modern"}),
        industry_affinity=frozenset({"saas", "consultancy", "professional-services"}),
        radius_scale=0.5,
        card_treatment="bordered",
        shadow_scale="soft",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=0.76,
        card_padding_scale=0.85,
        container_max_width=1360,
        type_scale_ratio=1.2,
        heading_weight=650,
        heading_tracking="-0.01em",
        eyebrow_treatment="rule",
        layout_bias=("grid", "minimal", "split"),
        hero_policy="hero-led",
        header_affinity=("minimal-line", "classic"),
        footer_affinity=("minimal-centered", "mega"),
        motion_intensity="subtle",
    ),
    DesignScheme(
        slug="airy-luxe",
        label="Airy Luxe",
        rationale=(
            "very generous air, near-square corners and no shadow at all — "
            "space is the luxury signal; nothing on the page is in a hurry"
        ),
        moods=frozenset({"luxury", "editorial", "modern"}),
        radius_scale=0.3,
        card_treatment="flat",
        shadow_scale="soft",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=1.55,
        card_padding_scale=1.25,
        container_max_width=1160,
        type_scale_ratio=1.38,
        heading_weight=500,
        heading_tracking="-0.02em",
        eyebrow_treatment="sentence",
        layout_bias=("centered", "minimal", "editorial", "single"),
        header_affinity=("centered-stack", "minimal-line"),
        footer_affinity=("minimal-centered", "editorial"),
        motion_intensity="subtle",
    ),
    DesignScheme(
        slug="bento-modular",
        label="Bento Modular",
        rationale=(
            "modular tiles of uneven weight throughout — one composition that "
            "shows range without needing a different layout per section"
        ),
        moods=frozenset({"modern", "playful", "technical"}),
        industry_affinity=frozenset({"saas", "agency", "ecommerce"}),
        radius_scale=1.25,
        card_treatment="elevated",
        shadow_scale="elevated",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=0.95,
        card_padding_scale=1.0,
        container_max_width=1320,
        eyebrow_treatment="chip",
        layout_bias=("bento", "grid", "split"),
        header_affinity=("glass-blur", "floating-pill"),
        motion_intensity="balanced",
    ),
    DesignScheme(
        slug="poster-display",
        label="Poster Display",
        rationale=(
            "type at poster scale with the chrome reduced to a hairline — the "
            "headline is the artwork, so everything else gets out of its way"
        ),
        moods=frozenset({"editorial", "luxury", "playful", "modern"}),
        industry_affinity=frozenset({"agency", "personal"}),
        radius_scale=0.2,
        card_treatment="flat",
        shadow_scale="soft",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=1.35,
        card_padding_scale=1.1,
        container_max_width=1240,
        type_scale_ratio=1.55,
        heading_weight=700,
        heading_tracking="-0.035em",
        eyebrow_treatment="underline",
        layout_bias=("asymmetric", "editorial", "minimal", "single"),
        hero_policy="editorial-mix",
        header_affinity=("minimal-line", "centered-stack"),
        footer_affinity=("editorial", "minimal-centered"),
        motion_intensity="balanced",
    ),
    DesignScheme(
        slug="calm-clinical",
        label="Calm Clinical",
        rationale=(
            "high whitespace, no texture and no seams — an unhurried, "
            "low-stimulus page for a decision that involves trust or care"
        ),
        moods=frozenset({"friendly", "modern", "technical"}),
        industry_affinity=frozenset(
            {"professional-services", "nonprofit", "childcare", "consultancy"}
        ),
        radius_scale=0.9,
        card_treatment="bordered",
        shadow_scale="soft",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=1.3,
        card_padding_scale=1.2,
        container_max_width=1200,
        type_scale_ratio=1.22,
        heading_weight=600,
        heading_tracking="-0.012em",
        eyebrow_treatment="sentence",
        layout_bias=("grid", "split", "centered"),
        header_affinity=("classic", "minimal-line"),
        footer_affinity=("minimal-centered", "mega"),
        motion_intensity="subtle",
    ),
    DesignScheme(
        slug="gallery-first",
        label="Gallery First",
        rationale=(
            "photography leads every section and the type stays quiet beneath "
            "it — for brands whose product is what it looks like"
        ),
        moods=frozenset({"friendly", "luxury", "editorial", "playful"}),
        industries=frozenset({"restaurant", "agency", "ecommerce", "personal"}),
        radius_scale=1.1,
        card_treatment="elevated",
        shadow_scale="elevated",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=1.05,
        card_padding_scale=0.9,
        container_max_width=1320,
        type_scale_ratio=1.28,
        heading_weight=600,
        eyebrow_treatment="underline",
        layout_bias=("background", "grid", "bento", "split"),
        hero_policy="fullbleed-all",
        footer_affinity=("editorial", "cta-banner"),
        motion_intensity="balanced",
    ),
    DesignScheme(
        slug="civic-trust",
        label="Civic Trust",
        rationale=(
            "familiar chrome, an even rhythm and a standing CTA banner — a "
            "cause or a practice earns clicks by being legible, not clever"
        ),
        moods=frozenset({"friendly", "modern", "technical"}),
        industries=frozenset({"nonprofit", "professional-services", "childcare"}),
        radius_scale=0.85,
        card_treatment="bordered",
        shadow_scale="soft",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=1.05,
        card_padding_scale=1.05,
        container_max_width=1240,
        heading_weight=700,
        eyebrow_treatment="caps-tracked",
        layout_bias=("grid", "banner", "split", "steps"),
        header_affinity=("classic", "glass-blur"),
        footer_affinity=("cta-banner", "mega"),
        motion_intensity="subtle",
    ),
    DesignScheme(
        slug="storefront",
        label="Storefront",
        rationale=(
            "image-topped cards in an even grid with the offer repeated near "
            "the fold — a shop reads as a shop, and browsing is the conversion"
        ),
        moods=frozenset({"friendly", "modern", "playful"}),
        industries=frozenset({"ecommerce", "restaurant"}),
        radius_scale=1.05,
        card_treatment="elevated",
        shadow_scale="elevated",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=0.9,
        card_padding_scale=0.9,
        container_max_width=1320,
        eyebrow_treatment="chip",
        layout_bias=("grid", "bento", "banner"),
        footer_affinity=("cta-banner", "mega"),
        motion_intensity="balanced",
    ),
    DesignScheme(
        slug="studio-mono",
        label="Studio Mono",
        rationale=(
            "near-monochrome with the accent spent only on a rule — the work "
            "supplies the colour, so the frame does not compete with it"
        ),
        moods=frozenset({"editorial", "technical", "modern", "luxury"}),
        industry_affinity=frozenset({"agency", "personal", "consultancy"}),
        radius_scale=0.15,
        card_treatment="outline",
        shadow_scale="soft",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=1.2,
        card_padding_scale=1.1,
        container_max_width=1200,
        type_scale_ratio=1.35,
        heading_weight=600,
        heading_tracking="-0.03em",
        eyebrow_treatment="underline",
        layout_bias=("editorial", "asymmetric", "minimal", "grid"),
        hero_policy="hero-led",
        header_affinity=("minimal-line", "centered-stack"),
        footer_affinity=("editorial", "minimal-centered"),
        motion_intensity="subtle",
    ),
    DesignScheme(
        slug="playroom",
        label="Playroom",
        rationale=(
            "maximum radius, a mesh wash and shaped seams between every band — "
            "energy without clutter, aimed at the parent as much as the child"
        ),
        moods=frozenset({"playful", "friendly"}),
        industries=frozenset({"childcare", "nonprofit"}),
        radius_scale=1.7,
        card_treatment="elevated",
        shadow_scale="dramatic",
        background_strategy="mesh",
        use_glass=False,
        divider_shape="peak",
        divider_height=68,
        padding_scale=1.05,
        card_padding_scale=1.15,
        container_max_width=1280,
        type_scale_ratio=1.45,
        heading_weight=800,
        eyebrow_treatment="chip",
        layout_bias=("steps", "grid", "bento", "background"),
        footer_affinity=("cta-banner", "mega"),
        motion_intensity="expressive",
    ),
    DesignScheme(
        slug="consult-formal",
        label="Consulting Formal",
        rationale=(
            "restrained shape, narrative splits and a measured rhythm — the "
            "argument is made in prose, so the layout stays out of the way"
        ),
        moods=frozenset({"technical", "modern", "luxury", "editorial"}),
        industry_affinity=frozenset(
            {"consultancy", "professional-services", "saas"}
        ),
        radius_scale=0.45,
        card_treatment="bordered",
        shadow_scale="soft",
        background_strategy="flat",
        use_glass=False,
        divider_shape=None,
        padding_scale=1.15,
        card_padding_scale=1.05,
        container_max_width=1220,
        type_scale_ratio=1.26,
        heading_weight=650,
        heading_tracking="-0.015em",
        eyebrow_treatment="rule",
        layout_bias=("split", "narrative", "grid", "editorial"),
        hero_policy="hero-led",
        header_affinity=("classic", "minimal-line"),
        footer_affinity=("minimal-centered", "mega"),
        motion_intensity="subtle",
    ),
)

SCHEMES_BY_SLUG: dict[str, DesignScheme] = {s.slug: s for s in DESIGN_SCHEMES}


# --- selection -------------------------------------------------------------------


def candidates_for(mood: BrandMood | None, industry: str | None) -> list[str]:
    """Ordered fit list of scheme slugs for one (mood, industry) cell.

    Mood and ``industries`` are hard gates (empty = neutral wildcard); the
    ordering then puts industry-specific briefs ahead of neutral ones, and
    ``industry_affinity`` ahead of no affinity at all. Declaration order breaks
    remaining ties, so the taste encoded in this file's ordering survives.

    Always non-empty: several schemes are gated on mood only, and a cell that
    somehow admitted nothing degrades to the whole catalogue rather than to a
    single answer, which is the failure mode this module exists to fix.
    """
    norm_industry = (industry or "").strip().lower()
    admitted = [
        s
        for s in DESIGN_SCHEMES
        if (not s.moods or (mood or "modern") in s.moods)
        and (not s.industries or norm_industry in s.industries)
    ]
    if not admitted:
        admitted = list(DESIGN_SCHEMES)

    def rank(scheme: DesignScheme) -> int:
        if norm_industry and norm_industry in scheme.industries:
            return 0  # a researched brief for exactly this industry
        if norm_industry and norm_industry in scheme.industry_affinity:
            return 1  # designed with this industry in mind
        if not scheme.industries:
            return 2  # neutral
        return 3  # gate admitted it on an empty industry

    order = {s.slug: i for i, s in enumerate(DESIGN_SCHEMES)}
    return [s.slug for s in sorted(admitted, key=lambda s: (rank(s), order[s.slug]))]


def select_scheme(
    *,
    seed: str,
    mood: BrandMood | None,
    industry: str | None,
    choice: str | None = None,
    avoid: set[str] | None = None,
) -> DesignScheme:
    """Pick this site's scheme: explicit choice → fit list → seeded rotation →
    diversity avoidance. Mirrors ``design_director.compose_design_manifest``'s
    chrome selection exactly, down to reusing ``pick_diverse``.

    ``choice`` is an explicit pin (a caller's stated intent); an unknown slug is
    a no-op rather than an error, the same way an invented palette slug is.
    """
    if choice:
        pinned = SCHEMES_BY_SLUG.get(choice.strip().lower())
        if pinned is not None:
            return pinned
    candidates = candidates_for(mood, industry)
    slug = pick_diverse(candidates, seed=seed or "site", salt="scheme", avoid=avoid)
    return SCHEMES_BY_SLUG.get(slug, NEUTRAL_SCHEME)


def by_slug(slug: str | None) -> DesignScheme:
    """Resolve a slug to its scheme, or the neutral (defer-everything) scheme."""
    if not slug:
        return NEUTRAL_SCHEME
    return SCHEMES_BY_SLUG.get(slug.strip().lower(), NEUTRAL_SCHEME)


def for_theme(theme: object) -> DesignScheme:
    """The scheme a built theme carries.

    Every downstream pass reads the scheme off the ``ThemeTokens`` it was
    already handed, which is why adding an axis needs no new parameter anywhere
    in the pipeline. A theme built before this feature — or with the kill switch
    off — carries no slug and yields the neutral scheme.
    """
    return by_slug(getattr(theme, "design_scheme", None))


# --- derived helpers used by the styling passes ------------------------------------


def scaled_px(value: float, scale: float, *, minimum: float = 0.0) -> str:
    """A scaled CSS length, always as a string with its unit.

    Vue's ``:style`` binding assigns raw values, so a bare number becomes the
    invalid string ``"24"`` and is dropped, while React (builder + preview)
    auto-appends ``px``. A numeric length is therefore a silent divergence
    between the published site and both editors — never emit one.
    """
    return f"{max(minimum, round(value * scale)):.0f}px"


def clamp_divider_height(height: int) -> int:
    """Divider heights are clamped to 8..400 by every renderer
    (webtree-public/lib/sectionDivider.ts); clamp here too, so the value we
    record on the element and the value that actually paints are one number."""
    return max(8, min(400, int(height)))
