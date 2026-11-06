"""An uploaded brief is read for what is copy, not only for what is large.

Fowable's 29-page PDF is ONE landing page in fourteen sections, each followed
by internal notes ("Never publish"), then eleven pages of internal appendices.
Read by font size alone, the cover headline was the only page title: the whole
document, notes and verification log included, became one sub-page and the
homepage got the cover's 115 characters. And a PDF stores visual lines, so that
headline arrived as three H1s and every wrapped sentence in fragments.

What these tests pin:
  * the PDF reader emits paragraphs, not visual lines, and collapses
    letter-spaced type;
  * the structure read (the paste reader's, shared) turns a brief's sections
    into its homepage, drops what was written for the team, re-anchors the
    images that stay and drops the ones in excluded parts;
  * the document keeps what a reader measured, and every failure keeps the
    headings' split.
"""

from __future__ import annotations

import asyncio
import unittest

import fitz

from app.config import settings
from app.routers.document import _build_preview
from app.services.doc_parser import _collapse_tracking, parse_pdf
from app.services.doc_structure import split_into_pages
from app.services.llm import LlmError
from app.services.paste_structure import (
    PasteOutline,
    PasteOutlinePage,
    structure_document,
)
from app.services.source_outline import DocImage, OutlineBlock, ParsedDocument


class ParagraphTest(unittest.TestCase):
    def test_a_wrapped_headline_is_one_heading(self):
        doc = fitz.open()
        page = doc.new_page()
        page.insert_textbox(
            fitz.Rect(50, 50, 250, 300),
            "Helping your kindergarten grow, one parent connection at a time.",
            fontsize=28,
        )
        body = (
            "Fowable helps your centre get discovered, connect with more parents "
            "and manage enquiries more easily with a professional website. "
        ) * 6
        page.insert_textbox(fitz.Rect(50, 320, 550, 800), body, fontsize=10)

        outline = parse_pdf(doc.tobytes(), "brief.pdf").outline

        headings = [b.text for b in outline if b.level > 0]
        self.assertEqual(
            headings, ["Helping your kindergarten grow, one parent connection at a time."]
        )
        # The wrapped body is one paragraph too, rejoined between words.
        self.assertEqual(len([b for b in outline if b.level == 0]), 1)

    def test_letter_spaced_type_is_collapsed(self):
        self.assertEqual(_collapse_tracking("W E B S I T E  C O P Y"), "WEBSITE COPY")
        self.assertEqual(
            _collapse_tracking("F O R  K I N D E R GA R T E N S , C H I L D CA R E"),
            "FOR KINDERGARTENS, CHILDCARE",
        )
        # Short words are not letter-spacing.
        self.assertEqual(_collapse_tracking("A to Z  of it"), "A to Z  of it")


# A brief in miniature. Block numbers (1-based) are what the model answers in.
_BLOCKS = [
    (0, "WEBSITE CONTENT BLUEPRINT"),                       # 1  cover label
    (1, "Helping your kindergarten grow."),                 # 2  cover headline
    (2, "How to use this document"),                        # 3  internal
    (0, "Internal notes are kept apart. Never publish."),  # 4  internal
    (0, "WEBSITE SECTION 01 / 03 · HERO"),                  # 5  label
    (0, "INTERNAL · PURPOSE Tell an owner what we are."),   # 6  note
    (1, "More parents discovering your centre."),           # 7  copy
    (0, "We build your website and follow up every family."),  # 8  copy
    (0, "WEBSITE SECTION 02 / 03 · ABOUT"),                 # 9  label
    (1, "About us"),                                        # 10 copy (a heading, not a page)
    (0, "Founded by early-years teachers."),                # 11 copy
    (0, "EDITORIAL NOTE Source: brochure p.3."),            # 12 note
    (0, "WEBSITE SECTION 03 / 03 · PLANS"),                 # 13 label
    (2, "Simple plans"),                                    # 14 copy
    (0, "Starter RM 119 / month. Growth RM 249 / month."),  # 15 copy
    (1, "APPENDIX B · INTERNAL — NOT FOR PUBLICATION"),     # 16 appendix
    (0, "Verification log: 18 answers checked."),           # 17 appendix
    (0, "Plan comparison for the sales team."),             # 18 appendix
]


