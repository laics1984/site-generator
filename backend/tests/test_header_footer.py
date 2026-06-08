import unittest

from app.models.brand import BrandIdentity
from app.services.header_footer import build_header
from app.services.theme import build_theme


class HeaderContrastTest(unittest.TestCase):
    def test_build_header_uses_dark_background_for_light_logo(self):
        brand = BrandIdentity(
            name="Acme",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#2563eb"],
            logo_is_light=True,
        )
        theme = build_theme("#2563eb")

        header = build_header(brand, theme, nav_items=[])

        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.secondary)
        self.assertNotEqual(header.styles.get("backgroundColor"), theme.palette.background)
