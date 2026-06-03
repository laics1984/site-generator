from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.brand import BrandIdentity, BrandMood
from app.models.builder_schema import (
    BodySchema,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.models.content_blocks import (
    IndustryCategoryLiteral,
    PagePlan,
    SitePlan,
    SourceContent,
)
from app.models.industry import PageScaffold
from app.services.industry_templates import get_template
from app.services.legal_pages import build_privacy_page, build_terms_page
from app.services.llm import LlmError
from app.services.planner import (
    DetectedBrand,
    detect_brand_cached,
    plan_site,
    plan_site_with_scaffolds,
)
from app.services.scaffold_enforcement import align_page_to_scaffold
from app.services.locale import detect_market, image_query_cue
from app.services.schema_builder import plan_to_site
from app.services.theme import build_theme

router = APIRouter(prefix="/api/generate", tags=["generate"])


# --- legacy free-form generate (unchanged behaviour) ---------------------------


class GenerateRequest(BaseModel):
    """Source content plus optional brand. Brand drives logo + palette + mood."""

    source: SourceContent
    brand: BrandIdentity | None = None
    mood_override: BrandMood | None = None
    contact: dict[str, str] | None = None


def _market_cue_for(source: SourceContent) -> str:
    """Best-effort regional cue for image queries (e.g. "Southeast Asian").

    Locale detection is an enhancement, never load-bearing — any failure must
    not break generation, so we swallow errors and fall back to no cue.
    """
    try:
        urls = [source.source_ref, *source.links, *source.images]
        return image_query_cue(detect_market(source.raw_text, urls=urls))
    except Exception:  # noqa: BLE001 — image localisation must not 500 a generation
        return ""


@router.post("/from-source", response_model=GeneratedSite)
async def generate_from_source(payload: GenerateRequest) -> GeneratedSite:
    """
    LLM picks pages and sections freely. Kept for backward compatibility;
    new flows should use /with-pages for deterministic output.
    """
    try:
        plan = await plan_site(payload.source)
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    brand = payload.brand
    mood = payload.mood_override or (brand.mood if brand else None) or plan.brand_mood
    if brand:
        brand = brand.model_copy(update={"mood": mood})

    seed_hex = (
        (brand.extracted_palette[0] if brand and brand.extracted_palette else None)
        or plan.primary_color_hint
    )
    theme = build_theme(seed_hex, mood=mood)

    return await plan_to_site(
        plan,
        brand=brand,
        theme=theme,
        scraped_images=payload.source.images,
        scraped_metadata=payload.source.image_metadata,
        contact=payload.contact,
        market_cue=_market_cue_for(payload.source),
    )


# --- scaffolded generate (new — driven by the page picker) ---------------------


class GenerateWithPagesRequest(BaseModel):
    """Scaffolded generate. The user has chosen the page set; we ask the LLM
    to write copy for the chosen pages' sections, then bolt on legal pages
    (boilerplate, no LLM) and build the themed site.

    If `detected_brand` is passed (from the /pages/recipe response), we skip
    the brand-detection LLM call entirely — saves ~10-30s per generation.
    """

    source: SourceContent
    selected_pages: list[PageScaffold] = Field(min_length=1)
    industry: IndustryCategoryLiteral = "other"
    brand: BrandIdentity | None = None
    mood_override: BrandMood | None = None
    contact: dict[str, str] | None = None
    jurisdiction: str | None = None
    legal_contact_email: str | None = None
    detected_brand: DetectedBrand | None = None


@router.post("/with-pages", response_model=GeneratedSite)
async def generate_with_pages(payload: GenerateWithPagesRequest) -> GeneratedSite:
    # Split scaffolds: LLM-generated content pages vs. boilerplate legal pages
    content_scaffolds = [s for s in payload.selected_pages if not s.is_legal]
    legal_scaffolds = [s for s in payload.selected_pages if s.is_legal]

    if not content_scaffolds:
        raise HTTPException(
            status_code=400,
            detail="At least one non-legal page (e.g. Home) must be selected.",
        )

    # Skip the second LLM call if the frontend already gave us the detection
    # from /api/pages/recipe. Falls back to the cached detector — if the recipe
    # endpoint ran in the same 5-min window the result is already memoised.
    if payload.detected_brand is not None:
        detected = payload.detected_brand
    else:
        try:
            detected = await detect_brand_cached(payload.source)
        except LlmError as exc:
            raise HTTPException(
                status_code=502, detail=f"Brand detection failed: {exc}"
            ) from exc

    # Determine mood / industry / colour seed with override precedence:
    #   1. user upload / explicit selection
    #   2. detected
    #   3. defaults
    mood = (
        payload.mood_override
        or (payload.brand.mood if payload.brand else None)
        or detected.brand_mood
    )
    industry = payload.industry or detected.industry_category

    brand = payload.brand or BrandIdentity(
        name=detected.site_name,
        tagline=detected.tagline,
        mood=mood,
        industry=industry,
    )
    if not brand.mood:
        brand = brand.model_copy(update={"mood": mood})

    seed_hex = (
        (brand.extracted_palette[0] if brand.extracted_palette else None)
        or detected.primary_color_hint
    )
    theme = build_theme(seed_hex, mood=mood)

    # Scaffolded LLM call — produces PagePlans for content_scaffolds in lockstep order.
    try:
        scaffolded = await plan_site_with_scaffolds(
            payload.source, detected, content_scaffolds
        )
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    # Build the SitePlan that schema_builder consumes.
    plan = SitePlan(
        site_name=scaffolded.site_name or detected.site_name,
        tagline=scaffolded.tagline or detected.tagline,
        brand_summary=scaffolded.brand_summary or detected.brand_summary,
        brand_mood=mood,
        industry_category=industry,
        primary_color_hint=scaffolded.primary_color_hint or seed_hex,
        pages=_align_pages_to_scaffolds(
            scaffolded.pages,
            content_scaffolds,
            brand_name=scaffolded.site_name or detected.site_name or "Untitled",
        ),
    )

    # Legal pages will be appended after plan_to_site, but they need to appear in
    # the footer nav. Pass their titles + slugs through.
    extra_footer_nav: list[tuple[str, str]] = [
        (s.title, f"/{s.slug}") for s in legal_scaffolds
    ]

    # Generate themed site (body + header + footer + theme)
    site = await plan_to_site(
        plan,
        brand=brand,
        theme=theme,
        scraped_images=payload.source.images,
        scraped_metadata=payload.source.image_metadata,
        contact=payload.contact,
        extra_footer_nav=extra_footer_nav,
        market_cue=_market_cue_for(payload.source),
    )

    # Bolt on legal pages from boilerplate
    contact_email = (
        payload.legal_contact_email
        or (payload.contact or {}).get("email")
        or "hello@example.com"
    )
    jurisdiction = payload.jurisdiction or "your country / state"
    for legal in legal_scaffolds:
        if legal.page_type == "privacy":
            site.pages.append(
                build_privacy_page(
                    plan.site_name, theme, contact_email=contact_email, jurisdiction=jurisdiction
                )
            )
        elif legal.page_type == "terms":
            site.pages.append(
                build_terms_page(
                    plan.site_name, theme, contact_email=contact_email, jurisdiction=jurisdiction
                )
            )

    return site


def _align_pages_to_scaffolds(
    llm_pages: list[PagePlan],
    scaffolds: list[PageScaffold],
    *,
    brand_name: str = "Untitled",
) -> list[PagePlan]:
    """
    Make sure the LLM output respects scaffold order + slugs + section structure
    even if it drifts.

    Two layers of enforcement:
      1. Page identity: match LLM pages to scaffold by slug/title; force scaffold
         slug/title/is_homepage. If the LLM dropped a page entirely, synthesise
         an empty one (it'll be filled by section defaults in step 2).
      2. Section structure: for each page, call align_page_to_scaffold which
         reorders blocks to match scaffold.sections, drops extras, and pads
         missing kinds with sane defaults.
    """
    by_slug = {p.slug: p for p in llm_pages}
    by_title = {p.title.lower(): p for p in llm_pages}
    aligned: list[PagePlan] = []
    for s in scaffolds:
        match = by_slug.get(s.slug) or by_title.get(s.title.lower())
        if match is None:
            # Synthesise a minimal page; the section defaults will populate it.
            match = PagePlan(
                page_type=s.page_type,  # type: ignore[arg-type]
                slug=s.slug,
                title=s.title,
                description=s.description or "",
                is_homepage=s.is_homepage,
                blocks=[],
                seo_title=f"{s.title} — {brand_name}",
                seo_description=s.description or "",
                parent_slug=s.parent_slug,
            )

        # Force scaffold identity (including hierarchy)
        match = match.model_copy(
            update={
                "slug": s.slug,
                "title": s.title,
                "is_homepage": s.is_homepage,
                "parent_slug": s.parent_slug,
            }
        )
        # Enforce section structure
        match = align_page_to_scaffold(match, s, brand_name=brand_name)
        aligned.append(match)
    return aligned


# --- debug ---------------------------------------------------------------------


@router.post("/plan-only", response_model=SitePlan)
async def plan_only(source: SourceContent) -> SitePlan:
    """Debug endpoint: returns the raw SitePlan without converting to BuilderElement trees."""
    try:
        return await plan_site(source)
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc
