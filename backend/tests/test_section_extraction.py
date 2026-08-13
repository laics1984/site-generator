"""Section-tree recovery and group-level card classification.

The bug these guard: Glorykids' /school-life reached the planner as a flat
heading list, so six sections arrived as one and their cards were pooled — and
separately, six facility cards were read as a six-person team roster, which
tipped page_inference into retyping the whole page `team`.
"""

import unittest

from bs4 import BeautifulSoup

from app.services import scraper
from app.services.section_extraction import extract_section_candidates


def _extract(html: str, url: str = "https://example.com/page"):
    soup = BeautifulSoup(html, "lxml")
    return extract_section_candidates(
        soup, url, person_name=scraper._looks_like_person_name
    )


# Two h2 sections, each with its own h3 card group — the shape /school-life has.
_TWO_SECTION_PAGE = """
<html><body>
  <h1>Our Curriculum</h1>
  <p>An intro paragraph about the school and how it teaches.</p>

  <h2>School Life</h2>
  <h3>18 months to 3 years old</h3>
  <p>Learning domains for our youngest children include English and Mathematics.</p>
  <h3>4 years old</h3>
  <p>Four year olds learn two languages alongside art, craft and movement.</p>
  <h3>5 years old</h3>
  <p>Five year olds learn three languages and use all four of our centres.</p>

  <h2>Centres</h2>
  <h3>Innovation Centre</h3>
  <p>Here students develop language, mathematics and critical thinking skills.</p>
  <h3>Science Centre</h3>
  <p>Here students experiment and learn the why and how of the world of science.</p>
  <h3>ICT Centre</h3>
  <p>Here students develop the technology literacy needed in a modern world.</p>

  <h2>Educational Field Trip</h2>
  <p>We organise numerous educational field trips aligned to curriculum themes.</p>
</body></html>
"""


class SectionSegmentationTest(unittest.TestCase):
    def test_heading_rank_separates_sections_and_keeps_cards_in_place(self):
        sections = _extract(_TWO_SECTION_PAGE)

        by_heading = {s.heading: s for s in sections}
        self.assertIn("School Life", by_heading)
        self.assertIn("Centres", by_heading)
        self.assertIn("Educational Field Trip", by_heading)

        self.assertEqual(
            [c.title for c in by_heading["School Life"].cards],
            ["18 months to 3 years old", "4 years old", "5 years old"],
        )
        self.assertEqual(
            [c.title for c in by_heading["Centres"].cards],
            ["Innovation Centre", "Science Centre", "ICT Centre"],
        )

    def test_a_card_title_is_not_also_emitted_as_its_own_section(self):
        """The h3s belong to the h2 above them. Emitting them as sections too
        would hand the planner the same content twice under two headings — the
        duplication that lets a card drift into the wrong section."""
        headings = [s.heading for s in _extract(_TWO_SECTION_PAGE)]

        for card_title in ("Innovation Centre", "Science Centre", "4 years old"):
            self.assertNotIn(card_title, headings)

    def test_prose_only_section_gets_no_cards(self):
        sections = {s.heading: s for s in _extract(_TWO_SECTION_PAGE)}

        field_trip = sections["Educational Field Trip"]
        self.assertEqual(field_trip.cards, [])
        self.assertEqual(field_trip.card_kind, "prose")
        self.assertIn("field trips", field_trip.prose)

    def test_container_heading_keeps_its_prose_and_does_not_eat_its_sections(self):
        """The h1 spans the whole page, but its h2 children carry cards of their
        own — so it is a container, not a three-card rack."""
        sections = {s.heading: s for s in _extract(_TWO_SECTION_PAGE)}

        self.assertEqual(sections["Our Curriculum"].cards, [])
        self.assertIn("intro paragraph", sections["Our Curriculum"].prose)

    def test_chrome_is_not_a_section_and_does_not_leak_into_the_last_one(self):
        """Without a chrome stop the final section runs to end-of-document and
        adopts the footer's address and phone number as its cards."""
        html = """
        <html><body>
          <h2>Educational Field Trip</h2>
          <p>We organise numerous educational field trips through the year.</p>
          <footer>
            <h3>Contact</h3>
            <div><span>03-2702 0242</span></div>
            <div><span>017-887-2575</span></div>
          </footer>
        </body></html>
        """
        sections = _extract(html)

        self.assertEqual([s.heading for s in sections], ["Educational Field Trip"])
        self.assertEqual(sections[0].cards, [])
        self.assertNotIn("2702", sections[0].prose)


