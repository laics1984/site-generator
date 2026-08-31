"""
The LLM paste-structuring pass.

A pasted content brief is the case that broke the line-shape reader: the
author's own page list ("1. HOME" … "7. CONTACT") looks exactly like a numbered
list of bullet points, and the layout labels between them ("Hero", "Subhead",
"Services grid (four cards)") look exactly like headings. Only meaning
separates the two, so the model is asked — and it answers in LINE NUMBERS,
which is what makes the copy on the finished pages verbatim what the user typed.

What these tests pin:
  * the model's answer is mapped back onto the user's own lines, never its
    restatement of them;
  * an answer that doesn't line up with the paste is discarded, not patched;
  * every failure path falls back to the heuristic reader instead of raising;
  * pages the classifier can't recognise ("AI Agents") still open, which is the
    entire reason this pass exists.
"""

import asyncio
import unittest

from app.config import settings
from app.services.doc_structure import split_into_pages
from app.services.llm import LlmError
from app.services.paste_structure import (
    PasteOutline,
    PasteOutlinePage,
    structure_paste,
)

BRIEF = """1. HOME
Hero

Headline options

Software that survives your next 10,000 users.

Subhead

WebTree builds web platforms for companies that have outgrown off-the-shelf tools.

2. AI AGENTS
Hero

Stop losing deals to whoever replied first.

What we build

Agents that qualify inbound enquiries and follow up until there's an answer.

3. CONTACT

Tell us what's broken. Thirty minutes, no deck, no pitch."""


def _outline() -> PasteOutline:
    """What a good model returns for BRIEF: line numbers, no prose."""
    return PasteOutline(
        site_title="WebTree",
        pages=[
            PasteOutlinePage(
                title="Home", page_type="home", title_line=1, first_line=1, last_line=10
            ),
            PasteOutlinePage(
                title="AI Agents",
                page_type="services",
                title_line=12,
                first_line=12,
                last_line=19,
            ),
            PasteOutlinePage(
                title="Contact",
                page_type="contact",
                title_line=21,
                first_line=21,
                last_line=23,
            ),
        ],
        # "Hero", "Headline options", "Subhead", "What we build" — labels, not copy.
        skip_lines=[2, 4, 8, 13, 17],
    )


class _FakeLLM:
    def __init__(self, outline: PasteOutline | None = None, error: Exception | None = None):
        self.outline = outline
        self.error = error
        self.prompts: list[str] = []

    async def chat_json(self, *, user_prompt, schema, **_):
        self.prompts.append(user_prompt)
        if self.error is not None:
            raise self.error
        return self.outline


class _StructureCase(unittest.TestCase):
    def setUp(self):
        self.original = settings.paste_llm_structure_enabled
        settings.paste_llm_structure_enabled = True

    def tearDown(self):
        settings.paste_llm_structure_enabled = self.original

    def run_structure(self, llm, text=BRIEF, title=None):
        return asyncio.run(structure_paste(text, title=title, llm=llm))


class PromptTest(_StructureCase):
    def test_the_model_sees_numbered_lines(self):
        llm = _FakeLLM(_outline())
        self.run_structure(llm)
        prompt = llm.prompts[0]
        self.assertTrue(prompt.startswith("1: 1. HOME"))
        self.assertIn("\n12: 2. AI AGENTS", prompt)

    def test_oversized_paste_is_not_sent(self):
        llm = _FakeLLM(_outline())
        original = settings.paste_structure_max_chars
        settings.paste_structure_max_chars = 50
        try:
            # Truncating would silently lose every page in the tail.
            self.assertIsNone(self.run_structure(llm))
            self.assertEqual(llm.prompts, [])
        finally:
            settings.paste_structure_max_chars = original


class AssemblyTest(_StructureCase):
    def test_pages_open_on_titles_the_classifier_cannot_recognise(self):
        structured = self.run_structure(_FakeLLM(_outline()))
        source = split_into_pages(
            structured.document, page_topics=structured.page_topics
        )
        self.assertEqual(
            [p.url_path for p in source.discovered_pages], ["/ai-agents", "/contact"]
        )
        # "AI AGENTS" classifies as nothing; it opens a page because the model
        # said it names one. That is the whole point of the pass.
        self.assertEqual(structured.page_topics["AI AGENTS"], "services")

    def test_copy_is_the_users_own_lines(self):
        structured = self.run_structure(_FakeLLM(_outline()))
        text = structured.document.raw_text
        self.assertIn("Software that survives your next 10,000 users.", text)
        self.assertIn("Stop losing deals to whoever replied first.", text)

    def test_skipped_lines_are_dropped(self):
        structured = self.run_structure(_FakeLLM(_outline()))
        text = structured.document.raw_text
        for label in ("Hero", "Headline options", "Subhead", "What we build"):
            self.assertNotIn(label, text)

    def test_title_text_comes_from_the_line_not_the_model(self):
        # The model titled it "AI Agents"; the line says "2. AI AGENTS". The
        # block and the topic key must agree, or the page never opens.
        structured = self.run_structure(_FakeLLM(_outline()))
        headings = [b.text for b in structured.document.outline if b.level == 1]
        self.assertEqual(headings, ["AI AGENTS", "CONTACT"])
        self.assertEqual(set(structured.page_topics), set(headings))

    def test_the_homepage_title_is_not_emitted_as_a_heading(self):
        structured = self.run_structure(_FakeLLM(_outline()))
        self.assertNotIn("HOME", [b.text for b in structured.document.outline])

    def test_site_title_is_used_and_the_users_title_wins(self):
        self.assertEqual(self.run_structure(_FakeLLM(_outline())).document.title, "WebTree")
        self.assertEqual(
            self.run_structure(_FakeLLM(_outline()), title="Acme Ltd").document.title,
            "Acme Ltd",
        )


