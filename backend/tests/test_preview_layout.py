"""
Preview-layout tests.

The point of `POST /api/preview/layout` is that the preview renders the SAME
layout the push writes — menus a `menu` element can resolve, and the header
`behavior` that drives overlay/shrink. The parity test below is the guard: if
someone changes how the push assembles that payload without routing the preview
through the same builder, the preview silently goes back to lying about what the
published site looks like.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    GeneratedPage,
    GeneratedSite,
    PageNode,
    PageSeo,
)
from app.services.cms_client import CmsClient
from app.services.push_orchestrator import PushRequest, push_site


def _site(*, header_overlay: bool = False) -> GeneratedSite:
    pages = [
        GeneratedPage(
            slug="",
            title="Home",
            is_homepage=True,
            body_schema=BodySchema(elements=[]),
            seo=PageSeo(),
        ),
        GeneratedPage(
            slug="about",
            title="About",
            is_homepage=False,
            body_schema=BodySchema(elements=[]),
            seo=PageSeo(),
        ),
        GeneratedPage(
            slug="privacy",
            title="Privacy",
            is_homepage=False,
            body_schema=BodySchema(elements=[]),
            seo=PageSeo(),
        ),
    ]
    return GeneratedSite(
        site_name="Parity Co",
        pages=pages,
        page_tree=[
            PageNode(slug="", title="Home", is_homepage=True, children=[]),
            PageNode(slug="about", title="About", is_homepage=False, children=[]),
        ],
        social_links=[("Instagram", "https://instagram.com/parity")],
        header_overlay=header_overlay,
        header_schema=BuilderElement(name="Header", type="__header", content=[]),
        footer_schema=BuilderElement(name="Footer", type="__footer", content=[]),
    )


def _push_layout_args(site: GeneratedSite) -> dict:
    """Run a push against a fully mocked CMS and return save_page_layout's kwargs."""
    req = PushRequest(
        site=site,
        cms_email="user@example.com",
        cms_password="secret",
        entity_token="entity-token",
        publish=False,
        push_builder_styles=False,
    )

    with (
        patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")),
        patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
        patch.object(
            CmsClient,
            "create_page",
            new=AsyncMock(return_value={"id": "page-1", "draftVersion": 1}),
        ),
        patch.object(
            CmsClient,
            "get_builder_payload",
            new=AsyncMock(return_value={"layout": {"versionId": "V0"}}),
        ),
        patch.object(
            CmsClient,
            "save_page_layout",
            new=AsyncMock(return_value={"versionId": "V1"}),
        ) as save_page_layout,
        patch.object(
            CmsClient,
            "save_page_draft",
            new=AsyncMock(return_value={"draftVersion": 2}),
        ),
        patch.object(CmsClient, "mint_builder_session", new=AsyncMock(return_value=None)),
    ):
        report = asyncio.run(push_site(req))

    assert report.success, report.error
    save_page_layout.assert_awaited_once()
    return save_page_layout.call_args.kwargs


def _strip_item_ids(menus: list[dict]) -> list[dict]:
    """Menu-item ids are fresh uuid4s per build, so they never compare equal.
    Everything that decides what renders — labels, hrefs, order, slots — does."""
    return [
        {**menu, "items": [{k: v for k, v in item.items() if k != "id"} for item in menu["items"]]}
        for menu in menus
    ]


def test_preview_layout_matches_what_the_push_sends():
    site = _site()
    client = TestClient(app)

    preview = client.post("/api/preview/layout", json=site.model_dump(mode="json"))
    assert preview.status_code == 200
    body = preview.json()

    pushed = _push_layout_args(site)

    assert _strip_item_ids(body["menus"]) == _strip_item_ids(pushed["menus"])
    assert body["header"] == pushed["header_schema"]
    assert body["footer"] == pushed["footer_schema"]


def test_preview_layout_carries_overlay_behavior():
    client = TestClient(app)

    overlay = client.post(
        "/api/preview/layout", json=_site(header_overlay=True).model_dump(mode="json")
    ).json()
    plain = client.post(
        "/api/preview/layout", json=_site(header_overlay=False).model_dump(mode="json")
    ).json()

    # Without this the preview can't reproduce a transparent header floating
    # over a full-bleed hero — the single most visible header/hero difference.
    assert overlay["header"]["behavior"]["overlay"] is True
    assert plain["header"]["behavior"]["overlay"] is False


def test_preview_layout_resolves_menu_slots():
    body = TestClient(app).post(
        "/api/preview/layout", json=_site().model_dump(mode="json")
    ).json()

    # A `menu` element resolves its items by slot against menus[]; the preview
    # renders empty nav if these don't line up.
    menu_ids = {m["id"] for m in body["menus"]}
    assert body["header"]["slots"]["primaryMenuId"] in menu_ids
    assert body["footer"]["slots"]["footerMenuId"] in menu_ids
    assert body["footer"]["slots"]["legalMenuId"] in menu_ids
    assert body["footer"]["slots"]["socialMenuId"] in menu_ids


def test_preview_layout_rejects_a_site_without_header_schema():
    site = _site().model_dump(mode="json")
    site["header_schema"] = None

    response = TestClient(app).post("/api/preview/layout", json=site)
    assert response.status_code == 422
