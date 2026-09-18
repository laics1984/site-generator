"""
Push a GeneratedSite into a webtree CMS entity.

A push is a SYNC of the entity's pages and design with the generated site
(services/cms_sync.py owns the rules). A fresh entity gets everything; a site
that already has pages gets its pages and design replaced and keeps what its
owner has built up in the CMS since — articles, events, categories, tags,
contacts, subscribers, insights and the WhatsApp settings.

Steps (in order):
  1. Auth — JWT login on the CMS API
  2. Inspect — list every page the entity has (archived too), settle the site's
     slugs against them, and plan: which pages are updated in place, which are
     created, which are archived
  3. Media upload — walk every page's BuilderElement tree, find image srcs
     that are data:image/... or external URLs (and document-link hrefs, e.g.
     scraped PDFs), upload to /api/file/add and rewrite to CDN URLs in-place
  4. Land pages — POST /pages for each page the entity lacks, PATCH the
     metadata of each page it has (restoring an archived match first). Capture
     pageId + draftVersion. Homepage goes first.
  5. Read the homepage's builder payload — captures layout.versionId for the
     save-layout step (layout is entity-scoped — write it once).
  6. Save layout — wrap header/footer + emit menus + PUT once on the homepage
  7. Save drafts — for every page, PUT /draft with its bodySchema
  8. Builder styles — mint launch-code session + PUT /builder/styles;
     site icon; WhatsApp button (first push only)
  9. (Optional) Publish — POST /publish for each page, then archive the pages
     the new site does not have
 10. Content types — article/event template pages (templateFor) the site lacks
     + on a first push, migrated article/event entries from the source site

Each step's outcome is appended to PushReport so the UI can show a per-step
status table. Failures abort the push but the report carries everything done
up to the failure point (useful for diagnostics + future resume support).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import unquote, urlparse

import httpx

from app.models.builder_schema import (
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.models.content_blocks import ContentCollections
from app.services.cms_client import CmsApiError, CmsClient
from app.services.cms_sync import (
    ExistingPage,
    PageChange,
    SyncPlan,
    describe_plan,
    existing_pages,
    normalize_site_slugs,
    plan_sync,
)
from app.services.cms_targets import CmsTarget, default_target
from app.services.menu_builder import build_layout_payload
from app.services.platform_routes import BLANK_TEMPLATE_BODY, TEMPLATE_PAGE_DEFAULTS
from app.services.timing import stage
from app.services.url_guard import UnsafeUrlError, assert_public_url

logger = logging.getLogger(__name__)


# --- public report types --------------------------------------------------------


@dataclass
class PushStep:
    name: str
    ok: bool = False
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    # A step that SUCCEEDED but not the way it was asked to. Distinct from
    # `error` on purpose: the push carries on and the report still says ok, but
    # the operator has something to go and fix in the CMS afterwards.
    warning: str | None = None


@dataclass
class PushReport:
    """Per-step outcome — what the frontend renders as a progress table."""

    success: bool = False
    steps: list[PushStep] = field(default_factory=list)
    # pageId → final CMS URL (or anchor) for navigation after success
    page_urls: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    def record(self, step: PushStep) -> None:
        self.steps.append(step)
        logger.info(
            "Push step %s %s: %s",
            step.name,
            "OK" if step.ok else "FAILED",
            step.detail or step.error or "",
        )


# --- request type ---------------------------------------------------------------


@dataclass
class PushRequest:
    """All inputs the orchestrator needs. Frontend collects these."""

    site: GeneratedSite
    cms_email: str
    cms_password: str
    entity_token: str
    publish: bool = False
    push_builder_styles: bool = True
    # Reset the site's existing article/event template pages to the new design
    # (see _ensure_template_pages). Opt-in: a template is the owner's article
    # and event design, often customised, and an update keeps it by default.
    replace_templates: bool = False
    # The site icon captured from the source. Optional for the same reason
    # push_builder_styles is: re-pushing to an entity whose owner has since
    # chosen their own icon must not silently replace it.
    push_favicon: bool = True
    # When True, create a brand-new entity (owned by the logged-in user) before
    # pushing, and ignore `entity_token`.
    create_entity: bool = False
    new_entity_name: str | None = None
    new_entity_url: str | None = None
    # Blog posts / events extracted from the source site (content_collections);
    # pushed as real CMS article/event entries after the pages land.
    collections: ContentCollections | None = None
    # Which CMS this lands in. Defaulted, not required: a caller that names none
    # gets the default target, which is exactly the behaviour before targets
    # existed. Resolved at the HTTP boundary (routers/cms.py) so an unknown name
    # is a 400 rather than something buried in a PushReport.
    target: CmsTarget = field(default_factory=default_target)


# --- the orchestrator -----------------------------------------------------------


# CMS calls that are mutually independent (media uploads, page creates, draft
# saves, publishes) run under this bound so a big site doesn't stampede the CMS.
_PUSH_CONCURRENCY = 5


async def _create_entity_tolerating_url_clash(
    client: CmsClient,
    *,
    name: str,
    entity_url: str | None,
    builder_styles: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    """Create the entity, retrying once without `entity_url` if that URL is taken.

    The CMS rules entity_url `nullable` but `unique` across the WHOLE install, so
    on a shared production CMS the site's own address may already belong to
    another tenant — a clash that simply cannot happen on a fresh local one. The
    URL is the one optional part of the request, so dropping it lands the site
    instead of failing an entire push over a field the operator can set later.

    Detected on the `errors` KEY, never on the message text: Laravel's copy is
    translatable and the key is the contract. Returns (entity, warning).
    """
    try:
        entity = await client.create_entity(
            entity_name=name, entity_url=entity_url, builder_styles=builder_styles
        )
        return entity, None
    except CmsApiError as exc:
        body = exc.response_body if isinstance(exc.response_body, dict) else {}
        errors = body.get("errors")
        clash = (
            exc.status == 422
            and isinstance(errors, dict)
            and "entity_url" in errors
            and bool(entity_url)
        )
        if not clash:
            raise
    entity = await client.create_entity(
        entity_name=name, entity_url=None, builder_styles=builder_styles
    )
    return entity, (
        f"The website URL {entity_url} is already registered to another site on this "
        "CMS, so this site was created without one. Set it in the admin once the "
        "clash is resolved."
    )


async def push_site(req: PushRequest) -> PushReport:
    """Run the full push and return a PushReport. Never raises."""
    report = PushReport()
    client = CmsClient.for_target(req.target)
    try:
        return await _run_push(client, req, report)
    except Exception as exc:
        logger.exception("Unexpected error during push")
        report.error = f"Unexpected error: {exc}"
        return report
    finally:
        await client.aclose()


def _raise_first_error(results: list) -> None:
    """Re-raise the first exception in an asyncio.gather(return_exceptions=True)
    result list, preserving the sequential loop's abort-on-first-error contract."""
    for res in results:
        if isinstance(res, BaseException):
            raise res


