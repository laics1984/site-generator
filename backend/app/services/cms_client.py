"""
HTTP client for the webtree CMS API.

All endpoints + shapes confirmed by recon (PHASE_4_RECON.md). The client
carries:
  - a JWT (from /api/auth/login) → used for every cms.auth route
  - an optional builder-session cookie (from the launch-code bridge) →
    used for /api/builder/styles only, since that route requires the
    builder.auth middleware

Two-phase auth is needed because builderStyles is entity-scoped + lives
behind builder.auth (session cookie), while page management uses cms.auth
(JWT). The launch-code bridge mints a session cookie via the JWT-authed
/api/builder/launch + /api/builder/redeem pair.

This module is pure HTTP + light validation. The push orchestrator
(push_orchestrator.py) sequences calls.
"""

from __future__ import annotations

import json
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import settings
from app.services.cms_targets import CmsTarget, default_target

logger = logging.getLogger(__name__)


# Config-driven (CMS_TIMEOUT_SECONDS / CMS_MEDIA_UPLOAD_TIMEOUT_SECONDS);
# defaults preserve the prior 30s / 120s behaviour.
_DEFAULT_TIMEOUT = settings.cms_timeout_seconds
_MEDIA_UPLOAD_TIMEOUT = settings.cms_media_upload_timeout_seconds  # large uploads are slow

# The most pages one GET /pages answers with (ListPagesRequest caps perPage at 100).
_PAGE_LIST_SIZE = 100


class CmsApiError(Exception):
    """Raised when a CMS API call returns a non-2xx response."""

    def __init__(self, status: int, message: str, *, response_body: Any = None):
        super().__init__(message)
        self.status = status
        self.response_body = response_body


