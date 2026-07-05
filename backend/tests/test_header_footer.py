import unittest

from app.models.brand import BrandIdentity
from app.services.header_footer import build_header
from app.services.theme import build_theme


class HeaderContrastTest(unittest.TestCase):
    def test_light_logo_on_light_site_keeps_light_header_and_gets_dark_lockup(self):
        brand = BrandIdentity(
            name="Acme",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#2563eb"],
            logo_is_light=True,
        )
        theme = build_theme("#2563eb")

        header = build_header(brand, theme, nav_items=[])

        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)
        chip = _find(header, "Logo lockup")
        self.assertIsNotNone(chip)
        self.assertEqual(chip.styles.get("backgroundColor"), theme.palette.secondary)


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
        header = self._header(dark=True, logo_is_light=False)
        theme = build_theme("#2563eb", color_scheme="dark")
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)
        chip = _find(header, "Logo lockup")
        self.assertIsNotNone(chip)
        self.assertEqual(chip.styles.get("backgroundColor"), "#ffffff")

    def test_dark_logo_on_light_site_has_no_lockup(self):
        header = self._header(dark=False, logo_is_light=False)
        theme = build_theme("#2563eb", color_scheme="light")
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)
        self.assertIsNone(_find(header, "Logo lockup"))

    def test_light_logo_on_dark_site_has_no_lockup(self):
        header = self._header(dark=True, logo_is_light=True)
        theme = build_theme("#2563eb", color_scheme="dark")
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)
        self.assertIsNone(_find(header, "Logo lockup"))


class HeaderThemeInkTest(unittest.TestCase):
    """Menu ink follows the theme scheme — one consistent white-or-near-black
    choice site-wide — and the header's bottom edge carries the builder's
    "Subtle" divider shadow preset, never a hard border."""

    def _brand(self):
        return BrandIdentity(name="Hope", extracted_palette=["#2563eb"])

    def test_light_theme_gets_dark_ink_on_light_header(self):
        from app.services.theme import _contrast, _relative_luminance

        theme = build_theme("#2563eb", color_scheme="light")
        header = build_header(self._brand(), theme, nav_items=[])
        bg = header.styles["backgroundColor"]
        self.assertEqual(bg, theme.palette.background)
        menu = _find_type(header, "menu")
        # Dark ink on the light header (and it clears WCAG AA).
        self.assertLess(_relative_luminance(menu.styles["color"]), 0.5)
        self.assertGreaterEqual(_contrast(bg, menu.styles["color"]), 4.5)

    def test_dark_theme_gets_white_ink_on_dark_header(self):
        theme = build_theme("#2563eb", color_scheme="dark")
        header = build_header(self._brand(), theme, nav_items=[])
        self.assertEqual(header.styles["backgroundColor"], theme.palette.background)
        menu = _find_type(header, "menu")
        self.assertEqual(menu.styles["color"], "#ffffff")

    def test_wordmark_ink_matches_menu_ink(self):
        # Typographic brand mark (no logo upload) must use the same ink as the
        # menu, not a palette colour that can vanish on a dark header.
        for scheme in ("light", "dark"):
            theme = build_theme("#2563eb", color_scheme=scheme)
            header = build_header(self._brand(), theme, nav_items=[])
            menu = _find_type(header, "menu")
            wordmark = _find(header, "Wordmark")
            self.assertIsNotNone(wordmark)
            self.assertEqual(wordmark.styles["color"], menu.styles["color"])

    def test_divider_is_subtle_shadow_preset_not_a_border(self):
        from app.services.header_footer import HEADER_DIVIDER_SUBTLE

        for scheme in ("light", "dark"):
            theme = build_theme("#2563eb", color_scheme=scheme)
            header = build_header(self._brand(), theme, nav_items=[])
            self.assertEqual(header.styles.get("boxShadow"), HEADER_DIVIDER_SUBTLE)
            self.assertNotIn("borderBottom", header.styles)


class HeaderOverlayTest(unittest.TestCase):
    """Overlay headers keep their REAL solid chrome on the element — the
    renderer strips it during the transparent phase and restores exactly these
    styles when it solidifies on scroll (pickRootBackgroundStyles). Only the
    behavior flag and the wt-header-ink markers differ from a normal header."""

    def _header(self, *, overlay, logo_is_light=None):
        brand = BrandIdentity(
            name="Hope",
            extracted_palette=["#2563eb"],
            logo_data_url="data:image/png;base64,abc" if logo_is_light is not None else None,
            logo_is_light=logo_is_light,
        )
        theme = build_theme("#2563eb", color_scheme="light")
        return theme, build_header(
            brand,
            theme,
            nav_items=[],
            primary_cta=("Get in touch", "#contact"),
            overlay=overlay,
        )

    def test_overlay_header_keeps_solid_chrome(self):
        from app.services.header_footer import HEADER_DIVIDER_SUBTLE

        theme, header = self._header(overlay=True)
        self.assertEqual(header.styles["backgroundColor"], theme.palette.background)
        self.assertEqual(header.styles.get("boxShadow"), HEADER_DIVIDER_SUBTLE)
        menu = _find_type(header, "menu")
        self.assertNotEqual(menu.styles["color"], "#ffffff")  # solid-state ink

    def test_text_bearing_elements_carry_ink_marker_but_not_the_cta(self):
        _, header = self._header(overlay=True)
        menu = _find_type(header, "menu")
        self.assertEqual(menu.classes, "wt-header-ink")
        brand_mark = _find(header, "Brand")
        self.assertEqual(brand_mark.classes, "wt-header-ink")
        cta = _find(header, "Header CTA")
        self.assertIsNotNone(cta)
        self.assertIsNone(cta.classes)

    def test_dark_logo_gets_lockup_chip_in_overlay_mode(self):
        # On a light solid header a dark logo needs no chip — but floating
        # over a dark hero it does; overlay mode forces it.
        _, solid = self._header(overlay=False, logo_is_light=False)
        self.assertIsNone(_find(solid, "Logo lockup"))
        _, overlaid = self._header(overlay=True, logo_is_light=False)
        chip = _find(overlaid, "Logo lockup")
        self.assertIsNotNone(chip)
        self.assertEqual(chip.styles.get("backgroundColor"), "#ffffff")

    def test_wrap_header_mirrors_overlay_and_reveal_offset_into_behavior(self):
        from app.services.menu_builder import wrap_header

        _, header = self._header(overlay=False)
        plain = wrap_header(header, menus=[])["behavior"]
        self.assertFalse(plain["overlay"])
        self.assertNotIn("scrollRevealOffset", plain)
        rich = wrap_header(
            header, menus=[], overlay=True, scroll_reveal_offset=80
        )["behavior"]
        self.assertTrue(rich["overlay"])
        self.assertEqual(rich["scrollRevealOffset"], 80)


def _find_type(node, type_name):
    if getattr(node, "type", None) == type_name:
        return node
    content = getattr(node, "content", None)
    if isinstance(content, list):
        for ch in content:
            found = _find_type(ch, type_name)
            if found is not None:
                return found
    return None
