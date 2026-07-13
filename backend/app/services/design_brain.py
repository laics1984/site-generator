"""
Design-brain passes: extra LLM calls that make the site's design decisions.

Two passes live here:
- Design recipe (generate_site_design_recipe): picks which catalog template
  variant each section of a page should use.
- Design language (generate_design_language): picks the curated colour palette
  and font pairing before theme construction.

Why this exists: section_content.mood_preferred_ids() deterministically
orders templates by mood, and select_template() always takes the first
feasible one in that order. That means every "modern" SaaS site picks the
exact same hero/features/cta layouts as every other "modern" SaaS site —
the single biggest reason generated sites converge on one look per mood.
This pass asks a model (with room to be a little bold — see
settings.design_temperature) to choose more deliberately per section,
constrained to the catalog's real ids for that section's kind.

Guardrails-first: select_template() already ignores an explicit_id that
doesn't belong to the section's type or isn't feasible for its content
(falls through to the deterministic order), so a hallucinated or unfit
choice can never break a page — it just gets silently discarded. A failed
LLM call (timeout, invalid JSON, disabled) returns an empty recipe, which
is a no-op: generation proceeds exactly as it did before this pass existed.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.config import settings
from app.models.brand import BrandMood
from app.services.industry_personality import personality_for
from app.services.llm import LlmError, LlmClient, get_reasoning_llm
from app.services.section_content import mood_allows
from app.services.template_filler import templates_for_type

logger = logging.getLogger(__name__)


class SectionDesignChoice(BaseModel):
    section_index: int
    template_id: str | None = None


class DesignRecipe(BaseModel):
    """One page's template picks, keyed by section index within that page."""

    sections: list[SectionDesignChoice] = Field(default_factory=list)

    def template_for(self, index: int) -> str | None:
        for choice in self.sections:
            if choice.section_index == index:
                return choice.template_id
        return None


class SiteSectionChoice(BaseModel):
    page_index: int
    section_index: int
    template_id: str | None = None


class SiteDesignRecipe(BaseModel):
    """A whole site's template picks from one batched call, keyed by
    (page_index, section_index). Page indices namespace the section indices so
    one page's "section 0" pick never lands on another page's section 0."""

    sections: list[SiteSectionChoice] = Field(default_factory=list)

    def recipe_for(self, page_index: int) -> DesignRecipe:
        """The per-page DesignRecipe for `page_index` (empty when unmatched), so
        the render path keeps using the same `template_for(index)` lookup."""
        return DesignRecipe(
            sections=[
                SectionDesignChoice(
                    section_index=c.section_index, template_id=c.template_id
                )
                for c in self.sections
                if c.page_index == page_index
            ]
        )


SYSTEM_PROMPT = """You are an art director choosing page layouts for a multi-page website.

Each page below lists its sections, and each section lists the EXACT template
ids available for it. Pick ONE template id per section — the layout/composition
that best suits the brand's mood and keeps the page feeling intentionally
designed, not generic. The brief includes the industry's design personality —
favor template variants that express it (e.g. bento grids for saas,
asymmetric editorial for agencies, quiet minimal grids for professional
services) while keeping one coherent language. Don't default to the safest-looking option every time:
when a bolder or more editorial variant fits the mood and the section still has
everything it needs, prefer it. Within a page, different sections should not all
use the same structural idea (e.g. don't pick a "split" layout for every
section). Across pages, keep a coherent design language rather than re-deciding
each page from scratch. Hero sections are pre-assigned by the generator's own
per-page art direction and are not listed.

Reply with ONE JSON object, no markdown, no commentary:
{
  "sections": [
    {"page_index": 0, "section_index": 0, "template_id": "<one of the ids given for that section>"},
    ...
  ]
}

One entry per section listed below, using its exact page_index and section_index.
Only use a template_id from that section's own list — never borrow an id from
another section's list."""


def _variant_label(template: dict) -> str:
    layout = template.get("layoutVariant", "")
    style = template.get("styleVariant", "")
    return f"{layout}/{style}".strip("/") or template.get("id", "")


def _section_options(kind: str, mood: BrandMood | None = None) -> list[str] | None:
    """The indented "- id (variant)" lines for a section kind, or None when the
    kind has fewer than two templates (nothing to choose — skip it). Mood-gated
    templates the brand can't use are never offered (see mood_allows)."""
    options = [t for t in templates_for_type(kind) if mood_allows(t, mood)]
    if len(options) < 2:
        return None
    return [f'    - "{t["id"]}" ({_variant_label(t)})' for t in options]


def _page_blurb(
    page_index: int, section_kinds: list[str], mood: BrandMood | None = None
) -> str | None:
    """One page's section/option listing, or None when no section on the page
    has more than one template to choose between.

    Heroes are excluded: their template is assigned per page by the hero
    director (services/hero_director.py) — letting the LLM also pick one caused
    every page to converge on the same split hero."""
    lines: list[str] = []
    for s_idx, kind in enumerate(section_kinds):
        if kind == "hero":
            continue
        opts = _section_options(kind, mood)
        if opts is None:
            continue
        lines.append(f'  Section {s_idx} ("{kind}"):\n' + "\n".join(opts))
    if not lines:
        return None
    return f"Page {page_index}:\n" + "\n".join(lines)


