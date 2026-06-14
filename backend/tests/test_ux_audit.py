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


class DarkTextSafetyTest(unittest.TestCase):
    """enforce_dark_text_safety fixes legacy dark text only where it sits on a
    theme-following background, and never touches fixed/colored surfaces."""

    def _run(self, tree):
        from app.services.section_content import enforce_dark_text_safety
        n = enforce_dark_text_safety([tree])
        return tree, n

    def test_dark_text_on_page_bg_retargeted(self):
        tree = _container(
            "band",
            [_text("copy", "Hello", color="rgba(15,23,42,0.7)")],
            backgroundColor="var(--builder-page-background, #ffffff)",
        )
        tree, n = self._run(tree)
        self.assertEqual(n, 1)
        copy = tree.content[0]
        self.assertEqual(copy.styles["color"], "var(--builder-color-text, #0f172a)")
        self.assertEqual(copy.styles.get("opacity"), "70%")  # alpha preserved

    def test_secondary_var_heading_retargeted(self):
        tree = _container(
            "sec",
            [_text("h", "Title", color="var(--builder-color-secondary, #0f172a)")],
            backgroundColor="var(--builder-color-surface, #f8fafc)",
        )
        tree, n = self._run(tree)
        self.assertEqual(tree.content[0].styles["color"], "var(--builder-color-text, #0f172a)")

    def test_text_on_fixed_white_card_untouched(self):
        # A fixed white card stays white in dark mode → its dark text must stay dark.
        tree = _container(
            "card",
            [_text("copy", "Hello", color="#0f172a")],
            backgroundColor="#ffffff",
        )
        tree, n = self._run(tree)
        self.assertEqual(n, 0)
        self.assertEqual(tree.content[0].styles["color"], "#0f172a")

    def test_text_on_photo_untouched(self):
        tree = _container(
            "hero",
            [_text("copy", "Hello", color="#0f172a")],
            backgroundImage="url(photo.jpg)",
        )
        _, n = self._run(tree)
        self.assertEqual(n, 0)

    def test_theme_var_and_primary_text_left_alone(self):
        tree = _container(
            "band",
            [
                _text("body", "x", color="var(--builder-color-text, #0f172a)"),
                _text("eyebrow", "y", color="var(--builder-color-primary, #2563eb)"),
            ],
            backgroundColor="var(--builder-page-background, #ffffff)",
        )
        _, n = self._run(tree)
        self.assertEqual(n, 0)


if __name__ == "__main__":
    unittest.main()
