"""
Regression test for the LAYOUT_VERSION_CONFLICT bug: saving builder_styles
(step 8) mints a new layout version on the CMS side, so the `publish` step
(step 9) must use THAT version id, not the one captured during save_layout
(step 6).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.services.cms_client import CmsClient
from app.services.push_orchestrator import PushRequest, push_site


def _minimal_site() -> GeneratedSite:
    home = GeneratedPage(
        slug="",
        title="Home",
        is_homepage=True,
        body_schema=BodySchema(elements=[]),
        seo=PageSeo(),
    )
    return GeneratedSite(
        site_name="Test Site",
        pages=[home],
        page_tree=[],
        builder_styles={"colors": {"primary": "#112233"}},
        header_schema=BuilderElement(name="Header", type="__header", content=[]),
        footer_schema=BuilderElement(name="Footer", type="__footer", content=[]),
    )


def test_publish_uses_layout_version_refreshed_by_builder_styles():
    req = PushRequest(
        site=_minimal_site(),
        cms_email="user@example.com",
        cms_password="secret",
        entity_token="entity-token",
        publish=True,
        push_builder_styles=True,
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
        ),
        patch.object(
            CmsClient,
            "save_page_draft",
            new=AsyncMock(return_value={"draftVersion": 2}),
        ),
        patch.object(CmsClient, "mint_builder_session", new=AsyncMock(return_value=None)),
        patch.object(
            CmsClient,
            "update_builder_styles",
            new=AsyncMock(return_value={"data": {"layout": {"versionId": "V2"}}}),
        ),
        patch.object(CmsClient, "publish_page", new=AsyncMock(return_value={})) as publish_page,
    ):
        report = asyncio.run(push_site(req))

    assert report.success, report.error
    publish_page.assert_awaited_once()
    _, kwargs = publish_page.call_args
    # V2 is the version minted by update_builder_styles, not V1 from save_layout.
    assert kwargs["expected_layout_version_id"] == "V2"
