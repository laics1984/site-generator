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
from app.services.cms_targets import CmsTarget
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


# --- slug normalization ---------------------------------------------------------

from app.models.builder_schema import PageNode  # noqa: E402
from app.services.push_orchestrator import _normalize_site_slugs  # noqa: E402


def _hierarchical_site() -> GeneratedSite:
    """A migrated site whose slugs are the source's own paths."""
    def _page(slug: str, *, parent: str | None = None, home: bool = False) -> GeneratedPage:
        return GeneratedPage(
            slug=slug,
            title=slug or "Home",
            is_homepage=home,
            body_schema=BodySchema(elements=[]),
            seo=PageSeo(),
            parent_slug=parent,
        )

    return GeneratedSite(
        site_name="MMTA",
        pages=[
            _page("", home=True),
            _page("committee"),
            _page("profile/ashley", parent="committee"),
        ],
        page_tree=[
            PageNode(
                slug="committee",
                title="Committee",
                children=[PageNode(slug="profile/ashley", title="Ashley Jinivon")],
            )
        ],
    )


def test_greenfield_push_keeps_the_source_url():
    # The whole point of the migration: mmta.org.my/profile/ashley still
    # resolves after the switchover, so its search ranking survives.
    site = _hierarchical_site()

    _normalize_site_slugs(site, keep_paths=True)

    assert [p.slug for p in site.pages] == ["", "committee", "profile/ashley"]
    assert site.pages[2].parent_slug == "committee"
    assert site.page_tree[0].children[0].slug == "profile/ashley"


def test_repush_over_a_live_site_still_flattens():
    # Renaming pages that are already published is the breakage this avoids.
    site = _hierarchical_site()

    _normalize_site_slugs(site, keep_paths=False)

    assert [p.slug for p in site.pages] == ["", "committee", "profile-ashley"]


def test_segments_are_still_sanitized_inside_a_kept_path():
    site = _hierarchical_site()
    site.pages[2].slug = "Profile/Kuek Ser Sheen Tse"

    _normalize_site_slugs(site, keep_paths=True)

    assert site.pages[2].slug == "profile/kuek-ser-sheen-tse"


# --- document (PDF/DOC/...) rehosting -------------------------------------------

from app.services.push_orchestrator import (  # noqa: E402
    _collect_document_hrefs,
    _needs_upload,
    _needs_upload_document,
    _resolve_document_to_bytes,
    _ResolveSkip,
    _rewrite_srcs,
    _upload_media,
)


def _link_el(href: str) -> BuilderElement:
    return BuilderElement(
        name="Link", type="link", styles={},
        content=BuilderElementContent(innerText="Download", href=href),
    )


def test_collect_document_hrefs_only_picks_link_type_document_extensions():
    out: dict[str, BuilderElement] = {}
    tree = BuilderElement(
        name="Card", type="container", styles={},
        content=[
            _link_el("https://mmta.org.my/files/brochure-en.pdf"),
            _link_el("https://mmta.org.my/about"),  # link, but not a document
            _img("https://mmta.org.my/photo.jpg"),  # image, not a link
        ],
    )
    _collect_document_hrefs(tree, out)
    assert list(out) == ["https://mmta.org.my/files/brochure-en.pdf"]


_TARGET = CmsTarget(name="default", api_base_url="http://localhost:8000")
_REMOTE = CmsTarget(name="remote", api_base_url="https://app-api.example.com")


def test_needs_upload_document_true_for_external_pdf():
    assert _needs_upload_document("https://mmta.org.my/files/brochure-en.pdf", _TARGET)


def test_needs_upload_document_false_for_already_hosted():
    assert not _needs_upload_document("https://cms.example/storage/brochure.pdf", _TARGET)


def test_needs_upload_document_false_for_non_document_link():
    assert not _needs_upload_document("https://mmta.org.my/about", _TARGET)


def test_needs_upload_document_false_for_non_http_scheme():
    assert not _needs_upload_document("mailto:info@mmta.org.my", _TARGET)


# --- _needs_upload: the image half, previously untested ------------------------


def test_needs_upload_true_for_data_uri_and_stock():
    assert _needs_upload("data:image/svg+xml;utf8,<svg/>", _TARGET)
    assert _needs_upload("https://images.pexels.com/photos/1/x.jpg", _TARGET)


def test_needs_upload_true_for_scraped_photo():
    assert _needs_upload("https://mmta.org.my/img/hero.jpg", _TARGET)


