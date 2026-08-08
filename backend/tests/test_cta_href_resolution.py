"""Every CTA button ships pointing at a page this site actually generated.

The LLM writes CTA hrefs from the SOURCE site's vocabulary, not from the page
set we ended up generating, so it invents paths that exist nowhere here
("/book-now") and in-page anchors that are never stamped ("#pricing" — the only
anchors this renderer emits are the hero scroll cue and the contact section).
Both used to ship as dead links, alongside a bare "#" wherever a CTA carried a
label but no href.

`schema_builder._resolve_block_cta_hrefs` reads every planned href as a
*reference to a page*, matches it against the real page set, and falls back to
the site's contact destination. What still resolves nowhere loses its label too,
so the button is dropped rather than shipped dead.

Offline: design brain off, no network, no LLM.
"""

import unittest

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import (
    ContactBlock,
    CtaBlock,
    HeroBlock,
    PagePlan,
    PricingBlock,
    PricingTier,
    ServiceItem,
    ServicesBlock,
    SitePlan,
)
from app.services.schema_builder import plan_to_site


def _hero(headline: str, **kwargs) -> HeroBlock:
    return HeroBlock(headline=headline, image_query=None, **kwargs)


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


def _all_hrefs(el) -> list[str]:
    """Every href anywhere in an element subtree."""
    hrefs: list[str] = []
    content = getattr(el, "content", None)
    if isinstance(content, list):
        for child in content:
            hrefs.extend(_all_hrefs(child))
    elif content is not None:
        if href := getattr(content, "href", None):
            hrefs.append(href)
    return hrefs


def _page_hrefs(site, slug: str) -> list[str]:
    page = next(p for p in site.pages if p.slug == slug)
    return [h for el in page.body_schema.elements for h in _all_hrefs(el)]


