"""The single-page scaffold path.

A source carrying one page's worth of grounded facts must not be handed the
industry template's four or five pages — the fidelity net would strip them back
to almost nothing and ship a site of empty rooms.
"""

import unittest

from app.models.content_blocks import SourceContent
from app.services.page_inference import infer_page_scaffolds


def _source(**overrides) -> SourceContent:
    base = dict(
        source_kind="facebook",
        source_ref="https://www.facebook.com/acmecoffee",
        title="Acme Coffee Roasters",
        raw_text="Acme Coffee Roasters — Coffee Shop\nAbout: Specialty coffee roasted in-house.",
    )
    base.update(overrides)
    return SourceContent(**base)


class SinglePageTest(unittest.TestCase):
    def test_returns_the_homepage_plus_legal_pages_only(self):
        scaffolds = infer_page_scaffolds(
            _source(), industry="restaurant", site_name="Acme", single_page=True
        )
        self.assertTrue(scaffolds[0].is_homepage)
        self.assertTrue(all(s.is_legal for s in scaffolds[1:]))

    def test_without_the_flag_the_template_still_fans_out(self):
        """The default path is untouched — a thin website should still get the
        industry template's core + suggested pages."""
        scaffolds = infer_page_scaffolds(
            _source(source_kind="url"), industry="restaurant", site_name="Acme"
        )
        self.assertGreater(len([s for s in scaffolds if not s.is_legal]), 1)

    def test_an_explicit_section_list_beats_the_industry_pattern(self):
        sections = ["hero", "about", "gallery", "contact", "cta"]
        scaffolds = infer_page_scaffolds(
            _source(),
            industry="restaurant",
            site_name="Acme",
            single_page=True,
            homepage_sections_override=sections,
        )
        self.assertEqual(scaffolds[0].sections, sections)

    def test_no_override_falls_back_to_the_industry_pattern(self):
        scaffolds = infer_page_scaffolds(
            _source(), industry="restaurant", site_name="Acme", single_page=True
        )
        home = scaffolds[0]
        self.assertEqual(home.sections[0], "hero")
        self.assertEqual(home.sections[-1], "cta")

    def test_the_homepage_slug_stays_the_site_root(self):
        scaffolds = infer_page_scaffolds(
            _source(), industry="other", site_name="Acme", single_page=True
        )
        self.assertEqual(scaffolds[0].slug, "")


if __name__ == "__main__":
    unittest.main()
