"""Per-page hero art direction (services/hero_director.py).

Default policy (hero_fullbleed_all_pages=True): EVERY page opens with a
full-bleed background hero (photo → stock → abstract) so the transparent
floating header engages site-wide. The legacy per-mood interior rotation is
kept behind the flag and still tested with it off.
"""

import unittest
from unittest import mock

from app.config import settings
from app.models.content_blocks import PagePlan
from app.services.hero_director import (
    IMAGELESS_HERO_IDS,
    HeroComposition,
    HeroDirective,
    hero_composition,
    plan_site_compositions,
    plan_site_heroes,
)


class _LegacyRotationCase(unittest.TestCase):
    """Base for tests of the legacy per-mood rotation (full-bleed policy off)."""

    def setUp(self):
        patcher = mock.patch.object(settings, "hero_fullbleed_all_pages", False)
        patcher.start()
        self.addCleanup(patcher.stop)

_APPROVED_NONPROFIT_IDS = {
    "hero-background-bold",
    "hero-editorial",
    "hero-modern-split",
    "hero-gradient",
    "hero-centered-minimal",
}


def _page(slug, page_type="landing", *, homepage=False, parent_slug=None, menu_hidden=False):
    return PagePlan(
        page_type=page_type,
        slug=slug,
        title=slug.title(),
        is_homepage=homepage,
        blocks=[],
        seo_title=slug,
        seo_description=slug,
        parent_slug=parent_slug,
        menu_hidden=menu_hidden,
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


class NonprofitDirectionTest(_LegacyRotationCase):
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


class MoodFallbackTest(_LegacyRotationCase):
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


class FullBleedEverywhereTest(unittest.TestCase):
    """Default policy: every page's hero is a full-bleed background so the
    transparent floating header engages on every page."""

    def _plan(self, *, has_source_background=False):
        return plan_site_heroes(
            _nonprofit_site(),
            mood="friendly",
            industry="childcare",
            has_source_background=has_source_background,
            seed="GloryKids",
        )

    def test_every_page_is_directed_full_bleed_background(self):
        directives = self._plan()
        self.assertEqual(set(directives), {p.slug for p in _nonprofit_site()})
        for d in directives.values():
            self.assertEqual(d.template_id, "hero-background-bold")
            self.assertEqual(d.layout, "background")

    def test_source_background_still_pins_only_the_homepage(self):
        directives = self._plan(has_source_background=True)
        self.assertTrue(directives["home"].pin_source_background)
        for slug, d in directives.items():
            if slug != "home":
                self.assertFalse(d.pin_source_background)

    def test_deterministic_for_same_inputs(self):
        self.assertEqual(self._plan(), self._plan())


class DirectiveShapeTest(_LegacyRotationCase):
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


class CentredByDefaultTest(unittest.TestCase):
    """Hero copy is centred unless `hero_anchored_copy` is explicitly turned on.

    An anchored column only works when the photograph has a genuinely open side
    to give it; across arbitrary scraped and stock imagery that's the exception,
    so the anchor more often lands copy over a busy half of the frame than
    beside a clean one. Centre is the reliable default.
    """

    def _pages(self):
        return [
            _page("home", "home", homepage=True),
            _page("about", "about"),
            _page("contact", "contact"),
        ]

    def test_the_switch_is_off_by_default(self):
        self.assertFalse(settings.hero_anchored_copy)

    def test_every_page_including_the_homepage_is_centred(self):
        comps = plan_site_compositions(self._pages(), seed="Blue Fin Bistro")
        self.assertEqual({c.anchor for c in comps.values()}, {"center"})

    def test_the_single_page_helper_agrees(self):
        for homepage in (True, False):
            comp = hero_composition(slug="about", seed="s", is_homepage=homepage)
            self.assertEqual(comp.anchor, "center")

    def test_turning_it_on_restores_anchoring(self):
        """The machinery stays intact and reversible — the switch is the whole
        difference, so the scrim and focal crop keep following the anchor."""
        with mock.patch.object(settings, "hero_anchored_copy", True):
            comps = plan_site_compositions(self._pages(), seed="Blue Fin Bistro")
        self.assertEqual(comps["home"].anchor, "left")
        self.assertGreater(len({c.anchor for c in comps.values()}), 1)


class ProfilePageInheritsParentHeroTest(_LegacyRotationCase):
    """A profile page reached from a roster's own link (``menu_hidden`` +
    ``parent_slug``) reads as a continuation of that roster page, not a new
    place — it must share the roster's exact directive/composition, not draw
    an independent rotation pick."""

    def _pages(self):
        return [
            _page("home", "home", homepage=True),
            _page("team", "team"),  # nonprofit by_page_type -> _EDITORIAL
            _page("team/ashley", "landing", parent_slug="team", menu_hidden=True),
            _page("team/dana", "landing", parent_slug="team", menu_hidden=True),
            # Same parent_slug, but NOT reached via the roster's link — must
            # keep its own explicit page-type directive, not inherit team's.
            _page("team/contact", "contact", parent_slug="team", menu_hidden=False),
        ]

    def _directives(self):
        return plan_site_heroes(
            self._pages(), mood="friendly", industry="nonprofit",
            has_source_background=False, seed="Hope Foundation",
        )

    def test_profile_pages_copy_the_parent_roster_directive(self):
        directives = self._directives()
        self.assertEqual(directives["team/ashley"], directives["team"])
        self.assertEqual(directives["team/dana"], directives["team"])

    def test_a_menu_visible_sub_page_keeps_its_own_directive(self):
        directives = self._directives()
        self.assertEqual(directives["team/contact"].template_id, "hero-centered-minimal")
        self.assertNotEqual(directives["team/contact"], directives["team"])

    def test_dangling_parent_slug_falls_back_safely(self):
        pages = self._pages() + [
            _page("orphan", "landing", parent_slug="nonexistent", menu_hidden=True)
        ]
        directives = plan_site_heroes(
            pages, mood="friendly", industry="nonprofit",
            has_source_background=False, seed="Hope Foundation",
        )
        self.assertIn("orphan", directives)

    def test_composition_also_inherits(self):
        with mock.patch.object(settings, "hero_anchored_copy", True):
            comps = plan_site_compositions(self._pages(), seed="Hope Foundation")
        self.assertEqual(comps["team/ashley"], comps["team"])
        self.assertEqual(comps["team/dana"], comps["team"])

    def test_composition_dangling_parent_slug_falls_back_safely(self):
        pages = self._pages() + [
            _page("orphan", "landing", parent_slug="nonexistent", menu_hidden=True)
        ]
        with mock.patch.object(settings, "hero_anchored_copy", True):
            comps = plan_site_compositions(pages, seed="Hope Foundation")
        self.assertIn("orphan", comps)


class HeroCompositionTest(unittest.TestCase):
    """With every page on the same full-bleed template, composition is the only
    axis of variety left — so it has to actually vary, and still be idempotent.

    Covers the opt-in anchored mode (`hero_anchored_copy`); the default centred
    behaviour is CentredByDefaultTest above.
    """

    def setUp(self):
        patcher = mock.patch.object(settings, "hero_anchored_copy", True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _pages(self):
        return [
            _page("home", "home", homepage=True),
            _page("about", "about"),
            _page("services", "services"),
            _page("team", "team"),
            _page("contact", "contact"),
            _page("faq", "faq"),
        ]

    def test_homepage_always_leads_left_anchored(self):
        comps = plan_site_compositions(self._pages(), seed="Blue Fin Bistro")
        self.assertEqual(comps["home"].anchor, "left")

    def test_consecutive_interiors_never_share_an_anchor(self):
        """Per-page seeding alone clusters: three identical compositions in a row
        is the exact convergence composition exists to prevent."""
        for seed in ("Blue Fin Bistro", "Meridian Law", "Sunny Days Kindergarten"):
            anchors = [
                c.anchor
                for slug, c in plan_site_compositions(self._pages(), seed=seed).items()
                if slug != "home"
            ]
            for a, b in zip(anchors, anchors[1:]):
                self.assertNotEqual(a, b, msg=f"{seed}: {anchors}")

    def test_a_site_uses_more_than_one_composition(self):
        for seed in ("Blue Fin Bistro", "Meridian Law", "Sunny Days Kindergarten"):
            comps = plan_site_compositions(self._pages(), seed=seed)
            self.assertGreater(len({c.anchor for c in comps.values()}), 1, msg=seed)

    def test_regeneration_is_idempotent(self):
        # md5-seeded, not hash()-seeded: the pick must survive a restart, or
        # every regeneration silently reshuffles the whole site.
        first = plan_site_compositions(self._pages(), seed="Blue Fin Bistro")
        second = plan_site_compositions(self._pages(), seed="Blue Fin Bistro")
        self.assertEqual(
            {k: v.anchor for k, v in first.items()},
            {k: v.anchor for k, v in second.items()},
        )

    def test_different_brands_compose_differently(self):
        a = plan_site_compositions(self._pages(), seed="Blue Fin Bistro")
        b = plan_site_compositions(self._pages(), seed="Meridian Law")
        self.assertNotEqual(
            [c.anchor for c in a.values()], [c.anchor for c in b.values()]
        )

    def test_banded_heroes_stay_centred_everywhere(self):
        """460px has no vertical room for an anchor to read as composition — a
        bottom-left copy block would just look like it fell out of the band."""
        comps = plan_site_compositions(
            self._pages(), seed="Blue Fin Bistro", hero_height="banded"
        )
        self.assertEqual({c.anchor for c in comps.values()}, {"center"})

    def test_single_page_helper_agrees_with_the_site_planner_on_the_homepage(self):
        solo = hero_composition(slug="home", seed="Blue Fin Bistro", is_homepage=True)
        planned = plan_site_compositions(self._pages(), seed="Blue Fin Bistro")["home"]
        self.assertEqual(solo.anchor, planned.anchor)

    def test_composition_is_frozen(self):
        c = HeroComposition("left")
        with self.assertRaises(Exception):
            c.anchor = "center"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
