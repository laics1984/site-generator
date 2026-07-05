"""Hero accent typography + industry-personality prompt injection.

- `headline_accent` renders the headline's trailing phrase as its own
  accent-coloured line (two stacked text elements — no HTML in innerText, the
  builder editor would show literal tags).
- Full-bleed photo heroes no longer keep the catalog's plain white ink:
  headline becomes a brand-tinted near-white and the eyebrow carries the
  lifted brand accent, both AA against the scrim-composited backdrop.
- The planner user prompt carries the industry's 2026 personality brief.

Heroes render through the catalogue/template path (fill_template +
_apply_hero_typography); these tests build real pages via plan_to_site with
the design brain off and no network.
"""

from __future__ import annotations

import unittest

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import (
    HeroBlock,
    ImageMetadata,
    PagePlan,
    SitePlan,
)
from app.services.schema_builder import (
    _SCRIM_COMPOSITE_BG,
    _split_headline,
    plan_to_site,
)
from app.services.theme import _contrast, build_theme


class SplitHeadlineTest(unittest.TestCase):
    def test_trailing_phrase_splits(self):
        self.assertEqual(
            _split_headline("Bread baked the slow way", "the slow way"),
            ("Bread baked", "the slow way"),
        )

    def test_case_and_whitespace_tolerant(self):
        self.assertEqual(
            _split_headline("Bread baked The Slow Way", "  the   slow way "),
            ("Bread baked", "The Slow Way"),
        )

    def test_interior_phrase_is_rejected(self):
        self.assertIsNone(_split_headline("Bread baked the slow way", "baked the"))

    def test_absent_or_full_headline_accent_is_rejected(self):
        self.assertIsNone(_split_headline("Bread baked slowly", None))
        self.assertIsNone(_split_headline("Bread baked slowly", ""))
        self.assertIsNone(_split_headline("Bread baked slowly", "Bread baked slowly"))


_HEADLINE = "Bread baked the slow way"
_ACCENT = "the slow way"

# A scraped full-bleed background photo → hero_director gives visual moods the
# hero-background-bold treatment (same fixture shape as test_overlay_integration).
_PHOTO_METADATA = [
    ImageMetadata(
        url="https://source.example/hero-bg.jpg", intent="hero", role="background",
        source_usage="css_background", width=2400, height=1400,
    )
]


def _plan(accent=None, mood="modern", eyebrow=None):
    return SitePlan(
        site_name="Accent Co",
        brand_mood=mood,
        industry_category="restaurant",
        pages=[
            PagePlan(
                page_type="home",
                slug="",
                title="Home",
                description="d",
                is_homepage=True,
                seo_title="Home | Accent Co",
                seo_description="Seo description for the accent test homepage.",
                blocks=[
                    HeroBlock(
                        kind="hero",
                        eyebrow=eyebrow,
                        headline=_HEADLINE,
                        headline_accent=accent,
                        primary_cta_label="Order",
                        primary_cta_href="#contact",
                        image_query="bakery bread",
                    )
                ],
            )
        ],
    )


def _texts(el, out=None):
    out = out if out is not None else []
    content = el.content
    if isinstance(content, list):
        for child in content:
            _texts(child, out)
    elif el.type == "text":
        out.append(el)
    return out


def _page_texts(site):
    home = site.pages[0]
    out = []
    for el in home.body_schema.elements:
        _texts(el, out)
    return out


def _by_inner(site, inner):
    return next((t for t in _page_texts(site) if t.content.innerText == inner), None)