async def _run_push(client: CmsClient, req: PushRequest, report: PushReport) -> PushReport:
    # 1. Auth
    try:
        await client.login(req.cms_email, req.cms_password)
        report.record(PushStep(name="auth", ok=True, detail="JWT acquired"))
    except CmsApiError as exc:
        report.record(PushStep(name="auth", ok=False, error=str(exc)))
        report.error = str(exc)
        return report

    # 1b. (Optional) Create a fresh entity and push into it.
    if req.create_entity:
        try:
            name = (req.new_entity_name or req.site.site_name or "New Site").strip()
            entity, url_warning = await _create_entity_tolerating_url_clash(
                client,
                name=name,
                entity_url=(req.new_entity_url or None),
                builder_styles=req.site.builder_styles or None,
            )
            req.entity_token = str(entity.get("entity_api_token") or "")
            report.record(
                PushStep(
                    name="create_entity",
                    ok=True,
                    detail=f"Created entity '{name}' (token {req.entity_token[:8]}…)",
                    data={
                        "entity_token": req.entity_token,
                        "entity_id": entity.get("entity_id"),
                        # Threaded through for callers that create the entity and
                        # then need to link to it. cms-api returns both on
                        # POST /api/entities; without them a caller has to
                        # reconstruct the platform host itself, which means the
                        # base domain lives in two places and drifts.
                        "public_identifier": entity.get("public_identifier"),
                        "public_url": entity.get("public_url"),
                    },
                    warning=url_warning,
                )
            )
        except CmsApiError as exc:
            report.record(PushStep(name="create_entity", ok=False, error=str(exc)))
            report.error = str(exc)
            return report

    # 2. Inspect the entity and plan the sync. The site's slugs are settled
    #    against the pages already there in the same call, so every match is on
    #    the spelling the page is published at (services/cms_sync.py).
    try:
        existing, renamed, plan = await inspect_entity(client, req)
    except CmsApiError as exc:
        report.record(PushStep(name="inspect", ok=False, error=str(exc)))
        report.error = str(exc)
        return report
    report.record(
        PushStep(name="inspect", ok=True, detail=describe_plan(plan), data=plan.as_dict())
    )
    if renamed:
        report.record(
            PushStep(
                name="normalize_slugs",
                ok=True,
                detail=f"Normalized {len(renamed)} slug(s)",
                data={"renamed": renamed},
            )
        )

    # 3. Media upload — collect unique image srcs, upload, build a rewrite map.
    #    Individual failures are non-fatal: the image is stripped from the schema
    #    so the published site never has a broken reference.
    try:
        with stage("push_media_upload"):
            media = await _upload_media(client, req)
        report.record(
            PushStep(
                name="media",
                ok=True,
                detail=_describe_media(media),
                data=media.counts(),
                warning=_media_warning(media),
            )
        )
    except CmsApiError as exc:
        report.record(PushStep(name="media", ok=False, error=str(exc)))
        report.error = str(exc)
        return report

    # Apply rewrites BEFORE we ship schemas — saves us a second pass and
    # ensures every src on the CMS side is a permanent URL.
    _apply_src_rewrites(req.site, media.rewrites)
    # Drop any image that couldn't be re-hosted (dead/404 source URL) so the
    # published site never renders a broken image pointing back at the source.
    stripped = _strip_invalid_images(req.site, media.failed)
    if stripped:
        logger.info("Stripped %d unresolvable image reference(s)", stripped)

    # 4. Land the pages — create the ones the entity lacks, refresh the metadata
    #    of the ones it has (restoring an archived match first). Homepage first.
    try:
        with stage("push_land_pages"):
            landed = await _land_pages(client, req, plan)
        report.record(
            PushStep(
                name="pages",
                ok=True,
                detail=_describe_landing(plan),
                data={"page_ids": [item.page_id for item in landed]},
            )
        )
    except CmsApiError as exc:
        report.record(PushStep(name="pages", ok=False, error=str(exc)))
        report.error = str(exc)
        return report

    # 5. Read the homepage's builder payload to capture layout.versionId
    homepage_id = landed[0].page_id
    try:
        builder_payload = await client.get_builder_payload(req.entity_token, homepage_id)
        layout = builder_payload.get("layout") or {}
        layout_version_id = layout.get("versionId")
        if not layout_version_id:
            raise CmsApiError(
                500,
                f"Builder payload missing layout.versionId: {builder_payload}",
            )
        report.record(
            PushStep(
                name="read_layout_version",
                ok=True,
                detail=f"layout.versionId={layout_version_id}",
                data={"layout_version_id": layout_version_id},
            )
        )
    except CmsApiError as exc:
        report.record(PushStep(name="read_layout_version", ok=False, error=str(exc)))
        report.error = str(exc)
        return report

    # 6. Save layout — wrap + emit menus + PUT once on the homepage. The layout
    #    is entity-scoped and has no draft state: the CMS re-pins every published
    #    page to the new version at once, so on an update the header, footer and
    #    menus go live here whether or not the pages are published below.
    try:
        try:
            menus, header_payload, footer_payload = build_layout_payload(req.site)
        except ValueError as exc:
            raise CmsApiError(500, str(exc)) from exc
        result = await client.save_page_layout(
            req.entity_token,
            homepage_id,
            expected_layout_version_id=layout_version_id,
            header_schema=header_payload,
            footer_schema=footer_payload,
            menus=menus,
        )
        # Capture refreshed layout.versionId for the publish step.
        new_layout_version_id = result.get("versionId") or layout_version_id
        report.record(
            PushStep(
                name="save_layout",
                ok=True,
                detail=f"{len(menus)} menu(s) + header/footer saved",
                data={"layout_version_id": new_layout_version_id},
            )
        )
        layout_version_id = new_layout_version_id
    except CmsApiError as exc:
        report.record(PushStep(name="save_layout", ok=False, error=str(exc)))
        report.error = str(exc)
        return report

    # 7. Save drafts — bodySchema per page, concurrently (each save uses only
    #    its own page's draftVersion; layout was already saved in step 6).
    saved_drafts: dict[str, int] = {}  # pageId → latest draft_version
    try:
        draft_sem = asyncio.Semaphore(_PUSH_CONCURRENCY)

        async def _save_one(item: _LandedPage) -> tuple[str, int]:
            body_schema = {
                "elements": [
                    el.model_dump(mode="json") if isinstance(el, BuilderElement) else el
                    for el in item.page.body_schema.elements
                ],
            }
            async with draft_sem:
                result = await client.save_page_draft(
                    req.entity_token,
                    item.page_id,
                    base_draft_version=item.draft_version,
                    body_schema=body_schema,
                )
            return item.page_id, int(result.get("draftVersion") or item.draft_version + 1)

        with stage("push_save_drafts"):
            draft_results = await asyncio.gather(
                *(_save_one(item) for item in landed), return_exceptions=True
            )
        _raise_first_error(draft_results)
        saved_drafts = dict(draft_results)
        report.record(
            PushStep(
                name="save_drafts",
                ok=True,
                detail=f"{len(saved_drafts)} draft(s) saved",
            )
        )
    except CmsApiError as exc:
        report.record(PushStep(name="save_drafts", ok=False, error=str(exc)))
        report.error = str(exc)
        return report

    # 8. Builder styles via launch-code bridge (optional)
    if req.push_builder_styles and req.site.builder_styles:
        try:
            await client.mint_builder_session(req.entity_token)
            styles_result = await client.update_builder_styles(req.site.builder_styles)
            # Saving builder_styles mints a new layout version and makes it the
            # entity's active version — refresh layout_version_id so the publish
            # step's expectedLayoutVersionId isn't stale (else LAYOUT_VERSION_CONFLICT).
            new_layout = (styles_result.get("data") or {}).get("layout") or {}
            if new_layout.get("versionId"):
                layout_version_id = new_layout["versionId"]
            report.record(PushStep(name="builder_styles", ok=True, detail="Theme applied"))
        except CmsApiError as exc:
            # Non-fatal — site already pushed; theme can be set manually.
            report.record(
                PushStep(
                    name="builder_styles",
                    ok=False,
                    error=str(exc),
                    detail="Theme push failed but site pages are in. Apply theme manually.",
                )
            )
    else:
        report.record(
            PushStep(name="builder_styles", ok=True, detail="Skipped (per request)")
        )

    # 8b. Site icon (optional). Non-fatal: the pages are already in, and an icon
    #     is something the owner can set in the admin afterwards.
    await _push_favicon(client, req, report)

    # 8c. WhatsApp chat button, when the source site published a number
    #     (services/whatsapp_discovery.py). Non-fatal for the same reason as the
    #     icon: the site is already in, and this is one switch in Site settings.
    #     First push only — an update keeps the owner's settings.
    await _push_whatsapp_widget(client, req, report, first_push=plan.first_push)

    # 9. Publish (optional) — concurrent; every publish uses its own page's saved
    #    draft version plus the shared (post-builder-styles) layout_version_id.
    if req.publish:
        try:
            publish_sem = asyncio.Semaphore(_PUSH_CONCURRENCY)

            async def _publish_one(page_id: str) -> None:
                async with publish_sem:
                    await client.publish_page(
                        req.entity_token,
                        page_id,
                        expected_draft_version=saved_drafts.get(page_id, 1),
                        expected_layout_version_id=layout_version_id,
                    )

            with stage("push_publish"):
                publish_results = await asyncio.gather(
                    *(_publish_one(item.page_id) for item in landed),
                    return_exceptions=True,
                )
            _raise_first_error(publish_results)
            report.record(
                PushStep(
                    name="publish",
                    ok=True,
                    detail=f"{len(landed)} page(s) published",
                )
            )
        except CmsApiError as exc:
            report.record(PushStep(name="publish", ok=False, error=str(exc)))
            report.error = str(exc)
            return report
    else:
        report.record(PushStep(name="publish", ok=True, detail="Skipped — pushed as drafts"))

    # 9b. Pages the new site does not have come off the live site — only when
    #     the new pages went live with them (see _archive_pages).
    await _archive_pages(client, req, plan, report, publishing=req.publish)

    # 10. CMS content types — the template pages the site's list elements need
    #     and, on a first push only, the migrated article/event entries. Both
    #     are non-fatal: the site is already pushed, so a failure here degrades
    #     to "add content later".
    await _ensure_template_pages(client, req, report, existing)
    await _push_content_entries(client, req, report, first_push=plan.first_push)

    # Record page IDs for the UI's "Open in builder" links
    for item in landed:
        report.page_urls[item.page_id] = item.page.slug or "/"

    report.success = True
    return report


# --- the sync: inspect, land, archive -------------------------------------------


@dataclass(frozen=True, slots=True)
class _LandedPage:
    """A generated page and the CMS page it now lives in."""

    page: GeneratedPage
    page_id: str
    draft_version: int


async def inspect_entity(
    client: CmsClient, req: PushRequest
) -> tuple[list[ExistingPage], dict[str, str], SyncPlan]:
    """List the entity's pages, settle the site's slugs against them, and plan.

    The one path to a plan: the push runs it and `POST /api/cms/plan` shows it,
    so what the operator confirms in the drawer is what the push does. Archived
    pages are listed too — they still own their slugs, so a generated page
    matching one is restored rather than recreated (services/cms_sync.py).
    Mutates ``req.site``: its slugs become the ones it will be published under.
    """
    rows = await client.list_pages(req.entity_token, status="all")
    existing = existing_pages(rows)
    renamed = normalize_site_slugs(
        req.site,
        existing_slugs={page.slug for page in existing if not page.is_template},
    )
    return existing, renamed, plan_sync(req.site, existing)


