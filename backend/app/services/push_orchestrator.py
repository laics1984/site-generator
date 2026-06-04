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

Each step's outcome is appended to PushReport so the UI can show a per-step
status table. Failures abort the push but the report carries everything done
up to the failure point (useful for diagnostics + future resume support).
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass, field
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
from app.services.cms_client import CmsApiError, CmsClient
from app.services.menu_builder import build_menus, wrap_footer, wrap_header

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


async def push_site(req: PushRequest) -> PushReport:
    """Run the full push and return a PushReport. Never raises."""
    report = PushReport()
    client = CmsClient.for_default()

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

    # 4. Create pages — homepage first so isHomepage=true is set deterministically
    pages_sorted = sorted(req.site.pages, key=lambda p: (not p.is_homepage, p.slug))
    created: list[tuple[GeneratedPage, str, int]] = []
    try:
        for page in pages_sorted:
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
            created.append((page, str(page_id), draft_version))
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
        menus = build_menus(req.site.page_tree, legal_pages=legal_pages)
        if req.site.header_schema is None or req.site.footer_schema is None:
            raise CmsApiError(
                500,
                "GeneratedSite is missing header_schema or footer_schema — "
                "rebuild the site with plan_to_site() before pushing.",
            )
        header_payload = wrap_header(req.site.header_schema, menus=menus)
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

    # 7. Save drafts — bodySchema per page
    saved_drafts: dict[str, int] = {}  # pageId → latest draft_version
    try:
        for page, page_id, draft_version in created:
            body_schema = {
                "elements": [
                    el.model_dump(mode="json") if isinstance(el, BuilderElement) else el
                    for el in page.body_schema.elements
                ],
            }
            result = await client.save_page_draft(
                req.entity_token,
                page_id,
                base_draft_version=draft_version,
                body_schema=body_schema,
            )
            saved_drafts[page_id] = int(result.get("draftVersion") or draft_version + 1)
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
            await client.update_builder_styles(req.site.builder_styles)
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

    # 9. Publish (optional)
    if req.publish:
        try:
            for _page, page_id, _draft_version in created:
                latest_draft = saved_drafts.get(page_id, 1)
                await client.publish_page(
                    req.entity_token,
                    page_id,
                    expected_draft_version=latest_draft,
                    expected_layout_version_id=layout_version_id,
                )
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

    for src in list(sources.keys()):
        if not _needs_upload(src):
            continue
        try:
            file_bytes, content_type, filename = await _resolve_to_bytes(src)
        except _ResolveSkip as exc:
            logger.info("Skipping unresolvable src %s: %s", src[:80], exc)
            continue
        if content_type not in {"image/png", "image/jpeg"}:
            # CMS rejects everything except png/jpg. Convert PNG if possible
            # (most data URLs we make are already PNG), else skip.
            logger.info("Skipping non-supported mime %s for src %s", content_type, src[:80])
            continue
        cdn_url = await client.upload_media(
            req.entity_token,
            file_bytes=file_bytes,
            filename=filename,
            content_type=content_type,
        )
        rewrites[src] = cdn_url
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


async def _resolve_to_bytes(src: str) -> tuple[bytes, str, str]:
    """Turn a src into (bytes, content_type, filename) ready for /api/file/add."""
    if src.startswith("data:"):
        return _decode_data_url(src)
    # https URL: fetch
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
            resp = await client.get(src)
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
