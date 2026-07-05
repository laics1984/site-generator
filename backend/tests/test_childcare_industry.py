"""Childcare / kindergarten industry wiring.

The childcare design brief (parent-first trust, soft pastel palette, rounded
friendly type, authentic children-at-play photography, book-a-tour conversion
structure) is spread across the industry-keyed registries. These tests pin
each registry's childcare entry so the brief keeps flowing into generation.
"""

import unittest

from app.models.content_blocks import PagePlan, industry_default_mood
from app.services.hero_director import plan_site_heroes
from app.services.industry_personality import personality_for
from app.services.industry_templates import get_template
from app.services.landing_patterns import homepage_sections
from app.services.media import _contextual_non_person_query, _INDUSTRY_CONTEXT_QUERIES
from app.services.theme import build_theme


class ChildcarePersonalityTest(unittest.TestCase):
    def test_has_its_own_personality(self):
        p = personality_for("childcare")
        self.assertNotEqual(p, personality_for("other"))
        # Parent-first tone, not child-facing.
        self.assertIn("parent", p.voice)
        # Signature visual moves from the design brief.
        self.assertIn("rounded", p.design)
        self.assertIn("pastel", p.design)


class ChildcareMoodTest(unittest.TestCase):
    def test_defaults_to_friendly_not_playful(self):
        # Joyful without being childish: the audience is parents.
        self.assertEqual(industry_default_mood("childcare"), "friendly")


class ChildcareTemplateTest(unittest.TestCase):
    def test_template_registered(self):
        tpl = get_template("childcare")
        self.assertEqual(tpl.industry, "childcare")

    def test_suggested_pages_cover_trust_builders(self):
        tpl = get_template("childcare")
        slugs = {p.slug for p in tpl.suggested_pages}
        self.assertIn("programs", slugs)  # programs by age
        self.assertIn("teachers", slugs)  # meet our teachers
        self.assertIn("gallery", slugs)   # authentic daily moments
        self.assertIn("faq", slugs)       # admissions / fees / safety

    def test_homepage_includes_gallery(self):
        home = get_template("childcare").core_pages[0]
        self.assertTrue(home.is_homepage)
        self.assertIn("gallery", home.sections)


class ChildcareLandingPatternTest(unittest.TestCase):
    def test_default_pattern_is_parent_trust_journey(self):
        sections = homepage_sections("childcare")
        self.assertEqual(sections[0], "hero")
        self.assertEqual(sections[-1], "cta")
        # why-us → philosophy → programs → daily journey → social proof → FAQ
        for kind in ("features", "about", "services", "process", "testimonials", "faq"):
            self.assertIn(kind, sections)


class ChildcareHeroDirectionTest(unittest.TestCase):
    def _pages(self):
        def page(slug, page_type, homepage=False):
            return PagePlan(
                page_type=page_type,
                slug=slug,
                title=slug.title(),
                is_homepage=homepage,
                blocks=[],
                seo_title=slug,
                seo_description=slug,
            )

        return [
            page("home", "home", homepage=True),
            page("programs", "services"),
            page("teachers", "team"),
            page("contact", "contact"),
        ]

    def test_homepage_leads_with_authentic_photography(self):
        directives = plan_site_heroes(
            self._pages(),
            mood="friendly",
            industry="childcare",
            has_source_background=False,
            seed="Sunny Days Kindergarten",
        )
        self.assertEqual(directives["home"].template_id, "hero-background-bold")
        self.assertEqual(directives["home"].layout, "background")

    def test_transactional_pages_stay_calm_and_centered(self):
        directives = plan_site_heroes(
            self._pages(),
            mood="friendly",
            industry="childcare",
            has_source_background=False,
            seed="Sunny Days Kindergarten",
        )
        self.assertEqual(directives["contact"].template_id, "hero-centered-minimal")


class ChildcareThemeTest(unittest.TestCase):
    def test_curated_palette_stays_soft_and_light(self):
        theme = build_theme(
            None,
            mood="friendly",
            palette_mode="auto",  # no logo colour → curated industry palette
            font_seed="Sunny Days Kindergarten",
            industry="childcare",
        )
        # Light, warm surfaces — the brief bans dark themes.
        self.assertEqual(theme.palette.background.lower(), "#ffffff")
        self.assertIn(
            theme.palette.primary.upper(), {"#0284C7", "#059669", "#8B5CF6"}
        )

    def test_font_pairing_is_rounded_kids_pairing(self):
        theme = build_theme(
            None,
            mood="friendly",
            palette_mode="auto",
            font_seed="Sunny Days Kindergarten",
            industry="childcare",
        )
        # The friendly pool's children/kids-tagged pairings are the rounded ones.
        self.assertTrue(
            any(
                face in theme.typography.heading_font
                for face in ("Fredoka", "Varela Round")
            ),
            theme.typography.heading_font,
        )


class ChildcareImageryTest(unittest.TestCase):
    def test_industry_context_query_registered(self):
        self.assertIn("childcare", _INDUSTRY_CONTEXT_QUERIES)
        self.assertIn("children", _INDUSTRY_CONTEXT_QUERIES["childcare"])

    def test_childcare_tokens_prefer_children_over_empty_classrooms(self):
        # The brief bans empty classrooms — kindergarten queries must resolve
        # to candid children-at-play imagery, not the generic school bucket.
        q = _contextual_non_person_query("kindergarten classroom activities")
        self.assertIn("children", q)


if __name__ == "__main__":
    unittest.main()