_SEO_FIELDS = (
    "title",
    "description",
    "keywords",
    "canonical",
    "ogTitle",
    "ogDescription",
    "ogImage",
    "twitterCard",
    "structuredData",
)


def _seo_payload(seo: PageSeo | None) -> dict[str, Any]:
    """Every SEO field the page states, the unset ones as explicit nulls.

    An update has to say "no canonical" as clearly as "this canonical": a key
    left out of the PATCH keeps whatever the page had, and a stale ogImage
    under a rewritten page is a lie in every share card. Create reads a null
    as absence, so one payload serves both.
    """
    seo = seo or PageSeo()
    payload: dict[str, Any] = {name: getattr(seo, name) for name in _SEO_FIELDS}
    payload["noindex"] = bool(seo.noindex)
    return payload


def _describe_landing(plan: SyncPlan) -> str:
    created, updates = len(plan.of("create")), plan.of("update")
    restored = sum(1 for change in updates if change.restore)
    parts = [f"{created} created", f"{len(updates)} updated"]
    if restored:
        parts.append(f"{restored} restored from the archive")
    return ", ".join(parts)


async def _land_pages(
    client: CmsClient, req: PushRequest, plan: SyncPlan
) -> list[_LandedPage]:
    """Give every generated page a CMS page to live in, in plan order.

    A create is the POST a fresh entity has always had. An update is a
    metadata PATCH on the matched page (title, description, SEO), after a
    restore when it was archived — the body follows in the draft-save step,
    which is the same for both. The first change is the homepage and lands
    alone, so ``isHomepage`` is settled before anything else is created; the
    rest are independent and run concurrently under the push bound.
    """
    token = req.entity_token
    by_slug = {page.slug: page for page in req.site.pages}

    async def _create(change: PageChange) -> _LandedPage:
        page = by_slug[change.slug]
        meta = await client.create_page(
            token,
            title=page.title,
            description=page.description,
            slug=page.slug or None,
            is_homepage=page.is_homepage,
            seo=_seo_payload(page.seo),
        )
        page_id = meta.get("id")
        if not page_id:
            raise CmsApiError(500, f"Create-page response missing id: {meta}")
        return _LandedPage(page, str(page_id), int(meta.get("draftVersion") or 1))

    async def _update(change: PageChange) -> _LandedPage:
        page = by_slug[change.slug]
        page_id = change.page_id or ""
        if change.restore:
            await client.restore_page(token, page_id)
        current = await client.get_page(token, page_id)
        base = int(current.get("draftVersion") or 1)
        result = await client.update_page(
            token,
            page_id,
            base_draft_version=base,
            title=page.title,
            description=page.description,
            seo=_seo_payload(page.seo),
        )
        return _LandedPage(page, page_id, int(result.get("draftVersion") or base + 1))

    async def _land(change: PageChange) -> _LandedPage:
        return await (_create(change) if change.action == "create" else _update(change))

    changes = plan.landing
    if not changes:
        return []
    landed = [await _land(changes[0])]
    rest = changes[1:]
    if rest:
        sem = asyncio.Semaphore(_PUSH_CONCURRENCY)

        async def _bounded(change: PageChange) -> _LandedPage:
            async with sem:
                return await _land(change)

        results = await asyncio.gather(
            *(_bounded(change) for change in rest), return_exceptions=True
        )
        _raise_first_error(results)
        landed.extend(results)  # gather preserves plan order
    return landed


async def _archive_pages(
    client: CmsClient,
    req: PushRequest,
    plan: SyncPlan,
    report: PushReport,
    *,
    publishing: bool,
) -> None:
    """Take the pages the new site does not have off the live site.

    Only when the new pages went live in the same push: a push that lands as
    drafts leaves the visitor's site as it was, and removing pages while their
    replacements are still drafts would break it in the meantime — so those
    are listed for the operator to archive after publishing. Archived, never
    deleted: the page keeps its revisions and the admin can restore it. Never
    fatal — the site is in, and an archive left to finish by hand is in the
    report.
    """
    extras = plan.of("archive")
    if not extras:
        return
    paths = ", ".join(f"/{change.slug}" for change in extras)
    if not publishing:
        report.record(
            PushStep(
                name="archive_pages",
                ok=True,
                detail=(
                    f"Skipped — pushed as drafts, so {len(extras)} page(s) not in "
                    "the new site stay live"
                ),
                warning=f"Archive after publishing: {paths}",
            )
        )
        return

    sem = asyncio.Semaphore(_PUSH_CONCURRENCY)

    async def _one(change: PageChange) -> None:
        async with sem:
            await client.archive_page(req.entity_token, change.page_id or "")

    results = await asyncio.gather(
        *(_one(change) for change in extras), return_exceptions=True
    )
    failures = [
        (change, result)
        for change, result in zip(extras, results)
        if isinstance(result, BaseException)
    ]
    if failures:
        left = ", ".join(f"/{change.slug}" for change, _ in failures)
        report.record(
            PushStep(
                name="archive_pages",
                ok=False,
                error=f"{len(failures)} page(s) could not be archived ({failures[0][1]})",
                detail=f"The new pages are live; archive these in the admin: {left}",
            )
        )
        return
    report.record(
        PushStep(
            name="archive_pages",
            ok=True,
            detail=f"{len(extras)} page(s) archived: {paths}",
        )
    )


# --- media upload helpers -------------------------------------------------------


_IMAGE_MIME_MAP = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


def _brand_field(brand: Any, name: str) -> Any:
    """Read one field off `GeneratedSite.brand`, whichever shape it is in.

    The field is typed `Any`, so it is a `BrandIdentity` when the plan is built
    in-process and a plain dict when the frontend posts the same site back to
    /api/cms/push. A bare getattr silently returns None for the dict form —
    which is every real push.
    """
    if brand is None:
        return None
    if isinstance(brand, dict):
        return brand.get(name)
    return getattr(brand, name, None)


@dataclass(frozen=True, slots=True)
class MediaUpload:
    """What the media step did.

    `rewrites` maps every src/href now hosted on the CMS to its URL; `failed`
    is the IMAGE srcs that resolved to nothing (stripped by the caller);
    `reused` counts the rewrites that were files the library already held.
    """

    rewrites: dict[str, str]
    failed: set[str]
    reused: int = 0

    @property
    def uploaded(self) -> int:
        return len(self.rewrites) - self.reused

    def counts(self) -> dict[str, int]:
        return {"uploaded": self.uploaded, "reused": self.reused, "failed": len(self.failed)}


def _describe_media(media: MediaUpload) -> str:
    parts = [f"{media.uploaded} uploaded"]
    if media.reused:
        parts.append(f"{media.reused} already in the library")
    if media.failed:
        parts.append(f"{len(media.failed)} skipped")
    return ", ".join(parts)


def _media_warning(media: MediaUpload) -> str | None:
    """Every image failing is a broken pipeline, not a few dead URLs.

    One unreachable image is ordinary and the detail line mentions it in
    passing. NONE landing means the CMS refused every upload — a migration it
    has not run, a bad token, a full disk — and because a src that cannot be
    re-hosted is stripped rather than left broken, the pages simply arrive with
    no photography and nothing else in the report says why. That happened: a
    CMS missing the `media_hash` column answered 500 to all 132 uploads of a
    push, and the step still read "ok".
    """
    if media.failed and media.uploaded == 0 and media.reused == 0:
        return (
            f"The CMS accepted none of the {len(media.failed)} image(s), so these "
            "pages land with no photos. Check the CMS log — a migration it has "
            "not run is the usual cause — then push again."
        )
    return None


async def _store_bytes(
    client: CmsClient,
    entity_token: str,
    *,
    file_bytes: bytes,
    filename: str,
    content_type: str,
) -> tuple[str, bool] | None:
    """Put these bytes in the entity's library, or find them already there.

    Returns (url, reused). `reused` is a file the library already held with
    exactly these bytes — on an update re-sending a site's photography, nearly
    every image — found by asking the CMS for the sha256 before sending
    anything. The hash is of the bytes as they go on the wire, after coercion,
    which is what keeps this lookup and the CMS's own dedup in
    `MediaController::store` keyed on the same thing. A lookup that fails is
    not an upload that fails: the upload proceeds, and the CMS dedups on its
    side anyway. The upload retries once on a gateway hiccup; None when the
    CMS refused the file.
    """
    digest = hashlib.sha256(file_bytes).hexdigest()
    try:
        existing = await client.lookup_media(entity_token, digest)
    except CmsApiError as exc:
        logger.info("Media lookup failed for %s (%s); uploading", filename, exc)
        existing = None
    if existing:
        return existing, True
    for attempt in range(2):
        try:
            url = await client.upload_media(
                entity_token,
                file_bytes=file_bytes,
                filename=filename,
                content_type=content_type,
            )
            return url, False
        except CmsApiError as exc:
            if attempt == 0 and exc.status in (502, 503, 504):
                logger.warning("Upload retry for %s (%s)", filename, exc)
                continue
            logger.warning("Upload failed for %s: %s", filename, exc)
            return None
    return None


