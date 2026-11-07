"""A document's titles decide which parts are pages and which are sections.

The same kindergarten brief — three headline sections and a "Get in touch" —
came out as one homepage when the structure model answered "one page", and as
a homepage plus a Contact page when it listed pages or was unavailable. The
picker then hid the Contact page, so the site sometimes had no contact form at
all. `outline_pages` reads the grouping from the outline the document states,
so every reader groups one document one way.

What these tests pin:
  * every title's role — cover, home, page, section, subtitle;
  * the evidence order: named pages, labelled sections, nested sections,
    page names, else one page;
  * the model's answer types a page and never decides one: the same document
    is the same set of pages whatever the model says, or without it.
"""

from __future__ import annotations

import asyncio
import unittest

from app.config import settings
from app.services.doc_structure import split_into_pages
from app.services.outline_pages import plan_pages
from app.services.paste_structure import PasteOutline, PasteOutlinePage, structure_document
from app.services.source_outline import OutlineBlock, ParsedDocument

B = OutlineBlock

# A one-page brief: a cover, then sections titled as headlines.
_ONE_PAGER = [
    B(1, "Sunny Steps Kindergarten"),                     # 0 cover
    B(0, "A warm, play-based kindergarten in Petaling Jaya."),
    B(2, "Why parents choose us"),                        # 2 section
    B(3, "Small classes"),                                # 3 card
    B(0, "Never more than 12 children per teacher."),
    B(3, "Bilingual"),                                    # 5 card
    B(0, "English and Mandarin every day."),
    B(2, "Our programmes"),                               # 7 section
    B(3, "Toddlers"),
    B(0, "Ages 2-3, half days."),
    B(3, "Nursery"),
    B(0, "Ages 4-5, full days."),
    B(2, "Get in touch"),                                 # 12 section
    B(0, "Call 03-1234 5678 or email hello@sunnysteps.my."),
]

# The same business written as a site map: the author names the home page.
_SITE_MAP = [
    B(1, "HOME"),                                         # 0 home
    B(0, "Welcome to Sunny Steps, a play-based kindergarten."),
    B(1, "PROGRAMMES"),                                   # 2 page (no topic word)
    B(2, "Toddlers"),
    B(0, "Ages 2-3, half days."),
    B(1, "ABOUT US"),                                     # 5 page
    B(2, "Our story"),
    B(0, "Founded in 2006 by two teachers."),
    B(1, "CONTACT"),                                      # 8 page
    B(0, "Call 03-1234 5678."),
]


def _plan(blocks, **kwargs):
    return plan_pages(blocks, **kwargs)


def _roles(plan, blocks):
    return {blocks[i].text: role for i, role in plan.roles.items()}


class RolesTest(unittest.TestCase):
    def test_a_brief_of_headlines_is_one_page_in_sections(self):
        plan = _plan(_ONE_PAGER)
        self.assertTrue(plan.one_page)
        self.assertEqual(plan.pages, ())
        roles = _roles(plan, _ONE_PAGER)
        self.assertEqual(roles["Sunny Steps Kindergarten"], "cover")
        for title in ("Why parents choose us", "Our programmes", "Get in touch"):
            self.assertEqual(roles[title], "section")
        for title in ("Small classes", "Bilingual", "Toddlers", "Nursery"):
            self.assertEqual(roles[title], "subtitle")

    def test_a_site_map_is_pages_and_its_home_title_is_the_homepage(self):
        plan = _plan(_SITE_MAP)
        self.assertFalse(plan.one_page)
        self.assertIn("names its pages", plan.reason)
        # A title with no topic word is still a page in a document that names
        # its pages; it is typed by the fallback every unknown page gets.
        self.assertEqual(plan.pages, ((2, "services"), (5, "about"), (8, "contact")))
        roles = _roles(plan, _SITE_MAP)
        self.assertEqual(roles["HOME"], "home")
        self.assertEqual(roles["Toddlers"], "section")
        self.assertEqual(roles["Our story"], "section")

    def test_stacked_titles_are_never_parts(self):
        blocks = [
            B(1, "Sunny Steps"),           # the opening title: the cover
            B(1, "Kindergarten & Care"),   # its second line, set as large
            B(0, "Since 2006."),
            B(3, "OUR PROGRAMMES"),        # an eyebrow stacked on its headline
            B(2, "Classes for every age"),
            B(0, "Toddlers to six-year-olds."),
            B(2, "Visit us"),
            B(0, "Open days every Saturday."),
        ]
        plan = _plan(blocks)
        roles = _roles(plan, blocks)
        self.assertEqual(roles["Sunny Steps"], "cover")
        self.assertEqual(roles["Kindergarten & Care"], "cover")
        self.assertEqual(roles["OUR PROGRAMMES"], "subtitle")
        self.assertEqual(roles["Classes for every age"], "section")
        self.assertTrue(plan.one_page)

    def test_a_lone_headline_above_the_parts_is_the_covers(self):
        blocks = [
            B(1, "Sunny Steps"),
            B(0, "A warm kindergarten."),
            B(1, "Enrol now for 2027"),     # set larger than the parts, owns a line
            B(0, "Limited places."),
            B(2, "About Us"),
            B(0, "Founded in 2006."),
            B(2, "Our Services"),
            B(0, "Full and half days."),
            B(2, "Contact"),
            B(0, "Call us."),
        ]
        plan = _plan(blocks)
        self.assertEqual(_roles(plan, blocks)["Enrol now for 2027"], "cover")
        self.assertEqual([blocks[i].text for i, _ in plan.pages], ["About Us", "Our Services", "Contact"])

    def test_no_titles_is_no_plan(self):
        self.assertIsNone(_plan([B(0, "One blob."), B(0, "More.")]))


