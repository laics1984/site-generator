"""Tests for the generated-site visual fixes: on-brand mesh gradient, scheme-aware
glass cards, and orphan-free card grids."""

import asyncio
import unittest

from app.models.builder_schema import BuilderElement
from app.services.image_styling import color_distance
from app.services.schema_builder import (
    apply_section_dividers,
    cap_gradient_textures,
    glass_card_styles,
    mesh_gradient,
    modernize_sections,
)
from app.services.template_filler import fill_template
from app.services.theme import _adjust_lightness, _hex_to_rgb, build_theme


def _rgba_prefix(hex_color: str) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    return f"rgba({r}, {g}, {b},"


class MeshGradientTest(unittest.TestCase):
    def test_mesh_is_monochromatic_no_clashing_accent(self):
        theme = build_theme("#d55d62", color_scheme="dark")
        p = theme.palette
        g = mesh_gradient(p)
        # On-brand: the primary and a lighter sibling appear; the split-complementary
        # accent (the muddy red/green clash) does not.
        self.assertIn(_rgba_prefix(p.primary), g)
        self.assertIn(_rgba_prefix(_adjust_lightness(p.primary, 0.18)), g)
        self.assertNotIn(_rgba_prefix(p.accent), g)


class GlassCardTest(unittest.TestCase):
    def test_light_scheme_keeps_white_pane(self):
        glass = glass_card_styles(build_theme("#2563eb", color_scheme="light"))
        self.assertEqual(glass["backgroundColor"], "rgba(255, 255, 255, 0.62)")

    def test_dark_scheme_uses_dark_glass_and_light_hairline(self):
        glass = glass_card_styles(build_theme("#2563eb", color_scheme="dark"))
        # A faint light film over the dark page (not an opaque white island), with a
        # light hairline so the edge reads on dark.
        self.assertEqual(glass["backgroundColor"], "rgba(255, 255, 255, 0.06)")
        self.assertIn("255, 255, 255", glass["border"])


class GridFitTest(unittest.TestCase):
    """$gridFit must never strand a lone card in the last row of a 3-wide grid."""

    @staticmethod
    async def _ri(query):
        return ("x.jpg", None)

    def _grid_type(self, count: int) -> str:
        template = {
            "id": "t",
            "tree": {
                "name": "grid",
                "type": "container",
                "styles": {},
                "$repeat": "items",
                "$gridFit": True,
                "content": [
                    {
                        "name": "card",
                        "type": "container",
                        "styles": {},
                        "content": [
                            {"name": "title", "type": "text", "styles": {}, "$slot": "title"}
                        ],
                    }
                ],
            },
        }
        content = {"items": [{"title": f"c{i}"} for i in range(count)]}
        root = asyncio.run(fill_template(template, content, resolve_image=self._ri))
        return root.type

    def test_four_items_use_two_columns(self):
        self.assertEqual(self._grid_type(4), "2Col")  # 2×2, not 3 + 1

    def test_three_and_six_use_three_columns(self):
        self.assertEqual(self._grid_type(3), "3Col")
        self.assertEqual(self._grid_type(6), "3Col")

    def test_five_uses_three_columns_balanced_pair(self):
        self.assertEqual(self._grid_type(5), "3Col")  # 3 + 2, no orphan

    def test_seven_drops_to_two_columns(self):
        self.assertEqual(self._grid_type(7), "2Col")  # avoid 3 + 3 + 1

    def test_two_items_use_two_columns(self):
        self.assertEqual(self._grid_type(2), "2Col")


