"""
Media is deduplicated by content hash before it is uploaded.

Every push re-sends a site's photography — the generated tree never carries a
CMS URL — and the CMS names each upload with a fresh timestamp, so an update
used to file a second copy of every photo, and there is no route to delete
one. `_store_bytes` asks `GET /api/file/lookup` with the sha256 of the bytes
it is about to send and uploads only on a miss.

Pinned here: the hash is of the bytes that go on the wire, a hit skips the
upload and is counted as reused, a lookup failure still uploads, and an API
without the route is probed once, not once per image.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import unittest
from io import BytesIO
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.services.cms_client import CmsApiError, CmsClient
from app.services.push_orchestrator import (
    MediaUpload,
    PushRequest,
    _describe_media,
    _store_bytes,
    _upload_media,
    push_site,
)

HIT = "https://cdn.example/storage/1788541037photo.png"
_SHA = "a" * 64


def _client(handler) -> CmsClient:
    client = CmsClient(base_url="http://cms.test")
    client.jwt = "test-jwt"
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


class LookupMediaClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_hit_is_the_stored_url(self):
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["path"] = request.url.path
            seen["params"] = dict(request.url.params)
            return httpx.Response(200, json={"t": "p", "i": HIT})

        client = _client(handler)
        found = await client.lookup_media("tok", _SHA)
        await client.aclose()

        self.assertEqual(found, HIT)
        self.assertEqual(seen["path"], "/api/file/lookup")
        self.assertEqual(seen["params"], {"e": "tok", "hash": _SHA})

    async def test_a_miss_is_none(self):
        client = _client(lambda request: httpx.Response(204))
        self.assertIsNone(await client.lookup_media("tok", _SHA))
        await client.aclose()

    async def test_an_api_without_the_route_is_probed_once(self):
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404, json={"message": "The route api/file/lookup could not be found."})

        client = _client(handler)
        self.assertIsNone(await client.lookup_media("tok", _SHA))
        self.assertIsNone(await client.lookup_media("tok", "b" * 64))
        await client.aclose()

        self.assertEqual(calls["n"], 1)

    async def test_the_apis_own_404_is_a_refusal_not_a_missing_route(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404, json={"error": {"code": "ENTITY_NOT_FOUND", "message": "Entity not found."}}
            )

        client = _client(handler)
        with self.assertRaises(CmsApiError) as ctx:
            await client.lookup_media("tok", _SHA)
        await client.aclose()

        self.assertEqual(ctx.exception.status, 404)
        self.assertTrue(client._media_lookup_supported)

    async def test_a_server_error_is_raised(self):
        client = _client(lambda request: httpx.Response(500, json={"message": "boom"}))
        with self.assertRaises(CmsApiError):
            await client.lookup_media("tok", _SHA)
        await client.aclose()


class StoreBytesTest(unittest.IsolatedAsyncioTestCase):
    def _client(self, *, found: str | None = None) -> AsyncMock:
        client = AsyncMock()
        client.lookup_media.return_value = found
        client.upload_media.return_value = "https://cdn.example/storage/new.png"
        return client

    async def test_a_hit_skips_the_upload(self):
        client = self._client(found=HIT)

        stored = await _store_bytes(
            client, "tok", file_bytes=b"png-bytes", filename="a.png", content_type="image/png"
        )

        self.assertEqual(stored, (HIT, True))
        client.upload_media.assert_not_awaited()
        # The hash the CMS is asked about is of exactly the bytes it would receive.
        client.lookup_media.assert_awaited_once_with("tok", hashlib.sha256(b"png-bytes").hexdigest())

    async def test_a_miss_uploads(self):
        client = self._client(found=None)

        stored = await _store_bytes(
            client, "tok", file_bytes=b"png-bytes", filename="a.png", content_type="image/png"
        )

        self.assertEqual(stored, ("https://cdn.example/storage/new.png", False))
        client.upload_media.assert_awaited_once()

    async def test_a_failed_lookup_still_uploads(self):
        client = self._client()
        client.lookup_media.side_effect = CmsApiError(503, "lookup down")

        stored = await _store_bytes(
            client, "tok", file_bytes=b"png-bytes", filename="a.png", content_type="image/png"
        )

        self.assertEqual(stored, ("https://cdn.example/storage/new.png", False))

    async def test_a_refused_upload_is_none(self):
        client = self._client()
        client.upload_media.side_effect = CmsApiError(422, "not an image")

        self.assertIsNone(
            await _store_bytes(
                client, "tok", file_bytes=b"x", filename="a.png", content_type="image/png"
            )
        )


def _png(color: tuple[int, int, int]) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), color).save(buf, format="PNG")
    return buf.getvalue()


def _data_url(png: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _site(*srcs: str) -> GeneratedSite:
    return GeneratedSite(
        site_name="Acme",
        pages=[
            GeneratedPage(
                slug="",
                title="Home",
                is_homepage=True,
                body_schema=BodySchema(
                    elements=[
                        BuilderElement(
                            name=f"Img {i}",
                            type="image",
                            content=BuilderElementContent(src=src, alt=""),
                        )
                        for i, src in enumerate(srcs)
                    ]
                ),
                seo=PageSeo(),
            )
        ],
        page_tree=[],
        header_schema=BuilderElement(name="Header", type="__header", content=[]),
        footer_schema=BuilderElement(name="Footer", type="__footer", content=[]),
    )


class UploadMediaDedupTest(unittest.TestCase):
    def test_only_the_bytes_the_library_lacks_are_uploaded(self):
        held, missing = _png((10, 20, 30)), _png((200, 100, 0))
        held_src, missing_src = _data_url(held), _data_url(missing)
        # A PNG this small passes through coercion untouched, so the hash the
        # CMS is asked about is the hash of the decoded data URL.
        by_hash = {hashlib.sha256(held).hexdigest(): HIT}

        async def lookup(_self, token, digest):
            return by_hash.get(digest)

        upload = AsyncMock(return_value="https://cdn.example/storage/new.png")
        req = PushRequest(
            site=_site(held_src, missing_src), cms_email="u@e.com", cms_password="s", entity_token="tok"
        )
        with (
            patch.object(CmsClient, "lookup_media", new=lookup),
            patch.object(CmsClient, "upload_media", new=upload),
        ):
            media = asyncio.run(_upload_media(CmsClient(base_url="http://cms.test"), req))

        self.assertEqual(
            media.rewrites, {held_src: HIT, missing_src: "https://cdn.example/storage/new.png"}
        )
        self.assertEqual(media.failed, set())
        self.assertEqual((media.reused, media.uploaded), (1, 1))
        upload.assert_awaited_once()
        self.assertEqual(upload.call_args.kwargs["file_bytes"], missing)

    def test_the_report_says_what_was_reused(self):
        self.assertEqual(
            _describe_media(MediaUpload({"a": "u", "b": "v"}, {"c"}, reused=1)),
            "1 uploaded, 1 already in the library, 1 skipped",
        )
        self.assertEqual(_describe_media(MediaUpload({}, set())), "0 uploaded")
        self.assertEqual(
            MediaUpload({"a": "u", "b": "v"}, {"c"}, reused=2).counts(),
            {"uploaded": 0, "reused": 2, "failed": 1},
        )

    def test_a_push_reports_the_reuse(self):
        png = _png((1, 2, 3))
        req = PushRequest(
            site=_site(_data_url(png)), cms_email="u@e.com", cms_password="s", entity_token="tok"
        )
        with (
            patch.object(CmsClient, "login", new=AsyncMock(return_value="jwt")),
            patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
            patch.object(CmsClient, "lookup_media", new=AsyncMock(return_value=HIT)),
            patch.object(CmsClient, "upload_media", new=AsyncMock()) as upload,
            patch.object(
                CmsClient, "create_page", new=AsyncMock(return_value={"id": "p1", "draftVersion": 1})
            ),
            patch.object(
                CmsClient, "get_builder_payload", new=AsyncMock(return_value={"layout": {"versionId": "V0"}})
            ),
            patch.object(CmsClient, "save_page_layout", new=AsyncMock(return_value={"versionId": "V1"})),
            patch.object(CmsClient, "save_page_draft", new=AsyncMock(return_value={"draftVersion": 2})),
        ):
            report = asyncio.run(push_site(req))

        self.assertTrue(report.success, report.error)
        upload.assert_not_awaited()
        step = next(s for s in report.steps if s.name == "media")
        self.assertEqual(step.detail, "0 uploaded, 1 already in the library")
        self.assertEqual(step.data, {"uploaded": 0, "reused": 1, "failed": 0})
        # The page's image now points at the file the library already had.
        image = req.site.pages[0].body_schema.elements[0]
        self.assertEqual(image.content.src, HIT)


if __name__ == "__main__":
    unittest.main()


class TotalMediaFailureIsLoudTest(unittest.TestCase):
    """A src that cannot be re-hosted is stripped, so pages that lost every
    photo look intentional. The one time it is not is when the CMS accepted
    nothing — which is what a pending `media_hash` migration did to a whole
    push."""

    def test_no_image_landing_is_a_warning(self):
        from app.services.push_orchestrator import _media_warning

        warning = _media_warning(MediaUpload({}, {"a", "b"}))

        self.assertIsNotNone(warning)
        self.assertIn("none of the 2", warning)

    def test_a_partial_failure_is_not(self):
        from app.services.push_orchestrator import _media_warning

        self.assertIsNone(_media_warning(MediaUpload({"a": "u"}, {"b"})))

    def test_reuse_alone_counts_as_landing(self):
        from app.services.push_orchestrator import _media_warning

        self.assertIsNone(_media_warning(MediaUpload({"a": "u"}, {"b"}, reused=1)))

    def test_a_clean_push_is_silent(self):
        from app.services.push_orchestrator import _media_warning

        self.assertIsNone(_media_warning(MediaUpload({"a": "u"}, set())))
