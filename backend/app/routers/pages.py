"""
Page-recipe endpoint. Given the scraped/uploaded source, returns:
- detected industry (LLM)
- the matching industry template (used as the optional pool for the picker)
- inferred_pages: a tree of PageScaffolds derived from the crawl (pre-checked)
- all industry options so the UI can offer a manual override
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.models.content_blocks import (
    IndustryCategoryLiteral,
    SourceContent,
    industry_default_mood,
)
from app.models.industry import IndustryTemplate, PageRecipeResponse, PageScaffold
from app.services.industry_templates import all_industries_summary, get_template
from app.services.llm import LlmError
from app.services.nav_extraction import strip_chrome_lines
from app.services.page_inference import (
    core_pages_not_in_inferred,
    infer_page_scaffolds,
    optional_pool_for,
)
from app.services.planner import detect_brand_cached
from app.services.theme import build_theme

router = APIRouter(prefix="/api/pages", tags=["pages"])


class RecipeRequest(BaseModel):
    source: SourceContent
    industry_override: IndustryCategoryLiteral | None = None


@router.post("/recipe", response_model=PageRecipeResponse)
async def page_recipe(payload: RecipeRequest) -> PageRecipeResponse:
    """
    Returns the suggested page set for the source plus the full DetectedBrand
    payload. The frontend should pass `detected_brand` back into
    /api/generate/with-pages so we don't redo the LLM detection.

    The picker uses ``inferred_pages`` as the source of truth: that's the tree
    of scaffolds derived from the crawled site (or the industry default if the
    crawl was empty). ``template`` still surfaces the optional pool the user
    can opt into.
    """
    # Idempotent re-pass: covers sources assembled outside scrape_url (extend
    # crawl merges, doc uploads) where repeated template menu strips may still
    # pollute raw_text.
    strip_chrome_lines(payload.source)

    detected = None
    if payload.industry_override:
        industry = payload.industry_override
    else:
        try:
            detected = await detect_brand_cached(payload.source)
            industry = detected.industry_category
        except LlmError as exc:
            raise HTTPException(
                status_code=502, detail=f"Brand detection failed: {exc}"
            ) from exc

    inferred = infer_page_scaffolds(
        payload.source,
        industry=industry,
        site_name=detected.site_name if detected else None,
    )

    # Reshape the template surfaced to the UI: optional_pages becomes the pool
    # the user can opt into on top of what we inferred. ``suggested_pages`` is
    # emptied since the inferred tree replaces it; core pages only appear if
    # the inferred tree didn't already include them (avoids duplicate About).
    base = get_template(industry)
    picker_template = IndustryTemplate(
        industry=base.industry,
        label=base.label,
        description=base.description,
        core_pages=core_pages_not_in_inferred(base.core_pages, inferred),
        suggested_pages=[],
        optional_pages=optional_pool_for(industry, inferred),
    )

    # Industry-aware preview theme so the picker can show the tailored fonts/colours
    # up front. Uses the same inputs as /generate/with-pages (industry + site name +
    # mood), so the previewed typography matches the generated site. No logo palette
    # exists yet here → palette_mode="auto" yields a curated industry palette unless
    # the LLM surfaced a colour hint.
    preview = build_theme(
        detected.primary_color_hint if detected else None,
        mood=(detected.brand_mood if detected else industry_default_mood(industry)),  # type: ignore[arg-type]
        font_seed=detected.site_name if detected else None,
        industry=industry,
        palette_mode="auto",
    )

    return PageRecipeResponse(
        industry=industry,
        template=picker_template,
        inferred_pages=inferred,
        all_industries=all_industries_summary(),
        detected_brand=detected.model_dump() if detected else None,
        theme_preview=preview.to_builder_styles(),
        google_fonts=preview.typography.google_fonts,
    )
