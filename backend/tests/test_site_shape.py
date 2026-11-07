"""A document is built as the site the person chose: one landing page, or pages.

A landing-page brief — a title, then "01 Hero" … "08 Pricing", "Footer" — reads
as one page, and does so every time. The person making the site may still want
a website of pages; the picker's "Site type" asks, and the document is read
again in that shape. Multi-page files a landing brief's parts the way a
multi-page site does: the parts a visitor goes to on purpose (pricing, the
questions, about, contact) get pages; the story that sells stays home.

What these tests pin:
  * the shape a person picks overrides the document, and the document's own
    reading stays on the plan for the picker to show;
  * a landing brief split into pages keeps its selling parts on the homepage,
    in order, and a part after a page goes back home;
  * an author's section numbers come off as a run, never one at a time;
  * a section title over a kept headline is kept, so the model cannot drop
    the parts the pages are grouped by;
  * "01 Hero" opens the page instead of repeating as an about block, and an
    FAQ written at body size is an FAQ.
"""

from __future__ import annotations

import asyncio
import unittest

from app.config import settings
from app.routers.document import _build_preview
from app.services.doc_structure import outline_plan, split_into_pages
from app.services.outline_pages import plan_pages
from app.services.outline_sections import outline_sections
from app.services.page_inference import infer_page_scaffolds
from app.services.paste_structure import (
    PasteOutline,
    PasteOutlinePage,
    _assemble,
    structure_document,
)
from app.services.source_outline import OutlineBlock, ParsedDocument, strip_numbered_titles

B = OutlineBlock

# A landing brief in the shape real ones take: a document title, numbered
# section titles over their headlines, cards, an FAQ at body size, plans.
_LANDING = [
    B(1, "Acme landing page copy"),
    B(0, "For small studio owners"),
    B(1, "01 Hero"),
    B(2, "Every booking in one place"),
    B(0, "Bookings, reminders and payments, sorted."),
    B(1, "02 Sound familiar"),
    B(2, "Bookings are hard to keep up with"),
    B(6, "Calls during class"), B(0, "You miss them while you teach."),
    B(6, "Lost messages"), B(0, "Requests get buried in chats."),
    B(1, "03 How Acme helps"),
    B(2, "One calendar for the whole studio"),
    B(6, "Online booking"), B(0, "Clients book themselves."),
    B(6, "Reminders"), B(0, "Fewer no-shows."),
    B(1, "04 How it works"),
    B(2, "Set up in a day"),
    B(0, "We import your timetable and you are live."),
    B(1, "05 Common questions"),
    B(0, "Do I need technical skills?"),
    B(0, "No. We set everything up for you."),
    B(0, "Can I cancel any time?"),
    B(0, "Yes, there is no contract."),
    B(0, "Do clients need an app?"),
    B(0, "No, they book from your website."),
    B(1, "06 Pricing"),
    B(2, "Plans for every studio"),
    B(6, "Solo"), B(0, "One instructor"), B(0, "RM 59 / month"),
    B(6, "Team"), B(0, "Up to five instructors"), B(0, "RM 149 / month"),
    B(1, "07 Testimonials"),
    B(2, "Studios that switched"),
    B(0, "We stopped losing bookings in the first week."),
    B(1, "Footer"),
    B(0, "Acme · bookings made simple"),
]


def _doc(blocks=_LANDING, kind="docx") -> ParsedDocument:
    return ParsedDocument(
        source_kind=kind, source_ref=f"brief.{kind}", title="Acme",
        raw_text="\n".join(b.text for b in blocks),
        headings=[b.text for b in blocks if b.level], outline=list(blocks),
    )


def _pages(shape=None):
    return [(p.url_path, p.title) for p in split_into_pages(_doc(), shape=shape).discovered_pages]


class ShapeTest(unittest.TestCase):
    def test_a_landing_brief_reads_as_one_page(self):
        plan = outline_plan(_doc())
        self.assertTrue(plan.one_page)
        self.assertTrue(plan.suggests_one_page)
        self.assertEqual(_pages(), [])

    def test_multi_page_files_its_parts_by_practice(self):
        plan = outline_plan(_doc(), "multi_page")
        self.assertFalse(plan.one_page)
        # The document's own reading stays on the plan for the picker.
        self.assertTrue(plan.suggests_one_page)
        self.assertIn("one page", plan.reason)
        self.assertEqual(
            _pages("multi_page"),
            [("/common-questions", "Common questions"), ("/pricing", "Pricing")],
        )

    def test_the_selling_parts_stay_home_in_order(self):
        home = split_into_pages(_doc(), shape="multi_page")
        headings = [s.heading for s in home.section_candidates]
        for kept in ("Sound familiar", "How Acme helps", "How it works", "Testimonials"):
            self.assertIn(kept, headings)
        # Testimonials came after the Pricing page: they went back home rather
        # than onto the page before them.
        pricing = next(p for p in home.discovered_pages if p.url_path == "/pricing")
        self.assertNotIn("Studios that switched", pricing.raw_text)
        self.assertIn("Studios that switched", home.raw_text)
        self.assertIn("RM 149 / month", pricing.raw_text)

    def test_one_page_folds_a_site_map(self):
        blocks = [
            B(1, "HOME"), B(0, "Welcome."),
            B(1, "ABOUT US"), B(0, "Since 2011."),
            B(1, "CONTACT"), B(0, "Call us."),
        ]
        self.assertEqual(len(split_into_pages(_doc(blocks)).discovered_pages), 2)
        source = split_into_pages(_doc(blocks), shape="one_page")
        self.assertEqual(source.discovered_pages, [])
        self.assertIn("Since 2011.", source.raw_text)

    def test_multi_page_with_nothing_to_file_is_still_a_website(self):
        blocks = [
            B(1, "Acme"), B(0, "We roast."),
            B(2, "Why our beans"), B(0, "Slow roasting."),
            B(2, "How we roast"), B(0, "Small batches."),
        ]
        plan = plan_pages(blocks, shape="multi_page")
        self.assertFalse(plan.one_page)
        self.assertEqual(plan.pages, ())
        # The recipe then builds the industry's pages around the whole homepage.
        source = split_into_pages(_doc(blocks), shape="multi_page")
        scaffolds = infer_page_scaffolds(source, industry="other", single_page=False)
        self.assertIn("contact", [s.page_type for s in scaffolds])


