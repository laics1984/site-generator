"""Multilingual P5: a translation is text on its page's ONE tree.

services/locale_overlay.py derives the overlay the CMS stores and applies it
for the preview; plan_to_site builds each translated page from its
counterpart's finished tree; translations.complete_translations finishes what
the string map missed; the push sends overlays, never pages
(docs/multilingual-plan.md P5, webtree-cms-api specs/multilingual-sites.md).
"""

from __future__ import annotations

import json
import pathlib
import re
import unittest

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.builder_schema import BodySchema, BuilderElement, BuilderElementContent, GeneratedPage, GeneratedSite, PageSeo
from app.models.content_blocks import CtaBlock, HeroBlock, MultilingualPlan, PagePlan, SitePlan
from app.services import locale_overlay
from app.services.cms_sync import ExistingPage, normalize_site_slugs, plan_sync
from app.services.schema_builder import plan_to_site
from app.services.translations import TranslatedStrings, complete_translations

_REPOS = pathlib.Path(__file__).resolve().parents[3]


def text(id_: str, value: str, **extra) -> BuilderElement:
    return BuilderElement(id=id_, name=extra.pop("name", "Body"), type="text",
                          content=BuilderElementContent(innerText=value), **extra)


def link(id_: str, label: str, href: str) -> BuilderElement:
    return BuilderElement(id=id_, name="Button", type="link", content=BuilderElementContent(innerText=label, href=href))


def section(*children: BuilderElement, id_: str = "s") -> BuilderElement:
    return BuilderElement(id=id_, name="Section", type="section", content=list(children))


class OverlayMirrorTest(unittest.TestCase):
    """The Python half must agree with the CMS's OverlayApplier."""

    def test_the_source_hash_is_the_cms_hash(self):
        # Computed by webtree-cms-api OverlayApplier::sourceHash for the same
        # node (2026-10-16); a drift here means every pushed translation would
        # read as outdated, or never would.
        node = BuilderElement(name="Button", type="link", content=BuilderElementContent(
            innerText="  Contact us ", href="/contact", ariaLabel="Contact 中文"))
        self.assertEqual(locale_overlay.source_hash(node), "a8866ae8e3c1d89f88de62046cde794412c51fba")

    def test_the_text_fields_are_the_cms_text_fields(self):
        php = _REPOS / "webtree-cms-api" / "app" / "Services" / "Localization" / "OverlayApplier.php"
        if not php.exists():
            self.skipTest(f"{php} not checked out beside this repo")
        declared = re.search(r"TEXT_FIELDS = \[([^\]]+)\]", php.read_text())
        self.assertIsNotNone(declared)
        self.assertEqual(tuple(re.findall(r"'([^']+)'", declared.group(1))), locale_overlay.TEXT_FIELDS)


class DeriveApplyTest(unittest.TestCase):
    MAP = {"Welcome": "Selamat datang", "We chose this school.": "Kami memilih sekolah ini.",
           "Bread baked the slow way": "Roti dibakar perlahan", "Contact us": "Hubungi kami"}

    def tree(self):
        group = BuilderElement(id="g", name="Headline group", type="container", content=[
            text("lead", "Bread baked"), text("accent", "the slow way", name="Headline accent")])
        return [section(text("h", "Welcome"), text("q", "“We chose this school.”"), group,
                        link("cta", "Contact us", "/contact"), text("n", "Unique Kindies"))]

    def test_every_mapped_string_is_overlaid_by_element_id(self):
        overlay = locale_overlay.derive(self.tree(), self.MAP, origin="ai")
        elements = overlay["elements"]
        self.assertEqual(elements["h"], {"innerText": "Selamat datang"})
        self.assertEqual(elements["q"], {"innerText": "“Kami memilih sekolah ini.”"})
        self.assertEqual(elements["cta"], {"innerText": "Hubungi kami"})
        # A split headline is one sentence: the lead takes it, the accent hides.
        self.assertEqual(elements["lead"], {"innerText": "Roti dibakar perlahan"})
        self.assertEqual(elements["accent"], {"hidden": True})
        self.assertEqual(overlay["meta"]["h"]["origin"], "ai")
        self.assertEqual([el.id for el in locale_overlay.missing(self.tree(), overlay)], ["n"])

    def test_apply_is_a_copy_with_the_words_and_links_of_the_language(self):
        tree = self.tree()
        overlay = locale_overlay.derive(tree, self.MAP, origin="ai")
        out = locale_overlay.apply(tree, overlay, lambda href: "/bm" + href)

        lines = out[0].content
        self.assertEqual([el.id for el in lines], ["h", "q", "g", "cta", "n"])
        self.assertEqual(lines[0].content.innerText, "Selamat datang")
        self.assertEqual([el.id for el in lines[2].content], ["lead"])  # accent hidden
        self.assertEqual(lines[3].content.href, "/bm/contact")
        self.assertEqual(tree[0].content[0].content.innerText, "Welcome")  # original untouched
        self.assertEqual(locale_overlay.as_text_map(tree, overlay)["Welcome"], "Selamat datang")


