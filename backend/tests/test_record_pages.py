"""Look-alike detail pages: laid out once per template, filled verbatim per page.

feruni.com has 170 /product/* pages built from one template. Planning each
through the content LLM cost a batch every few pages and had the model rewrite
product specifications. These tests pin the contract that replaced it:

  * a set is sibling pages sharing a parent AND a template signature;
  * the model answers in section NUMBERS — one call per set, never per page;
  * every word and photo on a record page is that page's own source content;
  * every failure falls back to a deterministic layout, never to an error.
"""

from __future__ import annotations

import unittest

from app.config import settings
from app.models.content_blocks import SectionCandidate, SourceCard, SourceContent
from app.models.industry import PageScaffold
from app.routers.generate import split_scaffolds
from app.services.llm import LlmError
from app.services.page_inference import infer_page_scaffolds
from app.services.record_pages import (
    RecordSlot,
    RecordTemplate,
    build_record_pages,
    default_record_template,
    fill_record_page,
    mark_record_sets,
    plan_record_template,
    without_shared_tails,
)

_CONTENT_FIELDS = {"headline", "subheadline", "heading", "subheading", "body", "title", "description", "caption"}


def _product(name: str, *, blurb: str | None = None) -> SourceContent:
    """One catalogue page in feruni's product template (trimmed): a title
    section, a colour rack, a description, a responsive duplicate of it, and a
    card row."""
    slug = name.lower()
    blurb = blurb or f"{name} turns colour into a tactile surface."
    return SourceContent(
        source_kind="url",
        source_ref=f"https://shop.test/product/{slug}/",
        url_path=f"/product/{slug}/",
        title=f"{name} - Shop",
        description=f"{name} tiles.",
        raw_text=f"{name}\n{blurb}",
        section_candidates=[
            SectionCandidate(heading=name.upper(), level=2, prose="colour design"),
            SectionCandidate(
                heading=f"{name} Colours",
                level=1,
                image_urls=[f"https://shop.test/{slug}-{i}.jpg" for i in range(4)],
            ),
            SectionCandidate(heading="Refined Formats", level=1, prose=blurb),
            SectionCandidate(heading="Refined Formats", level=1, prose=blurb),
            SectionCandidate(
                heading="Specification",
                level=1,
                cards=[
                    SourceCard(title="Size", body=f"{name} 230x76mm"),
                    SourceCard(title="Surface", body=f"{name} relief"),
                ],
            ),
        ],
    )


def _scaffold(page: SourceContent, **overrides) -> PageScaffold:
    slug = page.url_path.strip("/")
    fields = dict(
        page_type="landing",
        slug=slug,
        title=page.title.split(" - ")[0],
        sections=["hero"],
        parent_slug=slug.rsplit("/", 1)[0],
        from_source=True,
    )
    fields.update(overrides)
    return PageScaffold(**fields)


def _catalogue(count: int) -> tuple[list[PageScaffold], dict[str, SourceContent]]:
    pages = [_product(f"Tile{i}") for i in range(count)]
    return [_scaffold(p) for p in pages], {p.url_path.strip("/"): p for p in pages}


class _FakeLLM:
    def __init__(self, reply: RecordTemplate | None = None, error: Exception | None = None):
        self.reply = reply
        self.error = error
        self.calls = 0

    async def chat_json(self, **_):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.reply


