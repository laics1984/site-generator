import asyncio
import unittest

from app.models.content_blocks import SourceContent
from app.models.industry import PageScaffold
from app.routers.pages import RecipeRequest, page_recipe
from app.services.page_inference import core_pages_not_in_inferred


class PageRecipeTest(unittest.TestCase):
    def test_semantic_inferred_page_suppresses_template_core_slug_variant(self):
        core = [
            PageScaffold(page_type="home", slug="", title="Home", is_homepage=True, sections=[]),
            PageScaffold(page_type="about", slug="about", title="About", sections=[]),
            PageScaffold(page_type="contact", slug="contact", title="Contact", sections=[]),
            PageScaffold(page_type="privacy", slug="privacy", title="Privacy", sections=[], is_legal=True),
            PageScaffold(page_type="terms", slug="terms", title="Terms", sections=[], is_legal=True),
        ]
        inferred = [
            PageScaffold(page_type="home", slug="", title="Home", is_homepage=True, sections=[]),
            PageScaffold(page_type="about", slug="about-us", title="About Us", sections=[]),
            PageScaffold(page_type="contact", slug="contact-us", title="Contact Us", sections=[]),
        ]

        result = core_pages_not_in_inferred(core, inferred)

        self.assertEqual([page.slug for page in result], ["privacy", "terms"])


class RecipeThemePreviewTest(unittest.TestCase):
    """The recipe endpoint returns an industry-aware theme preview (no LLM needed
    via the industry_override path)."""

    def _recipe(self, industry):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.com",
            raw_text="Joe's place — fresh food made daily.",
        )
        payload = RecipeRequest(source=source, industry_override=industry)
        return asyncio.run(page_recipe(payload))

    def test_preview_includes_fonts_and_palette(self):
        res = self._recipe("restaurant")
        self.assertIsNotNone(res.theme_preview)
        typo = res.theme_preview["typography"]
        self.assertTrue(typo["headingFont"])
        self.assertTrue(typo["bodyFont"])
        self.assertTrue(res.google_fonts)
        # No logo colour at recipe time → curated industry palette, not generic blue.
        self.assertNotEqual(res.theme_preview["colors"]["primary"], "#2563eb")

    def test_preview_font_matches_generation_inputs(self):
        # Same inputs the generator uses (industry + mood + no seed) → identical font,
        # so the picker preview reflects the real site. restaurant → friendly mood.
        from app.models.content_blocks import industry_default_mood
        from app.services.theme import build_theme

        res = self._recipe("restaurant")
        gen = build_theme(
            None,
            mood=industry_default_mood("restaurant"),
            font_seed=None,
            industry="restaurant",
            palette_mode="auto",
        )
        self.assertEqual(
            res.theme_preview["typography"]["headingFont"],
            gen.typography.heading_font,
        )


if __name__ == "__main__":
    unittest.main()