class EvidenceTest(unittest.TestCase):
    def test_labelled_sections_are_one_page_even_when_titled_as_pages(self):
        blocks = [
            B(0, "SECTION 01 — HERO"),
            B(1, "Sunny Steps"),
            B(0, "A warm kindergarten."),
            B(0, "SECTION 02 — ABOUT"),
            B(2, "About us"),
            B(0, "Founded in 2006."),
            B(0, "SECTION 03 — CONTACT"),
            B(2, "Contact"),
            B(0, "Call us."),
        ]
        # The labels count even when the copy reader left them out as notes.
        plan = _plan(blocks, copy={1, 2, 4, 5, 7, 8})
        self.assertTrue(plan.one_page)
        self.assertIn("labels the sections", plan.reason)

    def test_parts_holding_sections_of_their_own_are_pages(self):
        blocks = [
            B(1, "Little Oaks"),
            B(0, "Learning outdoors."),
            B(2, "Our philosophy"),
            B(3, "Play"), B(4, "Free play"), B(0, "x"), B(4, "Guided play"), B(0, "x"),
            B(3, "Inquiry"), B(4, "Questions"), B(0, "x"), B(4, "Projects"), B(0, "x"),
            B(2, "Our campus"),
            B(3, "Indoors"), B(4, "Studio"), B(0, "x"), B(4, "Library"), B(0, "x"),
            B(3, "Outdoors"), B(4, "Garden"), B(0, "x"), B(4, "Sandpit"), B(0, "x"),
        ]
        plan = _plan(blocks)
        self.assertFalse(plan.one_page)
        self.assertIn("hold sections of their own", plan.reason)
        self.assertEqual([blocks[i].text for i, _ in plan.pages], ["Our philosophy", "Our campus"])

    def test_one_part_titled_as_a_page_is_that_page(self):
        # What the paste box is for: an intro, then the Contact page's copy.
        blocks = [B(1, "Acme"), B(0, "We roast."), B(2, "Contact"), B(0, "Call 012.")]
        plan = _plan(blocks)
        self.assertEqual(plan.pages, ((2, "contact"),))

    def test_page_names_must_be_most_of_the_parts(self):
        blocks = [
            B(1, "Acme"), B(0, "We roast."),
            B(2, "About Us"), B(0, "Since 2011."),
            B(2, "Why our beans taste better"), B(0, "Slow roasting."),
        ]
        self.assertTrue(_plan(blocks).one_page)

    def test_headlines_before_the_first_page_stay_on_the_homepage(self):
        blocks = [
            B(1, "Acme"), B(0, "We roast."),
            B(2, "Why choose us"), B(0, "Slow roasting."),
            B(2, "About Us"), B(0, "Since 2011."),
            B(2, "Services"), B(0, "Wholesale and retail."),
            B(2, "Contact"), B(0, "Call us."),
        ]
        plan = _plan(blocks)
        self.assertEqual(_roles(plan, blocks)["Why choose us"], "section")
        self.assertEqual([blocks[i].text for i, _ in plan.pages], ["About Us", "Services", "Contact"])

    def test_one_page_per_topic(self):
        blocks = [
            B(1, "Services"), B(0, "First."),
            B(1, "Solutions"), B(0, "Second."),
            B(1, "Contact"), B(0, "Reach us."),
        ]
        plan = _plan(blocks)
        self.assertEqual([blocks[i].text for i, _ in plan.pages], ["Services", "Contact"])
        self.assertEqual(_roles(plan, blocks)["Solutions"], "section")

    def test_the_models_answer_types_a_page_and_never_makes_one(self):
        # Naming a headline a page does not make the brief pages...
        self.assertTrue(_plan(_ONE_PAGER, named={12: "contact", 7: "services"}).one_page)
        # ...but it types a page the outline opens.
        plan = _plan(_SITE_MAP, named={2: "pricing"})
        self.assertEqual(plan.pages[0], (2, "pricing"))


