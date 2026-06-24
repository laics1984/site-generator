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
from app.services.llm import LlmError, OllamaClient, get_llm
from app.services.template_filler import templates_for_type

logger = logging.getLogger(__name__)


class SectionDesignChoice(BaseModel):
    section_index: int
    template_id: str | None = None


class DesignRecipe(BaseModel):
    sections: list[SectionDesignChoice] = Field(default_factory=list)

    def template_for(self, index: int) -> str | None:
        for choice in self.sections:
            if choice.section_index == index:
                return choice.template_id
        return None


SYSTEM_PROMPT = """You are an art director choosing page layouts for a website builder.

Each section below lists its content type and the EXACT template ids available
for it. Pick ONE template id per section — the layout/composition that best
suits the brand's mood and keeps the page feeling intentionally designed, not
generic. Don't default to the safest-looking option every time: when a bolder
or more editorial variant fits the mood and the section still has everything
it needs, prefer it. Different sections on the same page should not all use
the same structural idea (e.g. don't pick a "split" layout for every section).

Reply with ONE JSON object, no markdown, no commentary:
{
  "sections": [
    {"section_index": 0, "template_id": "<one of the ids given for that section>"},
    ...
  ]
}

One entry per section listed below, using its exact section_index. Only use a
template_id from that section's own list — never borrow an id from another
section's list."""


def _variant_label(template: dict) -> str:
    layout = template.get("layoutVariant", "")
    style = template.get("styleVariant", "")
    return f"{layout}/{style}".strip("/") or template.get("id", "")


def _section_blurb(index: int, kind: str) -> str | None:
    options = templates_for_type(kind)
    if len(options) < 2:
        # Nothing to choose between — don't waste prompt budget on it.
        return None
    lines = [f'  - "{t["id"]}" ({_variant_label(t)})' for t in options]
    return f'Section {index} ("{kind}"):\n' + "\n".join(lines)


async def generate_design_recipe(
    *,
    mood: BrandMood | None,
    industry: str | None,
    section_kinds: list[str],
    llm: OllamaClient | None = None,
) -> DesignRecipe:
    """Pick a template variant per section. Returns an empty recipe (safe
    no-op) on any failure, on a disabled design brain, or when no section in
    this page actually has more than one template to choose from."""
    if not settings.design_brain_enabled:
        return DesignRecipe()

    blurbs = [
        blurb
        for i, kind in enumerate(section_kinds)
        if (blurb := _section_blurb(i, kind)) is not None
    ]
    if not blurbs:
        return DesignRecipe()

    client = llm or get_llm()
    user_prompt = (
        f"Brand mood: {mood or 'modern'}\n"
        f"Industry: {industry or 'other'}\n\n" + "\n\n".join(blurbs)
    )
    try:
        return await client.chat_json(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            schema=DesignRecipe,
            temperature=settings.design_temperature,
            think=False,
        )
    except LlmError as exc:
        logger.warning("Design-brain pass failed, falling back to deterministic selection: %s", exc)
        return DesignRecipe()
