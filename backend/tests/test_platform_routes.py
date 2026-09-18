"""
The platform's own routes are never a site's pages.

A CMS-hosted site — the update flow's normal input — serves its article/event
template pages at their slugs and lists them in its sitemap. webtree.my's
crawl found `/article-template`, page inference typed it `services`, and the
model wrote a "No content available at this address yet" page for it; pushed
back, that page collides with the real template's slug. The template contract
has one home (services/platform_routes.py), read by the push that creates
templates and by the crawler that refuses to read them back.
"""

from __future__ import annotations

import unittest
import pathlib
from unittest.mock import AsyncMock, patch

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.services import push_orchestrator
from app.services.cms_client import CmsApiError, CmsClient
from app.services.cms_sync import existing_pages
from app.services.platform_routes import (
    BLANK_TEMPLATE_BODY,
    TEMPLATE_PAGE_DEFAULTS,
    TEMPLATE_PAGE_SLUGS,
    is_template_page_path,
)
from app.services.push_orchestrator import (
    PushReport,
    PushRequest,
    _ensure_template_pages,
)
from app.services.scraper import _is_crawlable_link

ENTRY = "https://webtree.my/"


class TemplatePagePathTest(unittest.TestCase):
    def test_every_template_slug_is_recognised_however_spelled(self):
        for slug in TEMPLATE_PAGE_SLUGS:
            for path in (f"/{slug}", f"/{slug}/", f"/{slug.upper()}"):
                with self.subTest(path=path):
                    self.assertTrue(is_template_page_path(path))

    def test_a_content_page_is_not(self):
        for path in ("/about", "/articles", "/services/web-design", "/"):
            with self.subTest(path=path):
                self.assertFalse(is_template_page_path(path))

    def test_the_push_creates_templates_from_the_same_table(self):
        """One table: the slug the push writes is the slug the crawler skips."""
        self.assertIs(push_orchestrator.TEMPLATE_PAGE_DEFAULTS, TEMPLATE_PAGE_DEFAULTS)
        self.assertEqual(
            set(TEMPLATE_PAGE_DEFAULTS), {"article", "event", "articleListing", "eventListing"}
        )


class CrawlRefusesPlatformRoutesTest(unittest.TestCase):
    def test_template_pages_are_not_crawled(self):
        for slug in TEMPLATE_PAGE_SLUGS:
            with self.subTest(slug=slug):
                self.assertFalse(_is_crawlable_link(f"{ENTRY}{slug}", ENTRY))

    def test_category_archives_are_not_crawled(self):
        # The CMS's /articles/category/<x> and WordPress's /category/<x> are
        # views over content that already has its own pages.
        self.assertFalse(
            _is_crawlable_link(f"{ENTRY}articles/category/website-development", ENTRY)
        )
        self.assertFalse(_is_crawlable_link(f"{ENTRY}category/news/", ENTRY))

    def test_the_sites_own_pages_still_are(self):
        for path in ("about", "articles", "how-we-work", "services/web-design"):
            with self.subTest(path=path):
                self.assertTrue(_is_crawlable_link(f"{ENTRY}{path}", ENTRY))


