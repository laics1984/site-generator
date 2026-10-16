"""Multilingual P2: the strategy, the page set per language, and the
same-page bilingual pass (docs/multilingual-plan.md §2-3).

The profiles here are built inline; what produces them from real text is
`test_language_analysis.py`'s job.
"""

from __future__ import annotations

import json
import unittest

from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.models.content_blocks import (
    CtaBlock,
    HeroBlock,
    InlineTranslation,
    LanguageProfile,
    LanguageShare,
    MultilingualChoice,
    MultilingualPlan,
    PageLanguage,
    PagePlan,
    SourceContent,
)
from app.models.industry import PageScaffold
from app.services.inline_translation import SECOND_LANGUAGE_NAME, apply_inline_translation
from app.services.locale import url_prefix
from app.services.multilingual_strategy import decide, with_plan
from app.services.page_inference import scaffolds_for_languages
from app.services.translations import TranslatedStrings, add_inline_translations, page_strings


def share(code: str, share_: float, *, substantial: bool = True, confidence: float = 0.95) -> LanguageShare:
    return LanguageShare(
        code=code, label=code, share=share_, confidence=confidence,
        coverage=1.0, substantial=substantial, amount=1000,
    )


def profile(pattern: str, *shares: LanguageShare, primary: str = "en", pages=()) -> LanguageProfile:
    return LanguageProfile(primary=primary, languages=list(shares), pattern=pattern, pages=list(pages))


ENGLISH_ONLY = profile("monolingual", share("en", 1.0))
UNIQUEKINDIES = profile("inline_bilingual", share("en", 0.56), share("zh-Hans", 0.44, confidence=1.0))
MMTA = profile(
    "parallel_urls", share("en", 0.96), share("ms", 0.02, confidence=0.91),
    share("zh-Hans", 0.01, confidence=1.0),
    pages=[
        PageLanguage(path="/", locale="en"),
        PageLanguage(path="/bm", locale="ms", url_locale="ms"),
        PageLanguage(path="/zh", locale="zh-Hans", url_locale="zh-Hans"),
    ],
)


class DecideTest(unittest.TestCase):
    """Plan §2's auto rules, Rule 5's override and Q4's added languages."""

    def test_an_english_only_source_is_a_single_language_site(self):
        plan = decide(ENGLISH_ONLY)
        self.assertEqual((plan.mode, plan.primary, plan.secondary), ("single", "en", []))

    def test_a_chinese_only_source_is_a_chinese_site_not_an_english_one(self):
        plan = decide(profile("monolingual", share("zh-Hans", 1.0), primary="zh-Hans"))
        self.assertEqual((plan.mode, plan.primary), ("single", "zh-Hans"))

    def test_incidental_text_never_earns_a_second_language(self):
        plan = decide(profile("incidental", share("en", 0.98), share("zh-Hans", 0.02, substantial=False)))
        self.assertEqual(plan.mode, "single")
        self.assertIn("isn't enough", plan.reason)

    def test_bilingual_headings_keep_both_languages_on_the_page(self):
        plan = decide(UNIQUEKINDIES)
        self.assertEqual((plan.mode, plan.primary, plan.secondary), ("bilingual", "en", ["zh-Hans"]))

    def test_mirrors_become_separate_pages_under_the_sources_own_directories(self):
        plan = decide(MMTA)
        self.assertEqual(plan.mode, "separate")
        self.assertEqual(plan.secondary, ["ms", "zh-Hans"])
        # /bm survives: an existing site's indexed URLs keep working.
        self.assertEqual(plan.prefixes, {"ms": "bm", "zh-Hans": "zh"})

    def test_the_users_mode_wins(self):
        plan = decide(MMTA, MultilingualChoice(mode="single"))
        self.assertEqual((plan.mode, plan.secondary, plan.chosen_by), ("single", [], "override"))

    def test_an_added_language_on_a_one_language_source_gets_its_own_pages(self):
        plan = decide(ENGLISH_ONLY, MultilingualChoice(locales=["bm"]))
        self.assertEqual((plan.mode, plan.secondary), ("separate", ["ms"]))
        self.assertEqual(plan.prefixes, {"ms": "ms"})
        self.assertIn("added at your request", plan.reason)

    def test_an_added_language_joins_the_detected_ones(self):
        plan = decide(MMTA, MultilingualChoice(locales=["ta"]))
        self.assertEqual(plan.secondary, ["ms", "zh-Hans", "ta"])

    def test_a_page_shows_two_languages_at_most(self):
        plan = decide(UNIQUEKINDIES, MultilingualChoice(locales=["ms"]))
        self.assertEqual(plan.secondary, ["zh-Hans"])
        self.assertIn("left out", plan.reason)

    def test_asking_for_two_languages_with_only_one_says_so(self):
        plan = decide(ENGLISH_ONLY, MultilingualChoice(mode="bilingual"))
        self.assertEqual(plan.mode, "single")
        self.assertIn("no second language", plan.reason)

    def test_thin_evidence_is_surfaced(self):
        weak = profile("parallel_urls", share("en", 0.9), share("ms", 0.1, confidence=0.4))
        plan = decide(weak)
        self.assertTrue(plan.uncertain)
        self.assertIn("check the preview", plan.reason)

    def test_no_profile_is_a_single_language_site(self):
        self.assertEqual(decide(None).mode, "single")

    def test_with_plan_analyses_a_source_that_arrives_without_a_profile(self):
        source = SourceContent(
            source_kind="paste", source_ref="paste",
            raw_text="Our kindergarten welcomes children aged three to six, and every day is planned around play.",
        )
        stamped = with_plan(source, None)
        self.assertIsNotNone(stamped.language_profile)
        self.assertEqual(stamped.multilingual.primary, "en")
        self.assertIsNone(source.multilingual)  # the caller's object is untouched