async def _upload_media(client: CmsClient, req: PushRequest) -> MediaUpload:
    """
    Walk every page's BuilderElement tree, find image srcs and document hrefs
    that aren't permanent webtree URLs, and get each hosted on the CMS — by
    upload, or by finding the same bytes already in the library
    (`_store_bytes`). Document upload failures are not included in the failed
    set — unlike a broken <img>, a link whose upload failed simply stays
    hotlinked to its original source, which still works.
    """
    rewrites: dict[str, str] = {}
    # Collect unique sources first to avoid uploading the same image twice
    # (e.g. a logo that appears on every page).
    sources: dict[str, BuilderElement] = {}  # src → first element using it (for alt)
    documents: dict[str, BuilderElement] = {}  # href → first link element using it
    for page in req.site.pages:
        for el in page.body_schema.elements:
            _collect_image_srcs(el, sources)
            _collect_document_hrefs(el, documents)
    # Also walk header/footer if present
    if req.site.header_schema:
        _collect_image_srcs(req.site.header_schema, sources)
        _collect_document_hrefs(req.site.header_schema, documents)
    if req.site.footer_schema:
        _collect_image_srcs(req.site.footer_schema, sources)
        _collect_document_hrefs(req.site.footer_schema, documents)
    # And the brand logo (it's pulled into the header but defensive doesn't hurt).
    # Skipped when the mark failed the render gate — nothing references it, so
    # uploading would just park a favicon in the tenant's media library. Read
    # through _brand_field: `brand` is a dict on the /api/cms/push path, where a
    # bare getattr made the whole block inert — gate included.
    brand = req.site.brand
    # Absent means "not stated", not "not renderable": BrandIdentity defaults it
    # True and the frontend's mirror declares it optional. Only an explicit
    # False is the gate closing.
    render_ok = _brand_field(brand, "logo_render_ok")
    if render_ok is None or render_ok:
        logo_url = _brand_field(brand, "logo_url") or _brand_field(
            brand, "logo_data_url"
        )
        if isinstance(logo_url, str):
            sources.setdefault(logo_url, _placeholder_logo_element(logo_url))

    uploadable = [src for src in sources if _needs_upload(src, req.target)]
    uploadable_docs = [
        href for href in documents if _needs_upload_document(href, req.target)
    ]
    if not uploadable and not uploadable_docs:
        return MediaUpload(rewrites, set())

    # Resolve + upload concurrently: each image/document is independent, and
    # the wait is dominated by network (download + POST). One shared download
    # client keeps connections pooled across items from the same host.
    sem = asyncio.Semaphore(_PUSH_CONCURRENCY)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as download_client:

        async def _upload_one(src: str) -> tuple[str, str, bool] | None:
            async with sem:
                try:
                    file_bytes, content_type, filename = await _resolve_to_bytes(
                        src, download_client
                    )
                except _ResolveSkip as exc:
                    logger.info("Skipping unresolvable src %s: %s", src[:80], exc)
                    return None
                # The CMS media store only accepts jpg/png (+ sanitized svg); a
                # source-served webp/avif/gif would otherwise be skipped and left
                # hotlinked to the origin. Transcode it so the published site is
                # self-contained. PIL transcode is CPU-bound — thread it off so
                # the other bounded-concurrency uploads keep moving.
                coerced = await asyncio.to_thread(
                    _coerce_to_cms_image, file_bytes, content_type, filename
                )
                if coerced is None:
                    logger.info(
                        "Skipping un-storable src %s (mime %s)", src[:80], content_type
                    )
                    return None
                file_bytes, content_type, filename = coerced
                stored = await _store_bytes(
                    client,
                    req.entity_token,
                    file_bytes=file_bytes,
                    filename=filename,
                    content_type=content_type,
                )
                return (src, *stored) if stored else None

        async def _upload_one_document(href: str) -> tuple[str, str, bool] | None:
            async with sem:
                try:
                    file_bytes, content_type, filename = await _resolve_document_to_bytes(
                        href, download_client
                    )
                except _ResolveSkip as exc:
                    logger.info("Skipping unresolvable document %s: %s", href[:80], exc)
                    return None
                stored = await _store_bytes(
                    client,
                    req.entity_token,
                    file_bytes=file_bytes,
                    filename=filename,
                    content_type=content_type,
                )
                return (href, *stored) if stored else None

        results = await asyncio.gather(
            *(_upload_one(src) for src in uploadable),
            *(_upload_one_document(href) for href in uploadable_docs),
            return_exceptions=True,
        )
    # Individual upload failures are handled per-item above (return None);
    # only truly unexpected exceptions propagate here.
    for res in results:
        if isinstance(res, BaseException):
            logger.warning("Unexpected error in media upload: %s", res)
    reused = 0
    for res in results:
        if res is not None:
            src, url, was_reused = res
            rewrites[src] = url
            reused += was_reused
    # Uploadable IMAGE srcs with no rewrite couldn't be fetched/stored (404,
    # hotlink block, un-decodable) — they're dead references the caller strips
    # so the published site never renders a broken image. Documents are
    # deliberately excluded: an un-rehosted document link still works (it
    # points at the original source), so it's left as-is, not stripped.
    failed = {src for src in uploadable if src not in rewrites}
    return MediaUpload(rewrites, failed, reused)


def _split_css_layers(value: str) -> list[str]:
    """Split a CSS value on commas at paren-depth 0 (so commas inside
    ``gradient(...)`` or ``url("data:...,...")`` are not split points)."""
    parts: list[str] = []
    buf = ""
    depth = 0
    for ch in value:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
            continue
        buf += ch
    if buf.strip():
        parts.append(buf)
    return parts


# The url() a background layer opens with. Group 1 spans the URL itself, so a
# caller can replace exactly that slice and leave the wrapper, the quoting and
# any `no-repeat center` tail as the author wrote them.
_BG_LAYER_URL_RE = re.compile(r"""^\s*url\(\s*['"]?\s*([^'")]+)""", re.IGNORECASE)


def _bg_layer_photo(layer: str) -> re.Match[str] | None:
    """The http(s) ``url(...)`` this background layer references, as a match.

    None for gradient layers and inline ``data:`` URIs (grain/mesh) — decoration
    that must stay in the schema untouched.
    """
    match = _BG_LAYER_URL_RE.match(layer)
    if match is None:
        return None
    return (
        match
        if match.group(1).strip().lower().startswith(("http://", "https://"))
        else None
    )


def _extract_bg_photo_urls(css_value: str | None) -> list[str]:
    """Real photo URLs referenced by a `background-image` value."""
    if not isinstance(css_value, str) or not css_value.strip():
        return []
    return [
        match.group(1).strip()
        for layer in _split_css_layers(css_value)
        if (match := _bg_layer_photo(layer)) is not None
    ]


def _rewrite_bg_photo_urls(css_value: str, rewrites: dict[str, str]) -> str:
    """`css_value` with each photo layer's URL swapped for its re-hosted one.

    Layer by layer, matching each URL WHOLE against the rewrite map — never
    `str.replace` over the raw value, which is order-dependent and silently
    corrupts any URL that another collected URL is a prefix of. A WordPress
    webp-conversion plugin serves `photo.jpeg` and `photo.jpeg.webp` side by side
    on one site, so both land in the map; replacing the shorter first produced
    `<cdn>/…photo.jpg` + the orphaned `.webp` — a URL the CMS never minted (the
    coercer had already normalised `.jpeg` to `.jpg`), and the longer key then
    had nothing left to match. Seven of mykiddyland's heroes published with a
    background that 404s, which a browser paints as nothing at all: no
    broken-image icon, just a blank band.

    Worse, it defeats the net: `_strip_invalid_images` runs after this and finds
    a dead reference by looking for the SOURCE url, which the mangled value no
    longer contains.

    This is the mirror of `_extract_bg_photo_urls`, which is what makes the two
    agree by construction — a URL is rewritten exactly when it was collected,
    and so exactly when it was uploaded.
    """
    changed = False
    layers: list[str] = []
    for layer in _split_css_layers(css_value):
        match = _bg_layer_photo(layer)
        new_url = rewrites.get(match.group(1).strip()) if match is not None else None
        if match is None or new_url is None:
            layers.append(layer)
            continue
        layers.append(layer[: match.start(1)] + new_url + layer[match.end(1) :])
        changed = True
    return ",".join(layers) if changed else css_value


def _collect_image_srcs(node: BuilderElement, out: dict[str, BuilderElement]) -> None:
    """Walk a BuilderElement tree, recording every uploadable image source.

    Covers both image-element ``content.src`` AND photo URLs embedded in a
    container's ``backgroundImage`` / ``background`` (hero/CTA/about photo bands).
    """
    content = node.content
    if node.type == "image" and isinstance(content, BuilderElementContent):
        src = content.src
        if isinstance(src, str) and src not in out:
            out[src] = node
    styles = node.styles or {}
    for key in ("backgroundImage", "background"):
        for url in _extract_bg_photo_urls(styles.get(key)):
            if url not in out:
                out[url] = node
    if isinstance(content, list):
        for child in content:
            _collect_image_srcs(child, out)


