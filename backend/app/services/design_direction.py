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

Then at most ONE reasoning call picks among the top `design_concept_candidates`
concepts BY INDEX and creates the four creative colors for this business. Code
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
from app.services import design_schemes
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

    def summary(self) -> str:
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


# --- scoring --------------------------------------------------------------------


def brand_alignment(scheme: DesignScheme, personality: BrandPersonality | None) -> float | None:
    """1.0 when the brand sits exactly on the scheme's target, 0.0 at the far
    corner. Only axes the source spoke to count, and an inferred axis counts for
    less than an evidenced one. None when too little is known to judge."""
    if personality is None or not scheme.personality:
        return None
    weighted = total = 0.0
    known = 0
    for axis, reading in personality.readings().items():
        if reading.value is None or axis not in scheme.personality:
            continue
        weight = 1.0 if reading.evidenced else _INFERRED_AXIS_WEIGHT
        weighted += weight * abs(scheme.personality[axis] - reading.value) / 4
        total += weight
        known += 1
    if known < _MIN_AXES_FOR_ALIGNMENT:
        return None
    return 1.0 - weighted / total


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


class ConceptChoice(BaseModel):
    """The model's answer: an index into the concepts it was shown, and the
    hero height. Either may be null to defer."""

    concept: int | None = None
    hero_height: HeroBackgroundHeight | None = None
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


CONCEPT_PROMPT = """You are an art director choosing the design direction for ONE business's website.

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

Create an intentional palette for THIS business: primary is its identity,
secondary a harmonious supporting color, accent a selective small emphasis,
surface a restrained foundation in the requested light/dark scheme. Industry
and category influence atmosphere, never impose fixed colors. Read personality,
audience, purpose, imagery and explicit direction. Prefer explicit user direction
and observed brand colors over inferred industry conventions. Preserve the logo's
recognizable color; complementary brand colors may inform secondary and accent.
Choose analogous, complementary, split-complementary, monochromatic, neutral,
warm/cool or another coherent relationship as appropriate. Avoid automatic blue
for technology, teal for healthcare, rainbow for childcare, or one palette per
industry. Spend accent sparingly; applications derive technical shades and inks.
Use only four #rrggbb palette values, not separate hover/text/border colors.
If a direction is pinned, keep its concept and hero height; change colors only.

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
) -> str:
    lines = [
        f"Business: {brand_name}",
        f"Industry: {industry or 'other'}",
        f"Mood: {mood}",
        f"Logo colour: {logo_hex or 'none'}",
    ]
    if positioning:
        lines.append(f"Positioning (inferred): {positioning}")
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
        lines.append(
            f"  {index}. {concept.scheme.label} — {concept.scheme.rationale}. "
            f"Palette: primary {theme.palette.primary}, accent {theme.palette.accent}, "
            f"page {theme.palette.background}. Type: {heading} headings / {body} body. "
            f"Score {concept.score:.2f} ({concept.summary().split('(', 1)[1]}"
        )
    return "\n".join(lines)


async def _model_choice(
    concepts: list[Concept], *, llm: LlmClient | None, **brief: object
) -> ConceptChoice | None:
    client = llm or get_reasoning_llm()
    try:
        choice = await client.chat_json(
            system_prompt=CONCEPT_PROMPT,
            user_prompt=_concept_prompt(concepts, **brief),  # type: ignore[arg-type]
            schema=ConceptChoice,
            temperature=settings.design_temperature,
        )
    except LlmError as exc:
        logger.warning("Concept pick failed, keeping the best-scored concept: %s", exc)
        return None
    return choice


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
    return f"{chosen.scheme.label} — {chosen.scheme.rationale}. {how}. Weighed: {weighed}"


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
    use_model: bool = True,
    llm: LlmClient | None = None,
) -> DesignDirection:
    """The site's theme, chosen among scored concepts. See the module docstring.

    ``use_model`` False is for the callers that must not spend a round-trip
    (the page recipe's preview, plan_to_site's no-theme path); they get the
    best-scored concept, which is also every caller's fallback."""
    seed = brand_name.strip() or "site"
    color_scheme = resolve_color_scheme(
        color_scheme_override, brand_color_scheme, logo_is_light, industry=industry
    )
    avoid_palettes = await recent_choices("palette", site_key=seed)

    def theme_for(scheme_slug: str | None) -> ThemeTokens:
        return build_theme(
            logo_hex,
            mood=mood,
            palette_mode="auto",
            font_seed=seed,
            industry=industry,
            color_scheme=color_scheme,
            avoid_palettes=avoid_palettes,
            scheme_choice=scheme_slug,
        )

    if not settings.design_schemes_enabled:
        # No languages to choose among: the per-mood defaults, deterministic.
        theme = theme_for(None)
        theme.hero_background_height = resolve_hero_height(
            hero_height, None, mood=mood, industry=industry
        )
        if settings.ai_palette_enabled and use_model:
            theme = _creative_theme(theme, None, brand_colors, design_direction)
        return DesignDirection(theme=theme, color_scheme=color_scheme, picked_by="schemes-off")

    pinned = design_schemes.SCHEMES_BY_SLUG.get((scheme_choice or "").strip().lower())
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
    if use_model and settings.design_language_enabled and (len(concepts) > 1 or settings.ai_palette_enabled):
        choice = await _model_choice(
            concepts,
            llm=llm,
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
        )
        if choice is not None:
            model_height = choice.hero_height
            if choice.concept is not None and 0 <= choice.concept < len(concepts):
                chosen = concepts[choice.concept]
                picked_by = "model"

    theme = chosen.theme
    if settings.ai_palette_enabled and use_model:
        theme = _creative_theme(theme, choice.palette if choice else None, brand_colors, design_direction)
    theme.hero_background_height = resolve_hero_height(
        hero_height, model_height, mood=mood, industry=industry
    )
    theme.design_rationale = _rationale(concepts, chosen, picked_by)
    logger.info(
        "Design direction for %s: %s (%s) of %s",
        seed, chosen.scheme.slug, picked_by, [c.scheme.slug for c in concepts],
    )
    return DesignDirection(
        theme=theme,
        color_scheme=color_scheme,
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
    """Same art director, with layout/type/hero pinned. One reasoning call."""
    client = llm or get_reasoning_llm()
    choice = await client.chat_json(
        system_prompt=CONCEPT_PROMPT,
        user_prompt=(f"Pinned direction: {theme.design_scheme or theme.mood}; scheme: {theme.color_scheme}. "
                     f"Current palette: {theme.palette.model_dump()}. Keep all non-color decisions.\n"
                     + str(brief)[:6000] + "\nExplicit design direction: " + (direction or "Create another appropriate coherent palette.")[:1000]),
        schema=ConceptChoice,
        temperature=settings.design_temperature,
    )
    if choice.palette is None:
        raise LlmError("The AI did not return a complete valid palette. Your current colors were kept.")
    return _creative_theme(theme, choice.palette, brief.get("brand_colors"), direction)