class UrlPrefixTest(unittest.TestCase):
    def test_chinese_is_short_unless_both_scripts_are_published(self):
        self.assertEqual(url_prefix("zh-Hans", ["en", "zh-Hans"]), "zh")
        self.assertEqual(url_prefix("zh-Hans", ["zh-Hans", "zh-Hant"]), "zh-hans")
        self.assertEqual(url_prefix("zh-Hant", ["zh-Hans", "zh-Hant"]), "zh-hant")
        self.assertEqual(url_prefix("ms", ["en", "ms"]), "ms")


def scaffold(slug: str, **extra) -> PageScaffold:
    return PageScaffold(
        page_type=extra.pop("page_type", "about"), slug=slug, title=slug.title() or "Home",
        sections=["hero"], is_homepage=slug == "", **extra,
    )


class LanguagePageSetTest(unittest.TestCase):
    """What the picker offers and generation builds, per mode."""

    def _pages(self):
        return [
            scaffold("", page_type="home"),
            scaffold("about"),
            scaffold("privacy", page_type="privacy", is_legal=True),
            scaffold("product/a", record_set="product/a"),
            # The owner's own Malay About page, crawled at /bm/about.
            scaffold("bm/about", locale="ms", translation_of="about", source_url="https://x.my/bm/about"),
        ]

    def test_separate_gives_every_page_a_version_in_every_language(self):
        plan = decide(MMTA)
        slugs = {s.slug: s for s in scaffolds_for_languages(self._pages(), plan, add_missing=True)}
        # The owner's own Malay page is kept, never duplicated.
        self.assertEqual(slugs["bm/about"].source_url, "https://x.my/bm/about")
        self.assertEqual(
            {slug for slug in slugs if slug.startswith(("bm", "zh"))},
            {"bm", "bm/about", "zh", "zh/about"},
        )
        self.assertEqual(slugs["zh/about"].locale, "zh-Hans")
        self.assertEqual(slugs["zh/about"].translation_of, "about")
        self.assertEqual(slugs["zh"].translation_of, "")

    def test_legal_pages_and_catalogue_records_are_not_translated(self):
        plan = decide(MMTA)
        slugs = {s.slug for s in scaffolds_for_languages(self._pages(), plan, add_missing=True)}
        self.assertFalse({"bm/privacy", "zh/privacy", "bm/product/a"} & slugs)

    def test_a_single_or_bilingual_site_builds_no_language_pages(self):
        for plan in (decide(ENGLISH_ONLY), decide(UNIQUEKINDIES)):
            out = scaffolds_for_languages(self._pages(), plan, add_missing=True)
            self.assertFalse([s for s in out if s.locale], plan.mode)

    def test_generation_filters_but_never_adds(self):
        """A page the user unticked in the picker stays unticked."""
        out = scaffolds_for_languages(self._pages(), decide(MMTA), add_missing=False)
        self.assertEqual([s.slug for s in out if s.locale], ["bm/about"])