class DividerMeshTest(unittest.TestCase):
    """A shaped divider must sit only against SOLID colour: both neighbouring
    sections are flattened (any mesh/grain dropped) and the seam fill is a plain
    colour. The texture accent is steered onto a non-neighbour band by
    modernize_sections, so it can still appear elsewhere on the page."""

    @staticmethod
    def _section(name, bg):
        return BuilderElement(
            name=name, type="section",
            styles={"backgroundColor": bg, "width": "100%"}, content=[],
        )

    def test_only_one_plain_section_is_decorated(self):
        # Clean-UI policy: texture is an accent, not a blanket. Exactly one plain
        # section per page carries it (the first eligible band); the rest stay
        # flat — both visually and in their honest backgroundTexture tag.
        theme = build_theme("#2563eb").model_copy(update={"background_strategy": "mesh"})
        page_bg = theme.page.background
        s1 = self._section("S1", page_bg)
        s2 = self._section("S2", page_bg)
        s3 = self._section("S3", page_bg)

        modernize_sections([s1, s2, s3], theme)

        decorated = [s for s in (s1, s2, s3) if "backgroundImage" in s.styles]
        self.assertEqual(len(decorated), 1)
        self.assertIs(decorated[0], s1)  # first eligible band wins
        self.assertEqual(s1.backgroundTexture, "mesh")
        self.assertEqual(s2.backgroundTexture, "flat")
        self.assertEqual(s3.backgroundTexture, "flat")
        self.assertNotIn("backgroundImage", s2.styles)
        self.assertNotIn("backgroundImage", s3.styles)

    def test_shaped_divider_neighbours_are_flat_and_seam_is_solid(self):
        theme = build_theme("#2563eb").model_copy(update={"background_strategy": "mesh"})
        page_bg = theme.page.background
        hero = self._section("Hero", page_bg)
        content = self._section("Features", page_bg)  # the revealed neighbour
        cta = self._section("CTA", page_bg)

        # Real build order: modernize tags textures, then dividers enforce flat.
        modernize_sections([hero, content, cta], theme)
        apply_section_dividers([hero, content, cta], "modern")

        # The hero→content seam is solid: no texture on the edge, neighbour flat.
        self.assertIsNone(hero.divider.bottom.texture)
        self.assertEqual(hero.divider.bottom.color, content.styles["backgroundColor"])
        self.assertEqual(content.backgroundTexture, "flat")
        self.assertNotIn("backgroundImage", content.styles)
        # The content→CTA seam too.
        self.assertIsNone(cta.divider.top.texture)
        self.assertEqual(cta.backgroundTexture, "flat")

    def test_texture_accent_avoids_divider_neighbours(self):
        # Five sections: hero/CTA seams claim {0,1} and {3,4}; the lone texture
        # accent must land on the only non-neighbour band (index 2).
        theme = build_theme("#2563eb").model_copy(update={"background_strategy": "mesh"})
        page_bg = theme.page.background
        secs = [
            self._section("Hero", page_bg),
            self._section("Features", page_bg),
            self._section("Services", page_bg),
            self._section("About", page_bg),
            self._section("CTA", page_bg),
        ]

        modernize_sections(secs, theme)
        apply_section_dividers(secs, "modern")

        # Neighbours stay flat; the non-neighbour keeps the mesh accent.
        for i in (0, 1, 3, 4):
            self.assertEqual(secs[i].backgroundTexture, "flat", f"section {i}")
            self.assertNotIn("backgroundImage", secs[i].styles)
        self.assertEqual(secs[2].backgroundTexture, "mesh")
        self.assertIn("backgroundImage", secs[2].styles)
        self.assertIsNone(secs[0].divider.bottom.texture)
        self.assertIsNone(secs[4].divider.top.texture)

    def test_divider_texture_is_none_when_revealed_section_is_flat(self):
        theme = build_theme("#2563eb").model_copy(update={"background_strategy": "flat"})
        page_bg = theme.page.background
        hero = self._section("Hero", page_bg)
        revealed = self._section("Revealed", page_bg)

        modernize_sections([hero, revealed], theme)
        apply_section_dividers([hero, revealed], "modern")

        self.assertIsNone(hero.divider.bottom.texture)


