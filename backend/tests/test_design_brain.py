"""Design-brain batching: one LLM call for the whole site, with per-page
index namespacing so one page's pick never lands on another page's section."""

import asyncio
import unittest
from typing import get_args

from app.config import settings
from app.models.brand import (
    INDUSTRY_HERO_HEIGHT,
    MOOD_HERO_HEIGHT,
    BrandMood,
)
from app.models.content_blocks import INDUSTRY_MOOD
from app.routers.generate import resolve_hero_height
from app.services.design_brain import (
    DesignLanguage,
    SiteDesignRecipe,
    SiteSectionChoice,
    _page_blurb,
    generate_design_language,
    generate_site_design_recipe,
)


class RecipeSlicingTest(unittest.TestCase):
    def test_recipe_for_slices_by_page_index(self):
        site = SiteDesignRecipe(
            sections=[
                SiteSectionChoice(page_index=0, section_index=0, template_id="hero-bento"),
                SiteSectionChoice(page_index=0, section_index=1, template_id="features-card-grid"),
                SiteSectionChoice(page_index=1, section_index=0, template_id="hero-editorial"),
            ]
        )
        p0 = site.recipe_for(0)
        self.assertEqual(p0.template_for(0), "hero-bento")
        self.assertEqual(p0.template_for(1), "features-card-grid")
        # Page 1's section 0 must NOT leak into page 0's section 0.
        self.assertEqual(site.recipe_for(1).template_for(0), "hero-editorial")
        # An unknown page → empty recipe → deterministic fallback downstream.
        self.assertEqual(site.recipe_for(2).sections, [])


class BatchedCallTest(unittest.TestCase):
    def test_one_call_covers_all_pages_and_namespaces_picks(self):
        calls: list[str] = []

        class FakeLLM:
            async def chat_json(self, *, user_prompt, schema, **_):
                calls.append(user_prompt)
                return schema(
                    sections=[
                        SiteSectionChoice(page_index=0, section_index=1, template_id="cta-banner"),
                        SiteSectionChoice(page_index=1, section_index=1, template_id="cta-minimal"),
                    ]
                )

        original = settings.design_brain_enabled
        settings.design_brain_enabled = True
        try:
            recipe = asyncio.run(
                generate_site_design_recipe(
                    mood="modern",
                    industry="saas",
                    pages=[["hero", "cta"], ["hero", "cta"]],
                    llm=FakeLLM(),
                )
            )
        finally:
            settings.design_brain_enabled = original

        self.assertEqual(len(calls), 1)  # ONE round-trip for the whole site
        self.assertIn("Page 0", calls[0])
        self.assertIn("Page 1", calls[0])
        # Heroes are pre-assigned by the hero director — never offered to the LLM.
        self.assertNotIn("hero", calls[0])
        self.assertEqual(recipe.recipe_for(0).template_for(1), "cta-banner")
        self.assertEqual(recipe.recipe_for(1).template_for(1), "cta-minimal")


class HeroExclusionTest(unittest.TestCase):
    def test_page_blurb_omits_hero_sections(self):
        # A page whose only multi-template section is the hero yields no blurb
        # at all (harmless: empty recipe → deterministic fallback downstream).
        self.assertIsNone(_page_blurb(0, ["hero"]))
        blurb = _page_blurb(0, ["hero", "cta"])
        assert blurb is not None
        self.assertNotIn('"hero"', blurb)
        self.assertIn('"cta"', blurb)


class NoOpTest(unittest.TestCase):
    def test_disabled_returns_empty_without_calling_llm(self):
        class BoomLLM:
            async def chat_json(self, *_, **__):
                raise AssertionError("LLM must not be called when design brain is off")

        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            recipe = asyncio.run(
                generate_site_design_recipe(
                    mood="modern",
                    industry="saas",
                    pages=[["hero", "features"]],
                    llm=BoomLLM(),
                )
            )
        finally:
            settings.design_brain_enabled = original
        self.assertEqual(recipe.sections, [])


