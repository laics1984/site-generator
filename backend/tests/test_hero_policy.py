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
from dataclasses import replace
from types import SimpleNamespace

from app.models.builder_schema import BuilderElement
from app.models.content_blocks import HeroBlock
from app.services.hero_director import HeroDirective
from app.services.image_styling import washed_photo_background
from app.services.pexels import PhotoResult
from app.services.schema_builder import (
    _abstract_theme_query,
    _apply_hero_photo_policy,
    _apply_hero_washed_background,
    _has_composable_subject,
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

    async def resolve(
        self, query, *, intent="generic", alt_fallback=None, prefer=None,
        slot_usage="any", pinned_url=None,
    ):
        self.calls.append(
            {
                "query": query, "intent": intent, "method": "resolve",
                "slot_usage": slot_usage, "pinned_url": pinned_url,
            }
        )
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
        self.assertEqual(ctx.resolver.calls[0]["slot_usage"], "inline")
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
        # A full-bleed slot must tell the resolver so its text-detection
        # screen (OCR/vision) actually runs on the winning candidate.
        self.assertEqual(ctx.resolver.calls[0]["slot_usage"], "background")

    async def test_split_mood_but_planner_forces_background_stays_full_bleed(self):
        # The split lean is soft: an explicit planner layout="background" wins.
        featured = _photo("pexels", "https://x/feat.jpg")
        abstract = _photo("pexels", "https://x/abs.jpg")
        ctx = _ctx(featured, abstract, mood="modern")
        block = HeroBlock(headline="Ship faster", image_query="dashboard", layout="background")

        _, washed = await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "background")
        self.assertIsNone(washed)
        self.assertEqual(ctx.resolver.calls[0]["slot_usage"], "background")

    async def test_split_drops_washed_bg_when_abstract_not_genuine(self):
        ctx = _ctx(_photo("pexels"), _photo("placeholder"), mood="technical")
        block = HeroBlock(headline="Welcome", image_query="modern office")

        _, washed = await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "split")
        self.assertIsNone(washed)
        self.assertEqual(ctx.resolver.calls[0]["slot_usage"], "inline")

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

    async def test_scraped_text_bearing_photo_never_wins_the_legacy_background_slot(self):
        """Regression: before the fix, this no-directive path resolved the
        hero photo BEFORE deciding it would render full-bleed, so the
        resolver was never told slot_usage="background" and the
        text-detection veto (image_match.bears_text) never ran — a scraped
        graphic/newsletter with its own baked-in wording could win the slot
        and collide with the site's real headline drawn on top of it.
        Exercises the REAL ImageResolver (not FakeResolver) so the veto's
        actual wiring is under test, not a mock of it."""
        from app.models.content_blocks import ImageMetadata
        from app.services.media import ImageResolver

        clean_fallback = PhotoResult(
            url="https://images.pexels.com/clean-crowd.jpg",
            alt="volunteers at a community event",
            photographer="X", photographer_url="u",
            source="pexels", avg_color="#8a7f6d",
        )

        class FakePexels:
            configured = True

            async def search_many(self, query, *, orientation):
                return [clean_fallback]

        text_bearing = ImageMetadata(
            url="https://x/newsletter.jpg",
            alt="community activities newsletter update",
            intent="hero",
            width=1600,
            height=1000,
            vision_has_text=True,
        )
        resolver = ImageResolver(scraped_metadata=[text_bearing], pexels=FakePexels())
        theme = build_theme("#2563eb", "friendly")  # not split-inclined
        ctx = SimpleNamespace(theme=theme, resolver=resolver, page_images=[])
        block = HeroBlock(
            headline="Make a Difference in the Field",
            image_query="community activities newsletter",
        )

        img_slot, _washed = await _apply_hero_photo_policy(block, ctx)

        # confirms the gap is live: the scraped candidate was excluded from
        # the background slot and a real substitute filled it instead.
        self.assertEqual(block.layout, "background")
        self.assertNotEqual(getattr(img_slot, "url", None), text_bearing.url)
        self.assertEqual(getattr(img_slot, "url", None), clean_fallback.url)


