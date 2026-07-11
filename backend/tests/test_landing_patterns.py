"""Tests for curated homepage landing patterns and their wiring into templates."""

import unittest
from typing import get_args

from app.models.content_blocks import SectionType
from app.models.industry import IndustryCategory
from app.services.industry_templates import get_template
from app.services.landing_patterns import (
    _LANDING_PATTERNS,
    _MAX_HOMEPAGE_SECTIONS,
    homepage_sections,
)

VALID = frozenset(get_args(SectionType))
INDUSTRIES = get_args(IndustryCategory)


class LandingPatternIntegrityTest(unittest.TestCase):
    def test_patterns_are_well_formed(self):
        for p in _LANDING_PATTERNS:
            self.assertTrue(p.industries, f"{p.name}: no industries")
            self.assertEqual(p.sections[0], "hero", f"{p.name}: must start with hero")
            self.assertEqual(p.sections[-1], "cta", f"{p.name}: must end with cta")
            self.assertEqual(len(p.sections), len(set(p.sections)), f"{p.name}: dup section")
            for s in p.sections:
                self.assertIn(s, VALID, f"{p.name}: unknown section {s!r}")

    def test_every_industry_resolves_to_valid_sections(self):
        for ind in INDUSTRIES:
            secs = homepage_sections(ind)
            self.assertEqual(secs[0], "hero")
            self.assertEqual(secs[-1], "cta")
            self.assertEqual(len(secs), len(set(secs)))
            self.assertTrue(set(secs).issubset(VALID), ind)

    def test_no_industry_homepage_exceeds_single_call_budget(self):
        # A homepage is generated in ONE LLM call; too many sections overflow the
        # token budget (empty MLX stream → 502). Every industry, with and without
        # a seed, must stay within the ceiling.
        for ind in INDUSTRIES:
            for seed in (None, "Seed A", "Seed B"):
                secs = homepage_sections(ind, seed=seed)
                self.assertLessEqual(len(secs), _MAX_HOMEPAGE_SECTIONS, ind)

    def test_normalize_cap_trims_oversized_patterns_keeping_hero_and_cta(self):
        # An over-long section list is trimmed from the body; hero stays first,
        # cta stays last, so the conversion frame survives the cap.
        oversized = ["hero", "features", "about", "services", "process",
                     "team", "testimonials", "faq", "gallery", "stats", "cta"]
        secs = homepage_sections("childcare", extra_sections=oversized)
        self.assertEqual(secs[0], "hero")
        self.assertEqual(secs[-1], "cta")
        self.assertLessEqual(len(secs), _MAX_HOMEPAGE_SECTIONS)


class LandingSelectionTest(unittest.TestCase):
    def test_industry_defaults_are_meaningful(self):
        self.assertIn("menu", homepage_sections("restaurant"))
        self.assertIn("pricing", homepage_sections("saas"))
        self.assertIn("team", homepage_sections("nonprofit"))
        self.assertIn("process", homepage_sections("professional-services"))

    def test_unknown_industry_falls_back_to_default(self):
        self.assertEqual(
            homepage_sections("quantum-widgets"),
            ["hero", "features", "testimonials", "cta"],
        )
        self.assertEqual(
            homepage_sections(None),
            ["hero", "features", "testimonials", "cta"],
        )

    def test_extra_sections_merged_before_cta_no_dupes(self):
        secs = homepage_sections("saas", extra_sections=["gallery"])
        self.assertEqual(secs[-1], "cta")
        self.assertIn("gallery", secs)
        self.assertEqual(len(secs), len(set(secs)))
        # extra inserted before the closing CTA, not after it
        self.assertLess(secs.index("gallery"), secs.index("cta"))

    def test_seed_varies_among_equally_fitting_patterns(self):
        seen = {tuple(homepage_sections("agency", seed=f"brand-{i}")) for i in range(40)}
        self.assertGreater(len(seen), 1)

    def test_selection_is_deterministic(self):
        self.assertEqual(
            homepage_sections("agency", seed="Acme"),
            homepage_sections("agency", seed="Acme"),
        )

    def test_always_hero_first_cta_last(self):
        for ind in INDUSTRIES:
            for seed in (None, "Acme", "Beta Corp", "Zeta"):
                secs = homepage_sections(ind, seed=seed)
                self.assertEqual(secs[0], "hero")
                self.assertEqual(secs[-1], "cta")


class HomepageVarietyTest(unittest.TestCase):
    """Brand/site name seeds the homepage pattern so same-industry sites differ."""

    def test_inferred_home_varies_by_site_name(self):
        from app.models.content_blocks import SourceContent
        from app.services.page_inference import infer_page_scaffolds

        def home_for(name):
            src = SourceContent(source_kind="url", source_ref="https://x.com", raw_text="hi")
            scaffolds = infer_page_scaffolds(src, industry="agency", site_name=name)
            home = next(s for s in scaffolds if s.is_homepage)
            return tuple(home.sections)

        seen = {home_for(f"Studio {i}") for i in range(40)}
        self.assertGreater(len(seen), 1)  # agency has several fitting patterns

    def test_inferred_home_is_deterministic_per_name(self):
        from app.models.content_blocks import SourceContent
        from app.services.page_inference import infer_page_scaffolds

        def home_for(name):
            src = SourceContent(source_kind="url", source_ref="https://x.com", raw_text="hi")
            scaffolds = infer_page_scaffolds(src, industry="agency", site_name=name)
            return tuple(next(s for s in scaffolds if s.is_homepage).sections)

        self.assertEqual(home_for("Aurora Studio"), home_for("Aurora Studio"))


class TemplateWiringTest(unittest.TestCase):
    def test_templates_use_industry_fit_homepage(self):
        # saas template homepage should now follow the pricing-led pattern.
        home = next(p for p in get_template("saas").core_pages if p.is_homepage)
        self.assertIn("pricing", home.sections)
        # restaurant homepage should feature the menu.
        home = next(p for p in get_template("restaurant").core_pages if p.is_homepage)
        self.assertIn("menu", home.sections)

    def test_every_template_homepage_is_valid(self):
        for ind in INDUSTRIES:
            home = next(p for p in get_template(ind).core_pages if p.is_homepage)
            self.assertEqual(home.sections[0], "hero")
            self.assertEqual(home.sections[-1], "cta")
            self.assertTrue(set(home.sections).issubset(VALID), ind)


if __name__ == "__main__":
    unittest.main()