class DesignLanguageTest(unittest.TestCase):
    """The design-language pass: offers the curated palette/pairing slugs and
    passes the LLM's picks through; degrades to an empty (deterministic-fallback)
    DesignLanguage when disabled or the call fails."""

    def test_prompt_offers_options_and_picks_pass_through(self):
        calls: list[str] = []

        class FakeLLM:
            async def chat_json(self, *, user_prompt, schema, **_):
                calls.append(user_prompt)
                return schema(palette="ai-platform", font_pairing="space-grotesk-dm-sans")

        original = settings.design_language_enabled
        settings.design_language_enabled = True
        try:
            language = asyncio.run(
                generate_design_language(
                    brand_name="Acme AI",
                    mood="modern",
                    industry="saas",
                    seed_hex="#7c3aed",
                    llm=FakeLLM(),
                )
            )
        finally:
            settings.design_language_enabled = original

        self.assertEqual(len(calls), 1)
        prompt = calls[0]
        # The industry's curated palettes and the mood's pairings are offered by slug.
        self.assertIn('"ai-platform"', prompt)
        self.assertIn('"space-grotesk-dm-sans"', prompt)
        # The brand colour is given so the pick can harmonise with the logo.
        self.assertIn("#7c3aed", prompt)
        self.assertEqual(language.palette, "ai-platform")
        self.assertEqual(language.font_pairing, "space-grotesk-dm-sans")

    def _prompt_for(self, **kwargs) -> str:
        calls: list[str] = []

        class FakeLLM:
            async def chat_json(self, *, user_prompt, schema, **_):
                calls.append(user_prompt)
                return schema()

        original = settings.design_language_enabled
        settings.design_language_enabled = True
        try:
            asyncio.run(generate_design_language(llm=FakeLLM(), **kwargs))
        finally:
            settings.design_language_enabled = original
        return calls[0]

    def test_dark_scheme_offers_only_dark_palettes(self):
        """The scheme is resolved before this pass so the menu can match it.

        Offering light slugs on a dark build would reduce the whole pass to a
        no-op: a light slug simply fails to resolve in a dark theme."""
        prompt = self._prompt_for(
            brand_name="Acme AI",
            mood="modern",
            industry="saas",
            seed_hex="#7c3aed",
            color_scheme="dark",
        )
        self.assertIn("Colour scheme: dark", prompt)
        self.assertIn('"dark-midnight-violet"', prompt)
        # No light-catalogue slug is on the dark menu.
        self.assertNotIn('"ai-platform"', prompt)
        self.assertNotIn('"saas"', prompt)

    def test_light_scheme_is_the_default_and_offers_light_palettes(self):
        prompt = self._prompt_for(
            brand_name="Acme AI", mood="modern", industry="saas", seed_hex="#7c3aed"
        )
        self.assertIn("Colour scheme: light", prompt)
        self.assertIn('"ai-platform"', prompt)
        self.assertNotIn("dark-midnight-violet", prompt)

    def test_prompt_states_each_palette_s_moods(self):
        prompt = self._prompt_for(
            brand_name="Acme AI", mood="technical", industry="saas", seed_hex=None
        )
        # A mood-tagged entry advertises its moods; the legacy wildcards say "any".
        self.assertIn("moods: technical", prompt)
        self.assertIn("moods: any", prompt)

    def test_disabled_returns_empty_without_calling_llm(self):
        class BoomLLM:
            async def chat_json(self, *_, **__):
                raise AssertionError("LLM must not be called when design language is off")

        original = settings.design_language_enabled
        settings.design_language_enabled = False
        try:
            language = asyncio.run(
                generate_design_language(
                    brand_name="Acme",
                    mood="modern",
                    industry="saas",
                    seed_hex=None,
                    llm=BoomLLM(),
                )
            )
        finally:
            settings.design_language_enabled = original
        self.assertEqual(language, DesignLanguage())

    def test_llm_failure_degrades_to_empty(self):
        from app.services.llm import LlmError

        class FailingLLM:
            async def chat_json(self, *_, **__):
                raise LlmError("boom")

        original = settings.design_language_enabled
        settings.design_language_enabled = True
        try:
            language = asyncio.run(
                generate_design_language(
                    brand_name="Acme",
                    mood="modern",
                    industry="saas",
                    seed_hex=None,
                    llm=FailingLLM(),
                )
            )
        finally:
            settings.design_language_enabled = original
        self.assertEqual(language, DesignLanguage())


