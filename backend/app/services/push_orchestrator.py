"""
Push a GeneratedSite into a webtree CMS entity.

Greenfield-only contract (per agreed scope): refuses to push when the entity
already has pages, unless caller passes ``force_overwrite=True``.

Steps (in order):
  1. Auth — JWT login on the CMS API
  2. Empty-entity guard — list pages, refuse if non-empty
  3. Media upload — walk every page's BuilderElement tree, find image srcs
     that are data:image/... or external URLs, upload to /api/file/add and
     rewrite to CDN URLs in-place
  4. Create pages — POST /pages for each generated page, capture pageId +
     draftVersion. Homepage goes first.
  5. Read first page's builder payload — captures layout.versionId for the
     save-layout step (layout is entity-scoped — write it once).
  6. Save layout — wrap header/footer + emit menus + PUT once on the homepage
  7. Save drafts — for every created page, PUT /draft with its bodySchema
  8. Builder styles — mint launch-code session + PUT /builder/styles
  9. (Optional) Publish — POST /publish for each page
 10. Content types — article/event/articleListing template pages (templateFor)
     + migrated article/event entries from the source site (non-fatal)

Each step's outcome is appended to PushReport so the UI can show a per-step
status table. Failures abort the push but the report carries everything done
up to the failure point (useful for diagnostics + future resume support).
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.models.builder_schema import (
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
)
from app.models.content_blocks import ContentCollections
from app.services.cms_client import CmsApiError, CmsClient
from app.services.menu_builder import build_menus, wrap_footer, wrap_header
from app.services.timing import stage

logger = logging.getLogger(__name__)


# --- public report types --------------------------------------------------------


@dataclass
class PushStep:
    name: str
    ok: bool = False
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


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
    force_overwrite: bool = False
    push_builder_styles: bool = True
    # When True, create a brand-new entity (owned by the logged-in user) before
    # pushing, and ignore `entity_token`. The created entity is empty so the
    # greenfield guard always passes.
    create_entity: bool = False
    new_entity_name: str | None = None
    new_entity_url: str | None = None
    # Blog posts / events extracted from the source site (content_collections);
    # pushed as real CMS article/event entries after the pages land.
    collections: ContentCollections | None = None


# --- slug normalization ---------------------------------------------------------


_SLUG_SEP_RE = re.compile(r"[^a-z0-9]+")


def _cms_safe_slug(raw: str) -> str:
    """Coerce any string into the CMS slug format ^[a-z0-9]+(?:-[a-z0-9]+)*$.

    Lowercase, collapse every run of non-alphanumerics to a single hyphen,
    trim hyphens, cap at the CMS's 160-char limit. "services/web-design" →
    "services-web-design"; "" / junk → "".
    """
    s = _SLUG_SEP_RE.sub("-", (raw or "").strip().lower()).strip("-")
    return s[:160].strip("-")


def _normalize_site_slugs(site: GeneratedSite) -> dict[str, str]:
    """Flatten every slug to the CMS's flat kebab-case format, keeping
    parent_slug, page_tree, and all baked nav hrefs consistent.

    Returns the {old_slug: new_slug} map of slugs that actually changed.
    Mutates `site` in place.
    """
    slug_map: dict[str, str] = {}
    used: set[str] = set()
    for page in site.pages:
        old = page.slug or ""
        if page.is_homepage:
            new = ""
        else:
            new = _cms_safe_slug(old) or _cms_safe_slug(page.title) or "page"
            base, n = new, 2
            while new in used:
                new = f"{base}-{n}"
                n += 1
            used.add(new)
        slug_map[old] = new
        page.slug = new

    # parent_slug references point at a parent's (old) slug — remap them.
    for page in site.pages:
        if page.parent_slug:
            page.parent_slug = (
                slug_map.get(page.parent_slug)
                or _cms_safe_slug(page.parent_slug)
                or None
            )

    # page_tree mirrors `pages` — keep node slugs in lock-step.
    def _fix_node(node) -> None:
        node.slug = (
            "" if node.is_homepage else (slug_map.get(node.slug) or _cms_safe_slug(node.slug))
        )
        for child in node.children:
            _fix_node(child)

    for node in site.page_tree or []:
        _fix_node(node)

    # Rewrite baked anchor hrefs (header/footer/body) that target an old slug.
    href_map = {
        f"/{old}": f"/{new}"
        for old, new in slug_map.items()
        if old and f"/{old}" != f"/{new}"
    }
    if href_map:
        for page in site.pages:
            for el in page.body_schema.elements:
                _rewrite_hrefs(el, href_map)
        if site.header_schema:
            _rewrite_hrefs(site.header_schema, href_map)
        if site.footer_schema:
            _rewrite_hrefs(site.footer_schema, href_map)

    # Report only the slugs that actually changed.
    return {old: new for old, new in slug_map.items() if old != new}


def _rewrite_hrefs(node: BuilderElement, href_map: dict[str, str]) -> None:
    """Walk a BuilderElement tree, rewriting internal anchor hrefs in place."""
    content = node.content
    if isinstance(content, BuilderElementContent):
        href = content.href
        if isinstance(href, str) and href:
            key = "/" + href.strip("/") if href != "/" else "/"
            if key in href_map:
                content.href = href_map[key]
    if isinstance(content, list):
        for child in content:
            _rewrite_hrefs(child, href_map)


# --- the orchestrator -----------------------------------------------------------


# CMS calls that are mutually independent (media uploads, page creates, draft
# saves, publishes) run under this bound so a big site doesn't stampede the CMS.
_PUSH_CONCURRENCY = 5


async def push_site(req: PushRequest) -> PushReport:
    """Run the full push and return a PushReport. Never raises."""
    report = PushReport()
    client = CmsClient.for_default()
    try:
        return await _run_push(client, req, report)
    finally:
        await client.aclose()


def _raise_first_error(results: list) -> None:
    """Re-raise the first exception in an asyncio.gather(return_exceptions=True)
    result list, preserving the sequential loop's abort-on-first-error contract."""
    for res in results:
        if isinstance(res, BaseException):
            raise res


