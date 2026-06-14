"""Tests for the frontend-design-authored asymmetric display hero variant."""

import unittest

from app.models.builder_schema import BuilderElement
from app.services.section_content import (
    get_template,
    mood_preferred_ids,
    templates_for_type,
)

HERO_ID = "hero-asymmetric-display"


def _walk(node):
    yield node
    content = node.get("content") if isinstance(node, dict) else None
    if isinstance(content, list):
        for ch in content:
            if isinstance(ch, dict):
                yield from _walk(ch)


class AsymmetricHeroCatalogTest(unittest.TestCase):
    def setUp(self):
        self.entry = get_template(HERO_ID)

    def test_present_and_well_formed(self):
        self.assertIsNotNone(self.entry, "new hero missing from catalog")
        for key in ("sectionType", "layoutVariant", "styleVariant", "label",
                    "slots", "sampleContent", "tree"):
            self.assertIn(key, self.entry)
        self.assertEqual(self.entry["sectionType"], "hero")
        self.assertEqual(self.entry["layoutVariant"], "asymmetric")

    def test_tree_parses_as_builder_element(self):
        el = BuilderElement.model_validate(self.entry["tree"])
        self.assertEqual(el.type, "container")

    def test_slots_match_sample_content(self):
        slot_ids = {s["id"] for s in self.entry["slots"]}
        self.assertEqual(slot_ids, set(self.entry["sampleContent"].keys()))

    def test_text_led_no_required_image(self):
        # No image slot at all → feasible even when a site has no hero image.
        self.assertFalse(any(s["id"] == "image" for s in self.entry["slots"]))

    def test_is_theme_driven_and_dark_safe(self):
        # Every text/background colour is a theme CSS var — no hardcoded hex/rgba
        # that would break on a dark palette (the older heroes hardcode dark rgba).
        for node in _walk(self.entry["tree"]):
            styles = node.get("styles", {}) if isinstance(node, dict) else {}
            for prop in ("color", "backgroundColor"):
                val = styles.get(prop)
                if val and val != "transparent":
                    self.assertTrue(
                        val.startswith("var("),
                        f"{node.get('name')}.{prop} = {val!r} is not a theme var",
                    )


class AsymmetricHeroSelectionTest(unittest.TestCase):
    def test_in_hero_pool(self):
        ids = {t["id"] for t in templates_for_type("hero")}
        self.assertIn(HERO_ID, ids)

    def test_preferred_for_display_led_moods(self):
        for mood in ("editorial", "luxury"):
            self.assertIn(HERO_ID, mood_preferred_ids(mood, "hero"), mood)

    def test_ranked_ahead_of_unlisted_variants_for_editorial(self):
        order = mood_preferred_ids("editorial", "hero")
        # "asymmetric" is listed for editorial, so it must outrank any hero whose
        # layoutVariant isn't in editorial's preference list.
        unlisted = [
            t["id"] for t in templates_for_type("hero")
            if t.get("layoutVariant") in ("bento", "gradient")
        ]
        for uid in unlisted:
            self.assertLess(order.index(HERO_ID), order.index(uid))


if __name__ == "__main__":
    unittest.main()