class CtaHrefResolutionTest(unittest.IsolatedAsyncioTestCase):
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

    def _plan(self, *pages: PagePlan) -> SitePlan:
        return SitePlan(
            site_name="Acme Co",
            brand_mood="modern",
            industry_category="agency",
            pages=list(pages),
        )

    async def test_an_invented_path_falls_back_to_the_contact_destination(self):
        # "/book-now" is a page the source site had and we never generated.
        site = await self._build(
            self._plan(
                _page(
                    "home",
                    "",
                    "Home",
                    _hero("Welcome", primary_cta_label="Book a class",
                          primary_cta_href="/book-now"),
                    is_homepage=True,
                ),
                _page("contact", "contact", "Contact", _hero("Get in touch")),
            )
        )

        hrefs = _page_hrefs(site, "")
        self.assertIn("/contact", hrefs)
        self.assertNotIn("/book-now", hrefs)

    async def test_a_near_miss_path_lands_on_the_page_it_names(self):
        # The contact page exists too, so reaching /services proves the href was
        # MATCHED rather than swept into the contact fallback.
        site = await self._build(
            self._plan(
                _page(
                    "home",
                    "",
                    "Home",
                    _hero("Welcome", primary_cta_label="See our work",
                          primary_cta_href="/Our-Services/"),
                    is_homepage=True,
                ),
                _page("services", "services", "Services", _hero("Services")),
                _page("contact", "contact", "Contact", _hero("Get in touch")),
            )
        )

        self.assertIn("/services", _page_hrefs(site, ""))

    async def test_a_child_page_linked_by_its_last_segment_still_resolves(self):
        site = await self._build(
            self._plan(
                _page(
                    "home",
                    "",
                    "Home",
                    _hero("Welcome", primary_cta_label="Coaching",
                          primary_cta_href="/coaching"),
                    is_homepage=True,
                ),
                _page("services", "services", "Services", _hero("Services")),
                _page(
                    "service",
                    "services/coaching",
                    "Coaching",
                    _hero("Coaching"),
                    parent_slug="services",
                ),
                _page("contact", "contact", "Contact", _hero("Get in touch")),
            )
        )

        self.assertIn("/services/coaching", _page_hrefs(site, ""))

    async def test_an_unstamped_anchor_is_read_as_a_page_reference(self):
        # "#pricing" is never stamped anywhere, but a Pricing page exists — the
        # anchor is what the LLM meant, the page is where it has to go.
        site = await self._build(
            self._plan(
                _page(
                    "home",
                    "",
                    "Home",
                    _hero("Welcome"),
                    PricingBlock(
                        heading="Plans",
                        tiers=[
                            PricingTier(name="Pro", price="$99", features=["B"],
                                        cta_label="Start", cta_href="#pricing"),
                        ],
                    ),
                    is_homepage=True,
                ),
                _page("pricing", "pricing", "Pricing", _hero("Pricing")),
                _page("contact", "contact", "Contact", _hero("Get in touch")),
            )
        )

        hrefs = _page_hrefs(site, "")
        self.assertIn("/pricing", hrefs)
        self.assertNotIn("#pricing", hrefs)

    async def test_a_secondary_cta_with_no_href_is_dropped_not_rendered_as_hash(self):
        # A label with no href used to render as href="#". The secondary action
        # gets no contact fallback either — the primary beside it already goes
        # there, and two buttons to one destination is not a choice.
        site = await self._build(
            self._plan(
                _page(
                    "home",
                    "",
                    "Home",
                    _hero(
                        "Welcome",
                        primary_cta_label="Get in touch",
                        secondary_cta_label="Our story",
                        secondary_cta_href=None,
                    ),
                    is_homepage=True,
                ),
                _page("contact", "contact", "Contact", _hero("Get in touch")),
            )
        )

        hrefs = _page_hrefs(site, "")
        self.assertNotIn("#", hrefs)
        self.assertEqual(["/contact"], hrefs)

    async def test_the_homepage_is_reachable_by_the_names_the_llm_writes_for_it(self):
        # The homepage has no slug to match on, so it is indexed under its title
        # and the literal "home".
        site = await self._build(
            self._plan(
                _page(
                    "home",
                    "",
                    "Home",
                    _hero("Welcome"),
                    CtaBlock(headline="Start over", cta_label="Back home",
                             cta_href="/home"),
                    is_homepage=True,
                ),
                _page("contact", "contact", "Contact", _hero("Get in touch")),
            )
        )

        self.assertIn("/", _page_hrefs(site, ""))

    async def test_external_and_protocol_hrefs_are_left_alone(self):
        site = await self._build(
            self._plan(
                _page(
                    "home",
                    "",
                    "Home",
                    _hero("Welcome", primary_cta_label="Book on Calendly",
                          primary_cta_href="https://calendly.com/acme"),
                    CtaBlock(headline="Call us", cta_label="Call",
                             cta_href="tel:+6591234567"),
                    is_homepage=True,
                ),
                _page("contact", "contact", "Contact", _hero("Get in touch")),
            )
        )

        hrefs = _page_hrefs(site, "")
        self.assertIn("https://calendly.com/acme", hrefs)
        self.assertIn("tel:+6591234567", hrefs)

    async def test_a_site_with_no_contact_affordance_drops_the_buttons(self):
        # Nothing to fall back to: no Contact page, no `contact` section. A
        # button with nowhere to go is dropped, never shipped pointing at
        # "#contact" — which lands nowhere on every page.
        site = await self._build(
            self._plan(
                _page(
                    "home",
                    "",
                    "Home",
                    _hero("Welcome", primary_cta_label="Enquire",
                          primary_cta_href="#contact"),
                    CtaBlock(headline="Ready?", cta_label="Call us",
                             cta_href="#contact"),
                    is_homepage=True,
                ),
                _page("services", "services", "Services", _hero("Services")),
            )
        )

        self.assertEqual([], _page_hrefs(site, ""))

    async def test_a_translated_page_resolves_against_its_own_locale(self):
        site = await self._build(
            self._plan(
                _page("home", "", "Home", _hero("Welcome"), is_homepage=True),
                _page("contact", "contact", "Contact", _hero("Get in touch")),
                # A `cta` block, not the hero: an interior hero drops its CTA
                # outright (apply_hero_cta_policy), so it can't show routing.
                _page(
                    "home",
                    "zh",
                    "首页",
                    _hero("欢迎"),
                    CtaBlock(headline="准备好了吗?", cta_label="联系我们",
                             cta_href="/contact"),
                    locale="zh",
                ),
                _page(
                    "contact",
                    "zh/contact",
                    "联系我们",
                    _hero("联系我们"),
                    ContactBlock(heading="联系我们"),
                    locale="zh",
                ),
            )
        )

        self.assertIn("/zh/contact", _page_hrefs(site, "zh"))

    async def test_no_page_ships_a_link_off_the_generated_page_set(self):
        """Site-wide invariant, across the block kinds that carry CTAs."""
        site = await self._build(
            self._plan(
                _page(
                    "home",
                    "",
                    "Home",
                    _hero("Welcome", primary_cta_label="Book a class",
                          primary_cta_href="/book-now",
                          secondary_cta_label="Our story",
                          secondary_cta_href="/story"),
                    ServicesBlock(
                        heading="Services",
                        items=[
                            ServiceItem(title="Coaching", description="1:1.",
                                        cta_label="More", cta_href="#contact"),
                        ],
                    ),
                    PricingBlock(
                        heading="Plans",
                        tiers=[
                            PricingTier(name="Basic", price="$29", features=["A"],
                                        cta_label="Start", cta_href="/signup"),
                        ],
                    ),
                    CtaBlock(headline="Ready?", cta_label="Get in touch",
                             cta_href="#contact"),
                    is_homepage=True,
                ),
                _page("about", "about", "About", _hero("About us")),
                _page(
                    "contact",
                    "contact",
                    "Contact",
                    _hero("Contact"),
                    ContactBlock(heading="Get in touch"),
                ),
            )
        )

        slugs = {f"/{p.slug}".rstrip("/") or "/" for p in site.pages}
        hrefs = [
            h
            for page in site.pages
            for el in page.body_schema.elements
            for h in _all_hrefs(el)
        ]
        hrefs.extend(_all_hrefs(site.header_schema))
        hrefs.extend(_all_hrefs(site.footer_schema))
        self.assertTrue(hrefs, "expected the site to render some links")
        for href in hrefs:
            if href.startswith(("http", "mailto:", "tel:")):
                continue
            self.assertNotEqual("#", href, "a bare '#' shipped")
            path = href.split("#", 1)[0].rstrip("/") or "/"
            self.assertIn(path, slugs, f"{href!r} points off the generated page set")


if __name__ == "__main__":
    unittest.main()
