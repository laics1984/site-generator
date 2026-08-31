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
from app.services.theme import build_theme
from app.services.ux_audit import audit_site, summarize


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

    def test_dark_secondary_text_on_dark_band_flips_to_light(self):
        theme = build_theme("#d55d62", color_scheme="dark")
        tree = _container(
            "sec",
            [_text("h", "Title", color="var(--builder-color-secondary)", fontSize="40px")],
            backgroundColor="var(--builder-color-secondary)",
        )
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 1)
        self.assertEqual(tree.content[0].styles["color"], "#ffffff")

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
        self.assertEqual(tree.content[0].styles["color"], "#ffffff")
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
        self.assertEqual(tree.content[0].styles["color"], "#0f172a")

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
        self.assertEqual(cta.styles["color"], "#ffffff")
        # Colour restated, the author's weight (1px solid, 0.14) preserved.
        self.assertEqual(cta.styles["border"], "1px solid rgba(255,255,255,0.14)")

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
        opaque brand ramp. Only an OPAQUE fill hides what it sits on — treating
        this one as the surface composites white over a backdrop nobody can
        read and flips the CTA's white label to near-black."""
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
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 0)
        self.assertEqual(tree.content[0].styles["color"], "#ffffff")

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
        tree, n = self._run(tree, theme)
        self.assertEqual(n, 0)
        self.assertEqual(tree.content[0].styles["color"], "#ffffff")

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
        self.assertEqual(tree.content[0].styles["color"], "#ffffff")  # ink still fixed
        self.assertEqual(tree.content[0].styles["border"], mix)  # outline untouched

    def test_measured_surface_overrides_the_photo_bail_out(self):
        """`surface=` is a caller saying "I painted this and measured it" — the
        photo rule (which exists because a photo's luminance is unknowable from
        the styles) then has nothing left to protect, and the node's own
        declared fill is a colour the wash covered, so neither is read."""
        theme = build_theme("#2563eb", color_scheme="dark")
        tree = _container(
            "hero",
            [_text("copy", "Hello", color="#0f172a")],
            backgroundColor="var(--builder-page-background, #ffffff)",
            backgroundImage="linear-gradient(135deg, rgba(2,6,23,0.8), rgba(37,99,235,0.18)), url(photo.jpg)",
        )
        from app.services.section_content import enforce_text_contrast

        self.assertEqual(enforce_text_contrast([tree], theme), 0)  # unmeasured: hands off
        self.assertEqual(enforce_text_contrast([tree], theme, surface="#0a0f1f"), 1)
        self.assertEqual(tree.content[0].styles["color"], "#ffffff")

    def test_a_photo_nested_under_a_measured_surface_still_owns_its_ink(self):
        """The override is for the elements passed in, not their subtrees: a
        photo tile inside the section is a fill nobody measured."""
        theme = build_theme("#2563eb", color_scheme="dark")
        tile = _container(
            "tile",
            [_text("caption", "On the photo", color="#0f172a")],
            backgroundImage="url(tile.jpg)",
        )
        tree = _container("hero", [tile], backgroundImage="url(photo.jpg)")
        from app.services.section_content import enforce_text_contrast

        self.assertEqual(enforce_text_contrast([tree], theme, surface="#0a0f1f"), 0)

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
        # below 7:1; brand-token text on the correct side is likewise left alone.
        theme = build_theme("#2563eb", color_scheme="dark")
        tree = _container(
            "band",
            [
                _text("body", "x", color="var(--builder-color-text)"),
                _text("eyebrow", "y", color="var(--builder-color-primary)"),
            ],
            backgroundColor="var(--builder-page-background)",
        )
        _, n = self._run(tree, theme)
        self.assertEqual(n, 0)

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
        # risked stomping the badge's own intentionally brand-coloured label.
        theme = build_theme("#2563eb", color_scheme="light")
        el = _text(
            "age-badge",
            "0–2 yrs",
            color="var(--builder-color-primary-ink, #2563eb)",
            backgroundColor="color-mix(in srgb, var(--builder-color-primary, #2563eb) 12%, #ffffff)",
        )
        _, n = self._run(el, theme)
        self.assertEqual(n, 0)


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


if __name__ == "__main__":
    unittest.main()