async def _run_push(client: CmsClient, req: PushRequest, report: PushReport) -> PushReport:
    # 0. Normalize slugs to the CMS's flat kebab-case format. The generator
    #    emits hierarchical slugs (e.g. "services/web-design"); the CMS slug
    #    rule is ^[a-z0-9]+(?:-[a-z0-9]+)*$ — no slashes — so we flatten every
    #    slug + rewrite parent_slug, page_tree, and baked nav hrefs to match.
    changed = _normalize_site_slugs(req.site)
    if changed:
        report.record(
            PushStep(
                name="normalize_slugs",
                ok=True,
                detail=f"Flattened {len(changed)} slug(s) to CMS format",
                data={"renamed": changed},
            )
        )

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
            entity = await client.create_entity(
                entity_name=name,
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
                    },
                )
            )
        except CmsApiError as exc:
            report.record(PushStep(name="create_entity", ok=False, error=str(exc)))
            report.error = str(exc)
            return report

    # 2. Empty-entity guard
    try:
        existing = await client.list_pages(req.entity_token)
    except CmsApiError as exc:
        report.record(PushStep(name="guard", ok=False, error=str(exc)))
        report.error = str(exc)
        return report
    if existing and not req.force_overwrite:
        msg = (
            f"Entity already has {len(existing)} page(s). "
            "This generator currently supports greenfield push only. "
            "Pass force_overwrite=True to push anyway, or use a fresh entity."
        )
        report.record(
            PushStep(name="guard", ok=False, error=msg, data={"existing_count": len(existing)})
        )
        report.error = msg
        return report
    report.record(
        PushStep(name="guard", ok=True, detail=f"Entity has {len(existing)} existing pages")
    )

    # 3. Media upload — collect unique image srcs, upload, build a rewrite map
    try:
        with stage("push_media_upload"):
            rewrites = await _upload_media(client, req)
        report.record(
            PushStep(
                name="media",
                ok=True,
                detail=f"{len(rewrites)} image(s) uploaded",
                data={"uploaded": len(rewrites)},
            )
        )
    except CmsApiError as exc:
        report.record(PushStep(name="media", ok=False, error=str(exc)))
        report.error = str(exc)
        return report

    # Apply rewrites BEFORE we ship schemas — saves us a second pass and
    # ensures every src on the CMS side is a permanent URL.
    _apply_src_rewrites(req.site, rewrites)

    # 4. Create pages — homepage first so isHomepage=true is set deterministically,
    #    then the rest concurrently (each create is independent on the CMS side).
    pages_sorted = sorted(req.site.pages, key=lambda p: (not p.is_homepage, p.slug))
    created: list[tuple[GeneratedPage, str, int]] = []
    try:
        async def _create_one(page: GeneratedPage) -> tuple[GeneratedPage, str, int]:
            seo = {}
            if page.seo:
                if page.seo.title:
                    seo["title"] = page.seo.title
                if page.seo.description:
                    seo["description"] = page.seo.description
                if page.seo.noindex:
                    seo["noindex"] = bool(page.seo.noindex)
            created_meta = await client.create_page(
                req.entity_token,
                title=page.title,
                description=page.description,
                slug=page.slug or None,
                is_homepage=page.is_homepage,
                seo=seo or None,
            )
            page_id = created_meta.get("id")
            draft_version = int(created_meta.get("draftVersion") or 1)
            if not page_id:
                raise CmsApiError(500, f"Create-page response missing id: {created_meta}")
            return (page, str(page_id), draft_version)

        with stage("push_create_pages"):
            if pages_sorted:
                created.append(await _create_one(pages_sorted[0]))
            rest = pages_sorted[1:]
            if rest:
                sem = asyncio.Semaphore(_PUSH_CONCURRENCY)

                async def _create_bounded(page: GeneratedPage):
                    async with sem:
                        return await _create_one(page)

                results = await asyncio.gather(
                    *(_create_bounded(p) for p in rest), return_exceptions=True
                )
                _raise_first_error(results)
                created.extend(results)  # gather preserves pages_sorted order
        report.record(
            PushStep(
                name="create_pages",
                ok=True,
                detail=f"{len(created)} page(s) created",
                data={"page_ids": [pid for _, pid, _ in created]},
            )
        )
    except CmsApiError as exc:
        report.record(PushStep(name="create_pages", ok=False, error=str(exc)))
        report.error = str(exc)
        return report

    # 5. Read first page's builder payload to capture layout.versionId
    homepage_id = created[0][1]
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

    # 6. Save layout — wrap + emit menus + PUT once on the homepage
    try:
        legal_pages = [
            (p.title, f"/{p.slug or ''}".rstrip("/") or "/")
            for p in req.site.pages
            if p.slug.lower() in ("privacy", "terms")
        ]
        menus = build_menus(
            req.site.page_tree,
            legal_pages=legal_pages,
            social_links=req.site.social_links,
        )
        if req.site.header_schema is None or req.site.footer_schema is None:
            raise CmsApiError(
                500,
                "GeneratedSite is missing header_schema or footer_schema — "
                "rebuild the site with plan_to_site() before pushing.",
            )
        header_payload = wrap_header(
            req.site.header_schema,
            menus=menus,
            overlay=req.site.header_overlay,
            scroll_reveal_offset=(
                settings.header_scroll_reveal_offset
                if req.site.header_overlay
                else None
            ),
            # Shrink applies to every header (independent of overlay); the
            # renderer shares the reveal offset when overlay is on, so reuse it.
            shrink_on_scroll=settings.header_shrink_enabled,
            scroll_shrink_offset=settings.header_scroll_reveal_offset,
            shrink_amount=settings.header_shrink_amount,
        )
        footer_payload = wrap_footer(req.site.footer_schema, menus=menus)
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

        async def _save_one(
            page: GeneratedPage, page_id: str, draft_version: int
        ) -> tuple[str, int]:
            body_schema = {
                "elements": [
                    el.model_dump(mode="json") if isinstance(el, BuilderElement) else el
                    for el in page.body_schema.elements
                ],
            }
            async with draft_sem:
                result = await client.save_page_draft(
                    req.entity_token,
                    page_id,
                    base_draft_version=draft_version,
                    body_schema=body_schema,
                )
            return page_id, int(result.get("draftVersion") or draft_version + 1)

        with stage("push_save_drafts"):
            draft_results = await asyncio.gather(
                *(_save_one(*item) for item in created), return_exceptions=True
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
                    *(_publish_one(page_id) for _page, page_id, _dv in created),
                    return_exceptions=True,
                )
            _raise_first_error(publish_results)
            report.record(
                PushStep(
                    name="publish",
                    ok=True,
                    detail=f"{len(created)} page(s) published",
                )
            )
        except CmsApiError as exc:
            report.record(PushStep(name="publish", ok=False, error=str(exc)))
            report.error = str(exc)
            return report
    else:
        report.record(PushStep(name="publish", ok=True, detail="Skipped — pushed as drafts"))

    # 10. CMS content types — template pages (article/event detail rendering)
    #     and the migrated article/event entries. Both are non-fatal: the site
    #     is already pushed, so a failure here degrades to "add content later".
    await _push_content_types(client, req, report)

    # Record page IDs for the UI's "Open in builder" links
    for page, page_id, _ in created:
        report.page_urls[page_id] = page.slug or "/"

    report.success = True
    return report