class NumberedTitlesTest(unittest.TestCase):
    def test_a_run_loses_its_numbers(self):
        titles = [b.text for b in strip_numbered_titles(_LANDING) if b.level == 1]
        self.assertEqual(titles[1:4], ["Hero", "Sound familiar", "How Acme helps"])
        self.assertIn("Pricing", titles)

    def test_a_lone_number_is_copy(self):
        blocks = [B(2, "12 children per class"), B(0, "x"), B(2, "3 Steps to enrol"), B(0, "y")]
        self.assertEqual(strip_numbered_titles(blocks), blocks)


class KeptSectionTitleTest(unittest.TestCase):
    def test_a_section_title_over_a_kept_headline_is_kept(self):
        # The model marks the numbered titles as layout labels; each sits over
        # a headline it kept, so each is the title of kept copy.
        blocks = [
            B(1, "01 Hero"), B(2, "Every booking in one place"), B(0, "Sorted."),
            B(1, "02 Pricing"), B(2, "Plans for every studio"), B(0, "RM 59 / month"),
            B(1, "03 Notes"), B(0, "Internal only."),
        ]
        page = PasteOutlinePage(title="Home", page_type="home", first_line=1, last_line=8)
        outline = PasteOutline(pages=[page], skip_lines=[1, 4, 7, 8])
        shell = ParsedDocument(source_kind="pdf", source_ref="b.pdf", title=None, raw_text="")
        document = _assemble(blocks, outline, [page], shell, given_title=None).document
        texts = [b.text for b in document.outline]
        self.assertIn("Pricing", texts)
        self.assertNotIn("Notes", texts)  # its own copy was skipped too
        self.assertNotIn("Internal only.", texts)


class HeroAndQuestionsTest(unittest.TestCase):
    def test_the_hero_opens_the_page_instead_of_repeating(self):
        source = split_into_pages(_doc())
        home = infer_page_scaffolds(source, industry="other", single_page=True)[0]
        # Body: sound familiar, how it helps, how it works, questions, plans,
        # testimonials — no about block for the hero or the document's title.
        self.assertEqual(
            home.sections,
            ["hero", "services", "features", "about", "faq", "pricing", "about", "contact", "cta"],
        )

    def test_questions_at_body_size_are_an_faq(self):
        section = next(
            s for s in outline_sections(strip_numbered_titles(_LANDING))
            if s.heading == "Common questions"
        )
        self.assertEqual(section.card_kind, "questions")
        self.assertEqual(
            [card.title for card in section.cards],
            ["Do I need technical skills?", "Can I cancel any time?", "Do clients need an app?"],
        )
        self.assertEqual(section.cards[1].body, "Yes, there is no contract.")

    def test_a_rhetorical_question_is_not_an_faq(self):
        blocks = [B(2, "Sound familiar?"), B(0, "Missed calls?"), B(0, "Lost messages?"),
                  B(0, "We fix that.")]
        section = outline_sections([B(1, "Acme"), *blocks, B(2, "More"), B(0, "x"), B(2, "End"), B(0, "y")])
        self.assertTrue(all(s.card_kind != "questions" for s in section))


class _FakeLLM:
    def __init__(self, outline):
        self.outline = outline

    async def chat_json(self, **_):
        return self.outline


class PreviewReportTest(unittest.TestCase):
    def setUp(self):
        self.original = settings.document_llm_structure_enabled
        settings.document_llm_structure_enabled = True

    def tearDown(self):
        settings.document_llm_structure_enabled = self.original

    def test_the_preview_reports_the_shape_built_and_suggested(self):
        for shape, built in ((None, "one_page"), ("multi_page", "multi_page")):
            report = _build_preview(_doc(), None, shape)["document"]
            self.assertEqual(report["site_shape"], built)
            self.assertEqual(report["suggested_site_shape"], "one_page")
            self.assertIn("one page", report["site_shape_reason"])

    def test_the_model_path_builds_the_same_pages(self):
        # The model calls the numbered titles labels and leaves them out.
        answer = PasteOutline(
            single_page=True,
            pages=[PasteOutlinePage(title="Home", page_type="home", first_line=3,
                                    last_line=len(_LANDING) - 2)],
            skip_lines=[n for n, b in enumerate(_LANDING, start=1) if b.level == 1],
        )
        structured = asyncio.run(
            structure_document(_doc(), llm=_FakeLLM(answer), shape="multi_page")
        )
        source = split_into_pages(
            structured.document, page_topics=structured.page_topics, shape="multi_page"
        )
        self.assertEqual(
            [(p.url_path, p.title) for p in source.discovered_pages],
            [("/common-questions", "Common questions"), ("/pricing", "Pricing")],
        )
        report = _build_preview(_doc(), structured, "multi_page")["document"]
        self.assertEqual(report["site_shape"], "multi_page")


if __name__ == "__main__":
    unittest.main()
