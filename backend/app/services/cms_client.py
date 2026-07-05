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

import logging
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


_DEFAULT_TIMEOUT = 30.0
_MEDIA_UPLOAD_TIMEOUT = 60.0  # large image uploads can be slow


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
    jwt: str | None = None
    # cookies for builder-session calls (set via launch-code bridge)
    _builder_cookies: dict[str, str] = field(default_factory=dict)
    # long-lived connection pool shared by every JWT call + media upload
    _http: httpx.AsyncClient | None = field(default=None, repr=False)

    # --- factory ---------------------------------------------------------------

    @classmethod
    def for_default(cls) -> "CmsClient":
        return cls(base_url=settings.cms_api_base_url.rstrip("/"))

    # --- auth ------------------------------------------------------------------

    async def login(self, email: str, password: str) -> str:
        """POST /api/auth/login. Stores the JWT on the client + returns it."""
        url = f"{self.base_url}/api/auth/login"
        try:
            async with httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT) as client:
                resp = await client.post(url, json={"email": email, "password": password})
        except httpx.ConnectError as exc:
            raise CmsApiError(
                503,
                f"Could not reach CMS at {self.base_url} — is it running? ({exc})",
            ) from exc
        except httpx.HTTPError as exc:
            raise CmsApiError(502, f"CMS request failed: {exc}") from exc
        body = _safe_json(resp)
        if resp.status_code != 200:
            raise CmsApiError(
                resp.status_code,
                f"Login failed: {body.get('message') or resp.text[:200]}",
                response_body=body,
            )
        token = body.get("access_token") or body.get("token")
        if not token:
            raise CmsApiError(500, f"Login response missing token: {body}", response_body=body)
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

    # --- pages -----------------------------------------------------------------

    async def list_pages(self, entity_token: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/api/entities/{entity_token}/pages"
        resp = await self._http_client().get(url, headers=self._jwt_headers())
        body = _safe_json(resp)
        if resp.status_code != 200:
            raise CmsApiError(resp.status_code, f"List pages failed: {resp.text[:200]}", response_body=body)
        return body.get("data") or []

    async def create_page(
        self,
        entity_token: str,
        *,
        title: str,
        description: str | None = None,
        slug: str | None = None,
        is_homepage: bool = False,
        seo: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /api/entities/{token}/pages → returns the created page metadata."""
        url = f"{self.base_url}/api/entities/{entity_token}/pages"
        payload: dict[str, Any] = {"title": title, "isHomepage": is_homepage}
        if description is not None:
            payload["description"] = description
        if slug is not None and slug != "":
            payload["slug"] = slug
        if seo:
            payload["seo"] = seo
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

    # --- builder-styles via launch-code bridge ---------------------------------

    async def mint_builder_session(self, entity_token: str) -> None:
        """
        Two-call bridge: POST /api/builder/launch (JWT) → {code}, then
        POST /api/builder/redeem (no auth) → Set-Cookie builder session.

        After this, calls to update_builder_styles() carry the session cookie.
        """
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

    # --- internals -------------------------------------------------------------

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
