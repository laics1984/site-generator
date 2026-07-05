"""
Design-brain pass: one extra LLM call that picks which catalog template
variant each section of a page should use.

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
from app.services.llm import LlmError, LlmClient, get_llm
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


def _section_options(kind: str) -> list[str] | None:
    """The indented "- id (variant)" lines for a section kind, or None when the
    kind has fewer than two templates (nothing to choose — skip it)."""
    options = templates_for_type(kind)
    if len(options) < 2:
        return None
    return [f'    - "{t["id"]}" ({_variant_label(t)})' for t in options]


def _page_blurb(page_index: int, section_kinds: list[str]) -> str | None:
    """One page's section/option listing, or None when no section on the page
    has more than one template to choose between.

    Heroes are excluded: their template is assigned per page by the hero
    director (services/hero_director.py) — letting the LLM also pick one caused
    every page to converge on the same split hero."""
    lines: list[str] = []
    for s_idx, kind in enumerate(section_kinds):
        if kind == "hero":
            continue
        opts = _section_options(kind)
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
        if (blurb := _page_blurb(p_idx, kinds)) is not None
    ]
    if not blurbs:
        return SiteDesignRecipe()

    client = llm or get_llm()
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
            # The whole-site prompt repeats each kind's option list per page, so
            # give it more room than the 4096 default to avoid truncation.
            num_ctx=8192,
            think=False,
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
