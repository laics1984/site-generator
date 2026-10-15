"""The contact form's Required chip states its own surface and its own ink.

A pixel survey of 54 generated sites (every design scheme x three palettes)
measured the marker at a median 1.95:1 — 216 of 216 under AA. Three things
stacked: the chip inked raw `--builder-color-primary`; its fill was a
translucent 10% primary tint over whatever band, glow or card it landed on, so
the surface behind it was unknowable; and the label's `opacity: 0.7` faded both
back toward the band. The contact band itself varies — `surface` on most
schemes, the page background or a hue tint on others — so no ink chosen for one
of them could have been right.

The chip now paints an OPAQUE tint (`primary` washed into `background`) and
wears the brand's small-text ink held to AA on that tint — the pair both token
builders derive as `--builder-color-primary-tint` / `-tint-ink`. Same rule as
`test_self_ink.py`: a rule that paints a surface states the ink in the same
place. And the label's opacity sits on the parts meant to recede, never on the
chip.

Nothing here is generated — this is renderer chrome, invisible to the schema —
so these are drift tests over sibling repos, skipped when one is absent.
"""

import pathlib
import re
import unittest

_REPOS = pathlib.Path(__file__).resolve().parents[3]
_SITEGEN = pathlib.Path(__file__).resolve().parents[2]

_PUBLIC_FORM = _REPOS / "webtree-public" / "components" / "blocks" / "ContactFormBlock.vue"
_PREVIEW_CSS = _SITEGEN / "frontend" / "src" / "preview" / "preview.css"
_CANVAS_FORM = _REPOS / "builder" / "src" / "components" / "tabs" / "editor-components" / "contact-form.tsx"
_PUBLIC_STYLES = _REPOS / "webtree-public" / "lib" / "styles.ts"
_BUILDER_STYLES = _REPOS / "builder" / "src" / "lib" / "builder-styles.ts"

_TINT = "--builder-color-primary-tint,"
_TINT_INK = "--builder-color-primary-tint-ink"


def _read(path: pathlib.Path, test: unittest.TestCase) -> str:
    if not path.exists():
        test.skipTest(f"{path} not checked out beside this repo")
    return path.read_text()


def _rule(css: str, selector: str, test: unittest.TestCase) -> str:
    match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    test.assertIsNotNone(match, f"`{selector}` rule is gone")
    return match.group(1)


class RequiredChipInkTest(unittest.TestCase):
    def test_the_chip_paints_its_tint_and_states_its_ink_together(self):
        for path in (_PUBLIC_FORM, _PREVIEW_CSS):
            with self.subTest(path.name):
                body = _rule(_read(path, self), ".wt-contact-form__required", self)
                self.assertRegex(body, r"\bbackground:\s*var\(" + re.escape(_TINT))
                self.assertRegex(body, r"\bcolor:\s*var\(" + re.escape(_TINT_INK))
                # The tint is opaque so the surface is known; a translucent
                # fill is the half that made every ink a guess.
                self.assertNotIn("transparent", body)

    def test_the_canvas_chip_reads_the_same_pair(self):
        source = _read(_CANVAS_FORM, self)
        chip = re.search(r"style=\{\{(.*?)\}\}\s*>\s*Required", source, re.S)
        self.assertIsNotNone(chip, "the canvas no longer draws a Required chip")
        self.assertRegex(chip.group(1), r"\bcolor:\s*'var\(" + re.escape(_TINT_INK))
        self.assertRegex(chip.group(1), r"\bbackgroundColor:\s*'var\(" + re.escape(_TINT))

    def test_no_renderer_fades_the_chip_with_its_label(self):
        # Opacity on an ancestor composites the chip's ink AND fill toward the
        # band, so it must sit on the icon and the label text, not their row.
        for path in (_PUBLIC_FORM, _PREVIEW_CSS):
            with self.subTest(path.name):
                css = _read(path, self)
                self.assertNotIn("opacity", _rule(css, ".wt-contact-form__label", self))
                self.assertRegex(
                    css,
                    r"\.wt-contact-form__icon,\s*\.wt-contact-form__label-text\s*\{\s*opacity: 0\.7;",
                )
        source = _read(_CANVAS_FORM, self)
        row = re.search(r'className="([^"]*)"[^>]*>\s*<Icon\b', source)
        self.assertIsNotNone(row, "the canvas label row is gone")
        self.assertNotIn("opacity-", row.group(1))

    def test_the_preview_carries_the_published_rule(self):
        # preview.css is generated (vendor-preview-css.mjs); this catches a
        # renderer edit that was never re-vendored.
        for selector in (".wt-contact-form__label", ".wt-contact-form__required"):
            with self.subTest(selector):
                self.assertEqual(
                    _rule(_read(_PUBLIC_FORM, self), selector, self),
                    _rule(_read(_PREVIEW_CSS, self), selector, self),
                )

    def test_both_token_builders_derive_the_pair_alike(self):
        # The published site and the builder canvas cannot import each other;
        # the preview's copy is pinned verbatim by
        # test_cms_list_layout.test_the_preview_token_builder_is_vendored_verbatim.
        public = _read(_PUBLIC_STYLES, self)
        builder = _read(_BUILDER_STYLES, self)
        for source in (public, builder):
            self.assertRegex(source, r"PRIMARY_TINT_WEIGHT = 0\.1\b")
        self.assertIn("ensureContrast(primaryInk, tint, 4.5)", public)
        self.assertIn("ensureContrast(primaryInk(colors), tint, 4.5)", builder)
        self.assertIn("'--builder-color-primary-tint': primaryTintPair.tint", public)
        self.assertIn("'--builder-color-primary-tint-ink': primaryTintPair.ink", public)
        self.assertIn("['--builder-color-primary-tint' as string]: tint", builder)
        self.assertIn("['--builder-color-primary-tint-ink' as string]: ink", builder)


if __name__ == "__main__":
    unittest.main()
