import logging
import re

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
    ImageMetadata,
    IndustryCategoryLiteral,
    LinkBarBlock,
    LinkBarLink,
    LinkCluster,
    PagePlan,
    ProfileCandidate,
    ServiceItem,
    ServicesBlock,
    SitePlan,
    SourceContent,
    TeamBlock,
    TeamMember,
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
from app.services.nav_extraction import find_linkbar_cluster, strip_linkbar_lines
from app.services.scaffold_enforcement import align_page_to_scaffold
from app.services.image_vision import VisionAnnotation, annotate_image_pool
from app.services.locale import detect_market, image_query_cue, place_query_cue
from app.services.schema_builder import plan_to_site
from app.services.theme import build_theme

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/generate", tags=["generate"])


# --- legacy free-form generate (unchanged behaviour) ---------------------------


class GenerateRequest(BaseModel):
    """Source content plus optional brand. Brand drives logo + palette + mood."""

    source: SourceContent
    brand: BrandIdentity | None = None
    mood_override: BrandMood | None = None
    contact: dict[str, str] | None = None


def _market_cues_for(source: SourceContent) -> tuple[str, str]:
    """Best-effort (demonym, place) cues for image queries — e.g.
    ("Southeast Asian", "Malaysia").

    Locale detection is an enhancement, never load-bearing — any failure must
    not break generation, so we swallow errors and fall back to no cues.
    """
    try:
        urls = [source.source_ref, *source.links, *source.images]
        market = detect_market(source.raw_text, urls=urls)
        return image_query_cue(market), place_query_cue(market)
    except Exception:  # noqa: BLE001 — image localisation must not 500 a generation
        return "", ""


def _image_pool_for(source: SourceContent) -> tuple[list[str], list[ImageMetadata]]:
    """Flatten entry + crawled page imagery into one de-duped resolver pool."""
    images: list[str] = []
    metadata: list[ImageMetadata] = []
    seen_images: set[str] = set()
    seen_metadata: set[str] = set()

    def add_page(page: SourceContent) -> None:
        for url in page.images:
            if url and url not in seen_images:
                seen_images.add(url)
                images.append(url)
        for item in page.image_metadata:
            if item.url and item.url not in seen_metadata:
                seen_metadata.add(item.url)
                metadata.append(item)
            if item.url and item.url not in seen_images:
                seen_images.add(item.url)
                images.append(item.url)

    add_page(source)
    for page in source.discovered_pages:
        add_page(page)

    return images, metadata


_NAME_TITLE_TOKENS = {
    "dr",
    "prof",
    "professor",
    "mr",
    "mrs",
    "ms",
    "miss",
    "dato",
    "datuk",
    "tan",
    "sir",
}


def _person_name_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if token and token not in _NAME_TITLE_TOKENS
    ]


def _normalized_person_name(value: str | None) -> str:
    return " ".join(_person_name_tokens(value))


def _profile_pool_for(source: SourceContent) -> list[ProfileCandidate]:
    """Flatten entry + crawled profile candidates without duplicates."""
    profiles: list[ProfileCandidate] = []
    seen: set[tuple[str, str | None]] = set()

    def add_page(page: SourceContent) -> None:
        blocked_names = {
            norm
            for norm in (
                _normalized_person_name(page.title),
                *(_normalized_person_name(h) for h in page.headings),
            )
            if norm
        }
        for profile in page.profile_candidates:
            key = (_normalized_person_name(profile.name), profile.photo_url)
            if not key[0] or key[0] in blocked_names or key in seen:
                continue
            seen.add(key)
            profiles.append(profile)

    add_page(source)
    for page in source.discovered_pages:
        add_page(page)

    return profiles


def _profile_match_score(member_name: str, profile: ProfileCandidate) -> float:
    member_norm = _normalized_person_name(member_name)
    profile_norm = _normalized_person_name(profile.name)
    if not member_norm or not profile_norm:
        return 0.0
    if member_norm == profile_norm:
        return 1.0

    member_tokens = set(member_norm.split())
    profile_tokens = set(profile_norm.split())
    if not member_tokens or not profile_tokens:
        return 0.0
    overlap = len(member_tokens & profile_tokens) / len(member_tokens)
    same_tail = member_norm.split()[-1] == profile_norm.split()[-1]
    if same_tail and overlap >= 0.8:
        return 0.86
    return 0.0


async def _annotate_source_images(
    source: SourceContent, metadata: list[ImageMetadata]
) -> dict[str, VisionAnnotation]:
    """Run the opt-in vision pass over the resolver pool + profile portraits.

    Returns {} instantly when no vision model is configured. Like locale
    detection, this is an enhancement — any failure must not break generation.
    """
    try:
        profile_urls = [
            p.photo_url for p in _profile_pool_for(source) if p.photo_url
        ]
        return await annotate_image_pool(metadata, extra_urls=profile_urls)
    except Exception:  # noqa: BLE001 — vision must not 500 a generation
        logger.exception("Vision annotation pass failed; continuing without it")
        return {}


