"""A card stands off the band it sits on (services/card_frames.py).

A dark site's "How we work" grid shipped its step cards at OKLab ΔE 0.038 from
the section — 1.07:1, a fill that is neither the band's colour nor a step off
it. Three causes, each pinned here:

- the treatment pass found cards by a name ending in "card", and the catalogue
  names its cells "Step Cell", "Tier Cell", "Member Cell", "Stat Cell",
  "FAQ Item", so the scheme's treatment never reached them;
- the treatments drew their hairline from `secondary`, the darkest colour of a
  dark palette, and filled with the page colour — frameless on every dark site;
- nothing judged a card against the band it finally sits on.
"""

from __future__ import annotations

import unittest
from unittest import mock

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.builder_schema import BuilderElement, BuilderElementContent, GeneratedSite
from app.models.content_blocks import (
    FaqBlock,
    FaqItem,
    HeroBlock,
    PagePlan,
    PricingBlock,
    PricingTier,
    ProcessBlock,
    ProcessStep,
    SitePlan,
)
from app.services import design_quality
from app.services.card_frames import (
    DARK_FILL_MIN,
    RULE_MIN,
    card_children,
    delta_e,
    separate_cards,
    walk_cards,
)
from app.services.color_system import derive
from app.services.design_schemes import DESIGN_SCHEMES
from app.services.schema_builder import modernize_sections, plan_to_site
from app.services.section_content import band_of_surface
from app.services.style_tokens import card_rule
from app.services.template_filler import fill_template, load_catalog
from app.services.theme import apply_creative_palette, build_theme

# The palette the reported site was built on: a near-black creative canvas.
_CREATIVE_DARK = {"primary": "#3f8f2a", "secondary": "#1f2a1c", "accent": "#a3e635",
                  "surface": "#161a15"}
# The catalogue's own step-cell styling, verbatim.
_STEP_CELL = {
    "borderRadius": "20px",
    "backgroundColor": "color-mix(in srgb, var(--builder-color-surface, #f8fafc) 96%, transparent)",
    "border": "1px solid rgba(148,163,184,0.14)",
}


def _dark_theme(scheme: str | None = None):
    theme = apply_creative_palette(
        build_theme("#3f8f2a", mood="modern", industry="agency", color_scheme="dark"),
        _CREATIVE_DARK,
    )
    return theme.model_copy(update={"design_scheme": scheme}) if scheme else theme


def _light_theme():
    return build_theme("#3f8f2a", mood="modern", industry="agency")


def _text(inner: str) -> BuilderElement:
    return BuilderElement(name="Step Title", type="text",
                          content=BuilderElementContent(innerText=inner), styles={})


def _cell(name: str = "Step Cell", **styles) -> BuilderElement:
    return BuilderElement(name=name, type="container", content=[_text("Plan")],
                          styles=dict(_STEP_CELL, **styles))


def _section(cells: list[BuilderElement], background: str = "var(--builder-page-background, #ffffff)"):
    grid = BuilderElement(name="Steps Grid", type="container", content=cells, styles={})
    return BuilderElement(name="Process - Steps", type="container", content=[grid],
                          styles={"backgroundColor": background})


class WhichNodesAreCardsTest(unittest.TestCase):
    def test_a_repeated_framed_cell_is_a_card_whatever_its_name(self):
        grid = _section([_cell(), _cell(), _cell()]).content[0]
        self.assertEqual(len(card_children(grid)), 3)

    def test_a_lone_framed_box_is_not_a_card_unless_it_says_so(self):
        grid = _section([_cell("About Highlight")]).content[0]
        self.assertEqual(card_children(grid), [])
        grid = _section([_cell("Contact Card")]).content[0]
        self.assertEqual(len(card_children(grid)), 1)

    def test_a_bare_wrapper_and_a_photo_tile_are_not_cards(self):
        wrappers = [BuilderElement(name="Item", type="container", content=[], styles={})] * 2
        photos = [_cell("Tile", backgroundImage="url(https://x/p.jpg)")] * 2
        self.assertEqual(card_children(_section(wrappers).content[0]), [])
        self.assertEqual(card_children(_section(photos).content[0]), [])