class MarkRecordSetsTest(unittest.TestCase):
    def test_same_parent_and_template_form_a_set(self):
        scaffolds, sources = _catalogue(settings.record_set_min_pages)
        sources["product/tile3"] = _product("Tile3", blurb="The richest description on the whole site, by far.")

        mark_record_sets(scaffolds, sources)

        self.assertEqual({s.record_set for s in scaffolds}, {"product/tile3"})
        self.assertTrue(all(s.menu_hidden for s in scaffolds))

    def test_too_few_siblings_are_planned_individually(self):
        scaffolds, sources = _catalogue(settings.record_set_min_pages - 1)
        mark_record_sets(scaffolds, sources)
        self.assertTrue(all(s.record_set is None for s in scaffolds))

    def test_a_different_template_is_a_different_set(self):
        scaffolds, sources = _catalogue(settings.record_set_min_pages)
        odd = _product("Odd")
        odd.section_candidates = odd.section_candidates[:2]
        scaffolds.append(_scaffold(odd))
        sources["product/odd"] = odd

        mark_record_sets(scaffolds, sources)

        self.assertIsNone(scaffolds[-1].record_set)
        self.assertTrue(all(s.record_set for s in scaffolds[:-1]))

    def test_roster_linked_and_top_level_pages_are_never_records(self):
        scaffolds, sources = _catalogue(settings.record_set_min_pages)
        scaffolds[0].menu_hidden = True  # a roster already routed this page
        scaffolds[1].parent_slug = None

        mark_record_sets(scaffolds, sources)

        self.assertTrue(all(s.record_set is None for s in scaffolds))

    def test_page_inference_marks_a_crawled_catalogue(self):
        pages = [_product(f"Tile{i}") for i in range(settings.record_set_min_pages)]
        source = SourceContent(
            source_kind="url",
            source_ref="https://shop.test/",
            raw_text="Home page text.",
            discovered_pages=pages,
        )

        scaffolds = infer_page_scaffolds(source, industry="other")

        records = [s for s in scaffolds if s.record_set]
        self.assertEqual(len(records), len(pages))
        self.assertTrue(all(s.slug.startswith("product/") for s in records))


class SharedTailTest(unittest.TestCase):
    FOOTER = "Share on facebook Share on twitter Art. Beauty. Inspiration. COPYRIGHT 2023 © SHOP. ALL RIGHTS RESERVED."

    def test_the_footer_every_page_ends_with_is_removed_and_nothing_else(self):
        blurbs = ["Relievo plays with light and shadow.", "Eterna bridges old craft and new technology."]
        pages = [_product("Relievo", blurb=blurbs[0]), _product("Eterna", blurb=blurbs[1])]
        for page in pages:
            spec = page.section_candidates[2]
            spec.prose = f"{spec.prose}\n{self.FOOTER}"

        cleaned = without_shared_tails(pages)

        self.assertEqual([p.section_candidates[2].prose for p in cleaned], blurbs)
        self.assertIn(self.FOOTER, pages[0].section_candidates[2].prose)  # the caller's copy is untouched

    def test_a_short_shared_ending_is_content_not_chrome(self):
        pages = [_product("Relievo"), _product("Eterna")]
        # "turns colour into a tactile surface." is 6 shared words — below the minimum.
        self.assertEqual(without_shared_tails(pages), pages)


class RecordTemplateTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        settings.record_template_llm_enabled = True  # conftest pins it off

    async def test_the_reply_is_cleaned_to_real_sections_and_one_hero(self):
        reply = RecordTemplate(
            slots=[
                RecordSlot(kind="gallery", section=2),
                RecordSlot(kind="hero", section=1),
                RecordSlot(kind="hero", section=3),  # a second hero
                RecordSlot(kind="about", section=2),  # section already used
                RecordSlot(kind="poster", section=4),  # unknown kind
                RecordSlot(kind="features", section=99),  # no such section
                RecordSlot(kind="features", section=5),
            ]
        )

        template = await plan_record_template(_product("Relievo"), client=_FakeLLM(reply))

        self.assertEqual(
            [(s.kind, s.section) for s in template.slots],
            [("hero", 1), ("gallery", 2), ("features", 5)],
        )

    async def test_any_failure_falls_back_to_the_default_layout(self):
        exemplar = _product("Relievo")
        for client in (
            _FakeLLM(error=LlmError("down")),
            _FakeLLM(RecordTemplate(slots=[RecordSlot(kind="gallery", section=2)])),  # no hero
        ):
            with self.subTest(client=client):
                self.assertEqual(
                    await plan_record_template(exemplar, client=client),
                    default_record_template(exemplar),
                )

    def test_the_default_layout_skips_responsive_duplicates(self):
        template = default_record_template(_product("Relievo"))
        self.assertEqual(
            [(s.kind, s.section) for s in template.slots],
            [("hero", 1), ("gallery", 2), ("about", 3), ("features", 5)],
        )


