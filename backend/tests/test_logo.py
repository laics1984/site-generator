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
