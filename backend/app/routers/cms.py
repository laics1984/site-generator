"""
CMS push endpoints — kicks off / inspects a push into the webtree CMS.

`POST /api/cms/test-connection` — verify creds + entity access without writing
`POST /api/cms/push`            — run the orchestrator and return a PushReport

The frontend renders PushReport.steps as a progress table.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.builder_schema import GeneratedSite
from app.services.cms_client import CmsApiError, CmsClient
from app.services.push_orchestrator import PushRequest, push_site

router = APIRouter(prefix="/api/cms", tags=["cms"])


class TestConnectionRequest(BaseModel):
    email: str
    password: str
    # Empty when the user intends to create a brand-new entity — we then only
    # verify the login.
    entity_token: str = ""


@router.post("/test-connection")
async def test_connection(payload: TestConnectionRequest) -> dict[str, Any]:
    """
    Verify CMS creds + (when given) that the user has access to the entity.
    Returns the entity's existing page count so the UI can warn if not empty.

    With no entity_token (create-new-entity mode) we just confirm the login
    succeeds — there's no entity to inspect yet.
    """
    client = CmsClient.for_default()
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


@router.post("/push")
async def push(payload: PushRequestBody) -> dict[str, Any]:
    """
    Run the full push pipeline. Returns a structured report so the frontend
    can show a per-step progress table.

    Failures don't raise — the report's `success: False` + `error` field tell
    the user what went wrong. The report still includes every step that DID
    succeed for diagnostics.
    """
    req = PushRequest(
        site=payload.site,
        cms_email=payload.email,
        cms_password=payload.password,
        entity_token=payload.entity_token,
        publish=payload.publish,
        force_overwrite=payload.force_overwrite,
        push_builder_styles=payload.push_builder_styles,
        create_entity=payload.create_entity,
        new_entity_name=payload.new_entity_name,
        new_entity_url=payload.new_entity_url,
    )
    report = await push_site(req)
    return {
        "success": report.success,
        "error": report.error,
        "steps": [asdict(s) for s in report.steps],
        "page_urls": report.page_urls,
    }
