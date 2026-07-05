"""Per-page hero art direction (services/hero_director.py).

The director must give a nonprofit site an immersive full-bleed homepage,
varied-but-coherent interior heroes drawn from the approved set, deterministic
output for the same inputs, and a pinned source background when the source
site led with a CSS background image.
"""

import unittest

from app.models.content_blocks import PagePlan
from app.services.hero_director import (
    IMAGELESS_HERO_IDS,
    HeroDirective,
    plan_site_heroes,
)

_APPROVED_NONPROFIT_IDS = {
    "hero-background-bold",
    "hero-editorial",
    "hero-modern-split",
    "hero-gradient",
    "hero-centered-minimal",
}


def _page(slug, page_type="landing", *, homepage=False):
    return PagePlan(
        page_type=page_type,
        slug=slug,
        title=slug.title(),
        is_homepage=homepage,
        blocks=[],
        seo_title=slug,
        seo_description=slug,
    )


def _nonprofit_site():
    return [
        _page("home", "home", homepage=True),
        _page("about", "about"),
        _page("programs", "services"),
        _page("contact", "contact"),
        _page("stories", "landing"),
        _page("events", "landing"),
    ]


class NonprofitDirectionTest(unittest.TestCase):
    def _plan(self, *, has_source_background=False, seed="Hope Foundation"):
        return plan_site_heroes(
            _nonprofit_site(),
            mood="friendly",
            industry="nonprofit",
            has_source_background=has_source_background,
            seed=seed,
        )

    def test_homepage_is_immersive_full_bleed(self):
        directives = self._plan()
        home = directives["home"]
        self.assertEqual(home.template_id, "hero-background-bold")
        self.assertEqual(home.layout, "background")
        self.assertFalse(home.pin_source_background)

    def test_every_page_gets_a_directive_from_the_approved_set(self):
        directives = self._plan()
        self.assertEqual(set(directives), {p.slug for p in _nonprofit_site()})
        for d in directives.values():
            self.assertIn(d.template_id, _APPROVED_NONPROFIT_IDS)

    def test_interior_pages_are_not_all_the_same_template(self):
        directives = self._plan()
        interior_ids = [d.template_id for slug, d in directives.items() if slug != "home"]
        self.assertGreaterEqual(len(set(interior_ids)), 2)

    def test_split_directives_always_want_the_abstract_wash(self):
        directives = self._plan()
        for d in directives.values():
            if d.template_id == "hero-modern-split":
                self.assertTrue(d.wants_wash)

    def test_deterministic_for_same_inputs(self):
        self.assertEqual(self._plan(), self._plan())

    def test_source_background_pins_the_homepage_only(self):
        directives = self._plan(has_source_background=True)
        home = directives["home"]
        self.assertEqual(home.template_id, "hero-background-bold")
        self.assertTrue(home.pin_source_background)
        for slug, d in directives.items():
            if slug != "home":
                self.assertFalse(d.pin_source_background)


class MoodFallbackTest(unittest.TestCase):
    def test_unknown_industry_falls_back_to_mood_spec(self):
        directives = plan_site_heroes(
            _nonprofit_site(),
            mood="modern",
            industry="saas",
            has_source_background=False,
            seed="Acme",
        )
        # Modern keeps its current split-washed homepage lead.
        home = directives["home"]
        self.assertEqual(home.template_id, "hero-modern-split")
        self.assertTrue(home.wants_wash)

    def test_no_mood_no_industry_still_directs_every_page(self):
        directives = plan_site_heroes(
            _nonprofit_site(),
            mood=None,
            industry=None,
            has_source_background=False,
            seed="Acme",
        )
        self.assertEqual(len(directives), len(_nonprofit_site()))

    def test_no_interior_rotation_contains_a_full_bleed_hero(self):
        # Full-bleed interiors push content below the fold (scroll-cue CTA
        # policy); rotations must stay compact for every mood.
        for mood in ("modern", "luxury", "friendly", "technical", "editorial", "playful"):
            pages = [_page("home", "home", homepage=True)] + [
                _page(f"p{i}") for i in range(6)
            ]
            directives = plan_site_heroes(
                pages, mood=mood, industry=None,
                has_source_background=False, seed="Acme",
            )
            for slug, d in directives.items():
                if slug != "home":
                    self.assertNotEqual(d.template_id, "hero-background-bold")


class DirectiveShapeTest(unittest.TestCase):
    def test_imageless_ids_never_pin_or_wash(self):
        directives = plan_site_heroes(
            _nonprofit_site(), mood="friendly", industry="nonprofit",
            has_source_background=True, seed="Hope",
        )
        for d in directives.values():
            if d.template_id in IMAGELESS_HERO_IDS:
                self.assertFalse(d.wants_wash)
                self.assertFalse(d.pin_source_background)

    def test_directive_is_hashable_and_frozen(self):
        d = HeroDirective("hero-editorial", "split")
        with self.assertRaises(Exception):
            d.template_id = "x"  # type: ignore[misc]
        self.assertIn(d, {d})


if __name__ == "__main__":
    unittest.main()