class EveryCatalogueCellIsFoundTest(unittest.IsolatedAsyncioTestCase):
    """The vocabulary trap, closed by structure: every `$repeat` item the
    catalogue frames as a card is found as one once filled — so a new cell
    name can never again ship outside the treatment and the floor."""

    async def test_every_framed_repeat_item_is_a_card(self):
        from app.services.card_frames import _repeated_frame

        async def resolve(query):
            return None, None

        missed, checked = [], set()
        for template in load_catalog()["sections"]:
            framed = set()

            def collect(node):
                if isinstance(node, dict):
                    # `$repeat` / `$bento` clone their first child once per item.
                    item = (node.get("content") or [None])[0] if (
                        node.get("$repeat") or node.get("$bento")) else None
                    if isinstance(item, dict) and item.get("type") in ("container", "2Col", "3Col"):
                        probe = BuilderElement(name=item.get("name"), type=item["type"],
                                               styles=item.get("styles") or {})
                        if _repeated_frame(probe):
                            framed.add(item.get("name"))
                    for value in node.values():
                        collect(value)
                elif isinstance(node, list):
                    for value in node:
                        collect(value)

            collect(template.get("tree"))
            if not framed or not template.get("sampleContent"):
                continue
            tree = await fill_template(template, template["sampleContent"], resolve_image=resolve)
            found = {c.name for node in tree.walk() for c in card_children(node)}
            for name in framed:
                if sum(1 for node in tree.walk() if node.name == name) >= 2:
                    checked.add(name)
                    if name not in found:
                        missed.append(f"{template['id']}: {name}")
        self.assertEqual(missed, [])
        # Not vacuous: the cells that shipped unjudged are among those checked.
        self.assertTrue({"Step Cell", "Tier Cell", "FAQ Item"} <= checked, checked)


class TreatmentReachesCellsTest(unittest.TestCase):
    def test_a_step_cell_takes_the_schemes_treatment(self):
        theme = _dark_theme("swiss-quiet")  # bordered
        section = _section([_cell(), _cell()])
        with mock.patch.object(settings, "design_schemes_enabled", True):
            modernize_sections([section], theme)
        cell = section.content[0].content[0]
        self.assertIn("--builder-color-surface-elevated", cell.styles["backgroundColor"])
        self.assertIn("--builder-color-border", cell.styles["border"])

    def test_the_dark_hairline_is_the_border_role_and_the_light_one_is_unchanged(self):
        dark, light = _dark_theme(), _light_theme()
        self.assertTrue(card_rule(dark.palette).startswith("var(--builder-color-border, "))
        r, g, b = (int(light.palette.secondary[i:i + 2], 16) for i in (1, 3, 5))
        self.assertEqual(card_rule(light.palette, 0.14), f"rgba({r}, {g}, {b}, 0.14)")


class FloorPassTest(unittest.TestCase):
    def test_the_reported_dark_grid_steps_off_its_band(self):
        theme = _dark_theme()
        section = _section([_cell() for _ in range(5)])
        before = [frame for _, frame in walk_cards(section, theme)]
        self.assertTrue(all(not f.separates for f in before))
        self.assertLess(before[0].fill_delta, DARK_FILL_MIN)

        self.assertEqual(separate_cards([section], theme), 5)
        for card, frame in walk_cards(section, theme):
            self.assertTrue(frame.separates)
            self.assertGreaterEqual(frame.fill_delta, DARK_FILL_MIN)
            # Still a dark card: the copy on it is already inked for one.
            self.assertEqual(band_of_surface(frame.fill, theme), "dark")

    def test_a_lifted_fill_is_a_palette_role_when_one_does_it(self):
        theme = _dark_theme()
        section = _section([_cell(), _cell()])
        separate_cards([section], theme)
        fill = section.content[0].content[0].styles["backgroundColor"]
        self.assertTrue(fill.startswith("var(--builder-color-"), fill)

    def test_a_deliberate_outline_card_on_a_dark_band_is_left_alone(self):
        theme = _dark_theme()
        colors = derive(theme.palette.model_dump())
        cells = [_cell(backgroundColor=theme.page.background,
                       border=f"1px solid {colors['border']}") for _ in range(2)]
        section = _section(cells)
        self.assertEqual(separate_cards([section], theme), 0)

    def test_a_flat_card_is_not_judged(self):
        theme = _dark_theme()
        section = _section([_cell(backgroundColor="transparent", border="none")] * 2)
        self.assertEqual(list(walk_cards(section, theme)), [])

    def test_a_frameless_light_card_gains_a_hairline_that_reads(self):
        theme = _light_theme()
        cells = [_cell(backgroundColor=theme.page.background, border="none") for _ in range(3)]
        section = _section(cells)
        self.assertEqual(separate_cards([section], theme), 3)
        for _, frame in walk_cards(section, theme):
            self.assertTrue(frame.separates)
            self.assertGreaterEqual(frame.rule_delta, RULE_MIN)

    def test_the_standard_light_card_already_reads(self):
        """White on gray-50 with a gray-200 rule: a near fill, framed."""
        theme = _light_theme()
        cells = [_cell(backgroundColor="#ffffff", border="1px solid #e5e7eb") for _ in range(2)]
        section = _section(cells, background="#f9fafb")
        self.assertEqual(separate_cards([section], theme), 0)

    def test_delta_e_reads_the_measurements_behind_the_floors(self):
        self.assertLess(delta_e("#121711", "#0c0e0b"), DARK_FILL_MIN)  # the report
        self.assertGreaterEqual(delta_e("#1f2937", "#111827"), DARK_FILL_MIN)  # tailwind dark