class TemplateSlugCollisionTest(unittest.IsolatedAsyncioTestCase):
    """A slug is owned by any page holding it, archived ones included, so a
    stale content page at `article-template` — read off the site's own live
    template route before the crawler skipped it — blocked the real template
    forever. Production only: local had no such history.

    The page IS that template, so it is adopted rather than left beside a
    suffixed twin."""

    def _request(self):
        site = GeneratedSite(
            site_name="WebTree", page_tree=[],
            pages=[GeneratedPage(
                slug="", title="Home", is_homepage=True, seo=PageSeo(),
                body_schema=BodySchema(elements=[BuilderElement(
                    name="List", type="articlesList",
                    content=BuilderElementContent(source="articles"))]),
            )],
        )
        return PushRequest(site=site, cms_email="u", cms_password="p", entity_token="tok")

    async def _run(self, rows):
        created: list[dict] = []
        calls: list[tuple[str, str]] = []

        async def create_page(_self, _token, **kw):
            if kw.get("slug") in {r.get("slug") for r in rows}:
                raise CmsApiError(422, "SLUG_ALREADY_EXISTS")
            created.append(kw)
            rows.append({"id": kw["slug"], "slug": kw["slug"], "status": "draft"})
            return {"id": kw["slug"], "draftVersion": 1}

        async def restore_page(_self, _token, page_id):
            calls.append(("restore", page_id))
            return {}

        async def get_page(_self, _token, page_id):
            calls.append(("get", page_id))
            return {"id": page_id, "draftVersion": 4}

        async def update_page(_self, _token, page_id, **kw):
            calls.append(("update", page_id))
            created.append({"slug": None, "template_for": kw.get("template_for"),
                            "title": kw.get("title"), "page_id": page_id})
            return {"draftVersion": 5}

        async def save_page_draft(_self, _token, page_id, **kw):
            calls.append(("draft", page_id))
            self.assertEqual(kw["body_schema"], BLANK_TEMPLATE_BODY)
            self.assertEqual(kw["base_draft_version"], 5)
            return {"draftVersion": 6}

        report = PushReport()
        with (
            patch.object(CmsClient, "create_page", new=create_page),
            patch.object(CmsClient, "restore_page", new=restore_page),
            patch.object(CmsClient, "get_page", new=get_page),
            patch.object(CmsClient, "update_page", new=update_page),
            patch.object(CmsClient, "save_page_draft", new=save_page_draft),
        ):
            await _ensure_template_pages(
                CmsClient(base_url="http://cms.test"), self._request(), report,
                existing_pages(rows),
            )
        return created, calls, next(s for s in report.steps if s.name == "template_pages")

    async def test_the_page_on_the_slug_is_adopted_as_the_template(self):
        rows = [
            {"id": "h", "slug": "", "title": "Home", "status": "published", "isHomepage": True},
            {"id": "stale", "slug": "article-template", "title": "Article template",
             "status": "archived"},
        ]

        created, calls, step = await self._run(rows)

        self.assertTrue(step.ok, step.error)
        # Archived, so restored before it can be edited; then flagged, then blanked.
        self.assertEqual(calls, [("restore", "stale"), ("get", "stale"),
                                 ("update", "stale"), ("draft", "stale")])
        adopted = next(c for c in created if c.get("page_id") == "stale")
        self.assertEqual(adopted["template_for"], "article")
        self.assertEqual(adopted["title"], "Article Template")
        # No suffixed twin, and the listing template is still created normally.
        self.assertEqual(
            {c["slug"] for c in created if c.get("slug")}, {"article-listing-template"}
        )
        self.assertEqual(step.data["adopted"], 1)

    async def test_a_live_page_the_new_site_still_has_is_never_adopted(self):
        """Only a page the generated site has dropped can be the stale route —
        one it still claims is content, and content is never a template."""
        request = self._request()
        request.site.pages.append(GeneratedPage(
            slug="article-template", title="Article template", seo=PageSeo(),
            body_schema=BodySchema(elements=[]),
        ))
        rows = [
            {"id": "h", "slug": "", "title": "Home", "status": "published", "isHomepage": True},
            {"id": "keep", "slug": "article-template", "title": "Article template",
             "status": "published"},
        ]
        report = PushReport()
        created: list[dict] = []

        async def create_page(_self, _token, **kw):
            created.append(kw)
            return {"id": "x", "draftVersion": 1}

        update = AsyncMock()
        with (
            patch.object(CmsClient, "create_page", new=create_page),
            patch.object(CmsClient, "update_page", new=update),
        ):
            await _ensure_template_pages(
                CmsClient(base_url="http://cms.test"), request, report, existing_pages(rows)
            )

        update.assert_not_awaited()
        # The slug is taken by a real page, so the template takes a free one.
        self.assertIn("article-template-2", {c["slug"] for c in created})

    async def test_a_clean_entity_still_gets_the_plain_slug(self):
        created, _calls, step = await self._run(
            [{"id": "h", "slug": "", "title": "Home", "status": "published", "isHomepage": True}]
        )

        self.assertTrue(step.ok, step.error)
        self.assertEqual({c["slug"] for c in created},
                         {"article-template", "article-listing-template"})

    async def test_an_existing_template_is_never_recreated(self):
        rows = [
            {"id": "h", "slug": "", "title": "Home", "status": "published", "isHomepage": True},
            {"id": "t", "slug": "article-template", "title": "T", "status": "draft",
             "templateFor": "article"},
            {"id": "l", "slug": "article-listing-template", "title": "L", "status": "draft",
             "templateFor": "articleListing"},
        ]

        created, calls, step = await self._run(rows)

        self.assertTrue(step.ok, step.error)
        self.assertEqual(created, [])
        self.assertEqual(calls, [])


