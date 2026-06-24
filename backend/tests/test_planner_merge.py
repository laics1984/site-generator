import unittest

from app.models.content_blocks import TimelineBlock, TimelineItem
from app.services.planner import _merge_blocks_of_kind, _merge_page_plans
from app.models.content_blocks import PagePlan
from app.models.industry import PageScaffold


class MergeTimelineBlocksTest(unittest.TestCase):
    def test_distinct_milestones_sharing_a_title_are_not_deduped(self):
        # Two genuinely different events ("Expansion" in 1998 vs 2015) must
        # both survive — a title-only dedupe key would wrongly collapse them.
        chunk_one = TimelineBlock(
            items=[TimelineItem(year="1998", title="Founded", description=None)]
        )
        chunk_two = TimelineBlock(
            items=[TimelineItem(year="2015", title="Founded", description="Second site opened.")]
        )

        merged = _merge_blocks_of_kind([chunk_one, chunk_two])

        self.assertEqual(len(merged.items), 2)
        self.assertEqual({item.year for item in merged.items}, {"1998", "2015"})

    def test_genuine_duplicate_across_chunk_boundary_is_deduped(self):
        # The exact same milestone repeated verbatim in both chunks (e.g. a
        # paragraph straddling the chunk split) should still collapse to one.
        chunk_one = TimelineBlock(
            items=[TimelineItem(year="1998", title="Founded", description=None)]
        )
        chunk_two = TimelineBlock(
            items=[
                TimelineItem(year="1998", title="Founded", description=None),
                TimelineItem(year="2015", title="Second location opened", description=None),
            ]
        )

        merged = _merge_blocks_of_kind([chunk_one, chunk_two])

        self.assertEqual(len(merged.items), 2)

    def test_merged_timeline_items_end_up_chronologically_ordered(self):
        chunk_one = TimelineBlock(
            items=[TimelineItem(year="2015", title="Second location opened")]
        )
        chunk_two = TimelineBlock(
            items=[TimelineItem(year="1998", title="Founded")]
        )

        merged = _merge_blocks_of_kind([chunk_one, chunk_two])

        self.assertEqual([item.year for item in merged.items], ["1998", "2015"])

    def test_merge_page_plans_preserves_chronological_timeline_order(self):
        scaffold = PageScaffold(
            page_type="about", slug="about", title="About", sections=["hero", "timeline", "cta"],
        )
        page_one = PagePlan(
            page_type="about",
            slug="about",
            title="About",
            blocks=[TimelineBlock(items=[TimelineItem(year="2015", title="Second location")])],
            seo_title="About - Example",
            seo_description="About us.",
        )
        page_two = PagePlan(
            page_type="about",
            slug="about",
            title="About",
            blocks=[TimelineBlock(items=[TimelineItem(year="1998", title="Founded")])],
            seo_title="About - Example",
            seo_description="About us.",
        )

        merged_page = _merge_page_plans([page_one, page_two], scaffold)

        timeline = next(blk for blk in merged_page.blocks if blk.kind == "timeline")
        self.assertEqual([item.year for item in timeline.items], ["1998", "2015"])


if __name__ == "__main__":
    unittest.main()
