"""A brief's homepage is built from the brief's own sections, whole.

Fowable's PDF is one landing page in fourteen sections. It was planned as a
fixed seven-section SaaS homepage: the 22k characters were cut into 6,000-char
chunks, every chunk was asked for every section, the merge kept one chunk's
pricing table (whichever looked most complete — once, three plans "From RM
119") and filled one six-card features block first-come from the opening
sections. The structure read also dropped card titles with their tags.

What these tests pin:
  * a price list is never cut between two chunks;
  * a skipped heading over kept copy is restored;
  * PDF heading sizes rank into six levels, so a section is not a card;
  * a document's outline becomes a section tree, plans and questions included;
  * an authored homepage follows that tree, a crawled one keeps its pattern;
  * the planner writes such a page a few sections per call, from their text.
"""

from __future__ import annotations

import unittest

from app.models.content_blocks import FeaturesBlock, SourceContent
from app.services.doc_parser import _pdf_size_to_level
from app.services.doc_structure import split_into_pages
from app.services.outline_sections import outline_sections
from app.services.page_inference import follows_source_tree, infer_page_scaffolds
from app.services.paste_structure import PasteOutline, PasteOutlinePage, _assemble
from app.services.planner import _tree_groups
from app.services.price_lists import price_list_spans, states_price_list
from app.services.source_outline import OutlineBlock, ParsedDocument
from app.services.source_router import split_raw_text


def _cards(section: str, titles: list[str], body: str) -> list[OutlineBlock]:
    blocks = [OutlineBlock(level=5, text=section)]
    for title in titles:
        blocks += [OutlineBlock(level=6, text=title), OutlineBlock(level=0, text=f"{title}: {body}")]
    return blocks


# A Fowable-shaped brief: a hero above the sections, offerings, plans whose
# prices are set as headings, a FAQ, and a short closing ask.
_BRIEF: list[OutlineBlock] = [
    OutlineBlock(level=3, text="More parents discovering your centre."),
    OutlineBlock(level=0, text="Fowable gives your kindergarten a website parents trust."),
    *_cards("Every parent enquiry, organised in one place.",
            ["One list of interested families", "Clear status and owner", "Simple numbers"],
            "kept together so your team can follow up with confidence."),
    *_cards("Make WhatsApp work for your whole team.",
            ["A shared WhatsApp inbox", "An AI agent you control"],
            "answers common questions and hands over to your team."),
    OutlineBlock(level=5, text="Simple plans that grow with your centre."),
    OutlineBlock(level=6, text=(
        "Every plan includes your website, hosting, your own web address, enquiry tracking "
        "and insights. Move up as your team and enquiries grow."
    )),
    OutlineBlock(level=6, text="Starter"),
    OutlineBlock(level=0, text="For smaller centres getting properly online."),
    OutlineBlock(level=0, text="Enquiry forms with automatic acknowledgement"),
    OutlineBlock(level=6, text="RM 119 / month, billed annually"),
    OutlineBlock(level=6, text="Growth"),
    OutlineBlock(level=0, text="For centres where WhatsApp is the main way parents get in touch."),
    OutlineBlock(level=0, text="Shared WhatsApp inbox and follow-ups"),
    OutlineBlock(level=6, text="RM 249 / month, billed annually"),
    OutlineBlock(level=6, text="Business"),
    OutlineBlock(level=0, text="For larger centres with bigger teams."),
    OutlineBlock(level=6, text="RM 449 / month, billed annually"),
    OutlineBlock(level=6, text="Which plan suits my centre?"),
    OutlineBlock(level=6, text="Starter"),
    OutlineBlock(level=0, text="Parents mostly message one person's phone."),
    *_cards("Questions owners often ask.",
            ["Do I need technical experience?", "Can I use my own domain?", "How quickly can I go live?"],
            "we set it up with you."),
    OutlineBlock(level=5, text="Your next enrolment could begin with a simple enquiry."),
    OutlineBlock(level=0, text="See your centre on Fowable"),
]


