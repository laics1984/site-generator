"""The preview is two pages, one full menu, and an additive CMS extension."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.models.builder_schema import BodySchema, BuilderElement, GeneratedPage, GeneratedSite, PageSeo
from app.models.content_blocks import NavLink, SourceContent
from app.models.industry import PageScaffold
from app.routers.generate import GenerateWithPagesRequest, generate_with_pages
from app.services.cms_client import CmsClient
from app.services.cms_sync import ExistingPage, plan_sync
from app.services import db, generation_projects
from app.services.menu_builder import build_layout_payload
from app.services.push_orchestrator import MediaUpload, PushRequest, inspect_entity, push_site


def page(slug: str, *, home: bool = False) -> GeneratedPage:
    return GeneratedPage(
        slug=slug, title="Home" if home else slug.title(), is_homepage=home,
        body_schema=BodySchema(elements=[]), seo=PageSeo(),
    )


def site(*pages: GeneratedPage) -> GeneratedSite:
    return GeneratedSite(
        site_name="Example", pages=list(pages),
        header_schema=BuilderElement(name="Header", type="__header", content=[]),
        footer_schema=BuilderElement(name="Footer", type="__footer", content=[]),
    )


def test_preview_keeps_full_source_menu_without_creating_pages() -> None:
    preview = site(page("", home=True), page("about"))
    preview.source_navigation = [
        NavLink(label="Home", href="/"),
        *[NavLink(label=f"Section {n}", href=f"/section-{n}") for n in range(10)],
        NavLink(label="Services", href="/services", children=[
            NavLink(label="Detail", href="/services/detail"),
        ]),
    ]

    layout = build_layout_payload(preview)
    primary = next(menu for menu in layout.menus if menu["id"] == "menu-primary")
    assert len(preview.pages) == 2
    assert [item["label"] for item in primary["items"]] == [item.label for item in preview.source_navigation]
    assert primary["items"][-1]["children"][0]["href"] == "/services/detail"


def test_extend_plan_adds_pages_without_archiving_or_rewriting_preview() -> None:
    extension = site(page("services"))
    existing = [
        ExistingPage(id="home-id", slug="", title="Home", status="published", is_homepage=True),
        ExistingPage(id="about-id", slug="about", title="About", status="published"),
        ExistingPage(id="other-id", slug="owner-page", title="Owner page", status="published"),
    ]

    plan = plan_sync(extension, existing, operation="extend")
    assert [(change.action, change.slug) for change in plan.changes] == [("create", "services")]
    assert plan.of("archive") == []

    with pytest.raises(ValueError, match="outside this generation project"):
        plan_sync(site(page("owner-page")), existing, operation="extend")


def test_remaining_mode_requires_saved_project_endpoint() -> None:
    payload = GenerateWithPagesRequest(
        source=SourceContent(source_kind="paste", source_ref="test", raw_text="Example"),
        selected_pages=[PageScaffold(page_type="home", slug="", title="Home", sections=[], is_homepage=True)],
        generation_mode="remaining",
    )
    with pytest.raises(Exception, match="saved project endpoint"):
        asyncio.run(generate_with_pages(payload))


def test_extension_rejects_preview_page_even_when_its_id_is_known() -> None:
    req = PushRequest(
        site=site(page("about")), cms_email="owner@example.com",
        cms_password="secret", entity_token="token", operation="extend",
        generation_project_id="project-1", publish=True,
    )
    project = {
        "status": "preview_published", "cms_target": "default", "entity_id": "entity-1",
        "page_ids": {"about": "about-id"},
        "preview_site": {"pages": [{"slug": ""}, {"slug": "about"}]},
        "published_slugs": ["", "about"],
        "request": {"selected_pages": [{"slug": ""}, {"slug": "about"}, {"slug": "services"}]},
    }
    with (
        patch("app.services.push_orchestrator.get_project", new=AsyncMock(return_value=project)),
        patch.object(CmsClient, "list_entities", new=AsyncMock(return_value=[
            {"entity_api_token": "token", "entity_id": "entity-1"}
        ])),
    ):
        with pytest.raises(ValueError, match="not pending"):
            asyncio.run(inspect_entity(CmsClient.for_target(req.target), req))


def test_extend_push_reuses_layout_and_publishes_only_new_page() -> None:
    extension = site(page("services"))
    req = PushRequest(
        site=extension, cms_email="owner@example.com", cms_password="secret",
        entity_token="token", operation="extend", generation_project_id="project-1",
        publish=True,
    )
    project = {
        "status": "preview_published", "cms_target": "default", "entity_id": "entity-1",
        "page_ids": {}, "request": {"selected_pages": [{"slug": "services"}]},
        "preview_site": {"pages": [{"slug": ""}, {"slug": "about"}]},
        "published_slugs": [],
    }
    rows = [
        {"id": "home-id", "slug": "", "title": "Home", "status": "published", "isHomepage": True},
        {"id": "about-id", "slug": "about", "title": "About", "status": "published"},
    ]
    with (
        patch("app.services.push_orchestrator.get_project", new=AsyncMock(return_value=project)),
        patch("app.services.push_orchestrator.record_page", new=AsyncMock()) as record,
        patch("app.services.push_orchestrator.mark_page_published", new=AsyncMock()) as published,
        patch("app.services.push_orchestrator.mark_complete", new=AsyncMock()),
        patch("app.services.push_orchestrator._upload_media", new=AsyncMock(return_value=MediaUpload({}, set()))),
        patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")),
        patch.object(CmsClient, "list_entities", new=AsyncMock(return_value=[
            {"entity_api_token": "token", "entity_id": "entity-1", "public_url": "https://preview.example"}
        ])),
        patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=rows)),
        patch.object(CmsClient, "create_page", new=AsyncMock(return_value={"id": "new-id", "draftVersion": 1})),
        patch.object(CmsClient, "get_builder_payload", new=AsyncMock(return_value={"layout": {"versionId": "V1"}})),
        patch.object(CmsClient, "save_page_layout", new=AsyncMock()) as save_layout,
        patch.object(CmsClient, "save_page_draft", new=AsyncMock(return_value={"draftVersion": 2})),
        patch.object(CmsClient, "publish_page", new=AsyncMock()) as publish_page,
        patch.object(CmsClient, "archive_page", new=AsyncMock()) as archive_page,
        patch.object(CmsClient, "update_page", new=AsyncMock()) as update_page,
    ):
        report = asyncio.run(push_site(req))

    assert report.success, report.error
    save_layout.assert_not_awaited()
    archive_page.assert_not_awaited()
    update_page.assert_not_awaited()
    publish_page.assert_awaited_once()
    record.assert_awaited_once_with("project-1", "services", "new-id")
    published.assert_awaited_once_with("project-1", "services")


def test_generation_project_persists_identity_and_publish_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "sitegen.db")
    monkeypatch.setattr(db, "_BOOTSTRAPPED", False)
    preview = site(page("", home=True), page("about"))

    async def exercise() -> None:
        project_id = await generation_projects.create_project(
            {"selected_pages": [{"slug": ""}, {"slug": "about"}, {"slug": "services"}]},
            preview,
        )
        await generation_projects.bind_project(
            project_id, target="default", entity_id="entity-1",
            public_url="https://preview.example",
        )
        await generation_projects.record_page(project_id, "", "home-id")
        await generation_projects.record_page(project_id, "about", "about-id")
        await generation_projects.mark_page_published(project_id, "")
        await generation_projects.mark_page_published(project_id, "about")
        await generation_projects.mark_preview_published(project_id)
        stored = await generation_projects.get_project(project_id)
        assert stored is not None
        assert stored["entity_id"] == "entity-1"
        assert stored["status"] == "preview_published"
        assert stored["page_ids"] == {"": "home-id", "about": "about-id"}
        assert set(stored["published_slugs"]) == {"", "about"}
        assert stored["request"]["selected_pages"][2]["slug"] == "services"
        assert len(stored["preview_site"]["pages"]) == 2

    try:
        asyncio.run(exercise())
    finally:
        monkeypatch.setattr(db, "_BOOTSTRAPPED", False)
