"""A one-page site carries its own contact form.

A document or paste read as one page gets no Contact page — the picker offers
one unticked — and no homepage recipe asked for a `contact` section, so the
site shipped with no form, and every "get in touch" action was dropped for
want of a destination. The brief's own "Get in touch" section was read as the
closing ask and folded into the CTA band.

What these tests pin:
  * a one-page homepage ends `contact`, `cta`, after whatever it earned;
  * the form is written from the brief's own closing contact lines;
  * a Contact page the user ticks takes the form back, and the homepage still
    follows its source's sections;
  * a multi-page homepage and a Facebook Page's evidence-gated list are
    unchanged.
"""

from __future__ import annotations

import unittest

from app.services.doc_structure import split_into_pages
from app.services.landing_patterns import _LANDING_PATTERNS
from app.services.page_inference import (
    _MAX_EXTRA_SECTIONS,
    _MAX_PAGE_SECTIONS,
    defer_home_contact,
    follows_source_tree,
    infer_page_scaffolds,
)
from app.services.planner import _tree_groups
from app.services.source_outline import OutlineBlock, ParsedDocument

B = OutlineBlock


def _brief(*tail) -> list[OutlineBlock]:
    return [
        B(1, "Sunny Steps Kindergarten"),
        B(0, "A warm, play-based kindergarten in Petaling Jaya."),
        B(2, "Why parents choose us"),
        B(3, "Small classes"), B(0, "Never more than 12 children per teacher."),
        B(3, "Bilingual"), B(0, "English and Mandarin every day."),
        B(2, "Our programmes"),
        B(3, "Toddlers"), B(0, "Ages 2-3, half days."),
        B(3, "Nursery"), B(0, "Ages 4-5, full days."),
        B(2, "Our day"),
        B(0, "Arrival, circle time, outdoor play, lunch and a rest."),
        *tail,
    ]


_GET_IN_TOUCH = (B(2, "Get in touch"), B(0, "Call 03-1234 5678 or email hello@sunnysteps.my."))


def _source(blocks):
    return split_into_pages(ParsedDocument(
        source_kind="docx", source_ref="brief.docx", title="Sunny Steps",
        raw_text="\n".join(b.text for b in blocks), outline=list(blocks),
    ))


def _scaffolds(source, **kwargs):
    single = not source.discovered_pages
    return infer_page_scaffolds(
        source, industry="childcare", site_name="Sunny Steps", single_page=single, **kwargs
    )


def _home(scaffolds):
    return next(s for s in scaffolds if s.is_homepage)


class OnePageContactTest(unittest.TestCase):
    def test_a_one_page_brief_gets_its_form_before_the_closing_cta(self):
        source = _source(_brief(*_GET_IN_TOUCH))
        home = _home(_scaffolds(source))
        self.assertEqual(home.sections[-2:], ["contact", "cta"])
        self.assertEqual(home.sections.count("contact"), 1)

    def test_the_form_is_written_from_the_briefs_own_contact_lines(self):
        source = _source(_brief(*_GET_IN_TOUCH))
        home = _home(_scaffolds(source))
        # Still planned section by section from the brief's own tree...
        self.assertIsNotNone(follows_source_tree(home, source))
        groups = _tree_groups(home, source)
        last, text = groups[-1][0], groups[-1][1].raw_text
        # ...and the last call, which writes the form, holds the brief's
        # closing contact lines rather than an about block of them.
        self.assertIn("contact", last.sections)
        self.assertIn("Call 03-1234 5678 or email hello@sunnysteps.my.", text)
        self.assertNotIn("Get in touch", [s.heading for s in source.section_candidates[:-1]])

    def test_a_brief_with_no_contact_copy_still_gets_a_form(self):
        home = _home(_scaffolds(_source(_brief())))
        self.assertEqual(home.sections[-2:], ["contact", "cta"])

    def test_a_brief_whose_sections_state_no_shape_gets_a_form_too(self):
        # No card group: the landing pattern builds the homepage instead.
        blocks = [
            B(1, "Sunny Steps"), B(0, "A warm kindergarten."),
            B(2, "Why families stay with us"), B(0, "Founded in 2006."),
            B(2, "Learning through play"), B(0, "Play first."),
            B(2, "A day with us"), B(0, "Circle time and play."),
        ]
        source = _source(blocks)
        self.assertEqual(source.discovered_pages, [])
        home = _home(_scaffolds(source))
        self.assertEqual(home.sections[-2:], ["contact", "cta"])

    def test_what_the_page_earned_comes_before_the_form(self):
        blocks = _brief(
            B(2, "Fees"),
            B(0, "Half day RM 450 / month. Full day RM 780 / month."),
            *_GET_IN_TOUCH,
        )
        home = _home(_scaffolds(_source(blocks)))
        self.assertEqual(home.sections[-3:], ["pricing", "contact", "cta"])

    def test_a_ticked_contact_page_takes_the_form_back(self):
        source = _source(_brief(*_GET_IN_TOUCH))
        scaffolds = _scaffolds(source)
        contact_page = next(
            page for page in infer_page_scaffolds(source, industry="childcare")
            if page.page_type == "contact"
        )
        picked = defer_home_contact([*scaffolds, contact_page])
        home = _home(picked)
        self.assertNotIn("contact", home.sections)
        # The form sat outside the brief's own sections, so the homepage
        # still follows them section by section.
        self.assertIsNotNone(follows_source_tree(home, source))

    def test_a_multi_page_homepage_leaves_the_form_to_its_contact_page(self):
        blocks = [
            B(1, "HOME"), B(0, "Welcome to Sunny Steps."),
            B(1, "ABOUT US"), B(0, "Founded in 2006."),
            B(1, "CONTACT"), B(0, "Call 03-1234 5678."),
        ]
        scaffolds = _scaffolds(_source(blocks))
        self.assertNotIn("contact", _home(scaffolds).sections)
        self.assertIn("contact", [s.page_type for s in scaffolds])

    def test_an_evidence_gated_list_stands_as_given(self):
        source = _source(_brief(*_GET_IN_TOUCH))
        override = ["hero", "about", "gallery", "cta"]
        home = _home(_scaffolds(source, homepage_sections_override=override))
        self.assertEqual(home.sections, override)

    def test_the_form_never_pushes_a_homepage_past_the_page_ceiling(self):
        longest = max(len(pattern.sections) for pattern in _LANDING_PATTERNS)
        self.assertLessEqual(longest + _MAX_EXTRA_SECTIONS + 1, _MAX_PAGE_SECTIONS)


if __name__ == "__main__":
    unittest.main()
