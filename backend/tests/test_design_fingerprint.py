"""Design fingerprint (services/design_fingerprint.py) — sameness as one number.

The fingerprint is what the diversity history and the regression harness
(scripts/measure_design_diversity.py) compare, so its properties are pinned
here: a site is identical to itself, a change in what a visitor notices moves
the score, it round-trips through storage, and it reads the BUILT tree rather
than the decisions meant to produce it.
"""

from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace

from app.services.design_fingerprint import (
    DesignFingerprint,
    field_similarity,
    fingerprint_of,
    hue_bucket,
    nearest,
    similarity,
)

BASE = DesignFingerprint(
    hero="hero-background-bold",
    header="classic",
    footer="mega",
    scheme="soft-warm",
    hue=6,
    canvas="white",
    heading_font="Fredoka",
    radius="round",
    sections=("features-card-grid", "about-image-split", "cta-banner"),
    bands=("tint", "tint", "light"),
    divider="wave",
    title_inks=3,
)


class SimilarityTest(unittest.TestCase):
    def test_a_design_is_identical_to_itself(self):
        self.assertEqual(similarity(BASE, BASE), 1.0)

    def test_score_is_symmetric_and_bounded(self):
        other = replace(BASE, hero="hero-editorial", heading_font="Fraunces", hue=1)
        self.assertEqual(similarity(BASE, other), similarity(other, BASE))
        self.assertGreaterEqual(similarity(BASE, other), 0.0)
        self.assertLess(similarity(BASE, other), 1.0)

    def test_every_noticeable_change_lowers_the_score(self):
        for change in (
            {"hero": "hero-editorial"},
            {"heading_font": "Fraunces"},
            {"hue": 0},
            {"sections": ("about-story", "cta-minimal")},
            {"bands": ("dark", "light", "photo")},
            {"scheme": "swiss-quiet"},
        ):
            with self.subTest(change=change):
                self.assertLess(similarity(BASE, replace(BASE, **change)), 1.0)

    def test_neighbouring_hues_count_half(self):
        self.assertEqual(field_similarity(BASE, replace(BASE, hue=7))["hue"], 0.5)
        self.assertEqual(field_similarity(BASE, replace(BASE, hue=5))["hue"], 0.5)
        self.assertEqual(field_similarity(BASE, replace(BASE, hue=0))["hue"], 0.0)
        # The wheel wraps: bucket 11 sits next to bucket 0.
        wrapped = field_similarity(replace(BASE, hue=11), replace(BASE, hue=0))
        self.assertEqual(wrapped["hue"], 0.5)

    def test_a_grey_brand_only_matches_another_grey_brand(self):
        grey = replace(BASE, hue=-1)
        self.assertEqual(field_similarity(grey, grey)["hue"], 1.0)
        self.assertEqual(field_similarity(grey, BASE)["hue"], 0.0)

    def test_section_order_matters_but_shared_sections_still_count(self):
        reordered = replace(BASE, sections=tuple(reversed(BASE.sections)))
        score = field_similarity(BASE, reordered)["sections"]
        self.assertGreater(score, 0.0)
        self.assertLess(score, 1.0)


class StorageTest(unittest.TestCase):
    def test_round_trips_through_json(self):
        self.assertEqual(DesignFingerprint.from_json(BASE.to_json()), BASE)

    def test_unreadable_or_foreign_payloads_are_none_not_errors(self):
        for raw in ("", "not json", "[1, 2]", None):
            with self.subTest(raw=raw):
                self.assertIsNone(DesignFingerprint.from_json(raw))

    def test_unknown_keys_are_ignored_and_missing_ones_default(self):
        parsed = DesignFingerprint.from_json('{"hero": "hero-minimal", "future_axis": 4}')
        self.assertEqual(parsed, DesignFingerprint(hero="hero-minimal"))


class HueBucketTest(unittest.TestCase):
    def test_greys_and_near_black_have_no_hue(self):
        for neutral in ("#111827", "#ffffff", "#808080", "#000000", "not-a-colour", None):
            with self.subTest(neutral=neutral):
                self.assertEqual(hue_bucket(neutral), -1)

    def test_saturated_colours_land_in_their_bucket(self):
        self.assertEqual(hue_bucket("#ff0000"), 0)
        self.assertEqual(hue_bucket("#00ff00"), 4)
        self.assertEqual(hue_bucket("#0000ff"), 8)


