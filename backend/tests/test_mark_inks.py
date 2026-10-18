"""A logo is judged by every ink a reader sees, not by its brand colours.

watr's mark is white letters around a red flame. `extract_palette_from_image_bytes`
drops near-white on purpose (white is nobody's brand colour), so the header and
footer judged the mark by its red alone — 6:1 on cream — and shipped the white
letters invisible on a cream bar. These build the brand the way production does:
the real extractor's palette plus the logo bytes in `logo_data_url`.
"""

import base64
import io
import unittest

from PIL import Image, ImageDraw

from app.models.brand import BrandIdentity
from app.models.builder_schema import PageNode
from app.services.header_footer import build_footer, build_header
from app.services.logo import extract_palette_from_image_bytes, mark_inks
from app.services.theme import _contrast, build_theme

WHITE = (255, 255, 255, 255)
RED = (185, 28, 28, 255)


def _png(*parts):
    """A transparent 240x80 mark; each part is (box, rgba)."""
    img = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for box, colour in parts:
        draw.rectangle(box, fill=colour)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


# White "letters" either side of a red "flame", roughly watr's proportions.
WHITE_AND_RED = _png(((0, 10, 90, 70), WHITE), ((150, 10, 239, 70), WHITE),
                     ((100, 5, 140, 75), RED))
RED_ONLY = _png(((100, 5, 140, 75), RED))


def _brand(png):
    extraction = extract_palette_from_image_bytes(png)
    return BrandIdentity(
        name="WATR",
        logo_url="https://example.com/logo.png",
        logo_data_url=extraction.logo_data_url,
        extracted_palette=extraction.palette,
        logo_is_light=extraction.logo_is_light,
        logo_source="logo",
        logo_render_ok=True,
    )


def _find(node, name):
    if getattr(node, "name", None) == name:
        return node
    for ch in node.content if isinstance(getattr(node, "content", None), list) else []:
        found = _find(ch, name)
        if found is not None:
            return found
    return None


class MarkInksTest(unittest.TestCase):
    def test_white_is_an_ink_even_though_it_is_not_a_brand_colour(self):
        inks = dict(mark_inks(WHITE_AND_RED))
        self.assertIn("#ffffff", inks)
        self.assertAlmostEqual(sum(inks.values()), 1.0, places=3)
        # The brand palette still leaves white out — that is its job.
        self.assertNotIn("#ffffff", extract_palette_from_image_bytes(WHITE_AND_RED).palette)

    def test_an_svg_counts_its_declared_colours(self):
        svg = b'<svg xmlns="http://www.w3.org/2000/svg"><path fill="#fff"/><path fill="#fff"/><path fill="#b91c1c"/></svg>'
        inks = dict(mark_inks(svg))
        self.assertAlmostEqual(inks["#ffffff"], 2 / 3, places=3)

    def test_unreadable_bytes_have_no_inks(self):
        self.assertEqual(mark_inks(b"not an image"), [])


class TwoToneMarkLeadsTheChromeTest(unittest.TestCase):
    def test_a_white_and_red_mark_puts_the_header_on_a_dark_bar(self):
        theme = build_theme("#b91c1c", color_scheme="light")
        header = build_header(_brand(WHITE_AND_RED), theme, nav_items=[])
        band = header.styles.get("backgroundColor")
        self.assertNotEqual(band, theme.palette.background)
        self.assertGreaterEqual(_contrast("#ffffff", band), 3.0)
        self.assertIsNotNone(_find(header, "Brand Logo"))

    def test_a_white_and_red_mark_puts_the_footer_on_a_dark_band_and_keeps_the_logo(self):
        theme = build_theme("#b91c1c", color_scheme="light")
        footer = build_footer(_brand(WHITE_AND_RED), theme, nav_items=[],
                              page_tree=[PageNode(slug="about", title="About")],
                              archetype="minimal-centered")
        band = footer.styles.get("backgroundColor")
        self.assertGreaterEqual(_contrast("#ffffff", band), 3.0)
        self.assertIsNotNone(_find(footer, "Brand Logo"))
        self.assertIsNone(_find(footer, "Brand"))

    def test_a_red_only_mark_keeps_the_light_bar(self):
        theme = build_theme("#b91c1c", color_scheme="light")
        header = build_header(_brand(RED_ONLY), theme, nav_items=[])
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)

    def test_a_data_url_that_is_not_base64_is_still_read(self):
        svg = '<svg xmlns="http://www.w3.org/2000/svg"><path fill="%23ffffff"/></svg>'
        brand = BrandIdentity(name="W", logo_data_url=f"data:image/svg+xml;utf8,{svg}",
                              extracted_palette=[], logo_render_ok=True)
        theme = build_theme("#b91c1c", color_scheme="light")
        header = build_header(brand, theme, nav_items=[])
        self.assertGreaterEqual(_contrast("#ffffff", header.styles["backgroundColor"]), 3.0)


if __name__ == "__main__":
    unittest.main()