def _brief_source(blocks: list[OutlineBlock] = _BRIEF, kind: str = "pdf") -> SourceContent:
    parsed = ParsedDocument(
        source_kind=kind,  # type: ignore[arg-type]
        source_ref="brief.pdf",
        title="Fowable",
        raw_text="\n".join(b.text for b in blocks),
        headings=[b.text for b in blocks if b.level],
        outline=list(blocks),
    )
    return split_into_pages(parsed, page_topics={})


class PriceListChunkTest(unittest.TestCase):
    def _page(self) -> tuple[str, list[str]]:
        filler = [f"Paragraph {i} " + "about the centre. " * 30 for i in range(8)]
        plans = [
            "Simple plans that grow with your centre.",
            "Starter",
            *["Starter feature line " + str(i) for i in range(12)],
            "RM 119 / month, billed annually",
            "Growth",
            *["Growth feature line " + str(i) for i in range(12)],
            "RM 249 / month, billed annually",
            "Business",
            "RM 449 / month, billed annually",
        ]
        headings = ["Simple plans that grow with your centre.", "Starter", "Growth", "Business", "After"]
        return "\n".join([*filler, *plans, "After", *filler]), headings

    def test_a_price_list_is_never_cut_between_two_chunks(self):
        text, headings = self._page()
        chunks = split_raw_text(text, 6000, headings=headings)
        holding = [c for c in chunks if "RM 119" in c or "RM 249" in c or "RM 449" in c]
        self.assertEqual(len(holding), 1)
        self.assertIn("Simple plans that grow with your centre.", holding[0])
        self.assertTrue(all(len(c) <= 6000 for c in chunks))

    def test_the_span_opens_on_the_list_heading_and_closes_before_the_next(self):
        text, headings = self._page()
        lines = text.split("\n")
        [(first, last)] = price_list_spans(lines, headings)
        self.assertEqual(lines[first], "Simple plans that grow with your centre.")
        self.assertEqual(lines[last], "RM 449 / month, billed annually")

    def test_one_fee_in_passing_is_not_a_price_list(self):
        self.assertFalse(states_price_list("Registration is RM 200 one-time."))
        self.assertFalse(price_list_spans(["Fees", "RM 300 / month"], ["Fees"]))


class SkippedTitleTest(unittest.TestCase):
    def test_a_skipped_card_title_over_kept_copy_is_restored(self):
        blocks = [
            OutlineBlock(level=1, text="Home"),
            OutlineBlock(level=3, text="One list of interested families"),
            OutlineBlock(level=0, text="ALL PLANS"),
            OutlineBlock(level=0, text="Each enquiry is linked to a parent profile."),
            OutlineBlock(level=3, text="INTERNAL NOTES"),
            OutlineBlock(level=0, text="Never publish this."),
        ]
        page = PasteOutlinePage(title="Home", page_type="home", title_line=1, first_line=1, last_line=6)
        outline = PasteOutline(pages=[page], skip_lines=[2, 3, 5, 6])
        shell = ParsedDocument(source_kind="pdf", source_ref="b.pdf", title=None, raw_text="")

        text = _assemble(blocks, outline, [page], shell, given_title=None).document.raw_text

        self.assertEqual(
            text.split("\n"),
            ["One list of interested families", "Each enquiry is linked to a parent profile."],
        )


class PdfHeadingLevelTest(unittest.TestCase):
    def test_a_section_headline_outranks_a_card_title(self):
        sizes = [34, 25, 22, 20, 17, 15, 13, 9]
        self.assertLess(_pdf_size_to_level(20, sizes), _pdf_size_to_level(9, sizes))
        self.assertEqual(_pdf_size_to_level(9, sizes), 6)


