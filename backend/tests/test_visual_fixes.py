"""Tests for the generated-site visual fixes: on-brand mesh gradient, scheme-aware
glass cards, and orphan-free card grids."""

import asyncio
import re
import unittest

from app.models.builder_schema import BuilderElement
from app.models.content_blocks import (
    AwardItem,
    AwardsBlock,
    MenuBlock,
    MenuCategory,
    MenuItem,
    PricingBlock,
    PricingTier,
    StatItem,
    StatsBlock,
    TeamBlock,
    TeamMember,
    TimelineBlock,
    TimelineItem,
)
from app.services.image_styling import (
    _TEXT_SCRIM_RATIO,
    brand_overlay_gradient,
    color_distance,
    overlay_alpha,
    photo_background,
    text_scrim_gradient,
)
from app.services.schema_builder import (
    RenderContext,
    _build_awards,
    _build_menu,
    _build_pricing,
    _build_stats,
    _build_team,
    _build_timeline,
    apply_section_dividers,
    cap_gradient_textures,
    glass_card_styles,
    make_style_tokens,
    mesh_gradient,
    modernize_sections,
)
from app.services.style_tokens import emphasis_ink, meta_ink
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


class PhotoOverlayTest(unittest.TestCase):
    """The hero/CTA photo overlay tints a photograph; it must not repaint it.
    A saturated brand hue composited at legibility alpha turns every photo into
    the same flat sheet of brand colour, which is what makes a generated page
    read as one-colour."""

    def _rgba(self, css: str) -> list[tuple[int, int, int, float]]:
        return [
            (int(r), int(g), int(b), float(a))
            for r, g, b, a in re.findall(
                r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", css.replace(" ", "")
            )
        ]

    def test_brand_end_is_mixed_toward_the_ink(self):
        ink, primary = "#221d2b", "#7c3aed"
        stops = self._rgba(brand_overlay_gradient(ink, primary, 0.55))
        self.assertEqual(len(stops), 2)
        (_, _, _, a1), (pr, pg, pb, _) = stops
        # The ink end keeps the full legibility alpha…
        self.assertEqual(a1, 0.55)
        # …and the brand end is closer to the ink than the raw primary is, so
        # the photo underneath keeps its own hues.
        raw = _hex_to_rgb(primary)
        ink_rgb = _hex_to_rgb(ink)
        dist = lambda c: sum(abs(x - y) for x, y in zip(c, ink_rgb))  # noqa: E731
        self.assertLess(dist((pr, pg, pb)), dist(raw))

    def test_brand_end_still_carries_the_hue(self):
        # Mixing toward ink must not flatten the tint to grey — the gradient
        # still has to read as the brand's colour.
        stops = self._rgba(brand_overlay_gradient("#221d2b", "#7c3aed", 0.55))
        pr, pg, pb, _ = stops[1]
        self.assertGreater(max(pr, pg, pb) - min(pr, pg, pb), 30)

    def test_alpha_ratio_between_the_two_ends_is_preserved(self):
        for alpha in (0.14, 0.24, 0.34):
            stops = self._rgba(brand_overlay_gradient("#0f172a", "#2563eb", alpha))
            self.assertEqual(stops[0][3], alpha)
            self.assertAlmostEqual(stops[1][3], round(alpha * 0.82, 2), places=2)

    def test_photo_background_layers_scrim_over_cast_over_photo(self):
        css = photo_background("#808080", "https://x/p.jpg", "#221d2b", "#7c3aed")
        layers, depth, start = [], 0, 0
        for i, ch in enumerate(css):  # split on top-level commas only
            depth += (ch == "(") - (ch == ")")
            if ch == "," and depth == 0:
                layers.append(css[start:i].strip())
                start = i + 1
        layers.append(css[start:].strip())
        self.assertTrue(layers[0].startswith("radial-gradient("), layers[0][:24])
        self.assertTrue(layers[1].startswith("linear-gradient("), layers[1][:24])
        self.assertEqual(layers[2], "url('https://x/p.jpg')")

    def test_scrim_fades_to_fully_transparent_before_the_edge(self):
        # The corners must show the photograph, not a wash — that is the whole
        # point of paying for legibility locally.
        css = text_scrim_gradient("#221d2b", 0.34)
        self.assertIn(",0)", css.replace(" ", ""))

    def test_cast_and_scrim_compound_to_the_legibility_sheet(self):
        """The invariant the split rests on: behind the copy the two layers add
        up to the single sheet that was there before (0.30→0.62 on the same
        luminance ramp), so every ink derived from _SCRIM_COMPOSITE_BG stays
        valid — while the edges carry only the much fainter cast."""
        for avg, old_sheet in (("#2b2b2b", 0.31), ("#808080", 0.37), ("#e0e0e0", 0.54)):
            cast = overlay_alpha(avg)
            scrim = round(cast * _TEXT_SCRIM_RATIO, 2)
            centre = 1 - (1 - cast) * (1 - scrim)
            self.assertAlmostEqual(centre, old_sheet, delta=0.02, msg=avg)
            # …and outside the scrim the photo is roughly twice as visible.
            self.assertLess(cast, old_sheet * 0.62, msg=avg)


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