# webtree-cms-api's MediaController validates uploads against
# mimes:jpg,jpeg,png,webp,avif,gif,pdf,doc,docx,xls,xlsx,odt,ods — note ppt/pptx
# are NOT accepted there, even though nav_extraction.DOCUMENT_EXTENSIONS treats
# them as document links for detection purposes. A link whose extension isn't
# in this map is simply left hotlinked (see _needs_upload_document) rather than
# fetched and rejected.
_DOCUMENT_MIME_MAP = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xls": "application/vnd.ms-excel",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
}


def _document_ext(href: str) -> str:
    try:
        path = urlparse(href).path.lower()
    except ValueError:
        return ""
    return path.rsplit(".", 1)[-1] if "." in path else ""


def _collect_document_hrefs(node: BuilderElement, out: dict[str, BuilderElement]) -> None:
    """Walk a BuilderElement tree, recording every re-hostable document href.

    Mirrors _collect_image_srcs but keys on ``type == "link"`` content.href —
    a download-card button (schema_builder._build_downloads) or any other
    link element that happens to point at a document.
    """
    content = node.content
    if node.type == "link" and isinstance(content, BuilderElementContent):
        href = content.href
        if (
            isinstance(href, str)
            and _document_ext(href) in _DOCUMENT_MIME_MAP
            and href not in out
        ):
            out[href] = node
    if isinstance(content, list):
        for child in content:
            _collect_document_hrefs(child, out)


def _placeholder_logo_element(src: str) -> BuilderElement:
    return BuilderElement(
        id="logo-src-placeholder",
        name="Logo",
        type="image",
        styles={},
        content=BuilderElementContent(src=src, alt="Logo"),
    )


