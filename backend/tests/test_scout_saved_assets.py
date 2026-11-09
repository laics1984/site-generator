"""The Scout push publishes exactly the files captured before review."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings
from app.models.builder_schema import BuilderElement
from app.services.cms_client import CmsApiError, CmsClient
from app.services.cms_targets import default_target
from app.services.push_orchestrator import PushReport, PushRequest, push_site
from app.services.scout_assets import (
    asset_key, freeze_assets, references, rewrite_responsive_assets, unpack_assets,
)
from tests.test_push_orchestrator import _minimal_site

PHOTO = "https://images.pexels.com/home.png"
BACKGROUND = "https://images.pexels.com/banner.png"
DOCUMENT = "https://centre.example.com/brochure.pdf"
LOGO = "data:image/svg+xml,%3Csvg%20xmlns%3D%22http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%22%2F%3E"
PDF = b"%PDF-1.4 saved brochure"


def site_with_assets():
    site = _minimal_site()
    site.header_schema.content = [
        BuilderElement(type="image", name="Logo", content={"src": LOGO}),
    ]
    site.pages[0].body_schema.elements = [
        BuilderElement(
            type="section", name="Hero", styles={"background": f'url("{BACKGROUND}")'},
            responsiveStyles={"mobile": {"backgroundImage": f'url("{PHOTO}")'}},
            content=[
                BuilderElement(type="image", name="Photo", content={"src": PHOTO}),
                BuilderElement(type="link", name="Brochure", content={"href": DOCUMENT}),
            ],
        )
    ]
    return site


def test_freeze_captures_logo_stock_background_and_download():
    site = site_with_assets()

    async def read(src, *, image, client):
        if src == DOCUMENT:
            return PDF, "application/pdf", "brochure.pdf"
        return b"captured:" + src.encode(), "image/png", "original.png"

    with patch("app.services.scout_assets._read_reference", side_effect=read):
        saved = asyncio.run(freeze_assets(site))
    assert set(saved) == {asset_key(src) for src in (LOGO, PHOTO, BACKGROUND, DOCUMENT)}
    upload = unpack_assets(site, saved)
    assert upload[DOCUMENT] == (PDF, "application/pdf", "brochure.pdf")
    assert upload[PHOTO][0] == b"captured:" + PHOTO.encode()
    assert upload[BACKGROUND][0] == b"captured:" + BACKGROUND.encode()
    assert site.pages[0].body_schema.elements[0].responsiveStyles.mobile["backgroundImage"] == f'url("{PHOTO}")'
    rewrite_responsive_assets(site, {PHOTO: "https://asset.example.com/photo.png"})
    assert site.pages[0].body_schema.elements[0].responsiveStyles.mobile["backgroundImage"] == (
        'url("https://asset.example.com/photo.png")'
    )


def test_unsupported_download_blocks_saving():
    site = site_with_assets()
    site.pages[0].body_schema.elements[0].content[1].content.href = "https://centre.example.com/deck.pptx"
    with pytest.raises(ValueError, match="cannot store the .pptx"):
        references(site)


def test_saved_publish_rejects_missing_files_before_cms_calls():
    with patch("app.routers.cms.push_site", new=AsyncMock()) as push:
        response = TestClient(app).post("/api/cms/scout/publish", json={
            "site": site_with_assets().model_dump(mode="json"),
            "assets": {},
            "target": "default",
            "expected_cms_origin": default_target().api_base_url,
            "email": "user@example.com", "password": "secret", "entity_token": "token",
        })
    assert response.status_code == 422
    push.assert_not_awaited()


@pytest.mark.parametrize("origin,expected", [("http://localhost", 200), ("https://cms.example.com", 409)])
def test_scout_builder_import_stays_local_and_unpublished(monkeypatch, origin, expected):
    monkeypatch.setattr(settings, "cms_api_base_url", origin)
    key = "7cc68a8d-eed5-4e90-a66b-44706e932f62"
    saved_assets = {PHOTO: (b"saved photo", "image/png", "photo.png")}
    with (
        patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")) as login,
        patch.object(CmsClient, "account_id", new=AsyncMock(return_value="owner")),
        patch.object(CmsClient, "provision_prospect_preview", new=AsyncMock(return_value={
            "entity_id": "site-id", "entity_api_token": "site-secret",
        })) as reserve,
        patch("app.routers.cms.unpack_assets", return_value=saved_assets),
        patch("app.routers.cms.push_site", new=AsyncMock(return_value=PushReport(
            success=True, page_urls={"page-id": "/"},
        ))) as push,
    ):
        response = TestClient(app).post("/api/cms/scout/local-draft", json={
            "site": site_with_assets().model_dump(mode="json"),
            "assets": {}, "target": "default", "draft_key": key,
            "email": "user@example.com", "password": "secret",
        })
    assert response.status_code == expected
    if expected == 409:
        login.assert_not_awaited()
        push.assert_not_awaited()
        return
    assert response.json() == {"entity_id": "site-id", "pages": [{"id": "page-id", "title": site_with_assets().pages[0].title}]}
    assert reserve.await_args.args[0]["expected_owner_id"] == "owner"
    assert reserve.await_args.args[0]["preview_key"] == key
    request = push.await_args.args[0]
    assert request.publish is False and request.saved_assets == saved_assets


def test_failed_upload_stops_before_any_pages_are_written():
    site = site_with_assets()
    req = PushRequest(
        site=site, cms_email="user@example.com", cms_password="secret", entity_token="token",
        publish=True, saved_assets={PHOTO: (b"saved photo", "image/png", "photo.png")},
    )
    with (
        patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")),
        patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
        patch.object(CmsClient, "get_storage_usage", new=AsyncMock(return_value={})),
        patch.object(CmsClient, "lookup_media", new=AsyncMock(return_value=None)),
        patch.object(CmsClient, "upload_media", new=AsyncMock(side_effect=CmsApiError(422, "too large"))) as upload,
        patch.object(CmsClient, "create_page", new=AsyncMock()) as create_page,
        patch("app.services.push_orchestrator._upload_media", new=AsyncMock()) as normal_upload,
    ):
        report = asyncio.run(push_site(req))

    assert not report.success
    assert "saved asset could not be stored" in report.error
    assert upload.await_args.kwargs["file_bytes"] == b"saved photo"
    normal_upload.assert_not_awaited()
    create_page.assert_not_awaited()


def test_saved_publish_rehosts_assets_in_page_and_layout_schemas():
    site = site_with_assets()
    file_names = {PHOTO: "home.png", BACKGROUND: "banner.png", LOGO: "logo.svg", DOCUMENT: "brochure.pdf"}

    async def read(src, *, image, client):
        if src == DOCUMENT:
            return PDF, "application/pdf", file_names[src]
        mime = "image/svg+xml" if src == LOGO else "image/png"
        return f"saved {file_names[src]}".encode(), mime, file_names[src]

    async def upload(entity_token, *, file_bytes, filename, content_type):
        assert entity_token == "token"
        return f"https://asset.fowable.com/{filename}"

    client = TestClient(app)
    with patch("app.services.scout_assets._read_reference", side_effect=read):
        frozen = client.post("/api/cms/scout/freeze", json=site.model_dump(mode="json"))
    assert frozen.status_code == 200
    with (
        patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")),
        patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
        patch.object(CmsClient, "get_storage_usage", new=AsyncMock(return_value={})),
        patch.object(CmsClient, "lookup_media", new=AsyncMock(return_value=None)),
        patch.object(CmsClient, "upload_media", new=AsyncMock(side_effect=upload)) as uploads,
        patch.object(CmsClient, "create_page", new=AsyncMock(return_value={"id": "page", "draftVersion": 1})),
        patch.object(CmsClient, "get_builder_payload", new=AsyncMock(return_value={"layout": {"versionId": "v1"}})),
        patch.object(CmsClient, "save_page_layout", new=AsyncMock(return_value={"versionId": "v2"})) as layout,
        patch.object(CmsClient, "save_page_draft", new=AsyncMock(return_value={"draftVersion": 2})) as draft,
        patch.object(CmsClient, "update_builder_styles", new=AsyncMock(return_value={})),
        patch.object(CmsClient, "publish_page", new=AsyncMock(return_value={})) as publish,
        patch("app.services.scout_assets._read_reference", new=AsyncMock()) as source_fetch,
    ):
        result = client.post("/api/cms/scout/publish", json={
            "site": site.model_dump(mode="json"), "assets": frozen.json(), "target": "default",
            "expected_cms_origin": default_target().api_base_url,
            "email": "user@example.com", "password": "secret", "entity_token": "token",
        })

    assert result.json()["success"], result.json()
    source_fetch.assert_not_awaited()
    publish.assert_awaited_once()
    files = {call.kwargs["filename"]: call.kwargs["file_bytes"] for call in uploads.await_args_list}
    assert files == {
        "home.png": b"saved home.png", "banner.png": b"saved banner.png",
        "logo.svg": b"saved logo.svg", "brochure.pdf": PDF,
    }
    assert "https://asset.fowable.com/logo.svg" in str(layout.await_args.kwargs["header_schema"])
    hero, = draft.await_args.kwargs["body_schema"]["elements"]
    assert hero["styles"]["background"] == 'url("https://asset.fowable.com/banner.png")'
    assert hero["responsiveStyles"]["mobile"]["backgroundImage"] == 'url("https://asset.fowable.com/home.png")'
    assert hero["content"][0]["content"]["src"] == "https://asset.fowable.com/home.png"
    assert hero["content"][1]["content"]["href"] == "https://asset.fowable.com/brochure.pdf"