def _page(page_type, slug, title, *blocks, **kwargs) -> PagePlan:
    return PagePlan(page_type=page_type, slug=slug, title=title, blocks=list(blocks),
                    seo_title=title, seo_description=title, **kwargs)


class TranslatedPagesTest(unittest.IsolatedAsyncioTestCase):
    async def _build(self, plan: SitePlan):
        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            return await plan_to_site(plan, brand=BrandIdentity(name="Tadika", mood="modern", extracted_palette=["#0e7490"]))
        finally:
            settings.design_brain_enabled = original

    async def test_a_translation_is_its_pages_tree_with_its_words(self):
        plan = SitePlan(site_name="Tadika", pages=[
            _page("home", "", "Home", HeroBlock(headline="Welcome", image_query=None),
                  CtaBlock(headline="Ready?", cta_label="Visit us", cta_href="/contact"), is_homepage=True),
            _page("contact", "contact", "Contact", HeroBlock(headline="Get in touch", image_query=None)),
            _page("home", "bm", "Laman utama", locale="ms", translation_of="",
                  translation_status="source",
                  text_map={"Welcome": "Selamat datang", "Ready?": "Sedia?", "Visit us": "Lawati kami"}),
            _page("contact", "bm/contact", "Hubungi", locale="ms", translation_of="contact",
                  text_map={"Get in touch": "Hubungi kami"}),
        ])
        site = await self._build(plan)
        home = next(p for p in site.pages if p.slug == "")
        bm = next(p for p in site.pages if p.slug == "bm")

        def walk(els):
            for el in els:
                yield el
                if isinstance(el.content, list):
                    yield from walk(el.content)

        base_ids = [el.id for el in walk(home.body_schema.elements)]
        bm_ids = [el.id for el in walk(bm.body_schema.elements)]
        self.assertTrue(set(bm_ids) <= set(base_ids))  # the same tree, nothing new
        texts = [el.content.innerText for el in walk(bm.body_schema.elements)
                 if isinstance(el.content, BuilderElementContent) and el.content.innerText]
        self.assertIn("Selamat datang", " ".join(texts))
        hrefs = [el.content.href for el in walk(bm.body_schema.elements)
                 if isinstance(el.content, BuilderElementContent) and el.content.href]
        self.assertIn("/bm/contact", hrefs)
        self.assertEqual((bm.locale, bm.translation_of, bm.translation_status), ("ms", "", "source"))
        self.assertEqual(bm.overlay["page"]["title"], "Laman utama")
        self.assertEqual(bm.seo.canonical, "/bm")
        # Never in the content menus.
        self.assertTrue(bm.menu_hidden)
        self.assertNotIn("bm", [n.slug for n in site.page_tree])


