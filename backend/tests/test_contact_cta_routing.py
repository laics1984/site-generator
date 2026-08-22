"""Every "#contact" placeholder href — the header's "Get in touch" CTA, a
mid-page `cta` block, a scaffolded hero — resolves to the site's real contact
destination: the Contact page (preferring one that renders a form), else the
page carrying a `contact` section. A bare "#contact" only ever resolved on the
page rendering that section, so it never ships. Offline: design brain off, no
network, no LLM.
"""

import unittest

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import (
    ContactBlock,
    CtaBlock,
    HeroBlock,
    PagePlan,
    SitePlan,
)
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


def _anchor_ids(page) -> list[str]:
    return [
        anchor
        for el in page.body_schema.elements
        if (anchor := getattr(el, "anchorId", None))
    ]


def _page(page_type, slug, title, *blocks, **kwargs) -> PagePlan:
    return PagePlan(
        page_type=page_type,
        slug=slug,
        title=title,
        blocks=list(blocks),
        seo_title=title,
        seo_description=title,
        **kwargs,
    )


def _plan(*extra_pages: PagePlan, home_blocks=None) -> SitePlan:
    home = _page(
        "home",
        "",
        "Home",
        *(
            home_blocks
            or [
                _hero("Welcome"),
                CtaBlock(headline="Ready?", cta_label="Get in touch", cta_href="#contact"),
            ]
        ),
        is_homepage=True,
    )
    return SitePlan(
        site_name="Acme Co",
        brand_mood="modern",
        industry_category="agency",
        pages=[home, *extra_pages],
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

    def _home_cta_hrefs(self, site) -> list[str]:
        home = next(p for p in site.pages if p.is_homepage)
        cta_section = next(
            el for el in home.body_schema.elements if (el.name or "").startswith("CTA")
        )
        return _all_hrefs(cta_section)

    async def test_midpage_cta_and_header_cta_route_to_the_real_contact_page(self):
        site = await self._build(
            _plan(_page("contact", "contact", "Contact", _hero("Get in touch")))
        )

        self.assertIn("/contact", self._home_cta_hrefs(site))
        self.assertNotIn("#contact", self._home_cta_hrefs(site))

        self.assertIn("/contact", _all_hrefs(site.header_schema))
        self.assertNotIn("#contact", _all_hrefs(site.header_schema))

    async def test_a_contact_page_with_a_form_wins_over_one_without(self):
        # The CTA promises an action, so the page that can complete it is the
        # destination — even though both read as Contact.
        site = await self._build(
            _plan(
                _page("contact", "contact-details", "Contact Details", _hero("Find us")),
                _page(
                    "contact",
                    "contact-us",
                    "Contact Us",
                    _hero("Get in touch"),
                    ContactBlock(heading="Send us a message"),
                ),
            )
        )

        self.assertIn("/contact-us", _all_hrefs(site.header_schema))
        self.assertIn("/contact-us", self._home_cta_hrefs(site))

    async def test_a_contact_page_the_plan_mislabeled_is_still_the_destination(self):
        # menu_builder drops this page from the primary nav on the same
        # slug/title inference, so the header CTA has to reach it regardless of
        # the page_type the LLM stamped on it.
        site = await self._build(
            _plan(_page("landing", "contact-us", "Contact Us", _hero("Get in touch")))
        )

        self.assertIn("/contact-us", _all_hrefs(site.header_schema))
        self.assertIn("/contact-us", self._home_cta_hrefs(site))

    async def test_a_roster_profile_named_contact_someone_is_not_the_destination(self):
        # "Contact Ashley" reads as a contact page to the slug/title heuristic,
        # but a menu-hidden roster detail page is a person, not the contact desk.
        site = await self._build(
            _plan(
                _page(
                    "landing",
                    "committee/ashley",
                    "Contact Ashley Jinivon",
                    _hero("Ashley Jinivon"),
                    menu_hidden=True,
                    parent_slug="committee",
                ),
                _page("contact", "contact", "Contact", _hero("Get in touch")),
            )
        )

        self.assertIn("/contact", _all_hrefs(site.header_schema))
        self.assertNotIn("/committee/ashley", _all_hrefs(site.header_schema))

    async def test_without_a_contact_page_the_ctas_land_on_the_contact_section(self):
        site = await self._build(
            _plan(
                home_blocks=[
                    _hero("Welcome"),
                    CtaBlock(headline="Ready?", cta_label="Get in touch", cta_href="#contact"),
                    ContactBlock(heading="Get in touch"),
                ]
            )
        )
        home = next(p for p in site.pages if p.is_homepage)

        self.assertIn("/#contact", _all_hrefs(site.header_schema))
        self.assertIn("/#contact", self._home_cta_hrefs(site))
        # ...and the section carries the matching anchor, so the link resolves.
        self.assertIn("contact", _anchor_ids(home))

    async def test_no_contact_affordance_at_all_drops_the_header_cta(self):
        # Nothing to route to: no Contact page, no contact section. A dead
        # "#contact" button is worse than no button.
        site = await self._build(_plan())

        self.assertNotIn("#contact", _all_hrefs(site.header_schema))

    async def test_a_translated_page_reaches_its_own_contact_twin(self):
        plan = _plan(
            _page("contact", "contact", "Contact", _hero("Get in touch")),
            _page(
                "contact",
                "bm/contact",
                "Hubungi Kami",
                _hero("Hubungi kami"),
                locale="bm",
                translation_of="contact",
            ),
            _page(
                "landing",
                "bm/keahlian",
                "Keahlian",
                _hero("Keahlian"),
                CtaBlock(headline="Sedia?", cta_label="Hubungi kami", cta_href="#contact"),
                locale="bm",
                translation_of="keahlian",
            ),
        )
        site = await self._build(plan)

        bm_page = next(p for p in site.pages if p.slug == "bm/keahlian")
        cta_section = next(
            el for el in bm_page.body_schema.elements if (el.name or "").startswith("CTA")
        )
        self.assertIn("/bm/contact", _all_hrefs(cta_section))
        # The site-wide header still points at the source-language page.
        self.assertIn("/contact", _all_hrefs(site.header_schema))


if __name__ == "__main__":
    unittest.main()