class ColorDistanceTest(unittest.TestCase):
    """color_distance ranks abstract candidates by closeness to the theme."""

    def test_same_hue_beats_clashing_hue(self):
        theme_blue = "#2563eb"
        near = color_distance("#1e40af", theme_blue)   # a deeper blue
        clash = color_distance("#dc2626", theme_blue)  # red, ~opposite hue
        self.assertLess(near, clash)

    def test_neutral_is_hue_agnostic(self):
        # A near-grey texture is judged on luminance only, so it never loses to a
        # saturated off-hue candidate purely on hue.
        theme = "#2563eb"
        grey = color_distance("#9ca3af", theme)
        off_hue = color_distance("#16a34a", theme)  # saturated green
        self.assertLess(grey, off_hue)


class CapGradientTexturesTest(unittest.TestCase):
    """At most one pure gradient/texture section survives per page; the rest are
    flattened to a solid on-brand band. Photos are never touched."""

    @staticmethod
    def _section(name, styles, content=None):
        return BuilderElement(
            name=name, type="section",
            styles={"width": "100%", **styles}, content=content or [],
        )

    def test_keeps_first_gradient_flattens_later_ones(self):
        theme = build_theme("#2563eb")
        g1 = self._section("Hero Gradient", {"background": "linear-gradient(135deg,#0f172a,#2563eb)"})
        g2 = self._section("CTA Gradient", {"background": "radial-gradient(90% 140% at 85% 0%,#fff,#2563eb)"})

        cap_gradient_textures([g1, g2], theme)

        # First survives as the single accent.
        self.assertIn("background", g1.styles)
        # Second is repainted as a solid band with contrast-correct text.
        self.assertNotIn("background", g2.styles)
        self.assertIn("backgroundColor", g2.styles)
        self.assertIn("color", g2.styles)
        self.assertEqual(g2.backgroundTexture, "flat")

    def test_nested_gradient_band_is_detected_and_flattened(self):
        # A CTA banner paints its gradient on an inner wrapper; the section root is
        # the plain page colour. It must still count and flatten.
        theme = build_theme("#2563eb")
        first = self._section("Hero Gradient", {"background": "linear-gradient(135deg,#0f172a,#2563eb)"})
        inner = BuilderElement(
            name="Banner", type="container",
            styles={"background": "linear-gradient(135deg,#0f172a,#2563eb)"}, content=[],
        )
        banner = self._section("CTA Banner", {"backgroundColor": "#ffffff"}, content=[inner])

        cap_gradient_textures([first, banner], theme)

        self.assertNotIn("background", inner.styles)  # nested gradient stripped
        self.assertEqual(banner.backgroundTexture, "flat")

    def test_photo_section_is_exempt(self):
        # A full-bleed photo hero (real url under a brand overlay) is a photo, not a
        # texture — it never counts against the budget and is left untouched.
        theme = build_theme("#2563eb")
        gradient = self._section("Hero Gradient", {"background": "linear-gradient(135deg,#0f172a,#2563eb)"})
        photo = self._section(
            "Photo Hero",
            {"backgroundImage": "linear-gradient(rgba(0,0,0,.4),rgba(0,0,0,.4)), url('https://x/p.jpg')"},
        )

        cap_gradient_textures([gradient, photo], theme)

        # Photo keeps its image even though it is the second texture-ish section.
        self.assertIn("url('https://x/p.jpg')", photo.styles["backgroundImage"])

    def test_mesh_decoration_counts_toward_budget(self):
        theme = build_theme("#2563eb")
        gradient = self._section("Hero Gradient", {"background": "linear-gradient(135deg,#0f172a,#2563eb)"})
        mesh = self._section("Mesh Band", {"backgroundImage": mesh_gradient(theme.palette)})
        mesh.backgroundTexture = "mesh"

        cap_gradient_textures([gradient, mesh], theme)

        self.assertEqual(mesh.backgroundTexture, "flat")
        self.assertNotIn("backgroundImage", mesh.styles)


if __name__ == "__main__":
    unittest.main()
