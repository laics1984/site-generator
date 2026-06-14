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


def _find(node, name):
    if getattr(node, "name", None) == name:
        return node
    content = getattr(node, "content", None)
    if isinstance(content, list):
        for ch in content:
            found = _find(ch, name)
            if found is not None:
                return found
    return None


class HeaderLogoLockupTest(unittest.TestCase):
    def _header(self, *, dark, logo_is_light):
        brand = BrandIdentity(
            name="Acme",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#2563eb"],
            logo_is_light=logo_is_light,
        )
        theme = build_theme("#2563eb", color_scheme="dark" if dark else "light")
        return build_header(brand, theme, nav_items=[])

    def test_dark_logo_on_dark_site_gets_light_lockup(self):
        # SOP: keep the dark header, give the dark logo a light chip — no light bar.
        header = self._header(dark=True, logo_is_light=False)
        self.assertNotEqual(header.styles.get("backgroundColor"), "#ffffff")
        chip = _find(header, "Logo lockup")
        self.assertIsNotNone(chip)
        self.assertEqual(chip.styles.get("backgroundColor"), "#ffffff")

    def test_dark_logo_on_light_site_has_no_lockup(self):
        self.assertIsNone(_find(self._header(dark=False, logo_is_light=False), "Logo lockup"))

    def test_light_logo_on_dark_site_has_no_lockup(self):
        # Light logo already pops on the dark header bar — no chip needed.
        header = self._header(dark=True, logo_is_light=True)
        self.assertIsNone(_find(header, "Logo lockup"))