class CardClassificationTest(unittest.TestCase):
    def test_facility_cards_are_offerings_not_people(self):
        """Titles that pass a two-capitalised-words name test, with photos, but
        no portrait geometry and no contact links. The group is what says these
        are rooms."""
        sections = {s.heading: s for s in _extract(_TWO_SECTION_PAGE)}

        self.assertEqual(sections["Centres"].card_kind, "offerings")
        self.assertEqual(sections["School Life"].card_kind, "offerings")

    def test_a_real_roster_still_classifies_as_people(self):
        """The guard must not cost genuine team pages their portraits: square
        photos, person-shaped names, and a mailto: link per card."""
        html = """
        <html><body>
          <h2>Our Team</h2>
          <h3>Aisha Rahman</h3>
          <img src="/a.jpg" width="400" height="400" alt="Aisha Rahman">
          <p>Aisha leads our early-years programme and mentors new teachers.</p>
          <a href="mailto:aisha@example.com">Email</a>
          <h3>Marcus Ong</h3>
          <img src="/m.jpg" width="400" height="400" alt="Marcus Ong">
          <p>Marcus coordinates enrichment and oversees our science curriculum.</p>
          <a href="mailto:marcus@example.com">Email</a>
          <h3>Siti binti Rahman</h3>
          <img src="/s.jpg" width="400" height="400" alt="Siti binti Rahman">
          <p>Siti has taught language and literacy here for eleven years.</p>
          <a href="mailto:siti@example.com">Email</a>
        </body></html>
        """
        sections = _extract(html)

        self.assertEqual(len(sections), 1)
        self.assertEqual(sections[0].card_kind, "people")
        self.assertEqual(
            [c.title for c in sections[0].cards],
            ["Aisha Rahman", "Marcus Ong", "Siti binti Rahman"],
        )

    def test_one_name_shaped_title_among_many_is_not_a_roster(self):
        """A roster agrees with itself. Per-card classification is what turned a
        facilities grid into a team; the group vote is the fix."""
        html = """
        <html><body>
          <h2>What We Offer</h2>
          <h3>Marcus Ong</h3>
          <img src="/1.jpg" width="400" height="400" alt="one">
          <p>A named scholarship offered to one student in each intake year.</p>
          <h3>Innovation Centre</h3>
          <img src="/2.jpg" width="400" height="400" alt="two">
          <p>Here students develop language, mathematics and critical thinking.</p>
          <h3>Science Centre</h3>
          <img src="/3.jpg" width="400" height="400" alt="three">
          <p>Here students experiment and learn the why and how of science.</p>
          <h3>Domestic-Science Centre</h3>
          <img src="/4.jpg" width="400" height="400" alt="four">
          <p>Here students learn to prepare, cook and decorate their own food.</p>
        </body></html>
        """
        sections = _extract(html)

        self.assertEqual(sections[0].card_kind, "offerings")

    def test_numbered_steps_need_an_ordinal_marker_not_just_a_leading_digit(self):
        """'18 months to 3 years old' is an age group, not step 18."""
        html = """
        <html><body>
          <h2>How It Works</h2>
          <h3>1. Book a tour</h3>
          <p>Come and see the classrooms, meet the teachers and ask questions.</p>
          <h3>2. Submit the form</h3>
          <p>Complete the enrolment form and return it with the documents listed.</p>
          <h3>3. Start school</h3>
          <p>Your child joins the class and we begin the settling-in programme.</p>
        </body></html>
        """
        steps = _extract(html)[0]
        ages = {s.heading: s for s in _extract(_TWO_SECTION_PAGE)}["School Life"]

        self.assertEqual(steps.card_kind, "steps")
        self.assertEqual(ages.card_kind, "offerings")

    def test_label_value_rows_do_not_become_cards(self):
        """A schedule matches the repeated-sibling signature as well as a card
        rack does. Promoting it costs the section ABOVE its own cards, because a
        parent only claims child headings when they are all leaves."""
        html = """
        <html><body>
          <h2>School Life</h2>
          <h3>18 months to 3 years old</h3>
          <div>
            <div><span>Full Programme :</span><span>8:30 am - 3:00 pm</span></div>
            <div><span>Extended Programme :</span><span>8:30 am - 5:30 pm</span></div>
          </div>
          <h3>4 years old</h3>
          <p>Four year olds learn two languages alongside art, craft and movement.</p>
          <h3>5 years old</h3>
          <p>Five year olds learn three languages and use all four of our centres.</p>
        </body></html>
        """
        sections = {s.heading: s for s in _extract(html)}

        self.assertIn("School Life", sections)
        self.assertEqual(
            [c.title for c in sections["School Life"].cards],
            ["18 months to 3 years old", "4 years old", "5 years old"],
        )

    def test_pages_without_headings_yield_no_tree(self):
        """No crash, no invention — the flat raw_text path still applies."""
        html = "<html><body><p>Just a paragraph, no headings at all here.</p></body></html>"

        self.assertEqual(_extract(html), [])


class ProfileConfirmationTest(unittest.TestCase):
    def test_section_classifier_overrules_portrait_anchored_profiles(self):
        """The portrait walk proposes one card at a time; the group confirms."""
        from app.models.content_blocks import ProfileCandidate

        sections = _extract(_TWO_SECTION_PAGE)
        proposed = [
            ProfileCandidate(name="Innovation Centre", confidence=0.8),
            ProfileCandidate(name="Science Centre", confidence=0.8),
            ProfileCandidate(name="Aisha Rahman", confidence=0.9),
        ]

        kept = scraper._confirm_profiles_against_sections(proposed, sections)

        # The two that are card titles in an `offerings` group are overruled;
        # the one matching no card is untouched (a loose portrait was never a
        # group member).
        self.assertEqual([p.name for p in kept], ["Aisha Rahman"])

    def test_people_groups_are_left_alone(self):
        from app.models.content_blocks import ProfileCandidate, SectionCandidate, SourceCard

        sections = [
            SectionCandidate(
                heading="Our Team",
                level=2,
                card_kind="people",
                cards=[SourceCard(title="Aisha Rahman"), SourceCard(title="Marcus Ong")],
            )
        ]
        proposed = [
            ProfileCandidate(name="Aisha Rahman", confidence=0.9),
            ProfileCandidate(name="Marcus Ong", confidence=0.9),
        ]

        kept = scraper._confirm_profiles_against_sections(proposed, sections)

        self.assertEqual(len(kept), 2)


if __name__ == "__main__":
    unittest.main()
