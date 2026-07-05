"""PagePlan.drop_empty_content_blocks: a block whose required content list is
empty (or below its min_length) is dropped — the intended "omit empty section"
outcome — instead of 502'ing the whole generation. The map is derived from the
block models, so newly added block kinds are covered automatically."""

import unittest

from app.models.content_blocks import _REQUIRED_LIST_FIELDS, PagePlan


def _page(blocks):
    return PagePlan(
        page_type="landing", slug="x", title="X",
        seo_title="X", seo_description="X", blocks=blocks,
    )


class DropEmptyBlocksTest(unittest.TestCase):
    def test_empty_list_blocks_are_dropped(self):
        # awards/timeline/stats/linkbar were the kinds missing from the old static
        # map — each with an empty list must now drop, not fail.
        page = _page(
            [
                {"kind": "hero", "headline": "Hi"},
                {"kind": "awards", "items": []},
                {"kind": "timeline", "items": []},
                {"kind": "stats", "items": []},
                {"kind": "linkbar", "links": []},
            ]
        )
        self.assertEqual([b.kind for b in page.blocks], ["hero"])

    def test_clients_below_min_length_is_dropped(self):
        # clients needs >=2; a single-logo strip should drop, not 502.
        page = _page(
            [
                {"kind": "hero", "headline": "Hi"},
                {"kind": "clients", "items": [{"name": "Acme"}]},
            ]
        )
        self.assertEqual([b.kind for b in page.blocks], ["hero"])

    def test_blocks_meeting_min_are_kept(self):
        page = _page(
            [
                {"kind": "awards", "items": [{"title": "Best Studio 2025"}]},
                {"kind": "clients", "items": [{"name": "Acme"}, {"name": "Globex"}]},
            ]
        )
        self.assertEqual([b.kind for b in page.blocks], ["awards", "clients"])

    def test_listless_blocks_are_untouched(self):
        page = _page([{"kind": "hero", "headline": "Hi"}, {"kind": "cta", "headline": "Go"}])
        self.assertEqual([b.kind for b in page.blocks], ["hero", "cta"])

    def test_derived_map_covers_newer_kinds(self):
        # Regression guard: these were silently missing before and caused 502s.
        for kind in ("awards", "clients", "stats", "timeline", "linkbar"):
            self.assertIn(kind, _REQUIRED_LIST_FIELDS)
        self.assertEqual(_REQUIRED_LIST_FIELDS["clients"][1], 2)


if __name__ == "__main__":
    unittest.main()
