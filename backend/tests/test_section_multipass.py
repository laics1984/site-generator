"""Section-level multipass: a rich page (up to 9 sections) is generated across
several LIGHT calls — one per hero-anchored section group — then merged into one
coherent page, instead of one heavy call that can overload a local MLX server.

This keeps per-call weight small while preserving context within the page (every
group shares the hero thesis + the page's own source) and across the site
(parent_context is untouched; the merged page still yields one hero)."""

import asyncio
import unittest

from app.models.content_blocks import (
    AboutBlock,
    CtaBlock,
    HeroBlock,
    PagePlan,
    SourceContent,
)
from app.config import settings
from app.models.industry import PageScaffold
from app.services.planner import (
    ScaffoldedSitePlan,
    _generate_page_section_chunks,
    _needs_section_chunking,
    _split_page_sections,
)

_MAX_SECTIONS_PER_BATCH = settings.max_sections_per_batch

_NINE = ["hero", "features", "about", "services", "process",
         "team", "testimonials", "faq", "cta"]


class SplitPageSectionsTest(unittest.TestCase):
    def test_every_group_is_light_and_hero_anchored(self):
        groups = _split_page_sections(_NINE, _MAX_SECTIONS_PER_BATCH)
        self.assertTrue(len(groups) >= 2)
        for g in groups:
            self.assertLessEqual(len(g), _MAX_SECTIONS_PER_BATCH)
            self.assertEqual(g[0], "hero")  # anchored so each group is a valid page

    def test_body_sections_are_partitioned_in_order_without_loss(self):
        groups = _split_page_sections(_NINE, _MAX_SECTIONS_PER_BATCH)
        seen = []
        for g in groups:
            for s in g:
                if s != "hero" and s not in seen:
                    seen.append(s)
        self.assertEqual(seen, [s for s in _NINE if s != "hero"])

    def test_a_page_that_already_fits_is_not_split(self):
        six = ["hero", "features", "about", "services", "testimonials", "cta"]
        self.assertEqual(_split_page_sections(six, _MAX_SECTIONS_PER_BATCH), [six])

    def test_needs_section_chunking_triggers_only_past_the_cap(self):
        def sc(secs):
            return PageScaffold(page_type="home", slug="", title="H", sections=secs)
        self.assertTrue(_needs_section_chunking(sc(_NINE)))
        self.assertFalse(_needs_section_chunking(sc(_NINE[:6])))


class _FakeClient:
    """Returns one prepared page per chat_json call (one call per section group)."""

    def __init__(self, pages):
        self._pages = list(pages)
        self.calls = 0

    async def chat_json(self, **kwargs):
        self.calls += 1
        page = self._pages.pop(0)
        return ScaffoldedSitePlan(site_name="Sunny Kids", pages=[page])


def _page(*blocks):
    return PagePlan(
        page_type="home", slug="", title="Home", blocks=list(blocks),
        seo_title="Home | Sunny Kids", seo_description="A warm place to grow.",
    )


class SectionChunkGenerationTest(unittest.TestCase):
    def test_chunks_are_generated_lightly_and_merged_into_one_page(self):
        scaffold = PageScaffold(
            page_type="home", slug="", title="Home", is_homepage=True, sections=_NINE,
        )
        # Two groups → two calls. Each returns its own hero (repeated anchor) plus
        # distinct body sections; the merge must keep ONE hero and order by scaffold.
        client = _FakeClient([
            _page(HeroBlock(headline="Where little minds grow"), AboutBlock()),
            _page(HeroBlock(headline="A DIFFERENT hero"), CtaBlock()),
        ])
        source = SourceContent(source_kind="url", source_ref="x", raw_text="About us. Visit us.")

        merged, first = asyncio.run(
            _generate_page_section_chunks(source, None, scaffold, {}, {}, client)
        )

        # One light call per section group — never a single heavy call.
        self.assertEqual(client.calls, len(_split_page_sections(_NINE, _MAX_SECTIONS_PER_BATCH)))
        self.assertIsNotNone(merged)
        kinds = [b.kind for b in merged.blocks]
        # Distinct sections from both chunks assembled, in scaffold order.
        self.assertEqual(kinds, ["hero", "about", "cta"])
        # Exactly one hero, and it's the FIRST chunk's (context anchor wins).
        self.assertEqual(kinds.count("hero"), 1)
        hero = next(b for b in merged.blocks if b.kind == "hero")
        self.assertEqual(hero.headline, "Where little minds grow")
        self.assertIsNotNone(first)


if __name__ == "__main__":
    unittest.main()


class HeadingAwareSplitTest(unittest.TestCase):
    """split_raw_text seals chunks at source-section boundaries when the page's
    headings are provided, so a heading + its paragraphs stay in one chunk."""

    def test_chunk_seals_before_a_heading_once_half_full(self):
        from app.services.source_router import split_raw_text

        section_a = "Programme A\n" + ("Body line about programme A. " * 8).strip()
        section_b = "Programme B\n" + ("Body line about programme B. " * 8).strip()
        text = section_a + "\n" + section_b
        max_chars = len(text) - 20  # forces a split somewhere

        chunks = split_raw_text(
            text, max_chars, headings=["Programme A", "Programme B"]
        )

        self.assertEqual(len(chunks), 2)
        self.assertTrue(chunks[1].startswith("Programme B"))

    def test_without_headings_greedy_packing_is_unchanged(self):
        from app.services.source_router import split_raw_text

        text = "\n".join(f"line {i}" for i in range(100))
        plain = split_raw_text(text, 200)
        with_headings = split_raw_text(text, 200, headings=[])
        self.assertEqual(plain, with_headings)

    def test_small_page_stays_single_chunk(self):
        from app.services.source_router import split_raw_text

        self.assertEqual(
            split_raw_text("Heading\nBody", 500, headings=["Heading"]),
            ["Heading\nBody"],
        )