def _profile_photo_vision_ok(
    photo_url: str | None, annotations: dict[str, VisionAnnotation] | None
) -> bool:
    """False when the vision pass saw the 'portrait' and it isn't one (a logo,
    a banner, an empty room). Unannotated photos keep the benefit of the doubt
    — the vision pass is opt-in and bounded, never a gate."""
    if not annotations or not photo_url:
        return True
    annotation = annotations.get(photo_url)
    if annotation is None:
        return True
    return annotation.kind == "photo" and annotation.people_count >= 1


def _enrich_plan_profile_photos(
    plan: SitePlan,
    source: SourceContent,
    annotations: dict[str, VisionAnnotation] | None = None,
) -> None:
    """Attach confidently matched scraped portraits to generated team members.

    Mutates the plan in place. Only concrete URLs from scraper-produced
    ProfileCandidate objects are applied, so older payloads and LLM-only plans
    keep using the existing photo_query fallback.
    """
    profiles = [
        p for p in _profile_pool_for(source)
        if p.photo_url and _profile_photo_vision_ok(p.photo_url, annotations)
    ]
    if not profiles:
        return

    used_urls: set[str] = set()
    for page in plan.pages:
        for block in page.blocks:
            if block.kind != "team":
                continue
            for member in block.members:
                scored = sorted(
                    (
                        (_profile_match_score(member.name, profile), profile)
                        for profile in profiles
                        if profile.photo_url not in used_urls
                    ),
                    key=lambda item: (item[0], item[1].confidence),
                    reverse=True,
                )
                if not scored or scored[0][0] < 0.85:
                    continue
                matched = scored[0][1]
                member.photo_url = matched.photo_url
                member.photo_alt = matched.photo_alt or member.name
                if matched.photo_url:
                    used_urls.add(matched.photo_url)


def _scraped_team_members(
    source: SourceContent,
    annotations: dict[str, VisionAnnotation] | None = None,
) -> list[TeamMember]:
    """Deterministic team members built from scraped profile candidates."""
    members: list[TeamMember] = []
    for profile in _profile_pool_for(source):
        if not profile.photo_url:
            continue
        if not _profile_photo_vision_ok(profile.photo_url, annotations):
            continue
        members.append(
            TeamMember(
                name=profile.name,
                role=profile.role or "",
                bio=profile.bio,
                photo_url=profile.photo_url,
                photo_alt=profile.photo_alt or profile.name,
                photo_query=None,
            )
        )
    return members[:12]


