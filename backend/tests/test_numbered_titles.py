"""An author's numbered list: siblings by their numbers, published without them.

Measured on uniquekindies.com (Wix). Its "How we teach" run is styled
1. p, 2) h2, 3) p, 4) h2, 5) p and its enrichment list h4, h5, h4. Read by tag
rank, the paragraph items vanished into their predecessor's prose and the h5
nested under its own predecessor; an image wall then skipped whichever items
had no photos, and the published pages opened on "4)" and "2)".
"""

from __future__ import annotations

import unittest

from bs4 import BeautifulSoup

from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.services import scraper
from app.services.profile_text import has_sentence_punctuation
from app.services.scaffold_enforcement import looks_like_team_member_name
from app.services.schema_builder import strip_title_list_markers
from app.services.section_extraction import extract_section_candidates
from app.services.source_outline import list_marker_number, strip_list_marker


def _extract(html: str):
    soup = BeautifulSoup(html, "lxml")
    return extract_section_candidates(
        soup, "https://example.com/page", person_name=scraper._looks_like_person_name
    )


class ListMarkerTest(unittest.TestCase):
    def test_markers_the_source_writes_are_read(self):
        self.assertEqual(list_marker_number("1) Waterplay | 戏水"), 1)
        self.assertEqual(list_marker_number("3)Thematic Approach"), 3)
        self.assertEqual(list_marker_number("2. Our approach"), 2)
        self.assertEqual(strip_list_marker("4) Character & Behaviour"), "Character & Behaviour")
        self.assertEqual(strip_list_marker("3)Thematic Approach"), "Thematic Approach")

    def test_numbers_that_are_not_markers_stay(self):
        for text in ("3.5 stars from parents", "2006 founding year", "18 months to 3 years",
                     "4 1/2 hours a day", "2.0 release"):
            self.assertIsNone(list_marker_number(text), text)
            self.assertEqual(strip_list_marker(text), text)

    def test_a_bare_number_is_its_own_title(self):
        self.assertEqual(strip_list_marker("5)"), "5)")
        self.assertIsNone(list_marker_number("5)"))


class NumberedRunTest(unittest.TestCase):
    def test_items_styled_as_paragraphs_still_open_their_sections(self):
        sections = _extract(
            """
            <html><body>
              <h2>How we teach</h2>
              <p>Guided by our values, we bring learning to life every day.</p>
              <p>1. Our Unique Teaching Approach</p>
              <p>Children learn through play, movement, songs and observation.</p>
              <h2>2) Tri-Language Story Reading</h2>
              <p>Stories in Chinese, Malay and English build vocabulary gradually.</p>
              <p>3) Thematic Learning Approach</p>
              <p>Themes connect subjects so that children see how ideas relate.</p>
              <h2>4) Character Development</h2>
              <p>Good habits begin early, through daily guidance and role models.</p>
            </body></html>
            """
        )
        headings = [s.heading for s in sections]
        self.assertEqual(
            headings,
            ["How we teach", "Our Unique Teaching Approach", "Tri-Language Story Reading",
             "Thematic Learning Approach", "Character Development"],
        )
        # Each item keeps its own words, not its neighbour's.
        by_heading = {s.heading: s.prose for s in sections}
        self.assertIn("Themes connect", by_heading["Thematic Learning Approach"])
        self.assertNotIn("Themes connect", by_heading["Tri-Language Story Reading"])
        self.assertIn("play, movement", by_heading["Our Unique Teaching Approach"])

    def test_one_list_in_mixed_heading_ranks_is_one_level(self):
        sections = _extract(
            """
            <html><body>
              <h2>Enrichment Courses</h2>
              <h4>1) Cambridge English</h4>
              <p>Cambridge English builds reading, writing and speaking skills.</p>
              <h5>2) UCMAS Mental Arithmetics</h5>
              <p>Mental arithmetic with the abacus trains focus and memory.</p>
              <h4>3) Globalart</h4>
              <p>Creative drawing classes develop imagination and fine motor skills.</p>
            </body></html>
            """
        )
        # Read by rank, the h5 nested under item 1 and the rack held 1) and 3).
        section = next(s for s in sections if s.heading == "Enrichment Courses")
        self.assertEqual(
            [c.title for c in section.cards],
            ["Cambridge English", "UCMAS Mental Arithmetics", "Globalart"],
        )

    def test_numbered_paragraphs_alone_are_body_copy(self):
        # No heading in the run: a numbered list in prose, not an outline.
        sections = _extract(
            """
            <html><body>
              <h2>How it works</h2>
              <p>1) You call us</p>
              <p>2) We visit</p>
              <p>3) We quote</p>
            </body></html>
            """
        )
        self.assertEqual([s.heading for s in sections], ["How it works"])

    def test_steps_are_still_recognised_from_their_numbers(self):
        sections = _extract(
            """
            <html><body>
              <h2>How enrolment works</h2>
              <div class="steps">
                <div class="step"><h3>1. Book a visit</h3><p>Choose a time that suits your family.</p></div>
                <div class="step"><h3>2. Meet the teachers</h3><p>See the classrooms and ask questions.</p></div>
                <div class="step"><h3>3. Enrol</h3><p>Complete the form and pick a start date.</p></div>
              </div>
            </body></html>
            """
        )
        section = next(s for s in sections if s.heading == "How enrolment works")
        self.assertEqual(section.card_kind, "steps")
        self.assertEqual([c.title for c in section.cards],
                         ["Book a visit", "Meet the teachers", "Enrol"])