class FillRecordPageTest(unittest.IsolatedAsyncioTestCase):
    def _texts(self, page) -> list[str]:
        found: list[str] = []

        def walk(value):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key in _CONTENT_FIELDS and isinstance(item, str):
                        found.append(item)
                    else:
                        walk(item)
            elif isinstance(value, list):
                for item in value:
                    walk(item)

        walk([block.model_dump() for block in page.blocks])
        return found

    def test_every_word_and_photo_is_the_pages_own(self):
        source = _product("Eterna")
        page = fill_record_page(
            default_record_template(_product("Relievo")), _scaffold(source), source
        )

        haystack = "\n".join(
            [s.heading for s in source.section_candidates]
            + [s.prose for s in source.section_candidates]
            + [c.title for s in source.section_candidates for c in s.cards]
            + [c.body for s in source.section_candidates for c in s.cards]
        )
        texts = self._texts(page)
        self.assertTrue(texts)
        for text in texts:
            self.assertIn(text, haystack)
            self.assertNotIn("Relievo", text)  # never the exemplar's content
        photos = {b.image_url for b in page.blocks if getattr(b, "image_url", None)}
        photos |= {i.image_url for b in page.blocks for i in getattr(b, "items", []) if getattr(i, "image_url", None)}
        self.assertTrue(photos <= set(source.images) | {u for s in source.section_candidates for u in s.image_urls})
        self.assertEqual((page.slug, page.parent_slug), ("product/eterna", "product"))

    def test_a_hero_section_with_a_description_shows_it_once_in_full(self):
        source = _product("Eterna")
        description = "Eterna is a patchwork collection. " * 8  # far past a tagline
        source.section_candidates[0].prose = description
        template = default_record_template(source)

        page = fill_record_page(template, _scaffold(source), source)

        self.assertIsNone(page.blocks[0].subheadline)
        self.assertEqual(self._texts(page).count(description.strip()), 1)

    def test_a_block_this_page_cannot_fill_still_shows_its_text(self):
        # feruni's sanitary-ware pages: spec cards with titles only, the spec
        # text in the section's prose.
        source = _product("Valve")
        spec = source.section_candidates[4]
        spec.cards = [SourceCard(title="Size"), SourceCard(title="Finish")]
        spec.prose = "Code WBFA301122CP cold angle valve."

        page = fill_record_page(default_record_template(_product("Relievo")), _scaffold(source), source)

        about = [b for b in page.blocks if b.kind == "about" and b.heading == "Specification"]
        self.assertEqual([b.body for b in about], ["Code WBFA301122CP cold angle valve."])

    def test_a_missing_section_is_skipped_and_the_page_still_opens(self):
        source = _product("Eterna")
        source.section_candidates = []

        page = fill_record_page(default_record_template(_product("Relievo")), _scaffold(source), source)

        self.assertEqual([b.kind for b in page.blocks], ["hero"])
        self.assertEqual(page.blocks[0].headline, "Eterna")

    async def test_one_layout_call_per_set_however_many_pages(self):
        settings.record_template_llm_enabled = True
        scaffolds, sources = _catalogue(8)
        mark_record_sets(scaffolds, sources)
        client = _FakeLLM(default_record_template(sources["product/tile0"]))

        pages = await build_record_pages(scaffolds, sources, client=client)

        self.assertEqual(client.calls, 1)
        self.assertEqual(len(pages), 8)


class SplitScaffoldsTest(unittest.TestCase):
    def test_record_pages_never_reach_the_content_planner(self):
        page = _product("Relievo")
        record = _scaffold(page, record_set="product/relievo")
        home = PageScaffold(page_type="home", slug="", title="Home", sections=["hero"], is_homepage=True)
        legal = PageScaffold(page_type="privacy", slug="privacy", title="Privacy", sections=[], is_legal=True)
        mirror = _scaffold(page, slug="bm/product/relievo", locale="bm", translation_of="product/relievo")

        split = split_scaffolds([home, record, legal, mirror])

        self.assertEqual(split.content, [home])
        self.assertEqual(split.records, [record])
        self.assertEqual(split.translations, [mirror])
        self.assertEqual(split.legal, [legal])


if __name__ == "__main__":
    unittest.main()