def _find_by_name(node, name):
    if getattr(node, "name", None) == name:
        return node
    content = getattr(node, "content", None)
    if isinstance(content, list):
        for child in content:
            found = _find_by_name(child, name)
            if found is not None:
                return found
    return None


class ElementAccentBalanceTest(unittest.TestCase):
    """Decorative 'pop' elements (badges, borders, step numbers, stat
    numbers) should draw from the brand accent via `emphasis_ink`, and pure
    metadata (role labels, prices, dates) should read as neutral via
    `meta_ink` — neither should independently default to `palette.primary`,
    which is what made one hue dominate every generated page. Section
    backgrounds/CTAs are untouched by this fix and aren't covered here."""

    @staticmethod
    def _ctx(theme):
        return RenderContext(theme=theme, resolver=None, styles=make_style_tokens(theme))

    def test_pricing_badge_and_highlighted_border_use_accent_not_primary(self):
        theme = build_theme("#2563eb")
        ctx = self._ctx(theme)
        block = PricingBlock(
            heading="Plans",
            tiers=[
                PricingTier(name="Basic", price="$9/mo"),
                PricingTier(name="Pro", price="$29/mo", highlighted=True),
            ],
        )
        section = asyncio.run(_build_pricing(block, ctx))
        expected = emphasis_ink(theme)

        badge = _find_by_name(section, "Badge")
        self.assertEqual(badge.styles["color"], expected)
        self.assertNotEqual(badge.styles["color"], theme.palette.primary)

        grid = _find_by_name(section, "Two Columns")
        highlighted_col = grid.content[1]  # the "Pro" tier, marked highlighted
        self.assertIn(expected, highlighted_col.styles["border"])
        self.assertNotIn(theme.palette.primary, highlighted_col.styles["border"])

    def test_stats_big_number_uses_accent_not_primary(self):
        theme = build_theme("#2563eb")
        ctx = self._ctx(theme)
        block = StatsBlock(items=[StatItem(value="10k", label="Users")])
        section = asyncio.run(_build_stats(block, ctx))

        stat_value = _find_by_name(section, "Stat value")
        self.assertEqual(stat_value.styles["color"], emphasis_ink(theme))
        self.assertNotEqual(stat_value.styles["color"], theme.palette.primary)

    def test_meta_text_no_longer_brand_colored(self):
        theme = build_theme("#2563eb")
        ctx = self._ctx(theme)
        expected = meta_ink(theme)

        team_block = TeamBlock(
            members=[TeamMember(name="Ada Lovelace", role="Engineer", photo_url="https://x/a.jpg")]
        )
        team_section = asyncio.run(_build_team(team_block, ctx))
        role = _find_by_name(team_section, "Member role")
        self.assertEqual(role.styles["color"], expected)
        self.assertNotEqual(role.styles["color"], theme.palette.primary)

        menu_block = MenuBlock(
            categories=[MenuCategory(name="Mains", items=[MenuItem(name="Burger", price="$12")])]
        )
        menu_section = asyncio.run(_build_menu(menu_block, ctx))
        price = _find_by_name(menu_section, "Item price")
        self.assertEqual(price.styles["color"], expected)
        self.assertNotEqual(price.styles["color"], theme.palette.primary)

        timeline_block = TimelineBlock(items=[TimelineItem(year="2020", title="Founded")])
        timeline_section = asyncio.run(_build_timeline(timeline_block, ctx))
        year = _find_by_name(timeline_section, "Timeline year")
        self.assertEqual(year.styles["color"], expected)
        self.assertNotEqual(year.styles["color"], theme.palette.primary)

        awards_block = AwardsBlock(items=[AwardItem(title="Best of 2020", issuer="Acme", year="2020")])
        awards_section = asyncio.run(_build_awards(awards_block, ctx))
        meta = _find_by_name(awards_section, "Award meta")
        self.assertEqual(meta.styles["color"], expected)
        self.assertNotEqual(meta.styles["color"], theme.palette.primary)


if __name__ == "__main__":
    unittest.main()
