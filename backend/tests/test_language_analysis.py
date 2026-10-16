"""Language analysis (docs/multilingual-plan.md §1, P1).

Scenarios A-F run over the committed synthetic fixtures in
tests/fixtures/languages/ — the same files `scripts/measure_language_analysis.py
--fixtures` replays. Everything else builds its source inline.
"""

from __future__ import annotations

import json
import sys
import unittest
from unittest import mock
from pathlib import Path

from app.models.content_blocks import NavLink, ProfileCandidate, SourceContent
from app.services import language_analysis as la
from app.services.language_analysis import analyze
from app.services.locale import (
    LOCALES,
    canonical_locale,
    is_rtl,
    locale_label,
    locale_segment,
)
from app.services.source_preview import source_preview_payload

FIXTURES = Path(__file__).parent / "fixtures" / "languages"


def fixture(name: str) -> SourceContent:
    return SourceContent.model_validate(json.loads((FIXTURES / f"{name}.json").read_text())["source"])


def page(raw_text: str, *, path: str | None = None, headings=(), title=None, **extra) -> SourceContent:
    return SourceContent(
        source_kind=extra.pop("source_kind", "url"),
        source_ref="https://example.test",
        raw_text=raw_text,
        headings=list(headings),
        url_path=path,
        title=title,
        **extra,
    )


def codes(profile) -> dict[str, bool]:
    return {item.code: item.substantial for item in profile.languages}


EN_PARA = (
    "Our kindergarten welcomes children aged three to six, and every day is planned "
    "around play, stories, music and time outdoors with caring teachers."
)
EN_PARA_2 = (
    "Parents are always welcome to visit, and we keep families informed with a weekly "
    "letter about what their children learned and enjoyed."
)
EN_PARA_3 = "Children take part in calligraphy, martial arts and dance every week, and learn to work together."
ZH_PARA = "我们的幼儿园欢迎三至六岁的孩子，每天的活动都围绕游戏、故事、音乐和户外时间来安排，老师们细心照顾每一个孩子。"
ZH_PARA_2 = "我们随时欢迎家长来访，每周都会给家长写一封信，告诉他们孩子学到了什么、喜欢做什么。"
ZH_PARA_3 = "孩子们每周都有书法、武术和舞蹈课，在快乐的活动中认识中华文化，也学会和同伴一起合作。"
ZH_HANT_PARA = "我們的幼兒園歡迎三至六歲的孩子，每天的活動都圍繞遊戲、故事、音樂和戶外時間來安排，老師們細心照顧每一個孩子。"
MS_PARA_2 = (
    "Ibu bapa sentiasa dialu-alukan untuk melawat, dan kami memaklumkan keluarga "
    "melalui surat mingguan tentang apa yang dipelajari dan digemari anak-anak mereka."
)
MS_PARA = (
    "Tadika kami mengalu-alukan kanak-kanak berumur tiga hingga enam tahun, dan setiap "
    "hari dirancang dengan permainan, cerita, muzik dan masa di luar bersama guru yang penyayang."
)