@dataclass
class CmsClient:
    """Stateful client — holds JWT + optional builder-session cookie jar."""

    base_url: str
    # repr=False on the credentials so an accidental repr()/exception dump of the
    # client can't leak the bearer token or session cookies.
    jwt: str | None = field(default=None, repr=False)
    # cookies for builder-session calls (set via launch-code bridge)
    _builder_cookies: dict[str, str] = field(default_factory=dict, repr=False)
    # long-lived connection pool shared by every JWT call + media upload
    _http: httpx.AsyncClient | None = field(default=None, repr=False)
    # False once GET /api/file/lookup answered a bare 404 — an API deployed
    # before the route existed. Remembered so a push against it pays one probe,
    # not one per image, and uploads as it always did.
    _media_lookup_supported: bool = field(default=True, repr=False)

    # --- factory ---------------------------------------------------------------

    @classmethod
    def for_target(cls, target: CmsTarget) -> "CmsClient":
        """The one place a chosen push destination becomes an origin.

        The client stays a plain `base_url` holder rather than carrying the
        CmsTarget: its business is one origin, and everything that needs the
        target's other facts (admin link, already-hosted check) lives outside.
        """
        return cls(base_url=target.api_base_url.rstrip("/"))

    @classmethod
    def for_default(cls) -> "CmsClient":
        """The target a caller that names none gets — today's behaviour."""
        return cls.for_target(default_target())

    # --- auth ------------------------------------------------------------------

    async def login(self, email: str, password: str) -> str:
        """POST /api/auth/login. Stores the JWT on the client + returns it.

        Runs on the pooled client like every other JWT call: over TLS to a
        remote target, a private one-shot client would pay a second handshake
        on the critical path of every test-connection and every push.
        """
        async with self._wrap_request("login"):
            url = f"{self.base_url}/api/auth/login"
            resp = await self._http_client().post(
                url, json={"email": email, "password": password}
            )
            _assert_not_redirect(resp, self.base_url)
            body = _safe_json(resp)
            if resp.status_code != 200:
                raise CmsApiError(
                    resp.status_code,
                    f"Login failed: {body.get('message') or resp.text[:200]}",
                    response_body=body,
                )
            token = body.get("access_token") or body.get("token")
            if not token:
                raise CmsApiError(
                    500, f"Login response missing token: {body}", response_body=body
                )
            self.jwt = token
            return token

    # --- entities --------------------------------------------------------------

    async def create_entity(
        self,
        *,
        entity_name: str,
        entity_url: str | None = None,
        builder_styles: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        POST /api/entities — create a fresh entity owned by the logged-in user.

        The CMS mints a unique entity_api_token and provisions the website
        layout in one transaction, so the returned token is immediately usable
        for the rest of the push. Returns the entity data dict (incl.
        ``entity_api_token``).
        """
        async with self._wrap_request("create_entity"):
            url = f"{self.base_url}/api/entities"
            payload: dict[str, Any] = {"entity_name": entity_name}
            if entity_url:
                payload["entity_url"] = entity_url
            if builder_styles:
                payload["builder_styles"] = builder_styles
            resp = await self._http_client().post(url, json=payload, headers=self._jwt_headers())
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Create entity failed [{resp.status_code}]: {_extract_error(body) or resp.text[:300]}",
                    response_body=body,
                )
            data = body.get("data") or body
            token = data.get("entity_api_token")
            if not token:
                raise CmsApiError(
                    500,
                    f"Create-entity response missing entity_api_token: {body}",
                    response_body=body,
                )
            return data

    async def list_entities(self) -> list[dict[str, Any]]:
        """GET /api/entities — the sites the signed-in user owns or manages.

        Each row carries the `entity_api_token` a push is keyed on, which is
        what lets the publish drawer offer a site picker instead of asking the
        operator to go and find the token in the admin.
        """
        async with self._wrap_request("list_entities"):
            url = f"{self.base_url}/api/entities"
            resp = await self._http_client().get(url, headers=self._jwt_headers())
            body = _safe_json(resp)
            if resp.status_code != 200:
                raise CmsApiError(
                    resp.status_code,
                    f"List sites failed [{resp.status_code}]: "
                    f"{_extract_error(body) or resp.text[:200]}",
                    response_body=body,
                )
            data = body.get("data")
            return data if isinstance(data, list) else []

    # --- pages -----------------------------------------------------------------

    async def list_pages(
        self, entity_token: str, *, status: str | None = None
    ) -> list[dict[str, Any]]:
        """GET /pages — every page of the entity, walking the CMS's pagination.

        The endpoint pages at 20 by default and never more than 100 at a time,
        so one call only ever saw a site's first 20 pages. Its default status
        filter also hides archived pages; ``status="all"`` lists those too,
        which a sync needs — an archived page still owns its slug.
        """
        rows: list[dict[str, Any]] = []
        page_number = 1
        async with self._wrap_request("list_pages"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages"
            while True:
                params: dict[str, Any] = {"perPage": _PAGE_LIST_SIZE, "page": page_number}
                if status:
                    params["status"] = status
                resp = await self._http_client().get(
                    url, params=params, headers=self._jwt_headers()
                )
                body = _safe_json(resp)
                if resp.status_code != 200:
                    raise CmsApiError(
                        resp.status_code,
                        f"List pages failed: {resp.text[:200]}",
                        response_body=body,
                    )
                batch = body.get("data") or []
                rows.extend(batch)
                total = (body.get("meta") or {}).get("total")
                if not batch or total is None or len(rows) >= int(total):
                    return rows
                page_number += 1

    async def get_page(self, entity_token: str, page_id: str) -> dict[str, Any]:
        """GET /pages/{id} — one page's metadata, including its ``draftVersion``.

        The list endpoint omits the draft version, and every write to an
        existing page (PATCH metadata, PUT draft) is a compare-and-swap on it.
        """
        async with self._wrap_request("get_page"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages/{page_id}"
            resp = await self._http_client().get(url, headers=self._jwt_headers())
            body = _safe_json(resp)
            if resp.status_code != 200:
                raise CmsApiError(
                    resp.status_code,
                    f"Get page failed: {_extract_error(body) or resp.text[:200]}",
                    response_body=body,
                )
            return body.get("data") or body

    async def update_page(
        self,
        entity_token: str,
        page_id: str,
        *,
        base_draft_version: int,
        title: str,
        description: str | None,
        seo: dict[str, Any],
        template_for: str | None = None,
    ) -> dict[str, Any]:
        """PATCH /pages/{id} — title, description and SEO of an existing page.

        ``template_for`` marks the page as an article/event template, which is
        how a page already sitting on a template's slug is adopted rather than
        left beside a suffixed twin (push_orchestrator._adopt_as_template).

        Bumps the draft version the way a draft save does, so the returned
        ``draftVersion`` is what the PUT /draft that follows must send. The slug
        is deliberately not sent: the page was matched on it.
        """
        async with self._wrap_request("update_page"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages/{page_id}"
            payload: dict[str, Any] = {
                "baseDraftVersion": base_draft_version,
                "title": title,
                "description": description,
                "seo": seo,
            }
            if template_for is not None:
                payload["templateFor"] = template_for
            resp = await self._http_client().patch(
                url, json=payload, headers=self._jwt_headers()
            )
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Update page failed [{resp.status_code}]: "
                    f"{_extract_error(body) or resp.text[:300]}",
                    response_body=body,
                )
            return body.get("data") or body

    async def restore_page(self, entity_token: str, page_id: str) -> dict[str, Any]:
        """POST /pages/{id}/restore — bring an archived page back.

        It returns as published when it has a published revision, as a draft
        otherwise; either way it is editable again, which an archived page is
        not, and it still owns the slug it always had.
        """
        async with self._wrap_request("restore_page"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages/{page_id}/restore"
            resp = await self._http_client().post(url, headers=self._jwt_headers())
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Restore page failed [{resp.status_code}]: "
                    f"{_extract_error(body) or resp.text[:300]}",
                    response_body=body,
                )
            return body.get("data") or body

    async def archive_page(self, entity_token: str, page_id: str) -> None:
        """DELETE /pages/{id} — archive, not delete.

        The page keeps its revisions and its slug, drops off the live site, and
        the admin's archive can restore it. Permanent deletion is a separate
        route this client deliberately does not call.
        """
        async with self._wrap_request("archive_page"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages/{page_id}"
            resp = await self._http_client().delete(url, headers=self._jwt_headers())
            if resp.status_code >= 400:
                body = _safe_json(resp)
                raise CmsApiError(
                    resp.status_code,
                    f"Archive page failed [{resp.status_code}]: "
                    f"{_extract_error(body) or resp.text[:300]}",
                    response_body=body,
                )

    async def create_page(
        self,
        entity_token: str,
        *,
        title: str,
        description: str | None = None,
        slug: str | None = None,
        is_homepage: bool = False,
        seo: dict[str, Any] | None = None,
        template_for: str | None = None,
    ) -> dict[str, Any]:
        """POST /api/entities/{token}/pages → returns the created page metadata.

        ``template_for`` ∈ {article, event, articleListing} marks the page as
        the detail/listing template the CMS routes that content type through.
        """
        async with self._wrap_request("create_page"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages"
            payload: dict[str, Any] = {"title": title, "isHomepage": is_homepage}
            if description is not None:
                payload["description"] = description
            if slug is not None and slug != "":
                payload["slug"] = slug
            if seo:
                payload["seo"] = seo
            if template_for:
                payload["templateFor"] = template_for
            resp = await self._http_client().post(url, json=payload, headers=self._jwt_headers())
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Create page failed [{resp.status_code}]: {body.get('message') or resp.text[:300]}",
                    response_body=body,
                )
            return body.get("data") or body

    async def get_builder_payload(
        self, entity_token: str, page_id: str, *, mode: str = "draft"
    ) -> dict[str, Any]:
        """GET /pages/{id}/builder — read concurrency tokens + current layout."""
        async with self._wrap_request("get_builder_payload"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages/{page_id}/builder"
            resp = await self._http_client().get(
                url, params={"mode": mode}, headers=self._jwt_headers()
            )
            body = _safe_json(resp)
            if resp.status_code != 200:
                raise CmsApiError(resp.status_code, f"Get builder payload failed: {resp.text[:200]}", response_body=body)
            return body.get("data") or body

    async def save_page_draft(
        self,
        entity_token: str,
        page_id: str,
        *,
        base_draft_version: int,
        body_schema: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._wrap_request("save_page_draft"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages/{page_id}/draft"
            payload = {
                "baseDraftVersion": base_draft_version,
                "bodySchema": body_schema,
            }
            resp = await self._http_client().put(url, json=payload, headers=self._jwt_headers())
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Save draft failed: {body.get('message') or resp.text[:300]}",
                    response_body=body,
                )
            return body.get("data") or body

    async def save_page_layout(
        self,
        entity_token: str,
        page_id: str,
        *,
        expected_layout_version_id: str,
        header_schema: dict[str, Any],
        footer_schema: dict[str, Any],
        menus: list[dict[str, Any]],
    ) -> dict[str, Any]:
        async with self._wrap_request("save_page_layout"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages/{page_id}/layout"
            payload = {
                "expectedLayoutVersionId": expected_layout_version_id,
                "headerSchema": header_schema,
                "footerSchema": footer_schema,
                "menus": menus,
            }
            resp = await self._http_client().put(url, json=payload, headers=self._jwt_headers())
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Save layout failed: {body.get('message') or resp.text[:300]}",
                    response_body=body,
                )
            return body.get("data") or body

    async def publish_page(
        self,
        entity_token: str,
        page_id: str,
        *,
        expected_draft_version: int,
        expected_layout_version_id: str,
    ) -> dict[str, Any]:
        async with self._wrap_request("publish_page"):
            url = f"{self.base_url}/api/entities/{entity_token}/pages/{page_id}/publish"
            payload = {
                "expectedDraftVersion": expected_draft_version,
                "expectedLayoutVersionId": expected_layout_version_id,
            }
            resp = await self._http_client().post(url, json=payload, headers=self._jwt_headers())
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Publish failed: {body.get('message') or resp.text[:300]}",
                    response_body=body,
                )
            return body.get("data") or body

    # --- media -----------------------------------------------------------------

    async def upload_media(
        self,
        entity_token: str,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> str:
        """POST /api/file/add. Returns the CDN URL for the uploaded media."""
        async with self._wrap_request("upload_media"):
            url = f"{self.base_url}/api/file/add"
            files = {"file": (filename, file_bytes, content_type)}
            data = {"entity": entity_token}
            resp = await self._http_client().post(
                url,
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {self.jwt}"} if self.jwt else {},
                timeout=_MEDIA_UPLOAD_TIMEOUT,
            )
            body = _safe_json(resp)
            # MediaController returns {t: 'p', i: <url>} on success, {t: 'f', errors: ...} on fail.
            if resp.status_code >= 400 or body.get("t") != "p":
                errors = body.get("errors") or body.get("message") or resp.text[:300]
                raise CmsApiError(
                    resp.status_code or 500,
                    f"Media upload failed for {filename}: {errors}",
                    response_body=body,
                )
            cdn_url = body.get("i")
            if not isinstance(cdn_url, str) or not cdn_url:
                raise CmsApiError(500, f"Media upload response missing URL: {body}", response_body=body)
            return cdn_url

    async def lookup_media(self, entity_token: str, sha256: str) -> str | None:
        """GET /api/file/lookup — the URL of a file this site already holds with
        exactly these bytes, or None.

        Asked before every upload, so an update that re-sends a site's
        photography files nothing it already has. A 204 is a miss. A bare 404
        is an API without the route, remembered for the life of the client (see
        `_media_lookup_supported`); a 404 that carries the CMS's own error
        shape is the API answering about the entity, and is raised like any
        other refusal.
        """
        if not self._media_lookup_supported:
            return None
        async with self._wrap_request("lookup_media"):
            url = f"{self.base_url}/api/file/lookup"
            resp = await self._http_client().get(
                url, params={"e": entity_token, "hash": sha256}, headers=self._jwt_headers()
            )
            if resp.status_code == 204:
                return None
            body = _safe_json(resp)
            if resp.status_code == 404 and not isinstance(body.get("error"), dict):
                self._media_lookup_supported = False
                return None
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Media lookup failed [{resp.status_code}]: "
                    f"{_extract_error(body) or resp.text[:200]}",
                    response_body=body,
                )
            found = body.get("i")
            return found if isinstance(found, str) and found else None

    async def set_entity_favicon(
        self,
        entity_token: str,
        *,
        file_bytes: bytes,
        filename: str,
        content_type: str,
    ) -> str | None:
        """POST /api/entities/{token}/favicon → the resolved icon URL.

        Deliberately not `/api/file/add`: a favicon is site chrome, not a
        media-library asset, and uploading it there would park an icon in the
        tenant's library for nothing to reference. The CMS re-encodes a raster
        icon to a 192px PNG (and stores an SVG as a sanitized vector), so this
        returns the URL a browser tab and a search result will actually use.

        Send what `_coerce_to_favicon` produced, not raw source bytes: the CMS
        decodes with GD, whose codecs vary by deployment.
        """
        async with self._wrap_request("set_entity_favicon"):
            url = f"{self.base_url}/api/entities/{entity_token}/favicon"
            files = {"favicon": (filename, file_bytes, content_type)}
            resp = await self._http_client().post(
                url,
                files=files,
                headers=self._jwt_headers(),
                timeout=_MEDIA_UPLOAD_TIMEOUT,
            )
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Set favicon failed [{resp.status_code}]: "
                    f"{_extract_error(body) or resp.text[:300]}",
                    response_body=body,
                )
            return (body.get("data") or {}).get("favicon_url")

    # --- articles / events (content-migration push) ------------------------------
    #
    # Legacy admin endpoints (routes/api_admin.php): multipart form posts under
    # JWT auth. Success/failure is signalled in the body (`success` bool) with
    # HTTP 200 either way, so every call checks the body, not the status code.

    async def create_category(self, entity_token: str, *, title: str) -> str:
        """POST /api/category/addCategory → the category's slug.

        Idempotent: "already existed" responses return the derived slug (the
        CMS derives it as Str::slug(title)) instead of raising.
        """
        async with self._wrap_request("create_category"):
            url = f"{self.base_url}/api/category/addCategory"
            resp = await self._http_client().post(
                url,
                data={"title": title, "entity": entity_token},
                headers={"Authorization": f"Bearer {self.jwt}"} if self.jwt else {},
            )
            body = _safe_json(resp)
            if body.get("success"):
                slug = (body.get("category") or {}).get("slug")
                if isinstance(slug, str) and slug:
                    return slug
                return _laravel_slug(title)
            message = body.get("message")
            if isinstance(message, dict) and any(
                "already existed" in str(v) for v in message.values()
            ):
                return _laravel_slug(title)
            raise CmsApiError(
                resp.status_code or 500,
                f"Create category failed: {message or resp.text[:300]}",
                response_body=body,
            )

    async def create_article(
        self,
        entity_token: str,
        *,
        title: str,
        slug: str,
        excerpt: str,
        body_html: str,
        category_slugs: list[str],
        published_at_ms: int | None = None,
        image: tuple[str, bytes, str] | None = None,
        publish: bool = True,
    ) -> str:
        """POST /api/articles/create → the created article id.

        ``image`` is (filename, bytes, content_type); required when publishing
        (the CMS rejects a published post without a previewImage upload).
        """
        async with self._wrap_request("create_article"):
            url = f"{self.base_url}/api/articles/create"
            data: dict[str, Any] = {
                "title": title,
                "slug": slug,
                "excerpt": excerpt,
                "body": body_html,
                # The controller json-decodes string values for both fields, and
                # calls count() on `tag` unconditionally — always send an array.
                "category": json.dumps(category_slugs),
                "tag": "[]",
                "entity": entity_token,
                "post_type": "published" if publish else "draft",
            }
            if published_at_ms is not None:
                data["published_at"] = str(published_at_ms)
            files = {"previewImage": image} if image else None
            resp = await self._http_client().post(
                url,
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {self.jwt}"} if self.jwt else {},
                timeout=_MEDIA_UPLOAD_TIMEOUT,
            )
            body = _safe_json(resp)
            if resp.status_code >= 400 or not body.get("success"):
                raise CmsApiError(
                    resp.status_code or 500,
                    f"Create article '{slug}' failed: {body.get('message') or resp.text[:300]}",
                    response_body=body,
                )
            return str(body.get("aid"))

    async def create_event(
        self,
        entity_token: str,
        *,
        title: str,
        slug: str,
        excerpt: str,
        body_html: str,
        location: str | None = None,
        start_ms: int | None = None,
        end_ms: int | None = None,
        published_at_ms: int | None = None,
        image: tuple[str, bytes, str] | None = None,
        publish: bool = True,
    ) -> str:
        """POST /api/events/create → the created event id.

        Publishing requires location + start + end + previewImage; the caller
        downgrades to draft when any of those can't be resolved. Event preview
        images are capped at 1500×1500 by the CMS (posts allow 5500).
        """
        async with self._wrap_request("create_event"):
            url = f"{self.base_url}/api/events/create"
            data: dict[str, Any] = {
                "event_name": title,
                "slug": slug,
                "excerpt": excerpt,
                "body": body_html,
                "entity": entity_token,
                "event_type": "published" if publish else "draft",
            }
            if location:
                data["location"] = location
            # start/end/published_at arrive as ms epochs; the controller converts.
            if start_ms is not None:
                data["start"] = str(start_ms)
            if end_ms is not None:
                data["end"] = str(end_ms)
            if published_at_ms is not None:
                data["published_at"] = str(published_at_ms)
            files = {"previewImage": image} if image else None
            resp = await self._http_client().post(
                url,
                data=data,
                files=files,
                headers={"Authorization": f"Bearer {self.jwt}"} if self.jwt else {},
                timeout=_MEDIA_UPLOAD_TIMEOUT,
            )
            body = _safe_json(resp)
            if resp.status_code >= 400 or not body.get("success"):
                raise CmsApiError(
                    resp.status_code or 500,
                    f"Create event '{slug}' failed: {body.get('message') or resp.text[:300]}",
                    response_body=body,
                )
            return str(body.get("eid"))

    # --- builder-styles via launch-code bridge ---------------------------------

    async def mint_builder_session(self, entity_token: str) -> None:
        """
        Two-call bridge: POST /api/builder/launch (JWT) → {code}, then
        POST /api/builder/redeem (no auth) → Set-Cookie builder session.

        After this, calls to update_builder_styles() carry the session cookie.
        """
        async with self._wrap_request("mint_builder_session"):
            # 1. Issue launch code via JWT
            launch_url = f"{self.base_url}/api/builder/launch"
            resp = await self._http_client().post(
                launch_url, json={"entity_api_token": entity_token}, headers=self._jwt_headers()
            )
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Launch code request failed: {body.get('message') or resp.text[:200]}",
                    response_body=body,
                )
            code = body.get("code") or body.get("launch_code")
            if not code:
                # The handler may put it under "data" or similar — be lenient.
                data = body.get("data") or {}
                code = data.get("code") or data.get("launch_code")
            if not code:
                # The CMS's current shape is {"launch_url": "<builder>/?code=…", "expires_in": 60}
                # — the code is URL-encoded into launch_url's query string.
                launch_url = body.get("launch_url") or (body.get("data") or {}).get("launch_url")
                if isinstance(launch_url, str) and launch_url:
                    qs = parse_qs(urlparse(launch_url).query)
                    code = (qs.get("code") or [None])[0]
            if not code:
                raise CmsApiError(500, f"Launch code response missing code: {body}", response_body=body)

            # 2. Redeem → Set-Cookie builder session
            redeem_url = f"{self.base_url}/api/builder/redeem"
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                resp = await client.post(redeem_url, json={"code": code})
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Launch code redeem failed: {body.get('message') or resp.text[:200]}",
                    response_body=body,
                )
            # Capture the Set-Cookie pairs for later builder.* calls.
            self._builder_cookies = dict(resp.cookies)
            if not self._builder_cookies:
                raise CmsApiError(
                    500,
                    "Redeem succeeded but no session cookie was set — "
                    "check the API's session cookie name + SameSite settings.",
                    response_body=body,
                )

    async def update_builder_styles(
        self, builder_styles: dict[str, Any]
    ) -> dict[str, Any]:
        """PUT /api/builder/styles — entity-scoped, uses the builder-session cookie."""
        if not self._builder_cookies:
            raise CmsApiError(
                500,
                "update_builder_styles called without mint_builder_session — "
                "the builder.auth middleware requires a session cookie.",
            )
        async with self._wrap_request("update_builder_styles"):
            url = f"{self.base_url}/api/builder/styles"
            async with httpx.AsyncClient(
                timeout=_DEFAULT_TIMEOUT, cookies=self._builder_cookies
            ) as client:
                resp = await client.put(url, json={"builder_styles": builder_styles})
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Update builder_styles failed: {body.get('message') or resp.text[:300]}",
                    response_body=body,
                )
            return body

    async def update_whatsapp_widget(
        self, entity_token: str, widget: dict[str, Any]
    ) -> dict[str, Any]:
        """PUT /api/entities/{token}/whatsapp-widget — the site's chat button.

        The admin (JWT) route rather than the builder-session one used by
        update_builder_styles: this needs no builder session, so it does not
        have to be sequenced after mint_builder_session.
        """
        async with self._wrap_request("update_whatsapp_widget"):
            url = f"{self.base_url}/api/entities/{entity_token}/whatsapp-widget"
            resp = await self._http_client().put(
                url, json=widget, headers=self._jwt_headers()
            )
            body = _safe_json(resp)
            if resp.status_code >= 400:
                raise CmsApiError(
                    resp.status_code,
                    f"Update WhatsApp widget failed: {_extract_error(body) or resp.text[:300]}",
                    response_body=body,
                )
            return body

    # --- internals -------------------------------------------------------------

    @asynccontextmanager
    async def _wrap_request(self, operation: str) -> AsyncIterator[None]:
        """Catch httpx transport errors and surface them as CmsApiError."""
        try:
            yield
        except CmsApiError:
            raise
        except httpx.ConnectError as exc:
            raise CmsApiError(
                503,
                f"Could not reach CMS at {self.base_url} during {operation} — is it running? ({exc})",
            ) from exc
        except httpx.TimeoutException as exc:
            raise CmsApiError(
                504,
                f"CMS request timed out during {operation}: {exc}",
            ) from exc
        except httpx.HTTPError as exc:
            raise CmsApiError(502, f"CMS request failed during {operation}: {exc}") from exc

    def _http_client(self) -> httpx.AsyncClient:
        """Lazily-created long-lived client so back-to-back CMS calls reuse
        connections (keep-alive) instead of opening a socket per request.
        Safe for concurrent requests. Close via aclose() when the push ends."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        return self._http

    async def aclose(self) -> None:
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()

    def _jwt_headers(self) -> dict[str, str]:
        if not self.jwt:
            raise CmsApiError(
                401, "CmsClient has no JWT — call login() first."
            )
        return {
            "Authorization": f"Bearer {self.jwt}",
            "Accept": "application/json",
        }


def _laravel_slug(value: str) -> str:
    """Mirror Laravel's Str::slug for the ASCII titles we send."""
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


def _assert_not_redirect(resp: httpx.Response, base_url: str) -> None:
    """Refuse a redirected CMS response instead of following it.

    Redirects are deliberately not followed on CMS calls: httpx turns a 301/302
    on a POST into a GET, which would silently half-apply a push. One check on
    login is enough to catch the misconfiguration — a server that redirects
    /api/auth/login redirects everything, and login is the first call in both
    test-connection and push. Without it the raw 3xx became the status of the
    generator's OWN response (routers/cms.py re-raises exc.status), so the
    browser got a bare 301.
    """
    if not resp.is_redirect:
        return
    location = resp.headers.get("location") or "elsewhere"
    raise CmsApiError(
        502,
        f"CMS at {base_url} redirected the login to {location}. Check the target's "
        "scheme and host — an http:// base URL for an https-only CMS is the usual "
        "cause.",
    )


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        body = resp.json()
        if isinstance(body, dict):
            return body
        return {"data": body}
    except Exception:
        return {}


def _extract_error(body: dict[str, Any]) -> str | None:
    """Pull a human message out of the CMS's varied error shapes.

    Handles Laravel validation (``{"message", "errors": {...}}``), the
    PageManagementException shape (``{"error": {"code", "message"}}``), and a
    plain ``{"message": ...}``.
    """
    if not isinstance(body, dict):
        return None
    err = body.get("error")
    if isinstance(err, dict) and err.get("message"):
        return str(err["message"])
    if isinstance(err, str) and err:
        return err
    # Laravel 422: surface the first field error if present, else the message.
    errors = body.get("errors")
    if isinstance(errors, dict) and errors:
        first = next(iter(errors.values()))
        if isinstance(first, list) and first:
            return str(first[0])
        if isinstance(first, str):
            return first
    if body.get("message"):
        return str(body["message"])
    return None