class HeroDirectiveTest(unittest.IsolatedAsyncioTestCase):
    """Directive path (hero_director → _apply_hero_directive): the per-page art
    direction wins over the mood lean, and slot_usage carries source-background
    provenance into the resolver."""

    async def test_split_directive_resolves_wash_regardless_of_mood(self):
        # friendly is NOT split-inclined — the directive alone must trigger the
        # abstract wash for a split hero.
        featured = _photo("scraped", "https://x/feat.jpg")
        abstract = _photo("pexels", "https://x/abs.jpg")
        ctx = _ctx(featured, abstract, mood="friendly")
        block = HeroBlock(headline="Our programs", image_query="volunteers")
        directive = HeroDirective("hero-modern-split", "split", wants_wash=True)

        img_slot, washed = await _apply_hero_photo_policy(block, ctx, directive)

        self.assertEqual(block.layout, "split")
        self.assertIs(img_slot, featured)
        self.assertIs(washed, abstract)
        self.assertEqual(ctx.resolver.calls[0]["slot_usage"], "inline")
        self.assertEqual(ctx.resolver.calls[1]["method"], "resolve_abstract_bg")
        self.assertEqual(ctx.resolver.calls[1]["intent"], "cta_bg")

    async def test_background_directive_resolves_with_background_slot_usage(self):
        featured = _photo("scraped", "https://x/bg.jpg")
        ctx = _ctx(featured, _photo("pexels"), mood="friendly")
        block = HeroBlock(headline="Change lives", image_query="community")
        directive = HeroDirective(
            "hero-background-bold", "background", pin_source_background=True
        )

        img_slot, washed = await _apply_hero_photo_policy(block, ctx, directive)

        self.assertEqual(block.layout, "background")
        self.assertIs(img_slot, featured)
        self.assertIsNone(washed)
        self.assertEqual(ctx.resolver.calls[0]["slot_usage"], "background")

    async def test_imageless_directive_skips_resolution_entirely(self):
        ctx = _ctx(_photo("scraped"), _photo("pexels"), mood="friendly")
        block = HeroBlock(headline="Get in touch", image_query="office")
        directive = HeroDirective("hero-centered-minimal", "split")

        img_slot, washed = await _apply_hero_photo_policy(block, ctx, directive)

        self.assertIsNone(img_slot)
        self.assertIsNone(washed)
        self.assertIsNone(block.image_query)  # imageless template stays feasible
        self.assertEqual(ctx.resolver.calls, [])  # no scraped photo burned

    async def test_split_directive_without_genuine_photo_degrades_to_imageless(self):
        ctx = _ctx(_photo("placeholder"), _photo("pexels"), mood="friendly")
        block = HeroBlock(headline="Programs", image_query="volunteers")
        directive = HeroDirective("hero-modern-split", "split", wants_wash=True)

        img_slot, washed = await _apply_hero_photo_policy(block, ctx, directive)

        self.assertIsNone(img_slot)
        self.assertIsNone(washed)
        self.assertIsNone(block.image_query)  # -> hero-gradient via preference

    async def test_background_directive_without_genuine_photo_uses_abstract(self):
        featured = _photo("placeholder")
        abstract = _photo("pexels", "https://x/abs.jpg")
        ctx = _ctx(featured, abstract, mood="friendly")
        block = HeroBlock(headline="Home", image_query="impact")
        directive = HeroDirective("hero-background-bold", "background")

        img_slot, washed = await _apply_hero_photo_policy(block, ctx, directive)

        self.assertEqual(block.layout, "background")
        self.assertIs(img_slot, abstract)
        self.assertEqual(block.image_query, _abstract_theme_query(ctx))
        self.assertIsNone(washed)


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
        self.assertIn("rgba(255,255,255,0.8)", light)
        self.assertIn("rgba(17,17,17,0.8)", dark)
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


