"""A dedicated Contact page is the real destination behind every "#contact"
placeholder href — the header's "Get in touch" CTA and any mid-page `cta`
block the scaffold/LLM defaulted to "#contact" for lack of a page-specific
target. Offline: design brain off, no network, no LLM.
"""

import unittest

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import CtaBlock, HeroBlock, PagePlan, SitePlan
from app.services.schema_builder import plan_to_site


def _hero(headline):
    return HeroBlock(headline=headline, image_query=None)


def _all_hrefs(el) -> list[str]:
    """Every href anywhere in an element subtree — leaf content and containers."""
    hrefs: list[str] = []
    content = getattr(el, "content", None)
    if isinstance(content, list):
        for child in content:
            hrefs.extend(_all_hrefs(child))
    elif content is not None:
        href = getattr(content, "href", None)
        if href:
            hrefs.append(href)
    return hrefs


def _plan(*, with_contact_page: bool) -> SitePlan:
    pages = [
        PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                _hero("Welcome"),
                CtaBlock(headline="Ready?", cta_label="Get in touch", cta_href="#contact"),
            ],
            seo_title="Home",
            seo_description="Home",
        ),
    ]
    if with_contact_page:
        pages.append(
            PagePlan(
                page_type="contact",
                slug="contact",
                title="Contact",
                blocks=[_hero("Get in touch")],
                seo_title="Contact",
                seo_description="Contact",
            )
        )
    return SitePlan(
        site_name="Acme Co",
        brand_mood="modern",
        industry_category="agency",
        pages=pages,
    )


class ContactCtaRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def _build(self, plan: SitePlan):
        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            return await plan_to_site(
                plan,
                brand=BrandIdentity(
                    name="Acme Co", mood="modern", extracted_palette=["#0e7490"]
                ),
            )
        finally:
            settings.design_brain_enabled = original

    async def test_midpage_cta_and_header_cta_route_to_the_real_contact_page(self):
        site = await self._build(_plan(with_contact_page=True))

        home = next(p for p in site.pages if p.is_homepage)
        cta_section = next(
            el for el in home.body_schema.elements if (el.name or "").startswith("CTA")
        )
        self.assertIn("/contact", _all_hrefs(cta_section))
        self.assertNotIn("#contact", _all_hrefs(cta_section))

        self.assertIn("/contact", _all_hrefs(site.header_schema))
        self.assertNotIn("#contact", _all_hrefs(site.header_schema))

    async def test_hrefs_stay_as_anchor_placeholders_without_a_contact_page(self):
        # No dedicated Contact page in the plan — nothing to route to, so the
        # in-page anchor placeholder is left untouched (unchanged behaviour).
        site = await self._build(_plan(with_contact_page=False))

        home = next(p for p in site.pages if p.is_homepage)
        cta_section = next(
            el for el in home.body_schema.elements if (el.name or "").startswith("CTA")
        )
        self.assertIn("#contact", _all_hrefs(cta_section))

        self.assertIn("#contact", _all_hrefs(site.header_schema))


if __name__ == "__main__":
    unittest.main()
