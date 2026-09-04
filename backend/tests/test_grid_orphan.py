"""A lone item in the final row is the row, not a column.

`$gridFit` (template_filler) picks the column count that best fits the item
count, but no count divides every grid: 4 cards in a 3-wide grid, or any odd
count in a 2-wide one, leaves the last item packed into column 1 with the rest
of the row void beside it. The published renderer had two special cases for
this — a `:only-child` rule (a grid holding exactly one item) and a tablet-only
three-col orphan rule — which are the same sentence written twice for the two
counts someone happened to hit.

There is one rule now, stated per breakpoint, because "alone in its row" is only
answerable against that breakpoint's own column count. Three renderers answer
it: webtree-public's CSS, the vendored `preview.css`, and the builder canvas —
which cannot use a media query, because it simulates a device by state rather
than by viewport width.

`$bento` needs its own answer: its grid is an inline `display: grid` on a plain
container, so no `.wt-container-block--*` class ever reaches it. That answer is
`_bento_placement`, mirrored in the builder's `section-catalog.ts`.

The renderer halves are drift tests over sibling repos — the `test_self_ink` /
`test_renderer_video_sizing` idiom — skipped rather than failed when a sibling
repo is absent, so the backend stays testable on its own.
"""

import pathlib
import re
import unittest

from app.services.template_filler import (
    _BENTO_BREAKPOINT_COLUMNS,
    _BENTO_COLUMNS,
    _BENTO_MOBILE_COLUMNS,
    _BENTO_TABLET_COLUMNS,
    _bento_placement,
    _with_responsive_styles,
)
from app.models.builder_schema import ResponsiveStyles

_REPOS = pathlib.Path(__file__).resolve().parents[3]
_SITEGEN = pathlib.Path(__file__).resolve().parents[2]

_PUBLIC_CONTAINER = _REPOS / "webtree-public" / "components" / "blocks" / "ContainerBlock.vue"
_PREVIEW_CSS = _SITEGEN / "frontend" / "src" / "preview" / "preview.css"
_BUILDER_COLUMN_LAYOUT = _REPOS / "builder" / "src" / "lib" / "column-layout.ts"
_BUILDER_CONTAINER = (
    _REPOS / "builder" / "src" / "components" / "tabs" / "editor-components" / "container.tsx"
)

# The rule, per effective column count. A grid is `columns` wide at these
# widths, and its last child is alone in its row iff the child count leaves a
# remainder of 1 against that count — `nth-child(<columns>n + 1)`.
_CSS_RULES = (
    ".wt-container-block--two-col > :last-child:nth-child(odd)",
    ".wt-container-block--three-col > :last-child:nth-child(3n + 1)",
    ".wt-container-block--three-col > :last-child:nth-child(odd)",
)


def _read(path: pathlib.Path, test: unittest.TestCase) -> str:
    if not path.exists():
        test.skipTest(f"{path} not checked out beside this repo")
    return path.read_text()