def _inks_under_aa(card: BuilderElement, fill: str, theme) -> list[str]:
    """Copy directly on `card` (not inside a nested surface) under 4.5:1."""
    from app.services.section_content import _parse_color, _to_hex
    from app.services.theme import _contrast

    bad = []
    for node in card.walk():
        if node.type not in ("text", "link") or node is card:
            continue
        if (node.styles or {}).get("backgroundColor") not in (None, "transparent"):
            continue
        parsed = _parse_color((node.styles or {}).get("color"), theme)
        if parsed and parsed[1] >= .999 and _contrast(_to_hex(parsed[0]), fill) < 4.5:
            bad.append(f"{node.name} {_to_hex(parsed[0])} on {fill}")
    return bad


def _keeps_brand_number(card: BuilderElement, theme) -> bool:
    from app.services.color_system import oklch
    from app.services.section_content import _parse_color, _to_hex

    number = next((n for n in card.walk() if n.name == "Step Number"), None)
    parsed = _parse_color((number.styles or {}).get("color"), theme) if number else None
    return parsed is None or oklch(_to_hex(parsed[0]))[1] > .04


class EverySchemeSeparatesItsCardsTest(unittest.IsolatedAsyncioTestCase):
    """End to end over every design scheme, on the reported creative dark
    palette, a curated dark one and a light one: no card on the finished page
    blends into its band, and the quality report says the same."""

    async def test_no_scheme_ships_a_card_that_blends(self):
        blocks = lambda: [
            HeroBlock(headline="We build software", subheadline="Small team.", layout="split"),
            ProcessBlock(steps=[ProcessStep(title=f"Step {i}", description="Done properly.")
                                for i in range(5)]),
            PricingBlock(tiers=[PricingTier(name=n, price="$1") for n in "ABC"]),
            FaqBlock(items=[FaqItem(question="Q?", answer="A.")] * 3),
        ]
        failures, judged, brand_lost = {}, 0, set()
        with mock.patch.object(settings, "design_schemes_enabled", True), \
             mock.patch.object(settings, "design_brain_enabled", False):
            for scheme in DESIGN_SCHEMES:
                dark = build_theme("#3f8f2a", mood="modern", industry="agency",
                                   scheme_choice=scheme.slug, color_scheme="dark")
                for label, theme in (
                    ("creative-dark", apply_creative_palette(dark, _CREATIVE_DARK)),
                    ("dark", dark),
                    ("light", build_theme("#3f8f2a", mood="modern", industry="agency",
                                          scheme_choice=scheme.slug)),
                ):
                    site = await plan_to_site(
                        SitePlan(site_name="WebTree", brand_mood="modern", industry_category="agency",
                                 pages=[PagePlan(page_type="home", slug="home", title="Home",
                                                 is_homepage=True, blocks=blocks(),
                                                 seo_title="Home", seo_description="Home")]),
                        brand=BrandIdentity(name="WebTree", mood="modern"), theme=theme,
                    )
                    frames = [(s, c, f) for s in site.pages[0].body_schema.elements
                              for c, f in walk_cards(s, theme)]
                    judged += len(frames)
                    bad = [f"{s.name}/{c.name} {f.fill_delta:.3f}" for s, c, f in frames
                           if not f.separates]
                    bad += [f"{c.name}/{ink}" for _, c, f in frames for ink in _inks_under_aa(c, f.fill, theme)]
                    brand_lost |= {f"{scheme.slug}/{label}" for _, c, f in frames if c.name == "Step Cell"
                                   and not _keeps_brand_number(c, theme)}
                    codes = {f.code for f in design_quality.evaluate(site).findings}
                    if bad or "card-blends-band" in codes:
                        failures[f"{scheme.slug}/{label}"] = bad[:3]
        self.assertEqual(failures, {})
        self.assertGreater(judged, 200)
        # A lifted card keeps its brand-coloured step numbers: the rebind has
        # no brand-ink pair for `surface-elevated`, and once dropped them to
        # the neutral ink.
        self.assertEqual(brand_lost, set())


class QualityFindingTest(unittest.TestCase):
    def test_a_blending_card_is_reported_and_a_separated_one_is_not(self):
        theme = _dark_theme()
        section = _section([_cell() for _ in range(3)])
        page = mock.Mock(slug="home", body_schema=mock.Mock(elements=[section]))
        site = mock.Mock(spec=GeneratedSite, pages=[page], theme=theme)
        ctx = mock.Mock(site=site)
        self.assertEqual([f.code for f in design_quality._check_card_bands(ctx)],
                         ["card-blends-band"])
        separate_cards([section], theme)
        self.assertEqual(list(design_quality._check_card_bands(ctx)), [])


if __name__ == "__main__":
    unittest.main()