def text(name: str, value: str, **extra) -> BuilderElement:
    return BuilderElement(name=name, type="text", content=BuilderElementContent(innerText=value), **extra)


def section(*children: BuilderElement) -> BuilderElement:
    return BuilderElement(name="Section", type="section", content=list(children))


class InlineTranslationTest(unittest.TestCase):
    """Mode B: the second language joins each string as its own line."""

    MAP = InlineTranslation(lang="zh-Hans", text={
        "Our programmes": "我们的课程",
        "A warm place to learn.": "一个温暖的学习地方。",
        "Bread baked the slow way": "慢工烘焙的面包",
    })

    def test_each_translated_string_gains_a_lang_tagged_line_after_it(self):
        heading = text("Heading", "Our programmes", htmlTag="h2", styles={"fontSize": "40px"})
        body = text("Body", "A warm place to learn.")
        tree = [section(heading, body)]
        added = apply_inline_translation(tree, self.MAP)

        lines = tree[0].content
        self.assertEqual(added, 2)
        self.assertEqual([el.content.innerText for el in lines], [
            "Our programmes", "我们的课程", "A warm place to learn.", "一个温暖的学习地方。",
        ])
        second = lines[1]
        self.assertEqual(second.lang, "zh-Hans")
        self.assertEqual(second.name, SECOND_LANGUAGE_NAME)
        # One <h1>/<h2> per heading, in the page's own language.
        self.assertIsNone(second.htmlTag)
        # Both languages at equal weight, as the source presents them.
        self.assertEqual(second.styles, {"fontSize": "40px"})
        self.assertIsNot(second.styles, heading.styles)

    def test_a_split_headline_is_one_string_and_its_line_follows_the_group(self):
        group = BuilderElement(name="Headline group", type="container", content=[
            text("Headline", "Bread baked"), text("Headline accent", "the slow way"),
        ])
        tree = [section(group)]
        apply_inline_translation(tree, self.MAP)

        self.assertEqual(len(group.content), 2)  # never between the halves
        self.assertEqual(tree[0].content[1].content.innerText, "慢工烘焙的面包")

    def test_a_quote_the_template_wrapped_keeps_its_marks(self):
        quote = text("Quote", "“We chose this school.”")
        tree = [section(quote)]
        apply_inline_translation(tree, InlineTranslation(lang="zh-Hans", text={"We chose this school.": "我们选择了这所学校。"}))
        self.assertEqual(tree[0].content[1].content.innerText, "“我们选择了这所学校。”")

    def test_links_and_untranslated_strings_stay_as_they_are(self):
        button = BuilderElement(name="Button", type="link", content=BuilderElementContent(innerText="Our programmes", href="/p"))
        other = text("Body", "Nothing mapped here.")
        tree = [section(button, other)]
        self.assertEqual(apply_inline_translation(tree, self.MAP), 0)
        self.assertEqual(len(tree[0].content), 2)

    def test_no_map_is_a_no_op(self):
        tree = [section(text("Body", "A warm place to learn."))]
        self.assertEqual(apply_inline_translation(tree, None), 0)


def page(headline: str, cta: str, slug: str = "about") -> PagePlan:
    return PagePlan(
        page_type="about", slug=slug, title="About",
        blocks=[
            HeroBlock(kind="hero", headline=headline, subheadline="", layout="background"),
            CtaBlock(kind="cta", headline=cta, cta_href="/contact"),
        ],
        seo_title="About", seo_description="About us.",
    )


class _FakeLlm:
    """Translates by lookup: one entry per string, in order."""

    def __init__(self, mapping: dict[str, str], *, drop: int = 0):
        self.mapping = mapping
        self.drop = drop
        self.prompts: list[str] = []

    async def chat_json(self, *, system_prompt, user_prompt, schema, **kwargs):
        self.prompts.append(user_prompt)
        strings = list(json.loads(user_prompt)["strings"].values())
        out = [self.mapping.get(text, text) for text in strings]
        return TranslatedStrings(translations=out[: len(out) - self.drop])

    async def list_models(self):
        return []


