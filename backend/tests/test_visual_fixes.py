"""Tests for the generated-site visual fixes: on-brand mesh gradient, scheme-aware
glass cards, and orphan-free card grids."""

import asyncio
import unittest

from app.services.schema_builder import glass_card_styles, mesh_gradient
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


if __name__ == "__main__":
    unittest.main()