# --- media upload helpers -------------------------------------------------------


_IMAGE_MIME_MAP = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


async def _upload_media(client: CmsClient, req: PushRequest) -> dict[str, str]:
    """
    Walk every page's BuilderElement tree, find srcs that aren't permanent
    webtree URLs, upload them, and return a {old_src: new_src} rewrite map.
    """
    rewrites: dict[str, str] = {}
    # Collect unique sources first to avoid uploading the same image twice
    # (e.g. a logo that appears on every page).
    sources: dict[str, BuilderElement] = {}  # src → first element using it (for alt)
    for page in req.site.pages:
        for el in page.body_schema.elements:
            _collect_image_srcs(el, sources)
    # Also walk header/footer if present
    if req.site.header_schema:
        _collect_image_srcs(req.site.header_schema, sources)
    if req.site.footer_schema:
        _collect_image_srcs(req.site.footer_schema, sources)
    # And the brand logo (it's pulled into the header but defensive doesn't hurt)
    if req.site.brand:
        logo_url = getattr(req.site.brand, "logo_url", None) or getattr(
            req.site.brand, "logo_data_url", None
        )
        if isinstance(logo_url, str):
            sources.setdefault(logo_url, _placeholder_logo_element(logo_url))

    uploadable = [src for src in sources if _needs_upload(src)]
    if not uploadable:
        return rewrites

    # Resolve + upload concurrently: each image is independent, and the wait is
    # dominated by network (download + POST). One shared download client keeps
    # connections pooled across images from the same host.
    sem = asyncio.Semaphore(_PUSH_CONCURRENCY)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as download_client:

        async def _upload_one(src: str) -> tuple[str, str] | None:
            async with sem:
                try:
                    file_bytes, content_type, filename = await _resolve_to_bytes(
                        src, download_client
                    )
                except _ResolveSkip as exc:
                    logger.info("Skipping unresolvable src %s: %s", src[:80], exc)
                    return None
                if content_type not in {"image/png", "image/jpeg"}:
                    # CMS rejects everything except png/jpg. Convert PNG if possible
                    # (most data URLs we make are already PNG), else skip.
                    logger.info(
                        "Skipping non-supported mime %s for src %s", content_type, src[:80]
                    )
                    return None
                cdn_url = await client.upload_media(
                    req.entity_token,
                    file_bytes=file_bytes,
                    filename=filename,
                    content_type=content_type,
                )
                return src, cdn_url

        results = await asyncio.gather(
            *(_upload_one(src) for src in uploadable), return_exceptions=True
        )
    _raise_first_error(results)
    for res in results:
        if res is not None:
            rewrites[res[0]] = res[1]
    return rewrites


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