def _brief() -> ParsedDocument:
    outline = [OutlineBlock(level=level, text=text) for level, text in _BLOCKS]
    return ParsedDocument(
        source_kind="pdf",
        source_ref="brief.pdf",
        title="Fowable · Kindergarten Website Content",
        raw_text="\n".join(block.text for block in outline),
        headings=[b.text for b in outline if b.level],
        outline=outline,
        images=[
            DocImage(b"cover", "image/png", 600, 130, anchor=1),     # after the cover label
            DocImage(b"hero", "image/jpeg", 1200, 800, anchor=8),    # after hero copy
            DocImage(b"appendix", "image/png", 900, 600, anchor=17),  # inside the appendix
        ],
        reference_colors=["#0e442f"],
    )


def _answer() -> PasteOutline:
    """What a good model says: one landing page, the team's lines left out."""
    return PasteOutline(
        site_title="Fowable",
        pages=[PasteOutlinePage(title="Home", page_type="home", first_line=5, last_line=15)],
        skip_lines=[5, 6, 9, 12, 13],
    )


class _FakeLLM:
    def __init__(self, outline=None, error=None):
        self.outline, self.error, self.prompts = outline, error, []

    async def chat_json(self, *, user_prompt, schema, **_):
        self.prompts.append(user_prompt)
        if self.error is not None:
            raise self.error
        return self.outline


