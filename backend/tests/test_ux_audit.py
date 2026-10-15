"""Tests for the static UX/accessibility audit over BuilderElement output."""

import unittest

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.services.theme import _contrast, _relative_luminance, build_theme, ink_css, ink_on
from app.services.ux_audit import audit_site, summarize


def _hex(color, theme):
    """A written colour (hex, rgba or a `var(--builder-*)` token) as the hex it paints."""
    from app.services.section_content import _parse_color, _to_hex

    return _to_hex(_parse_color(color, theme)[0])


def _text(name, inner, **styles):
    return BuilderElement(
        name=name, type="text", styles=styles,
        content=BuilderElementContent(innerText=inner),
    )


def _image(name, *, src="x.jpg", alt=None, width=None, height=None, **styles):
    return BuilderElement(
        name=name, type="image", styles=styles,
        content=BuilderElementContent(src=src, alt=alt, width=width, height=height),
    )


def _link(name, *, inner=None, aria=None, **styles):
    return BuilderElement(
        name=name, type="link", styles=styles,
        content=BuilderElementContent(innerText=inner, ariaLabel=aria),
    )


def _container(name, children, **styles):
    return BuilderElement(name=name, type="container", styles=styles, content=children)


def _site(elements, builder_styles=None):
    return GeneratedSite(
        site_name="T",
        pages=[GeneratedPage(
            slug="", title="Home",
            body_schema=BodySchema(elements=elements), seo=PageSeo(),
        )],
        builder_styles=builder_styles,
    )


def _rules(findings):
    return {f.rule for f in findings}


class AltAndImageTest(unittest.TestCase):
    def test_image_without_alt_flagged(self):
        f = audit_site(_site([_image("hero", width="800", height="600")]))
        self.assertIn("alt-text", _rules(f))

    def test_image_with_alt_and_dims_clean(self):
        f = audit_site(_site([_image("hero", alt="A spa", width="800", height="600")]))
        self.assertEqual(f, [])

    def test_image_without_dimensions_flagged(self):
        f = audit_site(_site([_image("hero", alt="A spa")]))
        self.assertIn("image-dimensions", _rules(f))


class LinkAndFontTest(unittest.TestCase):
    def test_link_without_label_flagged(self):
        self.assertIn("aria-label", _rules(audit_site(_site([_link("cta")]))))

    def test_link_with_text_clean(self):
        self.assertEqual(audit_site(_site([_link("cta", inner="Book now")])), [])

    def test_aria_label_satisfies_link(self):
        self.assertEqual(audit_site(_site([_link("ico", aria="Open menu")])), [])

    def test_small_font_flagged(self):
        self.assertIn(
            "readable-font-size",
            _rules(audit_site(_site([_text("fine", "Legal", fontSize="10px")]))),
        )

    def test_normal_font_clean(self):
        self.assertEqual(audit_site(_site([_text("body", "Hello", fontSize="16px")])), [])


class ContrastTest(unittest.TestCase):
    def test_low_contrast_via_builder_vars_flagged(self):
        tree = [_container(
            "band",
            [_text("copy", "Welcome", color="var(--builder-color-text)")],
            backgroundColor="var(--builder-color-background)",
        )]
        site = _site(tree, builder_styles={"colors": {"text": "#777777", "background": "#888888"}})
        self.assertIn("color-contrast", _rules(audit_site(site)))

    def test_good_contrast_clean(self):
        tree = [_container(
            "band",
            [_text("copy", "Welcome", color="#111111")],
            backgroundColor="#ffffff",
        )]
        self.assertNotIn("color-contrast", _rules(audit_site(_site(tree))))

    def test_var_fallback_hex_is_used(self):
        # color carries an inline fallback hex; background is a plain hex.
        tree = [_container(
            "band",
            [_text("copy", "Welcome", color="var(--missing, #999999)")],
            backgroundColor="#aaaaaa",
        )]
        self.assertIn("color-contrast", _rules(audit_site(_site(tree))))

    def test_unresolvable_var_is_skipped_not_crashed(self):
        # No builder_styles and no fallback → can't resolve → no contrast finding.
        tree = [_container(
            "band",
            [_text("copy", "Welcome", color="var(--builder-color-text)")],
            backgroundColor="var(--builder-color-background)",
        )]
        self.assertNotIn("color-contrast", _rules(audit_site(_site(tree))))


