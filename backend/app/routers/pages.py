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
    MultilingualChoice,
    SourceContent,
    resolve_industry,
    resolve_mood,
)
from app.models.industry import IndustryTemplate, PageRecipeResponse, PageScaffold
from app.services.industry_templates import all_industries_summary, get_template
from app.services.llm import LlmError
from app.services.nav_extraction import strip_chrome_lines
from app.services.page_inference import (
    core_pages_not_in_inferred,
    infer_page_scaffolds,
    optional_pool_for,
    scaffolds_for_languages,
)
from app.services.multilingual_strategy import addable_locales, with_plan
from app.services.planner import detect_brand_cached
from app.services import creative_directions
from app.services.design_direction import ContentProfile, resolve_direction

router = APIRouter(prefix="/api/pages", tags=["pages"])


class RecipeRequest(BaseModel):
    source: SourceContent
    industry_override: IndustryCategoryLiteral | None = None
    # A source that only justifies one page (a Facebook Page read). Returns the
    # homepage plus legal pages instead of the industry template's fan-out.
    single_page: bool = False
    # Section list gated on the facts the source actually holds, so no section
    # is requested that it can't ground. Ignored unless `single_page`.
    homepage_sections: list[str] | None = None
    # How to present the source's languages; None = automatic. The returned
    # `multilingual` plan is what /generate/with-pages will build.
    multilingual: MultilingualChoice | None = None


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
    payload.source = with_plan(payload.source, payload.multilingual)
    languages = payload.source.multilingual
    assert languages is not None

    detected = None
    if not payload.industry_override:
        try:
            detected = await detect_brand_cached(payload.source)
        except LlmError as exc:
            raise HTTPException(
                status_code=502, detail=f"Brand detection failed: {exc}"
            ) from exc
    industry = resolve_industry(
        payload.industry_override,
        payload.source.declared_industry,
        detected.industry_category if detected else None,
    )

    archetype = creative_directions.archetype_for_source(
        detected.archetype if detected else None, payload.source, industry
    )
    if detected is not None:
        detected = detected.model_copy(update={"archetype": archetype})
    # Preview theme for the picker: the same design direction /generate/with-pages
    # resolves (brief, content, history), minus the model's pick — a preview must
    # not spend a round-trip, and the best-scored concept is the pick's fallback.
    # No logo palette exists yet here, so the detection's colour hint stands in.
    # Resolved BEFORE the page tree: its creative direction ranks the homepage's
    # landing patterns (section_emphasis).
    preview_direction = await resolve_direction(
        brand_name=(detected.site_name if detected else None) or payload.source.title or "site",
        mood=resolve_mood(  # type: ignore[arg-type]
            override=None,
            brand_mood=None,
            detected=detected.brand_mood if detected else None,
            industry=industry,
        ),
        industry=industry,
        logo_hex=detected.primary_color_hint if detected else None,
        personality=detected.personality if detected else None,
        positioning=detected.positioning if detected else None,
        audience=detected.audience if detected else None,
        business_context=detected.brand_summary if detected else None,
        content=ContentProfile.of(payload.source),
        use_model=False,
        archetype=archetype,
    )
    preview = preview_direction.theme
    creative = preview_direction.creative

    inferred = infer_page_scaffolds(
        payload.source,
        industry=industry,
        site_name=detected.site_name if detected else None,
        single_page=payload.single_page,
        homepage_sections_override=payload.homepage_sections,
        archetype=archetype,
        section_emphasis=creative.section_emphasis if creative else (),
    )
    # Every page in every language the plan publishes at its own URLs — the
    # owner's own version where the crawl found it, else translated from ours.
    inferred = scaffolds_for_languages(inferred, languages, add_missing=True)

    # Reshape the template surfaced to the UI: optional_pages becomes the pool
    # the user can opt into on top of what we inferred. ``suggested_pages`` is
    # emptied since the inferred tree replaces it; core pages only appear if
    # the inferred tree didn't already include them (avoids duplicate About).
    base = get_template(industry)
    core = core_pages_not_in_inferred(base.core_pages, inferred)
    offered: list[PageScaffold] = []
    if payload.single_page:
        # A single-page source carries one homepage's worth of text, so a
        # template About or Contact page would only restate it. Still offered,
        # never pre-ticked; the pages it EARNED arrive as inferred pages.
        offered = [page for page in core if not page.is_legal]
        core = [page for page in core if page.is_legal]
    picker_template = IndustryTemplate(
        industry=base.industry,
        label=base.label,
        description=base.description,
        core_pages=core,
        suggested_pages=[],
        optional_pages=[*offered, *optional_pool_for(industry, inferred)],
    )

    return PageRecipeResponse(
        industry=industry,
        template=picker_template,
        inferred_pages=inferred,
        all_industries=all_industries_summary(),
        detected_brand=detected.model_dump() if detected else None,
        theme_preview=preview.to_builder_styles(),
        google_fonts=preview.typography.google_fonts,
        multilingual=languages,
        available_locales=addable_locales(),
    )