class HeroAccentRenderTest(unittest.IsolatedAsyncioTestCase):
    async def _site(self, accent=None, mood="modern", eyebrow=None, photo=False):
        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            return await plan_to_site(
                _plan(accent, mood, eyebrow),
                brand=BrandIdentity(name="Accent Co", mood=mood),
                theme=build_theme("#2563eb", mood=mood, color_scheme="light"),
                scraped_metadata=(
                    [m.model_copy() for m in _PHOTO_METADATA] if photo else None
                ),
            )
        finally:
            settings.design_brain_enabled = original

    async def test_no_accent_keeps_single_headline(self):
        site = await self._site()
        self.assertIsNotNone(_by_inner(site, _HEADLINE))
        self.assertIsNone(_by_inner(site, _ACCENT))

    async def test_accent_becomes_its_own_coloured_line(self):
        site = await self._site(accent=_ACCENT)
        lead = _by_inner(site, "Bread baked")
        accent = _by_inner(site, _ACCENT)
        self.assertIsNotNone(lead)
        self.assertIsNotNone(accent)
        self.assertTrue(accent.name.endswith("accent"))
        self.assertIn("color", accent.styles)
        self.assertNotEqual(accent.styles.get("color"), lead.styles.get("color"))
        # Full headline no longer exists as a single node.
        self.assertIsNone(_by_inner(site, _HEADLINE))

    async def test_invalid_accent_is_a_silent_noop(self):
        site = await self._site(accent="Bread baked")  # interior phrase
        self.assertIsNotNone(_by_inner(site, _HEADLINE))
        self.assertIsNone(_by_inner(site, "the slow way"))

    async def test_photo_hero_inks_are_brand_tinted_and_aa(self):
        site = await self._site(
            accent=_ACCENT, mood="friendly", eyebrow="Since 1998", photo=True
        )
        home = site.pages[0]
        hero = home.body_schema.elements[0]
        self.assertTrue(getattr(hero, "headerOverlaySafe", None), "expected full-bleed hero")
        lead = _by_inner(site, "Bread baked")
        accent = _by_inner(site, _ACCENT)
        eyebrow = _by_inner(site, "Since 1998")
        for el in (lead, accent, eyebrow):
            self.assertIsNotNone(el)
            color = el.styles.get("color")
            self.assertIsInstance(color, str)
            self.assertGreaterEqual(_contrast(_SCRIM_COMPOSITE_BG, color), 4.5)
        self.assertNotEqual(lead.styles["color"].lower(), "#ffffff")
        # Eyebrow carries the lifted accent, not a flat near-white.
        self.assertNotEqual(eyebrow.styles["color"], lead.styles["color"])

    async def test_luxury_accent_line_is_italic(self):
        site = await self._site(accent=_ACCENT, mood="luxury", photo=True)
        accent = _by_inner(site, _ACCENT)
        self.assertIsNotNone(accent)
        self.assertEqual(accent.styles.get("fontStyle"), "italic")

    async def test_modern_accent_line_is_not_italic(self):
        site = await self._site(accent=_ACCENT, mood="modern")
        accent = _by_inner(site, _ACCENT)
        self.assertIsNotNone(accent)
        self.assertNotIn("fontStyle", accent.styles)


class PersonalityPromptTest(unittest.TestCase):
    def test_user_prompt_carries_industry_personality(self):
        from app.models.content_blocks import SourceContent
        from app.models.industry import PageScaffold
        from app.services.planner import DetectedBrand, _build_scaffolded_user_prompt

        prompt = _build_scaffolded_user_prompt(
            SourceContent(
                source_kind="url",
                source_ref="https://x.test",
                title="T",
                raw_text="text",
            ),
            DetectedBrand(site_name="T", industry_category="restaurant"),
            [PageScaffold(page_type="home", slug="", title="Home", sections=["hero"])],
        )
        self.assertIn("industry_personality", prompt)
        self.assertIn("appetite-led", prompt)

    def test_hero_schema_line_mentions_headline_accent(self):
        from app.services.planner import _SCAFFOLD_BLOCK_SCHEMAS, _scaffold_system_prompt

        self.assertIn("headline_accent", _SCAFFOLD_BLOCK_SCHEMAS["hero"])
        self.assertIn("headline_accent", _scaffold_system_prompt(frozenset({"hero"})))

    def test_personality_briefs_cover_all_industries(self):
        from app.services.industry_personality import (
            personality_for,
            personality_prompt_lines,
        )

        for industry in (
            "restaurant", "agency", "saas", "professional-services",
            "ecommerce", "consultancy", "nonprofit", "personal", "other",
        ):
            brief = personality_prompt_lines(industry)
            self.assertTrue(brief.startswith("voice: "))
            self.assertIn("design: ", brief)
        # Unknown industries degrade to the generic brief.
        self.assertEqual(personality_for("zeppelin"), personality_for("other"))
