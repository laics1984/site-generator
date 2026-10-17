"""The design direction (services/design_direction.py): concepts scored on
evidence, at most one model pick by index, every failure a deterministic one."""

from __future__ import annotations

import asyncio
import unittest

from app.config import settings
from app.models.brand import AxisReading, BrandPersonality
from app.services import design_schemes
from app.services.design_direction import (
    ContentProfile,
    brand_alignment,
    content_fit,
    resolve_direction,
    score_schemes,
)
from app.services.diversity import seeded_index
from app.services.llm import LlmError


def _personality(evidenced: bool = True, **values: int) -> BrandPersonality:
    return BrandPersonality(**{
        axis: AxisReading(value=v, evidence="quoted" if evidenced else None)
        for axis, v in values.items()
    })


# Two kindergartens whose OWN content reads very differently.
CALM_NATURAL = _personality(
    playful_sophisticated=1, calm_energetic=-2, organic_structured=-2,
    minimal_expressive=-1, traditional_contemporary=-1,
)
LOUD_PLAYFUL = _personality(
    playful_sophisticated=-2, calm_energetic=2, minimal_expressive=2,
    premium_accessible=2,
)


class _SchemesOn(unittest.TestCase):
    def setUp(self):
        self._saved = (settings.design_schemes_enabled, settings.design_language_enabled,
                       settings.design_concept_candidates)
        settings.design_schemes_enabled = True
        settings.design_language_enabled = True
        settings.design_concept_candidates = 3

    def tearDown(self):
        (settings.design_schemes_enabled, settings.design_language_enabled,
         settings.design_concept_candidates) = self._saved


class AlignmentTest(unittest.TestCase):
    def test_too_little_known_is_no_score(self):
        scheme = design_schemes.by_slug("playroom")
        self.assertIsNone(brand_alignment(scheme, None))
        self.assertIsNone(brand_alignment(scheme, _personality(calm_energetic=2)))

    def test_a_brand_on_the_target_scores_one(self):
        scheme = design_schemes.by_slug("playroom")
        self.assertEqual(brand_alignment(scheme, _personality(**scheme.personality)), 1.0)

    def test_an_evidenced_axis_outweighs_an_inferred_one(self):
        scheme = design_schemes.by_slug("playroom")  # calm_energetic +2, minimal_expressive +2
        evidenced_miss = BrandPersonality(
            calm_energetic=AxisReading(value=-2, evidence="quiet mornings"),
            minimal_expressive=AxisReading(value=2),
        )
        inferred_miss = BrandPersonality(
            calm_energetic=AxisReading(value=-2),
            minimal_expressive=AxisReading(value=2, evidence="bursting with colour"),
        )
        self.assertLess(
            brand_alignment(scheme, evidenced_miss), brand_alignment(scheme, inferred_miss)
        )


class ContentFitTest(unittest.TestCase):
    def test_a_photo_led_language_without_photographs_is_held_back(self):
        gallery = design_schemes.by_slug("gallery-first")
        self.assertLess(content_fit(gallery, ContentProfile(photos=0)), 1.0)
        self.assertEqual(content_fit(gallery, ContentProfile(photos=12, prose_chars=5000)), 1.0)

    def test_a_narrative_language_needs_a_story(self):
        story = design_schemes.by_slug("storybook-editorial")
        self.assertLess(content_fit(story, ContentProfile(photos=3, prose_chars=300)), 1.0)
        self.assertEqual(content_fit(story, ContentProfile(photos=3, prose_chars=4000)), 1.0)

    def test_unknown_content_demotes_nothing(self):
        for scheme in design_schemes.DESIGN_SCHEMES:
            self.assertEqual(content_fit(scheme, None), 1.0)


class ScoringTest(unittest.TestCase):
    CELL = design_schemes.candidates_for("friendly", "childcare")

    def test_with_nothing_known_the_brand_seeded_rotation_decides(self):
        """No evidence and no history: every score ties and the order is the
        fit list rotated by the brand seed — so one brand regenerates
        identically and two brands still diverge."""
        order = [s.slug for s, *_ in score_schemes(
            self.CELL, seed="Tadika Murni", personality=None, content=None, freshness={},
        )]
        offset = seeded_index("Tadika Murni", "scheme", len(self.CELL))
        self.assertEqual(order, self.CELL[offset:] + self.CELL[:offset])

    def test_a_stale_scheme_drops(self):
        top = score_schemes(
            self.CELL, seed="Tadika Murni", personality=None, content=None, freshness={},
        )[0][0].slug
        again = score_schemes(
            self.CELL, seed="Tadika Murni", personality=None, content=None, freshness={top: 0.0},
        )
        self.assertNotEqual(again[0][0].slug, top)
        self.assertEqual(again[-1][0].slug, top)

    def test_personality_sends_two_kindergartens_to_different_languages(self):
        def top(personality):
            return score_schemes(
                self.CELL, seed="Same Name", personality=personality,
                content=None, freshness={},
            )[0][0].slug

        calm, loud = top(CALM_NATURAL), top(LOUD_PLAYFUL)
        self.assertNotEqual(calm, loud)
        self.assertIn(calm, ("nature-studio", "storybook-editorial", "calm-clinical", "soft-warm"))
        self.assertIn(loud, ("playroom", "bold-block"))


