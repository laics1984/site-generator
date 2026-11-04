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
  * the planner writes such a page a few sections per call, from their text;
  * a headline set a size up after the sections begin is a section, and a
    table priced under its plan names keeps each plan's features;
  * a price list the tree missed still earns the homepage its pricing.
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


def _docx_brief() -> bytes:
    """A brief laid out the way Fowable's DOCX is: Heading 1 section names,
    bold 16pt headlines, bold 11.5pt card titles, cards and plans in tables,
    and a body-size bold button after the plan table."""
    import io

    import docx
    from docx.shared import Pt

    document = docx.Document()

    def para(text: str, size: float = 11, bold: bool = False) -> None:
        run = document.add_paragraph().add_run(text)
        run.font.size, run.bold = Pt(size), bold

    def section(name: str, headline: str) -> None:
        document.add_heading(name, level=1)
        para("SECTION HEADLINE", 8, bold=True)
        para(headline, 16, bold=True)

    section("The problem", "Running a centre is full-time.")
    cells = document.add_table(rows=1, cols=2).rows[0].cells
    cells[0].text = "Questions come in after hours\nParents research after the kids are asleep."
    cells[1].text = "Enquiries in too many places\nForms, WhatsApp and Facebook all differ."
    section("How Fowable helps", "Five things that change from the first week")
    for title in ("1. A website parents trust", "2. Never miss a parent enquiry"):
        para(title, 11.5, bold=True)
        para(f"{title} is explained here in a sentence of body copy.")
    section("Plans", "A plan for every size of centre")
    plans = document.add_table(rows=3, cols=3)
    for column, (name, feature, price) in enumerate((
        ("Starter", "Website\nEnquiry forms", "RM 159 / month"),
        ("Growth", "Everything in Starter\nShared inbox", "RM 249 / month"),
        ("Business", "Everything in Growth\nUnlimited pages", "RM 399 / month"),
    )):
        for row, text in enumerate((name, feature, price)):
            plans.rows[row].cells[column].text = text
    para("Talk to us about the right plan", 11, bold=True)
    para("Prices in Malaysian Ringgit, billed monthly.")
    section("Frequently asked questions", "Questions principals ask us")
    for question in ("Do I need any technical skills?", "Is a Facebook Page enough?"):
        para(question, 11.5, bold=True)
        para("Yes, and we set it up with you.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class DocxBriefTest(unittest.TestCase):
    def setUp(self):
        from app.services.doc_parser import parse_docx

        self.parsed = parse_docx(_docx_brief(), "brief.docx")
        self.tree = outline_sections(self.parsed.outline)

    def test_tables_are_read_where_they_sit(self):
        texts = [b.text for b in self.parsed.outline]
        self.assertLess(texts.index("Starter"), texts.index("Talk to us about the right plan"))
        self.assertLess(texts.index("Plans"), texts.index("Starter"))

    def test_bold_type_above_body_size_is_a_heading_and_a_button_is_not(self):
        level = {b.text: b.level for b in self.parsed.outline}
        self.assertGreater(level["1. A website parents trust"], level["Five things that change from the first week"])
        self.assertGreater(level["Five things that change from the first week"], level["How Fowable helps"])
        self.assertEqual(level["Talk to us about the right plan"], 0)
        self.assertEqual(level["SECTION HEADLINE"], 0)

    def test_the_brief_reads_as_cards_plans_and_questions(self):
        by_heading = {s.heading: s for s in self.tree}
        plans = by_heading["Plans"]
        self.assertEqual(plans.card_kind, "plans")
        self.assertEqual(plans.subheading, "A plan for every size of centre")
        self.assertEqual(
            [(c.title, c.meta, c.body.split("\n")) for c in plans.cards],
            [
                ("Starter", ["RM 159 / month"], ["Website", "Enquiry forms"]),
                ("Growth", ["RM 249 / month"], ["Everything in Starter", "Shared inbox"]),
                ("Business", ["RM 399 / month"], ["Everything in Growth", "Unlimited pages"]),
            ],
        )
        # The plan table's own ask is the section's copy, not Business's feature.
        self.assertIn("Talk to us about the right plan", plans.prose)
        self.assertEqual(by_heading["The problem"].card_kind, "offerings")
        self.assertEqual(
            [c.title for c in by_heading["The problem"].cards],
            ["Questions come in after hours", "Enquiries in too many places"],
        )
        self.assertEqual(
            [c.title for c in by_heading["How Fowable helps"].cards],
            ["1. A website parents trust", "2. Never miss a parent enquiry"],
        )
        self.assertEqual(by_heading["Frequently asked questions"].card_kind, "questions")

    def test_its_homepage_asks_for_pricing(self):
        source = split_into_pages(self.parsed, page_topics={})
        home = infer_page_scaffolds(source, industry="saas", site_name="Fowable")[0]
        self.assertIn("pricing", home.sections)
        self.assertIn("faq", home.sections)


class StructuredDocxTest(unittest.TestCase):
    def test_the_structure_read_keeps_a_headline_above_its_cards(self):
        # In-page headings move one level down; capped at six, a DOCX's
        # headlines (5) met its card titles (6).
        from app.services.doc_parser import parse_docx

        parsed = parse_docx(_docx_brief(), "brief.docx")
        page = PasteOutlinePage(
            title="Home", page_type="home", title_line=None,
            first_line=1, last_line=len(parsed.outline),
        )
        assembled = _assemble(parsed.outline, PasteOutline(pages=[page]), [page], parsed, given_title=None)
        tree = {s.heading: s for s in outline_sections(assembled.document.outline)}
        self.assertEqual(tree["Plans"].subheading, "A plan for every size of centre")
        # The assembler strips list numbering from every line it keeps.
        self.assertEqual(
            [c.title for c in tree["How Fowable helps"].cards],
            ["A website parents trust", "Never miss a parent enquiry"],
        )

    def test_a_brief_that_opens_on_a_section_shows_it_once_as_the_hero(self):
        source = _brief_source(_BRIEF[2:])  # no cover headline above the sections
        home = infer_page_scaffolds(source, industry="saas", site_name="Fowable")[0]
        self.assertEqual(home.sections, ["hero", "services", "pricing", "faq", "cta"])
        from app.config import settings

        old = settings.multipass_max_chars_per_call
        settings.multipass_max_chars_per_call = 400
        try:
            groups = _tree_groups(home, source)
        finally:
            settings.multipass_max_chars_per_call = old
        self.assertTrue(groups[0][1].raw_text.startswith("Every parent enquiry, organised in one place."))
        self.assertEqual(groups[0][1].section_candidates[0].heading, "Make WhatsApp work for your whole team.")


class ProseOnlyBriefTest(unittest.TestCase):
    def test_a_brief_with_no_card_group_keeps_the_landing_pattern_and_its_prices(self):
        blocks = []
        for name in ("Welcome", "Our approach", "Plans", "Visit us"):
            blocks += [OutlineBlock(level=1, text=name), OutlineBlock(level=0, text=f"{name} copy.")]
        blocks.append(OutlineBlock(level=0, text="Starter RM 159 / month. Growth RM 249 / month."))
        home = infer_page_scaffolds(_brief_source(blocks), industry="saas", site_name="Fowable")[0]
        self.assertIn("pricing", home.sections)
        self.assertNotEqual(home.sections, ["hero", "about", "about", "about", "about", "cta"])


class LargerHeadlineTest(unittest.TestCase):
    """The simplified Fowable brief set its pricing headline at 16pt among
    15pt ones. Read as a cover headline, it took its plans out of the tree."""

    def _plans(self) -> list[OutlineBlock]:
        return [
            OutlineBlock(level=4, text="Simple plans for every size of centre"),
            OutlineBlock(level=6, text="Starter"),
            OutlineBlock(level=0, text="Website"),
            OutlineBlock(level=0, text="RM 159 / month"),
            OutlineBlock(level=6, text="Growth"),
            OutlineBlock(level=0, text="Shared inbox"),
            OutlineBlock(level=0, text="RM 249 / month"),
        ]

    def test_a_larger_headline_after_the_first_section_opens_one(self):
        tree = outline_sections([
            OutlineBlock(level=3, text="Parents ask at 10 pm."),
            *_cards("One", ["A", "B"], "copy."),
            *_cards("Two", ["C", "D"], "copy."),
            *_cards("Three", ["E", "F"], "copy."),
            *self._plans(),
        ])
        self.assertEqual([s.heading for s in tree], ["One", "Two", "Three", "Simple plans for every size of centre"])
        self.assertEqual([(c.title, c.meta) for c in tree[-1].cards],
                         [("Starter", ["RM 159 / month"]), ("Growth", ["RM 249 / month"])])

    def test_a_part_title_with_nothing_under_it_is_not_a_section(self):
        tree = outline_sections([
            *_cards("One", ["A", "B"], "copy."),
            OutlineBlock(level=4, text="Part two"),
            *_cards("Two", ["C", "D"], "copy."),
            *_cards("Three", ["E", "F"], "copy."),
        ])
        self.assertEqual([s.heading for s in tree], ["One", "Two", "Three"])


def _docx_short_brief() -> bytes:
    """Laid out the way the simplified Fowable brief is: no heading styles,
    15pt bold section headlines and a 16pt one for pricing, card tables, and a
    plan table priced in the row under the plan names."""
    import io

    import docx
    from docx.shared import Pt

    document = docx.Document()

    def para(text: str, size: float = 11, bold: bool = False) -> None:
        run = document.add_paragraph().add_run(text)
        run.font.size, run.bold = Pt(size), bold

    def cards(*cells: str) -> None:
        for cell, text in zip(document.add_table(rows=1, cols=len(cells)).rows[0].cells, cells):
            cell.text = text

    para("01 · HERO", 8, bold=True)
    para("Parents ask at 10 pm.", 20, bold=True)
    para("A website, one shared WhatsApp inbox and an assistant.")
    para("Great centres lose families before the first visit.", 15, bold=True)
    cards("One phone, many hands\nNobody knows who replied.", "Messages after hours\nParents ask at night.")
    para("One place for every parent enquiry.", 15, bold=True)
    cards("A website parents trust\nWe build it for you.", "Never miss an enquiry\nEvery form is saved.")
    para("Questions principals ask us", 15, bold=True)
    for question in ("Do I need technical skills?", "Can I switch plans later?"):
        para(question, 11.5, bold=True)
        para("Yes, and we set it up with you.")
    para("Simple plans for every size of centre", 16, bold=True)
    para("Start where you are. Move up as you grow.")
    plans = document.add_table(rows=4, cols=3)
    for column, plan in enumerate((
        ("Starter", "RM 159 / month", "For centres getting online", "Website on your own domain\nUp to 5 pages"),
        ("Growth", "RM 249 / month", "For centres on WhatsApp", "Everything in Starter, plus:\nShared inbox"),
        ("Business", "RM 399 / month", "For multi-branch centres", "Everything in Growth, plus:\nUnlimited pages"),
    )):
        for row, text in enumerate(plan):
            plans.rows[row].cells[column].text = text
    para("Prices in Malaysian Ringgit, billed monthly.", 8)
    para("See your centre's demo website", 11, bold=True)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


class PricedFirstTableTest(unittest.TestCase):
    """A plan's copy ends at its price; a table priced in the row under the
    names lost every feature to the section's prose."""

    def setUp(self):
        from app.services.doc_parser import parse_docx

        self.parsed = parse_docx(_docx_short_brief(), "short.docx")

    def test_each_plan_keeps_its_own_copy(self):
        plans = {s.heading: s for s in outline_sections(self.parsed.outline)}["Simple plans for every size of centre"]
        self.assertEqual(plans.card_kind, "plans")
        self.assertEqual(
            [(c.title, c.meta, c.body.split("\n")) for c in plans.cards],
            [
                ("Starter", ["RM 159 / month"],
                 ["For centres getting online", "Website on your own domain", "Up to 5 pages"]),
                ("Growth", ["RM 249 / month"],
                 ["For centres on WhatsApp", "Everything in Starter, plus:", "Shared inbox"]),
                ("Business", ["RM 399 / month"],
                 ["For multi-branch centres", "Everything in Growth, plus:", "Unlimited pages"]),
            ],
        )
        self.assertIn("Prices in Malaysian Ringgit, billed monthly.", plans.prose)

    def test_its_homepage_closes_on_its_plans(self):
        source = split_into_pages(self.parsed, page_topics={})
        home = infer_page_scaffolds(source, industry="childcare", site_name="Fowable")[0]
        self.assertEqual(home.sections, ["hero", "services", "features", "faq", "pricing", "cta"])


class PricesTheTreeMissedTest(unittest.TestCase):
    """The tree is the homepage's order, not a licence to lose its prices."""

    def setUp(self):
        self.source = _brief_source([
            OutlineBlock(level=3, text="More parents discovering your centre."),
            *_cards("Every parent enquiry, organised in one place.", ["One list", "Clear status"], "kept together."),
            OutlineBlock(level=5, text="What it costs"),
            OutlineBlock(level=0, text="Starter is RM 119 / month for a single centre."),
            OutlineBlock(level=0, text="Growth is RM 249 / month for a team on WhatsApp."),
            *_cards("Make WhatsApp work for your whole team.", ["A shared inbox", "An AI agent"], "answers."),
            *_cards("Questions owners often ask.", ["Do I need skills?", "Can I use my domain?"], "we help."),
        ])
        self.home = infer_page_scaffolds(self.source, industry="childcare", site_name="Fowable")[0]

    def test_a_price_list_in_prose_still_earns_pricing(self):
        self.assertEqual(self.home.sections, ["hero", "services", "about", "features", "faq", "pricing", "cta"])
        self.assertIsNotNone(follows_source_tree(self.home, self.source))

    def test_the_call_that_writes_pricing_reads_the_price_list(self):
        from app.config import settings

        old = settings.multipass_max_chars_per_call
        settings.multipass_max_chars_per_call = 150
        try:
            groups = _tree_groups(self.home, self.source)
        finally:
            settings.multipass_max_chars_per_call = old

        costs = next(i for i, (_, g) in enumerate(groups) if "What it costs" in g.headings)
        last_sub, last = groups[-1]
        self.assertLess(costs, len(groups) - 1)
        self.assertIn("pricing", last_sub.sections)
        self.assertIn("Growth is RM 249 / month for a team on WhatsApp.", last.raw_text)


class GroupKindOrderTest(unittest.TestCase):
    def test_card_grids_take_the_kind_their_position_asked_for(self):
        # The model wrote "The problem" as features and "The solution" as
        # services; asked the other way round, the merge swapped them.
        from app.models.content_blocks import HeroBlock, PagePlan, ServicesBlock
        from app.models.industry import PageScaffold
        from app.services.planner import _kinds_as_asked

        item = [{"title": "Card", "description": "Copy"}]
        page = PagePlan(
            page_type="home", slug="", title="Home", description="", is_homepage=True,
            seo_title="t", seo_description="d",
            blocks=[
                HeroBlock(headline="H"),
                FeaturesBlock(heading="The problem", items=item),
                ServicesBlock(heading="The solution", items=item),
            ],
        )
        sub = PageScaffold(page_type="home", slug="", title="Home", is_homepage=True,
                           sections=["hero", "services", "features"])

        fixed = _kinds_as_asked(page, sub)

        self.assertEqual(
            [(b.kind, getattr(b, "heading", None)) for b in fixed.blocks],
            [("hero", None), ("services", "The problem"), ("features", "The solution")],
        )


class CardItemCapTest(unittest.TestCase):
    def test_a_grid_past_the_cap_keeps_its_first_items_instead_of_failing(self):
        items = [{"title": f"Card {i}", "description": "d"} for i in range(15)]
        block = FeaturesBlock(items=items)
        self.assertEqual([i.title for i in block.items], [f"Card {i}" for i in range(12)])


if __name__ == "__main__":
    unittest.main()