class FallbackTest(_StructureCase):
    def test_llm_error_falls_back(self):
        self.assertIsNone(self.run_structure(_FakeLLM(error=LlmError("no server"))))

    def test_unexpected_error_falls_back(self):
        # A read of a paste must never 500 because the model misbehaved.
        self.assertIsNone(self.run_structure(_FakeLLM(error=RuntimeError("boom"))))

    def test_disabled_flag_never_calls(self):
        settings.paste_llm_structure_enabled = False
        llm = _FakeLLM(_outline())
        self.assertIsNone(self.run_structure(llm))
        self.assertEqual(llm.prompts, [])

    def test_ranges_past_the_end_are_discarded(self):
        outline = PasteOutline(
            pages=[
                PasteOutlinePage(
                    title="Home", page_type="home", first_line=1, last_line=9999
                )
            ]
        )
        self.assertIsNone(self.run_structure(_FakeLLM(outline)))

    def test_overlapping_ranges_are_dropped_not_patched(self):
        # A wrong range moves someone's copy onto the wrong page — invisible in
        # review, and worse than the heuristic read it replaced.
        outline = PasteOutline(
            pages=[
                PasteOutlinePage(title="Home", page_type="home", first_line=1, last_line=14),
                PasteOutlinePage(
                    title="AI Agents", page_type="services", title_line=12,
                    first_line=12, last_line=19,
                ),
                PasteOutlinePage(
                    title="Contact", page_type="contact", title_line=21,
                    first_line=21, last_line=23,
                ),
            ]
        )
        structured = self.run_structure(_FakeLLM(outline))
        self.assertEqual([b.text for b in structured.document.outline if b.level == 1], ["CONTACT"])

    def test_no_pages_at_all_falls_back(self):
        self.assertIsNone(self.run_structure(_FakeLLM(PasteOutline(pages=[]))))

    def test_invented_page_type_heals(self):
        outline = PasteOutline(
            pages=[
                PasteOutlinePage(title="Home", page_type="home", first_line=1, last_line=10),
                PasteOutlinePage(
                    title="AI Agents",
                    page_type="portfolio-showcase",  # not a PageType
                    title_line=12,
                    first_line=12,
                    last_line=19,
                ),
            ]
        )
        structured = self.run_structure(_FakeLLM(outline))
        self.assertEqual(structured.page_topics["AI AGENTS"], "services")


class SplitterUnchangedWithoutTopicsTest(unittest.TestCase):
    """`page_topics` is additive: every other caller must be untouched."""

    def test_classified_split_is_identical(self):
        from app.services.paste_source import read_paste

        parsed = read_paste(
            "# Acme\n\nWe roast.\n\n## Contact\n\nCall us.\n\n## About Us\n\nSince 2011."
        ).document
        without = split_into_pages(parsed)
        explicit_none = split_into_pages(parsed, page_topics=None)
        self.assertEqual(without.model_dump(), explicit_none.model_dump())
        self.assertEqual(
            [p.url_path for p in without.discovered_pages], ["/contact", "/about-us"]
        )

    def test_one_page_per_topic_still_holds_without_topics(self):
        from app.services.source_outline import OutlineBlock, ParsedDocument

        parsed = ParsedDocument(
            source_kind="paste",
            source_ref="x",
            title="Acme",
            raw_text="",
            outline=[
                OutlineBlock(1, "Contact"),
                OutlineBlock(0, "Call us."),
                OutlineBlock(1, "Contact Us"),
                OutlineBlock(0, "Or email."),
            ],
        )
        # Both classify as "contact" — the second must not open a second page.
        self.assertEqual(len(split_into_pages(parsed).discovered_pages), 1)

    def test_named_pages_may_share_a_type(self):
        from app.services.source_outline import OutlineBlock, ParsedDocument

        parsed = ParsedDocument(
            source_kind="paste",
            source_ref="x",
            title="Acme",
            raw_text="",
            outline=[
                OutlineBlock(0, "We build things."),
                OutlineBlock(1, "AI Agents"),
                OutlineBlock(0, "Agents that qualify leads."),
                OutlineBlock(1, "Custom Software"),
                OutlineBlock(0, "Internal tools."),
            ],
        )
        source = split_into_pages(
            parsed,
            page_topics={"AI Agents": "services", "Custom Software": "services"},
        )
        self.assertEqual(
            [p.url_path for p in source.discovered_pages],
            ["/ai-agents", "/custom-software"],
        )


if __name__ == "__main__":
    unittest.main()
