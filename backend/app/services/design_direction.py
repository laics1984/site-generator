"""
Design direction — the one place a site's visual language is decided.

Before this module, the same sequence (colour scheme → design-language LLM pick
→ build_theme with avoid-sets → hero height) was spelled out at three call
sites — both generate endpoints and plan_to_site's no-theme path — plus a
fourth, simpler copy in the page recipe's preview. A fix to one silently
missed the others.

It also decided badly. The LLM was handed one industry's palette menu (three
entries for childcare) and the industry's design brief, and its pick was
"never overridden by history" — so the same prompt gave the same answer, and
production built 14 of 18 kindergartens in `kindergarten-sky`.

Now a site's direction is chosen among CONCEPTS. A concept is one design scheme
(services/design_schemes.py) fully realised for this brand — its palette and
type pairing already picked by build_theme inside the scheme's strategies — and
each is scored on things that can be measured:

* **brand alignment** — distance from the brand's KNOWN personality axes
  (services/brand_brief.py) to the scheme's own target point; evidenced axes
  count more than inferred ones, unknown axes not at all;
* **content fit** — a photo-led language with no source photography, an
  editorial one with little prose, a type-led one with a rich photo library;
* **freshness** — how unlike the last few sites' design fingerprints the
  concept already is (services/design_fingerprint.py: scheme, hue, canvas, type,
  radius), so two concepts in different schemes that would still land on the
  same font and colour count as the repeat they are. With no fingerprints yet
  recorded, whether the scheme itself was recently used.

With no evidence and no history every score ties, and the brand-seeded
rotation the fit list always had decides — so a brand still regenerates
identically, and the deterministic path degrades to exactly the old one.

A reasoning call picks among the top `design_concept_candidates` concepts BY
INDEX. Shared mode also chooses colors; separate mode retains the original
design prompt and follows with a palette-only call. Code
validates that complete proposal and derives readable variants. A failed or
malformed proposal keeps the validated concept's palette. The same call states
the hero height. With creative colors disabled, a one-concept shortlist needs
no model call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ValidationError, field_validator

from app.config import settings
from app.models.brand import (
    BrandMood,
    BrandPersonality,
    CreativePalette,
    HeroBackgroundHeight,
    ThemeTokens,
    default_hero_height,
)
from app.models.content_blocks import ImageMetadata, SourceContent
from app.services import creative_directions, design_schemes
from app.services.business_archetype import spec_for as archetype_spec
from app.services.creative_directions import CreativeDirection
from app.services.design_schemes import DesignScheme
from app.services.design_fingerprint import (
    FINGERPRINT_WINDOW,
    THEME_FIELDS,
    fingerprint_of_theme,
    nearest,
    parse_all,
)
from app.services.diversity import recent_choices, recent_records, seeded_index
from app.services.industry_personality import personality_for
from app.services.llm import LlmClient, LlmError, get_reasoning_llm
from app.services.source_router import is_content_photo
from app.services.theme import apply_creative_palette, build_theme, resolve_color_scheme

logger = logging.getLogger(__name__)

# How the three criteria weigh. Brand alignment leads: a direction that suits
# the business beats a merely novel one. Freshness outweighs content fit
# because content fit only ever demotes a direction the material cannot carry.
_ALIGNMENT_WEIGHT = 0.5
_CONTENT_WEIGHT = 0.2
_FRESHNESS_WEIGHT = 0.3
# An inferred axis (no source quote behind it) counts for less than an evidenced one.
_INFERRED_AXIS_WEIGHT = 0.6
# Fewer known axes than this and alignment is "no evidence", not a score.
_MIN_AXES_FOR_ALIGNMENT = 2
# A direction lists its realising schemes best first; that order is taste and
# is worth this much of a scheme score when choosing among them.
_REALISER_ORDER_BONUS = 0.08
# Below this much source prose an editorial, narrative language has nothing to tell.
_THIN_PROSE_CHARS = 1200
# From this many source photos a type-led language leaves good material unused.
_RICH_PHOTO_COUNT = 6


@dataclass(frozen=True)
class ContentProfile:
    """What the source material can carry, measured before any design exists."""

    photos: int = 0  # content-grade source photographs
    hero_photos: int = 0  # of those, measured large enough for a full-bleed hero
    unsized_photos: int = 0  # of those, never measured — could be either
    prose_chars: int = 0

    @classmethod
    def of_images(cls, images: list[ImageMetadata] | None, prose_chars: int = 0) -> ContentProfile:
        photos = [m for m in images or [] if is_content_photo(m)]
        floor = settings.hero_min_background_dim
        return cls(
            photos=len(photos),
            hero_photos=sum(1 for m in photos if max(m.width or 0, m.height or 0) >= floor),
            unsized_photos=sum(1 for m in photos if not (m.width or m.height)),
            prose_chars=prose_chars,
        )

    @classmethod
    def of(cls, source: SourceContent | None) -> ContentProfile | None:
        if source is None:
            return None
        return cls.of_images(source.image_metadata, len(source.raw_text or ""))

    @property
    def has_hero_photo(self) -> bool | None:
        """True when a measured photo can open a hero full-bleed; False only
        when it is KNOWN that none can (no photos at all, or every one measured
        too small); None when unmeasured photos leave it open — the hero
        resolver will judge those, so nothing is decided in advance."""
        if self.hero_photos:
            return True
        return None if self.unsized_photos else False


@dataclass(frozen=True)
class Concept:
    """One design scheme realised for this brand, with why it scored as it did."""

    scheme: DesignScheme
    theme: ThemeTokens
    score: float
    alignment: float | None  # None = no personality evidence to judge by
    content_fit: float
    freshness: float  # 1 = unlike every recent site, 0 = a repeat
    # The creative direction this concept realises (None: no directions are
    # authored for the business, and the concept is a bare scheme as before).
    direction: CreativeDirection | None = None
    # Why the direction scored as it did: alignment/archetype/evidence/content/freshness.
    direction_parts: dict[str, float | None] = field(default_factory=dict)

    def summary(self) -> str:
        if self.direction is not None:
            parts = ", ".join(
                f"{k} {'n/a' if v is None else f'{v:.2f}'}" for k, v in self.direction_parts.items()
            )
            return f"{self.direction.slug} via {self.scheme.slug} {self.score:.2f} ({parts})"
        alignment = "n/a" if self.alignment is None else f"{self.alignment:.2f}"
        return (
            f"{self.scheme.slug} {self.score:.2f} (alignment {alignment}, "
            f"content fit {self.content_fit:.2f}, freshness {self.freshness:.2f})"
        )


@dataclass(frozen=True)
class DesignDirection:
    theme: ThemeTokens
    color_scheme: str
    concepts: tuple[Concept, ...] = ()
    picked_by: Literal["score", "model", "pin", "schemes-off"] = "score"
    hero_height_from_model: HeroBackgroundHeight | None = field(default=None)

    @property
    def creative(self) -> CreativeDirection | None:
        """The creative direction the chosen theme realises, if any."""
        return creative_directions.for_theme(self.theme)


# --- scoring --------------------------------------------------------------------


def personality_alignment(
    target: dict[str, int], personality: BrandPersonality | None
) -> float | None:
    """1.0 when the brand sits exactly on a target point, 0.0 at the far
    corner. Only axes the source spoke to count, and an inferred axis counts for
    less than an evidenced one. None when too little is known to judge."""
    if personality is None or not target:
        return None
    weighted = total = 0.0
    known = 0
    for axis, reading in personality.readings().items():
        if reading.value is None or axis not in target:
            continue
        weight = 1.0 if reading.evidenced else _INFERRED_AXIS_WEIGHT
        weighted += weight * abs(target[axis] - reading.value) / 4
        total += weight
        known += 1
    if known < _MIN_AXES_FOR_ALIGNMENT:
        return None
    return 1.0 - weighted / total


def brand_alignment(scheme: DesignScheme, personality: BrandPersonality | None) -> float | None:
    """The brand's distance from a scheme's own target point."""
    return personality_alignment(scheme.personality, personality)


def score_directions(
    directions: list[CreativeDirection],
    *,
    seed: str,
    personality: BrandPersonality | None,
    archetype: str | None,
    evidence: str | None,
    photos: int | None,
    recent: set[str],
) -> list[tuple[CreativeDirection, float, dict[str, float | None]]]:
    """Every authored direction scored on what THIS business is, best first.

    What the business is comes first — its personality, the kind of business
    it is, the words it uses about itself — and freshness only separates
    near-equals. The brand seed rotates the list before a stable sort, so it
    decides ties and nothing else: two kindergartens with different evidence
    land on different directions whatever their names hash to."""
    if not directions:
        return []
    offset = seeded_index(seed, "direction", len(directions))
    rotated = directions[offset:] + directions[:offset]
    rows = []
    for position, direction in enumerate(rotated):
        alignment = personality_alignment(direction.personality, personality)
        parts: dict[str, float | None] = {
            "alignment": alignment,
            "archetype": creative_directions.archetype_fit(direction, archetype),
            "evidence": creative_directions.evidence_score(direction, evidence),
            "content": creative_directions.content_fit(direction, photos),
            "freshness": 0.0 if direction.slug in recent else 1.0,
        }
        score = (
            creative_directions.ALIGNMENT_WEIGHT * (0.5 if alignment is None else alignment)
            + creative_directions.ARCHETYPE_WEIGHT * parts["archetype"]  # type: ignore[operator]
            + creative_directions.EVIDENCE_WEIGHT * parts["evidence"]  # type: ignore[operator]
            + creative_directions.CONTENT_WEIGHT * parts["content"]  # type: ignore[operator]
            + creative_directions.FRESHNESS_WEIGHT * parts["freshness"]  # type: ignore[operator]
        )
        rows.append((position, direction, round(score, 6), parts))
    rows.sort(key=lambda row: (-row[2], row[0]))
    return [(direction, score, parts) for _, direction, score, parts in rows]


def direction_realisers(
    direction: CreativeDirection, mood: BrandMood | None, industry: str | None
) -> list[str]:
    """The schemes that can carry a direction for this business, best first.

    The direction's own list, kept to schemes whose hard INDUSTRY gate admits
    the business; of those, the ones the mood gate also admits when any are —
    a direction is chosen on the same personality the mood is derived from, so
    the two usually agree, and when they don't the direction's own list wins
    rather than leaving it unrealisable."""
    norm = (industry or "").strip().lower()
    own = [
        slug for slug in direction.schemes
        if (scheme := design_schemes.SCHEMES_BY_SLUG.get(slug)) is not None
        and (not scheme.industries or norm in scheme.industries)
    ]
    admitted = set(design_schemes.candidates_for(mood, industry))
    return [slug for slug in own if slug in admitted] or own


def content_fit(scheme: DesignScheme, content: ContentProfile | None) -> float:
    """How well the source material can carry the scheme. Only demotes: a
    language is never promoted for material, only held back when it would have
    to be faked (stock photography standing in for a photo-led brand, a
    narrative page with no story to tell)."""
    if content is None:
        return 1.0
    fit = 1.0
    photo_led = scheme.hero_policy == "fullbleed-all" or "background" in scheme.layout_bias[:2]
    if photo_led and content.photos == 0:
        fit -= 0.4
    if scheme.hero_policy == "editorial-mix" and content.photos >= _RICH_PHOTO_COUNT:
        fit -= 0.15
    if scheme.layout_bias[:1] in (("editorial",), ("narrative",)) and (
        content.prose_chars < _THIN_PROSE_CHARS
    ):
        fit -= 0.3
    return max(0.0, fit)


def score_schemes(
    candidates: list[str],
    *,
    seed: str,
    personality: BrandPersonality | None,
    content: ContentProfile | None,
    freshness: dict[str, float],
) -> list[tuple[DesignScheme, float, float | None, float, float]]:
    """Every candidate scheme scored, best first, as (scheme, score, alignment,
    content fit, freshness). ``freshness`` maps slug → 0..1 (missing = 1.0).
    Ties keep the brand-seeded rotation of the fit list, which is the whole
    selection when nothing is known — exactly the order select_scheme's
    pick_diverse started from."""
    if not candidates:
        return []
    offset = seeded_index(seed, "scheme", len(candidates))
    rotated = candidates[offset:] + candidates[:offset]
    scored = []
    for position, slug in enumerate(rotated):
        scheme = design_schemes.by_slug(slug)
        alignment = brand_alignment(scheme, personality)
        fit = content_fit(scheme, content)
        fresh = freshness.get(slug, 1.0)
        score = (
            _ALIGNMENT_WEIGHT * (0.5 if alignment is None else alignment)
            + _CONTENT_WEIGHT * fit
            + _FRESHNESS_WEIGHT * fresh
        )
        scored.append((position, scheme, round(score, 6), alignment, fit, fresh))
    scored.sort(key=lambda row: (-row[2], row[0]))
    return [(scheme, score, alignment, fit, fresh) for _, scheme, score, alignment, fit, fresh in scored]


async def _freshness(
    themes: dict[str, ThemeTokens], *, seed: str
) -> dict[str, float]:
    """Each candidate's distance from the recent sites, by fingerprint over the
    tokens its theme already fixes. Before any fingerprint was recorded (a
    history from before this existed), whether the scheme itself was recently
    used — the signal the fingerprint generalises."""
    recent = parse_all(await recent_records("fingerprint", window=FINGERPRINT_WINDOW))
    if recent:
        return {
            slug: round(1.0 - nearest(fingerprint_of_theme(theme), recent, THEME_FIELDS)[0], 6)
            for slug, theme in themes.items()
        }
    used = await recent_choices("scheme", site_key=seed)
    return {slug: 0.0 if slug in used else 1.0 for slug in themes}


# --- the model's pick -------------------------------------------------------------


class DesignChoice(BaseModel):
    """Legacy design-only answer; creative colors are deliberately absent."""

    concept: int | None = None
    hero_height: HeroBackgroundHeight | None = None


class PaletteChoice(BaseModel):
    """A complete creative palette, with invalid proposals discarded together."""

    palette: CreativePalette | None = None

    @field_validator("palette", mode="before")
    @classmethod
    def _complete_palette(cls, value: object) -> CreativePalette | None:
        # A bad palette must not discard a usable concept or trigger a repair
        # call. Retain the complete deterministic fallback, never partial hexes.
        try:
            return CreativePalette.model_validate(value) if value is not None else None
        except (ValidationError, ValueError, TypeError):
            return None


class ConceptChoice(DesignChoice, PaletteChoice):
    """Shared-call response: design and palette in a single decision."""


_DESIGN_INSTRUCTIONS = """You are an art director choosing the design direction for ONE business's website.