class _FakeLLM:
    def __init__(self, outline):
        self.outline = outline

    async def chat_json(self, **_):
        return self.outline


def _doc(blocks) -> ParsedDocument:
    return ParsedDocument(
        source_kind="docx",
        source_ref="brief.docx",
        title="Sunny Steps",
        raw_text="\n".join(b.text for b in blocks),
        headings=[b.text for b in blocks if b.level],
        outline=list(blocks),
    )


class SameDocumentSamePagesTest(unittest.TestCase):
    """Whatever the model answers — or without it — one document, one site."""

    def setUp(self):
        self.original = settings.document_llm_structure_enabled
        settings.document_llm_structure_enabled = True

    def tearDown(self):
        settings.document_llm_structure_enabled = self.original

    def _paths(self, blocks, answer):
        if answer is None:
            source = split_into_pages(_doc(blocks))
        else:
            structured = asyncio.run(structure_document(_doc(blocks), llm=_FakeLLM(answer)))
            source = split_into_pages(structured.document, page_topics=structured.page_topics)
        return [page.url_path for page in source.discovered_pages]

    def _answers(self, blocks, parts):
        """No model, then three answers a model gives. ``parts`` are the
        1-based lines of the titles a model would call pages."""
        last = len(blocks)
        ends = [*(start - 1 for start in parts[1:]), last]
        one_page = PasteOutline(
            single_page=True,
            pages=[PasteOutlinePage(title="Home", page_type="home", first_line=1, last_line=last)],
        )
        # Every part a page, each starting on its title.
        pages = PasteOutline(pages=[
            PasteOutlinePage(title=blocks[start - 1].text, page_type="services",
                             title_line=start, first_line=start, last_line=end)
            for start, end in zip(parts, ends)
        ])
        # Measured on the local model: each page's copy starts on the line
        # AFTER its title, so every title sits outside every page.
        titles_outside = PasteOutline(pages=[
            PasteOutlinePage(title=blocks[start - 1].text, page_type="services",
                             title_line=start, first_line=start + 1, last_line=end)
            for start, end in zip(parts, ends)
        ])
        return [None, one_page, pages, titles_outside]

    def test_a_one_page_brief_is_one_page_on_every_read(self):
        for answer in self._answers(_ONE_PAGER, [1, 3, 8, 13]):
            self.assertEqual(self._paths(_ONE_PAGER, answer), [])

    def test_a_site_map_is_the_same_pages_on_every_read(self):
        for answer in self._answers(_SITE_MAP, [1, 3, 6, 9]):
            self.assertEqual(
                self._paths(_SITE_MAP, answer), ["/programmes", "/about-us", "/contact"]
            )

    def test_the_home_label_is_not_copy(self):
        source = split_into_pages(_doc(_SITE_MAP))
        self.assertNotIn("HOME", source.raw_text.split("\n"))
        self.assertIn("Welcome to Sunny Steps, a play-based kindergarten.", source.raw_text)


if __name__ == "__main__":
    unittest.main()
