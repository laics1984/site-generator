"""Industry-derived brand mood: fills in when the LLM gives no usable mood."""

import unittest

from app.models.content_blocks import (
    INDUSTRY_MOOD,
    SitePlan,
    heal_brand_mood_value,
    industry_default_mood,
)
from app.services.planner import DetectedBrand, ScaffoldedSitePlan


class IndustryMoodMapTest(unittest.TestCase):
    def test_mapping_values_are_valid_moods(self):
        valid = {"modern", "luxury", "friendly", "technical", "editorial", "playful"}
        self.assertTrue(set(INDUSTRY_MOOD.values()).issubset(valid))

    def test_industry_default_mood(self):
        self.assertEqual(industry_default_mood("restaurant"), "friendly")
        self.assertEqual(industry_default_mood("agency"), "editorial")
        self.assertEqual(industry_default_mood("saas"), "modern")
        self.assertEqual(industry_default_mood("consultancy"), "technical")
        self.assertEqual(industry_default_mood("personal"), "editorial")
        # unknown / None → modern
        self.assertEqual(industry_default_mood("quantum-widgets"), "modern")
        self.assertEqual(industry_default_mood(None), "modern")


class HealMoodTest(unittest.TestCase):
    def test_valid_mood_kept_invalid_becomes_none(self):
        self.assertEqual(heal_brand_mood_value("luxury"), "luxury")
        self.assertIsNone(heal_brand_mood_value("bold"))
        self.assertIsNone(heal_brand_mood_value(""))
        self.assertIsNone(heal_brand_mood_value(None))


class SitePlanMoodTest(unittest.TestCase):
    def test_absent_mood_filled_from_industry(self):
        plan = SitePlan(site_name="X", industry_category="restaurant", pages=[])
        self.assertEqual(plan.brand_mood, "friendly")

    def test_invalid_mood_falls_back_to_industry(self):
        plan = SitePlan(
            site_name="X", industry_category="consultancy", brand_mood="zazzy", pages=[]
        )
        self.assertEqual(plan.brand_mood, "technical")

    def test_explicit_valid_mood_is_preserved(self):
        plan = SitePlan(
            site_name="X", industry_category="restaurant", brand_mood="luxury", pages=[]
        )
        self.assertEqual(plan.brand_mood, "luxury")

    def test_other_industry_defaults_to_modern(self):
        plan = SitePlan(site_name="X", pages=[])  # industry defaults to "other"
        self.assertEqual(plan.brand_mood, "modern")


class PlannerModelMoodTest(unittest.TestCase):
    def test_detected_brand_fills_from_industry(self):
        self.assertEqual(
            DetectedBrand(site_name="X", industry_category="nonprofit").brand_mood,
            "friendly",
        )

    def test_scaffolded_plan_fills_from_industry(self):
        plan = ScaffoldedSitePlan(site_name="X", industry_category="agency", pages=[])
        self.assertEqual(plan.brand_mood, "editorial")

    def test_explicit_mood_preserved_in_detected_brand(self):
        self.assertEqual(
            DetectedBrand(
                site_name="X", industry_category="saas", brand_mood="playful"
            ).brand_mood,
            "playful",
        )


if __name__ == "__main__":
    unittest.main()
