"""High contrast where it is easiest to lose: a dark gradient hero and a CTA
button on its own band (section_content.enforce_text_contrast).

A gradient is judged by the stop the copy reads WORST on, not the average of
its stops; a solid button must stand off its band (WCAG 1.4.11, 3:1) and its
label must read on its fill (4.5:1)."""

import unittest

from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.services.section_content import _parse_color, _to_hex, enforce_text_contrast
from app.services.theme import _contrast, build_theme


def _hex(color, theme):
    return _to_hex(_parse_color(color, theme)[0])


def _text(inner, **styles):
    return BuilderElement(name="Headline", type="text", styles=styles,
                          content=BuilderElementContent(innerText=inner))


def _link(inner, **styles):
    return BuilderElement(name="CTA", type="link", styles=styles,
                          content=BuilderElementContent(innerText=inner, href="/contact"))


def _section(children, **styles):
    return BuilderElement(name="Hero", type="container", styles=styles, content=children)


class GradientWorstStopTest(unittest.TestCase):
    def test_dark_copy_on_a_dark_ramp_reads_on_every_stop(self):
        # Averages to a mid blue that a mid-dark ink would clear; the copy over
        # the navy end would not.
        theme = build_theme("#2563eb", color_scheme="light")
        stops = ("#0f172a", "#3b82f6")
        hero = _section(
            [
                _text("Learning that grows", color="#334155", fontSize="44px"),
                _text("Small classes, real teachers.", color="#334155", fontSize="18px"),
            ],
            background=f"linear-gradient(135deg, {stops[0]}, {stops[1]})",
        )
        enforce_text_contrast([hero], theme)
        headline = _hex(hero.content[0].styles["color"], theme)
        body = _hex(hero.content[1].styles["color"], theme)
        # The headline clears the large-text floor on EVERY stop.
        for stop in stops:
            self.assertGreaterEqual(_contrast(headline, stop), 3.0, (headline, stop))
        # No colour reaches 4.5:1 on both ends of this ramp (white is 3.7:1 on
        # #3b82f6), so body copy takes the ink whose worst stop is best: light.
        self.assertGreater(min(_contrast(body, s) for s in stops),
                           min(_contrast("#0f172a", s) for s in stops))
        self.assertNotEqual(body.lower(), "#334155")

    def test_a_translucent_sheen_is_not_the_surface(self):
        theme = build_theme("#2563eb", color_scheme="light")
        hero = _section(
            [_text("Ready when you are", color="#ffffff", fontSize="40px")],
            background=(
                "radial-gradient(90% 140% at 85% 0%, rgba(255,255,255,0.2), "
                "rgba(255,255,255,0) 55%), linear-gradient(120deg, #1d4ed8, #0f172a)"
            ),
        )
        enforce_text_contrast([hero], theme)
        ink = _hex(hero.content[0].styles["color"], theme)
        self.assertGreaterEqual(min(_contrast(ink, s) for s in ("#1d4ed8", "#0f172a")), 3.0)


class ButtonStandsOffItsBandTest(unittest.TestCase):
    def test_a_brand_button_on_a_brand_band_takes_a_fill_that_stands_off_it(self):
        theme = build_theme("#2563eb", color_scheme="light")
        band = theme.palette.primary
        cta = _section(
            [_link("Book a visit", backgroundColor=band, color="#1e3a8a")],
            backgroundColor=band,
        )
        enforce_text_contrast([cta], theme)
        button = cta.content[0]
        fill = _hex(button.styles["backgroundColor"], theme)
        label = _hex(button.styles["color"], theme)
        self.assertGreaterEqual(_contrast(fill, band), 3.0)
        self.assertGreaterEqual(_contrast(label, fill), 4.5)

    def test_a_button_that_already_stands_off_is_untouched(self):
        theme = build_theme("#2563eb", color_scheme="light")
        cta = _section(
            [_link("Book a visit", backgroundColor="#ffffff", color="#0f172a")],
            backgroundColor="#0f172a",
        )
        enforce_text_contrast([cta], theme)
        self.assertEqual(cta.content[0].styles["backgroundColor"], "#ffffff")

    def test_a_ghost_button_keeps_no_fill_and_its_label_follows_the_band(self):
        theme = build_theme("#2563eb", color_scheme="light")
        cta = _section(
            [_link("Learn more", backgroundColor="transparent", color="#0f172a",
                   border="1px solid #0f172a")],
            backgroundColor="#0f172a",
        )
        enforce_text_contrast([cta], theme)
        ghost = cta.content[0]
        self.assertEqual(ghost.styles["backgroundColor"], "transparent")
        self.assertGreaterEqual(_contrast(_hex(ghost.styles["color"], theme), "#0f172a"), 4.5)


if __name__ == "__main__":
    unittest.main()
