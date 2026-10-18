"""A card section floats on its neighbour's background (schema_builder.
blend_panelled_sections), and a boundary between two same-coloured flat
sections gets no shaped divider (`_boundary_edge`).

watr's CTA: a dark rounded card inside a cream band tint between two white
sections, with a white wedge cut into the band — a stripe that framed nothing."""

import unittest

from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.services.schema_builder import _boundary_edge, blend_panelled_sections
from app.services.theme import build_theme


def _text(inner):
    return BuilderElement(name="Heading", type="text", styles={"color": "#ffffff"},
                          content=BuilderElementContent(innerText=inner))


def _cta(band="#eedfd0", card="#171009"):
    panel = BuilderElement(name="CTA Banner", type="container", content=[_text("Ready?")],
                           styles={"backgroundColor": card, "borderRadius": "28px"})
    return BuilderElement(name="CTA - Banner", type="container", content=[panel],
                          styles={"backgroundColor": band, "borderTop": "1px solid #ddd"})


def _band(name, color):
    return BuilderElement(name=name, type="container", content=[_text(name)],
                          styles={"backgroundColor": color})


class CardTakesItsNeighboursBackdropTest(unittest.TestCase):
    def setUp(self):
        self.theme = build_theme("#b91c1c", color_scheme="light")

    def test_the_card_section_wears_the_section_above(self):
        about, cta = _band("About", "#fffdfc"), _cta()
        blend_panelled_sections([_band("Hero", "#101820"), about, cta], self.theme)
        self.assertEqual(cta.styles["backgroundColor"], "#fffdfc")
        self.assertNotIn("borderTop", cta.styles)
        self.assertEqual(cta.backgroundTexture, "flat")

    def test_a_token_stays_a_token(self):
        above = _band("About", "var(--builder-color-background, #fbf5ee)")
        cta = _cta()
        blend_panelled_sections([above, cta], self.theme)
        self.assertEqual(cta.styles["backgroundColor"], "var(--builder-color-background, #fbf5ee)")

    def test_a_dark_card_never_melts_into_a_dark_band(self):
        # The section above is as dark as the card: the page background instead.
        cta = _cta(card="#171009")
        blend_panelled_sections([_band("Hero", "#101820"), cta], self.theme)
        self.assertEqual(cta.styles["backgroundColor"], self.theme.page.background)

    def test_a_section_without_a_card_keeps_its_band(self):
        about = _band("About", "#eedfd0")
        blend_panelled_sections([_band("Hero", "#ffffff"), about], self.theme)
        self.assertEqual(about.styles["backgroundColor"], "#eedfd0")

    def test_a_gradient_cta_keeps_its_own_fill(self):
        cta = _cta()
        cta.styles["background"] = "linear-gradient(135deg, #b91c1c, #2b2620)"
        blend_panelled_sections([_band("About", "#ffffff"), cta], self.theme)
        self.assertEqual(cta.styles["backgroundColor"], "#eedfd0")
        self.assertIn("background", cta.styles)


class NoSeamBetweenOneColourTest(unittest.TestCase):
    def test_two_flat_sections_of_one_colour_get_no_divider(self):
        self.assertIsNone(_boundary_edge([_band("A", "#fffdfc"), _band("B", "#fffdfc")], 0, "top"))

    def test_two_flat_sections_of_different_colours_still_do(self):
        self.assertEqual(_boundary_edge([_band("A", "#fffdfc"), _band("B", "#eedfd0")], 0, "top"),
                         (1, "top", 0))


if __name__ == "__main__":
    unittest.main()