class ScenarioTest(unittest.TestCase):
    """Plan Scenarios A-F."""

    def test_a_english_only_is_monolingual_english(self):
        profile = analyze(fixture("scenario_a_english_only"))

        self.assertEqual(profile.pattern, "monolingual")
        self.assertEqual(profile.primary, "en")
        self.assertEqual([item.code for item in profile.languages], ["en"])
        self.assertEqual(profile.pairs, [])
        self.assertTrue(all(p.locale == "en" for p in profile.pages))

    def test_b_bilingual_headings_are_inline_bilingual_and_paired_in_order(self):
        profile = analyze(fixture("scenario_b_bilingual_headings"))

        self.assertEqual(profile.pattern, "inline_bilingual")
        # English comes first in every "English | 中文" heading, so it leads,
        # whatever the letter counts say.
        self.assertEqual(profile.primary, "en")
        self.assertEqual(codes(profile), {"en": True, "zh-Hans": True})
        heading = next(p for p in profile.pairs if p.first == "Programmes")
        self.assertEqual((heading.kind, heading.first_lang), ("heading", "en"))
        self.assertEqual((heading.second, heading.second_lang), ("课程", "zh-Hans"))
        # Source order is kept: the first pair is the page's first heading.
        self.assertEqual((profile.pairs[0].first, profile.pairs[0].second), ("Welcome", "欢迎"))
        # Every page declares lang="en"; the text overrules it.
        self.assertTrue(any("not trusted" in line for line in profile.evidence))

    def test_c_full_mirrors_are_parallel_urls_at_full_coverage(self):
        profile = analyze(fixture("scenario_c_parallel_mirrors"))

        self.assertEqual(profile.pattern, "parallel_urls")
        self.assertEqual(profile.primary, "en")
        zh = next(item for item in profile.languages if item.code == "zh-Hans")
        self.assertEqual(zh.coverage, 1.0)
        mirror = next(p for p in profile.pages if p.path == "/zh/about")
        self.assertEqual((mirror.locale, mirror.url_locale), ("zh-Hans", "zh"))

    def test_d_three_languages_with_the_source_spelling_bm(self):
        profile = analyze(fixture("scenario_d_en_ms_zh"))

        self.assertEqual(profile.pattern, "parallel_urls")
        # The root's language is primary even though Malay has more letters.
        self.assertEqual(profile.primary, "en")
        self.assertEqual(codes(profile), {"en": True, "ms": True, "zh-Hans": True})
        bm = next(p for p in profile.pages if p.path == "/bm/about")
        self.assertEqual((bm.locale, bm.url_locale, bm.declared), ("ms", "ms", "en"))
        self.assertIn("Bahasa Malaysia", profile.reason)

    def test_e_partial_translations_report_their_coverage(self):
        profile = analyze(fixture("scenario_e_partial_translations"))

        self.assertEqual(profile.pattern, "parallel_urls")
        zh = next(item for item in profile.languages if item.code == "zh-Hans")
        self.assertEqual(zh.coverage, 0.4)  # 2 of 5 pages
        self.assertIn("40%", profile.reason)

    def test_f_a_chinese_brand_name_never_makes_a_chinese_site(self):
        profile = analyze(fixture("scenario_f_chinese_brand"))

        self.assertEqual(profile.primary, "en")
        self.assertIn(profile.pattern, ("monolingual", "incidental"))
        self.assertFalse(codes(profile).get("zh-Hans", False))


class ScriptTest(unittest.TestCase):
    def test_traditional_chinese_is_told_from_simplified(self):
        profile = analyze(page(f"{ZH_HANT_PARA}\n{ZH_HANT_PARA}。"))

        self.assertEqual(profile.primary, "zh-Hant")

    def test_han_with_no_differential_character_is_plain_zh_never_assumed(self):
        # 中文 + 人口 + 大小: identical in both scripts.
        text = "中文人口大小上下左右日月山水天地中文人口大小上下左右日月山水天地" * 4
        profile = analyze(page(text))

        self.assertEqual(profile.primary, "zh")
        self.assertTrue(any("undetermined" in line for line in profile.evidence))

    def test_declared_lang_breaks_only_the_tie_the_text_cannot(self):
        text = "中文人口大小上下左右日月山水天地中文人口大小上下左右日月山水天地" * 4
        profile = analyze(page(text, declared_lang="zh-TW"))

        self.assertEqual(profile.primary, "zh-Hant")

    def test_the_differential_table_never_counts_a_character_for_both(self):
        self.assertFalse(la.SIMPLIFIED_ONLY & la.TRADITIONAL_ONLY)
        self.assertGreaterEqual(len(la.SIMPLIFIED_ONLY), 150)

    def test_tamil_is_identified_by_script(self):
        tamil = "எங்கள் மழலையர் பள்ளி மூன்று முதல் ஆறு வயது வரையிலான குழந்தைகளை வரவேற்கிறது " * 4
        profile = analyze(page(tamil))

        self.assertEqual(profile.primary, "ta")


