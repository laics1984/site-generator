"""Content-driven layout and page rhythm (design-identity plan, Phase 4):
the homepage hero answers to the evidence, sections avoid repeating the one
above, the zigzag is a scheme's choice, and a flat page is reported."""

from __future__ import annotations

import asyncio
import unittest
from unittest import mock

from app.config import settings
from app.models.builder_schema import BodySchema, BuilderElement, GeneratedPage, GeneratedSite, PageSeo
from app.models.content_blocks import AboutBlock, ImageMetadata, PagePlan
from app.services import design_schemes
from app.services.design_direction import ContentProfile
from app.services.hero_director import IMAGELESS_HERO_IDS, _LEADS, plan_site_heroes
from app.services.section_content import block_to_section, visual_weight
from app.services.ux_audit import audit_design


def _pages() -> list[PagePlan]:
    def page(slug, page_type, homepage=False):
        return PagePlan(
            page_type=page_type, slug=slug, title=slug.title() or "Home",
            is_homepage=homepage, blocks=[], seo_title=slug or "home",
            seo_description=slug or "home",
        )

    return [page("", "home", True), page("about", "about"), page("programs", "services"),
            page("contact", "contact")]


def _heroes(**kwargs):
    defaults = dict(mood="friendly", has_source_background=False, seed="Little Acorns")
    defaults.update(kwargs)
    return plan_site_heroes(_pages(), **defaults)


class TypeLedHeroTest(unittest.TestCase):
    """A source with no photograph opens on type, not on a stock picture
    standing in for the business on its own front page."""

    def test_no_photograph_means_every_page_opens_on_type(self):
        scheme = design_schemes.by_slug("storybook-editorial")
        heroes = _heroes(has_hero_photo=False, scheme=scheme)
        self.assertEqual(heroes[""].template_id, _LEADS[scheme.type_led_hero].template_id)
        for directive in heroes.values():
            self.assertIn(directive.template_id, IMAGELESS_HERO_IDS)

    def test_it_holds_under_the_site_wide_full_bleed_policy(self):
        with mock.patch.object(settings, "hero_fullbleed_all_pages", True):
            heroes = _heroes(has_hero_photo=False)
        self.assertTrue(all(d.template_id in IMAGELESS_HERO_IDS for d in heroes.values()))

    def test_the_pill_and_a_source_background_still_win(self):
        forced = _heroes(has_hero_photo=False, force_background=True)
        self.assertEqual(forced[""].template_id, "hero-background-bold")
        pinned = _heroes(has_hero_photo=False, has_source_background=True)
        self.assertEqual(pinned[""].template_id, "hero-background-bold")

    def test_unknown_evidence_changes_nothing(self):
        self.assertEqual(_heroes(has_hero_photo=None), _heroes())


class SchemeHomepageLeadTest(unittest.TestCase):
    def test_a_schemes_lead_opens_the_homepage_under_either_policy(self):
        scheme = design_schemes.by_slug("calm-clinical")  # split-washed
        for fullbleed in (True, False):
            with mock.patch.object(settings, "hero_fullbleed_all_pages", fullbleed):
                heroes = _heroes(scheme=scheme, has_hero_photo=True)
            self.assertEqual(heroes[""].template_id, "hero-modern-split", fullbleed)

    def test_banded_keeps_a_photo_lead_but_not_a_type_lead(self):
        photo_led = _heroes(scheme=design_schemes.by_slug("calm-clinical"), hero_height="banded")
        self.assertEqual(photo_led[""].template_id, "hero-modern-split")
        type_led = _heroes(scheme=design_schemes.by_slug("poster-display"), hero_height="banded")
        self.assertEqual(type_led[""].template_id, "hero-background-bold")

    def test_every_scheme_lead_names_a_real_hero_and_type_leads_show_no_photo(self):
        for scheme in design_schemes.DESIGN_SCHEMES:
            if scheme.homepage_hero:
                self.assertIn(scheme.homepage_hero, _LEADS, scheme.slug)
            self.assertIn(scheme.type_led_hero, _LEADS, scheme.slug)
            self.assertIn(
                _LEADS[scheme.type_led_hero].template_id, IMAGELESS_HERO_IDS, scheme.slug
            )


class HeroEvidenceTest(unittest.TestCase):
    def _meta(self, **kw):
        return ImageMetadata(url="https://x/p.jpg", role="content", **kw)

    def test_three_answers(self):
        self.assertTrue(ContentProfile.of_images([self._meta(width=1600, height=1000)]).has_hero_photo)
        self.assertFalse(ContentProfile.of_images([]).has_hero_photo)
        self.assertFalse(ContentProfile.of_images([self._meta(width=640, height=400)]).has_hero_photo)
        # Never measured: the resolver judges it, nothing is decided in advance.
        self.assertIsNone(ContentProfile.of_images([self._meta()]).has_hero_photo)