class ReplaceTemplatesTest(unittest.IsolatedAsyncioTestCase):
    """"Replace templates" resets the site's live templates to blank drafts,
    which is what makes the builder lay each one out again from the NEW
    homepage's hero. Nothing is published: a published blank template would put
    an empty page on every article, so the live site keeps its current template
    until the owner publishes the rebuilt one."""

    ROWS = [
        {"id": "h", "slug": "", "title": "Home", "status": "published", "isHomepage": True},
        {"id": "art", "slug": "article-template", "title": "Custom article",
         "status": "published", "templateFor": "article"},
        {"id": "lst", "slug": "article-listing-template", "title": "Custom listing",
         "status": "draft", "templateFor": "articleListing"},
        # The new site has no events, but its events are kept, so this follows too.
        {"id": "evt", "slug": "event-template", "title": "Custom event",
         "status": "published", "templateFor": "event"},
        # The owner switched this one off; a reset leaves it off.
        {"id": "off", "slug": "event-listing-template", "title": "Retired",
         "status": "archived", "templateFor": "eventListing"},
    ]

    async def _run(self, *, replace: bool):
        request = TemplateSlugCollisionTest._request(self)
        request.replace_templates = replace
        drafts: dict[str, dict] = {}
        calls: list[tuple[str, str]] = []

        async def get_page(_self, _token, page_id):
            calls.append(("get", page_id))
            return {"id": page_id, "draftVersion": 3}

        async def save_page_draft(_self, _token, page_id, **kw):
            drafts[page_id] = kw
            return {"draftVersion": kw["base_draft_version"] + 1}

        async def restore_page(_self, _token, page_id):
            calls.append(("restore", page_id))
            return {}

        create_page = AsyncMock(return_value={"id": "n", "draftVersion": 1})
        publish_page = AsyncMock()
        report = PushReport()
        with (
            patch.object(CmsClient, "get_page", new=get_page),
            patch.object(CmsClient, "save_page_draft", new=save_page_draft),
            patch.object(CmsClient, "restore_page", new=restore_page),
            patch.object(CmsClient, "create_page", new=create_page),
            patch.object(CmsClient, "publish_page", new=publish_page),
        ):
            await _ensure_template_pages(
                CmsClient(base_url="http://cms.test"), request, report,
                existing_pages(list(self.ROWS)),
            )
        step = next((s for s in report.steps if s.name == "template_pages"), None)
        return drafts, calls, create_page, publish_page, step

    async def test_every_live_template_is_reset_to_a_blank_draft(self):
        drafts, calls, create_page, publish_page, step = await self._run(replace=True)

        self.assertEqual(set(drafts), {"art", "lst", "evt"})
        for page_id, kw in drafts.items():
            with self.subTest(page=page_id):
                self.assertEqual(kw["body_schema"], BLANK_TEMPLATE_BODY)
                self.assertEqual(kw["base_draft_version"], 3)
        # Live templates are not archived, so nothing needs restoring.
        self.assertNotIn("restore", {name for name, _ in calls})
        create_page.assert_not_awaited()
        publish_page.assert_not_awaited()
        self.assertTrue(step.ok, step.error)
        self.assertEqual(step.data["reset"], 3)
        self.assertIn("3 reset to the new design", step.detail)
        self.assertIn("publish", step.warning)

    async def test_an_archived_template_is_left_off(self):
        drafts, calls, *_ = await self._run(replace=True)

        self.assertNotIn("off", drafts)
        self.assertNotIn(("restore", "off"), calls)

    async def test_without_the_option_templates_are_untouched(self):
        drafts, _calls, create_page, publish_page, step = await self._run(replace=False)

        self.assertEqual(drafts, {})
        create_page.assert_not_awaited()
        publish_page.assert_not_awaited()
        self.assertEqual(step.data["reset"], 0)
        self.assertIsNone(step.warning)


class ReplaceTemplatesEndpointTest(unittest.TestCase):
    def test_the_option_reaches_the_push(self):
        from fastapi.testclient import TestClient

        from app.main import app
        from app.services.push_orchestrator import PushReport as Report

        captured = {}

        async def fake_push(req):
            captured["replace"] = req.replace_templates
            return Report(success=True)

        body = {
            "site": {"site_name": "Acme", "pages": [{
                "slug": "", "title": "Home", "is_homepage": True,
                "body_schema": {"elements": []}, "seo": {},
            }]},
            "email": "a@b.c", "password": "x", "entity_token": "tok",
        }
        with patch("app.routers.cms.push_site", new=fake_push):
            client = TestClient(app)
            client.post("/api/cms/push", json=body)
            self.assertFalse(captured["replace"])
            client.post("/api/cms/push", json={**body, "replace_templates": True})
            self.assertTrue(captured["replace"])


class BlankTemplateBodyTest(unittest.TestCase):
    """The adopted page is left exactly as a freshly created one, so the body
    mirrored here must stay equal to the CMS's own default."""

    def test_it_matches_the_cms(self):
        php = pathlib.Path(
            "../../webtree-cms-api/app/Support/PageBuilderDefaults.php"
        ).resolve()
        if not php.exists():
            self.skipTest("webtree-cms-api not checked out beside this repo")
        body = php.read_text()
        block = body[body.index("function bodySchema"):body.index("function headerSchema")]
        element = BLANK_TEMPLATE_BODY["elements"][0]
        for literal in (
            f"'id' => '{element['id']}'",
            f"'type' => '{element['type']}'",
            f"'name' => '{element['name']}'",
            f"'classes' => '{element['classes']}'",
            f"'minHeight' => '{element['styles']['minHeight']}'",
            f"'height' => '{element['styles']['height']}'",
        ):
            with self.subTest(literal=literal):
                self.assertIn(literal, block)


if __name__ == "__main__":
    unittest.main()