You are given what is known about the business (its name, industry, mood, the
personality its own content shows, and its logo colour) and a short list of
design CONCEPTS already prepared for it. Each concept is a complete visual
language — colour, typography, shape, density and composition — with a score
from the generator's own checks (brand alignment, how well the source material
can carry it, and whether recent sites already used it).

Pick the ONE concept that best expresses THIS business — not the safest, and
not what any business in its industry would get. The scores are evidence, not
orders: overrule them when the business clearly calls for another concept.
Choose only among the concepts listed.

Then decide how tall the site's photo hero should be. "full" gives every page a
full-screen photographic opening; "banded" gives a shorter photo band so the
content starts near the fold. Choose "full" when the brand sells atmosphere,
place, craft or emotion and the imagery IS the message; "banded" when the
visitor arrived to read, compare or act.

"""

_PALETTE_INSTRUCTIONS = """Create an intentional palette for THIS business: primary is its identity,
secondary a harmonious supporting color, accent a selective small emphasis,
surface a restrained foundation in the requested light/dark scheme. Industry
and category influence atmosphere, never impose fixed colors. Read personality,
audience, purpose, imagery and explicit direction. Prefer explicit user direction
and observed brand colors over inferred industry conventions. Preserve the logo's
recognizable color; complementary brand colors may inform secondary and accent.
Choose analogous, complementary, split-complementary, monochromatic, neutral,
warm/cool or another coherent relationship as appropriate. When a creative
direction is given, its colour voice (soft pastel, earth tones, stark neutrals…)
sets the palette's character; never fall back to one palette per industry or an
automatic blue for technology or teal for healthcare.
Spend accent sparingly; applications derive technical shades and inks.
Use only four #rrggbb palette values, not separate hover/text/border colors.
If a direction is pinned, keep its concept and hero height; change colors only.
"""

DESIGN_PROMPT = _DESIGN_INSTRUCTIONS + """Reply with ONE JSON object, no markdown, no commentary:
{"concept": <concept number>, "hero_height": "full" | "banded" | null}
"""

PALETTE_PROMPT = _PALETTE_INSTRUCTIONS + """
The design is already selected. Return only {"palette":{"primary":"#...","secondary":"#...","accent":"#...","surface":"#..."}} as JSON."""

CONCEPT_PROMPT = _DESIGN_INSTRUCTIONS + _PALETTE_INSTRUCTIONS + """
Reply with ONE JSON object, no markdown, no commentary:
{"concept": <concept number>, "hero_height": "full" | "banded" | null,
 "palette": {"primary":"#...", "secondary":"#...", "accent":"#...", "surface":"#..."}}
