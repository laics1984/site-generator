"""A source that states its plans earns a pricing section.

Only the saas landing pattern asks for `pricing`, so a document listing
"RM 159 / month · RM 249 / month · RM 399 / month" under any other industry
(the generator reads a page about kindergartens as childcare) shipped with no
pricing at all — and the fidelity net then dropped what the planner was never
asked for. Two distinct recurring prices are the evidence; one is a sentence.
"""

import unittest

from app.models.content_blocks import SourceContent
from app.services.page_inference import _detect_content_signals, infer_page_scaffolds
from app.services.price_lists import states_price_list


def _source(raw_text: str, **overrides) -> SourceContent:
    base = dict(
        source_kind="docx",
        source_ref="landing.docx",
        title="Fowable for Kindergartens",
        raw_text=raw_text,
    )
    base.update(overrides)
    return SourceContent(**base)


_PLANS = (
    "Simple plans for every size of centre\n"
    "Starter | Growth · Most popular | Business\n"
    "RM 159 / month | RM 249 / month | RM 399 / month\n"
)


class PriceTierDetectionTest(unittest.TestCase):
    def test_distinct_recurring_prices_are_tiers(self):
        self.assertTrue(states_price_list(_PLANS))

    def test_common_spellings_of_a_billing_period(self):
        for text in (
            "$29/mo or $290/yr",
            "RM650 a month, RM700 a month",
            "S$1,200 per year and S$1,500 per year",
            "€9 per user, €19 per user",
            "Fees: RM 1,200 per term or RM 1,350 per term",
        ):
            with self.subTest(text=text):
                self.assertTrue(states_price_list(text))

    def test_one_price_is_a_sentence_not_a_price_list(self):
        self.assertFalse(states_price_list("Full-day programme: RM 650 a month, meals included."))

    def test_the_same_price_twice_is_still_one(self):
        self.assertFalse(states_price_list("RM 249 / month. Yes, only RM 249 / month."))

    def test_prices_without_a_period_are_not_plans(self):
        """A menu or a one-off quote prices things, not subscriptions."""
        self.assertFalse(states_price_list("Nasi lemak RM 12, teh tarik RM 3.50, cake RM 15"))
        self.assertFalse(states_price_list("Websites from RM 3,000. Logos from RM 800."))

    def test_a_bare_number_with_a_period_is_not_a_price(self):
        self.assertFalse(states_price_list("We post 4 / month and run 2 per year."))


class PricingSignalTest(unittest.TestCase):
    def test_price_tiers_are_detected_as_pricing(self):
        self.assertIn("pricing", _detect_content_signals(_source(_PLANS)))

    def test_pricing_ranks_ahead_of_prose_guesses(self):
        """The cap is two extras; a stated price list outranks wording hints."""
        text = _PLANS + "Award-winning, trusted by 500+ centres since 2015."
        detected = _detect_content_signals(_source(text))
        self.assertEqual(detected[0], "pricing")


class HomepageEarnsPricingTest(unittest.TestCase):
    def test_single_page_document_earns_pricing_under_any_industry(self):
        scaffolds = infer_page_scaffolds(
            _source(_PLANS), industry="childcare", site_name="Fowable", single_page=True
        )
        home = scaffolds[0]
        self.assertIn("pricing", home.sections)
        self.assertEqual(home.sections[-1], "cta")
        self.assertLess(home.sections.index("pricing"), home.sections.index("cta"))

    def test_no_price_list_no_pricing(self):
        scaffolds = infer_page_scaffolds(
            _source("A warm, play-based kindergarten in Petaling Jaya."),
            industry="childcare",
            site_name="Tadika Ceria",
            single_page=True,
        )
        self.assertNotIn("pricing", scaffolds[0].sections)

    def test_an_override_still_stands_as_given(self):
        """A Facebook Page's evidence-gated list is never second-guessed."""
        sections = ["hero", "about", "contact", "cta"]
        scaffolds = infer_page_scaffolds(
            _source(_PLANS, source_kind="facebook"),
            industry="restaurant",
            site_name="Acme",
            single_page=True,
            homepage_sections_override=sections,
        )
        self.assertEqual(scaffolds[0].sections, sections)

    def test_template_fallback_home_earns_pricing_too(self):
        scaffolds = infer_page_scaffolds(
            _source(_PLANS, source_kind="url"), industry="childcare", site_name="Fowable"
        )
        home = next(s for s in scaffolds if s.is_homepage)
        self.assertIn("pricing", home.sections)

    def test_saas_home_keeps_one_pricing_section(self):
        scaffolds = infer_page_scaffolds(
            _source(_PLANS), industry="saas", site_name="Fowable", single_page=True
        )
        self.assertEqual(scaffolds[0].sections.count("pricing"), 1)


if __name__ == "__main__":
    unittest.main()
