"""`locations-map-split` — a map-led locations section, card-free by design.

`locations-map-cards` was the only locations layout in the catalog, and it was
wrong at both ends of its own range:

* **One branch.** Its `Branch Grid` is a `$gridFit` repeat, and `$gridFit` maps
  `n == 1` through its `n <= 2` branch to `2Col` — one card in a two-column grid,
  half width, with a void beside it. No renderer had an `:only-child` rule.
* **Any branch, at the wrong width.** Its map is the one video node in the whole
  catalog authored with a fixed `height` and no `aspectRatio`. `VideoBlock`'s
  frame sized itself from its width alone, so the map overflowed and painted
  over the branch address whenever `width x 9/16` beat the authored height —
  ~77px of overlap at 1280px, and on every current iPhone.

The split layout removes the class of defect rather than one instance: it is a
flex column of full-width rows, so no count can strand a card in a column; its
map is sized by ratio, so it cannot outgrow its box; and it has no card, so
there is no `overflow: hidden` left to clip copy with.

The renderer half of the story is pinned in test_renderer_video_sizing.py.
"""

import asyncio
import unittest

from app.models.content_blocks import LocationItem, LocationsBlock
from app.services.section_content import (
    _PREFERENCE,
    _locations_content,
    select_template,
)
from app.services.template_filler import fill_template, get_template

SPLIT = "locations-map-split"
CARDS = "locations-map-cards"


async def _stub_image(query: str):
    return "", None


def _block(n: int) -> LocationsBlock:
    return LocationsBlock(
        heading="Visit us",
        subheading="Drop by any weekday.",
        items=[
            LocationItem(
                name=f"Branch {i}",
                address=f"{i} Jalan Ampang, 50450 Kuala Lumpur",
                hours="Mon-Fri 9am-6pm",
                phone="+60 12-345 6789",
                whatsapp="+60123456789",
            )
            for i in range(1, n + 1)
        ],
    )


def _fill(template_id: str, block: LocationsBlock):
    return asyncio.run(
        fill_template(
            get_template(template_id), _locations_content(block), resolve_image=_stub_image
        )
    )


def _walk(el):
    yield el
    if isinstance(el.content, list):
        for child in el.content:
            yield from _walk(child)


def _named(el, name: str):
    return next(n for n in _walk(el) if n.name == name)


def _breakpoint(el, device: str) -> dict:
    """A node's own overrides for one breakpoint (`responsiveStyles` is a model)."""
    responsive = el.responsiveStyles
    return (getattr(responsive, device, None) or {}) if responsive else {}


def _select(block: LocationsBlock) -> str:
    content = _locations_content(block)
    return select_template(
        "locations", content, preferred_ids=_PREFERENCE["locations"](content, block)
    )["id"]


class SelectionTest(unittest.TestCase):
    def test_the_split_is_the_default_at_every_branch_count(self):
        for n in (1, 2, 3, 6):
            with self.subTest(branches=n):
                self.assertEqual(_select(_block(n)), SPLIT)

    def test_the_card_grid_stays_reachable(self):
        # "Alongside", not "instead of": the design brain can still offer it and
        # it stays insertable in the builder's section browser.
        block = _block(3)
        chosen = select_template(
            "locations", _locations_content(block), explicit_id=CARDS
        )
        self.assertEqual(chosen["id"], CARDS)

    def test_both_layouts_read_the_same_content(self):
        # The split declares slot-for-slot what the grid declares, which is what
        # keeps `_locations_content` (and `is_feasible`, and facebook_authority's
        # single-item rewrite) untouched by this layout existing.
        self.assertEqual(get_template(SPLIT)["slots"], get_template(CARDS)["slots"])