class PageStringsTest(unittest.TestCase):
    def test_every_prose_string_once_with_the_page_title_and_seo(self):
        about = page("Welcome", "Welcome")
        # The CTA's default button label is prose the page shows, so it goes too.
        self.assertEqual(page_strings(about), ["About", "About us.", "Welcome", "Get started"])


class AddInlineTranslationsTest(unittest.IsolatedAsyncioTestCase):
    async def test_each_page_gets_its_map_and_the_owners_page_as_wording(self):
        about = page("Welcome", "Visit us")
        llm = _FakeLlm({"Welcome": "欢迎", "Visit us": "欢迎参观", "Unique Kindies": "Unique Kindies"})
        own = SourceContent(source_kind="url", source_ref="x", raw_text="欢迎来到快乐儿童之家")
        await add_inline_translations([about], "zh-Hans", {"about": own}, primary="en", client=llm)

        self.assertEqual(about.inline_translation.lang, "zh-Hans")
        self.assertEqual(about.inline_translation.text, {"Welcome": "欢迎", "Visit us": "欢迎参观"})
        self.assertIn("欢迎来到快乐儿童之家", llm.prompts[0])
        # The primary page itself is untouched.
        self.assertEqual(about.blocks[0].headline, "Welcome")

    async def test_an_unchanged_string_is_not_a_translation(self):
        about = page("Unique Kindies", "Visit us")
        await add_inline_translations([about], "zh-Hans", {}, primary="en", client=_FakeLlm({"Visit us": "欢迎参观"}))
        self.assertNotIn("Unique Kindies", about.inline_translation.text)

    async def test_an_owners_pair_is_split_and_kept_word_for_word(self):
        """uniquekindies: the planner copied "… | 品格与行为培养" verbatim."""
        about = page("Character & Behaviour Development | 品格与行为培养", "Visit us")
        llm = _FakeLlm({"Visit us": "欢迎参观"})
        await add_inline_translations([about], "zh-Hans", {}, primary="en", client=llm)

        self.assertEqual(about.blocks[0].headline, "Character & Behaviour Development")
        self.assertEqual(
            about.inline_translation.text["Character & Behaviour Development"], "品格与行为培养"
        )
        # The model is never asked to translate what the owner already wrote.
        self.assertNotIn("Character & Behaviour", llm.prompts[0])

    async def test_a_failed_translation_leaves_the_page_monolingual(self):
        about = page("Welcome", "Visit us")
        await add_inline_translations([about], "zh-Hans", {}, primary="en", client=_FakeLlm({"Welcome": "欢迎"}, drop=1))
        self.assertIsNone(about.inline_translation)


class WriteInTest(unittest.TestCase):
    """Mode A: the planner is told the language, not left to infer it."""

    def test_the_scaffolded_prompt_names_the_primary_language(self):
        from app.services.planner import _build_scaffolded_user_prompt

        source = SourceContent(
            source_kind="url", source_ref="x", raw_text="Welcome / 欢迎",
            multilingual=MultilingualPlan(mode="bilingual", primary="en", secondary=["zh-Hans"]),
        )
        payload = json.loads(_build_scaffolded_user_prompt(source, None, []))
        self.assertEqual(payload["write_in"], "English (en)")

    def test_no_plan_adds_nothing(self):
        from app.services.planner import _build_user_prompt

        source = SourceContent(source_kind="url", source_ref="x", raw_text="Hello")
        self.assertNotIn("write_in", json.loads(_build_user_prompt(source)))


class LanguageMenuTest(unittest.TestCase):
    def test_a_separate_language_site_gets_a_language_menu_one_item_per_language(self):
        from app.models.builder_schema import PageNode
        from app.services.menu_builder import UTILITY_MENU_ID, build_menus

        menus = build_menus(
            [PageNode(slug="", title="首页", is_homepage=True)],
            languages=MultilingualPlan(
                mode="separate", primary="zh-Hans", secondary=["en", "ms"], prefixes={"en": "en", "ms": "bm"},
            ),
        )
        menu = next(m for m in menus if m["id"] == UTILITY_MENU_ID)
        self.assertEqual(menu["purpose"], "language")
        self.assertEqual(
            [(i["id"], i["label"], i["locale"], i["href"]) for i in menu["items"]],
            [("language-zh-Hans", "中文", "zh-Hans", "/"),
             ("language-en", "English", "en", "/en"),
             ("language-ms", "Bahasa Malaysia", "ms", "/bm")],
        )

    def test_a_single_or_bilingual_site_has_no_language_menu(self):
        from app.models.builder_schema import PageNode
        from app.services.menu_builder import build_menus

        for plan in (None, MultilingualPlan(mode="bilingual", primary="en", secondary=["zh-Hans"])):
            menus = build_menus([PageNode(slug="", title="Home", is_homepage=True)], languages=plan)
            self.assertFalse([m for m in menus if m["purpose"] == "language"])


