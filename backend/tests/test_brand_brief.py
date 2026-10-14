"""The brand brief (services/brand_brief.py) — facts, inferences and unknowns
kept apart, and a mood read from the business rather than its industry."""

from __future__ import annotations

import asyncio
import json
import unittest

from app.models.brand import AxisReading, BrandPersonality
from app.models.content_blocks import SourceContent
from app.services.brand_brief import MIN_AXES_FOR_MOOD, ground_brief, mood_from_personality
from app.services.planner import DetectedBrand, _build_scaffolded_user_prompt, detect_brand

SOURCE = (
    "Little Acorns Montessori has taught children in Shah Alam since 1998. "
    "Our Forest Friday programme takes every class outdoors once a week. "
    "Mixed-age classrooms, unhurried mornings and hands-on materials. "
    "Our teachers hold AMI diplomas."
)


def _personality(**values: int | None) -> BrandPersonality:
    return BrandPersonality(**{axis: AxisReading(value=v) for axis, v in values.items()})


class DifferentiatorTest(unittest.TestCase):
    def _ground(self, *claims: str) -> list[str]:
        brief = DetectedBrand(site_name="Little Acorns", differentiators=list(claims))
        return ground_brief(brief, SOURCE).differentiators

    def test_verbatim_source_claims_survive(self):
        kept = self._ground("Our Forest Friday programme takes every class outdoors once a week")
        self.assertEqual(len(kept), 1)

    def test_an_invented_claim_is_dropped(self):
        self.assertEqual(self._ground("Award-winning bilingual STEM curriculum"), [])

    def test_a_changed_number_is_dropped(self):
        # Fuzzy grounding alone would accept this: everything but the year matches.
        self.assertEqual(self._ground("has taught children in Shah Alam since 1988"), [])
        self.assertEqual(len(self._ground("has taught children in Shah Alam since 1998")), 1)

    def test_no_source_means_nothing_is_a_fact(self):
        brief = DetectedBrand(site_name="x", differentiators=["Forest Friday programme"])
        self.assertEqual(ground_brief(brief, None).differentiators, [])

    def test_duplicates_collapse_and_the_list_is_capped(self):
        claim = "Mixed-age classrooms, unhurried mornings and hands-on materials"
        kept = self._ground(claim, claim, *([claim] * 8))
        self.assertEqual(kept, [claim])


class EvidenceTest(unittest.TestCase):
    def test_real_evidence_is_kept_and_fake_evidence_cleared(self):
        brief = DetectedBrand(
            site_name="x",
            personality=BrandPersonality(
                calm_energetic=AxisReading(value=-2, evidence="unhurried mornings"),
                minimal_expressive=AxisReading(value=2, evidence="a riot of colour and noise"),
            ),
        )
        grounded = ground_brief(brief, SOURCE).personality
        self.assertTrue(grounded.calm_energetic.evidenced)
        # The value survives as an INFERENCE; the invented quote does not.
        self.assertEqual(grounded.minimal_expressive.value, 2)
        self.assertIsNone(grounded.minimal_expressive.evidence)
        self.assertFalse(grounded.minimal_expressive.evidenced)

    def test_unknown_axes_stay_unknown(self):
        brief = DetectedBrand(site_name="x", personality=_personality(calm_energetic=-1))
        known = ground_brief(brief, SOURCE).personality.known()
        self.assertEqual(known, {"calm_energetic": -1})

    def test_a_reading_off_the_scale_is_healed_not_fatal(self):
        self.assertEqual(AxisReading(value="1.6").value, 2)
        self.assertEqual(AxisReading(value=9).value, 2)
        self.assertIsNone(AxisReading(value="very").value)
        self.assertIsNone(AxisReading(value=True).value)