def _is_cms_hosted(url: str, target: CmsTarget) -> bool:
    """True when this URL already lives on the CMS we are pushing INTO, so
    re-hosting it would only duplicate the asset.

    The host comes off the target, never off settings: with a destination chosen
    per push, a settings-derived host is wrong for every push that doesn't go to
    the default CMS — silently re-uploading assets that are already there.

    The path arm is host-agnostic on purpose and does the heavy lifting: the CMS
    serves its media store from its own routes and, in production, from a
    separate asset host entirely. Nothing here ever constructs a media URL —
    `upload_media` returns the CDN URL the CMS itself minted.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if target.api_host and parsed.hostname == target.api_host:
        return True
    return "/storage/" in parsed.path or "/api/image/" in parsed.path


def _needs_upload(src: str, target: CmsTarget) -> bool:
    """True for any src we should re-host so the published site is self-contained:
    data URLs, stock photos, and external http(s) images NOT already on the CMS."""
    if src.startswith("data:image/"):
        return True
    if src.startswith("https://images.pexels.com/") or src.startswith(
        "https://picsum.photos/"
    ):
        return True
    try:
        parsed = urlparse(src)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    # External/scraped photo → re-host it on the CMS.
    return not _is_cms_hosted(src, target)


def _needs_upload_document(href: str, target: CmsTarget) -> bool:
    """True for an absolute http(s) document URL not already on the CMS."""
    try:
        parsed = urlparse(href)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if _is_cms_hosted(href, target):
        return False
    return _document_ext(href) in _DOCUMENT_MIME_MAP


class _ResolveSkip(Exception):
    pass


async def _assert_fetchable(url: str) -> None:
    """Refuse a src pointing at a private/internal host (SECURITY.md §2).

    Push time is a fetch boundary like any other: every URL here came from
    outside — a scraped page's markup, or now markup the user pasted straight
    in, which makes it directly attacker-chosen rather than requiring a site
    they control to be scraped first. A refusal is a `_ResolveSkip`, so one bad
    src is dropped from the tree by `_strip_invalid_images` exactly like an
    unreachable one; it never fails the push.
    """
    try:
        await assert_public_url(url)
    except UnsafeUrlError as exc:
        raise _ResolveSkip(str(exc)) from exc


def _brand_field(brand: Any, name: str) -> Any:
    """Read one field off `GeneratedSite.brand`, whichever shape it is in.

    The field is typed `Any`, so it is a `BrandIdentity` when the plan is built
    in-process and a plain dict when the frontend posts the same site back to
    /api/cms/push. A bare getattr silently returns None for the dict form —
    which is every real push.
    """
    if brand is None:
        return None
    if isinstance(brand, dict):
        return brand.get(name)
    return getattr(brand, name, None)


async def _push_whatsapp_widget(
    client: CmsClient, req: PushRequest, report: PushReport, *, first_push: bool
) -> None:
    """Push the discovered WhatsApp click-to-chat config, if there is one.

    First push only. A site that already has pages has an owner who may have
    set the button up — or switched it off — in the admin since, and an update
    is pages and design; their settings are kept as they are.

    Nothing to push is a success, not a skip with an excuse: most sources do not
    publish a WhatsApp number, and the country code is never inferred from a
    plain phone number.
    """
    if not first_push:
        report.record(
            PushStep(
                name="whatsapp_widget",
                ok=True,
                detail="Skipped — the site's WhatsApp settings are kept as they are",
            )
        )
        return

    widget = req.site.whatsapp_widget

    if not widget:
        report.record(
            PushStep(
                name="whatsapp_widget",
                ok=True,
                detail="Skipped (no WhatsApp number found on the source)",
            )
        )
        return

    try:
        await client.update_whatsapp_widget(req.entity_token, widget)
        report.record(
            PushStep(
                name="whatsapp_widget",
                ok=True,
                detail=f"Chat button enabled for +{widget.get('phone')}",
            )
        )
    except CmsApiError as exc:
        report.record(
            PushStep(
                name="whatsapp_widget",
                ok=False,
                error=str(exc),
                detail="Site is in; set the WhatsApp button manually in Site settings.",
            )
        )


async def _push_favicon(
    client: CmsClient, req: PushRequest, report: PushReport
) -> None:
    """Send the source site's icon to the CMS, as the entity's favicon.

    Everything here is already built: `_resolve_to_bytes` fetches a URL or
    decodes a data: URI, and `_coerce_to_favicon` normalizes whatever came back
    to a still PNG — including the `.ico` a `<link rel="icon">` most often
    points at, and the animated WebP that a brand logo occasionally is.

    Never fatal. The pages are pushed by this point, and an icon is a thing the
    owner can set in Site settings; failing the whole push over one would be a
    poor trade.
    """
    if not req.push_favicon:
        report.record(PushStep(name="favicon", ok=True, detail="Skipped (per request)"))
        return

    src = _brand_field(req.site.brand, "favicon_url")

    if not isinstance(src, str) or not src.strip():
        report.record(
            PushStep(name="favicon", ok=True, detail="Skipped — source declared none")
        )
        return

    try:
        file_bytes, content_type, filename = await _resolve_to_bytes(src)
        coerced = await asyncio.to_thread(
            _coerce_to_favicon, file_bytes, content_type, filename
        )
        if coerced is None:
            report.record(
                PushStep(
                    name="favicon",
                    ok=True,
                    detail=f"Skipped — unreadable image ({content_type})",
                )
            )
            return

        file_bytes, content_type, filename = coerced
        favicon_url = await client.set_entity_favicon(
            req.entity_token,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
        )
        report.record(
            PushStep(
                name="favicon",
                ok=True,
                detail="Site icon set",
                data={"favicon_url": favicon_url} if favicon_url else {},
            )
        )
    except (CmsApiError, _ResolveSkip, httpx.HTTPError) as exc:
        report.record(
            PushStep(
                name="favicon",
                ok=False,
                error=str(exc),
                detail="Site icon not set. Upload one in Site settings.",
            )
        )


async def _resolve_to_bytes(
    src: str, client: httpx.AsyncClient | None = None
) -> tuple[bytes, str, str]:
    """Turn a src into (bytes, content_type, filename) ready for /api/file/add."""
    if src.startswith("data:"):
        return _decode_data_url(src)
    await _assert_fetchable(src)
    # https URL: fetch (with the caller's pooled client when provided)
    try:
        if client is not None:
            resp = await client.get(src)
        else:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True) as one_shot:
                resp = await one_shot.get(src)
        if resp.status_code >= 400:
            raise _ResolveSkip(f"http {resp.status_code}")
    except httpx.HTTPError as exc:
        raise _ResolveSkip(str(exc)) from exc
    content_type = (resp.headers.get("content-type") or "").split(";", 1)[0].lower()
    if content_type not in {"image/png", "image/jpeg"}:
        # Fall back: try infer from filename
        path = urlparse(src).path.lower()
        ext = path.rsplit(".", 1)[-1] if "." in path else ""
        content_type = _IMAGE_MIME_MAP.get(ext, content_type or "image/jpeg")
    filename = _filename_from_url(src) or ("image." + content_type.split("/")[-1])
    return resp.content, content_type, filename


async def _resolve_document_to_bytes(
    href: str, client: httpx.AsyncClient
) -> tuple[bytes, str, str]:
    """Turn a document href into (bytes, content_type, filename) for /api/file/add.

    Unlike _resolve_to_bytes there is no transcoding — documents are opaque
    binary files. An extension outside _DOCUMENT_MIME_MAP is a _ResolveSkip so
    the link stays hotlinked rather than uploaded as something the CMS
    validator would reject.
    """
    await _assert_fetchable(href)
    try:
        resp = await client.get(href)
        if resp.status_code >= 400:
            raise _ResolveSkip(f"http {resp.status_code}")
    except httpx.HTTPError as exc:
        raise _ResolveSkip(str(exc)) from exc
    ext = _document_ext(href)
    content_type = _DOCUMENT_MIME_MAP.get(ext)
    if content_type is None:
        raise _ResolveSkip(f"unsupported document extension: {ext!r}")
    filename = _filename_from_url(href) or f"document.{ext}"
    return resp.content, content_type, filename


# Formats the CMS media store accepts natively (webtree-cms-api
# MediaController::store + BackendController thumbnailing; svg via its dedicated
# SvgSanitizer path). These pass through untouched so webp/avif keep their size
# advantage. The API routes validation on the uploaded filename's extension, so
# we always normalize the extension to the canonical one for the format.
_CMS_NATIVE_EXT_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "webp": "image/webp",
    "avif": "image/avif",
    "gif": "image/gif",
    "svg": "image/svg+xml",
}
_CMS_NATIVE_MIME_TO_EXT = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "image/avif": "avif",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}


def _native_cms_ext(content_type: str, filename: str) -> str | None:
    """Canonical CMS-storable extension for these bytes, or None if the format
    isn't natively accepted. Trusts the content-type first, then the filename
    extension (servers often mislabel svg/avif as text/plain or octet-stream)."""
    if content_type in _CMS_NATIVE_MIME_TO_EXT:
        return _CMS_NATIVE_MIME_TO_EXT[content_type]
    fext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if fext in _CMS_NATIVE_EXT_TO_MIME:
        return "jpg" if fext == "jpeg" else fext
    return None


_IMAGE_MAX_DIM = 2500


def _coerce_to_cms_image(
    file_bytes: bytes, content_type: str, filename: str
) -> tuple[bytes, str, str] | None:
    """Return (bytes, mime, filename) the CMS will store. None ⇒ not a usable image.

    - jpg/png/webp/avif/gif/svg pass through untouched (extension normalized).
      Oversized raster images (any dimension > 2500px) are downscaled to JPEG
      to keep upload times and CMS storage reasonable.
    - anything else the CMS can't store (bmp/tiff/ico/…) is transcoded with PIL:
      PNG when it carries transparency (logos/icons), else JPEG (photos).
    """
    from io import BytesIO

    from PIL import Image

    base = (filename.rsplit(".", 1)[0] if "." in filename else filename) or "image"

    ext = _native_cms_ext(content_type, filename)
    if ext is not None:
        # SVG / GIF always pass through (vector / animated).
        if ext in ("svg", "gif"):
            return file_bytes, _CMS_NATIVE_EXT_TO_MIME[ext], f"{base}.{ext}"
        # Raster native formats: downscale if oversized.
        try:
            with Image.open(BytesIO(file_bytes)) as img:
                if max(img.size) > _IMAGE_MAX_DIM:
                    converted = img.convert("RGB")
                    converted.thumbnail((_IMAGE_MAX_DIM, _IMAGE_MAX_DIM))
                    out = BytesIO()
                    converted.save(out, format="JPEG", quality=85)
                    return out.getvalue(), "image/jpeg", f"{base}.jpg"
        except Exception:  # noqa: BLE001
            pass
        return file_bytes, _CMS_NATIVE_EXT_TO_MIME[ext], f"{base}.{ext}"

    try:
        with Image.open(BytesIO(file_bytes)) as img:
            img.load()
            has_alpha = img.mode in ("RGBA", "LA") or (
                img.mode == "P" and "transparency" in img.info
            )
            if max(img.size) > _IMAGE_MAX_DIM:
                img.thumbnail((_IMAGE_MAX_DIM, _IMAGE_MAX_DIM))
            out = BytesIO()
            if has_alpha:
                img.convert("RGBA").save(out, format="PNG")
                return out.getvalue(), "image/png", f"{base}.png"
            img.convert("RGB").save(out, format="JPEG", quality=85)
            return out.getvalue(), "image/jpeg", f"{base}.jpg"
    except Exception:  # noqa: BLE001 — unsupported/corrupt bytes ⇒ leave hotlinked
        return None


# The CMS re-encodes a favicon to a 192px PNG, so anything larger is bytes we
# upload and it discards.
_FAVICON_MAX_DIM = 512


def _coerce_to_favicon(
    file_bytes: bytes, content_type: str, filename: str
) -> tuple[bytes, str, str] | None:
    """Return (bytes, mime, filename) the CMS favicon endpoint will accept.

    Not `_coerce_to_cms_image`: that one is built for the media library, where
    passing a format through untouched is the point — webp and avif keep their
    size advantage and nothing re-encodes them. The favicon endpoint is the
    opposite case. It decodes what it is sent with GD, whose codecs depend on
    how the deployed PHP was built, and a source site's declared icon is exactly
    where the awkward formats turn up: an animated WebP logo, an AVIF, a CMYK
    JPEG. Every one of those is a 422 that fails the step for no good reason,
    when Pillow is right here and can hand over a still PNG the server is
    certain to read.

    SVG is the exception and passes through: the CMS sanitizes and stores it as
    a vector, which is sharper than any raster we could rasterize it into — and
    Pillow could not rasterize it anyway.
    """
    from io import BytesIO

    from PIL import Image

    if _native_cms_ext(content_type, filename) == "svg":
        return file_bytes, "image/svg+xml", "favicon.svg"

    try:
        with Image.open(BytesIO(file_bytes)) as img:
            # An .ico opens at its largest entry; an animated GIF/WebP opens at
            # its first frame, which is the one a favicon should be.
            frame = img.convert("RGBA")
            if max(frame.size) > _FAVICON_MAX_DIM:
                frame.thumbnail((_FAVICON_MAX_DIM, _FAVICON_MAX_DIM))
            out = BytesIO()
            frame.save(out, format="PNG")
            return out.getvalue(), "image/png", "favicon.png"
    except Exception:  # noqa: BLE001 — not a decodable image; the step reports it
        return None


# RFC 2397 allows any number of `;parameter` segments between the media type and
# the comma, and `;base64` is only ONE of them. The previous pattern accepted a
# bare `;base64` and nothing else, so it failed to match at all on the
# percent-encoded SVGs this repo generates — `media.monogram_avatar_url` and
# `media._placeholder_photo` both emit `data:image/svg+xml;utf8,…`. That is a
# _ResolveSkip, which lands the src in `failed` and has _strip_invalid_images
# delete the element: every monogram avatar and every gradient placeholder
# vanished from the published site while rendering correctly in the preview.
_DATA_URL_RE = re.compile(
    r"data:(?P<ct>[^;,]*)(?P<params>(?:;[^;,]*)*),(?P<data>.*)", re.DOTALL
)


def _decode_data_url(src: str) -> tuple[bytes, str, str]:
    """Decode a data: URI into (bytes, content_type, filename).

    Handles both encodings the spec allows: `;base64` payloads, and the default
    percent-encoded text form used by our own SVG generators (`icons.icon_data_url`
    writes no parameter at all, `monogram_avatar_url` writes `;utf8`). Only the
    `;base64` token selects base64 — any other parameter is a charset hint.
    """
    m = _DATA_URL_RE.match(src)
    if not m:
        raise _ResolveSkip("malformed data URL")
    content_type = m.group("ct") or "image/png"
    params = (m.group("params") or "").lower()
    raw = m.group("data") or ""
    if ";base64" in params:
        try:
            decoded = base64.b64decode(raw)
        except Exception as exc:  # noqa: BLE001
            raise _ResolveSkip(f"base64 decode failed: {exc}") from exc
    else:
        # Percent-encoded text (SVG markup). unquote, not unquote_plus: `quote`
        # never writes `+` for a space, so treating it as one would corrupt any
        # payload that legitimately contains a plus.
        try:
            decoded = unquote(raw).encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            raise _ResolveSkip(f"data URL decode failed: {exc}") from exc
    ext = content_type.split("/")[-1] if "/" in content_type else "png"
    if ext == "svg+xml":
        ext = "svg"
    filename = f"upload.{ext}"
    return decoded, content_type, filename


def _filename_from_url(src: str) -> str | None:
    try:
        path = urlparse(src).path
    except ValueError:
        return None
    name = path.rsplit("/", 1)[-1]
    return name or None


# --- src-rewrite pass -----------------------------------------------------------


def _apply_src_rewrites(site: GeneratedSite, rewrites: dict[str, str]) -> None:
    """Walk every BuilderElement tree on the site + rewrite image srcs and
    document link hrefs in-place."""
    if not rewrites:
        return
    for page in site.pages:
        for el in page.body_schema.elements:
            _rewrite_srcs(el, rewrites)
    if site.header_schema:
        _rewrite_srcs(site.header_schema, rewrites)
    if site.footer_schema:
        _rewrite_srcs(site.footer_schema, rewrites)


def _strip_invalid_images(site: GeneratedSite, failed: set[str]) -> int:
    """Remove image elements + background layers whose src couldn't be re-hosted
    (dead/404 source URL), so nothing renders as a broken image. In-place;
    returns how many references were removed."""
    if not failed:
        return 0
    removed = 0

    def _is_dead_image(node: BuilderElement) -> bool:
        content = node.content
        return (
            node.type == "image"
            and isinstance(content, BuilderElementContent)
            and isinstance(content.src, str)
            and content.src in failed
        )

    def _strip_bg(node: BuilderElement) -> None:
        nonlocal removed
        styles = node.styles or {}
        for key in ("backgroundImage", "background"):
            value = styles.get(key)
            if not isinstance(value, str) or not any(u in value for u in failed):
                continue
            kept = [
                layer.strip()
                for layer in _split_css_layers(value)
                if not any(u in failed for u in _extract_bg_photo_urls(layer))
            ]
            new_value = ", ".join(l for l in kept if l)
            if new_value:
                styles[key] = new_value
            else:
                styles.pop(key, None)
            removed += 1

    def _prune(children: list[BuilderElement]) -> list[BuilderElement]:
        nonlocal removed
        kept: list[BuilderElement] = []
        for child in children:
            if _is_dead_image(child):
                removed += 1
                continue
            _strip_bg(child)
            if isinstance(child.content, list):
                child.content = _prune(child.content)
            kept.append(child)
        return kept

    for page in site.pages:
        page.body_schema.elements = _prune(page.body_schema.elements)
    for root in (site.header_schema, site.footer_schema):
        if root is None:
            continue
        _strip_bg(root)
        if isinstance(root.content, list):
            root.content = _prune(root.content)
    return removed


def _rewrite_srcs(node: BuilderElement, rewrites: dict[str, str]) -> None:
    content = node.content
    if node.type == "image" and isinstance(content, BuilderElementContent):
        src = content.src
        if isinstance(src, str) and src in rewrites:
            content.src = rewrites[src]
    elif node.type == "link" and isinstance(content, BuilderElementContent):
        href = content.href
        if isinstance(href, str) and href in rewrites:
            content.href = rewrites[href]
    # Rewrite photo URLs embedded in background styles, preserving the gradient
    # overlay and the url() wrapper. One layer-wise pass per value, whatever the
    # size of the rewrite map — see _rewrite_bg_photo_urls.
    styles = node.styles or {}
    for key in ("backgroundImage", "background"):
        value = styles.get(key)
        if not isinstance(value, str):
            continue
        new_value = _rewrite_bg_photo_urls(value, rewrites)
        if new_value != value:
            styles[key] = new_value
    if isinstance(content, list):
        for child in content:
            _rewrite_srcs(child, rewrites)


# --- CMS content types: template pages + migrated article/event entries ----------


_DEFAULT_ARTICLE_CATEGORY = "News"

# Events with a start but no end get this duration so publishing (which
# requires both) still works.
_DEFAULT_EVENT_DURATION = timedelta(hours=2)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

# CMS previewImage dimension caps (PostController allows 5500, EventController 1500).
_ARTICLE_IMAGE_MAX_DIM = 5400
_EVENT_IMAGE_MAX_DIM = 1400


def _iter_elements(nodes: list[BuilderElement]):
    stack = list(nodes)
    while stack:
        el = stack.pop()
        yield el
        if isinstance(el.content, list):
            stack.extend(el.content)


def _site_list_sources(site: GeneratedSite) -> set[str]:
    """Which dynamic list elements the generated pages carry ({'articles','events'})."""
    found: set[str] = set()
    for page in site.pages:
        for el in _iter_elements(page.body_schema.elements):
            if el.type == "articlesList":
                found.add("articles")
            elif el.type == "eventsList":
                found.add("events")
    return found


def _prepare_preview_image(
    file_bytes: bytes, filename: str, *, max_dim: int
) -> tuple[str, bytes, str] | None:
    """Coerce raw image bytes into a previewImage the CMS accepts.

    jpg/png within the dimension cap pass through; everything else (webp, avif,
    oversized) is converted/downscaled to JPEG. None ⇒ bytes aren't an image.
    """
    from io import BytesIO

    from PIL import Image

    try:
        with Image.open(BytesIO(file_bytes)) as img:
            fmt = (img.format or "").upper()
            needs_resize = max(img.size) > max_dim
            needs_convert = fmt not in ("JPEG", "PNG")
            base = filename.rsplit(".", 1)[0] or "preview"
            if not needs_resize and not needs_convert:
                ext = "jpg" if fmt == "JPEG" else "png"
                mime = "image/jpeg" if fmt == "JPEG" else "image/png"
                return (f"{base}.{ext}", file_bytes, mime)
            converted = img.convert("RGB")
            if needs_resize:
                converted.thumbnail((max_dim, max_dim))
            out = BytesIO()
            converted.save(out, format="JPEG", quality=85)
            return (f"{base}.jpg", out.getvalue(), "image/jpeg")
    except Exception:  # noqa: BLE001 — corrupt/unsupported bytes ⇒ try the next candidate
        return None


async def _entry_preview_image(
    image_url: str | None,
    fallback_srcs: list[str],
    download_client: httpx.AsyncClient,
    *,
    max_dim: int,
) -> tuple[str, bytes, str] | None:
    """(filename, bytes, mime) for an entry's cover — entry image first, then
    site imagery, None when nothing resolvable (caller downgrades to draft)."""
    for candidate in [image_url, *fallback_srcs]:
        if not candidate:
            continue
        try:
            file_bytes, _content_type, filename = await _resolve_to_bytes(
                candidate, download_client
            )
        except _ResolveSkip:
            continue
        prepared = await asyncio.to_thread(
            _prepare_preview_image, file_bytes, filename, max_dim=max_dim
        )
        if prepared:
            return prepared
    return None


def _is_slug_conflict(exc: CmsApiError) -> bool:
    body = exc.response_body if isinstance(exc.response_body, dict) else {}
    message = body.get("message")
    return isinstance(message, dict) and "slug" in message


def _fallback_image_srcs(site: GeneratedSite, limit: int = 3) -> list[str]:
    sources: dict[str, BuilderElement] = {}
    for page in site.pages:
        for el in page.body_schema.elements:
            _collect_image_srcs(el, sources)
    return [
        src
        for src in sources
        if src.startswith(("http://", "https://", "data:image/"))
    ][:limit]


def _wanted_templates(site: GeneratedSite, cols: ContentCollections) -> list[str]:
    """The templateFor pages the site's list elements and migrated entries need."""
    sources = _site_list_sources(site)
    wanted: list[str] = []
    if "articles" in sources or cols.has_articles:
        wanted += ["article", "articleListing"]
    if "events" in sources or cols.has_events:
        # The public /events route renders from the eventListing template and
        # 404s without one.
        wanted += ["event", "eventListing"]
    return wanted


