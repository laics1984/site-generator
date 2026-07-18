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


def test_media_upload_failure_is_non_fatal_and_pages_still_created():
    """Individual media upload failures are skipped — the push continues and
    creates pages (failed images are stripped from the schema)."""
    site = _site_with_pages(2, images_per_page=2)
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
            "upload_media",
            new=AsyncMock(side_effect=CmsApiError(500, "upload exploded")),
        ),
        patch.object(
            CmsClient,
            "create_page",
            new=AsyncMock(return_value={"id": "page-1", "draftVersion": 1}),
        ) as create_page,
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
    ):
        report = asyncio.run(push_site(req))

    assert report.success, report.error
    media_step = next(s for s in report.steps if s.name == "media")
    assert media_step.ok
    assert media_step.data["failed"] == 4
    create_page.assert_awaited()


# --- _coerce_to_cms_image: transcode source formats the CMS can't store -------

from io import BytesIO  # noqa: E402

from PIL import Image  # noqa: E402

from app.services.push_orchestrator import _coerce_to_cms_image  # noqa: E402


def _encode(mode: str, fmt: str, *, size=(8, 8)) -> bytes:
    if mode == "RGBA":
        img = Image.new("RGBA", size, (10, 20, 30, 128))
    else:
        img = Image.new("RGB", size, (10, 20, 30))  # PIL down-converts for GIF/P
    buf = BytesIO()
    img.save(buf, format=fmt)
    return buf.getvalue()


def test_coerce_passes_through_jpeg_and_png():
    jpg = _encode("RGB", "JPEG")
    assert _coerce_to_cms_image(jpg, "image/jpeg", "photo.jpg") == (
        jpg, "image/jpeg", "photo.jpg",
    )
    png = _encode("RGB", "PNG")
    assert _coerce_to_cms_image(png, "image/png", "logo.png") == (
        png, "image/png", "logo.png",
    )


def test_coerce_passes_webp_avif_gif_through_untouched():
    # Now natively storable by the CMS — keep the original bytes (webp/avif keep
    # their size advantage), just normalize the filename extension.
    webp = _encode("RGBA", "WEBP")
    assert _coerce_to_cms_image(webp, "image/webp", "hero.webp") == (
        webp, "image/webp", "hero.webp",
    )
    # AVIF isn't decoded on this path, so raw bytes suffice to prove passthrough.
    avif = b"\x00\x00\x00 ftypavif-fake-bytes"
    assert _coerce_to_cms_image(avif, "image/avif", "pic.avif") == (
        avif, "image/avif", "pic.avif",
    )
    gif = _encode("RGB", "GIF")
    assert _coerce_to_cms_image(gif, "image/gif", "anim.gif") == (
        gif, "image/gif", "anim.gif",
    )


def test_coerce_trusts_filename_extension_when_mime_is_mislabeled():
    # Servers frequently serve svg/avif as text/plain or octet-stream.
    svg = b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"
    assert _coerce_to_cms_image(svg, "text/plain", "icon.svg") == (
        svg, "image/svg+xml", "icon.svg",
    )
    avif = b"\x00\x00\x00 ftypavif"
    assert _coerce_to_cms_image(avif, "application/octet-stream", "x.avif") == (
        avif, "image/avif", "x.avif",
    )


def test_coerce_transcodes_non_native_format_to_storable_image():
    # BMP isn't in the CMS's accepted set → transcode to jpg/png.
    bmp = _encode("RGB", "BMP")
    body, mime, name = _coerce_to_cms_image(bmp, "image/bmp", "old.bmp")
    assert mime == "image/jpeg"
    assert name == "old.jpg"
    assert Image.open(BytesIO(body)).format == "JPEG"


def test_coerce_returns_none_for_non_image_bytes():
    assert _coerce_to_cms_image(b"not an image", "image/bmp", "broken.bmp") is None


# --- _strip_invalid_images: drop dead/404 image references ---------------------

from app.services.push_orchestrator import _strip_invalid_images  # noqa: E402


def _img(src: str) -> BuilderElement:
    return BuilderElement(
        name="Image", type="image", styles={}, content=BuilderElementContent(src=src, alt=""),
    )