"""

def _concept_prompt(
    concepts: list[Concept],
    *,
    brand_name: str,
    mood: BrandMood,
    industry: str | None,
    logo_hex: str | None,
    personality: BrandPersonality | None,
    positioning: str | None,
    audience: str | None = None,
    brand_colors: list[str] | None = None,
    reference_colors: list[str] | None = None,
    image_colors: list[str] | None = None,
    design_direction: str | None = None,
    business_context: str | None = None,
    include_palette_context: bool = True,
    archetype: str | None = None,
) -> str:
    lines = [
        f"Business: {brand_name}",
        f"Industry: {industry or 'other'}",
        f"Mood: {mood}",
        f"Logo colour: {logo_hex or 'none'}",
    ]
    if (spec := archetype_spec(archetype)) is not None:
        lines.append(f"Kind of business: {spec.label}")
    if positioning:
        lines.append(f"Positioning (inferred): {positioning}")
    if not include_palette_context:
        # Who it is for and what it does decide a direction as much as its
        # personality does; the palette context below repeats them at length.
        if audience:
            lines.append(f"Audience: {audience[:300]}")
        if business_context:
            lines.append(f"What it does: {business_context[:600]}")
    if include_palette_context:
        lines.extend([
            f"Audience: {(audience or 'unknown')[:300]}",
            f"Observed brand colors: {(brand_colors or [])[:6]}",
            f"Observed website colors: {(reference_colors or [])[:8]}",
            f"Measured image colors: {(image_colors or [])[:6]}",
            f"Explicit design direction: {(design_direction or 'none')[:1000]}",
            f"Business content and purpose: {(business_context or 'unknown')[:1600]}",
            f"Requested scheme: {concepts[0].theme.color_scheme if concepts else 'light'}",
        ])
    if personality is not None and personality.known():
        traits = ", ".join(
            f"{axis.replace('_', '↔')} {value:+d}" for axis, value in personality.known().items()
        )
        lines.append(f"Personality from its own content (-2..+2): {traits}")
    lines.append(f"Industry voice: {personality_for(industry).voice}")
    lines.append("")
    lines.append("Concepts:")
    for index, concept in enumerate(concepts):
        theme = concept.theme
        heading = theme.typography.heading_font.split(",")[0].strip('"\' ')
        body = theme.typography.body_font.split(",")[0].strip('"\' ')
        if concept.direction is not None:
            lines.append(
                f"  {index}. {creative_directions.prompt_line(concept.direction)}. "
                f"Realised as {concept.scheme.label} on a {theme.color_scheme} canvas. "
                f"Palette: primary {theme.palette.primary}, accent {theme.palette.accent}, "
                f"page {theme.palette.background}. Type: {heading} headings / {body} body. "
                f"Score {concept.score:.2f} ({concept.summary().split('(', 1)[1]}"
            )
            continue
        lines.append(
            f"  {index}. {concept.scheme.label} — {concept.scheme.rationale}. "
            f"Palette: primary {theme.palette.primary}, accent {theme.palette.accent}, "
            f"page {theme.palette.background}. Type: {heading} headings / {body} body. "
            f"Score {concept.score:.2f} ({concept.summary().split('(', 1)[1]}"
        )
    return "\n".join(lines)


async def _model_choice(
    concepts: list[Concept], *, llm: LlmClient | None, shared: bool = True, **brief: object
) -> ConceptChoice | None:
    client = llm or get_reasoning_llm()
    try:
        choice = await client.chat_json(
            system_prompt=CONCEPT_PROMPT if shared else DESIGN_PROMPT,
            user_prompt=_concept_prompt(concepts, include_palette_context=shared, **brief),  # type: ignore[arg-type]
            schema=ConceptChoice if shared else DesignChoice,
            temperature=settings.design_temperature,
        )
    except LlmError as exc:
        logger.warning("Concept pick failed, keeping the best-scored concept: %s", exc)
        return None
    return ConceptChoice.model_validate(choice.model_dump())


# --- the direction ----------------------------------------------------------------


def resolve_hero_height(
    explicit: HeroBackgroundHeight | None,
    chosen: HeroBackgroundHeight | None,
    *,
    mood: BrandMood | None,
    industry: str | None,
) -> HeroBackgroundHeight:
    """Site-wide hero height, most-specific source first: the user's own pick
    ("Auto" sends None) → the model's pick → the industry/mood default. Always
    fully determined, so a disabled or failed pick never leaves it unset."""
    return explicit or chosen or default_hero_height(mood, industry)


def _rationale(direction_concepts: list[Concept], chosen: Concept, picked_by: str) -> str:
    weighed = "; ".join(c.summary() for c in direction_concepts)
    how = {
        "model": "chosen by the reasoning model among the scored concepts",
        "score": "best score",
        "pin": "explicitly pinned",
    }[picked_by]
    lead = f"{chosen.scheme.label} — {chosen.scheme.rationale}"
    if chosen.direction is not None:
        lead = f"{chosen.direction.label} direction, realised as {lead}"
    return f"{lead}. {how}. Weighed: {weighed}"


async def _direction_concepts(
    directions: list[CreativeDirection],
    *,
    seed: str,
    mood: BrandMood,
    industry: str | None,
    archetype: str | None,
    personality: BrandPersonality | None,
    content: ContentProfile | None,
    evidence: str,
    theme_for,
    scheme_for,
) -> list[Concept]:
    """One concept per top-scored DIRECTION, each realised by its best scheme.

    Distinct directions, not three realisations of one, is what the model is
    asked to choose between — a shortlist of three schemes for one direction
    would ask it to choose a shade. Within a direction its own scheme order is
    taste (best first) and earns a small bonus; scheme alignment, content fit
    and fingerprint freshness decide the rest, as they always have."""
    ranked = score_directions(
        directions,
        seed=seed,
        personality=personality,
        archetype=archetype,
        evidence=evidence,
        photos=content.photos if content is not None else None,
        recent=await recent_choices("direction", site_key=seed),
    )
    concepts: list[Concept] = []
    for direction, direction_score, parts in ranked[: max(1, settings.design_concept_candidates)]:
        realisers = direction_realisers(direction, mood, industry)
        if not realisers:
            continue
        canvas = scheme_for(direction)
        themes = {slug: theme_for(slug, canvas) for slug in realisers}
        scored = score_schemes(
            realisers,
            seed=seed,
            personality=personality,
            content=content,
            freshness=await _freshness(themes, seed=seed),
        )
        order = {slug: i for i, slug in enumerate(direction.schemes)}

        def ranked_realiser(row: tuple) -> float:
            scheme, score = row[0], row[1]
            return score + _REALISER_ORDER_BONUS * (1 - order.get(scheme.slug, 0) / max(1, len(order)))

        scheme, _score, alignment, fit, fresh = max(scored, key=ranked_realiser)
        concepts.append(
            Concept(
                scheme=scheme,
                theme=themes[scheme.slug],
                score=direction_score,
                alignment=alignment,
                content_fit=fit,
                freshness=fresh,
                direction=direction,
                direction_parts=parts,
            )
        )
    return concepts


async def resolve_direction(
    *,
    brand_name: str,
    mood: BrandMood,
    industry: str | None,
    logo_hex: str | None,
    color_scheme_override: str | None = None,
    brand_color_scheme: str | None = None,
    logo_is_light: bool | None = None,
    personality: BrandPersonality | None = None,
    positioning: str | None = None,
    audience: str | None = None,
    brand_colors: list[str] | None = None,
    reference_colors: list[str] | None = None,
    image_colors: list[str] | None = None,
    design_direction: str | None = None,
    business_context: str | None = None,
    content: ContentProfile | None = None,
    hero_height: HeroBackgroundHeight | None = None,
    scheme_choice: str | None = None,
    palette_design_mode: Literal["shared", "separate"] | None = None,
    generate_palette: bool = True,
    use_model: bool = True,
    llm: LlmClient | None = None,
    archetype: str | None = None,
) -> DesignDirection:
    """The site's theme, chosen among scored concepts. See the module docstring.

    ``use_model`` False is for the callers that must not spend a round-trip
    (the page recipe's preview, plan_to_site's no-theme path); they get the
    best-scored concept, which is also every caller's fallback."""
    shared = (palette_design_mode or settings.ai_palette_call_mode) == "shared"
    palette_enabled = settings.ai_palette_enabled and generate_palette and use_model
    seed = brand_name.strip() or "site"
    color_scheme = resolve_color_scheme(
        color_scheme_override, brand_color_scheme, logo_is_light, industry=industry
    )
    avoid_palettes = await recent_choices("palette", site_key=seed)

    def theme_for(scheme_slug: str | None, scheme_override: str | None = None) -> ThemeTokens:
        return build_theme(
            logo_hex,
            mood=mood,
            palette_mode="auto",
            font_seed=seed,
            industry=industry,
            color_scheme=scheme_override or color_scheme,
            avoid_palettes=avoid_palettes,
            scheme_choice=scheme_slug,
        )

    palette_brief = {
        "business": seed, "industry": industry, "mood": mood,
        "personality": personality.model_dump() if personality else None,
        "positioning": positioning, "audience": audience,
        "brand_colors": brand_colors, "reference_colors": reference_colors,
        "image_colors": image_colors, "business_context": business_context,
    }

    async def separate_palette(selected: ThemeTokens) -> ThemeTokens:
        try:
            return await regenerate_palette(selected, palette_brief, design_direction, llm)
        except LlmError as exc:
            logger.warning("Palette pick failed, keeping the selected concept colors: %s", exc)
            return _creative_theme(selected, None, brand_colors, design_direction)

    if not settings.design_schemes_enabled:
        # No languages to choose among: the per-mood defaults, deterministic.
        theme = theme_for(None)
        theme.hero_background_height = resolve_hero_height(
            hero_height, None, mood=mood, industry=industry
        )
        if palette_enabled:
            theme = await separate_palette(theme)
        return DesignDirection(theme=theme, color_scheme=color_scheme, picked_by="schemes-off")

    pinned = design_schemes.SCHEMES_BY_SLUG.get((scheme_choice or "").strip().lower())
    directions = (
        [] if pinned is not None else creative_directions.directions_for(industry, archetype)
    )
    if directions:
        concepts = await _direction_concepts(
            directions,
            seed=seed,
            mood=mood,
            industry=industry,
            archetype=archetype,
            personality=personality,
            content=content,
            evidence=" ".join(t for t in (positioning, audience, business_context) if t),
            theme_for=theme_for,
            scheme_for=lambda d: resolve_color_scheme(
                color_scheme_override, brand_color_scheme, logo_is_light,
                industry=industry, direction=d,
            ),
        )
    else:
        candidates = [pinned.slug] if pinned is not None else design_schemes.candidates_for(mood, industry)
        # Every candidate realised (a handful of pure build_theme calls): freshness
        # is judged on what the concept would actually look like, not its name.
        themes = {slug: theme_for(slug) for slug in candidates}
        scored = score_schemes(
            candidates,
            seed=seed,
            personality=personality,
            content=content,
            freshness={} if pinned is not None else await _freshness(themes, seed=seed),
        )
        shortlist = scored[: max(1, settings.design_concept_candidates)]
        concepts = [
            Concept(
                scheme=scheme,
                theme=themes[scheme.slug],
                score=score,
                alignment=alignment,
                content_fit=fit,
                freshness=fresh,
            )
            for scheme, score, alignment, fit, fresh in shortlist
        ]

    chosen = concepts[0]
    picked_by: Literal["score", "model", "pin"] = "pin" if pinned is not None else "score"
    model_height: HeroBackgroundHeight | None = None
    choice: ConceptChoice | None = None
    if use_model and settings.design_language_enabled and (len(concepts) > 1 or (shared and palette_enabled)):
        choice = await _model_choice(
            concepts,
            llm=llm,
            shared=shared and palette_enabled,
            brand_name=seed,
            mood=mood,
            industry=industry,
            logo_hex=logo_hex,
            personality=personality,
            positioning=positioning,
            audience=audience,
            brand_colors=brand_colors,
            reference_colors=reference_colors,
            image_colors=image_colors,
            design_direction=design_direction,
            business_context=business_context,
            archetype=archetype,
        )
        if choice is not None:
            model_height = choice.hero_height
            if choice.concept is not None and 0 <= choice.concept < len(concepts):
                chosen = concepts[choice.concept]
                picked_by = "model"

    theme = chosen.theme
    theme.hero_background_height = resolve_hero_height(
        hero_height, model_height, mood=mood, industry=industry
    )
    if chosen.direction is not None:
        theme.creative_direction = chosen.direction.slug
        theme.business_archetype = archetype
        # The palette call hears the direction's colour voice (a gentle
        # nursery's soft pastels, a studio's stark neutrals) — the one place
        # the industry's colour knowledge reaches the model, and it is the
        # direction's, not a rule for the whole industry.
        palette_brief["creative_direction"] = (
            f"{chosen.direction.label}: {chosen.direction.palette_voice}"
        )
    if palette_enabled:
        if shared:
            theme = _creative_theme(theme, choice.palette if choice else None, brand_colors, design_direction)
        else:
            theme = await separate_palette(theme)
    theme.design_rationale = _rationale(concepts, chosen, picked_by)
    logger.info(
        "Design direction for %s: %s (%s) of %s",
        seed, chosen.summary(), picked_by, [c.summary() for c in concepts],
    )
    return DesignDirection(
        theme=theme,
        color_scheme=theme.color_scheme,
        concepts=tuple(concepts),
        picked_by=picked_by,
        hero_height_from_model=model_height,
    )