class LatinTest(unittest.TestCase):
    def test_malay_is_not_read_as_english(self):
        profile = analyze(page(f"{MS_PARA}\n{MS_PARA} Daftar sekarang untuk tahun depan."))

        self.assertEqual(profile.primary, "ms")

    def test_malay_family_is_indonesian_only_on_market_evidence(self):
        text = (
            "Kami menyediakan layanan pendidikan terbaik untuk anak Anda di Jakarta. "
            "Hubungi kami di +62 21 555 0199 untuk jadwal kunjungan sekolah.\n"
            "Sekolah kami berlokasi di Jakarta Selatan dan buka setiap hari kerja."
        )
        profile = analyze(page(text, links=["https://sekolah.example.id/"]))

        self.assertEqual(profile.primary, "id")

    def test_a_short_heading_inherits_its_section_language(self):
        # "Our Programmes" alone is read as German by py3langid (p≈0.26).
        source = page(f"Our Programmes\n{EN_PARA}\n{EN_PARA_2}", headings=["Our Programmes"])
        profile = analyze(source)

        self.assertEqual([item.code for item in profile.languages], ["en"])


class ExclusionTest(unittest.TestCase):
    def test_contacts_addresses_and_names_are_not_counted(self):
        source = page(
            f"{EN_PARA}\n{EN_PARA_2}\n"
            "陈美玲\n"
            "88, Jalan Bukit Bintang, 55100 Kuala Lumpur\n"
            "hello@example.test\nhttps://example.test/zh/关于我们\n+60 3-1234 5678",
            profile_candidates=[ProfileCandidate(name="陈美玲")],
        )
        profile = analyze(source)

        self.assertEqual([item.code for item in profile.languages], ["en"])

    def test_brand_from_the_reader_is_excluded(self):
        source = page(f"金莲大酒家 Golden Lotus\n{EN_PARA}\n{EN_PARA_2}")

        with_brand = analyze(source, brand_name="金莲大酒家 Golden Lotus")
        self.assertEqual([item.code for item in with_brand.languages], ["en"])

    def test_title_segments_every_page_shares_are_the_brand(self):
        brand = "阳光幼儿园"
        source = page(
            f"{brand}\n{EN_PARA}", title=f"Home | {brand}",
            discovered_pages=[page(f"{brand}\n{EN_PARA_2}", path="/about", title=f"About | {brand}")],
        )
        self.assertEqual([item.code for item in analyze(source).languages], ["en"])

    def test_facebook_digest_labels_and_machine_facts_are_not_the_owners_words(self):
        digest = "\n".join([
            "Tadika Murni — Kindergarten",
            f"About: {MS_PARA}",
            "Address: 12, Jalan Murni, 43000 Kajang, Selangor",
            "Phone: +60 12-345 6789",
            "Opening hours:",
            "  Monday 8:00 AM - 6:00 PM",
            "  Tuesday 8:00 AM - 6:00 PM",
            "Followers on Facebook: 761",
            "Updates from the Page:",
            f"  {MS_PARA}",
        ])
        profile = analyze(page(digest, source_kind="facebook"))

        self.assertEqual([item.code for item in profile.languages], ["ms"])


class PatternTest(unittest.TestCase):
    def test_two_languages_in_separate_sections_are_mixed_not_paired(self):
        source = page(
            f"About\n{EN_PARA}\n{EN_PARA_2}\n{EN_PARA_3}\n"
            f"家长心声\n{ZH_PARA}\n{ZH_PARA_2}\n{ZH_PARA_3}",
            headings=["About", "家长心声"],
        )
        profile = analyze(source)

        self.assertEqual(profile.pattern, "mixed_sections")
        self.assertEqual(profile.pairs, [])

    def test_adjacent_paragraphs_of_comparable_size_pair(self):
        source = page(
            f"{EN_PARA} {EN_PARA_2}\n{ZH_PARA}{ZH_PARA_2}\n"
            f"{EN_PARA_2} {EN_PARA}\n{ZH_PARA_2}{ZH_PARA}"
        )
        profile = analyze(source)

        self.assertEqual(profile.pattern, "inline_bilingual")
        self.assertEqual(len(profile.pairs), 2)
        self.assertEqual(profile.pairs[0].kind, "paragraph")

    def test_a_mirror_directory_in_the_primary_language_is_not_a_translation(self):
        source = page(
            EN_PARA,
            discovered_pages=[
                page(EN_PARA_2, path="/about"),
                page(f"{EN_PARA} again", path="/bm/about"),
            ],
        )
        profile = analyze(source)

        self.assertEqual(profile.pattern, "monolingual")
        self.assertTrue(any("not counted as a translation" in line for line in profile.evidence))

    def test_a_language_short_of_the_absolute_amount_says_so(self):
        source = page(f"{ZH_PARA}\n{ZH_PARA_2}\n{ZH_PARA_3}\n{EN_PARA}\n{EN_PARA_2}")
        profile = analyze(source)

        self.assertEqual(profile.pattern, "incidental")
        self.assertIn("only", profile.reason)

    def test_no_text_is_unknown(self):
        profile = analyze(page("2024 · 12 · 06"))

        self.assertEqual(profile.pattern, "unknown")
        self.assertIsNone(profile.primary)

    def test_nav_labels_are_read_once(self):
        source = page(
            EN_PARA,
            nav_links=[NavLink(label="Home | 首页", href="/"), NavLink(label="About | 关于", href="/about")],
        )
        profile = analyze(source)

        self.assertEqual([p.kind for p in profile.pairs], ["label", "label"])