async def _ensure_template_pages(
    client: CmsClient,
    req: PushRequest,
    report: PushReport,
    existing: list[ExistingPage],
) -> None:
    """Create the article/event template pages the site needs and lacks.

    Same contract as the builder's ensureTemplatePages — a page whose
    templateFor marks it as the detail/listing layout for that content type.
    Created blank; the builder renders its default layout. One the entity
    already has is kept as it is, however it has been edited since: it is the
    owner's article/event design, not this site's. Never raises.
    """
    wanted = _wanted_templates(req.site, req.collections or ContentCollections())
    # Archived templates are the owner's explicit "off", so a reset leaves them
    # be; every live one is reset, whether or not the new site needs its kind —
    # the articles and events it renders are kept, so its design follows too.
    replaceable = (
        [page for page in existing if page.is_template and not page.archived]
        if req.replace_templates
        else []
    )
    if not wanted and not replaceable:
        return
    have = {page.template_for for page in existing if page.template_for}
    taken = {page.slug for page in existing}
    # A page the generated site does not claim, sitting on a template's own
    # slug, IS that template — read back off the live site's template route
    # before the crawler learned to skip it. Adopt it rather than leave it
    # beside a suffixed twin.
    claimed = {page.slug for page in req.site.pages}
    adoptable = {
        page.slug: page
        for page in existing
        if not page.is_template and page.slug not in claimed
    }
    try:
        created = adopted = reset = 0
        for page in replaceable:
            await _reset_template(client, req.entity_token, page)
            reset += 1
        for kind in wanted:
            if kind in have:
                continue
            title, description, slug = TEMPLATE_PAGE_DEFAULTS[kind]
            stale = adoptable.get(slug)
            if stale is not None:
                await _adopt_as_template(
                    client, req.entity_token, stale,
                    kind=kind, title=title, description=description,
                )
                adopted += 1
                continue
            slug = _free_template_slug(slug, taken)
            await client.create_page(
                req.entity_token,
                title=title,
                description=description,
                slug=slug,
                template_for=kind,
            )
            taken.add(slug)
            created += 1
        report.record(
            PushStep(
                name="template_pages",
                ok=True,
                detail=_describe_templates(created, adopted, reset),
                data={
                    "templates": wanted,
                    "created": created,
                    "adopted": adopted,
                    "reset": reset,
                },
                warning=_template_draft_warning(created + adopted + reset),
            )
        )
    except CmsApiError as exc:
        report.record(
            PushStep(
                name="template_pages",
                ok=False,
                error=str(exc),
                detail="Template pages failed — the builder auto-creates them on first open.",
            )
        )