class StructureDocumentTest(unittest.TestCase):
    def setUp(self):
        self.original = settings.document_llm_structure_enabled
        settings.document_llm_structure_enabled = True

    def tearDown(self):
        settings.document_llm_structure_enabled = self.original

    def read(self, llm, parsed=None):
        return asyncio.run(structure_document(parsed or _brief(), llm=llm))

    def test_the_model_sees_heading_levels_as_evidence(self):
        llm = _FakeLLM(_answer())
        self.read(llm)
        self.assertIn("\n2: # Helping your kindergarten grow.", llm.prompts[0])
        self.assertIn("\n14: ## Simple plans", llm.prompts[0])
        self.assertIn("\n8: We build your website", llm.prompts[0])

    def test_a_brief_of_sections_builds_the_homepage_and_nothing_for_the_team(self):
        structured = self.read(_FakeLLM(_answer()))
        source = split_into_pages(structured.document, page_topics=structured.page_topics)

        self.assertEqual(source.discovered_pages, [])
        copy = source.raw_text
        for line in ("More parents discovering your centre.", "Founded by early-years teachers.",
                     "Starter RM 119 / month. Growth RM 249 / month."):
            self.assertIn(line, copy)
        for line in ("Never publish", "INTERNAL · PURPOSE", "EDITORIAL NOTE",
                     "WEBSITE SECTION", "Verification log", "sales team"):
            self.assertNotIn(line, copy)

    def test_an_in_page_heading_never_opens_a_page(self):
        # "About us" is the document's own H1 inside the landing page. The
        # brief labels its sections ("WEBSITE SECTION 02 / 03"), so its outline
        # reads as one page and "About us" is a section of it — keeping the
        # rank the document gave it, since the outline, not a re-levelling,
        # decides which titles are pages.
        structured = self.read(_FakeLLM(_answer()))
        about = next(b for b in structured.document.outline if b.text == "About us")
        self.assertEqual(about.level, 1)
        self.assertEqual(structured.page_topics, {})
        source = split_into_pages(structured.document, page_topics=structured.page_topics)
        self.assertEqual(source.discovered_pages, [])

    def test_images_follow_their_copy_and_leave_with_excluded_parts(self):
        structured = self.read(_FakeLLM(_answer()))
        kept = {image.data: image.anchor for image in structured.document.images}
        # The appendix photo went with the appendix; the cover's sat before
        # every page, so it went too. The hero photo still follows its copy.
        self.assertEqual(set(kept), {b"hero"})
        outline = structured.document.outline
        self.assertEqual(outline[kept[b"hero"] - 1].text,
                         "We build your website and follow up every family.")

    def test_the_site_is_named_by_the_model_and_keeps_what_was_measured(self):
        document = self.read(_FakeLLM(_answer())).document
        self.assertEqual(document.title, "Fowable")
        self.assertEqual(document.source_kind, "pdf")
        self.assertEqual(document.source_ref, "brief.pdf")
        self.assertEqual(document.reference_colors, ["#0e442f"])

    def test_every_failure_keeps_the_headings_split(self):
        self.assertIsNone(self.read(_FakeLLM(error=LlmError("no server"))))
        self.assertIsNone(self.read(_FakeLLM(error=RuntimeError("boom"))))
        self.assertIsNone(self.read(_FakeLLM(PasteOutline(pages=[]))))
        settings.document_llm_structure_enabled = False
        llm = _FakeLLM(_answer())
        self.assertIsNone(self.read(llm))
        self.assertEqual(llm.prompts, [])
        # The preview still builds from the document's own headings.
        payload = _build_preview(_brief(), None)
        self.assertEqual(payload["document"]["structured_by"], "headings")

    def test_a_long_brief_is_one_page_decided_once(self):
        # Too long for one call: the MAP sees the outline and decides one page
        # and the internal parts; the windows only mark notes, and never see
        # what the map left out.
        class Mapped(_FakeLLM):
            async def chat_json(self, *, user_prompt, schema, **_):
                self.prompts.append(user_prompt)
                if user_prompt.startswith("[Outline"):
                    return PasteOutline(
                        site_title="Fowable",
                        # The copy ends at line 15; the appendix after it is out.
                        pages=[PasteOutlinePage(title="Home", page_type="home",
                                                title_line=5, first_line=5, last_line=15)],
                    )
                shown = {int(r.split(":")[0]) for r in user_prompt.splitlines()[1:]}
                return PasteOutline(skip_lines=[n for n in (5, 6, 9, 12, 13) if n in shown])

        llm = Mapped()
        original = settings.paste_structure_max_chars
        settings.paste_structure_max_chars = 500
        try:
            structured = self.read(llm)
        finally:
            settings.paste_structure_max_chars = original

        whole = self.read(_FakeLLM(_answer()))
        self.assertEqual(structured.document.outline, whole.document.outline)
        self.assertEqual(structured.page_topics, {})
        for part in llm.prompts[1:]:
            self.assertNotIn("Verification log", part)
            self.assertNotIn("How to use this document", part)

    def test_sections_of_one_page_fold_into_the_homepage(self):
        # Asked outright, the model says the brief is ONE page — while still
        # naming its sections, as the local model did for Fowable's fourteen.
        answer = PasteOutline(
            site_title="Fowable",
            single_page=True,
            pages=[
                PasteOutlinePage(title="Hero", page_type="home", title_line=5, first_line=5, last_line=8),
                PasteOutlinePage(title="About", page_type="about", title_line=9, first_line=9, last_line=12),
                PasteOutlinePage(title="Plans", page_type="pricing", title_line=13, first_line=13, last_line=15),
            ],
            skip_lines=[5, 6, 9, 12, 13],
        )
        structured = self.read(_FakeLLM(answer))
        source = split_into_pages(structured.document, page_topics=structured.page_topics)

        self.assertEqual(source.discovered_pages, [])
        for line in ("More parents discovering your centre.", "Founded by early-years teachers.",
                     "Starter RM 119 / month. Growth RM 249 / month."):
            self.assertIn(line, source.raw_text)

    def test_the_map_cannot_skip_lines_it_cannot_see(self):
        # The map sees headings and short lines only; a line or run it marks
        # inside a page is a guess, and Fowable's map took sections' copy with
        # its guesses. Where the copy ENDS is the map's to say.
        class Mapped(_FakeLLM):
            async def chat_json(self, *, user_prompt, schema, **_):
                self.prompts.append(user_prompt)
                if user_prompt.startswith("[Outline"):
                    return PasteOutline(
                        pages=[PasteOutlinePage(title="Home", page_type="home",
                                                title_line=5, first_line=5, last_line=15)],
                        skip_lines=[10, 14],  # "About us", "Simple plans": copy
                        skip_ranges=[[10, 11]],  # a guess inside the copy
                    )
                return PasteOutline()

        original = settings.paste_structure_max_chars
        settings.paste_structure_max_chars = 500
        try:
            structured = self.read(Mapped())
        finally:
            settings.paste_structure_max_chars = original
        texts = [block.text for block in structured.document.outline]
        self.assertIn("About us", texts)
        self.assertIn("Simple plans", texts)
        self.assertIn("Founded by early-years teachers.", texts)
        self.assertNotIn("Verification log: 18 answers checked.", texts)

    def test_the_preview_uses_the_structured_read(self):
        structured = self.read(_FakeLLM(_answer()))
        payload = _build_preview(_brief(), structured)
        self.assertEqual(payload["document"]["structured_by"], "llm")
        self.assertNotIn("Verification log", payload["source_content"]["raw_text"])


if __name__ == "__main__":
    unittest.main()