class AdjacencyTest(unittest.TestCase):
    EDITORIAL_BIAS = design_schemes.by_slug("storybook-editorial").layout_bias

    def _pick(self, block, **kw):
        return block_to_section(block.model_copy(deep=True), mood="friendly", industry="childcare", **kw)[0]

    def test_a_repeat_of_the_family_above_is_swapped_within_the_brands_language(self):
        about = AboutBlock(heading="About", body="Story. " * 40, image_query="team at work")
        first = self._pick(about, layout_bias=self.EDITORIAL_BIAS)
        again = self._pick(about, layout_bias=self.EDITORIAL_BIAS, previous_family=first["layoutVariant"])
        self.assertNotEqual(again["layoutVariant"], first["layoutVariant"])
        # Same use of the picture: a lone image either way.
        self.assertTrue(any(s["kind"] == "image" for s in again["slots"]))

    def test_no_alternative_in_the_language_means_the_repeat_stands(self):
        about = AboutBlock(heading="About", body="Story. " * 40)
        first = self._pick(about)
        self.assertEqual(self._pick(about, previous_family=first["layoutVariant"])["id"], first["id"])

    def test_an_explicit_pick_is_never_swapped(self):
        about = AboutBlock(heading="About", body="Story. " * 40, image_query="team at work")
        chosen = self._pick(about, explicit_id="about-image-split", previous_family="split")
        self.assertEqual(chosen["id"], "about-image-split")


class ZigzagIsASchemesChoiceTest(unittest.TestCase):
    def _about_rows(self, scheme_slug: str) -> list[list[str]]:
        from scripts.measure_design_diversity import fixture_brand, offline_generation
        from app.models.content_blocks import HeroBlock, SitePlan
        from app.services.schema_builder import plan_to_site

        plan = SitePlan(site_name="Story Co", industry_category="childcare", pages=[PagePlan(
            page_type="home", slug="", title="Home", is_homepage=True,
            seo_title="Story Co — Home", seo_description="A description long enough for the audit.",
            blocks=[HeroBlock(headline="Story Co")] + [
                AboutBlock(heading=f"Chapter {i}", body="Story. " * 30, image_query=f"scene {i}")
                for i in range(3)
            ],
        )])
        original = design_schemes.select_scheme
        design_schemes.select_scheme = lambda **_kw: design_schemes.by_slug(scheme_slug)
        try:
            with offline_generation(schemes=True, history=False):
                site = asyncio.run(plan_to_site(plan, brand=fixture_brand("Story Co", "#0f766e", "childcare")))
        finally:
            design_schemes.select_scheme = original
        rows = []
        for section in site.pages[0].body_schema.elements:
            for child in section.content if isinstance(section.content, list) else []:
                if child.type == "2Col":
                    rows.append([c.name for c in child.content])
        return rows

    def test_an_alternating_scheme_zigzags_and_a_restrained_one_holds_its_side(self):
        alternating = self._about_rows("soft-warm")
        held = self._about_rows("civic-trust")
        self.assertGreaterEqual(len(held), 2)
        self.assertEqual(len({tuple(r) for r in held}), 1)  # one side, every row
        self.assertGreater(len({tuple(r) for r in alternating}), 1)


class RhythmAuditTest(unittest.TestCase):
    @staticmethod
    def _site(template_names: list[str]) -> GeneratedSite:
        sections = [BuilderElement(name=n, type="container", content=[]) for n in template_names]
        page = GeneratedPage(slug="", title="Home", is_homepage=True,
                             body_schema=BodySchema(elements=sections), seo=PageSeo())
        return GeneratedSite(site_name="x", pages=[page])

    def test_three_medium_sections_in_a_row_are_reported(self):
        flat = self._site([
            "Features - Card Grid", "Services - Offer Grid", "Testimonials - Quote Grid",
        ])
        self.assertTrue(any(f.rule == "rhythm-monotone" for f in audit_design(flat)))

    def test_a_varied_page_is_not(self):
        varied = self._site(["Features - Card Grid", "CTA - Banner", "Testimonials - Quote Grid"])
        self.assertFalse(audit_design(varied))

    def test_every_catalog_body_template_has_a_weight(self):
        from app.services.template_filler import load_catalog

        for entry in load_catalog()["sections"]:
            if entry["id"].startswith("chrome-") or entry["sectionType"] in ("header", "footer"):
                continue
            self.assertIn(visual_weight(entry["id"]), ("heavy", "medium", "light"), entry["id"])


if __name__ == "__main__":
    unittest.main()