def _extract_bg_photo_urls(css_value: str | None) -> list[str]:
    """Real photo URLs referenced by a `background-image` value.

    Only http(s) ``url(...)`` layers count — gradient layers and inline ``data:``
    URIs (grain/mesh) are decoration and must stay in the schema untouched.
    """
    if not isinstance(css_value, str) or not css_value.strip():
        return []
    urls: list[str] = []
    for layer in _split_css_layers(css_value):
        m = re.match(r"""^\s*url\(\s*['"]?\s*([^'")]+)""", layer, re.IGNORECASE)
        if not m:
            continue
        inner = m.group(1).strip()
        if inner.lower().startswith(("http://", "https://")):
            urls.append(inner)
    return urls


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


def _placeholder_logo_element(src: str) -> BuilderElement:
    return BuilderElement(
        id="logo-src-placeholder",
        name="Logo",
        type="image",
        styles={},
        content=BuilderElementContent(src=src, alt="Logo"),
    )


def _needs_upload(src: str) -> bool:
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
    # Already hosted by the CMS / its media store → leave it alone.
    try:
        cms_host = urlparse(settings.cms_api_base_url).hostname or ""
    except ValueError:
        cms_host = ""
    if cms_host and parsed.hostname == cms_host:
        return False
    if "/storage/" in parsed.path or "/api/image/" in parsed.path:
        return False
    # External/scraped photo → re-host it on the CMS.
    return True


