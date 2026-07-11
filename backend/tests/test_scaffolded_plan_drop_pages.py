"""Page-level resilience for a drifting LLM.

PagePlan.salvage_page_content keeps as much of a page as possible (recover
misplaced blocks, drop only individually-bad blocks, default missing SEO) so a
technicality never blanks a page's real content. ScaffoldedSitePlan.
drop_invalid_pages is the last-resort safety net: a page that can't validate
even after salvage is dropped so the rest of the site still parses, and is
re-synthesised downstream from its scaffold."""

import unittest

from app.services.planner import ScaffoldedSitePlan

_GOOD_PAGE = {
    "page_type": "home",
    "slug": "",
    "title": "Home",
    "is_homepage": True,
    "blocks": [{"kind": "hero", "headline": "Welcome"}],
    "seo_title": "Home — Acme",
    "seo_description": "Acme homepage.",
}


def _plan(pages):
    return ScaffoldedSitePlan.model_validate(
        {"site_name": "Acme", "industry_category": "other", "pages": pages}
    )


class SalvagePageContentTest(unittest.TestCase):
    def test_page_missing_seo_keeps_its_real_content(self):
        # The regression that blanked About Us / School Life: a page whose only
        # fault was missing SEO fields was dropped whole and re-synthesised as an
        # empty shell. Now its real about content survives; SEO is defaulted.
        page = {
            "page_type": "about",
            "slug": "about",
            "title": "About Us",
            "blocks": [
                {"kind": "hero", "headline": "About GloryKids"},
                {"kind": "about", "heading": "Our Story", "body": "Established in 2004 in Kepong."},
                {"kind": "cta", "headline": "Join us"},
            ],
        }
        plan = _plan([_GOOD_PAGE, page])
        about = next(p for p in plan.pages if p.slug == "about")
        self.assertEqual([b.kind for b in about.blocks], ["hero", "about", "cta"])
        self.assertEqual(about.seo_title, "About Us")

    def test_one_malformed_block_does_not_blank_the_page(self):
        page = {
            "page_type": "about",
            "slug": "about",
            "title": "About",
            "seo_title": "t",
            "seo_description": "d",
            "blocks": [
                {"kind": "hero", "headline": "H"},
                {"kind": "about", "heading": "Real", "body": "Real content."},
                {"kind": "notakind", "foo": "bar"},  # invalid — dropped, rest kept
            ],
        }
        about = next(p for p in _plan([page]).pages if p.slug == "about")
        self.assertEqual([b.kind for b in about.blocks], ["hero", "about"])

    def test_blocks_under_sections_alias_are_recovered(self):
        page = {
            "page_type": "about",
            "slug": "about",
            "title": "About",
            "sections": [
                {"kind": "hero", "headline": "H"},
                {"kind": "about", "heading": "X", "body": "y"},
            ],
        }
        about = next(p for p in _plan([page]).pages if p.slug == "about")
        self.assertEqual([b.kind for b in about.blocks], ["hero", "about"])


class DropInvalidPagesTest(unittest.TestCase):
    def test_page_that_cannot_validate_even_after_salvage_is_dropped(self):
        # No slug (a required field with no default / heal) → unrecoverable →
        # dropped so the rest of the plan still parses.
        bad = {"page_type": "team", "title": "Our Teachers"}  # missing slug
        plan = _plan([_GOOD_PAGE, bad])
        self.assertEqual([p.slug for p in plan.pages], [""])

    def test_good_pages_all_survive(self):
        second = {**_GOOD_PAGE, "slug": "about", "title": "About", "is_homepage": False}
        plan = _plan([_GOOD_PAGE, second])
        self.assertEqual([p.slug for p in plan.pages], ["", "about"])

    def test_non_list_pages_left_for_the_caller_retry(self):
        # A whole-plan shape error is NOT silently healed — the retry path owns it.
        with self.assertRaises(Exception):
            ScaffoldedSitePlan.model_validate(
                {"site_name": "Acme", "pages": "not-a-list"}
            )


if __name__ == "__main__":
    unittest.main()
