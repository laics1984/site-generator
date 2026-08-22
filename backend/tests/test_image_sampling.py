"""Pixel sampling of photos (services/image_sampling.py).

Scraped photos arrive with no `dominant_color`, so before this pass every one of
them landed on the same blind mid-cast and a dead-centre crop. These tests pin
the two numbers the sampler exists to produce — the colour the scrim adapts to,
and the band the crop frames — against images whose answers are known by
construction.

The measurement functions are exercised directly (no network): `sample_photo`
itself is a thin fetch + timeout wrapper, and the suite is offline by contract
(see conftest._offline_photo_sampling).
"""

import asyncio
import unittest
from io import BytesIO

from PIL import Image, ImageDraw

from app.config import settings
from app.services.image_sampling import (
    _FOCAL_MAX,
    _FOCAL_MIN,
    _measure,
    sample_photo,
)


def _png(img: Image.Image) -> bytes:
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _flat(rgb: tuple[int, int, int], size=(400, 300)) -> bytes:
    return _png(Image.new("RGB", size, rgb))


def _detail_band(top: int, bottom: int, size=(400, 300)) -> bytes:
    """A smooth grey frame with a band of vertical stripes between two rows —
    a stand-in for the busy part of a photograph (faces, text, edges)."""
    img = Image.new("RGB", size, (200, 200, 200))
    draw = ImageDraw.Draw(img)
    for x in range(0, size[0], 6):
        draw.line([(x, top), (x, bottom)], fill=(10, 10, 10), width=2)
    return _png(img)


class DominantColourTest(unittest.TestCase):
    def test_reads_a_flat_colour_exactly(self):
        self.assertEqual(_measure(_flat((20, 30, 60))).dominant_hex, "#141e3c")
        self.assertEqual(_measure(_flat((240, 235, 225))).dominant_hex, "#f0ebe1")

    def test_luminance_separates_dark_from_bright_photos(self):
        dark = _measure(_flat((20, 30, 60)))
        bright = _measure(_flat((240, 235, 225)))
        self.assertLess(dark.luminance, 0.1)
        self.assertGreater(bright.luminance, 0.7)
        # The band is what the schema_builder luminance pass consumes.
        self.assertEqual(dark.band, "dark")
        self.assertEqual(bright.band, "light")

    def test_median_resists_a_large_bright_region(self):
        """Mean would be dragged toward whichever region is largest; the scrim
        should adapt to the body of the image, not to a blown-out sky."""
        img = Image.new("RGB", (400, 300), (250, 250, 250))  # sky, top 25%
        ImageDraw.Draw(img).rectangle([0, 75, 400, 300], fill=(40, 60, 30))
        sample = _measure(_png(img))
        self.assertEqual(sample.band, "dark")  # the ground, which is 75% of it
        self.assertLess(sample.luminance, 0.2)

    def test_undecodable_bytes_are_skipped_not_raised(self):
        self.assertIsNone(_measure(b"this is not an image"))
        self.assertIsNone(_measure(b""))


class FocalPointTest(unittest.TestCase):
    def test_detail_in_the_upper_third_pulls_the_crop_up(self):
        sample = _measure(_detail_band(20, 90))
        self.assertLessEqual(sample.focal_y, 0.35)

    def test_detail_in_the_lower_third_pulls_the_crop_down(self):
        sample = _measure(_detail_band(210, 280))
        self.assertGreaterEqual(sample.focal_y, 0.55)

    def test_a_structureless_frame_falls_back_to_centre(self):
        """A flat colour field or a smooth gradient carries no subject, which is
        exactly the case where centring was already the right answer."""
        self.assertEqual(_measure(_flat((128, 128, 128))).focal_y, 0.5)

    def test_focal_point_stays_inside_the_usable_band(self):
        # Detail hard against the top edge must not crop the subject under the
        # header, nor (at the bottom) behind the copy.
        for top, bottom in ((0, 12), (288, 300)):
            focal = _measure(_detail_band(top, bottom)).focal_y
            self.assertGreaterEqual(focal, _FOCAL_MIN)
            self.assertLessEqual(focal, _FOCAL_MAX)


class SamplePhotoGateTest(unittest.TestCase):
    def test_disabled_setting_short_circuits_before_any_fetch(self):
        # conftest disables sampling for the whole suite; this pins that the
        # flag is honoured rather than merely making fetches fail.
        self.assertFalse(settings.photo_sampling_enabled)
        self.assertIsNone(asyncio.run(sample_photo("https://cdn.example.com/x.jpg")))

    def test_unfetchable_url_returns_none_without_raising(self):
        original = settings.photo_sampling_enabled
        settings.photo_sampling_enabled = True
        try:
            # Not http(s) and not a data: URL → the fetcher rejects it outright,
            # no network, no exception.
            self.assertIsNone(asyncio.run(sample_photo("ftp://example.com/x.jpg")))
        finally:
            settings.photo_sampling_enabled = original

    def test_data_uri_is_sampled_without_network(self):
        import base64

        original = settings.photo_sampling_enabled
        settings.photo_sampling_enabled = True
        try:
            payload = base64.b64encode(_flat((20, 30, 60))).decode()
            sample = asyncio.run(sample_photo(f"data:image/png;base64,{payload}"))
            self.assertIsNotNone(sample)
            self.assertEqual(sample.dominant_hex, "#141e3c")
        finally:
            settings.photo_sampling_enabled = original


if __name__ == "__main__":
    unittest.main()