class CleanAndSummaryTest(unittest.TestCase):
    def test_clean_site_has_no_findings(self):
        tree = [_container(
            "hero",
            [
                _text("headline", "Welcome", color="#111111", fontSize="40px"),
                _image("photo", alt="A spa", width="800", height="600"),
                _link("cta", inner="Book now"),
            ],
            backgroundColor="#ffffff",
        )]
        self.assertEqual(audit_site(_site(tree)), [])

    def test_summarize_counts_by_severity(self):
        f = audit_site(_site([_image("hero"), _link("cta")]))  # 2 high-sev issues + dims
        s = summarize(f)
        self.assertEqual(s["high"], len(f))
        self.assertGreaterEqual(s["high"], 2)


class TextContrastTest(unittest.TestCase):
    """enforce_text_contrast (symmetric, scheme-agnostic) flips text that is on the
    WRONG luminance side of its resolved band — in either scheme — and leaves
    correct-side, photo-overlay, and own-surface text untouched."""

    def _run(self, tree, theme):
        from app.services.section_content import enforce_text_contrast
        n = enforce_text_contrast([tree], theme)
        return tree, n

    @staticmethod
    def _light_ink(theme, surface="var(--builder-color-secondary)"):
        """The palette's ink for a dark surface, as the ink pass WRITES it —
        its token when un-nudged (`theme.ink_on` → `theme.ink_css`), never a
        literal white."""
        from app.services.theme import ink_css, ink_on

        return ink_css(theme.palette, ink_on(theme.palette, _hex(surface, theme)))

    def test_dark_secondary_text_on_dark_band_flips_to_light(self):
        theme = build_theme("#d55d62", color_scheme="dark")
        tree = _container(
            "sec",
            [_text("h", "Title", color="var(--builder-color-secondary)", fontSize="40px")],
            backgroundColor="var(--builder-color-secondary)",
        )
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 1)
        self.assertEqual(tree.content[0].styles["color"], self._light_ink(theme))

    def test_dark_text_on_dark_literal_band_with_mesh_flips(self):
        # The exact observed bug: a literal dark band carrying a decorative mesh
        # gradient overlay (no real photo) — must still recolour the dark text.
        theme = build_theme("#d55d62", color_scheme="dark")
        tree = _container(
            "about",
            [_text("body", "x", color="rgba(15,23,42,0.68)")],
            backgroundColor="#332424",
            backgroundImage="radial-gradient(at 8% 12%, rgba(213,93,98,0.34) 0px, transparent 46%)",
        )
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 1)
        color = _hex(tree.content[0].styles["color"], theme)
        self.assertGreater(_contrast(color, "#332424"), 7.0)  # the palette's light ink
        self.assertEqual(tree.content[0].styles.get("opacity"), "68%")  # alpha preserved

    def test_light_text_on_light_band_flips_to_dark(self):
        # Inverse case: hard-coded white text on a light surface (light scheme).
        theme = build_theme("#2563eb", color_scheme="light")
        tree = _container(
            "band",
            [_text("h", "x", color="#ffffff")],
            backgroundColor="var(--builder-color-surface)",
        )
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 1)
        self.assertEqual(tree.content[0].styles["color"], "var(--builder-color-ink-dark, #0f172a)")

    def test_text_on_photo_untouched(self):
        theme = build_theme("#2563eb", color_scheme="dark")
        tree = _container(
            "hero",
            [_text("copy", "Hello", color="#0f172a")],
            backgroundImage="url(photo.jpg)",
        )
        _, n = self._run(tree, theme)
        self.assertEqual(n, 0)

    def test_ghost_button_on_a_dark_band_flips_ink_and_outline(self):
        """A ghost CTA states a colour and paints no surface, so it vanishes on a
        same-luminance band exactly like a paragraph — and its hairline vanishes
        with it, which leaves a legible label in an invisible frame."""
        theme = build_theme("#2563eb", color_scheme="dark")
        tree = _container(
            "band",
            [_link(
                "Secondary CTA",
                inner="See pricing",
                color="var(--builder-color-secondary, #0f172a)",
                backgroundColor="transparent",
                border="1px solid rgba(15,23,42,0.14)",
            )],
            backgroundColor="var(--builder-color-secondary)",
        )
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 1)
        cta = tree.content[0]
        self.assertEqual(cta.styles["color"], self._light_ink(theme))
        # Colour restated, the author's weight (1px solid, 0.14) preserved.
        ink = _hex(cta.styles["color"], theme)
        r, g, b = (int(ink[i : i + 2], 16) for i in (1, 3, 5))
        self.assertEqual(cta.styles["border"], f"1px solid rgba({r},{g},{b},0.14)")

    def test_solid_button_label_is_measured_against_its_own_fill(self):
        """The primary CTA's token pair is contrast-guaranteed by the theme; it
        must be read as the button's own surface, not judged against the band —
        which on a light band would flip its white label to dark."""
        theme = build_theme("#2563eb", color_scheme="light")
        tree = _container(
            "band",
            [_link(
                "Primary CTA",
                inner="Book a demo",
                color="var(--builder-button-text, #ffffff)",
                backgroundColor="var(--builder-button-background, #2563eb)",
            )],
            backgroundColor="var(--builder-color-surface)",
        )
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 0)
        self.assertEqual(
            tree.content[0].styles["color"], "var(--builder-button-text, #ffffff)"
        )

    def test_a_translucent_chip_on_a_gradient_keeps_the_bands_ink(self):
        """The gradient hero's ghost CTA: `rgba(255,255,255,0.12)` over an
        opaque brand ramp. The chip lightens the ramp by a tint, not to a light
        surface — it must not flip the label to near-black. The literal white
        label becomes the palette's light ink for that surface."""
        theme = build_theme("#2563eb", color_scheme="light")
        tree = _container(
            "hero",
            [_link(
                "Secondary CTA",
                inner="See pricing",
                color="#ffffff",
                backgroundColor="rgba(255,255,255,0.12)",
                border="1px solid rgba(255,255,255,0.45)",
            )],
            background=(
                "linear-gradient(135deg, var(--builder-color-secondary, #0f172a), "
                "var(--builder-color-primary, #2563eb))"
            ),
        )
        tree, _ = self._run(tree, theme)
        label = _hex(tree.content[0].styles["color"], theme)
        self.assertGreater(_relative_luminance(label), 0.8)  # still a LIGHT ink
        self.assertNotEqual(label.lower(), "#ffffff")  # …the palette's, not a literal

    def test_a_sheen_layer_does_not_hide_the_opaque_fill_beneath_it(self):
        """`cta-gradient`'s shape: a translucent radial sheen stacked ON TOP of
        an opaque brand ramp. Read as one string, the sheen's `rgba(…,0)` stop
        made the whole fill look decorative, so the panel's white-on-brand copy
        was measured against the page background and flipped to near-black."""
        theme = build_theme("#2563eb", color_scheme="light")
        tree = _container(
            "cta",
            [_text("h", "Ready when you are", color="#ffffff", fontSize="40px")],
            background=(
                "radial-gradient(90% 140% at 85% 0%, rgba(255,255,255,0.2), "
                "rgba(255,255,255,0) 55%), linear-gradient(120deg, "
                "var(--builder-color-primary, #2563eb), var(--builder-color-secondary, #0f172a))"
            ),
        )
        tree, _ = self._run(tree, theme)
        heading = _hex(tree.content[0].styles["color"], theme)
        self.assertGreater(_relative_luminance(heading), 0.8)  # never near-black
        self.assertNotEqual(heading.lower(), "#ffffff")

    def test_a_computed_border_colour_is_left_alone(self):
        """A `color-mix()` / `var()` outline is brand-hued by construction, and
        the only hex in it is a fallback or a mix stop — rewriting either
        changes what the expression means."""
        theme = build_theme("#2563eb", color_scheme="dark")
        mix = "1px solid color-mix(in srgb, var(--builder-color-primary, #2563eb) 30%, #ffffff)"
        tree = _container(
            "band",
            [_link(
                "Download button",
                inner="Download",
                color="var(--builder-color-secondary, #0f172a)",
                backgroundColor="transparent",
                border=mix,
            )],
            backgroundColor="var(--builder-color-secondary)",
        )
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 1)
        self.assertEqual(tree.content[0].styles["color"], self._light_ink(theme))  # ink still fixed
        self.assertEqual(tree.content[0].styles["border"], mix)  # outline untouched

    def test_measured_surface_overrides_the_photo_bail_out(self):
        """A painted surface (`BuilderElement.paint_surface`) is a painter
        saying "I painted this and measured it" — the photo rule (which exists
        because a photo's luminance is unknowable from the styles) then has
        nothing left to protect, and the node's own declared fill is a colour
        the wash covered, so neither is read."""
        theme = build_theme("#2563eb", color_scheme="dark")
        tree = _container(
            "hero",
            [_text("copy", "Hello", color="#0f172a")],
            backgroundColor="var(--builder-page-background, #ffffff)",
            backgroundImage="linear-gradient(135deg, rgba(2,6,23,0.8), rgba(37,99,235,0.18)), url(photo.jpg)",
        )
        from app.services.section_content import enforce_text_contrast

        self.assertEqual(enforce_text_contrast([tree], theme), 0)  # unmeasured: hands off
        tree.paint_surface("#0a0f1f")
        self.assertEqual(enforce_text_contrast([tree], theme), 1)
        self.assertEqual(tree.content[0].styles["color"], self._light_ink(theme, "#0a0f1f"))

    def test_a_photo_nested_under_a_measured_surface_still_owns_its_ink(self):
        """The record is the painted node's, not its subtree's: a photo tile
        inside the section is a fill nobody measured."""
        theme = build_theme("#2563eb", color_scheme="dark")
        tile = _container(
            "tile",
            [_text("caption", "On the photo", color="#0f172a")],
            backgroundImage="url(tile.jpg)",
        )
        tree = _container("hero", [tile], backgroundImage="url(photo.jpg)")
        tree.paint_surface("#0a0f1f")
        from app.services.section_content import enforce_text_contrast

        self.assertEqual(enforce_text_contrast([tree], theme), 0)

    def test_dark_text_on_light_card_untouched(self):
        # Dark scheme, but a light glass card keeps its dark text (correct side).
        theme = build_theme("#2563eb", color_scheme="dark")
        tree = _container(
            "card",
            [_text("copy", "Hello", color="#0f172a")],
            backgroundColor="#ffffff",
        )
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 0)
        self.assertEqual(tree.content[0].styles["color"], "#0f172a")

    def test_correct_side_text_left_alone(self):
        # Light text on the dark page band is already correct → untouched even if
        # below 7:1.
        theme = build_theme("#2563eb", color_scheme="dark")
        tree = _container(
            "band",
            [_text("body", "x", color="var(--builder-color-text)")],
            backgroundColor="var(--builder-page-background)",
        )
        _, n = self._run(tree, theme)
        self.assertEqual(n, 0)

    def test_brand_text_keeps_its_hue_and_is_held_to_aa(self):
        # Brand-token text is the design, so it is never flipped to a neutral —
        # but it is not exempt from AA either: this primary eyebrow read 3.75:1
        # on the dark page. It is lifted along its own hue until it reads.
        from app.services.theme import _hex_to_rgb, _rgb_to_hls

        theme = build_theme("#2563eb", color_scheme="dark")
        eyebrow = _text("eyebrow", "y", color="var(--builder-color-primary)")
        tree = _container("band", [eyebrow], backgroundColor="var(--builder-page-background)")
        _, n = self._run(tree, theme)
        self.assertEqual(n, 1)
        page = _hex("var(--builder-page-background)", theme)
        ink = eyebrow.styles["color"]
        self.assertGreaterEqual(_contrast(ink, page), 4.5)
        hue = lambda c: _rgb_to_hls(*_hex_to_rgb(c))[0] * 360
        self.assertAlmostEqual(hue(ink), hue(theme.palette.primary), delta=2)

    def test_color_mix_card_background_not_misread_as_opaque_primary(self):
        # The process-enrollment-steps "Step Cell"/"Step Title" shape: a
        # near-white color-mix() card (6% primary over white) used to be
        # misread by _VAR_TOKEN as the token's raw, fully-opaque colour —
        # judging the card "dark" and force-flipping the correct dark title
        # to white, landing white-on-near-white.
        theme = build_theme("#2563eb", color_scheme="light")
        tree = _container(
            "step-cell",
            [_text("title", "Safeguard Standards", color="var(--builder-color-secondary, #0f172a)")],
            backgroundColor="color-mix(in srgb, var(--builder-color-primary, #2563eb) 6%, #ffffff)",
        )
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 0)
        self.assertEqual(
            tree.content[0].styles["color"], "var(--builder-color-secondary, #0f172a)"
        )

    def test_color_mix_background_does_not_force_recolor_its_own_brand_text(self):
        # The services-programs-age "Age Badge" / hero-playful-split "Sticker
        # Eyebrow" shape: a single text node carries BOTH a color-mix() fill
        # and a primary-ink foreground. Misreading the fill as opaque primary
        # risked stomping the badge's own intentionally brand-coloured label to
        # a light neutral. Read as the 12% wash it is, the label stays brand-
        # hued and dark — only held to AA on that wash (it read 4.37:1).
        from app.services.section_content import _own_fill, _to_hex
        from app.services.theme import _hex_to_rgb, _rgb_to_hls

        theme = build_theme("#2563eb", color_scheme="light")
        el = _text(
            "age-badge",
            "0–2 yrs",
            color="var(--builder-color-primary-ink, #2563eb)",
            backgroundColor="color-mix(in srgb, var(--builder-color-primary, #2563eb) 12%, #ffffff)",
        )
        wash = _to_hex(_own_fill(el, theme, (255, 255, 255)))
        self._run(el, theme)
        ink = el.styles["color"]
        self.assertGreaterEqual(_contrast(ink, wash), 4.5)
        self.assertLess(_relative_luminance(ink), _relative_luminance(wash))
        hue = lambda c: _rgb_to_hls(*_hex_to_rgb(c))[0] * 360
        self.assertAlmostEqual(hue(ink), hue(theme.palette.primary), delta=2)