def test_strip_removes_dead_img_and_bg_layer_keeps_valid():
    dead = "https://src.example/gone.jpg"
    good = "https://cms.example/storage/ok.jpg"
    hero = BuilderElement(
        name="Hero", type="container",
        styles={"backgroundImage": f"linear-gradient(#000, #000), url('{dead}')"},
        content=[_img(dead), _img(good)],
    )
    keep_bg = BuilderElement(
        name="Band", type="container",
        styles={"backgroundImage": f"url('{good}')"}, content=[],
    )
    page = GeneratedPage(
        slug="", title="Home", is_homepage=True,
        body_schema=BodySchema(elements=[hero, keep_bg]), seo=PageSeo(),
    )
    site = GeneratedSite(
        site_name="S", pages=[page], page_tree=[], builder_styles={},
        header_schema=BuilderElement(name="H", type="__header", content=[_img(dead)]),
        footer_schema=BuilderElement(name="F", type="__footer", content=[]),
    )

    removed = _strip_invalid_images(site, {dead})

    # dead <img> in hero + dead <img> in header + dead bg layer = 3
    assert removed == 3
    hero_out = site.pages[0].body_schema.elements[0]
    srcs = [c.content.src for c in hero_out.content]
    assert srcs == [good]  # dead image dropped, valid kept
    assert dead not in hero_out.styles.get("backgroundImage", "")
    assert "linear-gradient" in hero_out.styles["backgroundImage"]  # gradient kept
    assert site.pages[0].body_schema.elements[1].styles["backgroundImage"] == f"url('{good}')"
    assert site.header_schema.content == []  # dead header logo removed


def test_strip_noop_when_nothing_failed():
    good = "https://cms.example/storage/ok.jpg"
    page = GeneratedPage(
        slug="", title="Home", is_homepage=True,
        body_schema=BodySchema(elements=[_img(good)]), seo=PageSeo(),
    )
    site = GeneratedSite(
        site_name="S", pages=[page], page_tree=[], builder_styles={},
        header_schema=None, footer_schema=None,
    )
    assert _strip_invalid_images(site, set()) == 0
    assert len(site.pages[0].body_schema.elements) == 1


# --- httpx exception wrapping: push_site returns a report, never a 500 -------

import httpx  # noqa: E402


def test_httpx_timeout_during_media_upload_skips_image_and_continues():
    """The exact bug that caused the original 500: httpx.ReadTimeout in upload_media.
    Now the timed-out image is skipped (with one retry), and the push continues."""
    site = _site_with_pages(1, images_per_page=1)
    req = PushRequest(
        site=site,
        cms_email="u@e.com",
        cms_password="pw",
        entity_token="tok",
        publish=False,
        push_builder_styles=False,
    )
    with (
        patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")),
        patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
        patch.object(
            CmsClient,
            "upload_media",
            new=AsyncMock(side_effect=CmsApiError(504, "CMS request timed out during upload_media")),
        ),
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
    ):
        report = asyncio.run(push_site(req))
    assert report.success, report.error
    media_step = next(s for s in report.steps if s.name == "media")
    assert media_step.data["failed"] == 1


def test_unexpected_exception_caught_by_push_site():
    """Any non-CmsApiError exception → structured report, never raises."""
    req = PushRequest(
        site=_minimal_site(),
        cms_email="u@e.com",
        cms_password="pw",
        entity_token="tok",
    )
    with patch.object(
        CmsClient, "login",
        new=AsyncMock(side_effect=RuntimeError("something broke")),
    ):
        report = asyncio.run(push_site(req))
    assert not report.success
    assert "Unexpected error" in (report.error or "")
    assert "something broke" in (report.error or "")


def test_wrap_request_maps_connect_error_to_cms_api_error():
    import pytest
    client = CmsClient(base_url="http://localhost:9999")
    client.jwt = "fake-jwt"

    async def _run():
        async with client._wrap_request("test_op"):
            raise httpx.ConnectError("refused")

    with pytest.raises(CmsApiError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.status == 503
    assert "test_op" in str(exc_info.value)


def test_wrap_request_maps_timeout_to_cms_api_error():
    import pytest
    client = CmsClient(base_url="http://localhost:9999")

    async def _run():
        async with client._wrap_request("test_op"):
            raise httpx.ReadTimeout("timed out")

    with pytest.raises(CmsApiError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.status == 504


def test_wrap_request_passes_cms_api_error_through():
    import pytest
    client = CmsClient(base_url="http://localhost:9999")

    async def _run():
        async with client._wrap_request("test_op"):
            raise CmsApiError(422, "validation failed")

    with pytest.raises(CmsApiError) as exc_info:
        asyncio.run(_run())
    assert exc_info.value.status == 422  # not re-wrapped to 502
