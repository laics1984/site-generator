"""Pixel graphic screening — see services/image_graphics.py.

The images here are SYNTHESIZED to the same shapes the real measurements found
(a flat wordmark on transparency, an opaque photograph, and the case that makes
this a two-part test: a photograph someone cut out of its background).
"""

import asyncio
import unittest
from io import BytesIO

from PIL import Image, ImageDraw

from app.config import settings
from app.models.content_blocks import ImageMetadata
from app.services import image_graphics
from app.services.image_graphics import (
    is_flat_graphic,
    measure,
    screen_source_images_for_graphics,
)


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _wordmark() -> bytes:
    """A flat two-colour mark on transparency — what a logo actually is."""
    img = Image.new("RGBA", (562, 129), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 40, 260, 90], fill=(74, 168, 50, 255))
    draw.rectangle([300, 40, 540, 90], fill=(255, 255, 255, 255))
    return _png(img)


def _photograph(mode: str = "RGB") -> Image.Image:
    """Every pixel a different colour — a photograph's defining property at
    this sample size, and the opposite of a wordmark's handful of flats."""
    img = Image.new(mode, (400, 300))
    px = img.load()
    for y in range(300):
        for x in range(400):
            value = (x * 7 % 256, (x + y * 3) % 256, (y * 11 + x) % 256)
            px[x, y] = value if mode == "RGB" else (*value, 255)
    return img


def _cutout() -> bytes:
    """A real photograph with its background erased — transparent AND detailed.
    The case transparency alone would wrongly withdraw."""
    img = _photograph("RGBA")
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).ellipse([60, 45, 340, 255], fill=255)
    img.putalpha(mask)
    return _png(img)


class MeasurementTest(unittest.TestCase):
    def test_a_wordmark_is_transparent_and_flat(self):
        clear, distinct = measure(_wordmark())

        self.assertGreater(clear, 0.5)
        self.assertLess(distinct, 0.1)
        self.assertTrue(is_flat_graphic(clear, distinct))

    def test_a_photograph_is_opaque_and_varied(self):
        clear, distinct = measure(_png(_photograph()))

        self.assertEqual(clear, 0.0)
        self.assertGreater(distinct, 0.5)
        self.assertFalse(is_flat_graphic(clear, distinct))

    def test_a_cut_out_photograph_is_transparent_but_not_flat(self):
        """The whole reason flatness is part of the test. A product cutout is
        as transparent as a wordmark and must stay in the pool."""
        clear, distinct = measure(_cutout())

        self.assertGreater(clear, 0.4)  # as clear as the wordmark
        self.assertGreater(distinct, 0.4)  # but nothing like as flat
        self.assertFalse(is_flat_graphic(clear, distinct))

    def test_either_signal_alone_is_never_enough(self):
        self.assertFalse(is_flat_graphic(0.0, 0.01))  # flat but opaque
        self.assertFalse(is_flat_graphic(0.9, 0.9))  # clear but detailed
        self.assertTrue(is_flat_graphic(0.9, 0.01))

    def test_undecodable_bytes_are_skipped_not_raised(self):
        self.assertIsNone(measure(b"not an image"))
        self.assertIsNone(measure(b""))

    def test_a_sliver_of_visible_pixels_is_not_judged(self):
        """A nearly-empty frame is trivially 'flat'; the ratio means nothing."""
        img = Image.new("RGBA", (400, 400), (0, 0, 0, 0))
        ImageDraw.Draw(img).rectangle([0, 0, 12, 12], fill=(20, 20, 20, 255))

        self.assertIsNone(measure(_png(img)))


class ScreenTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings.graphic_detection_enabled = True
        image_graphics._GRAPHIC_CACHE.clear()
        self.addCleanup(image_graphics._GRAPHIC_CACHE.clear)
        self.addCleanup(setattr, settings, "graphic_detection_enabled", False)

    def _patch_fetch(self, payloads: dict[str, bytes]):
        import app.services.image_vision as vision

        async def _fake(url, client=None):
            return payloads.get(url)

        original = vision._fetch_image_bytes
        vision._fetch_image_bytes = _fake
        self.addCleanup(setattr, vision, "_fetch_image_bytes", original)

    async def test_a_wordmark_is_withdrawn_from_the_photo_pool(self):
        """The webtree.my case: the site's only image is its logo, so the
        page-local size fallback hands it to every photo slot on the site."""
        pool = [ImageMetadata(url="https://x.test/mark.png", intent="generic", role="content")]
        self._patch_fetch({"https://x.test/mark.png": _wordmark()})

        await screen_source_images_for_graphics(pool)

        self.assertEqual(pool[0].role, "logo")

    async def test_a_photograph_keeps_its_measured_role(self):
        pool = [ImageMetadata(url="https://x.test/team.jpg", intent="about", role="content")]
        self._patch_fetch({"https://x.test/team.jpg": _png(_photograph())})

        await screen_source_images_for_graphics(pool)

        self.assertEqual(pool[0].role, "content")

    async def test_a_logo_wall_tile_is_never_screened(self):
        """A partner/award wall IS the section's content, and its tiles are flat
        transparent graphics by definition — the same exception classify_role
        and logo_extraction._in_logo_wall already make."""
        pool = [
            ImageMetadata(url=f"https://x.test/p{i}.png", intent="generic", role="gallery")
            for i in range(4)
        ]
        self._patch_fetch({m.url: _wordmark() for m in pool})

        await screen_source_images_for_graphics(pool)

        self.assertEqual([m.role for m in pool], ["gallery"] * 4)

    async def test_the_flag_off_is_a_true_no_op(self):
        settings.graphic_detection_enabled = False
        pool = [ImageMetadata(url="https://x.test/mark.png", intent="generic", role="content")]
        self._patch_fetch({"https://x.test/mark.png": _wordmark()})

        self.assertEqual(await screen_source_images_for_graphics(pool), {})
        self.assertEqual(pool[0].role, "content")

    async def test_an_unfetchable_image_leaves_the_pool_untouched(self):
        pool = [ImageMetadata(url="https://x.test/gone.png", intent="about", role="content")]
        self._patch_fetch({})

        await screen_source_images_for_graphics(pool)

        self.assertEqual(pool[0].role, "content")

    async def test_a_verdict_is_cached_so_a_regeneration_refetches_nothing(self):
        calls: list[str] = []
        import app.services.image_vision as vision

        async def _counting(url, client=None):
            calls.append(url)
            return _wordmark()

        original = vision._fetch_image_bytes
        vision._fetch_image_bytes = _counting
        self.addCleanup(setattr, vision, "_fetch_image_bytes", original)

        for _ in range(3):
            pool = [
                ImageMetadata(url="https://x.test/mark.png", intent="generic", role="content")
            ]
            await screen_source_images_for_graphics(pool)
            self.assertEqual(pool[0].role, "logo")

        self.assertEqual(len(calls), 1)

    async def test_the_cap_is_honoured(self):
        pool = [
            ImageMetadata(url=f"https://x.test/{i}.png", intent="generic", role="content")
            for i in range(6)
        ]
        self._patch_fetch({m.url: _wordmark() for m in pool})

        await screen_source_images_for_graphics(pool, max_images=2)

        self.assertEqual(sum(1 for m in pool if m.role == "logo"), 2)


if __name__ == "__main__":
    unittest.main()