class ColorMixParsingTest(unittest.TestCase):
    """Direct unit coverage of _parse_color's color-mix() handling."""

    def _parse(self, value, theme):
        from app.services.section_content import _parse_color
        return _parse_color(value, theme)

    def test_literal_hex_mix(self):
        theme = build_theme("#2563eb")
        rgb, a = self._parse("color-mix(in srgb, #000000 50%, #ffffff)", theme)
        self.assertEqual(rgb, (128, 128, 128))
        self.assertEqual(a, 1.0)

    def test_var_token_mix_matches_catalog_shape(self):
        from app.services.theme import _hex_to_rgb

        theme = build_theme("#2563eb", color_scheme="light")
        rgb, a = self._parse(
            "color-mix(in srgb, var(--builder-color-primary, #2563eb) 6%, #ffffff)", theme,
        )
        pr, pg, pb = _hex_to_rgb(theme.palette.primary)
        self.assertEqual(
            rgb,
            (
                round(0.06 * pr + 0.94 * 255),
                round(0.06 * pg + 0.94 * 255),
                round(0.06 * pb + 0.94 * 255),
            ),
        )
        self.assertEqual(a, 1.0)

    def test_gradient_wrapped_color_mix_still_bails(self):
        theme = build_theme("#2563eb")
        v = (
            "linear-gradient(180deg, "
            "color-mix(in srgb, var(--builder-color-primary, #2563eb) 7%, #ffffff), "
            "#ffffff)"
        )
        self.assertIsNone(self._parse(v, theme))

    def test_mix_over_transparent_is_the_colour_at_that_alpha(self):
        # The catalog's muted ink: the theme text, translucent — read the way
        # the `rgba(15,23,42,0.68)` literal it replaced was.
        from app.services.theme import _hex_to_rgb

        theme = build_theme("#2563eb", color_scheme="dark")
        rgb, a = self._parse(
            "color-mix(in srgb, var(--builder-color-text, #111827) 68%, transparent)", theme,
        )
        self.assertEqual(rgb, _hex_to_rgb(theme.palette.text))
        self.assertAlmostEqual(a, 0.68)

    def test_muted_ink_over_transparent_flips_on_a_wrong_side_band(self):
        from app.services.section_content import enforce_text_contrast

        theme = build_theme("#2563eb", color_scheme="light")
        body = _text("body", "x", color="color-mix(in srgb, var(--builder-color-text, #111827) 68%, transparent)")
        band = _container("band", [body], backgroundColor="var(--builder-color-secondary)")
        self.assertEqual(enforce_text_contrast([band], theme), 1)
        # The palette's light ink (its brand-tinted near-white) as its token,
        # never a literal.
        self.assertEqual(
            body.styles["color"],
            ink_css(theme.palette, ink_on(theme.palette, theme.palette.secondary)),
        )
        self.assertEqual(body.styles.get("opacity"), "68%")  # alpha preserved


