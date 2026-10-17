"""Creative directions — an industry's design intelligence, without an industry look.

The design-identity plan (docs/design-identity-plan.md) removed the ten places
that pinned ONE look on every kindergarten, and it was right to: production
built 14 of 18 schools in one palette. But it removed the industry's design
*knowledge* with its *look* — the concept call stopped hearing what makes a good
school or SaaS site at all, and a seeded rotation over generic schemes did the
choosing (docs/design-intelligence-report.md).

A **creative direction** is that knowledge, stated as several distinct ways a
business of this kind can be art-directed well — a Montessori casa and a
tuition centre are both ``childcare`` and want nothing in common visually. Each
direction says:

* what it is FOR (``brief``, ``personality`` target, ``archetypes`` it suits,
  ``cues`` in a business's own words that call for it);
* how it is REALISED — ``schemes`` (existing design schemes that carry it; the
  scheme still owns every pixel), ``color_scheme_lean``, ``image_shape``,
  ``hero_leads``, ``section_emphasis``;
* how the MODELS should treat it — ``imagery`` (stock/photo brief for the
  planner), ``palette_voice`` (for the palette call);
* what the evaluator must not see on it (``anti_patterns``) and how it converts
  (``cta_strategy``).

Selection is ``design_direction.resolve_direction``: directions are scored on the
business's own evidence first, and the brand seed only breaks ties. An industry
with no authored directions gets ``None`` and the pipeline behaves exactly as
before — as does ``CREATIVE_DIRECTIONS_ENABLED=false``.

Data, not code: a new direction is an entry here plus at least two realising
schemes. ``test_creative_directions`` fails on any unknown scheme, archetype,
landing pattern, anti-pattern or image shape, and on a direction that cannot be
realised for an industry it claims.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from app.config import settings
from app.services.design_schemes import HeroLead

ColorSchemeLean = Literal["light", "dark"] | None

# Intents a CTA can serve, ordered per direction; schema_builder.cta_label_for
# takes the first whose destination the site actually has.
CtaIntent = Literal[
    "visit", "tour", "enquire", "whatsapp", "trial-class", "demo", "trial",
    "consult", "project", "assessment", "call",
]

# Codes the design-quality evaluator (services/design_quality.py) checks a
# finished site against. Kept here so a direction can only name codes the
# evaluator implements (test_creative_directions pins the two together).
AntiPattern = Literal[
    "rainbow-titles",        # per-section title inks
    "multi-tint-bands",      # every band a different tint
    "dramatic-shadows",
    "wave-dividers",
    "glass-cards",
    "purple-blue-gradient",
    "ambient-motion",
    "gradient-heavy",        # more than one gradient fill per page
    "repeated-card-grids",   # the same card grid composition twice on a page
]


@dataclass(frozen=True)
class CreativeDirection:
    slug: str
    label: str
    # Hard gate: the industries this direction is authored for.
    industries: frozenset[str]
    # Soft affinity, and — for archetypes that live in several industries
    # (it-services, tech-consultancy) — what unlocks the direction outside them.
    archetypes: frozenset[str]
    # Existing design schemes that realise it, best first. >=2.
    schemes: tuple[str, ...]
    brief: str
    imagery: str
    palette_voice: str
    personality: dict[str, int] = field(default_factory=dict)
    # Words in the business's own positioning/audience/summary that call for
    # this direction (whole-word, case-insensitive).
    cues: tuple[str, ...] = ()
    color_scheme_lean: ColorSchemeLean = None
    image_shape: str = "soft-rect"
    # Homepage openings in order of preference; the scheme's own lead is used
    # when none of these is available for the content.
    hero_leads: tuple[HeroLead, ...] = ()
    # Landing-pattern names (services/landing_patterns.py), best first.
    section_emphasis: tuple[str, ...] = ()
    cta_strategy: tuple[CtaIntent, ...] = ("enquire",)
    anti_patterns: tuple[AntiPattern, ...] = ()
    allow_ambient_motion: bool = False
    # A direction carried by its photographs needs some: below this many
    # content photos it is held back (never promoted for having them).
    min_photos: int = 0


_CHILDCARE = frozenset({"childcare"})
_EARLY_YEARS = frozenset({"kindergarten", "montessori-preschool", "nursery-daycare", "enrichment-centre"})
_TECH = frozenset({"saas", "agency"})

DIRECTIONS: tuple[CreativeDirection, ...] = (
    # --- childcare -------------------------------------------------------------
    CreativeDirection(
        slug="organic-editorial",
        label="Organic editorial",
        industries=_CHILDCARE,
        archetypes=frozenset({"montessori-preschool", "kindergarten"}),
        schemes=("storybook-editorial", "nature-studio", "editorial-broadsheet"),
        brief=(
            "warm educational storytelling: an editorial serif, soft natural materials, "
            "unhurried split layouts that let the school's philosophy read like a story"
        ),
        imagery=(
            "authentic, warmly lit moments of learning — hands with wooden materials, a "
            "teacher reading with a small group, natural textures; never posed rows"
        ),
        palette_voice="warm naturals around the brand colour — cream, clay, sage — with one quiet accent",
        personality={"playful_sophisticated": 1, "organic_structured": -1, "calm_energetic": -1, "minimal_expressive": 0},
        cues=("story", "journey", "philosophy", "reggio", "approach", "community", "heritage"),
        image_shape="arch",
        hero_leads=("editorial", "split-plain"),
        section_emphasis=("approach-led", "day-in-the-life"),
        cta_strategy=("visit", "tour", "enquire"),
        anti_patterns=("rainbow-titles", "multi-tint-bands", "dramatic-shadows", "gradient-heavy"),
    ),
    CreativeDirection(
        slug="contemporary-minimalist",
        label="Contemporary minimalist",
        industries=_CHILDCARE,
        archetypes=frozenset({"montessori-preschool", "kindergarten", "nursery-daycare"}),
        schemes=("airy-luxe", "swiss-quiet", "calm-clinical"),
        brief=(
            "clean, elegant and premium: refined type, purposeful whitespace, a restrained "
            "palette and few, large photographs — quality shown through calm"
        ),
        imagery=(
            "uncluttered daylight classrooms, close details of materials, one child deeply "
            "concentrating; generous negative space, muted tones"
        ),
        palette_voice="a quiet near-neutral palette led by the brand colour, used sparingly",
        personality={"playful_sophisticated": 2, "minimal_expressive": -2, "calm_energetic": -2, "premium_accessible": -2},
        cues=("premium", "montessori", "curated", "bespoke", "calm", "prepared environment", "boutique", "exclusive", "modern", "contemporary"),
        image_shape="arch",
        # The wash-free editorial split: a calm direction's opening must not
        # sit on the split hero's abstract wash, which reads loud.
        hero_leads=("editorial", "minimal"),
        section_emphasis=("approach-led", "parent-trust"),
        cta_strategy=("tour", "visit", "enquire"),
        anti_patterns=("rainbow-titles", "multi-tint-bands", "wave-dividers", "dramatic-shadows", "gradient-heavy", "ambient-motion"),
    ),
    CreativeDirection(
        slug="expressive-contemporary",
        label="Expressive contemporary",
        industries=_CHILDCARE,
        archetypes=frozenset({"enrichment-centre", "kindergarten", "nursery-daycare"}),
        schemes=("playroom", "bold-block", "bento-modular"),
        brief=(
            "confident colour and playful type with controlled energy: modern educational "
            "branding, bold blocks of the brand's own hues, sticker-like details used sparingly"
        ),
        imagery=(
            "children mid-activity — building, coding, painting, performing — bright and "
            "candid, with clear subjects rather than busy group shots"
        ),
        palette_voice="bright, confident colour built from the brand's hues; a cheerful multi-colour voice is welcome",
        personality={"playful_sophisticated": -2, "calm_energetic": 2, "minimal_expressive": 2, "traditional_contemporary": 1},
        cues=("fun", "play", "exciting", "adventure", "coding", "creative", "energetic", "hands-on", "projects"),
        image_shape="blob",
        hero_leads=("playful-split", "bento"),
        section_emphasis=("class-led", "parent-trust"),
        cta_strategy=("trial-class", "visit", "whatsapp", "enquire"),
        anti_patterns=("glass-cards", "purple-blue-gradient"),
        allow_ambient_motion=True,
    ),
    CreativeDirection(
        slug="nature-learning",
        label="Nature-inspired learning",
        industries=_CHILDCARE,
        archetypes=frozenset({"kindergarten", "montessori-preschool", "nursery-daycare"}),
        schemes=("nature-studio", "soft-warm", "storybook-editorial"),
        brief=(
            "earth-inspired calm: natural textures, curved seams, outdoor learning told "
            "through generous photography and soft organic shapes"
        ),
        imagery=(
            "outdoor exploration, gardening, sand and water play, leaves and natural "
            "materials, dappled daylight; green and earth tones"
        ),
        palette_voice="earth tones — leaf green, sand, clay — anchored on the brand colour",
        personality={"organic_structured": -2, "calm_energetic": -1, "traditional_contemporary": 0, "minimal_expressive": 0},
        cues=("nature", "garden", "outdoor", "outdoors", "forest", "eco", "green", "farm", "natural", "sustainab\\w*"),
        image_shape="pebble",
        hero_leads=("editorial", "background", "split-plain"),
        section_emphasis=("day-in-the-life", "approach-led"),
        cta_strategy=("visit", "tour", "enquire"),
        anti_patterns=("rainbow-titles", "glass-cards", "purple-blue-gradient", "gradient-heavy"),
    ),
    CreativeDirection(
        slug="documentary-story",
        label="Documentary storytelling",
        industries=_CHILDCARE,
        archetypes=_EARLY_YEARS,
        schemes=("gallery-first", "storybook-editorial", "nature-studio"),
        brief=(
            "large authentic photographs and a strong editorial hierarchy with minimal "
            "decoration — the school's real days, educators and children carry the page"
        ),
        imagery=(
            "the centre's own photographs first; stock only for atmosphere, never standing "
            "in for this school's rooms, staff or children"
        ),
        palette_voice="a neutral frame that lets photographs carry the colour; the brand colour as the one accent",
        personality={"minimal_expressive": -1, "playful_sophisticated": 1, "calm_energetic": 0},
        cues=("day in the life", "our days", "moments", "stories", "events", "gallery"),
        image_shape="soft-rect",
        hero_leads=("background", "editorial"),
        section_emphasis=("day-in-the-life",),
        cta_strategy=("visit", "tour", "enquire"),
        anti_patterns=("rainbow-titles", "multi-tint-bands", "wave-dividers", "gradient-heavy", "ambient-motion"),
        min_photos=8,
    ),
    CreativeDirection(
        slug="contemporary-academic",
        label="Contemporary academic",
        industries=_CHILDCARE,
        archetypes=frozenset({"kindergarten", "enrichment-centre"}),
        schemes=("civic-trust", "consult-formal", "calm-clinical"),
        brief=(
            "structured and confident: professional type, a clear grid, strong educational "
            "messaging about curriculum and readiness — premium but approachable"
        ),
        imagery=(
            "focused learning — reading, writing, numeracy, small-group teaching — bright "
            "orderly classrooms; real teachers in action"
        ),
        palette_voice="a confident institutional colour from the brand with one warm accent; no rainbow",
        personality={"organic_structured": 1, "traditional_contemporary": -1, "playful_sophisticated": 0, "minimal_expressive": -1},
        cues=("curriculum", "readiness", "academic", "primary school", "kspk", "bilingual", "trilingual", "results", "stem", "languages"),
        image_shape="arch",
        hero_leads=("editorial", "split-plain"),
        section_emphasis=("parent-trust", "class-led"),
        cta_strategy=("visit", "enquire", "whatsapp"),
        anti_patterns=("rainbow-titles", "multi-tint-bands", "glass-cards", "gradient-heavy"),
    ),
    CreativeDirection(
        slug="gentle-nurturing",
        label="Gentle nurturing",
        industries=_CHILDCARE,
        archetypes=frozenset({"nursery-daycare", "kindergarten"}),
        schemes=("soft-nursery", "soft-warm"),
        brief=(
            "soft and reassuring: gentle pastel washes of the brand's own hues, rounded but "
            "restrained shapes, humanist type and quiet motion — a calm first place away from home"
        ),
        imagery=(
            "tender carer-child moments, naps and story time, soft daylight, close and calm; "
            "babies and toddlers only as the source's own context supports"
        ),
        palette_voice="soft pastel tints of the brand's own hues on a warm white, one gentle accent",
        personality={"calm_energetic": -2, "organic_structured": -2, "playful_sophisticated": -1, "premium_accessible": 1},
        # Words about HOW care feels. Ages (infant, toddler) are the archetype's
        # evidence, not this direction's: a brisk extended-hours daycare takes
        # babies too.
        cues=("gentle", "home-like", "homely", "cosy", "cozy", "nurturing", "unhurried", "rhythm", "naps?", "tender", "soft"),
        color_scheme_lean="light",
        image_shape="pebble",
        hero_leads=("playful-split", "split-plain"),
        section_emphasis=("parent-trust", "day-in-the-life"),
        cta_strategy=("visit", "whatsapp", "enquire"),
        anti_patterns=("rainbow-titles", "dramatic-shadows", "gradient-heavy", "glass-cards", "ambient-motion"),
    ),
    # --- technology, SaaS and agencies -------------------------------------------
    CreativeDirection(
        slug="product-led-minimal",
        label="Product-led minimalist",
        industries=_TECH,
        archetypes=frozenset({"saas-product", "developer-platform"}),
        schemes=("swiss-quiet", "calm-clinical", "dense-technical"),
        brief=(
            "elegant type and refined spacing around the product: clear capability "
            "statements, real interface views when supplied, one conversion path"
        ),
        imagery=(
            "the product's own screenshots when the source has them; otherwise calm, "
            "uncluttered work scenes or none — never an invented dashboard"
        ),
        palette_voice="near-neutral foundations with the brand colour as the single action colour",
        personality={"minimal_expressive": -2, "organic_structured": 2, "traditional_contemporary": 2, "calm_energetic": -1, "playful_sophisticated": 1},
        cues=("product", "platform", "workflow", "workflows", "automate", "automation", "simple"),
        color_scheme_lean="light",
        image_shape="soft-rect",
        hero_leads=("split-plain", "minimal"),
        section_emphasis=("product-tour", "pricing-led"),
        cta_strategy=("trial", "demo", "enquire"),
        anti_patterns=("purple-blue-gradient", "glass-cards", "gradient-heavy", "ambient-motion", "repeated-card-grids"),
    ),
    CreativeDirection(
        slug="b2b-saas",
        label="Contemporary B2B SaaS",
        industries=_TECH,
        archetypes=frozenset({"saas-product"}),
        schemes=("bento-modular", "glass-lab", "dense-technical"),
        brief=(
            "strong product messaging in a modular grid: well-structured feature "
            "explanations, use-case storytelling and an obvious demo or trial path"
        ),
        imagery=(
            "interface previews only from the source; teams at work for use cases; no "
            "glowing abstract backdrops"
        ),
        palette_voice="a crisp light system around the brand colour, one bold accent kept for actions and key numbers",
        personality={"traditional_contemporary": 2, "organic_structured": 1, "calm_energetic": 1, "premium_accessible": 1, "minimal_expressive": 0},
        cues=("teams", "b2b", "integrations", "demo", "trial", "finance teams", "sales teams", "scale"),
        color_scheme_lean="light",
        image_shape="soft-rect",
        hero_leads=("bento", "split-plain"),
        section_emphasis=("product-tour", "features-led"),
        cta_strategy=("demo", "trial", "enquire"),
        anti_patterns=("purple-blue-gradient", "gradient-heavy", "repeated-card-grids"),
    ),
    CreativeDirection(
        slug="editorial-tech",
        label="Editorial technology",
        industries=_TECH,
        archetypes=frozenset({"tech-consultancy", "saas-product", "digital-agency"}),
        schemes=("editorial-broadsheet", "studio-mono", "consult-formal"),
        brief=(
            "bold typographic hierarchy and asymmetric layouts with thoughtful whitespace — "
            "a point of view, argued in words, positions the business"
        ),
        imagery="few, sophisticated images — architecture, people thinking, texture — or none",
        palette_voice="ink-on-paper neutrals with the brand colour as a sharp editorial accent",
        personality={"playful_sophisticated": 2, "minimal_expressive": 1, "traditional_contemporary": 1, "calm_energetic": -1},
        cues=("insight", "insights", "research", "perspective", "thinking", "point of view", "ideas"),
        image_shape="soft-rect",
        hero_leads=("editorial", "asymmetric"),
        section_emphasis=("insight-led", "services-process"),
        cta_strategy=("consult", "enquire"),
        anti_patterns=("purple-blue-gradient", "glass-cards", "gradient-heavy", "ambient-motion"),
    ),
    CreativeDirection(
        slug="premium-enterprise",
        label="Premium enterprise",
        industries=frozenset({"saas"}),
        archetypes=frozenset({"tech-consultancy", "it-services"}),
        schemes=("consult-formal", "swiss-quiet", "dense-technical"),
        brief=(
            "a confident, structured identity for serious buyers: clear solution "
            "architecture, professional colour, credibility shown through stated facts"
        ),
        imagery="restrained corporate photography — real offices, infrastructure, people in discussion — or none",
        palette_voice="deep, assured brand colour with steel neutrals; no playful accents",
        personality={"organic_structured": 2, "calm_energetic": -2, "premium_accessible": -2, "playful_sophisticated": 2},
        cues=("enterprise", "bank", "banks", "insurers", "compliance", "governance", "regulated", "security", "financial institutions"),
        color_scheme_lean="light",
        image_shape="soft-rect",
        hero_leads=("split-plain", "editorial"),
        section_emphasis=("solutions-led", "insight-led"),
        cta_strategy=("consult", "assessment", "enquire"),
        anti_patterns=("purple-blue-gradient", "glass-cards", "gradient-heavy", "ambient-motion", "rainbow-titles"),
    ),
    CreativeDirection(
        slug="creative-studio",
        label="Creative digital studio",
        industries=frozenset({"agency"}),
        archetypes=frozenset({"digital-agency", "marketing-agency"}),
        schemes=("poster-display", "studio-mono", "gallery-first"),
        brief=(
            "expressive art direction led by the work: distinctive display type, "
            "portfolio-driven layouts and case-study storytelling"
        ),
        imagery="the studio's own work first — project images large and uncropped where possible",
        palette_voice="a confident, distinctive brand colour against stark neutrals; contrast over decoration",
        personality={"minimal_expressive": 2, "traditional_contemporary": 2, "playful_sophisticated": 1, "calm_energetic": 1},
        cues=("studio", "brand identity", "art direction", "portfolio", "craft", "design", "selected work", "case studies"),
        # The work reads strongest on a dark canvas; an explicit choice or a
        # light-drawn logo can still turn it.
        color_scheme_lean="dark",
        image_shape="soft-rect",
        hero_leads=("asymmetric", "editorial"),
        section_emphasis=("work-first", "portfolio-grid"),
        cta_strategy=("project", "consult", "enquire"),
        anti_patterns=("purple-blue-gradient", "glass-cards", "repeated-card-grids"),
        allow_ambient_motion=True,
    ),
    CreativeDirection(
        slug="developer-centric",
        label="Developer-centric",
        industries=frozenset({"saas"}),
        archetypes=frozenset({"developer-platform"}),
        schemes=("dense-technical", "studio-mono", "glass-lab"),
        brief=(
            "technical clarity: product architecture, code or workflow examples from the "
            "source, documentation-style structure and functional interface demonstrations"
        ),
        imagery="code, diagrams and the product's own interface; no stock people",
        palette_voice="a dark or graphite canvas with the brand colour as a precise signal",
        personality={"organic_structured": 2, "minimal_expressive": -1, "traditional_contemporary": 2, "playful_sophisticated": 1},
        cues=("api", "apis", "sdk", "developers", "developer", "open source", "docs", "cli", "github"),
        color_scheme_lean="dark",
        image_shape="soft-rect",
        hero_leads=("minimal", "split-plain"),
        section_emphasis=("product-tour", "pricing-led"),
        cta_strategy=("trial", "demo", "enquire"),
        anti_patterns=("purple-blue-gradient", "gradient-heavy", "rainbow-titles"),
    ),
    CreativeDirection(
        slug="human-centered-tech",
        label="Human-centred technology",
        industries=_TECH,
        archetypes=frozenset({"it-services", "tech-consultancy", "marketing-agency", "saas-product"}),
        schemes=("calm-clinical", "nature-studio", "storybook-editorial"),
        brief=(
            "approachable technology: real people and teams, business problems told as "
            "stories, clear outcomes and a warm, trust-oriented composition"
        ),
        imagery="the team and clients' real working environments; people helping people, local where the source is",
        palette_voice="a friendly, warm palette around the brand colour; nothing neon",
        personality={"premium_accessible": 2, "organic_structured": -1, "calm_energetic": -1, "playful_sophisticated": 0},
        cues=("local", "people", "friendly", "small business", "small businesses", "sme", "smes", "support", "partner", "trusted"),
        color_scheme_lean="light",
        image_shape="soft-rect",
        hero_leads=("split-plain", "background"),
        section_emphasis=("solutions-led", "services-process"),
        cta_strategy=("call", "assessment", "enquire"),
        anti_patterns=("purple-blue-gradient", "glass-cards", "gradient-heavy", "ambient-motion"),
    ),
)

DIRECTIONS_BY_SLUG: dict[str, CreativeDirection] = {d.slug: d for d in DIRECTIONS}

# Weights for direction scoring (design_direction.score_directions). Brand
# alignment leads as it does for schemes; what the business SAYS about itself
# (archetype, cue words) is next; freshness only separates near-equals.
ALIGNMENT_WEIGHT = 0.35
ARCHETYPE_WEIGHT = 0.25
EVIDENCE_WEIGHT = 0.2
CONTENT_WEIGHT = 0.1
FRESHNESS_WEIGHT = 0.1



def by_slug(slug: str | None) -> CreativeDirection | None:
    return DIRECTIONS_BY_SLUG.get((slug or "").strip().lower())


def for_theme(theme: object) -> CreativeDirection | None:
    """The direction a built theme carries (None before this feature, with the
    flag off, or for an industry with no authored directions)."""
    return by_slug(getattr(theme, "creative_direction", None))


def directions_for(industry: str | None, archetype: str | None) -> list[CreativeDirection]:
    """Directions authored for this business, in declaration order. An
    archetype that lives in several industries unlocks its directions outside
    their home industries (an IT-services firm filed as `consultancy`)."""
    if not settings.creative_directions_enabled:
        return []
    norm = (industry or "").strip().lower()
    return [
        d for d in DIRECTIONS
        if norm in d.industries or (archetype is not None and archetype in d.archetypes and _crosses(archetype))
    ]


# Archetypes valid in more than one industry: their directions travel with them.
_CROSS_INDUSTRY_ARCHETYPES = frozenset({"it-services", "tech-consultancy", "digital-agency", "marketing-agency"})


def _crosses(archetype: str) -> bool:
    return archetype in _CROSS_INDUSTRY_ARCHETYPES


# Compiled once at import: a lookup table, not a cache (app/deployment's
# inventory tells the two apart by whether the dict starts full).
_CUE_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    d.slug: tuple(re.compile(rf"(?<![a-z0-9]){cue}(?![a-z0-9])", re.IGNORECASE) for cue in d.cues)
    for d in DIRECTIONS
}


def _cue_patterns(direction: CreativeDirection) -> tuple[re.Pattern[str], ...]:
    return _CUE_PATTERNS.get(direction.slug, ())


def evidence_score(direction: CreativeDirection, text: str | None) -> float:
    """0..1: how much of the business's own description calls for this
    direction. Two distinct cues are full evidence; one is half."""
    if not text:
        return 0.0
    hits = sum(1 for pattern in _cue_patterns(direction) if pattern.search(text))
    return min(1.0, hits / 2)


def archetype_fit(direction: CreativeDirection, archetype: str | None) -> float:
    """1 when the direction was authored for this archetype, 0.5 when the
    archetype is unknown, 0.2 when it is known and not one of them."""
    if archetype is None:
        return 0.5
    return 1.0 if archetype in direction.archetypes else 0.2


def content_fit(direction: CreativeDirection, photos: int | None) -> float:
    """Only demotes: a photo-carried direction without the photographs."""
    if photos is None or direction.min_photos <= 0:
        return 1.0
    return 1.0 if photos >= direction.min_photos else 0.2


def prompt_line(direction: CreativeDirection) -> str:
    """The direction as one line for a model: what it is and how it looks."""
    return f"{direction.label} — {direction.brief}"


def planner_context(direction: CreativeDirection) -> dict[str, str]:
    """The direction as the copy planner reads it (``design_direction`` in its
    payload): the look in one line, what its pictures should feel like, and
    which hero layout the homepage opens on. Short on purpose — the planner
    prompt is token-budgeted, and the scheme owns every styling decision."""
    hero = "background" if direction.hero_leads[:1] == ("background",) else "split"
    return {"look": prompt_line(direction), "imagery": direction.imagery, "hero": hero}


def archetype_for_source(detected: str | None, source: object, industry: str | None) -> str | None:
    """The business archetype every caller uses: the detection model's when it
    fits the industry, else what the source's own text names. None with
    directions off, so the page set and the planner payload stay exactly as
    before."""
    if not settings.creative_directions_enabled:
        return None
    from app.services.business_archetype import resolve_archetype

    evidence = " ".join(
        str(part) for part in (
            getattr(source, "title", None),
            getattr(source, "description", None),
            (getattr(source, "raw_text", None) or "")[:12000],
        ) if part
    )
    return resolve_archetype(detected, evidence=evidence, industry=industry)


# The intents a site's CONTACT PAGE fulfils, with the header button's words.
# The header CTA always routes to the contact destination, so an intent that
# needs a destination of its own (a WhatsApp chat, a self-serve trial signup)
# is skipped rather than promised: a label must match where it goes.
_CONTACT_CTA_LABELS: dict[str, str] = {
    "visit": "Arrange a Visit",
    "tour": "Request a Tour",
    "trial-class": "Request a Trial Class",
    "demo": "Request a Demo",
    "consult": "Request a Consultation",
    "project": "Start a Project",
    "assessment": "Request an Assessment",
    "call": "Talk to Us",
    "enquire": "Enquire Now",
}


def contact_cta_label(direction: CreativeDirection) -> str | None:
    """The header button's words for a direction: its first CTA intent the
    contact page can actually fulfil."""
    return next(
        (_CONTACT_CTA_LABELS[i] for i in direction.cta_strategy if i in _CONTACT_CTA_LABELS),
        None,
    )
