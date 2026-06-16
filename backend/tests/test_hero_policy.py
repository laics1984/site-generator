"""Hero treatment normalisation (schema_builder._apply_hero_photo_policy).

A hero must get ONE consistent treatment site-wide: a full-bleed background photo
when a genuine image is available (a scraped/document match, or stock when Pexels
is configured), else the brand gradient header. The decision keys off the
resolver's real source — a `picsum` placeholder means "no scraped match and no
stock photo", which is the gradient case.
"""

import unittest
from types import SimpleNamespace

from app.models.content_blocks import HeroBlock
from app.services.pexels import PhotoResult
from app.services.schema_builder import _apply_hero_photo_policy


def _photo(source):
    return PhotoResult(
        url=f"https://img.example/{source}.jpg",
        alt=f"{source} image",
        photographer=None,
        photographer_url=None,
        source=source,
    )


class FakeResolver:
    """Returns a fixed PhotoResult and records the resolve() call."""

    def __init__(self, photo):
        self._photo = photo
        self.calls = []

    async def resolve(self, query, *, intent="generic", alt_fallback=None, prefer=None):
        self.calls.append(
            {"query": query, "intent": intent, "alt_fallback": alt_fallback, "prefer": prefer}
        )
        return self._photo


def _ctx(photo):
    return SimpleNamespace(resolver=FakeResolver(photo), page_images=[])


class HeroPhotoPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_scraped_photo_forces_background_and_keeps_query(self):
        block = HeroBlock(headline="Great coffee", image_query="coffee beans roasting", layout="split")
        ctx = _ctx(_photo("scraped"))

        photo = await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "background")
        self.assertEqual(block.image_query, "coffee beans roasting")
        self.assertEqual(photo.source, "scraped")
        # Resolved against the hero pool with the block's own query.
        self.assertEqual(ctx.resolver.calls[0]["intent"], "hero")
        self.assertEqual(ctx.resolver.calls[0]["query"], "coffee beans roasting")

    async def test_stock_photo_forces_background(self):
        block = HeroBlock(headline="Welcome", image_query="modern office", layout="split")
        ctx = _ctx(_photo("pexels"))

        await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "background")
        self.assertEqual(block.image_query, "modern office")

    async def test_picsum_falls_back_to_gradient(self):
        # No scraped match, no stock -> resolver returns picsum -> gradient header.
        block = HeroBlock(headline="About us", image_query="abstract texture", layout="background")
        ctx = _ctx(_photo("picsum"))

        await _apply_hero_photo_policy(block, ctx)

        self.assertIsNone(block.image_query)

    async def test_missing_query_is_filled_for_background_feasibility(self):
        # A genuine photo but a blank image_query: derive one so the background
        # variant stays feasible (selection drops to gradient otherwise).
        block = HeroBlock(headline="Our services", image_query=None)
        ctx = _ctx(_photo("scraped"))

        await _apply_hero_photo_policy(block, ctx)

        self.assertEqual(block.layout, "background")
        self.assertTrue((block.image_query or "").strip())
        self.assertEqual(block.image_query, "Our services")

    async def test_blank_query_string_treated_as_missing(self):
        block = HeroBlock(headline="Contact", image_query="   ", image_alt="reach the team")
        ctx = _ctx(_photo("pexels"))

        await _apply_hero_photo_policy(block, ctx)

        # image_alt wins over headline when present.
        self.assertEqual(block.image_query, "reach the team")


if __name__ == "__main__":
    unittest.main()