class HeadingInkTokenTest(unittest.TestCase):
    """`--builder-color-heading`: `secondary` where it reads on the page, `text`
    where it doesn't (a dark palette's secondary is its darkest band colour)."""

    def test_light_palette_inks_headings_with_secondary(self):
        from app.services.section_content import _token_hex

        theme = build_theme("#2563eb", color_scheme="light")
        self.assertEqual(_token_hex("--builder-color-heading", theme), theme.palette.secondary)

    def test_dark_palette_inks_headings_with_text(self):
        from app.services.section_content import _token_hex

        theme = build_theme("#2563eb", color_scheme="dark")
        self.assertEqual(_token_hex("--builder-color-heading", theme), theme.palette.text)

    def test_a_heading_already_on_the_right_side_keeps_its_token(self):
        from app.services.section_content import enforce_text_contrast

        theme = build_theme("#2563eb", color_scheme="dark")
        heading = _text("h", "Title", color="var(--builder-color-heading, #0f172a)")
        self.assertEqual(enforce_text_contrast([_container("sec", [heading])], theme), 0)
        self.assertEqual(heading.styles["color"], "var(--builder-color-heading, #0f172a)")

    def test_audit_resolves_the_derived_token_not_its_fallback(self):
        colors = {"secondary": "#05080f", "text": "#e5e9ee", "background": "#090d16"}
        tree = [_container(
            "band",
            [_text("h", "Welcome", color="var(--builder-color-heading, #0f172a)")],
            backgroundColor="var(--builder-color-background)",
        )]
        site = _site(tree, builder_styles={"colors": colors})
        self.assertNotIn("color-contrast", _rules(audit_site(site)))