class TitleBackstopTest(unittest.TestCase):
    def test_a_copied_number_is_dropped_from_a_section_title(self):
        title = BuilderElement(
            id="t", name="Heading", type="text",
            content=BuilderElementContent(innerText="4) Character & Behaviour"),
        )
        section = BuilderElement(id="s", name="Section", type="container", content=[title])
        strip_title_list_markers([section])
        self.assertEqual(title.content.innerText, "Character & Behaviour")


class SiteTitleIsNotASubjectTest(unittest.TestCase):
    _PAGE = """
    <html><head><meta property="og:site_name" content="Unique Kindies"></head><body>
      <{wrap}><h1>UNIQUE KINDIES</h1></{wrap}>
      <main>
        <h2>Enrichment Courses</h2>
        <img src="/stock-banner.jpg" alt="" width="600" height="600" />
        <p>Our enrichment courses run after school hours.</p>
      </main>
    </body></html>
    """

    def test_the_header_site_title_is_not_a_person(self):
        soup = BeautifulSoup(self._PAGE.format(wrap="header"), "lxml")
        self.assertEqual(scraper._extract_profile_candidates(soup, "https://example.my/x"), [])

    def test_the_site_name_is_not_a_person_outside_the_header_either(self):
        soup = BeautifulSoup(self._PAGE.format(wrap="div"), "lxml")
        self.assertEqual(scraper._extract_profile_candidates(soup, "https://example.my/x"), [])


class SentenceIsNotANameTest(unittest.TestCase):
    def test_a_bilingual_sentence_is_not_a_name(self):
        text = "Unique Kindies 创立于 2006 年，致力于以“爱、智慧、欢乐”陪伴孩子成长。"
        self.assertTrue(has_sentence_punctuation(text))
        self.assertFalse(scraper._looks_like_person_name(text))
        self.assertFalse(looks_like_team_member_name(text))

    def test_names_with_latin_punctuation_survive(self):
        for name in ("Dr. Aisha Rahman", "Jane Doe, MD"):
            self.assertTrue(scraper._looks_like_person_name(name), name)


if __name__ == "__main__":
    unittest.main()


class StaleSourceBrandProfileTest(unittest.TestCase):
    """A source crawled before the scraper fixes still carries the brand as a
    'person' and two sentence-'people' that normalise to the brand. Generation
    must not turn that into a profile page."""

    def test_sentences_filed_as_names_do_not_roster_the_brand(self):
        from app.models.content_blocks import ProfileCandidate, SourceContent
        from app.routers.generate import _profile_page_block, _rostered_names

        def candidate(name: str, url: str) -> ProfileCandidate:
            return ProfileCandidate(name=name, photo_url=f"https://x.my/{url}.jpg",
                                    source_url="https://x.my", confidence=0.8)

        about = SourceContent(
            source_kind="url", source_ref="https://x.my/about", url_path="/about",
            raw_text="About", profile_candidates=[
                candidate("Unique Kindies 创立于 2006 年，致力于陪伴孩子成长。", "a"),
                candidate("在 Unique Kindies，我们始终以孩子的成长为核心。", "b"),
            ],
        )
        programmes = SourceContent(
            source_kind="url", source_ref="https://x.my/projects", url_path="/projects",
            title="Programmes | Unique Kindies", headings=["UNIQUE KINDIES"],
            raw_text="UNIQUE KINDIES Programmes",
            profile_candidates=[candidate("UNIQUE KINDIES", "stock")],
        )
        site = SourceContent(source_kind="url", source_ref="https://x.my", raw_text="",
                             discovered_pages=[about, programmes])

        rostered = _rostered_names(site)
        self.assertEqual(rostered, set())
        self.assertIsNone(_profile_page_block(programmes, None, rostered))