class _ResolveSkip(Exception):
    pass


async def _resolve_to_bytes(
    src: str, client: httpx.AsyncClient | None = None
) -> tuple[bytes, str, str]:
    """Turn a src into (bytes, content_type, filename) ready for /api/file/add."""
    if src.startswith("data:"):
        return _decode_data_url(src)
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


_DATA_URL_RE = re.compile(r"data:(?P<ct>[^;,]+)(;base64)?,(?P<data>.*)", re.DOTALL)


def _decode_data_url(src: str) -> tuple[bytes, str, str]:
    m = _DATA_URL_RE.match(src)
    if not m:
        raise _ResolveSkip("malformed data URL")
    content_type = m.group("ct") or "image/png"
    raw = m.group("data") or ""
    try:
        decoded = base64.b64decode(raw)
    except Exception as exc:  # noqa: BLE001
        raise _ResolveSkip(f"base64 decode failed: {exc}") from exc
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
    """Walk every BuilderElement tree on the site + rewrite image srcs in-place."""
    if not rewrites:
        return
    for page in site.pages:
        for el in page.body_schema.elements:
            _rewrite_srcs(el, rewrites)
    if site.header_schema:
        _rewrite_srcs(site.header_schema, rewrites)
    if site.footer_schema:
        _rewrite_srcs(site.footer_schema, rewrites)


def _rewrite_srcs(node: BuilderElement, rewrites: dict[str, str]) -> None:
    content = node.content
    if node.type == "image" and isinstance(content, BuilderElementContent):
        src = content.src
        if isinstance(src, str) and src in rewrites:
            content.src = rewrites[src]
    # Rewrite photo URLs embedded in background styles, preserving the gradient
    # overlay + url() wrapper (substring replace of the exact collected URL).
    styles = node.styles or {}
    for key in ("backgroundImage", "background"):
        value = styles.get(key)
        if not isinstance(value, str):
            continue
        new_value = value
        for old, new in rewrites.items():
            if old in new_value:
                new_value = new_value.replace(old, new)
        if new_value != value:
            styles[key] = new_value
    if isinstance(content, list):
        for child in content:
            _rewrite_srcs(child, rewrites)


# --- CMS content types: template pages + migrated article/event entries ----------


# Mirrors the builder's TEMPLATE_DEFAULTS (builder/src/lib/page-management.ts) so
# templates created here are indistinguishable from ones the builder auto-creates.
_TEMPLATE_PAGE_DEFAULTS: dict[str, tuple[str, str, str]] = {
    "article": (
        "Article Template",
        "Default layout used to render every published article.",
        "article-template",
    ),
    "event": (
        "Event Template",
        "Default layout used to render every published event.",
        "event-template",
    ),
    "articleListing": (
        "Article Listing Template",
        "Default layout used to render article index, category, and tag listing pages.",
        "article-listing-template",
    ),
}

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
        prepared = _prepare_preview_image(file_bytes, filename, max_dim=max_dim)
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


