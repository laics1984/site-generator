"""CMS content-type push: article/event entry creation + template pages.

Client-level tests drive real httpx requests into a MockTransport so the
multipart payload shape (form fields + previewImage file) is asserted as the
CMS will actually receive it. Orchestrator-level tests patch CmsClient methods
(same pattern as test_push_orchestrator.py).
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.services.cms_client import CmsClient
from app.services.content_collections import (
    ArticleEntry,
    ContentCollections,
    EventEntry,
)
from app.services.push_orchestrator import (
    PushReport,
    PushRequest,
    _push_content_types,
    _site_list_sources,
)

# 1x1 red PNG
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c626001000000ffff030000060005"
    "57bfabd40000000049454e44ae426082"
)


def _client_with_transport(handler) -> CmsClient:
    client = CmsClient(base_url="http://cms.test")
    client.jwt = "test-jwt"
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return client


class CmsClientContentTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_article_sends_multipart_with_preview_image(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url"] = str(request.url)
            captured["body"] = request.read()
            captured["content_type"] = request.headers.get("content-type", "")
            return httpx.Response(200, json={"success": True, "aid": "a-1"})

        client = _client_with_transport(handler)
        aid = await client.create_article(
            "entity-tok",
            title="Grand Opening",
            slug="grand-opening",
            excerpt="We opened.",
            body_html="<p>Body</p>",
            category_slugs=["news"],
            published_at_ms=1750000000000,
            image=("cover.png", _PNG, "image/png"),
            publish=True,
        )
        await client.aclose()

        self.assertEqual(aid, "a-1")
        self.assertIn("/api/articles/create", captured["url"])
        self.assertIn("multipart/form-data", captured["content_type"])
        body = captured["body"]
        self.assertIn(b'name="title"', body)
        self.assertIn(b'name="post_type"', body)
        self.assertIn(b"published", body)
        self.assertIn(b'name="category"', body)
        self.assertIn(b'["news"]', body)
        # tag must always be present — the controller count()s it.
        self.assertIn(b'name="tag"', body)
        self.assertIn(b'name="published_at"', body)
        self.assertIn(b"1750000000000", body)
        self.assertIn(b'name="previewImage"; filename="cover.png"', body)

    async def test_create_event_sends_ms_timestamps(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["body"] = request.read()
            captured["url"] = str(request.url)
            return httpx.Response(200, json={"success": True, "eid": "e-1"})

        client = _client_with_transport(handler)
        start = datetime(2026, 9, 12, 19, 0, tzinfo=timezone.utc)
        end = datetime(2026, 9, 12, 23, 0, tzinfo=timezone.utc)
        eid = await client.create_event(
            "entity-tok",
            title="Charity Dinner",
            slug="charity-dinner",
            excerpt="Fundraiser.",
            body_html="<p>Join us</p>",
            location="Grand Ballroom",
            start_ms=int(start.timestamp() * 1000),
            end_ms=int(end.timestamp() * 1000),
            image=("cover.png", _PNG, "image/png"),
            publish=True,
        )
        await client.aclose()

        self.assertEqual(eid, "e-1")
        self.assertIn("/api/events/create", captured["url"])
        body = captured["body"]
        self.assertIn(b'name="event_name"', body)
        self.assertIn(str(int(start.timestamp() * 1000)).encode(), body)
        self.assertIn(str(int(end.timestamp() * 1000)).encode(), body)
        self.assertIn(b'name="location"', body)
        self.assertIn(b'name="event_type"', body)

    async def test_create_article_body_level_failure_raises(self):
        def handler(request: httpx.Request) -> httpx.Response:
            # CMS signals validation failure with HTTP 200 + success:false.
            return httpx.Response(
                200,
                json={"success": False, "message": {"slug": ["taken"]}},
            )

        client = _client_with_transport(handler)
        from app.services.cms_client import CmsApiError

        with self.assertRaises(CmsApiError):
            await client.create_article(
                "entity-tok",
                title="X",
                slug="x",
                excerpt="x",
                body_html="<p>x</p>",
                category_slugs=["news"],
                publish=False,
            )
        await client.aclose()

    async def test_create_category_duplicate_returns_derived_slug(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "success": False,
                    "message": {"title": 'Category "News" already existed'},
                },
            )

        client = _client_with_transport(handler)
        slug = await client.create_category("entity-tok", title="News")
        await client.aclose()
        self.assertEqual(slug, "news")

    async def test_create_page_passes_template_for(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["json"] = request.read()
            return httpx.Response(200, json={"data": {"id": "p-1"}})

        client = _client_with_transport(handler)
        await client.create_page(
            "entity-tok",
            title="Article Template",
            slug="article-template",
            template_for="article",
        )
        await client.aclose()
        self.assertIn(b'"templateFor": "article"', captured["json"])


def _site_with_lists() -> GeneratedSite:
    def page(slug, title, element_type=None, homepage=False):
        elements = []
        if element_type:
            elements.append(
                BuilderElement(
                    name="List",
                    type=element_type,
                    content=BuilderElementContent(source="articles"),
                )
            )
        return GeneratedPage(
            slug=slug,
            title=title,
            is_homepage=homepage,
            body_schema=BodySchema(elements=elements),
            seo=PageSeo(),
        )

    return GeneratedSite(
        site_name="Test Site",
        pages=[
            page("", "Home", homepage=True),
            page("blog", "Blog", "articlesList"),
            page("events", "Events", "eventsList"),
        ],
        page_tree=[],
    )


class ContentTypesPushTest(unittest.IsolatedAsyncioTestCase):
    def _request(self, collections=None) -> PushRequest:
        return PushRequest(
            site=_site_with_lists(),
            cms_email="user@example.com",
            cms_password="secret",
            entity_token="entity-tok",
            collections=collections,
        )

    def test_site_list_sources_detects_both(self):
        self.assertEqual(
            _site_list_sources(_site_with_lists()), {"articles", "events"}
        )

    async def test_template_pages_created_for_detected_lists(self):
        create_page = AsyncMock(return_value={"id": "p"})
        with (
            patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
            patch.object(CmsClient, "create_page", new=create_page),
        ):
            client = CmsClient(base_url="http://cms.test")
            report = PushReport()
            await _push_content_types(client, self._request(), report)

        kinds = {call.kwargs["template_for"] for call in create_page.call_args_list}
        self.assertEqual(kinds, {"article", "articleListing", "event"})
        step = next(s for s in report.steps if s.name == "template_pages")
        self.assertTrue(step.ok)

    async def test_existing_templates_are_not_recreated(self):
        create_page = AsyncMock(return_value={"id": "p"})
        existing = [
            {"id": "1", "templateFor": "article"},
            {"id": "2", "templateFor": "articleListing"},
            {"id": "3", "templateFor": "event"},
        ]
        with (
            patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=existing)),
            patch.object(CmsClient, "create_page", new=create_page),
        ):
            client = CmsClient(base_url="http://cms.test")
            await _push_content_types(client, self._request(), PushReport())
        create_page.assert_not_called()

    async def test_entries_pushed_published_and_draft(self):
        collections = ContentCollections(
            articles=[
                ArticleEntry(
                    title="Post",
                    slug="post",
                    excerpt="x",
                    body_html="<p>x</p>",
                    published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    image_url="https://src.example/cover.png",
                    source_url="https://src.example/blog/post",
                )
            ],
            events=[
                EventEntry(
                    title="Dated Event",
                    slug="dated-event",
                    excerpt="x",
                    body_html="<p>x</p>",
                    start=datetime(2026, 9, 12, 19, 0, tzinfo=timezone.utc),
                    image_url="https://src.example/cover.png",
                    source_url="https://src.example/events/dated",
                ),
                EventEntry(
                    title="Undated Event",
                    slug="undated-event",
                    excerpt="x",
                    body_html="<p>x</p>",
                    source_url="https://src.example/events/undated",
                ),
            ],
        )
        create_article = AsyncMock(return_value="a-1")
        create_event = AsyncMock(return_value="e-1")

        async def fake_resolve(src, client=None):
            return (_PNG, "image/png", "cover.png")

        with (
            patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
            patch.object(CmsClient, "create_page", new=AsyncMock(return_value={"id": "p"})),
            patch.object(CmsClient, "create_category", new=AsyncMock(return_value="news")),
            patch.object(CmsClient, "create_article", new=create_article),
            patch.object(CmsClient, "create_event", new=create_event),
            patch(
                "app.services.push_orchestrator._resolve_to_bytes",
                new=fake_resolve,
            ),
        ):
            client = CmsClient(base_url="http://cms.test")
            report = PushReport()
            await _push_content_types(client, self._request(collections), report)

        # Article: published with category + image.
        art_kwargs = create_article.call_args.kwargs
        self.assertTrue(art_kwargs["publish"])
        self.assertEqual(art_kwargs["category_slugs"], ["news"])
        self.assertIsNotNone(art_kwargs["image"])
        self.assertEqual(
            art_kwargs["published_at_ms"],
            int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000),
        )

        # Entries are pushed concurrently, so locate event calls by slug —
        # call order is not a contract.
        event_calls = {c.kwargs["slug"]: c.kwargs for c in create_event.call_args_list}
        # Dated event published with a defaulted end + location fallback.
        dated = event_calls["dated-event"]
        self.assertTrue(dated["publish"])
        self.assertEqual(dated["location"], "To be announced")
        self.assertEqual(
            dated["end_ms"] - dated["start_ms"], 2 * 60 * 60 * 1000
        )
        # Undated event downgrades to draft.
        undated = event_calls["undated-event"]
        self.assertFalse(undated["publish"])

        step = next(s for s in report.steps if s.name == "content_entries")
        self.assertTrue(step.ok)
        self.assertIn("2 published", step.detail)
        self.assertIn("1 draft", step.detail)

    async def test_entries_push_concurrently_under_the_bound(self):
        # Migration entries used to upload one-at-a-time; they now fan out
        # under _PUSH_CONCURRENCY like every other push step.
        import asyncio

        from app.services import push_orchestrator as po

        n = 8
        collections = ContentCollections(
            articles=[
                ArticleEntry(
                    title=f"Post {i}",
                    slug=f"post-{i}",
                    excerpt="x",
                    body_html="<p>x</p>",
                    published_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    image_url=f"https://src.example/cover-{i}.png",
                    source_url=f"https://src.example/blog/post-{i}",
                )
                for i in range(n)
            ]
        )
        in_flight = {"now": 0, "max": 0}

        async def tracking_resolve(src, client=None):
            in_flight["now"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["now"])
            try:
                await asyncio.sleep(0.01)
                return (_PNG, "image/png", "cover.png")
            finally:
                in_flight["now"] -= 1

        with (
            patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
            patch.object(CmsClient, "create_page", new=AsyncMock(return_value={"id": "p"})),
            patch.object(CmsClient, "create_category", new=AsyncMock(return_value="news")),
            patch.object(CmsClient, "create_article", new=AsyncMock(return_value="a-1")),
            patch(
                "app.services.push_orchestrator._resolve_to_bytes",
                new=tracking_resolve,
            ),
        ):
            client = CmsClient(base_url="http://cms.test")
            report = PushReport()
            await _push_content_types(client, self._request(collections), report)

        self.assertGreater(in_flight["max"], 1)
        self.assertLessEqual(in_flight["max"], po._PUSH_CONCURRENCY)
        step = next(s for s in report.steps if s.name == "content_entries")
        self.assertTrue(step.ok)
        self.assertIn(f"{n} published", step.detail)

    async def test_entry_failure_is_non_fatal(self):
        from app.services.cms_client import CmsApiError

        collections = ContentCollections(
            articles=[
                ArticleEntry(
                    title="Bad",
                    slug="bad",
                    excerpt="x",
                    body_html="<p>x</p>",
                    image_url="https://src.example/cover.png",
                    source_url="https://src.example/blog/bad",
                ),
                ArticleEntry(
                    title="Good",
                    slug="good",
                    excerpt="x",
                    body_html="<p>x</p>",
                    image_url="https://src.example/cover.png",
                    source_url="https://src.example/blog/good",
                ),
            ]
        )

        calls = []

        async def flaky_create_article(self, entity_token, **kwargs):
            calls.append(kwargs["slug"])
            if kwargs["slug"] == "bad":
                raise CmsApiError(500, "boom")
            return "a-2"

        async def fake_resolve(src, client=None):
            return (_PNG, "image/png", "cover.png")

        with (
            patch.object(CmsClient, "list_pages", new=AsyncMock(return_value=[])),
            patch.object(CmsClient, "create_page", new=AsyncMock(return_value={"id": "p"})),
            patch.object(CmsClient, "create_category", new=AsyncMock(return_value="news")),
            patch.object(CmsClient, "create_article", new=flaky_create_article),
            patch(
                "app.services.push_orchestrator._resolve_to_bytes",
                new=fake_resolve,
            ),
        ):
            client = CmsClient(base_url="http://cms.test")
            report = PushReport()
            await _push_content_types(client, self._request(collections), report)

        self.assertEqual(calls, ["bad", "good"])
        step = next(s for s in report.steps if s.name == "content_entries")
        self.assertIn("1 failed", step.detail)
        self.assertIn("1 published", step.detail)


if __name__ == "__main__":
    unittest.main()
