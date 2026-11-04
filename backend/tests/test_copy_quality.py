"""The website copy standard: one guidance block in every copy-writing prompt,
the market's English spelling stated to the model, and an advisory audit that
reads the same cliché lists the prompt quotes."""

from __future__ import annotations

import json
import unittest

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.models.content_blocks import MultilingualPlan, SourceContent
from app.models.industry import PageScaffold
from app.services import planner, prompts
from app.services.locale import _MARKETS, _ENGLISH_SPELLING, MarketContext, english_spelling, source_market
from app.services.ux_audit import audit_copy


def _site(*elements: BuilderElement) -> GeneratedSite:
    section = BuilderElement(name="Section", type="container", content=list(elements))
    page = GeneratedPage(slug="", title="Home", is_homepage=True,
                         body_schema=BodySchema(elements=[section]), seo=PageSeo())
    return GeneratedSite(site_name="x", pages=[page])


def _text(text: str, tag: str | None = None, name: str = "Body") -> BuilderElement:
    return BuilderElement(name=name, type="text", htmlTag=tag,
                          content=BuilderElementContent(innerText=text))


class OneGuidanceTest(unittest.TestCase):
    """Both planning prompts carry the same block, so the scaffolded and the
    free-form path can't hold copy to two different standards."""

    def test_both_copy_prompts_carry_the_guidance(self):
        self.assertIn(prompts.WEBSITE_COPY_GUIDANCE, planner._scaffold_system_prompt())
        self.assertIn(prompts.WEBSITE_COPY_GUIDANCE, prompts.LEGACY_SYSTEM_PROMPT)

    def test_the_prompt_quotes_clichés_from_the_audited_list(self):
        quoted = [p for p in prompts.AI_STOCK_PHRASES if f'"{p}"' in prompts.WEBSITE_COPY_GUIDANCE]
        self.assertGreaterEqual(len(quoted), 5)

    def test_the_lists_do_not_overlap(self):
        # A phrase in both would be reported under whichever rule ran first.
        self.assertFalse(set(prompts.AI_STOCK_PHRASES) & set(prompts.GENERIC_HEADLINES))

    def test_the_guidance_names_the_spelling_key_the_payload_sends(self):
        self.assertIn("`english_spelling`", prompts.WEBSITE_COPY_GUIDANCE)


class EnglishSpellingTest(unittest.TestCase):
    def test_every_named_country_is_a_real_market(self):
        # A misspelt key would be a silent off switch for that country.
        self.assertLessEqual(set(_ENGLISH_SPELLING), set(_MARKETS))

    def test_malaysia_spells_programmes_and_the_us_programs(self):
        def market(country):
            return MarketContext(country=country, region="r", demonym="d", region_demonym="d")

        self.assertIn("programmes", english_spelling(market("Malaysia")))
        self.assertIn("enquiries", english_spelling(market("Malaysia")))
        self.assertIn("programs", english_spelling(market("United States")))

    def test_no_country_no_convention(self):
        region_only = MarketContext(country=None, region="Southeast Asia",
                                    demonym="Southeast Asian", region_demonym="Southeast Asian")
        self.assertIsNone(english_spelling(region_only))
        self.assertIsNone(english_spelling(None))

    def test_the_source_market_reads_its_urls(self):
        source = SourceContent(source_kind="url", source_ref="https://tadika.com.my/",
                               title="Tadika", raw_text="Welcome.")
        self.assertEqual(source_market(source).country, "Malaysia")


class SpellingPayloadTest(unittest.TestCase):
    @staticmethod
    def _payload(**source_fields) -> dict:
        source = SourceContent(source_kind="url", source_ref="https://tadika.com.my/",
                               title="Tadika", raw_text="Call +60 3-1234 5678.", **source_fields)
        scaffold = PageScaffold(slug="", title="Home", page_type="home", is_homepage=True,
                                sections=["hero", "cta"])
        return json.loads(planner._build_scaffolded_user_prompt(source, None, [scaffold], None, {"": source}))

    def test_an_english_malaysian_site_is_told_british_spelling(self):
        self.assertIn("programmes", self._payload()["english_spelling"])
        english = self._payload(multilingual=MultilingualPlan(mode="single", primary="en"))
        self.assertIn("programmes", english["english_spelling"])

    def test_a_site_written_in_another_language_is_not(self):
        chinese = self._payload(multilingual=MultilingualPlan(mode="single", primary="zh-Hans"))
        self.assertNotIn("english_spelling", chinese)
        self.assertIn("write_in", chinese)

    def test_a_market_with_no_convention_sends_nothing(self):
        source = SourceContent(source_kind="url", source_ref="x", title="Acme", raw_text="Hello.")
        self.assertNotIn("english_spelling", planner._language_payload(source))


class StockPhraseAuditTest(unittest.TestCase):
    def test_a_cliché_in_body_copy_is_reported(self):
        found = audit_copy(_site(_text("Look no further for weekend tuition in Ipoh.")))
        self.assertEqual([f.rule for f in found], ["stock-phrase"])

    def test_a_cliché_heading_is_reported_once(self):
        found = audit_copy(_site(_text("Your Trusted Partner in Care", tag="h2")))
        self.assertEqual([f.rule for f in found], ["stock-phrase"])

    def test_a_split_headline_is_read_whole_and_its_lines_not_again(self):
        group = BuilderElement(name="Heading group", type="container", htmlTag="h1", content=[
            _text("Elevate your", name="Heading"),
            _text("weekends", name="Heading accent"),
        ])
        self.assertEqual([f.rule for f in audit_copy(_site(group))], ["stock-phrase"])

    def test_a_curly_apostrophe_is_the_same_cliché(self):
        found = audit_copy(_site(_text("We’re passionate about bread.")))
        self.assertEqual([f.rule for f in found], ["stock-phrase"])

    def test_specific_copy_is_not_reported(self):
        self.assertEqual(audit_copy(_site(
            _text("Sourdough baked daily on Jalan Bukit Bintang", tag="h1"),
            _text("We open at 7am and sell out of croissants by ten."),
        )), [])


if __name__ == "__main__":
    unittest.main()