def test_needs_upload_false_for_non_http_scheme():
    assert not _needs_upload("mailto:info@mmta.org.my", _TARGET)


def test_needs_upload_false_when_already_on_the_target_host():
    assert not _needs_upload("http://localhost:8000/img/hero.jpg", _TARGET)
    assert not _needs_upload("https://cms.example/storage/hero.jpg", _TARGET)


def test_host_check_follows_the_target_not_settings():
    """The destination is per-push, so a settings-derived host would be wrong for
    every push that doesn't go to the default CMS."""
    from app.config import settings

    original = settings.cms_api_base_url
    settings.cms_api_base_url = "https://app-api.example.com"
    try:
        # Settings now name the remote host, but the DEFAULT target still doesn't.
        assert _needs_upload("https://app-api.example.com/img/a.jpg", _TARGET)
        assert not _needs_upload("https://app-api.example.com/img/a.jpg", _REMOTE)
        assert _needs_upload_document("https://app-api.example.com/a.pdf", _TARGET)
        assert not _needs_upload_document("https://app-api.example.com/a.pdf", _REMOTE)
    finally:
        settings.cms_api_base_url = original


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class _FakeClient:
    def __init__(self, response: _FakeResponse):
        self._response = response

    async def get(self, _href: str) -> _FakeResponse:
        return self._response


def test_resolve_document_to_bytes_success():
    client = _FakeClient(_FakeResponse(200, b"%PDF-1.4 fake"))
    body, content_type, filename = asyncio.run(
        _resolve_document_to_bytes("https://mmta.org.my/files/brochure-en.pdf", client)
    )
    assert body == b"%PDF-1.4 fake"
    assert content_type == "application/pdf"
    assert filename == "brochure-en.pdf"


def test_resolve_document_to_bytes_skips_unsupported_extension():
    import pytest

    # .ppt/.pptx are detected as document links (nav_extraction.DOCUMENT_EXTENSIONS)
    # but aren't in webtree-cms-api's upload mime whitelist — skip, don't upload.
    client = _FakeClient(_FakeResponse(200, b"fake"))
    with pytest.raises(_ResolveSkip):
        asyncio.run(
            _resolve_document_to_bytes("https://mmta.org.my/files/deck.pptx", client)
        )


def test_resolve_document_to_bytes_skips_404():
    import pytest

    client = _FakeClient(_FakeResponse(404))
    with pytest.raises(_ResolveSkip):
        asyncio.run(
            _resolve_document_to_bytes("https://mmta.org.my/files/gone.pdf", client)
        )


def test_rewrite_srcs_rewrites_link_href():
    old = "https://mmta.org.my/files/brochure-en.pdf"
    new = "https://cms.example/storage/brochure-en.pdf"
    node = _link_el(old)
    _rewrite_srcs(node, {old: new})
    assert node.content.href == new


def test_upload_media_rehosts_document_links_alongside_images():
    """End-to-end through _upload_media: a scraped document href gets
    fetched, uploaded via the same client.upload_media as images, and comes
    back in the rewrite map — without being counted in the image `failed` set."""
    pdf_href = "https://mmta.org.my/files/brochure-en.pdf"
    page = GeneratedPage(
        slug="", title="Home", is_homepage=True,
        body_schema=BodySchema(elements=[_link_el(pdf_href)]), seo=PageSeo(),
    )
    site = GeneratedSite(
        site_name="S", pages=[page], page_tree=[], builder_styles={},
        header_schema=BuilderElement(name="H", type="__header", content=[]),
        footer_schema=BuilderElement(name="F", type="__footer", content=[]),
    )
    req = PushRequest(
        site=site, cms_email="u@e.com", cms_password="pw", entity_token="tok",
    )

    async def _fake_get(_self, _url, **_kwargs):
        return _FakeResponse(200, b"%PDF-1.4 fake")

    with (
        patch.object(httpx.AsyncClient, "get", new=_fake_get),
        patch.object(
            CmsClient, "upload_media",
            new=AsyncMock(return_value="https://cms.example/storage/brochure-en.pdf"),
        ),
    ):
        client = CmsClient(base_url="http://localhost:8000")
        rewrites, failed = asyncio.run(_upload_media(client, req))

    assert rewrites == {pdf_href: "https://cms.example/storage/brochure-en.pdf"}
    assert failed == set()


