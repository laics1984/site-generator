import io
import unittest

from PIL import Image

from app.services.logo import extract_palette_from_image_bytes


def _png_bytes(color: tuple[int, int, int, int]) -> bytes:
    image = Image.new("RGBA", (32, 32), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


class LogoExtractionTest(unittest.TestCase):
    def test_extract_palette_handles_single_color_logo(self):
        image_bytes = _png_bytes((37, 99, 235, 255))

        extraction = extract_palette_from_image_bytes(image_bytes)

        self.assertEqual(extraction.seed_hex, "#2563eb")
        self.assertEqual(extraction.palette, ["#2563eb"])
        self.assertTrue(extraction.logo_data_url.startswith("data:image/png;base64,"))

    def test_extract_palette_marks_white_logo_as_light(self):
        image_bytes = _png_bytes((255, 255, 255, 255))

        extraction = extract_palette_from_image_bytes(image_bytes)

        self.assertTrue(extraction.logo_is_light)
        self.assertEqual(extraction.palette, ["#2563eb"])


class SvgLogoExtractionTest(unittest.TestCase):
    def test_extract_palette_from_svg_uses_markup_colors(self):
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
            b'<rect width="100" height="100" fill="#ffffff"/>'
            b'<path d="M0 0h100v100H0z" fill="#ff6600"/>'
            b"</svg>"
        )

        extraction = extract_palette_from_image_bytes(svg)

        self.assertEqual(extraction.seed_hex, "#ff6600")
        self.assertEqual(extraction.palette, ["#ff6600"])
        self.assertTrue(extraction.logo_data_url.startswith("data:image/svg+xml;base64,"))

    def test_extract_palette_from_svg_with_xml_declaration_and_css_fill(self):
        svg = (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            b"<style>.brand{fill:#06c;}</style>"
            b'<path class="brand" d="M0 0h10v10H0z"/>'
            b"</svg>"
        )

        extraction = extract_palette_from_image_bytes(svg)

        self.assertEqual(extraction.seed_hex, "#0066cc")

    def test_extract_palette_from_svg_marks_predominantly_light_logo(self):
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            b'<rect width="10" height="10" fill="#ffffff"/>'
            b'<rect width="10" height="2" fill="#ffffff"/>'
            b'<rect width="10" height="2" fill="#ffffff"/>'
            b'<path d="M2 2h6v6H2z" fill="#1a73e8"/>'
            b"</svg>"
        )

        extraction = extract_palette_from_image_bytes(svg)

        self.assertTrue(extraction.logo_is_light)
        self.assertEqual(extraction.palette, ["#1a73e8"])

    def test_extract_palette_from_svg_with_no_literal_colors_falls_back(self):
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
            b'<path d="M0 0h10v10H0z" fill="currentColor"/>'
            b"</svg>"
        )

        extraction = extract_palette_from_image_bytes(svg)

        self.assertEqual(extraction.palette, ["#2563eb"])
        self.assertFalse(extraction.logo_is_light)
        self.assertTrue(extraction.logo_data_url.startswith("data:image/svg+xml;base64,"))