class _FakeLLM:
    def __init__(self, reply=None, error=None):
        self.calls: list[str] = []
        self._reply, self._error = reply, error

    async def chat_json(self, *, system_prompt, user_prompt, schema, **_):
        self.calls.append(user_prompt)
        if self._error:
            raise self._error
        return schema(**(self._reply or {}))


def _direction(llm=None, **overrides):
    kwargs = dict(
        brand_name="Little Acorns", mood="friendly", industry="childcare",
        logo_hex="#e11d48", personality=CALM_NATURAL, llm=llm, generate_palette=False,
    )
    kwargs.update(overrides)
    return asyncio.run(resolve_direction(**kwargs))


class ResolveDirectionTest(_SchemesOn):
    def test_the_model_picks_by_index_among_built_concepts(self):
        llm = _FakeLLM({"concept": 2, "hero_height": "banded"})
        direction = _direction(llm)
        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(direction.picked_by, "model")
        self.assertIs(direction.theme, direction.concepts[2].theme)
        self.assertEqual(direction.theme.design_scheme, direction.concepts[2].scheme.slug)
        self.assertEqual(direction.theme.hero_background_height, "banded")
        # The prompt shows complete concepts, not a palette menu.
        for concept in direction.concepts:
            self.assertIn(concept.scheme.label, llm.calls[0])
        self.assertIn("Weighed:", direction.theme.design_rationale)

    def test_an_out_of_range_index_keeps_the_best_score(self):
        direction = _direction(_FakeLLM({"concept": 9}))
        self.assertEqual(direction.picked_by, "score")
        self.assertIs(direction.theme, direction.concepts[0].theme)

    def test_a_failed_call_keeps_the_best_score(self):
        direction = _direction(_FakeLLM(error=LlmError("down")))
        self.assertEqual(direction.picked_by, "score")
        self.assertIs(direction.theme, direction.concepts[0].theme)

    def test_one_candidate_or_no_model_makes_no_call(self):
        llm = _FakeLLM({"concept": 1})
        settings.design_concept_candidates = 1
        self.assertEqual(_direction(llm).picked_by, "score")
        settings.design_concept_candidates = 3
        self.assertEqual(_direction(llm, use_model=False).picked_by, "score")
        settings.design_language_enabled = False
        self.assertEqual(_direction(llm).picked_by, "score")
        self.assertEqual(llm.calls, [])

    def test_the_users_hero_height_beats_the_models(self):
        direction = _direction(_FakeLLM({"concept": 0, "hero_height": "banded"}), hero_height="full")
        self.assertEqual(direction.theme.hero_background_height, "full")

    def test_a_pinned_scheme_is_the_only_concept(self):
        llm = _FakeLLM({"concept": 0})
        direction = _direction(llm, scheme_choice="nature-studio")
        self.assertEqual(direction.picked_by, "pin")
        self.assertEqual(direction.theme.design_scheme, "nature-studio")
        self.assertEqual(llm.calls, [])

    def test_schemes_off_is_deterministic_and_makes_no_call(self):
        settings.design_schemes_enabled = False
        llm = _FakeLLM({"concept": 1})
        direction = _direction(llm)
        self.assertEqual(direction.picked_by, "schemes-off")
        self.assertIsNone(direction.theme.design_scheme)
        self.assertEqual(llm.calls, [])

    def test_the_logo_still_leads_every_concept(self):
        direction = _direction(_FakeLLM({"concept": 0}))
        primaries = {c.theme.palette.primary for c in direction.concepts}
        self.assertEqual(len(primaries), 1)  # same logo, same palette, different languages
        self.assertEqual(len({c.scheme.slug for c in direction.concepts}), len(direction.concepts))


class OneHomeTest(_SchemesOn):
    """plan_to_site's no-theme path is the same function, minus the model."""

    def test_plan_to_site_builds_the_best_scored_concept(self):
        from scripts.measure_design_diversity import fixture_brand, fixture_plan, offline_generation
        from app.services.schema_builder import plan_to_site

        with offline_generation(schemes=True, history=False):
            site = asyncio.run(plan_to_site(
                fixture_plan("Kiddyland Lane", "childcare"),
                brand=fixture_brand("Kiddyland Lane", "#0f766e", "childcare"),
            ))
            expected = _direction(
                None, brand_name="Kiddyland Lane", logo_hex="#0f766e",
                personality=None, use_model=False,
            )
        self.assertEqual(site.theme.design_scheme, expected.theme.design_scheme)
        self.assertEqual(site.theme.typography, expected.theme.typography)
        self.assertEqual(site.theme.palette, expected.theme.palette)


if __name__ == "__main__":
    unittest.main()
