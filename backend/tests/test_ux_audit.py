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


def _link(name, *, inner=None, aria=None):
    return BuilderElement(
        name=name, type="link",
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