class RecipeEndpointTest(unittest.TestCase):
    """The picker and the build read one plan."""

    def test_the_recipe_returns_the_plan_and_the_pages_it_publishes(self):
        from fastapi.testclient import TestClient

        from app.main import app

        source = SourceContent(
            source_kind="url", source_ref="https://x.test", raw_text="Welcome to our school.",
            language_profile=MMTA,
        )
        response = TestClient(app).post("/api/pages/recipe", json={
            "source": source.model_dump(mode="json"),
            "industry_override": "childcare",
        })
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["multilingual"]["mode"], "separate")
        locales = {page["locale"] for page in body["inferred_pages"] if page.get("locale")}
        self.assertEqual(locales, {"ms", "zh-Hans"})
        self.assertIn({"code": "ta", "label": "தமிழ்"}, body["available_locales"])

        single = TestClient(app).post("/api/pages/recipe", json={
            "source": source.model_dump(mode="json"),
            "industry_override": "childcare",
            "multilingual": {"mode": "single", "locales": []},
        }).json()
        self.assertEqual(single["multilingual"]["mode"], "single")
        self.assertFalse([p for p in single["inferred_pages"] if p.get("locale")])


class SplitBilingualTest(unittest.TestCase):
    def test_a_pair_splits_by_script_in_either_order(self):
        from app.services.language_analysis import split_bilingual

        self.assertEqual(split_bilingual("Programmes | 课程", "en", "zh-Hans"), ("Programmes", "课程"))
        self.assertEqual(split_bilingual("课程 / Programmes", "en", "zh-Hans"), ("Programmes", "课程"))

    def test_one_script_never_splits(self):
        from app.services.language_analysis import split_bilingual

        # English and Malay share a script: a slash in a heading stays a slash.
        self.assertIsNone(split_bilingual("Day care / After school", "en", "ms"))
        self.assertIsNone(split_bilingual("Hello | World", "en", "zh-Hans"))


class ReplyShapeTest(unittest.TestCase):
    def test_a_numbered_reply_is_read_by_number(self):
        self.assertEqual(
            TranslatedStrings.model_validate({"translations": {"2": "b", "1": "a"}}).translations,
            ["a", "b"],
        )

    def test_a_gap_in_the_numbers_cannot_pass_for_a_whole_reply(self):
        reply = TranslatedStrings.model_validate({"translations": {"1": "a", "3": "c"}})
        self.assertNotEqual(len(reply.translations), 2)


class FencedReplyTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_fenced_reply_validates_without_a_retry(self):
        """The local client never stripped a markdown fence; a fenced reply
        failed validation twice for nothing but its wrapper."""
        from app.services.llm import _validated

        calls = []

        async def post(payload):
            calls.append(payload)
            return '```json\n{"translations": ["欢迎"]}\n```'

        result = await _validated(post, {"messages": [], "max_tokens": 100}, TranslatedStrings)
        self.assertEqual(result.translations, ["欢迎"])
        self.assertEqual(len(calls), 1)


class PreviewLanguagesVendoredTest(unittest.TestCase):
    """The preview's language helpers are webtree-public's, verbatim (P4):
    hreflang, <html lang dir> and the language menu must read the same in the
    preview as on the published site. Skips while the sibling checkout's
    branch predates lib/languages.ts."""

    def test_the_preview_copy_matches_upstream(self):
        import pathlib

        repos = pathlib.Path(__file__).resolve().parents[3]
        public = repos / "webtree-public" / "lib" / "languages.ts"
        preview = repos / "site-generator" / "frontend" / "src" / "preview" / "lib" / "languages.ts"
        if not public.exists():
            self.skipTest(f"{public} not checked out beside this repo")
        body = preview.read_text().split("\n", 3)[3]  # the three-line provenance header
        self.assertEqual(public.read_text().replace("~/types/public", "./public"), body)