# --- brand logo: the dict-shaped brand on the real push path --------------------
#
# GeneratedSite.brand is typed `Any`: a BrandIdentity when the plan is built
# in-process, a plain dict once the frontend posts the site back to
# /api/cms/push — which is every real push. `getattr` on a dict returns the
# default, so the logo block read logo_render_ok as True and both URLs as None,
# and added nothing at all. The header schema walk reaches the same URL, so the
# upload survived; the casualty was the gate the block claims to apply.

from app.services.push_orchestrator import _brand_field  # noqa: E402

_LOGO_URL = "https://acme.test/logo.png"
_LOGO_CDN = "https://cms.example/storage/logo.png"


class _FakeImageResponse:
    """_resolve_to_bytes reads .headers; _resolve_document_to_bytes doesn't."""

    def __init__(self, content: bytes):
        self.status_code = 200
        self.content = content
        self.headers = {"content-type": "image/png"}


def _site_with_brand(brand: object, *, header_logo: str | None = None) -> GeneratedSite:
    home = GeneratedPage(
        slug="", title="Home", is_homepage=True,
        body_schema=BodySchema(elements=[]), seo=PageSeo(),
    )
    site = GeneratedSite(
        site_name="S", pages=[home], page_tree=[], builder_styles={},
        header_schema=BuilderElement(
            name="Header", type="__header",
            content=[_img(header_logo)] if header_logo else [],
        ),
        footer_schema=BuilderElement(name="Footer", type="__footer", content=[]),
    )
    site.brand = brand
    return site


def _upload_with_brand(site: GeneratedSite):
    """Run _upload_media against a site, returning (rewrites, failed, upload mock)."""
    req = PushRequest(
        site=site, cms_email="u@e.com", cms_password="pw", entity_token="tok",
    )
    png = _encode("RGB", "PNG")

    async def _fake_get(_self, _url, **_kwargs):
        return _FakeImageResponse(png)

    upload = AsyncMock(return_value=_LOGO_CDN)
    with (
        patch.object(httpx.AsyncClient, "get", new=_fake_get),
        patch.object(CmsClient, "upload_media", new=upload),
    ):
        client = CmsClient(base_url="http://localhost:8000")
        rewrites, failed = asyncio.run(_upload_media(client, req))
    return rewrites, failed, upload


def test_brand_field_reads_object_and_dict_shapes():
    from app.models.brand import BrandIdentity

    obj = BrandIdentity(name="Acme", logo_url=_LOGO_URL)
    for brand in (obj, obj.model_dump()):
        assert _brand_field(brand, "logo_url") == _LOGO_URL
        assert _brand_field(brand, "logo_render_ok") is True
    # A missing field, and a missing brand, read as None rather than raising.
    assert _brand_field({"name": "Acme"}, "logo_url") is None
    assert _brand_field(None, "logo_render_ok") is None


def test_dict_brand_logo_is_uploaded_when_renderable():
    """The real push path. The mark passed the render gate and the header
    doesn't happen to carry it, so this block is the only thing that re-hosts
    it — which is what it was written to do, and never did for a dict."""
    site = _site_with_brand(
        {"name": "Acme", "logo_url": _LOGO_URL, "logo_render_ok": True}
    )

    rewrites, failed, _ = _upload_with_brand(site)

    assert rewrites == {_LOGO_URL: _LOGO_CDN}
    assert failed == set()


def test_dict_brand_logo_is_skipped_when_render_gate_failed():
    """logo_render_ok=False is an og:image or an icon too small for the header
    lockup: a palette source no renderer draws. Uploading it would park a
    favicon in the tenant's media library."""
    site = _site_with_brand(
        {"name": "Acme", "logo_url": _LOGO_URL, "logo_render_ok": False}
    )

    rewrites, failed, upload = _upload_with_brand(site)

    assert rewrites == {}
    assert failed == set()
    assert upload.await_count == 0


def test_dict_brand_without_render_flag_is_treated_as_renderable():
    """Absent is "not stated", not "not renderable": BrandIdentity defaults the
    field True and the frontend's TS mirror declares it optional. This is what
    `getattr(brand, "logo_render_ok", True)` meant, preserved."""
    site = _site_with_brand({"name": "Acme", "logo_url": _LOGO_URL})

    rewrites, _, _ = _upload_with_brand(site)

    assert rewrites == {_LOGO_URL: _LOGO_CDN}