class OutlineSectionsTest(unittest.TestCase):
    def setUp(self):
        self.tree = outline_sections(_BRIEF)

    def test_the_recurring_rank_opens_sections_and_the_cover_does_not(self):
        self.assertEqual(
            [s.heading for s in self.tree],
            [
                "Every parent enquiry, organised in one place.",
                "Make WhatsApp work for your whole team.",
                "Simple plans that grow with your centre.",
                "Questions owners often ask.",
                "Your next enrolment could begin with a simple enquiry.",
            ],
        )

    def test_plans_carry_their_prices_and_their_whole_copy(self):
        plans = self.tree[2]
        self.assertEqual(plans.card_kind, "plans")
        self.assertTrue(plans.subheading.startswith("Every plan includes"))
        self.assertEqual(
            [(c.title, c.meta) for c in plans.cards],
            [
                ("Starter", ["RM 119 / month, billed annually"]),
                ("Growth", ["RM 249 / month, billed annually"]),
                ("Business", ["RM 449 / month, billed annually"]),
            ],
        )
        # "Which plan suits" names Starter again: that is Starter's copy.
        self.assertIn("Parents mostly message one person's phone.", plans.cards[0].body)
        self.assertIn("Which plan suits my centre?", plans.prose)

    def test_questions_are_a_faq_and_other_cards_offerings(self):
        self.assertEqual(self.tree[3].card_kind, "questions")
        self.assertEqual(self.tree[0].card_kind, "offerings")
        self.assertEqual(
            [c.title for c in self.tree[0].cards],
            ["One list of interested families", "Clear status and owner", "Simple numbers"],
        )

    def test_an_outline_with_no_recurring_rank_states_no_sections(self):
        self.assertEqual(outline_sections([OutlineBlock(level=1, text="Only"), OutlineBlock(level=0, text="x")]), [])


class AuthoredHomepageTest(unittest.TestCase):
    def test_a_brief_homepage_follows_its_sections_and_closes_on_the_cta(self):
        source = _brief_source()
        home = infer_page_scaffolds(source, industry="saas", site_name="Fowable")[0]
        self.assertEqual(home.sections, ["hero", "services", "features", "pricing", "faq", "cta"])
        self.assertIsNotNone(follows_source_tree(home, source))

    def test_a_crawled_homepage_keeps_its_landing_pattern(self):
        source = _brief_source().model_copy(update={"source_kind": "url"})
        home = infer_page_scaffolds(source, industry="saas", site_name="Fowable")[0]
        self.assertNotEqual(home.sections, ["hero", "services", "features", "pricing", "faq", "cta"])


class TreeGroupsTest(unittest.TestCase):
    def setUp(self):
        self.source = _brief_source()
        self.home = infer_page_scaffolds(self.source, industry="saas", site_name="Fowable")[0]

    def test_each_call_gets_its_own_sections_text(self):
        from app.config import settings

        old = settings.multipass_max_chars_per_call
        settings.multipass_max_chars_per_call = 700
        try:
            groups = _tree_groups(self.home, self.source)
        finally:
            settings.multipass_max_chars_per_call = old

        self.assertGreater(len(groups), 1)
        self.assertEqual([s for sub, _ in groups for s in sub.sections if s != "hero"],
                         ["services", "features", "pricing", "faq", "cta"])
        first, last = groups[0][1].raw_text, groups[-1][1].raw_text
        self.assertTrue(first.startswith("More parents discovering your centre."))
        self.assertIn("See your centre on Fowable", last)  # the closing ask, for the CTA
        pricing = next(g for sub, g in groups if "pricing" in sub.sections)
        self.assertIn("RM 449 / month", pricing.raw_text)
        self.assertNotIn("Do I need technical experience?", pricing.raw_text)

    def test_a_scaffold_that_no_longer_follows_the_tree_is_not_grouped(self):
        edited = self.home.model_copy(update={"sections": ["hero", "features", "cta"]})
        self.assertIsNone(_tree_groups(edited, self.source))


class CardItemCapTest(unittest.TestCase):
    def test_a_grid_past_the_cap_keeps_its_first_items_instead_of_failing(self):
        items = [{"title": f"Card {i}", "description": "d"} for i in range(15)]
        block = FeaturesBlock(items=items)
        self.assertEqual([i.title for i in block.items], [f"Card {i}" for i in range(12)])


if __name__ == "__main__":
    unittest.main()