if __name__ == "__main__":
    unittest.main()


class GenericHeadlineTest(unittest.TestCase):
    """Stock headings are reported (advisory) from the same list the copy
    prompt names, so the rule and its check cannot drift apart."""

    @staticmethod
    def _site(*headings: BuilderElement):
        from app.models.builder_schema import BodySchema, GeneratedPage, GeneratedSite, PageSeo

        section = BuilderElement(name="Section", type="container", content=list(headings))
        page = GeneratedPage(slug="", title="Home", is_homepage=True,
                             body_schema=BodySchema(elements=[section]), seo=PageSeo())
        return GeneratedSite(site_name="x", pages=[page])

    @staticmethod
    def _heading(text: str, tag: str = "h2") -> BuilderElement:
        return BuilderElement(name="Heading", type="text", htmlTag=tag,
                              content=BuilderElementContent(innerText=text))

    def test_a_stock_phrase_is_reported_and_a_specific_heading_is_not(self):
        from app.services.ux_audit import audit_copy

        stock = audit_copy(self._site(self._heading("Where Little Minds Grow!")))
        self.assertEqual([f.rule for f in stock], ["generic-headline"])
        specific = audit_copy(self._site(self._heading("Forest Fridays, every week of the year")))
        self.assertEqual(specific, [])

    def test_a_split_headline_group_is_read_whole(self):
        from app.services.ux_audit import audit_copy

        group = BuilderElement(name="Heading group", type="container", htmlTag="h1", content=[
            BuilderElement(name="Heading", type="text", content=BuilderElementContent(innerText="Building bright")),
            BuilderElement(name="Heading accent", type="text", content=BuilderElementContent(innerText="futures")),
        ])
        self.assertEqual(len(audit_copy(self._site(group))), 1)

    def test_body_text_is_not_a_heading(self):
        from app.services.ux_audit import audit_copy

        body = BuilderElement(name="Body", type="text",
                              content=BuilderElementContent(innerText="We believe in learning through play."))
        self.assertEqual(audit_copy(self._site(body)), [])

    def test_the_copy_prompt_quotes_examples_from_the_same_list(self):
        from app.services.prompts import GENERIC_HEADLINES, WEBSITE_COPY_GUIDANCE

        quoted = [p for p in GENERIC_HEADLINES if f'"{p}"' in WEBSITE_COPY_GUIDANCE]
        self.assertGreaterEqual(len(quoted), 3)
        self.assertIn("brand.differentiators", WEBSITE_COPY_GUIDANCE)