class AlternatingRowsTest(unittest.TestCase):
    def test_rows_are_a_flex_column_not_a_grid(self):
        rows = _named(_fill(SPLIT, _block(1)), "Branch Rows")
        self.assertEqual(rows.type, "container")
        self.assertEqual(rows.styles.get("flexDirection"), "column")

    def test_a_single_branch_fills_the_row(self):
        rows = _named(_fill(SPLIT, _block(1)), "Branch Rows")
        self.assertEqual(len(rows.content), 1)
        self.assertEqual(rows.content[0].styles.get("width"), "100%")

    def test_the_map_alternates_sides_down_the_list(self):
        rows = _named(_fill(SPLIT, _block(5)), "Branch Rows")
        directions = [row.styles.get("flexDirection") for row in rows.content]
        self.assertEqual(
            directions, ["row", "row-reverse", "row", "row-reverse", "row"]
        )

    def test_every_row_keeps_the_map_first_in_the_dom(self):
        # `row-reverse` is a paint order, not a source order. Reordering the
        # nodes instead would put the address above the map on a phone for every
        # other branch, since the stacked direction is plain `column`.
        rows = _named(_fill(SPLIT, _block(4)), "Branch Rows")
        for row in rows.content:
            self.assertEqual(
                [child.name for child in row.content],
                ["Branch Map Frame", "Branch Details"],
            )

    def test_rows_stack_map_first_on_a_phone_in_both_phases(self):
        rows = _named(_fill(SPLIT, _block(2)), "Branch Rows")
        for row in rows.content:
            self.assertEqual(
                _breakpoint(row, "mobile").get("flexDirection"),
                "column",
                "the cycle patches base styles only, so the breakpoint still wins",
            )


class MapSizingTest(unittest.TestCase):
    def test_the_map_states_a_ratio_and_never_a_height(self):
        node = _named(_fill(SPLIT, _block(1)), "Branch Map")
        self.assertEqual(node.type, "video")
        self.assertTrue(node.styles.get("aspectRatio"))
        self.assertIsNone(node.styles.get("height"))
        for device in ("mobile", "tablet"):
            self.assertIsNone(
                _breakpoint(node, device).get("height"),
                f"a {device} height would reintroduce the overflow this replaces",
            )

    def test_the_map_carries_its_accessible_name(self):
        # Without it every map on the published site announces itself as
        # "Embedded video" — the renderer's default for the element type.
        node = _named(_fill(SPLIT, _block(1)), "Branch Map")
        self.assertEqual(node.content.title, "Map: Branch 1")


class NoCardCanClipTheCopyTest(unittest.TestCase):
    def test_nothing_in_the_section_hides_its_overflow(self):
        # Except the map itself, which clips its own iframe to its radius.
        tree = _fill(SPLIT, _block(3))
        clippers = [
            n.name
            for n in _walk(tree)
            if (n.styles or {}).get("overflow") == "hidden" and n.name != "Branch Map"
        ]
        self.assertEqual(clippers, [])

    def test_nothing_pins_its_height_to_its_container(self):
        tree = _fill(SPLIT, _block(3))
        pinned = [n.name for n in _walk(tree) if (n.styles or {}).get("height") == "100%"]
        self.assertEqual(pinned, [])


class CopyStatesItsInkInTokensTest(unittest.TestCase):
    """The card paid for its hard-coded slate ink with its own white fill.

    With the card gone the copy sits straight on the section band, which the
    luminance pass may resolve dark — so a literal `rgba(15,23,42,...)` address
    would ship near-black on near-black. Tokens are what let
    `enforce_text_contrast` measure and flip them.
    """

    def test_every_text_ink_is_a_theme_token(self):
        tree = _fill(SPLIT, _block(2))
        details = _named(tree, "Branch Details")
        for node in _walk(details):
            if node.type != "text":
                continue
            with self.subTest(node=node.name):
                self.assertRegex(
                    str((node.styles or {}).get("color", "")),
                    r"^var\(--builder-color-",
                    "a literal ink here is invisible on a dark band",
                )


if __name__ == "__main__":
    unittest.main()
