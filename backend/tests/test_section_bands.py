"""Band rhythm around self-contained section panels.

`cta-banner` (and any template shaped like it) puts its whole message inside one
inset, rounded, self-filled card and defaults its own root to the page
background. The luminance pass used to paint that root with the dark brand band
anyway, so the panel's brand gradient landed on a near-identical colour and its
border/radius read as a rendering accident. These tests pin the rule: a
panelled section keeps a light band, and a plain card GRID is not mistaken for
a panel."""

import asyncio
import re
import unittest

from app.models.brand import ColorPalette
from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.services.section_content import (
    SectionVisualInput,
    _has_inset_panel,
    apply_luminance_rhythm,
    enforce_fill_contrast,
    polish_inset_panels,
)
from app.services.template_filler import fill_template, get_template
from app.services.theme import _contrast, _relative_luminance, build_theme

PALETTE = ColorPalette(
    primary="#7c3aed",
    secondary="#221d2b",  # the dark band / brand ink
    accent="#16a34a",
    text="#221d2b",
    background="#ffffff",
    surface="#faf5ff",
)


def _el(name: str, styles: dict, children: list[BuilderElement] | None = None, type_="container"):
    return BuilderElement(
        id=name,
        name=name,
        type=type_,
        styles=styles,
        content=children if children is not None else BuilderElementContent(innerText=name),
    )


async def _filled(template_id: str, content: dict) -> BuilderElement:
    async def resolve_image(query: str):
        return f"https://img/{query}.jpg", "#888888"

    return await fill_template(
        get_template(template_id), content, resolve_image=resolve_image
    )


class InsetPanelDetectionTest(unittest.TestCase):
    def test_cta_banner_is_recognised_as_panelled(self):
        section = asyncio.run(
            _filled(
                "cta-banner",
                {
                    "heading": "Join Our Community",
                    "body": "Empowering lives through music therapy",
                    "primary_cta": {"innerText": "Get involved", "href": "/contact"},
                    "secondary_cta": None,
                },
            )
        )
        self.assertTrue(_has_inset_panel(section))

    def test_a_card_grid_is_not_a_panel(self):
        # Four self-filled siblings, not one — the services grid must keep its
        # normal band (it is the cards, not the section, that carry the fill).
        cards = [
            _el(f"Card {i}", {"backgroundColor": "#ffffff", "borderRadius": "20px"})
            for i in range(4)
        ]
        section = _el(
            "Services",
            {"backgroundColor": "var(--builder-page-background, #ffffff)"},
            [_el("Grid", {"display": "grid"}, cards)],
        )
        self.assertFalse(_has_inset_panel(section))

    def test_a_lone_button_is_not_a_panel(self):
        # A single filled `link` inside an actions row — a rounded surface, but
        # not a container, so it must not trip the panel rule.
        actions = _el(
            "Actions",
            {"display": "flex"},
            [_el("Primary CTA", {"backgroundColor": "#7c3aed", "borderRadius": "14px"}, type_="link")],
        )
        section = _el("CTA", {}, [_el("Content", {}, [actions])])
        self.assertFalse(_has_inset_panel(section))

    def test_a_square_edged_fill_is_not_a_panel(self):
        # A full-bleed coloured band inside a section is not an inset card:
        # without a radius there is no frame for the band to fight.
        section = _el("CTA", {}, [_el("Strap", {"backgroundColor": "#7c3aed"})])
        self.assertFalse(_has_inset_panel(section))


class PanelledSectionBandTest(unittest.TestCase):
    def test_panelled_section_keeps_a_light_band(self):
        section = asyncio.run(
            _filled(
                "cta-banner",
                {
                    "heading": "Join Our Community",
                    "body": None,
                    "primary_cta": {"innerText": "Get involved", "href": "/contact"},
                    "secondary_cta": None,
                },
            )
        )
        # Seeded so plain alternation would otherwise hand this section the
        # dark band (it follows a light one).
        inputs = [
            SectionVisualInput(participates=True, band_override="light"),
            SectionVisualInput(participates=True),
        ]
        before = _el("About", {}, [])
        plans = apply_luminance_rhythm([before, section], inputs, PALETTE)

        self.assertEqual(plans[1].band, "light")
        # Two forced-light neighbours collide, so the pass steps this one down
        # and draws a hairline seam (§3.3 step 4) — still a light band, which is
        # all the panel needs to read as a deliberate frame.
        self.assertTrue(plans[1].separator_before)
        self.assertIn("borderTop", section.styles)
        self.assertGreater(_relative_luminance(section.styles["backgroundColor"]), 0.7)

    def test_an_explicit_dark_override_still_wins(self):
        section = asyncio.run(
            _filled(
                "cta-banner",
                {
                    "heading": "Join Our Community",
                    "body": None,
                    "primary_cta": {"innerText": "Go", "href": "/x"},
                    "secondary_cta": None,
                },
            )
        )
        inputs = [SectionVisualInput(participates=True, band_override="dark")]
        plans = apply_luminance_rhythm([section], inputs, PALETTE)
        self.assertEqual(plans[0].band, "dark")

    def test_a_plain_section_still_alternates_into_the_dark_band(self):
        plain = _el("Features", {}, [_el("Grid", {}, [])])
        inputs = [
            SectionVisualInput(participates=True, band_override="light"),
            SectionVisualInput(participates=True),
        ]
        plans = apply_luminance_rhythm([_el("About", {}, []), plain], inputs, PALETTE)
        self.assertEqual(plans[1].band, "dark")
        self.assertEqual(plain.styles["backgroundColor"], PALETTE.secondary)


