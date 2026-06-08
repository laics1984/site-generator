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

from app.models.content_blocks import IndustryCategoryLiteral, SourceContent
from app.models.industry import IndustryTemplate, PageRecipeResponse, PageScaffold
from app.services.industry_templates import all_industries_summary, get_template
from app.services.llm import LlmError
from app.services.page_inference import (
    core_pages_not_in_inferred,
    infer_page_scaffolds,
    optional_pool_for,
)
from app.services.planner import detect_brand_cached

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

    return PageRecipeResponse(
        industry=industry,
        template=picker_template,
        inferred_pages=inferred,
        all_industries=all_industries_summary(),
        detected_brand=detected.model_dump() if detected else None,
    )
