"""Tests for the generated-site visual fixes: on-brand mesh gradient, scheme-aware
glass cards, and orphan-free card grids."""

import asyncio
import unittest

from app.models.builder_schema import BuilderElement
from app.services.schema_builder import (
    apply_section_dividers,
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
    """A section revealed by a shaped divider keeps its mesh/grain decoration —
    the divider's fill carries a matching `texture` tag instead of forcing the
    section flat, so the seam still reads as a continuous handoff. Requires the
    build order in build_site: modernize_sections (tags backgroundTexture) runs
    before apply_section_dividers (reads that tag onto the divider edge)."""

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

    def test_divider_inherits_revealed_sections_texture(self):
        theme = build_theme("#2563eb").model_copy(update={"background_strategy": "mesh"})
        page_bg = theme.page.background
        # A photo hero already carries a fill, so the single texture accent lands
        # on the next plain band — the one the hero's divider reveals.
        hero = self._section("Hero", page_bg)
        hero.styles["backgroundImage"] = "url('https://example.com/p.jpg')"
        revealed = self._section("Revealed", page_bg)

        # Simulates the real build order: tag first, then assign dividers.
        modernize_sections([hero, revealed], theme)
        apply_section_dividers([hero, revealed], "modern")

        edge = hero.divider.bottom
        self.assertEqual(edge.texture, "mesh")
        self.assertEqual(edge.color, revealed.styles["backgroundColor"])

    def test_divider_texture_is_none_when_revealed_section_is_flat(self):
        theme = build_theme("#2563eb").model_copy(update={"background_strategy": "flat"})
        page_bg = theme.page.background
        hero = self._section("Hero", page_bg)
        revealed = self._section("Revealed", page_bg)

        modernize_sections([hero, revealed], theme)
        apply_section_dividers([hero, revealed], "modern")

        self.assertIsNone(hero.divider.bottom.texture)


if __name__ == "__main__":
    unittest.main()