class _FakeLlm:
    def __init__(self, mapping):
        self.mapping, self.prompts = mapping, []

    async def chat_json(self, *, system_prompt, user_prompt, schema, **kwargs):
        self.prompts.append(user_prompt)
        strings = json.loads(user_prompt)["strings"]
        return TranslatedStrings(translations={k: self.mapping.get(v, v) for k, v in strings.items()})

    async def list_models(self):
        return []


def _generated(slug, elements, **extra) -> GeneratedPage:
    return GeneratedPage(slug=slug, title=extra.pop("title", slug or "Home"), is_homepage=slug == "",
                         body_schema=BodySchema(elements=elements), seo=PageSeo(), **extra)


class CompleteTranslationsTest(unittest.IsolatedAsyncioTestCase):
    async def test_what_the_map_missed_is_translated_and_a_name_counts_as_done(self):
        base = [section(text("eyebrow", "About"), text("h", "Welcome"), text("brand", "UNIQUE KINDIES"))]
        overlay = locale_overlay.derive(base, {"Welcome": "欢迎"}, origin="ai")
        site = GeneratedSite(
            site_name="Tadika",
            pages=[_generated("", base),
                   _generated("zh", locale_overlay.apply(base, overlay), locale="zh-Hans",
                              translation_of="", overlay=overlay)],
            header_schema=BuilderElement(id="hdr", name="Header", type="__header", content=[link("hcta", "Welcome", "/")]),
            footer_schema=BuilderElement(id="ftr", name="Footer", type="__footer", content=[text("f", "All rights reserved")]),
            multilingual=MultilingualPlan(mode="separate", primary="en", secondary=["zh-Hans"]),
        )
        llm = _FakeLlm({"About": "关于", "All rights reserved": "版权所有"})
        left = await complete_translations(site, {}, client=llm)

        zh = site.pages[1]
        self.assertEqual(left, [])
        self.assertEqual(zh.overlay["elements"]["eyebrow"], {"innerText": "关于"})
        self.assertEqual(zh.overlay["elements"]["brand"], {"innerText": "UNIQUE KINDIES"})
        self.assertEqual(locale_overlay.missing(base, zh.overlay), [])
        self.assertEqual(zh.body_schema.elements[0].content[0].content.innerText, "关于")
        # Only what the map missed was sent.
        self.assertNotIn("Welcome", llm.prompts[0])
        layout = site.layout_overlays["zh-Hans"]["elements"]
        self.assertEqual(layout["hcta"], {"innerText": "欢迎"})  # reused from the page
        self.assertEqual(layout["f"], {"innerText": "版权所有"})


class SyncTest(unittest.TestCase):
    def site(self):
        return GeneratedSite(site_name="Tadika", pages=[
            _generated("", [section()]),
            _generated("profile/ashley", [section()], title="Ashley"),
            _generated("bm/profile/ashley", [section()], locale="ms", translation_of="profile/ashley",
                       overlay={"elements": {}}),
        ])

    def test_a_translation_is_never_a_cms_page_and_an_old_mirror_is_archived(self):
        site = self.site()
        existing = [
            ExistingPage(id="h", slug="", title="Home", status="published", is_homepage=True),
            # Pushed before translations existed: a page of its own at the mirror URL.
            ExistingPage(id="m", slug="bm/profile-ashley", title="Ashley (BM)", status="published"),
        ]
        normalize_site_slugs(site, existing_slugs={p.slug for p in existing})
        plan = plan_sync(site, existing)

        landing = [c.slug for c in plan.changes if c.action != "archive"]
        self.assertNotIn("bm/profile-ashley", landing)
        self.assertNotIn("bm/profile/ashley", landing)
        self.assertIn("m", [c.page_id for c in plan.changes if c.action == "archive"])

    def test_a_translation_follows_its_page_onto_the_live_spelling(self):
        site = self.site()
        live = ExistingPage(id="a", slug="profile-ashley", title="Ashley", status="published")
        normalize_site_slugs(site, existing_slugs={"", live.slug})
        bm = site.pages[2]
        self.assertEqual((bm.translation_of, bm.slug), ("profile-ashley", "bm/profile-ashley"))


