"""Image-selection fixes: keep non-photographic graphics (QR codes, screenshots,
maps, banners) out of large featured slots, and never repeat an image on a page."""

import unittest

from app.models.content_blocks import ImageMetadata
from app.services.image_match import rank_candidates
from app.services.media import ImageResolver, _placeholder_photo


def _img(url, **kw):
    return ImageMetadata(url=url, **kw)


class _UnconfiguredPexels:
    configured = False


class FeaturedGraphicExclusionTest(unittest.TestCase):
    def test_graphic_excluded_for_hero_slot(self):
        # A graphic (e.g. a QR code) with a strong lexical match must NOT win a
        # hero slot. It is the only candidate, so nothing is chosen.
        qr = _img(
            "/qr.png", alt="team office workplace", intent="generic",
            vision_kind="graphic", width=600, height=600,
        )
        self.assertIsNone(rank_candidates("team office workplace", "hero", [qr]).chosen)

    def test_graphic_still_allowed_for_generic_slot(self):
        qr = _img(
            "/qr.png", alt="team office workplace", intent="generic",
            vision_kind="graphic", width=600, height=600,
        )
        chosen = rank_candidates("team office workplace", "generic", [qr]).chosen
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.url, "/qr.png")

    def test_real_photo_wins_about_over_graphic(self):
        photo = _img(
            "/photo.jpg", alt="our team", intent="about",
            vision_kind="photo", width=1200, height=800,
        )
        graphic = _img(
            "/qr.png", alt="our team", intent="about",
            vision_kind="graphic", width=600, height=600,
        )
        chosen = rank_candidates("our team", "about", [graphic, photo]).chosen
        self.assertIsNotNone(chosen)
        self.assertEqual(chosen.url, "/photo.jpg")


class PlaceholderUniquenessTest(unittest.IsolatedAsyncioTestCase):
    def test_nonce_changes_url_zero_is_identity(self):
        base = _placeholder_photo("x", "landscape", None)
        zero = _placeholder_photo("x", "landscape", None, nonce=0)
        one = _placeholder_photo("x", "landscape", None, nonce=1)
        self.assertEqual(base.url, zero.url)  # nonce 0 == original, byte-identical
        self.assertNotEqual(base.url, one.url)

    async def test_repeated_placeholder_seed_does_not_repeat(self):
        # No scraped pool and no Pexels → both resolutions fall to the gradient
        # placeholder; the same seed must not produce the same image twice.
        r = ImageResolver(scraped_images=[], pexels=_UnconfiguredPexels())
        a = await r.resolve("team", intent="generic")
        b = await r.resolve("team", intent="generic")
        self.assertEqual(a.source, "placeholder")
        self.assertEqual(b.source, "placeholder")
        self.assertNotEqual(a.url, b.url)


if __name__ == "__main__":
    unittest.main()
