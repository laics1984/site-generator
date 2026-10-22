"""A browser-printed PDF's rendering artefacts never become the site's pictures.

Fowable's brief (Chrome → PDF, 29 pages) carried eight "images" and not one
photograph: every CSS box-shadow came through as a blurred grey mask and every
hatched "photo placeholder" box as a pattern tile, so page after page published
a blank grey rectangle as its photo. Its cover mark lost its soft mask (white
letters on a black box, palette read as six greys), and the brand's deep green
— painted on every page, but not in the white logo — never reached the palette.
"""

from __future__ import annotations

import io
import unittest

import fitz
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from app.routers.document import _build_preview
from app.services.doc_parser import parse_pdf
from app.services.photo_quality import depicts_nothing


def _encode(image: Image.Image, fmt: str = "JPEG") -> bytes:
    buf = io.BytesIO()
    image.save(buf, format=fmt)
    return buf.getvalue()


def _shadow_mask(size=(1200, 860)) -> bytes:
    """What Skia writes for `box-shadow`: a blurred white card on black, greyscale."""
    image = Image.new("L", size, 0)
    ImageDraw.Draw(image).rectangle((150, 150, size[0] - 150, size[1] - 150), fill=255)
    return _encode(image.filter(ImageFilter.GaussianBlur(40)))


def _placeholder_hatch(size=(2100, 350)) -> bytes:
    """A `repeating-linear-gradient` placeholder: faint cream diagonal stripes."""
    y, x = np.mgrid[0 : size[1], 0 : size[0]]
    stripe = ((x + y) // 14) % 2 == 0
    rgb = np.where(stripe[..., None], (241, 238, 231), (247, 247, 242)).astype("uint8")
    return _encode(Image.fromarray(rgb), "PNG")


def _photo(size=(900, 600), seed=3) -> bytes:
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0 : size[1], 0 : size[0]]
    grey = (np.sin(x / 31.0) + np.cos(y / 19.0)) * 45 + 128 + rng.normal(0, 18, (size[1], size[0]))
    rgb = np.stack([grey, grey * 0.85 + 20, grey * 0.7], axis=-1).clip(0, 255).astype("uint8")
    return _encode(Image.fromarray(rgb))


def _calm_photo(size=(900, 600), seed=5) -> bytes:
    """As featureless as a photograph gets — a calm sea under a hazy sky: a
    smooth gradient, a soft swell and sensor noise, no edge anywhere. Measured
    on Pexels: Laplacian 5.4, under the floor, but colour variety 0.54, ten
    times the shadow mask's."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0 : size[1], 0 : size[0]]
    y = yy / size[1]
    swell = 5 * np.sin(xx / 23.0 + 2 * np.sin(yy / 17.0)) * y
    noise = rng.normal(0, 3.0, (size[1], size[0], 3))
    rgb = np.stack([120 + 90 * y, 160 + 60 * y, 200 + 30 * y], axis=-1) + swell[..., None] + noise
    return _encode(Image.fromarray(rgb.clip(0, 255).astype("uint8")))


def _diagram(size=(900, 500)) -> bytes:
    """A flat two-colour chart: few colours, but sharp edges — content."""
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for i, height in enumerate((120, 260, 180, 340)):
        draw.rectangle((80 + i * 190, size[1] - 40 - height, 200 + i * 190, size[1] - 40), fill="#1d4ed8")
    draw.line((60, size[1] - 40, size[0] - 40, size[1] - 40), fill="black", width=3)
    return _encode(image, "PNG")


class DepictsNothingTest(unittest.TestCase):
    def test_a_box_shadow_mask_shows_nothing(self):
        self.assertTrue(depicts_nothing(_shadow_mask()))

    def test_a_placeholder_hatch_shows_nothing(self):
        self.assertTrue(depicts_nothing(_placeholder_hatch()))

    def test_photographs_and_diagrams_are_kept(self):
        for name, raw in (("photo", _photo()), ("calm photo", _calm_photo()), ("diagram", _diagram())):
            with self.subTest(name):
                self.assertFalse(depicts_nothing(raw))

    def test_undecodable_bytes_are_not_judged(self):
        self.assertFalse(depicts_nothing(b"not an image"))


def _brief_pdf() -> bytes:
    """A designed brief in miniature: green cover band, white wordmark drawn
    through a soft mask, a shadowed card, a placeholder box and one photo."""
    doc = fitz.open()
    cover = doc.new_page()
    cover.draw_rect(cover.rect, fill=(14 / 255, 68 / 255, 47 / 255), color=None)
    mark = Image.new("RGBA", (600, 130), (0, 0, 0, 0))
    ImageDraw.Draw(mark).rectangle((20, 20, 580, 110), fill=(255, 255, 255, 255))
    cover.insert_image(fitz.Rect(50, 50, 194, 81), stream=_encode(mark, "PNG"))
    cover.insert_text((50, 200), "Helping your kindergarten grow", fontsize=30, color=(1, 1, 1))

    body = doc.new_page()
    body.draw_rect(fitz.Rect(40, 40, 560, 300), fill=(0.6, 0.6, 0.6), color=None)  # a grey card: no hue
    body.insert_text((50, 330), "Our programmes", fontsize=24)
    body.insert_text((50, 360), "Parents find fees, hours and age groups in one place. " * 2, fontsize=10)
    body.insert_image(fitz.Rect(50, 380, 300, 560), stream=_shadow_mask())
    body.insert_image(fitz.Rect(50, 570, 560, 640), stream=_placeholder_hatch())
    body.insert_image(fitz.Rect(320, 380, 560, 540), stream=_photo())
    body.insert_text((50, 700), "Every enquiry is kept safe and your team is alerted. " * 2, fontsize=10)
    raw = doc.tobytes()
    doc.close()
    return raw


class BrowserPrintedBriefTest(unittest.TestCase):
    def setUp(self):
        self.raw = _brief_pdf()

    def test_only_the_photograph_reaches_the_pages(self):
        source = _build_preview(parse_pdf(self.raw, "brief.pdf"), None)["source_content"]
        pages = [source, *source["discovered_pages"]]
        placed = [url for page in pages for url in page["images"]]
        self.assertEqual(len(placed), 1)

    def test_a_soft_masked_mark_keeps_its_transparency(self):
        logo = next(img for img in parse_pdf(self.raw, "brief.pdf").images if img.height < 256)
        with Image.open(io.BytesIO(logo.data)) as image:
            self.assertIn("A", image.getbands())
            self.assertEqual(image.getchannel("A").getextrema()[0], 0)

    def test_the_documents_painted_brand_colour_is_observed(self):
        colors = parse_pdf(self.raw, "brief.pdf").reference_colors
        self.assertEqual(colors[:1], ["#0e442f"])
        self.assertNotIn("#999999", colors)  # a neutral card says nothing about the brand
        source = _build_preview(parse_pdf(self.raw, "brief.pdf"), None)["source_content"]
        self.assertEqual(source["reference_colors"][:1], ["#0e442f"])


if __name__ == "__main__":
    unittest.main()