class CaptureCorrectionTest(unittest.TestCase):
    """What the first real-site captures overturned (plan, P0-P1 corrections)."""

    def _site(self, *mirrors: SourceContent, nav=()) -> SourceContent:
        root = page(EN_PARA + "\n" + EN_PARA_2, path="/", nav_links=list(nav))
        english = [page(EN_PARA_3 + f" Page {i} of our English site.", path=f"/p{i}") for i in range(30)]
        return root.model_copy(update={"discovered_pages": [*english, *mirrors]})

    def test_one_mirror_page_is_enough_when_it_is_a_whole_page_in_that_language(self):
        """mmta: /bm is one page of 32, so its SHARE can't reach the threshold;
        what it holds is what proves the language."""
        site = self._site(page(MS_PARA + "\n" + MS_PARA_2, path="/bm"))
        profile = analyze(site)
        self.assertEqual(profile.pattern, "parallel_urls")
        self.assertTrue(codes(profile)["ms"])
        self.assertLess(next(i for i in profile.languages if i.code == "ms").share, la.MIN_SHARE)

    def test_a_language_the_menu_links_to_but_the_crawl_never_read_is_stated(self):
        nav = [NavLink(label="Bahasa Malaysia", href="/bm"), NavLink(label="中文", href="/zh")]
        profile = analyze(self._site(nav=nav))
        self.assertNotEqual(profile.pattern, "parallel_urls")  # unread: not counted
        self.assertIn("/bm", profile.reason)
        self.assertIn("/zh", profile.reason)
        self.assertTrue(any("not read" in line for line in profile.evidence))

    def test_an_ambiguous_code_in_the_menu_needs_its_language_label(self):
        nav = [NavLink(label="IT Support", href="/it"), NavLink(label="My Account", href="/my")]
        profile = analyze(self._site(nav=nav))
        self.assertFalse(any("not read" in line for line in profile.evidence))

    def test_place_names_and_address_lines_are_not_pairs(self):
        """mmta and mykiddyland: Title-Case places read as Malay beside English."""
        source = page(
            "\n".join([
                EN_PARA,
                "Older adults with dementia",
                "Kota Kinabalu, Sabah",
                "Community programs",
                "Taman Bukit Indah",
                "No 23, Block D2, Jalan PJU 1A",
                "20B, Dataran Ara Damansara,",
                EN_PARA_2,
            ]),
            path="/",
        )
        profile = analyze(source)
        self.assertEqual(profile.pairs, [])
        self.assertEqual(profile.primary, "en")

    def test_a_title_case_course_name_is_not_german(self):
        """uniquekindies: 'Enrichment Classes | 额外课程' scored its English side as de."""
        source = page(
            EN_PARA + "\n" + ZH_PARA,
            path="/",
            headings=["Enrichment Classes | 额外课程", "Programmes | 课程"],
        )
        profile = analyze(source)
        langs = {(p.first, p.first_lang) for p in profile.pairs}
        self.assertIn(("Enrichment Classes", "en"), langs)
        self.assertNotIn("de", codes(profile))

    def test_english_and_malay_paragraphs_side_by_side_still_pair(self):
        """The same-script rule narrows Latin pairs to confident paragraphs; it
        must not remove the real thing."""
        source = page("\n".join([EN_PARA, MS_PARA, EN_PARA_2, MS_PARA_2]), path="/")
        profile = analyze(source)
        self.assertEqual(len(profile.pairs), 2)
        self.assertEqual({(p.first_lang, p.second_lang) for p in profile.pairs}, {("en", "ms")})


