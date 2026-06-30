"""Design-brain batching: one LLM call for the whole site, with per-page
index namespacing so one page's pick never lands on another page's section."""

import asyncio
import unittest

from app.config import settings
from app.services.design_brain import (
    SiteDesignRecipe,
    SiteSectionChoice,
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
                        SiteSectionChoice(page_index=0, section_index=0, template_id="hero-bento"),
                        SiteSectionChoice(page_index=1, section_index=0, template_id="hero-editorial"),
                    ]
                )

        original = settings.design_brain_enabled
        settings.design_brain_enabled = True
        try:
            recipe = asyncio.run(
                generate_site_design_recipe(
                    mood="modern",
                    industry="saas",
                    pages=[["hero"], ["hero"]],
                    llm=FakeLLM(),
                )
            )
        finally:
            settings.design_brain_enabled = original

        self.assertEqual(len(calls), 1)  # ONE round-trip for the whole site
        self.assertIn("Page 0", calls[0])
        self.assertIn("Page 1", calls[0])
        self.assertEqual(recipe.recipe_for(0).template_for(0), "hero-bento")
        self.assertEqual(recipe.recipe_for(1).template_for(0), "hero-editorial")


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


if __name__ == "__main__":
    unittest.main()