async def generate_site_design_recipe(
    *,
    mood: BrandMood | None,
    industry: str | None,
    pages: list[list[str]],
    llm: LlmClient | None = None,
) -> SiteDesignRecipe:
    """Pick a template variant per section for the WHOLE site in one LLM call.

    `pages` is one list of section kinds per page, in page order. Returns an
    empty recipe (a safe no-op — every lookup yields None, so selection falls
    back to the deterministic mood-ordered choice) on a disabled design brain,
    on any LLM failure, or when no section on any page has more than one
    template to choose from. Per-section feasibility is still enforced
    downstream, so an unfit or hallucinated pick can never break a page."""
    if not settings.design_brain_enabled:
        return SiteDesignRecipe()

    blurbs = [
        blurb
        for p_idx, kinds in enumerate(pages)
        if (blurb := _page_blurb(p_idx, kinds, mood)) is not None
    ]
    if not blurbs:
        return SiteDesignRecipe()

    client = llm or get_reasoning_llm()
    user_prompt = (
        f"Brand mood: {mood or 'modern'}\n"
        f"Industry: {industry or 'other'}\n"
        f"Industry design personality: {personality_for(industry).design}\n\n"
        + "\n\n".join(blurbs)
    )
    try:
        return await client.chat_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=SiteDesignRecipe,
            temperature=settings.design_temperature,
            num_ctx=settings.design_num_ctx,
        )
    except LlmError as exc:
        logger.warning("Design-brain pass failed, falling back to deterministic selection: %s", exc)
        return SiteDesignRecipe()


async def generate_design_recipe(
    *,
    mood: BrandMood | None,
    industry: str | None,
    section_kinds: list[str],
    llm: LlmClient | None = None,
) -> DesignRecipe:
    """Single-page convenience wrapper over `generate_site_design_recipe`.

    Kept for callers that pick layouts for one page in isolation; the batched
    site-level call is preferred when rendering a whole site (one round-trip
    instead of one per page)."""
    site = await generate_site_design_recipe(
        mood=mood, industry=industry, pages=[section_kinds], llm=llm
    )
    return site.recipe_for(0)


# --- design language: palette + font pairing --------------------------------


class DesignLanguage(BaseModel):
    """The LLM's site-wide design-language picks. Both fields are curated-option
    slugs; None (or a slug the theme lookups reject) defers that decision to
    build_theme's deterministic pickers, so an empty DesignLanguage is always a
    safe no-op."""

    palette: str | None = None
    font_pairing: str | None = None


DESIGN_LANGUAGE_PROMPT = """You are an art director choosing the design language for a website.

You are given the brand (name, mood, industry, design personality, and its own
colour if one was extracted from the logo), a list of curated colour palettes
and a list of curated font pairings. Pick the ONE palette and the ONE font
pairing that best serve this specific brand — not the safest generic option.

Guidance:
- If the brand colour is strong and distinctive, prefer the palette whose
  primary harmonises with it; a brand's own colour should not be fought without
  reason. With no brand colour (null), choose freely on brief fit.
- Judge palettes by the feeling of the swatches against the industry and mood;
  judge font pairings by their tags and the personality of the faces.
- If you genuinely cannot improve on an automatic choice, answer null for that
  field to defer.

Reply with ONE JSON object, no markdown, no commentary:
{"palette": "<palette slug or null>", "font_pairing": "<font pairing slug or null>"}

Only use slugs from the lists given."""


async def generate_design_language(
    *,
    brand_name: str | None,
    mood: BrandMood | None,
    industry: str | None,
    seed_hex: str | None,
    llm: LlmClient | None = None,
) -> DesignLanguage:
    """Pick the site's curated palette + font pairing in one small LLM call.

    Runs before build_theme so the picks flow in as palette_choice/font_choice.
    Returns an empty DesignLanguage (deterministic theming, exactly as before
    this pass existed) when the pass is disabled or the LLM call fails; invalid
    slugs are additionally discarded by the theme lookups themselves."""
    # Imported here (not at module top) to keep this module import-light for
    # the callers that only need the recipe models.
    from app.services.theme import curated_palette_options, font_pairing_options

    if not settings.design_language_enabled:
        return DesignLanguage()

    effective_mood: BrandMood = mood or "modern"
    palette_lines = [
        f'  - "{o["slug"]}" ({o["name"]}; primary {o["primary"]}, accent {o["accent"]}, '
        f'dark {o["dark"]}, tint {o["tint"]}; suits: {", ".join(o["categories"]) or "any"})'
        for o in curated_palette_options(industry)
    ]
    font_lines = [
        f'  - "{o["slug"]}" ({o["heading_font"]} headings / {o["body_font"]} body; '
        f'tags: {", ".join(o["tags"]) or "none"})'
        for o in font_pairing_options(effective_mood)
    ]
    user_prompt = (
        f"Brand name: {brand_name or 'unknown'}\n"
        f"Brand mood: {effective_mood}\n"
        f"Industry: {industry or 'other'}\n"
        f"Industry design personality: {personality_for(industry).design}\n"
        f"Brand colour (from logo): {seed_hex or 'null'}\n\n"
        "Curated palettes:\n" + "\n".join(palette_lines) + "\n\n"
        "Font pairings:\n" + "\n".join(font_lines)
    )

    client = llm or get_reasoning_llm()
    try:
        language = await client.chat_json(
            system_prompt=DESIGN_LANGUAGE_PROMPT,
            user_prompt=user_prompt,
            schema=DesignLanguage,
            temperature=settings.design_temperature,
            num_ctx=settings.design_num_ctx,
        )
    except LlmError as exc:
        logger.warning(
            "Design-language pass failed, falling back to deterministic theming: %s", exc
        )
        return DesignLanguage()
    logger.info(
        "Design language picked: palette=%s font_pairing=%s (brand=%s)",
        language.palette,
        language.font_pairing,
        brand_name,
    )
    return language