class _Cms:
    """Records the language calls the push makes."""

    def __init__(self, *, entitled: bool, incomplete: set[str] = frozenset()):
        self.entitled, self.incomplete, self.calls = entitled, set(incomplete), []

    async def put_languages(self, token, config):
        self.calls.append(("languages", config))
        return {**config, "entitled": self.entitled}

    async def get_translation(self, token, kind, target, locale):
        return {"draftVersion": None}

    async def save_translation(self, token, kind, target, locale, *, overlay, base_draft_version):
        self.calls.append(("save", kind, target, locale, overlay))
        return {}

    async def publish_translation(self, token, kind, target, locale):
        from app.services.cms_client import CmsApiError

        if target in self.incomplete:
            raise CmsApiError(409, "incomplete", response_body={"error": {"code": "TRANSLATION_INCOMPLETE"}})
        self.calls.append(("publish", kind, target, locale))
        return {}


class PushLanguagesTest(unittest.IsolatedAsyncioTestCase):
    def req(self, *, publish=True):
        from app.services.push_orchestrator import PushRequest

        site = GeneratedSite(
            site_name="Tadika",
            pages=[_generated("", [section()]), _generated("about", [section()], title="About"),
                   _generated("bm/about", [section()], title="Tentang", locale="ms",
                              translation_of="about", overlay={"elements": {"h": {"innerText": "Tentang"}}})],
            multilingual=MultilingualPlan(mode="separate", primary="en", secondary=["ms"], prefixes={"ms": "bm"}),
            layout_overlays={"ms": {"elements": {"f": {"innerText": "Hak cipta"}}, "meta": {}}},
        )
        return PushRequest(site=site, cms_email="", cms_password="", entity_token="tok", publish=publish)

    async def _push(self, cms, req):
        from app.services.push_orchestrator import PushReport, _LandedPage, _push_languages

        report = PushReport()
        landed = [_LandedPage(page=req.site.pages[0], page_id="p-home", draft_version=1),
                  _LandedPage(page=req.site.pages[1], page_id="p-about", draft_version=1)]
        menus = [{"id": "menu-primary", "purpose": "primary", "items": [{"id": "i1", "label": "About", "href": "/about"}]}]
        await _push_languages(cms, req, report, landed, menus)
        return report

    async def test_overlays_go_to_the_counterparts_cms_page_and_publish_when_allowed(self):
        cms = _Cms(entitled=True)
        report = await self._push(cms, self.req())

        self.assertEqual(cms.calls[0], ("languages", {
            "primary": "en", "mode": "separate", "locales": [{"code": "ms", "prefix": "bm"}]}))
        saves = [c for c in cms.calls if c[0] == "save"]
        self.assertEqual([(c[1], c[2], c[3]) for c in saves], [("page", "p-about", "ms"), ("layout", "website", "ms")])
        layout = saves[1][4]
        self.assertEqual(layout["menus"], {"menu-primary": {"i1": {"label": "Tentang"}}})
        self.assertEqual(layout["elements"], {"f": {"innerText": "Hak cipta"}})
        self.assertEqual(len([c for c in cms.calls if c[0] == "publish"]), 2)
        step = report.steps[-1]
        self.assertEqual((step.name, step.data["published"], step.warning), ("languages", 2, None))

    async def test_without_the_plan_translations_are_kept_as_drafts_and_the_step_says_why(self):
        cms = _Cms(entitled=False)
        report = await self._push(cms, self.req())

        self.assertFalse([c for c in cms.calls if c[0] == "publish"])
        self.assertEqual(report.steps[-1].data["draft"], 2)
        self.assertIn("plan doesn't include more than one language", report.steps[-1].warning)

    async def test_an_incomplete_translation_stays_a_draft(self):
        cms = _Cms(entitled=True, incomplete={"p-about"})
        report = await self._push(cms, self.req())

        self.assertEqual(report.steps[-1].data["incomplete"], 1)
        self.assertIn("untranslated text", report.steps[-1].warning)
