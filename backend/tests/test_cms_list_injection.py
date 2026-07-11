"""Blog / events pages get the builder's dynamic list element appended.

Offline through plan_to_site — Pexels unconfigured, design brain off.
"""

import unittest

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import HeroBlock, PagePlan, SitePlan
from app.services.schema_builder import plan_to_site
from app.services.theme import build_theme


def _page(slug, page_type, title, *, homepage=False):
    return PagePlan(
        page_type=page_type,
        slug=slug,
        title=title,
        is_homepage=homepage,
        blocks=[
            HeroBlock(
                headline=f"{title} headline",
                subheadline="Sub headline.",
                image_query="office",
                primary_cta_label="Contact",
                primary_cta_href="/contact",
            )
        ],
        seo_title=title,
        seo_description=title,
    )


class CmsListInjectionTest(unittest.IsolatedAsyncioTestCase):
    async def _site(self):
        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            plan = SitePlan(
                site_name="Acme Co",
                brand_mood="friendly",
                industry_category="other",
                pages=[
                    _page("home", "home", "Home", homepage=True),
                    _page("blog", "blog", "Blog"),
                    _page("events", "events", "Events"),
                ],
            )
            return await plan_to_site(
                plan,
                brand=BrandIdentity(name="Acme Co", mood="friendly"),
                theme=build_theme("#0e7490"),
            )
        finally:
            settings.design_brain_enabled = original

    async def test_blog_page_ends_with_articles_list(self):
        site = await self._site()
        blog = next(p for p in site.pages if p.slug == "blog")
        last = blog.body_schema.elements[-1]
        self.assertEqual(last.type, "articlesList")
        self.assertEqual(last.content.source, "articles")
        self.assertEqual(last.content.heading, "Latest Articles")
        self.assertEqual(last.content.layout, "grid")
        self.assertTrue(last.content.pagination["enabled"])

    async def test_events_page_ends_with_events_list(self):
        site = await self._site()
        events = next(p for p in site.pages if p.slug == "events")
        last = events.body_schema.elements[-1]
        self.assertEqual(last.type, "eventsList")
        self.assertEqual(last.content.source, "events")
        self.assertEqual(last.content.heading, "Upcoming Events")

    async def test_other_pages_get_no_list_element(self):
        site = await self._site()
        home = next(p for p in site.pages if p.is_homepage)
        types = {el.type for el in home.body_schema.elements}
        self.assertNotIn("articlesList", types)
        self.assertNotIn("eventsList", types)


if __name__ == "__main__":
    unittest.main()