def _creative_theme(theme: ThemeTokens, palette: CreativePalette | None,
                    brand_colors: list[str] | None, direction: str | None) -> ThemeTokens:
    from app.services.color_system import normalize_hex
    creative = palette or CreativePalette(**{
        key: getattr(theme.palette, key) for key in ("primary", "secondary", "accent", "surface")
    })
    if brand_colors and not direction:
        try:
            creative = creative.model_copy(update={"primary": normalize_hex(brand_colors[0])})
        except ValueError:
            pass
    return apply_creative_palette(theme, creative)


async def regenerate_palette(theme: ThemeTokens, brief: dict, direction: str | None = None,
                             llm: LlmClient | None = None) -> ThemeTokens:
    """Palette-only reasoning; layout, typography and hero remain pinned."""
    client = llm or get_reasoning_llm()
    choice = await client.chat_json(
        system_prompt=PALETTE_PROMPT,
        user_prompt=(f"Pinned direction: {theme.design_scheme or theme.mood}; scheme: {theme.color_scheme}. "
                     f"Current palette: {theme.palette.model_dump()}. Keep all non-color decisions.\n"
                     + str(brief)[:6000] + "\nExplicit design direction: " + (direction or "Create another appropriate coherent palette.")[:1000]),
        schema=PaletteChoice,
        temperature=settings.design_temperature,
    )
    if choice.palette is None:
        raise LlmError("The AI did not return a complete valid palette. Your current colors were kept.")
    result = _creative_theme(theme, choice.palette, brief.get("brand_colors"), direction)
    if result.color_scheme != theme.color_scheme:
        raise LlmError("The proposed surface changed the selected light/dark design. Current colors were kept.")
    return result