def test_dict_brand_falls_back_to_logo_data_url():
    png_data_url = "data:image/png;base64," + base64.b64encode(
        _encode("RGB", "PNG")
    ).decode()
    site = _site_with_brand({"name": "Acme", "logo_data_url": png_data_url})

    rewrites, _, _ = _upload_with_brand(site)

    assert rewrites == {png_data_url: _LOGO_CDN}


def test_brand_logo_already_in_the_header_is_uploaded_once():
    """setdefault, not assignment: when the header schema already collected the
    same URL the brand block is a no-op, so the fixed gate adds no second
    upload to the sites where the logo does render."""
    site = _site_with_brand(
        {"name": "Acme", "logo_url": _LOGO_URL, "logo_render_ok": True},
        header_logo=_LOGO_URL,
    )

    rewrites, _, upload = _upload_with_brand(site)

    assert rewrites == {_LOGO_URL: _LOGO_CDN}
    assert upload.await_count == 1


def test_object_brand_logo_upload_is_unchanged():
    """The in-process shape, where getattr already worked."""
    from app.models.brand import BrandIdentity

    site = _site_with_brand(BrandIdentity(name="Acme", logo_url=_LOGO_URL))

    rewrites, _, _ = _upload_with_brand(site)

    assert rewrites == {_LOGO_URL: _LOGO_CDN}


def test_object_brand_logo_skipped_when_render_gate_failed():
    from app.models.brand import BrandIdentity

    site = _site_with_brand(
        BrandIdentity(name="Acme", logo_url=_LOGO_URL, logo_render_ok=False)
    )

    rewrites, _, upload = _upload_with_brand(site)

    assert rewrites == {}
    assert upload.await_count == 0
# --- create_entity: the clash that only a shared CMS can produce ---------------


def _create_entity_req() -> PushRequest:
    return PushRequest(
        site=_minimal_site(),
        cms_email="user@example.com",
        cms_password="secret",
        entity_token="",
        create_entity=True,
        new_entity_name="Acme",
        new_entity_url="https://acme.example",
        push_builder_styles=False,
    )


def _push_with_create_entity(create_entity: AsyncMock) -> object:
    with (
        patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")),
        patch.object(CmsClient, "create_entity", new=create_entity),
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
            CmsClient, "save_page_layout", new=AsyncMock(return_value={"versionId": "V1"})
        ),
        patch.object(
            CmsClient, "save_page_draft", new=AsyncMock(return_value={"draftVersion": 2})
        ),
    ):
        return asyncio.run(push_site(_create_entity_req()))


def _step(report, name: str):
    return next(s for s in report.steps if s.name == name)


def test_duplicate_entity_url_retries_without_it_and_warns():
    """entity_url is unique across a whole CMS, so on a shared production one the
    site's own address may already belong to another tenant. The URL is the one
    optional part of the request — dropping it lands the site instead of failing
    the push over a field the operator can set later."""
    create_entity = AsyncMock(
        side_effect=[
            CmsApiError(
                422,
                "Create entity failed",
                response_body={"errors": {"entity_url": ["already exists"]}},
            ),
            {"entity_api_token": "tok", "entity_id": 7},
        ]
    )
    report = _push_with_create_entity(create_entity)

    assert report.success, report.error
    step = _step(report, "create_entity")
    assert step.ok
    assert step.warning and "https://acme.example" in step.warning
    # Retried once, with the URL dropped and nothing else changed.
    assert create_entity.await_count == 2
    assert create_entity.await_args_list[0].kwargs["entity_url"] == "https://acme.example"
    assert create_entity.await_args_list[1].kwargs["entity_url"] is None
    assert create_entity.await_args_list[1].kwargs["entity_name"] == "Acme"


def test_a_clean_create_entity_carries_no_warning():
    create_entity = AsyncMock(return_value={"entity_api_token": "tok", "entity_id": 7})
    report = _push_with_create_entity(create_entity)

    assert report.success, report.error
    assert _step(report, "create_entity").warning is None
    assert create_entity.await_count == 1


def test_other_create_entity_failures_still_abort_the_push():
    """Only the entity_url clash is recoverable — detected on the errors KEY, never
    on Laravel's (translatable) message text."""
    create_entity = AsyncMock(
        side_effect=CmsApiError(
            422, "Create entity failed", response_body={"errors": {"entity_name": ["required"]}}
        )
    )
    report = _push_with_create_entity(create_entity)

    assert not report.success
    assert not _step(report, "create_entity").ok
    assert create_entity.await_count == 1
