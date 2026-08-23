"""
CMS push endpoints — kicks off / inspects a push into the webtree CMS.

`GET  /api/cms/targets`         — which CMS installs this generator can push to
`POST /api/cms/test-connection` — verify creds + entity access without writing
`POST /api/cms/push`            — run the orchestrator and return a PushReport

A request picks its destination by NAME, never by URL — see
services/cms_targets.py for why that distinction is load-bearing.

The frontend renders PushReport.steps as a progress table.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.builder_schema import GeneratedSite
from app.services.cms_client import CmsApiError, CmsClient
from app.services.cms_targets import (
    CmsTarget,
    UnknownCmsTarget,
    available_targets,
    resolve_target,
)
from app.services.push_orchestrator import PushRequest, push_site

router = APIRouter(prefix="/api/cms", tags=["cms"])


def _admin_url(target: CmsTarget) -> str | None:
    """Deep link into the webtree admin suite's page list, or None when the
    admin app's origin isn't configured for this target.

    The frontend reads no `import.meta.env` (see ARCHITECTURE.md), so the only
    way it can offer an "Open in webtree admin" link is for us to hand one over.
    Returning None is the honest answer when we don't know the host — the UI
    then omits the link rather than inventing a URL that 404s.

    Reads the link off the TARGET, not settings: a push that lands in a remote
    CMS must not be followed by a localhost deep link — plausible, silent, and
    pointing at an entity that isn't there.
    """
    base = (target.admin_base_url or "").strip().rstrip("/")
    if not base:
        return None
    return f"{base}/webpages/list"


def _resolve(name: str | None) -> CmsTarget:
    """Target name → target, as a 400 rather than a 500 when it isn't one."""
    try:
        return resolve_target(name)
    except UnknownCmsTarget as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/targets")
async def list_targets() -> list[dict[str, Any]]:
    """Which CMS installs this generator can push into, default target first.

    The frontend can't know these — it reads no `import.meta.env` and the hosts
    live in the backend's settings. It renders a picker only when there is more
    than one, so an install that never configured a second CMS sees no new UI.
    """
    return [
        {
            "name": t.name,
            "label": t.label,
            "api_base_url": t.api_base_url,
            "is_remote": t.is_remote,
        }
        for t in available_targets()
    ]


class TestConnectionRequest(BaseModel):
    email: str
    password: str
    # Empty when the user intends to create a brand-new entity — we then only
    # verify the login.
    entity_token: str = ""
    # Which CMS to test against. A NAME from GET /targets, never a URL: this
    # endpoint forwards the caller's credentials, so accepting a URL would make
    # an unauthenticated local endpoint a credential-forwarding proxy to any
    # host. None ⇒ the default target.
    target: str | None = None


@router.post("/test-connection")
async def test_connection(payload: TestConnectionRequest) -> dict[str, Any]:
    """
    Verify CMS creds + (when given) that the user has access to the entity.
    Returns the entity's existing page count so the UI can warn if not empty.

    With no entity_token (create-new-entity mode) we just confirm the login
    succeeds — there's no entity to inspect yet.
    """
    client = CmsClient.for_target(_resolve(payload.target))
    try:
        try:
            await client.login(payload.email, payload.password)
        except CmsApiError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

        if not payload.entity_token.strip():
            return {"ok": True, "existing_page_count": 0, "existing_pages": []}

        try:
            pages = await client.list_pages(payload.entity_token)
        except CmsApiError as exc:
            raise HTTPException(status_code=exc.status, detail=str(exc)) from exc
    finally:
        await client.aclose()

    return {
        "ok": True,
        "existing_page_count": len(pages),
        "existing_pages": [
            {
                "id": p.get("id"),
                "title": p.get("title"),
                "slug": p.get("slug"),
                "isHomepage": p.get("isHomepage"),
            }
            for p in pages[:20]
        ],
    }


class PushRequestBody(BaseModel):
    """Site is the full GeneratedSite payload from a prior /generate call."""

    site: GeneratedSite
    email: str
    password: str
    entity_token: str = ""
    publish: bool = False
    force_overwrite: bool = False
    push_builder_styles: bool = Field(
        default=True,
        description="Apply the generated theme via the launch-code → /builder/styles bridge.",
    )
    create_entity: bool = Field(
        default=False,
        description="Create a new entity (owned by the logged-in user) and push into it; entity_token is ignored.",
    )
    new_entity_name: str | None = None
    new_entity_url: str | None = None
    target: str | None = Field(
        default=None,
        description="Which CMS to push into — a name from GET /api/cms/targets, "
        "never a URL. None means the default target.",
    )


@router.post("/push")
async def push(payload: PushRequestBody) -> dict[str, Any]:
    """
    Run the full push pipeline. Returns a structured report so the frontend
    can show a per-step progress table.

    Failures don't raise — the report's `success: False` + `error` field tell
    the user what went wrong. The report still includes every step that DID
    succeed for diagnostics.
    """
    target = _resolve(payload.target)
    req = PushRequest(
        site=payload.site,
        target=target,
        cms_email=payload.email,
        cms_password=payload.password,
        entity_token=payload.entity_token,
        publish=payload.publish,
        force_overwrite=payload.force_overwrite,
        push_builder_styles=payload.push_builder_styles,
        create_entity=payload.create_entity,
        new_entity_name=payload.new_entity_name,
        new_entity_url=payload.new_entity_url,
        collections=payload.site.collections,
    )
    report = await push_site(req)
    return {
        "success": report.success,
        "error": report.error,
        "steps": [asdict(s) for s in report.steps],
        "page_urls": report.page_urls,
        "admin_url": _admin_url(target) if report.success else None,
    }