async def _push_content_types(
    client: CmsClient, req: PushRequest, report: PushReport
) -> None:
    """Create article/event template pages + migrated entries. Never raises;
    every failure is recorded as a non-fatal step (the site itself is pushed)."""
    sources = _site_list_sources(req.site)
    cols = req.collections or ContentCollections()
    need_articles = "articles" in sources or cols.has_articles
    need_events = "events" in sources or cols.has_events
    if not need_articles and not need_events:
        return

    # Template pages: same contract as the builder's ensureTemplatePages — a
    # page whose templateFor marks it as the detail/listing layout for that
    # content type. Created blank; the builder renders its default layout.
    wanted: list[str] = []
    if need_articles:
        wanted += ["article", "articleListing"]
    if need_events:
        wanted += ["event"]
    try:
        existing_pages = await client.list_pages(req.entity_token)
        have = {p.get("templateFor") for p in existing_pages if p.get("templateFor")}
        created_templates = 0
        for kind in wanted:
            if kind in have:
                continue
            title, description, slug = _TEMPLATE_PAGE_DEFAULTS[kind]
            await client.create_page(
                req.entity_token,
                title=title,
                description=description,
                slug=slug,
                template_for=kind,
            )
            created_templates += 1
        report.record(
            PushStep(
                name="template_pages",
                ok=True,
                detail=f"{created_templates} template page(s) created",
                data={"templates": wanted},
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

    if not cols.has_articles and not cols.has_events:
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
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as dl:
            for article in cols.articles if category_slug else []:
                image = await _entry_preview_image(
                    article.image_url, fallback_srcs, dl, max_dim=_ARTICLE_IMAGE_MAX_DIM
                )
                publish = image is not None  # published posts require a previewImage
                published_ms = (
                    int(article.published_at.timestamp() * 1000)
                    if article.published_at
                    else now_ms
                )

                async def _create_article(slug: str, _a=article, _img=image, _pub=publish, _ms=published_ms) -> None:
                    await client.create_article(
                        req.entity_token,
                        title=_a.title,
                        slug=slug,
                        excerpt=_a.excerpt,
                        body_html=_a.body_html,
                        category_slugs=[category_slug],
                        published_at_ms=_ms,
                        image=_img,
                        publish=_pub,
                    )

                try:
                    await _create_with_slug_retry(_create_article, article.slug)
                    published += 1 if publish else 0
                    drafted += 0 if publish else 1
                except CmsApiError as exc:
                    failures.append(f"article {article.slug}: {exc}")

            for event in cols.events:
                image = await _entry_preview_image(
                    event.image_url, fallback_srcs, dl, max_dim=_EVENT_IMAGE_MAX_DIM
                )
                start = event.start
                end = event.end or (start + _DEFAULT_EVENT_DURATION if start else None)
                location = event.location or "To be announced"
                # Publishing requires location + start + end + previewImage.
                publish = bool(image and start)

                async def _create_event(slug: str, _e=event, _img=image, _pub=publish, _s=start, _en=end, _loc=location) -> None:
                    await client.create_event(
                        req.entity_token,
                        title=_e.title,
                        slug=slug,
                        excerpt=_e.excerpt,
                        body_html=_e.body_html,
                        location=_loc,
                        start_ms=int(_s.timestamp() * 1000) if _s else None,
                        end_ms=int(_en.timestamp() * 1000) if _en else None,
                        published_at_ms=now_ms,
                        image=_img,
                        publish=_pub,
                    )

                try:
                    await _create_with_slug_retry(_create_event, event.slug)
                    published += 1 if publish else 0
                    drafted += 0 if publish else 1
                except CmsApiError as exc:
                    failures.append(f"event {event.slug}: {exc}")

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