class HeroHeightDecisionTest(unittest.TestCase):
    """Hero height is a design judgement, not a hardcoded default: the pass may
    pick it, and everything downstream must still be fully determined when it
    doesn't (resolve_hero_height)."""

    def _language(self, llm):
        original = settings.design_language_enabled
        settings.design_language_enabled = True
        try:
            return asyncio.run(
                generate_design_language(
                    brand_name="Blue Fin Bistro",
                    mood="friendly",
                    industry="restaurant",
                    seed_hex=None,
                    llm=llm,
                )
            )
        finally:
            settings.design_language_enabled = original

    def test_prompt_asks_for_a_hero_height(self):
        calls: list[str] = []

        class FakeLLM:
            async def chat_json(self, *, system_prompt, user_prompt, schema, **_):
                calls.append(system_prompt)
                return schema(hero_height="full")

        self._language(FakeLLM())
        # Both options are named, and the design personality line the model is
        # meant to read them against is already in the payload.
        self.assertIn("hero_height", calls[0])
        self.assertIn("banded", calls[0])

    def test_pick_passes_through(self):
        class FakeLLM:
            async def chat_json(self, *, schema, **_):
                return schema(palette=None, font_pairing=None, hero_height="banded")

        self.assertEqual(self._language(FakeLLM()).hero_height, "banded")

    def test_deferring_is_a_safe_no_op(self):
        class FakeLLM:
            async def chat_json(self, *, schema, **_):
                return schema(hero_height=None)

        self.assertIsNone(self._language(FakeLLM()).hero_height)


class HeroHeightPrecedenceTest(unittest.TestCase):
    """explicit user pick → LLM pick → industry default → mood default → full."""

    def test_explicit_user_pick_beats_everything(self):
        self.assertEqual(
            resolve_hero_height("banded", "full", mood="luxury", industry="restaurant"),
            "banded",
        )

    def test_llm_pick_wins_when_the_user_chose_auto(self):
        self.assertEqual(
            resolve_hero_height(None, "banded", mood="luxury", industry="restaurant"),
            "banded",
        )

    def test_industry_default_beats_mood(self):
        # A "modern" restaurant still leads with photography.
        self.assertEqual(
            resolve_hero_height(None, None, mood="modern", industry="restaurant"), "full"
        )
        # …and a "friendly" SaaS still gets to its copy.
        self.assertEqual(
            resolve_hero_height(None, None, mood="friendly", industry="saas"), "banded"
        )

    def test_mood_default_applies_when_the_industry_has_no_lean(self):
        self.assertEqual(
            resolve_hero_height(None, None, mood="technical", industry="other"), "banded"
        )
        self.assertEqual(
            resolve_hero_height(None, None, mood="luxury", industry="other"), "full"
        )

    def test_result_is_always_determined_with_nothing_known(self):
        # A disabled or failed pass must never leave the theme unset.
        self.assertEqual(
            resolve_hero_height(None, None, mood=None, industry=None), "full"
        )

    def test_industry_map_only_uses_real_categories(self):
        # Keys are IndustryCategory values; a typo'd slug would silently never
        # match and the industry would quietly fall through to its mood.
        self.assertTrue(set(INDUSTRY_HERO_HEIGHT) <= set(INDUSTRY_MOOD))

    def test_every_mood_has_a_default(self):
        self.assertEqual(set(MOOD_HERO_HEIGHT), set(get_args(BrandMood)))


if __name__ == "__main__":
    unittest.main()