def _describe_templates(created: int, adopted: int, reset: int = 0) -> str:
    parts = [f"{created} template page(s) created"]
    if adopted:
        parts.append(f"{adopted} adopted from a page already on that slug")
    if reset:
        parts.append(f"{reset} reset to the new design")
    return ", ".join(parts)


def _template_draft_warning(touched: int) -> str | None:
    """What the operator still has to do for a template the push wrote.

    Every template this step writes is left as a BLANK DRAFT, deliberately.
    The layout is the builder's to build: on opening a blank template it lays
    one out from the site's own homepage hero (SET_UP_CMS_TEMPLATE →
    createCmsTemplateSections), which is exactly "the new design", and
    reproducing that here would mean porting the builder's template and
    hero-style code. Publishing the blank draft instead would put an empty
    page on every article, so the live site keeps whatever template it was
    already serving until the owner publishes the rebuilt one.
    """
    if not touched:
        return None
    return (
        f"{touched} article/event template(s) are blank drafts. Open each in the "
        "builder, which lays it out in the new design, then publish it — the live "
        "site keeps its current template until you do."
    )


async def _adopt_as_template(
    client: CmsClient,
    entity_token: str,
    page: ExistingPage,
    *,
    kind: str,
    title: str,
    description: str,
) -> None:
    """Make the page already sitting on a template's slug BE that template.

    Cheaper and tidier than creating a suffixed twin beside it: one page, at
    the slug it belongs on, and nothing stale left for the operator to find.

    Its body is reset to the one the CMS gives a page it has just created,
    because an article renders from its template's `bodySchema`
    (webtree-public ContentDetail.vue) — adopting a page that still carries a
    hero and a CTA would publish every article as a hero and a CTA. What is
    lost is the generated copy of a page that was never real; its published
    revisions are untouched, so the CMS can still show what was there.

    A restore first when it is archived: `ensurePageIsEditable` refuses to
    touch an archived page, and it is the caller's job to know that the page
    might be one.
    """
    base = await _editable_draft_version(client, entity_token, page)
    result = await client.update_page(
        entity_token,
        page.id,
        base_draft_version=base,
        title=title,
        description=description,
        seo={},
        template_for=kind,
    )
    await _save_blank_body(
        client, entity_token, page.id, int(result.get("draftVersion") or base + 1)
    )


async def _reset_template(client: CmsClient, entity_token: str, page: ExistingPage) -> None:
    """Put an existing template back to a blank draft, for the builder to lay
    out again in the new design (see _template_draft_warning)."""
    base = await _editable_draft_version(client, entity_token, page)
    await _save_blank_body(client, entity_token, page.id, base)


async def _editable_draft_version(
    client: CmsClient, entity_token: str, page: ExistingPage
) -> int:
    """The page's current draft version, restoring it first when archived.

    `ensurePageIsEditable` refuses an archived page, and every write that
    follows is a compare-and-swap on the draft version the list endpoint does
    not carry.
    """
    if page.archived:
        await client.restore_page(entity_token, page.id)
    current = await client.get_page(entity_token, page.id)
    return int(current.get("draftVersion") or 1)


async def _save_blank_body(
    client: CmsClient, entity_token: str, page_id: str, base_draft_version: int
) -> None:
    await client.save_page_draft(
        entity_token,
        page_id,
        base_draft_version=base_draft_version,
        body_schema=BLANK_TEMPLATE_BODY,
    )


def _free_template_slug(slug: str, taken: set[str]) -> str:
    """`slug`, suffixed until no page on this entity holds it.

    A template page is found by its `templateFor`, never by its slug — the CMS
    routes article and event rendering through PublishedTemplateResolver — so a
    suffix costs the site nothing.

    It has to exist because a slug is owned by ANY page holding it, archived
    ones included (`PageSlugService::slugExists` has no status filter). A site
    pushed before the crawler learned to skip the platform's own routes carries
    a stale content page at exactly `article-template`, read back off its own
    live site. Creating the real template then fails SLUG_ALREADY_EXISTS every
    time — on production only, because only production has that history.
    """
    if slug not in taken:
        return slug
    # Bounded by construction: one of `len(taken) + 2` candidates is free.
    return next(
        candidate
        for n in range(2, len(taken) + 3)
        if (candidate := f"{slug}-{n}") not in taken
    )


async def _push_content_entries(
    client: CmsClient, req: PushRequest, report: PushReport, *, first_push: bool
) -> None:
    """Create the migrated article/event entries. Never raises; every failure
    is recorded as a non-fatal step (the site itself is pushed).

    First push only. An update keeps the site's articles and events exactly as
    they are — re-creating the migrated ones would duplicate every post the
    owner already has (the slug retry would file them as `post-2`).
    """
    cols = req.collections or ContentCollections()
    if not cols.has_articles and not cols.has_events:
        return
    if not first_push:
        report.record(
            PushStep(
                name="content_entries",
                ok=True,
                detail="Skipped — the site's articles and events are kept as they are",
            )
        )
        return

    # Published articles must reference an existing category.
    category_slug: str | None = None
    if cols.has_articles:
        try:
            category_slug = await client.create_category(
                req.entity_token, title=_DEFAULT_ARTICLE_CATEGORY
            )
        except CmsApiError as exc:
            report.record(
                PushStep(
                    name="content_entries",
                    ok=False,
                    error=f"Category creation failed — articles skipped: {exc}",
                )
            )
            if not cols.has_events:
                return

    now_ms = int(_utcnow().timestamp() * 1000)
    published = 0
    drafted = 0
    failures: list[str] = []
    fallback_srcs = _fallback_image_srcs(req.site)

    async def _create_with_slug_retry(create, base_slug: str) -> None:
        slug = base_slug
        for attempt in range(4):
            try:
                await create(slug)
                return
            except CmsApiError as exc:
                if _is_slug_conflict(exc) and attempt < 3:
                    slug = f"{base_slug}-{attempt + 2}"
                    continue
                raise

    with stage("push_content_entries"):
        sem = asyncio.Semaphore(_PUSH_CONCURRENCY)

        async def _push_article(article, dl) -> tuple[int, int, str | None]:
            """Create one migrated article → (published Δ, drafted Δ, failure)."""
            async with sem:
                image = await _entry_preview_image(
                    article.image_url, fallback_srcs, dl, max_dim=_ARTICLE_IMAGE_MAX_DIM
                )
                publish = image is not None  # published posts require a previewImage
                published_ms = (
                    int(article.published_at.timestamp() * 1000)
                    if article.published_at
                    else now_ms
                )

                async def _create_article(slug: str) -> None:
                    await client.create_article(
                        req.entity_token,
                        title=article.title,
                        slug=slug,
                        excerpt=article.excerpt,
                        body_html=article.body_html,
                        category_slugs=[category_slug],
                        published_at_ms=published_ms,
                        image=image,
                        publish=publish,
                    )

                try:
                    await _create_with_slug_retry(_create_article, article.slug)
                    return (1, 0, None) if publish else (0, 1, None)
                except CmsApiError as exc:
                    return 0, 0, f"article {article.slug}: {exc}"

        async def _push_event(event, dl) -> tuple[int, int, str | None]:
            """Create one migrated event → (published Δ, drafted Δ, failure)."""
            async with sem:
                image = await _entry_preview_image(
                    event.image_url, fallback_srcs, dl, max_dim=_EVENT_IMAGE_MAX_DIM
                )
                start = event.start
                end = event.end or (start + _DEFAULT_EVENT_DURATION if start else None)
                location = event.location or "To be announced"
                # Publishing requires location + start + end + previewImage.
                publish = bool(image and start)

                async def _create_event(slug: str) -> None:
                    await client.create_event(
                        req.entity_token,
                        title=event.title,
                        slug=slug,
                        excerpt=event.excerpt,
                        body_html=event.body_html,
                        location=location,
                        start_ms=int(start.timestamp() * 1000) if start else None,
                        end_ms=int(end.timestamp() * 1000) if end else None,
                        published_at_ms=now_ms,
                        image=image,
                        publish=publish,
                    )

                try:
                    await _create_with_slug_retry(_create_event, event.slug)
                    return (1, 0, None) if publish else (0, 1, None)
                except CmsApiError as exc:
                    return 0, 0, f"event {event.slug}: {exc}"

        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as dl:
            # Entries are mutually independent (each pays an image download +
            # resize + a CMS create), so fan them out under the same bounded
            # concurrency as the other push steps instead of one at a time.
            # gather preserves submission order → deterministic failure list.
            outcomes = await asyncio.gather(
                *[_push_article(a, dl) for a in (cols.articles if category_slug else [])],
                *[_push_event(e, dl) for e in cols.events],
            )
        for pub_delta, draft_delta, failure in outcomes:
            published += pub_delta
            drafted += draft_delta
            if failure:
                failures.append(failure)

    detail = f"{published} published, {drafted} draft(s)"
    if failures:
        detail += f", {len(failures)} failed"
    report.record(
        PushStep(
            name="content_entries",
            ok=published + drafted > 0 or not failures,
            detail=detail,
            data={"failures": failures} if failures else {},
            error="; ".join(failures) if failures and published + drafted == 0 else None,
        )
    )