def _rule_body(css: str, selector: str) -> str:
    """The declarations of the first rule with this exact selector."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return match.group(1) if match else ""


class RendererSpansTheLoneLastItemTest(unittest.TestCase):
    def _assert_all_rules_span_the_row(self, css: str, where: str) -> None:
        for selector in _CSS_RULES:
            body = _rule_body(css, selector)
            self.assertRegex(
                body,
                r"grid-column:\s*1\s*/\s*-1",
                f"{where} does not span the lone last item for `{selector}`",
            )

    def test_public_renderer_states_every_breakpoint(self):
        self._assert_all_rules_span_the_row(_read(_PUBLIC_CONTAINER, self), "ContainerBlock.vue")

    def test_preview_mirrors_the_public_renderer(self):
        # preview.css is GENERATED — `node scripts/vendor-preview-css.mjs
        # ../../webtree-public` from frontend/. A hand-edit here is the drift.
        self._assert_all_rules_span_the_row(_read(_PREVIEW_CSS, self), "preview.css")

    def test_the_three_col_desktop_rule_is_fenced_above_the_tablet_range(self):
        """A three-col grid is TWO columns wide at tablet, so its 3n+1 test is
        wrong there — it would widen a 4th card that is not alone in its row."""
        css = _read(_PUBLIC_CONTAINER, self)
        rule = ".wt-container-block--three-col > :last-child:nth-child(3n + 1)"
        before = css[: css.index(rule)]
        self.assertRegex(
            before.split("@media")[-1],
            r"min-width:\s*1024px",
            "the 3-column orphan test must sit inside the >=1024px query",
        )

    def test_the_superseded_special_cases_are_gone(self):
        """`:only-child` is the n = 0 case of the general rule and the tablet
        `span 2 / span 2` is its two-column case. Leaving either beside the rule
        that subsumes it is the duplication this replaced."""
        for path in (_PUBLIC_CONTAINER, _PREVIEW_CSS):
            css = _read(path, self)
            self.assertNotIn(".wt-container-block--column-layout > :only-child", css)
            self.assertNotIn("span 2 / span 2", css)


class BuilderCanvasMirrorsTheRuleTest(unittest.TestCase):
    def test_the_canvas_column_table_matches_the_css_breakpoints(self):
        """The canvas cannot read a media query, so it carries the column counts
        as data. This is the one place they can drift from the stylesheet."""
        source = _read(_BUILDER_COLUMN_LAYOUT, self)
        for layout, expected in (
            ("'2Col'", "{ Desktop: 2, Tablet: 2, Mobile: 1 }"),
            ("'3Col'", "{ Desktop: 3, Tablet: 2, Mobile: 1 }"),
        ):
            self.assertIn(f"{layout}: {expected}", source)

    def test_the_canvas_states_the_same_remainder_rule(self):
        source = _read(_BUILDER_COLUMN_LAYOUT, self)
        self.assertIn("count % columns === 1", source)
        self.assertIn("columns > 1", source)

    def test_the_canvas_derives_its_grid_and_its_orphan_from_one_answer(self):
        """One `currentColumnCount`, used to draw the grid and to place the last
        child. Two copies of that table is how the two disagree."""
        source = _read(_BUILDER_CONTAINER, self)
        self.assertIn("spansWholeRow(index, content.length, currentColumnCount)", source)
        self.assertIn("columnLayoutColumns(type as ColumnLayoutType, currentDevice)", source)


def _bento_pack(count: int, columns: int) -> list[tuple[int, int]]:
    """(tiles, empty cells) per row after laying `count` tiles out on `columns`.

    A deliberately independent implementation of CSS grid auto-placement, so the
    tests below measure the rendered result rather than restating
    `_bento_placement` back at itself.
    """
    breakpoint = next(
        (name for name, cols in _BENTO_BREAKPOINT_COLUMNS.items() if cols == columns),
        None,
    )
    occupied: dict[tuple[int, int], int] = {}
    boxes: list[tuple[int, int]] = []
    for index in range(count):
        base, overrides = _bento_placement(index, count)
        span = overrides.get(breakpoint, {}).get("gridColumn") or base["gridColumn"]
        width = columns if span == "1 / -1" else min(int(span.split()[1]), columns)
        height = 2 if base.get("gridRow") == "span 2" else 1
        row = 0
        while True:
            fit = next(
                (
                    col
                    for col in range(columns - width + 1)
                    if all(
                        (row + r, col + c) not in occupied
                        for r in range(height)
                        for c in range(width)
                    )
                ),
                None,
            )
            if fit is not None:
                occupied.update(
                    ((row + r, fit + c), index)
                    for r in range(height)
                    for c in range(width)
                )
                boxes.append((row, height))
                break
            row += 1
    rows = max(row + height for row, height in boxes)
    return [
        (
            len({occupied[(row, col)] for col in range(columns) if (row, col) in occupied}),
            sum((row, col) not in occupied for col in range(columns)),
        )
        for row in range(rows)
    ]


class BentoNeverStrandsATileTest(unittest.TestCase):
    """The catalog gates every bento at `minItems: 3`, but the builder lets an
    editor delete tiles, so the range starts at 1."""

    _COUNTS = range(1, 16)

    def test_no_row_holds_a_lone_tile_at_any_width(self):
        """The property, stated exactly: a row that is not full holds at least
        two tiles. One tile beside a void is the defect; two is a partial row."""
        for columns in (_BENTO_COLUMNS, _BENTO_TABLET_COLUMNS, _BENTO_MOBILE_COLUMNS):
            for count in self._COUNTS:
                for row, (tiles, empty) in enumerate(_bento_pack(count, columns)):
                    if empty:
                        self.assertGreaterEqual(
                            tiles,
                            2,
                            f"{count} tiles on {columns} columns: row {row} holds "
                            f"1 tile and {empty}/{columns} empty cells — stranded",
                        )

    def test_a_partial_row_of_two_is_left_alone(self):
        """The deliberate omission, matching the CSS rule: two tiles read as a
        balanced row, and widening one of them would unbalance it."""
        self.assertEqual(_bento_pack(5, _BENTO_COLUMNS)[-1], (2, 2))

    def test_the_lead_keeps_its_block_once_there_are_three_tiles(self):
        base, _ = _bento_placement(0, 3)
        self.assertEqual(base, {"gridColumn": "span 4", "gridRow": "span 2"})

    def test_the_lead_never_overflows_the_two_column_mobile_grid(self):
        """A span WIDER than the grid creates implicit columns, and those steal
        space from the explicit `1fr` tracks — the 4-wide lead shipped 382px/8px
        slivers on every phone."""
        for count in (3, 4, 6, 9):
            _, overrides = _bento_placement(0, count)
            self.assertEqual(overrides["mobile"], {"gridColumn": "1 / -1"}, count)

    def test_a_breakpoint_layer_is_emitted_only_when_the_widths_disagree(self):
        # 7 tiles strand the last one at 6 columns but not at 4.
        base, overrides = _bento_placement(6, 7)
        self.assertEqual(base, {"gridColumn": "1 / -1"})
        self.assertEqual(overrides["tablet"], {"gridColumn": "span 2"})
        # 6 tiles strand it at 4 columns but not at 6.
        base, overrides = _bento_placement(5, 6)
        self.assertEqual(base, {"gridColumn": "span 2"})
        self.assertEqual(overrides["tablet"], {"gridColumn": "1 / -1"})
        # 9 agrees everywhere, so nothing is written and the template's own
        # layers are left exactly as authored.
        self.assertEqual(_bento_placement(8, 9), ({"gridColumn": "span 2"}, {}))


class ResponsiveStyleMergeTest(unittest.TestCase):
    def test_the_templates_own_layers_ride_through(self):
        merged = _with_responsive_styles(
            ResponsiveStyles(mobile={"padding": "20px"}, tablet={"padding": "24px"}),
            {"tablet": {"gridColumn": "1 / -1"}},
        )
        # The layer that was not patched is untouched, not dropped.
        self.assertEqual(merged.mobile, {"padding": "20px"})
        self.assertEqual(merged.tablet, {"padding": "24px", "gridColumn": "1 / -1"})

    def test_a_tile_with_no_responsive_styles_gains_only_the_patch(self):
        merged = _with_responsive_styles(None, {"mobile": {"gridColumn": "1 / -1"}})
        self.assertIsNone(merged.tablet)
        self.assertEqual(merged.mobile, {"gridColumn": "1 / -1"})


if __name__ == "__main__":
    unittest.main()