class MoodFromPersonalityTest(unittest.TestCase):
    def test_too_little_evidence_derives_nothing(self):
        two = _personality(calm_energetic=-2, premium_accessible=2)
        self.assertLess(len(two.known()), MIN_AXES_FOR_MOOD)
        self.assertIsNone(mood_from_personality(two))

    def test_each_prototype_is_its_own_nearest_mood(self):
        from app.services.brand_brief import _MOOD_PROTOTYPES

        for mood, prototype in _MOOD_PROTOTYPES.items():
            self.assertEqual(mood_from_personality(_personality(**prototype)), mood)

    def test_a_calm_sophisticated_school_is_not_read_as_friendly(self):
        """The case the industry lock made impossible: a Montessori whose own
        content is quiet, sophisticated and premium."""
        montessori = _personality(
            playful_sophisticated=2, calm_energetic=-2, minimal_expressive=-1,
            premium_accessible=-1,
        )
        self.assertIn(mood_from_personality(montessori), ("editorial", "luxury"))

    def test_a_derived_mood_outranks_the_detected_one_and_says_so(self):
        brief = DetectedBrand(
            site_name="x", brand_mood="friendly", industry_category="childcare",
            personality=_personality(
                playful_sophisticated=-2, minimal_expressive=2, calm_energetic=2,
            ),
        )
        self.assertEqual(brief.mood_basis, "detected")
        grounded = ground_brief(brief, SOURCE)
        self.assertEqual(grounded.brand_mood, "playful")
        self.assertEqual(grounded.mood_basis, "personality")

    def test_the_basis_records_an_industry_default(self):
        brief = DetectedBrand(site_name="x", industry_category="childcare")
        self.assertEqual((brief.brand_mood, brief.mood_basis), ("friendly", "industry"))


class DetectionCallTest(unittest.TestCase):
    """One call: the brief rides the existing detection round-trip."""

    def test_detect_brand_grounds_what_the_model_returns(self):
        calls: list[str] = []

        class FakeLLM:
            async def chat_json(self, *, system_prompt, user_prompt, schema, **_):
                calls.append(system_prompt)
                return schema.model_validate({
                    "site_name": "Little Acorns",
                    "brand_mood": "friendly",
                    "industry_category": "childcare",
                    "differentiators": ["Forest Friday programme", "Ofsted Outstanding"],
                    "personality": {
                        "playful_sophisticated": {"value": 1, "evidence": "hands-on materials"},
                        "calm_energetic": {"value": -2, "evidence": "unhurried mornings"},
                        "organic_structured": {"value": -1, "evidence": "takes every class outdoors"},
                    },
                })

        source = SourceContent(source_kind="url", source_ref="https://x", raw_text=SOURCE)
        brief = asyncio.run(detect_brand(source, llm=FakeLLM()))
        self.assertEqual(len(calls), 1)
        self.assertEqual(brief.differentiators, ["Forest Friday programme"])
        self.assertEqual(brief.mood_basis, "personality")
        self.assertNotIn("mood_basis", calls[0])

    def test_the_prompt_no_longer_maps_industries_to_moods(self):
        from app.services.prompts import DETECT_BRAND_PROMPT

        self.assertNotIn("childcare, education", DETECT_BRAND_PROMPT)
        self.assertIn("NOT implied by its industry", DETECT_BRAND_PROMPT)


class CopyPayloadTest(unittest.TestCase):
    def test_the_copywriter_sees_facts_and_gaps_not_inferences(self):
        brief = DetectedBrand(
            site_name="Little Acorns",
            industry_category="childcare",
            audience="anxious first-time parents",
            positioning="the calm alternative",
            differentiators=["Forest Friday programme"],
            unknowns=["fees"],
            personality=_personality(calm_energetic=-2),
        )
        source = SourceContent(source_kind="url", source_ref="https://x", raw_text=SOURCE)
        payload = json.loads(_build_scaffolded_user_prompt(source, brief, []))["brand"]
        self.assertEqual(payload["differentiators"], ["Forest Friday programme"])
        self.assertEqual(payload["unknowns"], ["fees"])
        for inference in ("audience", "positioning", "personality", "mood_basis"):
            self.assertNotIn(inference, payload)


if __name__ == "__main__":
    unittest.main()