class PreviewSeamTest(unittest.TestCase):
    """Every reader's preview passes `source_preview_payload`, which analyses once."""

    def test_the_preview_payload_carries_the_profile(self):
        source = fixture("scenario_c_parallel_mirrors")
        payload = source_preview_payload(
            url="https://example.test", final_url="https://example.test",
            source_content=source, brand_candidate=None, image_candidates=[],
        )

        profile = payload["source_content"]["language_profile"]
        self.assertEqual(profile["pattern"], "parallel_urls")
        self.assertIsNone(source.language_profile)  # the reader's object is untouched
        self.assertIsNone(payload["source_content"]["discovered_pages"][0]["language_profile"])

    def test_a_missing_model_states_itself_instead_of_failing_the_preview(self):
        """A backend image built before py3langid joined requirements.txt
        500'd every preview (POST /api/document/preview on an English DOCX)."""
        source = fixture("scenario_a_english_only")
        la._identifier.cache_clear()
        self.addCleanup(la._identifier.cache_clear)
        with mock.patch.dict(sys.modules, {"py3langid": None, "py3langid.langid": None}):
            payload = source_preview_payload(
                url="doc.docx", final_url="doc.docx",
                source_content=source, brand_candidate=None, image_candidates=[],
            )

        profile = payload["source_content"]["language_profile"]
        self.assertEqual(profile["pattern"], "unknown")
        self.assertEqual(profile["reason"], la.MODEL_UNAVAILABLE_REASON)

    def test_the_scraper_records_the_declared_lang(self):
        from app.services.scraper import _parse_rendered_html

        html = f'<html lang="en"><head><title>T</title></head><body><main><p>{ZH_PARA}</p></main></body></html>'
        parsed = _parse_rendered_html(html, "https://example.test/zh", require_text=False)

        self.assertEqual(parsed.source_content.declared_lang, "en")


class LocaleRegistryTest(unittest.TestCase):
    def test_source_spellings_resolve_to_bcp47(self):
        self.assertEqual(canonical_locale("bm"), "ms")
        self.assertEqual(canonical_locale("cn"), "zh-Hans")
        self.assertEqual(canonical_locale("tw"), "zh-Hant")
        self.assertEqual(canonical_locale("zh_TW"), "zh-Hant")
        self.assertEqual(canonical_locale("zh-hans"), "zh-Hans")
        self.assertEqual(canonical_locale("fr-FR"), "fr")
        self.assertIsNone(canonical_locale("xx"))
        self.assertIsNone(canonical_locale(""))

    def test_locale_segment_keeps_the_source_spelling(self):
        self.assertEqual(locale_segment("/bm/committee"), "bm")
        self.assertEqual(locale_segment("/zh-hans/about"), "zh-hans")
        self.assertIsNone(locale_segment("/about"))

    def test_labels_come_from_the_registry_and_keep_existing_switcher_text(self):
        self.assertEqual(locale_label("bm"), "Bahasa Malaysia")
        self.assertEqual(locale_label("ms"), "Bahasa Malaysia")
        self.assertEqual(locale_label("zh"), "中文")
        self.assertEqual(locale_label("cn"), "中文")
        self.assertEqual(locale_label("tw"), "繁體中文")
        self.assertEqual(locale_label("xx"), "XX")

    def test_every_registry_code_is_its_own_canonical_form(self):
        for code in LOCALES:
            self.assertEqual(canonical_locale(code), code)
        self.assertTrue(is_rtl("ar"))
        self.assertFalse(is_rtl("bm"))


if __name__ == "__main__":
    unittest.main()