class ComposableSubjectTest(unittest.TestCase):
    """An anchored hero puts its copy to one side so the PHOTOGRAPH keeps the
    other. Without a subject there is nothing on the open side, so anchored copy
    reads as text shoved against an edge — those heroes centre."""

    def test_a_real_photo_can_be_composed_around(self):
        self.assertTrue(_has_composable_subject(_photo("scraped")))
        self.assertTrue(_has_composable_subject(_photo("pexels")))

    def test_the_abstract_wash_cannot(self):
        """It is a genuine Pexels photo — `source` alone can't tell it apart —
        but it is picked on colour distance and carries no subject."""
        abstract = replace(_photo("pexels"), is_abstract=True)
        self.assertIn(abstract.source, _GENUINE)  # would pass a source-only gate
        self.assertFalse(_has_composable_subject(abstract))

    def test_placeholder_and_missing_photos_cannot(self):
        self.assertFalse(_has_composable_subject(_photo("placeholder")))
        self.assertFalse(_has_composable_subject(None))

    def test_resolve_abstract_bg_marks_what_it_returns(self):
        """The flag has to be set where the abstract is produced, or the gate
        above silently never fires."""
        import asyncio

        from app.services.media import ImageResolver

        class FakePexels:
            configured = True

            async def search_many(self, query, *, orientation):
                return [
                    PhotoResult(
                        url="https://img.example/tex.jpg", alt="texture",
                        photographer=None, photographer_url=None,
                        source="pexels", avg_color="#2563eb",
                    )
                ]

        resolver = ImageResolver(scraped_images=[], pexels=FakePexels())
        got = asyncio.run(
            resolver.resolve_abstract_bg("soft gradient", color_target_hex="#2563eb")
        )
        self.assertIsNotNone(got)
        self.assertTrue(got.is_abstract)

    def test_a_normally_resolved_photo_is_not_marked_abstract(self):
        self.assertFalse(_photo("pexels").is_abstract)


class TextVetoScopeTest(unittest.TestCase):
    """The baked-in-text veto is for SOURCE artwork only.

    Pexels ships photographs, not posters, so paying to screen them (a vision
    call or an OCR pass per image) buys nothing. The scope is enforced by type —
    stock photos are PhotoResult and never become ImageMetadata — and these
    tests pin that so a future refactor can't quietly widen it.
    """

    def test_the_veto_cannot_be_applied_to_a_stock_photo(self):
        import inspect

        from app.services.image_match import bears_text

        params = list(inspect.signature(bears_text).parameters.values())
        self.assertIn("ImageMetadata", str(params[0].annotation))
        # PhotoResult carries none of the fields the veto reads.
        stock = _photo("pexels")
        for field in ("vision_has_text", "vision_kind", "role"):
            self.assertFalse(hasattr(stock, field), msg=field)

    def test_stock_wins_a_hero_background_despite_a_text_shaped_alt(self):
        """An alt tripping every lexical hint must not cost a stock photo the
        hero — the hints only ever apply to scraped artwork."""
        import asyncio

        from app.services.media import ImageResolver

        class FakePexels:
            configured = True

            async def search_many(self, query, *, orientation):
                return [
                    PhotoResult(
                        url="https://images.pexels.com/poster-flyer-billboard.jpg",
                        alt="vintage poster flyer billboard advert infographic",
                        photographer="X", photographer_url="u",
                        source="pexels", avg_color="#8a7f6d",
                    )
                ]

        resolver = ImageResolver(scraped_metadata=[], pexels=FakePexels())
        got = asyncio.run(
            resolver.resolve("anything", intent="hero", slot_usage="background")
        )
        self.assertEqual(got.source, "pexels")

    def test_the_vision_pass_is_fed_the_scraped_pool_only(self):
        """Every call site hands it `scraped_metadata`. Nothing resolved from
        Pexels even exists yet when it runs, and it must stay that way — stock
        photos would burn the vision_max_images budget for no benefit."""
        import inspect
        import re

        from app.routers import generate

        src = inspect.getsource(generate)
        calls = re.findall(
            r"_annotate_source_images\(\s*([^)]*?)\)", src, flags=re.S
        )
        # One definition-site match is the `async def`; the rest are real calls.
        invocations = [c for c in calls if "metadata:" not in c]
        self.assertTrue(invocations, "no call sites found — did the name change?")
        for args in invocations:
            self.assertIn("scraped_metadata", args)


if __name__ == "__main__":
    unittest.main()
