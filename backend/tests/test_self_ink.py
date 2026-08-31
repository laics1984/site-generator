"""`wt-self-ink` — a control that paints its own surface states its own ink.

`.wt-ui-menu-button` (the mobile menu pill) declared a background and no colour,
so three call sites each guessed the ink from their surroundings, one of them
against a hard-coded `#ffffff` that was true of no renderer. On a dark palette
that put near-black ink on a near-black pill the moment the header solidified on
scroll; on a light palette it was white on white at scroll position 0.

Nothing here is generated — this is renderer chrome, invisible to the schema — so
these are drift tests over sibling repos, the same idiom as
`test_design_schemes.test_pinned_name_mirror_matches_the_renderer` and the
section-catalog parity check. Skipped rather than failed when a sibling repo is
absent, since the backend must stay testable on its own.
"""

import pathlib
import re
import unittest

_REPOS = pathlib.Path(__file__).resolve().parents[3]
_SITEGEN = pathlib.Path(__file__).resolve().parents[2]

# The overlay header forces white ink on `wt-header-ink` and its descendants
# with !important. This is the one subtree it must skip. Hand-duplicated in
# three places that must stay identical.
_EXCLUSION = ":not(.wt-self-ink):not(.wt-self-ink *)"

_INK_RULE_COPIES = (
    _REPOS / "webtree-public" / "components" / "public" / "PublicSiteShell.vue",
    _REPOS / "builder" / "src" / "index.css",
    _SITEGEN / "frontend" / "src" / "preview" / "preview.css",
)

# Every renderer that draws the pill. The preview is a port of the Vue one and
# the builder canvas is a third hand-written copy.
_TOGGLE_MARKUP = (
    _REPOS / "webtree-public" / "components" / "blocks" / "MenuBlock.vue",
    _REPOS / "builder" / "src" / "components" / "tabs" / "editor-components" / "menu.tsx",
    _SITEGEN / "frontend" / "src" / "preview" / "blocks" / "MenuBlock.tsx",
)

_SURFACE_RULES = (
    _REPOS / "webtree-public" / "app" / "assets" / "css" / "main.css",
    _SITEGEN / "frontend" / "src" / "preview" / "preview.css",
)


def _read(path: pathlib.Path, test: unittest.TestCase) -> str:
    if not path.exists():
        test.skipTest(f"{path} not checked out beside this repo")
    return path.read_text()


class SelfInkContractTest(unittest.TestCase):
    def test_menu_button_states_background_and_ink_together(self):
        # The defect in one line: a rule that paints a surface must state the
        # ink in the same place. `--builder-color-text` is 7:1 against
        # `--builder-color-background` by construction (theme.py builds every
        # palette's text with _ensure_contrast_against(min_ratio=7.0)), so the
        # pair needs no measuring — and a renderer that measures instead is how
        # this broke.
        for path in _SURFACE_RULES:
            with self.subTest(path.name):
                text = _read(path, self)
                match = re.search(r"\.wt-ui-menu-button\s*\{(.*?)\}", text, re.S)
                self.assertIsNotNone(match, "`.wt-ui-menu-button` rule is gone")
                body = match.group(1)
                self.assertIn("--builder-color-background", body)
                self.assertIn(
                    "color: var(--builder-color-text", body,
                    "the pill paints a surface without stating its ink",
                )
                # `.wt-page-header--overlay .wt-header-ink`'s shadow INHERITS.
                self.assertIn("text-shadow: none", body)

    def test_overlay_ink_rule_skips_self_inked_subtrees(self):
        # Only the DESCENDANT arm is narrowed. `.wt-header-ink` on its own
        # still carries the overlay's white ink and its text-shadow — the pill
        # is a descendant, never the marked element itself.
        for path in _INK_RULE_COPIES:
            with self.subTest(path.name):
                text = _read(path, self)
                arms = re.findall(r"\.wt-header-ink \*([^,{\n]*)", text)
                self.assertTrue(arms, "the overlay ink override is gone")
                for tail in arms:
                    self.assertEqual(
                        tail.strip(), _EXCLUSION,
                        "an un-narrowed descendant selector reaches into a "
                        "control that paints its own surface",
                    )

    def test_every_renderer_marks_the_pill(self):
        # A renderer that draws the pill without the marker gets the overlay's
        # white ink forced onto its own opaque surface — silently, and only in
        # one header phase.
        for path in _TOGGLE_MARKUP:
            with self.subTest(path.name):
                self.assertIn("wt-self-ink", _read(path, self))

    def test_no_renderer_measures_against_a_hard_coded_white(self):
        # The original bug, pinned: the pill's background is the palette's, not
        # white, so a contrast check against `'#ffffff'` is measuring a surface
        # that does not exist.
        for path in _TOGGLE_MARKUP:
            with self.subTest(path.name):
                text = _read(path, self)
                self.assertNotIn("pickAccessibleTextColor('#ffffff')", text)
                self.assertNotIn("getContrastRatio(preferredColor, '#ffffff')", text)


if __name__ == "__main__":
    unittest.main()