if __name__ == "__main__":
    unittest.main()


class FillContrastTest(unittest.TestCase):
    """A catalog template paints its fills with `var(--builder-color-*)`, so it
    cannot know whether the theme it lands in keeps the white ink on that fill
    readable. `cta-banner` ramps secondary → primary: on a mid-luminance brand
    white lands at 3.7:1, on a lime brand at 2:1. The pass resolves the tokens
    to concrete hexes, darkened until the ink meets AA — and, as a side effect,
    makes the fill legible to ux_audit, which reads no colour from a gradient."""

    def _theme(self, primary):
        theme = build_theme(primary, palette_mode="curated", industry="nonprofit", font_seed="X")
        return theme.model_copy(
            update={"palette": theme.palette.model_copy(update={"primary": primary})}
        )

    def _panel(self, *, body, primary="#0891b2"):
        section = asyncio.run(
            _filled(
                "cta-banner",
                {
                    "heading": "Join Our Effort to Empower Lives",
                    "body": body,
                    "primary_cta": {"innerText": "Become a Member", "href": "/join"},
                    "secondary_cta": None,
                },
            )
        )
        enforce_fill_contrast([section], self._theme(primary))
        return section.content[0]

    def _stops(self, panel):
        return re.findall(r"#[0-9a-fA-F]{6}", panel.styles["background"])

    def test_tokens_are_resolved_to_concrete_hexes(self):
        panel = self._panel(body=None)
        self.assertNotIn("var(", panel.styles["background"])
        self.assertEqual(len(self._stops(panel)), 2)

    def test_small_text_forces_the_brand_stop_down_to_aa(self):
        panel = self._panel(body="Membership is open to practitioners and students.")
        for stop in self._stops(panel):
            self.assertGreaterEqual(_contrast("#ffffff", stop), 4.5, stop)

    def test_a_display_only_panel_keeps_its_brand_colour(self):
        # 38px/800 is WCAG "large text"; holding it to the 4.5 body floor would
        # darken the brand end for no legibility gain, so the design stands.
        panel = self._panel(body=None)
        self.assertIn("#0891b2", self._stops(panel))

    def test_a_brand_that_fails_even_for_display_text_is_corrected(self):
        # White on lime is 1.98:1 — unreadable at any size.
        panel = self._panel(body=None, primary="#84cc16")
        self.assertNotIn("#84cc16", self._stops(panel))
        for stop in self._stops(panel):
            self.assertGreaterEqual(_contrast("#ffffff", stop), 3.5, stop)

    def test_a_photo_fill_is_left_to_its_own_overlay(self):
        section = _el(
            "Hero",
            {
                "background": "linear-gradient(135deg, #1d272b, #0891b2), url('https://x/p.jpg')",
                "color": "#ffffff",
            },
            [_el("Heading", {"color": "#ffffff", "fontSize": "16px"}, None, type_="text")],
        )
        before = section.styles["background"]
        enforce_fill_contrast([section], self._theme("#0891b2"))
        self.assertEqual(section.styles["background"], before)

    def test_ink_inside_its_own_surface_does_not_drive_the_fill(self):
        # The white pill button on the panel brings its own background; its
        # label's contrast is the button's problem, not the gradient's.
        panel = self._panel(body=None)
        self.assertIn("#0891b2", self._stops(panel))


class PanelPolishTest(unittest.TestCase):
    def _polished(self, primary="#0891b2"):
        section = asyncio.run(
            _filled(
                "cta-banner",
                {
                    "heading": "Join Our Effort to Empower Lives Through Music Therapy",
                    "body": None,
                    "primary_cta": {"innerText": "Become a Member", "href": "/join"},
                    "secondary_cta": None,
                },
            )
        )
        theme = build_theme(primary, palette_mode="curated", industry="nonprofit", font_seed="X")
        theme = theme.model_copy(
            update={"palette": theme.palette.model_copy(update={"primary": primary})}
        )
        enforce_fill_contrast([section], theme)
        polish_inset_panels([section])
        return section.content[0]

    def test_headline_gets_a_measure_cap(self):
        heading = self._polished().content[0]
        self.assertEqual(heading.styles["maxWidth"], "820px")
        self.assertEqual(heading.styles["textWrap"], "balance")

    def test_dark_panel_hairline_is_flipped_to_light(self):
        # The authored border is rgba(30,41,59,0.16) — a light-page hairline,
        # invisible against the panel's own dark fill.
        self.assertEqual(
            self._polished().styles["border"], "1px solid rgba(255,255,255,0.14)"
        )

    def test_a_plain_section_is_untouched(self):
        section = _el("Services", {}, [_el("Grid", {"display": "grid"}, [])])
        before = dict(section.styles)
        polish_inset_panels([section])
        self.assertEqual(section.styles, before)
