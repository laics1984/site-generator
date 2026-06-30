"""Hero imagery policy (schema_builder._apply_hero_photo_policy).

Photos lead:
  * a genuine featured photo + a SaaS/professional mood (modern/technical), unless
    the planner forced a full-bleed -> SPLIT hero (featured photo in the column) +
    a colour-matched abstract background photo washed in brand colour;
  * a genuine featured photo + any other mood -> full-bleed FEATURED photo, no wash;
  * no featured photo -> full-bleed colour-matched abstract hero;
  * nothing genuine -> gradient header (image_query dropped).
The decision keys off the resolver's real source; only scraped/pexels are genuine.
"""

import unittest
from types import SimpleNamespace

from app.models.builder_schema import BuilderElement
from app.models.content_blocks import HeroBlock
from app.services.image_styling import washed_photo_background
from app.services.pexels import PhotoResult
from app.services.schema_builder import (
    _abstract_theme_query,
    _apply_hero_photo_policy,
    _apply_hero_washed_background,
)
from app.services.theme import build_theme


def _photo(source, url=None):
    return PhotoResult(
        url=url or f"https://img.example/{source}.jpg",
        alt=f"{source} image",
        photographer=None,
        photographer_url=None,
        source=source,
    )


_GENUINE = {"scraped", "pexels"}


class FakeResolver:
    """Routes by query so the policy calls return distinct photos: a query
    containing 'abstract' returns the abstract photo, anything else the featured
    one. `resolve_abstract_bg` mirrors the real resolver — it returns the abstract
    photo only when it is a genuine (scraped/pexels) source, else None. Records
    every call."""

    def __init__(self, featured, abstract):
        self._featured = featured
        self._abstract = abstract
        self.calls = []

    async def resolve(self, query, *, intent="generic", alt_fallback=None, prefer=None):
        self.calls.append({"query": query, "intent": intent, "method": "resolve"})
        if query and "abstract" in query:
            return self._abstract
        return self._featured

    async def resolve_abstract_bg(self, query, *, color_target_hex, intent="cta_bg"):
        self.calls.append(
            {"query": query, "intent": intent, "method": "resolve_abstract_bg"}
        )
        return self._abstract if self._abstract.source in _GENUINE else None


def _ctx(featured, abstract, *, mood="modern", scheme="light"):
    theme = build_theme("#2563eb", mood, color_scheme=scheme)
    return SimpleNamespace(
        theme=theme, resolver=FakeResolver(featured, abstract), page_images=[]
    )


class HeroPhotoPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_genuine_featured_split_mood_gives_split_with_color_matched_bg(self):
        featured = _photo("scraped", "https://x/feat.jpg")
        abstract = _photo("pexels", "https://x/abs.jpg")
        ctx = _ctx(featured, abstract, mood="modern")  # split-inclined
        block = HeroBlock(headline="Great coffee", image_query="coffee beans")

        img_slot, washed = await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "split")
        self.assertEqual(block.image_query, "coffee beans")  # featured query kept
        self.assertIs(img_slot, featured)  # fills the split column
        self.assertIs(washed, abstract)  # washes the section background
        self.assertEqual(ctx.resolver.calls[0]["intent"], "hero")
        self.assertEqual(ctx.resolver.calls[1]["method"], "resolve_abstract_bg")
        self.assertEqual(ctx.resolver.calls[1]["intent"], "cta_bg")

    async def test_genuine_featured_non_split_mood_is_full_bleed_photo(self):
        featured = _photo("scraped", "https://x/feat.jpg")
        abstract = _photo("pexels", "https://x/abs.jpg")
        ctx = _ctx(featured, abstract, mood="friendly")  # not split-inclined
        block = HeroBlock(headline="Fresh meals", image_query="brunch table")

        img_slot, washed = await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "background")  # full-bleed featured photo
        self.assertIs(img_slot, featured)
        self.assertIsNone(washed)  # no abstract wash
        # No second resolve for an abstract background.
        self.assertEqual(len(ctx.resolver.calls), 1)

    async def test_split_mood_but_planner_forces_background_stays_full_bleed(self):
        # The split lean is soft: an explicit planner layout="background" wins.
        featured = _photo("pexels", "https://x/feat.jpg")
        abstract = _photo("pexels", "https://x/abs.jpg")
        ctx = _ctx(featured, abstract, mood="modern")
        block = HeroBlock(headline="Ship faster", image_query="dashboard", layout="background")

        _, washed = await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "background")
        self.assertIsNone(washed)

    async def test_split_drops_washed_bg_when_abstract_not_genuine(self):
        ctx = _ctx(_photo("pexels"), _photo("placeholder"), mood="technical")
        block = HeroBlock(headline="Welcome", image_query="modern office")

        _, washed = await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "split")
        self.assertIsNone(washed)

    async def test_no_featured_falls_to_full_bleed_color_matched_abstract(self):
        featured = _photo("placeholder")
        abstract = _photo("pexels", "https://x/abs.jpg")
        ctx = _ctx(featured, abstract, mood="modern")
        block = HeroBlock(headline="About us", image_query="team portrait", layout="split")

        img_slot, washed = await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "background")  # never split without a photo
        self.assertEqual(block.image_query, _abstract_theme_query(ctx))
        self.assertIs(img_slot, abstract)
        self.assertIsNone(washed)
        self.assertEqual(ctx.resolver.calls[-1]["method"], "resolve_abstract_bg")

    async def test_nothing_genuine_falls_to_gradient(self):
        ctx = _ctx(_photo("placeholder", "data:f"), _photo("placeholder", "data:a"))
        block = HeroBlock(headline="Contact", image_query="abstract texture")

        img_slot, washed = await _apply_hero_photo_policy(block, ctx)

        self.assertIsNone(block.image_query)  # -> hero-gradient via preference
        self.assertIsNone(washed)
        self.assertEqual(img_slot.source, "placeholder")

    async def test_missing_query_is_filled_for_split_feasibility(self):
        # Genuine featured photo but a blank query: derive one so the split image
        # slot stays feasible.
        ctx = _ctx(_photo("scraped"), _photo("pexels"), mood="modern")
        block = HeroBlock(headline="Our services", image_query=None)

        await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "split")
        self.assertEqual(block.image_query, "Our services")

    async def test_blank_query_string_uses_image_alt(self):
        ctx = _ctx(_photo("pexels"), _photo("pexels"), mood="modern")
        block = HeroBlock(headline="Contact", image_query="   ", image_alt="reach the team")

        await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "split")
        self.assertEqual(block.image_query, "reach the team")


class WashedBackgroundTest(unittest.TestCase):
    def test_light_and_dark_schemes_differ_and_keep_the_photo(self):
        light = washed_photo_background(
            "u", scheme="light", surface_hex="#ffffff",
            secondary_hex="#111111", primary_hex="#2563eb",
        )
        dark = washed_photo_background(
            "u", scheme="dark", surface_hex="#ffffff",
            secondary_hex="#111111", primary_hex="#2563eb",
        )
        self.assertIn("url('u')", light)
        self.assertIn("url('u')", dark)
        # Light washes over the surface; dark washes over the dark secondary.
        self.assertIn("rgba(255,255,255,0.92)", light)
        self.assertIn("rgba(17,17,17,0.92)", dark)
        self.assertNotEqual(light, dark)

    def test_apply_drops_background_shorthand_and_sets_image(self):
        el = BuilderElement(
            name="Hero - Modern Split",
            type="container",
            styles={
                "background": "linear-gradient(180deg, #fff, #eee)",
                "backgroundColor": "#ffffff",
            },
            content=[],
        )
        ctx = _ctx(_photo("pexels"), _photo("pexels"))
        _apply_hero_washed_background(el, _photo("pexels", "https://x/abs.jpg"), ctx)

        self.assertNotIn("background", el.styles)  # shorthand removed
        self.assertIn("url('https://x/abs.jpg')", el.styles["backgroundImage"])
        self.assertEqual(el.styles["backgroundSize"], "cover")


if __name__ == "__main__":
    unittest.main()