class BuiltSiteTest(unittest.TestCase):
    """Read off a real plan_to_site build — the tree that ships."""

    def test_reads_the_homepage_the_generator_built(self):
        from scripts.measure_design_diversity import (
            fixture_brand,
            fixture_plan,
            offline_generation,
        )
        from app.services.schema_builder import plan_to_site

        async def build():
            return await plan_to_site(
                fixture_plan("Quarry House", "restaurant"),
                brand=fixture_brand("Quarry House", "#b91c1c", "restaurant"),
            )

        with offline_generation(schemes=False, history=False):
            site = asyncio.run(build())
        fp = fingerprint_of(site)

        self.assertTrue(fp.hero.startswith("hero-"))
        self.assertTrue(fp.sections)
        self.assertNotIn(fp.hero, fp.sections)
        self.assertEqual(len(fp.bands), len(fp.sections))
        self.assertEqual(fp.header, site.design_manifest["header_archetype"])
        self.assertEqual(fp.heading_font, site.theme.typography.heading_font.split(",")[0].strip('"'))
        # Serialized round-trip (the frontend posts the site back as JSON).
        from app.models.builder_schema import GeneratedSite

        posted = GeneratedSite.model_validate(site.model_dump(mode="json"))
        self.assertEqual(fingerprint_of(posted), fp)


if __name__ == "__main__":
    unittest.main()


class NearestTest(unittest.TestCase):
    def test_nearest_finds_the_closest_and_handles_empty(self):
        close = replace(BASE, heading_font="Fraunces")
        far = replace(BASE, hero="hero-minimal", heading_font="Inter", hue=1, scheme="swiss-quiet")
        score, match = nearest(BASE, [far, close])
        self.assertIs(match, close)
        self.assertEqual(nearest(BASE, []), (0.0, None))

    def test_a_restricted_comparison_ignores_everything_else(self):
        from app.services.design_fingerprint import THEME_FIELDS

        other_page = replace(BASE, hero="hero-minimal", sections=(), bands=(), divider="none")
        self.assertEqual(similarity(BASE, other_page, THEME_FIELDS), 1.0)
        self.assertLess(similarity(BASE, other_page), 1.0)


class HistoryTest(unittest.TestCase):
    """End to end through an isolated history DB (the harness's own)."""

    def _build(self, name: str, logo: str):
        from scripts.measure_design_diversity import fixture_brand, fixture_plan
        from app.services.schema_builder import plan_to_site

        return asyncio.run(plan_to_site(fixture_plan(name, "childcare"), brand=fixture_brand(name, logo, "childcare")))

    def test_the_second_site_reports_its_distinctiveness_without_naming_the_first(self):
        from scripts.measure_design_diversity import offline_generation

        with offline_generation(schemes=True, history=True):
            first = self._build("Sunbeam House", "#f59e0b")
            second = self._build("Rainbow Yard", "#f59e0b")
        self.assertIsNone(
            next((d for d in first.design_manifest["decisions"] if d["area"] == "distinctiveness"), None)
        )
        decision = next(d for d in second.design_manifest["decisions"] if d["area"] == "distinctiveness")
        self.assertTrue(0.0 <= float(decision["choice"]) <= 1.0)
        self.assertNotIn("Sunbeam", decision["rationale"])
        # The builder sees the same manifest the site carries.
        self.assertEqual(second.builder_styles["designManifest"], second.design_manifest)

    def test_a_concept_that_would_repeat_a_recent_site_is_scored_stale(self):
        from unittest import mock

        from app.services import design_direction
        from app.services.design_fingerprint import fingerprint_of_theme
        from app.services.theme import build_theme

        settings_on = mock.patch.object(design_direction.settings, "design_schemes_enabled", True)
        with settings_on:
            themes = {
                slug: build_theme(None, mood="friendly", font_seed="X", industry="childcare", scheme_choice=slug)
                for slug in ("playroom", "nature-studio")
            }
        stored = [fingerprint_of_theme(themes["playroom"]).to_json()]

        async def fake_records(area, *, window):
            return stored

        with mock.patch.object(design_direction, "recent_records", fake_records):
            freshness = asyncio.run(design_direction._freshness(themes, seed="X"))
        self.assertEqual(freshness["playroom"], 0.0)
        self.assertGreater(freshness["nature-studio"], 0.0)