def _ensure_scraped_team_blocks(
    plan: SitePlan,
    source: SourceContent,
    annotations: dict[str, VisionAnnotation] | None = None,
) -> None:
    """Fallback when scraped portraits exist but the final team block lost them.

    The LLM sometimes omits the team section or rewrites member names enough
    that photo matching fails. For team pages, prefer the concrete scraped
    roster over losing the portraits entirely.
    """
    scraped_members = _scraped_team_members(source, annotations)
    if not scraped_members:
        return

    for page in plan.pages:
        team_indexes = [
            idx for idx, block in enumerate(page.blocks) if getattr(block, "kind", None) == "team"
        ]
        if team_indexes:
            for idx in team_indexes:
                block = page.blocks[idx]
                if any(getattr(member, "photo_url", None) for member in block.members):
                    continue
                page.blocks[idx] = TeamBlock(
                    heading=block.heading,
                    subheading=block.subheading,
                    members=scraped_members,
                )
            continue

        if page.page_type != "team":
            continue

        insert_at = next(
            (idx for idx, block in enumerate(page.blocks) if getattr(block, "kind", None) == "cta"),
            len(page.blocks),
        )
        page.blocks.insert(
            insert_at,
            TeamBlock(
                heading="Meet the team",
                subheading=None,
                members=scraped_members,
            ),
        )


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

    scraped_images, scraped_metadata = _image_pool_for(payload.source)
    annotations = await _annotate_source_images(payload.source, scraped_metadata)
    _enrich_plan_profile_photos(plan, payload.source, annotations)
    _ensure_scraped_team_blocks(plan, payload.source, annotations)

    market_cue, place_cue = _market_cues_for(payload.source)
    return await plan_to_site(
        plan,
        brand=brand,
        theme=theme,
        scraped_images=scraped_images,
        scraped_metadata=scraped_metadata,
        contact=payload.contact,
        market_cue=market_cue,
        place_cue=place_cue,
        social_links=_social_links_for(payload.source),
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

    # Announcement/quick-links strap: claim it BEFORE planning so its text is
    # out of raw_text (the LLM must not also narrate it into a paragraph);
    # the strap itself is re-injected as a linkbar section after alignment.
    linkbar_cluster = find_linkbar_cluster(payload.source)
    if linkbar_cluster is not None:
        strip_linkbar_lines(payload.source, linkbar_cluster)

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
    # Hub-page guarantee: every child page is reachable from its parent's body,
    # not just from the footer. Runs after alignment so appended items survive.
    _ensure_hub_child_links(plan.pages)
    if linkbar_cluster is not None:
        _inject_linkbar(plan.pages, linkbar_cluster)

    # Legal pages will be appended after plan_to_site, but they need to appear in
    # the footer nav. Pass their titles + slugs through.
    extra_footer_nav: list[tuple[str, str]] = [
        (s.title, f"/{s.slug}") for s in legal_scaffolds
    ]

    # Generate themed site (body + header + footer + theme)
    scraped_images, scraped_metadata = _image_pool_for(payload.source)
    annotations = await _annotate_source_images(payload.source, scraped_metadata)
    _enrich_plan_profile_photos(plan, payload.source, annotations)
    _ensure_scraped_team_blocks(plan, payload.source, annotations)

    market_cue, place_cue = _market_cues_for(payload.source)
    site = await plan_to_site(
        plan,
        brand=brand,
        theme=theme,
        scraped_images=scraped_images,
        scraped_metadata=scraped_metadata,
        contact=payload.contact,
        extra_footer_nav=extra_footer_nav,
        market_cue=market_cue,
        place_cue=place_cue,
        social_links=_social_links_for(payload.source),
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

        # Force scaffold identity (including hierarchy + nav priority)
        match = match.model_copy(
            update={
                "slug": s.slug,
                "title": s.title,
                "is_homepage": s.is_homepage,
                "parent_slug": s.parent_slug,
                "nav_rank": s.nav_rank,
                "from_source": s.from_source,
            }
        )
        # Enforce section structure
        match = align_page_to_scaffold(match, s, brand_name=brand_name)
        aligned.append(match)
    return aligned


def _social_links_for(source: SourceContent) -> list[tuple[str, str]]:
    return [(link.label, link.href) for link in source.social_links]


def _inject_linkbar(pages: list[PagePlan], cluster: LinkCluster) -> None:
    """Recreate the source's announcement strap as a linkbar section.

    Inserted right after the homepage hero — where these straps live on real
    sites. Links are kept only when they resolve inside the generated site
    (a generated page's slug, or a homepage anchor); a strap reduced to fewer
    than two working links is dropped rather than rendered half-broken.
    """
    home = next((p for p in pages if p.is_homepage), None)
    if home is None:
        return

    generated_slugs = {p.slug for p in pages}
    links: list[LinkBarLink] = []
    for link in cluster.links[:6]:
        href = link.href
        path = href.split("#", 1)[0].strip("/").lower()
        is_home_anchor = "#" in href and path == ""
        if not (is_home_anchor or path in generated_slugs):
            continue
        links.append(LinkBarLink(label=link.label, href=href))
    if len(links) < 2:
        return

    block = LinkBarBlock(
        label=cluster.context_label or None,
        links=links,
    )
    hero_index = next(
        (i for i, b in enumerate(home.blocks) if b.kind == "hero"), None
    )
    insert_at = hero_index + 1 if hero_index is not None else 0
    home.blocks.insert(insert_at, block)


def _ensure_hub_child_links(pages: list[PagePlan]) -> None:
    """Make every parent page's services block cover all of its child pages.

    schema_builder already cross-links service items to children whose titles
    match (``_match_child_by_title``); what it can't do is invent an item for a
    child the LLM never mentioned. Here we append a minimal linked item per
    uncovered child, within the block's max-items bound. Pages without a
    services block are left alone — their children stay reachable via the
    footer columns.
    """
    children_by_parent: dict[str, list[PagePlan]] = {}
    for p in pages:
        if p.parent_slug:
            children_by_parent.setdefault(p.parent_slug, []).append(p)
    if not children_by_parent:
        return

    # Lazy import: schema_builder is heavy and generate.py already depends on
    # it at call time via plan_to_site.
    from app.services.schema_builder import ChildPageRef, _match_child_by_title

    by_slug = {p.slug: p for p in pages}
    for parent_slug, kids in children_by_parent.items():
        parent = by_slug.get(parent_slug)
        if parent is None:
            continue
        services = next(
            (b for b in parent.blocks if isinstance(b, ServicesBlock)), None
        )
        if services is None:
            continue
        for kid in kids:
            ref = ChildPageRef(slug=kid.slug, title=kid.title, page_type=kid.page_type)
            covered = any(
                _match_child_by_title(item.title, [ref]) is not None
                for item in services.items
            )
            if covered:
                continue
            if len(services.items) >= 8:  # ServicesBlock max_length
                break
            services.items.append(
                ServiceItem(
                    title=kid.title,
                    description=kid.description
                    or f"Find out more about {kid.title.lower()}.",
                    cta_label="Learn more",
                    cta_href=f"/{kid.slug}",
                )
            )


# --- debug ---------------------------------------------------------------------


@router.post("/plan-only", response_model=SitePlan)
async def plan_only(source: SourceContent) -> SitePlan:
    """Debug endpoint: returns the raw SitePlan without converting to BuilderElement trees."""
    try:
        plan = await plan_site(source)
        _enrich_plan_profile_photos(plan, source)
        _ensure_scraped_team_blocks(plan, source)
        return plan
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc
