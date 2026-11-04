"""The CmsClient calls a sync makes, as the CMS will actually receive them.

Real httpx requests into a MockTransport (the test_content_push.py idiom), so
the query strings, verbs and JSON bodies are asserted on the wire rather than
on a mock's kwargs.
"""

from __future__ import annotations

import json
import unittest

import httpx

from app.services.cms_client import CmsApiError, CmsClient


def _client(handler) -> CmsClient:
    client = CmsClient(base_url="http://cms.test")
    client.jwt = "test-jwt"
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


class ListPagesTest(unittest.IsolatedAsyncioTestCase):
    async def test_walks_every_page_of_the_listing(self):
        """The CMS pages at 100 at most; a 250-page site is three requests."""
        seen: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            params = dict(request.url.params)
            seen.append(params)
            page = int(params["page"])
            size = int(params["perPage"])
            start = (page - 1) * size
            rows = [{"id": str(i), "slug": f"p{i}"} for i in range(start, min(start + size, 250))]
            return httpx.Response(
                200, json={"data": rows, "meta": {"page": page, "perPage": size, "total": 250}}
            )

        client = _client(handler)
        rows = await client.list_pages("tok", status="all")
        await client.aclose()

        self.assertEqual(len(rows), 250)
        self.assertEqual([p["page"] for p in seen], ["1", "2", "3"])
        self.assertTrue(all(p["perPage"] == "100" and p["status"] == "all" for p in seen))

    async def test_no_status_means_the_cms_default(self):
        seen: list[dict[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(dict(request.url.params))
            return httpx.Response(200, json={"data": [], "meta": {"total": 0}})

        client = _client(handler)
        rows = await client.list_pages("tok")
        await client.aclose()

        self.assertEqual(rows, [])
        self.assertNotIn("status", seen[0])

    async def test_a_listing_without_meta_is_one_page(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": [{"id": "1"}]})

        client = _client(handler)
        rows = await client.list_pages("tok")
        await client.aclose()

        self.assertEqual(rows, [{"id": "1"}])


class PageWritesTest(unittest.IsolatedAsyncioTestCase):
    async def test_update_page_patches_metadata_against_the_draft_version(self):
        captured: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["method"] = request.method
            captured["path"] = request.url.path
            captured["json"] = json.loads(request.read())
            return httpx.Response(200, json={"data": {"id": "p1", "draftVersion": 8}})

        client = _client(handler)
        result = await client.update_page(
            "tok",
            "p1",
            base_draft_version=7,
            title="About",
            description=None,
            seo={"title": "About Acme", "noindex": False},
        )
        await client.aclose()

        self.assertEqual(captured["method"], "PATCH")
        self.assertEqual(captured["path"], "/api/entities/tok/pages/p1")
        self.assertEqual(
            captured["json"],
            {
                "baseDraftVersion": 7,
                "title": "About",
                "description": None,
                "seo": {"title": "About Acme", "noindex": False},
            },
        )
        self.assertEqual(result["draftVersion"], 8)

    async def test_update_page_surfaces_the_cms_error_shape(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                409,
                json={"error": {"code": "DRAFT_VERSION_CONFLICT", "message": "The page draft has changed."}},
            )

        client = _client(handler)
        with self.assertRaises(CmsApiError) as ctx:
            await client.update_page(
                "tok", "p1", base_draft_version=1, title="x", description=None, seo={}
            )
        await client.aclose()
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("The page draft has changed", str(ctx.exception))

    async def test_restore_and_archive_use_the_page_routes(self):
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path))
            if request.method == "DELETE":
                return httpx.Response(204)  # no body, as the CMS answers
            return httpx.Response(200, json={"data": {"id": "p1", "status": "published"}})

        client = _client(handler)
        restored = await client.restore_page("tok", "p1")
        await client.archive_page("tok", "p2")
        await client.aclose()

        self.assertEqual(restored["status"], "published")
        self.assertEqual(
            seen,
            [
                ("POST", "/api/entities/tok/pages/p1/restore"),
                ("DELETE", "/api/entities/tok/pages/p2"),
            ],
        )

    async def test_get_page_reads_the_draft_version(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"data": {"id": "p1", "draftVersion": 3}})

        client = _client(handler)
        page = await client.get_page("tok", "p1")
        await client.aclose()

        self.assertEqual(page["draftVersion"], 3)


class ListEntitiesTest(unittest.IsolatedAsyncioTestCase):
    async def test_returns_the_rows(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/api/entities")
            self.assertEqual(request.headers["authorization"], "Bearer test-jwt")
            return httpx.Response(200, json={"data": [{"entity_api_token": "t", "entity_name": "Acme"}]})

        client = _client(handler)
        rows = await client.list_entities()
        await client.aclose()

        self.assertEqual(rows, [{"entity_api_token": "t", "entity_name": "Acme"}])

    async def test_a_missing_route_is_a_cms_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, json={"message": "Not Found"})

        client = _client(handler)
        with self.assertRaises(CmsApiError) as ctx:
            await client.list_entities()
        await client.aclose()
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()


class SaveTranslationsTest(unittest.IsolatedAsyncioTestCase):
    """A push's translations go in one request; saving and publishing are two
    routes because the CMS gates them on different roles."""

    ITEMS = [{"type": "page", "id": "p1", "locale": "zh-Hans", "overlay": {"elements": {}}, "baseDraftVersion": None}]

    async def test_drafts_and_publishing_go_to_their_own_routes_in_one_request(self):
        seen: list[tuple[str, str, dict]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, request.url.path, json.loads(request.content)))
            return httpx.Response(200, json={"data": {"items": [{"locale": "zh-Hans", "published": False}]}})

        client = _client(handler)
        drafts = await client.save_translations("tok", self.ITEMS, publish=False)
        await client.save_translations("tok", self.ITEMS, publish=True, allow_incomplete=True)
        await client.aclose()

        self.assertEqual(seen, [
            ("PUT", "/api/entities/tok/translations", {"items": self.ITEMS}),
            ("POST", "/api/entities/tok/translations/publish", {"items": self.ITEMS, "allowIncomplete": True}),
        ])
        self.assertEqual(drafts, [{"locale": "zh-Hans", "published": False}])

    async def test_the_summary_is_asked_for_the_pushed_pages_only(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {"pages": [], "layout": None}})

        client = _client(handler)
        await client.get_translation_summary("tok", ["p1", "p2"])
        await client.aclose()

        self.assertEqual(seen[0].url.path, "/api/entities/tok/translations")
        self.assertEqual(dict(seen[0].url.params), {"pageIds": "p1,p2"})

    async def test_a_refusal_carries_the_cms_message_and_the_item(self):
        refusal = {"error": {"code": "DRAFT_VERSION_CONFLICT", "message": "This translation has changed since it was loaded.",
                             "details": {"draftVersion": 2, "item": {"type": "page", "id": "p1", "locale": "zh-Hans"}}}}

        client = _client(lambda request: httpx.Response(409, json=refusal))
        with self.assertRaises(CmsApiError) as ctx:
            await client.save_translations("tok", self.ITEMS, publish=False)
        await client.aclose()

        self.assertEqual(str(ctx.exception), "Saving the translations failed: This translation has changed since it was loaded.")
        self.assertEqual(ctx.exception.response_body["error"]["details"]["item"]["locale"], "zh-Hans")


class BuilderStylesTest(unittest.IsolatedAsyncioTestCase):
    """The theme goes to the CMS on the admin credential, never a session cookie.

    It used to ride a builder session the client minted for itself, and the
    CMS refuses a cookie write that carries no browser Origin: every push to
    production failed CROSS_SITE_REQUEST at the theme step.
    """

    async def test_the_theme_is_written_with_the_bearer_token_and_no_cookie(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, json={"data": {"layout": {"versionId": "v2"}}})

        client = _client(handler)
        saved = await client.update_builder_styles(
            "tok", {"colors": {"primary": "#123456"}}, expected_layout_version_id="v1"
        )
        await client.aclose()

        (request,) = seen
        self.assertEqual((request.method, request.url.path), ("PUT", "/api/entities/tok/builder-styles"))
        self.assertEqual(request.headers["authorization"], "Bearer test-jwt")
        self.assertNotIn("cookie", request.headers)
        self.assertEqual(
            json.loads(request.content),
            {"builder_styles": {"colors": {"primary": "#123456"}}, "expectedLayoutVersionId": "v1"},
        )
        self.assertEqual(saved, {"layout": {"versionId": "v2"}})

    async def test_reading_and_a_stale_version_name_the_site(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(200, json={"data": {"builder_styles": {}, "layout": {"versionId": "v1"}}})
            return httpx.Response(409, json={"message": "The live design changed.", "code": "LAYOUT_VERSION_CONFLICT"})

        client = _client(handler)
        live = await client.get_builder_styles("tok")
        with self.assertRaises(CmsApiError) as ctx:
            await client.update_builder_styles("tok", {}, expected_layout_version_id="v0")
        await client.aclose()

        self.assertEqual(live["layout"]["versionId"], "v1")
        self.assertEqual(ctx.exception.status, 409)
        self.assertIn("The live design changed.", str(ctx.exception))
