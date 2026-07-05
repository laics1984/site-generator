"""
Push-orchestrator tests:

- Regression for the LAYOUT_VERSION_CONFLICT bug: saving builder_styles
  (step 8) mints a new layout version on the CMS side, so the `publish` step
  (step 9) must use THAT version id, not the one captured during save_layout
  (step 6).
- Concurrency behavior: media uploads / draft saves run in parallel but
  bounded, homepage is still created first, and a failed upload aborts the
  push before any page is created.
"""

from __future__ import annotations

import asyncio
import base64
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.services.cms_client import CmsApiError, CmsClient
from app.services.push_orchestrator import _PUSH_CONCURRENCY, PushRequest, push_site


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


def _data_url(seed: str) -> str:
    return "data:image/png;base64," + base64.b64encode(seed.encode()).decode()


def _site_with_pages(n: int, images_per_page: int = 0) -> GeneratedSite:
    pages = []
    for i in range(n):
        elements = [
            BuilderElement(
                name=f"Img {i}-{j}",
                type="image",
                content=BuilderElementContent(src=_data_url(f"img-{i}-{j}"), alt=""),
            )
            for j in range(images_per_page)
        ]
        pages.append(
            GeneratedPage(
                slug="" if i == 0 else f"page-{i}",
                title="Home" if i == 0 else f"Page {i}",
                is_homepage=i == 0,
                body_schema=BodySchema(elements=elements),
                seo=PageSeo(),
            )
        )
    return GeneratedSite(
        site_name="Test Site",
        pages=pages,
        page_tree=[],
        builder_styles=None,
        header_schema=BuilderElement(name="Header", type="__header", content=[]),
        footer_schema=BuilderElement(name="Footer", type="__footer", content=[]),
    )


class _InFlightTracker:
    """Async callable that records the max number of concurrent invocations."""

    def __init__(self, result_fn):
        self._result_fn = result_fn
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls = 0

    async def __call__(self, *args, **kwargs):
        self.in_flight += 1
        self.calls += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        await asyncio.sleep(0.005)
        self.in_flight -= 1
        return self._result_fn(*args, **kwargs)


def _base_patches(create_page_mock):
    return (
        patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")),
        patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
        patch.object(CmsClient, "create_page", new=create_page_mock),
        patch.object(
            CmsClient,
            "get_builder_payload",
            new=AsyncMock(return_value={"layout": {"versionId": "V0"}}),
        ),
        patch.object(
            CmsClient, "save_page_layout", new=AsyncMock(return_value={"versionId": "V1"})
        ),
    )


def _unique_create_page():
    counter = {"n": 0}

    async def _create(self, entity_token, *, title, **kwargs):
        counter["n"] += 1
        return {"id": f"page-{counter['n']}", "draftVersion": 1, "title": title}

    return _create


def test_media_uploads_and_drafts_run_concurrently_bounded():
    site = _site_with_pages(4, images_per_page=3)  # 12 unique data-url images
    req = PushRequest(
        site=site,
        cms_email="user@example.com",
        cms_password="secret",
        entity_token="entity-token",
        publish=False,
        push_builder_styles=False,
    )

    # NB: non-function callables patched onto the class don't get bound, so no `self`.
    upload_tracker = _InFlightTracker(
        lambda entity_token, **kw: f"https://cms/storage/{kw['filename']}"
    )
    draft_tracker = _InFlightTracker(
        lambda entity_token, page_id, **kw: {"draftVersion": kw["base_draft_version"] + 1}
    )

    with ExitStack() as stack:
        for p in _base_patches(_unique_create_page()):
            stack.enter_context(p)
        stack.enter_context(patch.object(CmsClient, "upload_media", new=upload_tracker))
        stack.enter_context(patch.object(CmsClient, "save_page_draft", new=draft_tracker))
        report = asyncio.run(push_site(req))

    assert report.success, report.error
    assert upload_tracker.calls == 12
    assert 1 < upload_tracker.max_in_flight <= _PUSH_CONCURRENCY
    assert draft_tracker.calls == 4
    assert 1 < draft_tracker.max_in_flight <= _PUSH_CONCURRENCY


def test_homepage_created_before_other_pages():
    site = _site_with_pages(5)
    req = PushRequest(
        site=site,
        cms_email="user@example.com",
        cms_password="secret",
        entity_token="entity-token",
        publish=False,
        push_builder_styles=False,
    )

    order: list[str] = []
    counter = {"n": 0}

    async def _create(self, entity_token, *, title, **kwargs):
        order.append(title)
        counter["n"] += 1
        return {"id": f"page-{counter['n']}", "draftVersion": 1}

    with ExitStack() as stack:
        for p in _base_patches(_create):
            stack.enter_context(p)
        stack.enter_context(
            patch.object(CmsClient, "upload_media", new=AsyncMock(return_value="https://cms/x.png"))
        )
        stack.enter_context(
            patch.object(
                CmsClient, "save_page_draft", new=AsyncMock(return_value={"draftVersion": 2})
            )
        )
        report = asyncio.run(push_site(req))

    assert report.success, report.error
    assert order[0] == "Home"
    assert len(order) == 5


def test_media_upload_failure_aborts_push_before_pages():
    site = _site_with_pages(2, images_per_page=2)
    req = PushRequest(
        site=site,
        cms_email="user@example.com",
        cms_password="secret",
        entity_token="entity-token",
    )

    create_page = AsyncMock(return_value={"id": "page-1", "draftVersion": 1})
    with (
        patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")),
        patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
        patch.object(
            CmsClient,
            "upload_media",
            new=AsyncMock(side_effect=CmsApiError(500, "upload exploded")),
        ),
        patch.object(CmsClient, "create_page", new=create_page),
    ):
        report = asyncio.run(push_site(req))

    assert not report.success
    media_step = next(s for s in report.steps if s.name == "media")
    assert not media_step.ok
    assert "upload exploded" in (media_step.error or "")
    create_page.assert_not_awaited()
