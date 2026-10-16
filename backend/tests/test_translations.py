"""Tests for a page's text in another language (services/translations.py).

A multilingual source (mmta.org.my ships /bm and /zh copies of every page) must
not be generated twice. The translated page is a clone of its counterpart with
only the prose swapped, and the model is shown the prose ALONE — a numbered
list of strings — so the guarantee under test is mostly a *negative* one: the
photo, the layout, the links and the people's names are never in what the
model sees, so they cannot come back changed.
"""

from __future__ import annotations

import json
import unittest

from app.models.content_blocks import (
    CtaBlock,
    HeroBlock,
    PagePlan,
    SourceContent,
    TeamBlock,
    TeamMember,
)
from app.models.industry import PageScaffold
from app.services.llm import LlmError
from app.services.translations import (
    TranslatedStrings,
    build_translated_pages,
    page_strings,
)

MALAY = {
    "Committee": "Jawatankuasa",
    "Committee | MMTA": "Jawatankuasa | MMTA",
    "The MMTA committee.": "Jawatankuasa MMTA.",
    "Meet the committee": "Temui jawatankuasa",
    "The people behind MMTA.": "Orang di sebalik MMTA.",
    "Join us": "Sertai kami",
    "Our committee": "Jawatankuasa kami",
    "President": "Presiden",
    "Sandra leads the association.": "Sandra mengetuai persatuan.",
}


class _FakeLlm:
    """Translates by lookup, the way a well-behaved model answers: one entry per
    string, in order, unknown strings returned unchanged."""

    def __init__(self, mapping=None, error: Exception | None = None, drop: int = 0):
        self.mapping = MALAY if mapping is None else mapping
        self.error = error
        self.drop = drop
        self.calls = 0
        self.last_strings: list[str] = []

    async def chat_json(self, *, system_prompt, user_prompt, schema, **kwargs):
        self.calls += 1
        self.last_user_prompt = user_prompt
        if self.error is not None:
            raise self.error
        strings = list(json.loads(user_prompt)["strings"].values())
        self.last_strings = strings
        translated = [self.mapping.get(text, text) for text in strings]
        return TranslatedStrings(translations=translated[: len(translated) - self.drop])

    async def list_models(self):
        return []


def _canonical() -> PagePlan:
    return PagePlan(
        page_type="team",
        slug="committee",
        title="Committee",
        blocks=[
            HeroBlock(
                kind="hero",
                headline="Meet the committee",
                subheadline="The people behind MMTA.",
                layout="background",
                image_url="https://cdn.example.my/hero-committee.jpg",
                image_ref=3,
                primary_cta_label="Join us",
                primary_cta_href="/membership",
            ),
            TeamBlock(
                kind="team",
                heading="Our committee",
                members=[
                    TeamMember(
                        name="Sandra Cheah",
                        role="President",
                        bio="Sandra leads the association.",
                        photo_url="https://cdn.example.my/sandra.jpg",
                    )
                ],
            ),
        ],
        seo_title="Committee | MMTA",
        seo_description="The MMTA committee.",
    )


def _scaffold(slug="bm/committee", locale="ms", of="committee") -> PageScaffold:
    return PageScaffold(
        page_type="team",
        slug=slug,
        title="Committee (Bahasa Malaysia)",
        sections=["hero", "team"],
        locale=locale,
        translation_of=of,
    )


class TranslationCloneTest(unittest.IsolatedAsyncioTestCase):
    async def _build(self, llm, scaffolds=None, pages=None, sources=None):
        return await build_translated_pages(
            pages if pages is not None else [_canonical()],
            scaffolds if scaffolds is not None else [_scaffold()],
            sources or {},
            client=llm,
        )

    async def test_prose_is_translated(self):
        [page] = await self._build(_FakeLlm())

        self.assertEqual(page.slug, "bm/committee")
        self.assertEqual(page.locale, "ms")  # BCP 47; the URL keeps /bm
        self.assertEqual(page.translation_of, "committee")
        self.assertEqual(page.title, "Jawatankuasa")
        self.assertEqual(page.seo_title, "Jawatankuasa | MMTA")
        self.assertEqual(page.blocks[0].headline, "Temui jawatankuasa")
        self.assertEqual(page.blocks[0].primary_cta_label, "Sertai kami")
        self.assertEqual(page.blocks[1].members[0].role, "Presiden")

    async def test_the_counterpart_is_left_untouched(self):
        canonical = _canonical()
        await self._build(_FakeLlm(), pages=[canonical])

        self.assertEqual(canonical.blocks[0].headline, "Meet the committee")
        self.assertEqual(canonical.slug, "committee")

    async def test_the_model_sees_strings_never_photos_links_or_names(self):
        """Sending whole blocks let a model blank `visual_policy` on every block
        of uniquekindies — and one bad field failed the whole page. What the
        model is never shown, it cannot change."""
        llm = _FakeLlm()
        [page] = await self._build(llm)

        prompt = llm.last_user_prompt
        for kept in ("hero-committee.jpg", "/membership", "Sandra Cheah", "sandra.jpg", "background"):
            self.assertNotIn(kept, prompt)
        hero, team = page.blocks
        self.assertEqual(hero.image_url, "https://cdn.example.my/hero-committee.jpg")
        self.assertEqual(hero.image_ref, 3)
        self.assertEqual(hero.layout, "background")
        self.assertEqual(team.members[0].name, "Sandra Cheah")
        self.assertEqual(team.members[0].photo_url, "https://cdn.example.my/sandra.jpg")

    async def test_a_repeated_string_is_sent_once(self):
        page = _canonical()
        page.blocks.append(CtaBlock(kind="cta", headline="Join us", cta_href="/membership"))
        self.assertEqual(page_strings(page).count("Join us"), 1)

        [built] = await self._build(_FakeLlm(), pages=[page])
        self.assertEqual(built.blocks[2].headline, "Sertai kami")

    async def test_a_blank_translation_keeps_the_original_string(self):
        [page] = await self._build(_FakeLlm({**MALAY, "Meet the committee": "   "}))

        self.assertEqual(page.blocks[0].headline, "Meet the committee")
        self.assertEqual(page.blocks[1].heading, "Jawatankuasa kami")

    async def test_a_reply_of_the_wrong_length_falls_back_to_the_primary_language(self):
        [page] = await self._build(_FakeLlm(drop=1))

        # The clone still ships — in English rather than shifted by one string.
        self.assertEqual(len(page.blocks), 2)
        self.assertEqual(page.blocks[0].headline, "Meet the committee")

    async def test_llm_failure_still_ships_the_page(self):
        [page] = await self._build(_FakeLlm(error=LlmError("model exploded")))

        self.assertEqual(page.slug, "bm/committee")
        self.assertEqual(page.blocks[0].headline, "Meet the committee")

    async def test_internal_links_follow_the_reader_into_their_language(self):
        pages = [_canonical(), _canonical().model_copy(update={"slug": "membership"})]
        scaffolds = [_scaffold(), _scaffold(slug="bm/membership", of="membership")]
        built = await self._build(_FakeLlm(), scaffolds=scaffolds, pages=pages)

        committee = next(p for p in built if p.slug == "bm/committee")
        # /membership was translated too, so the Malay CTA stays in Malay.
        self.assertEqual(committee.blocks[0].primary_cta_href, "/bm/membership")

    async def test_links_follow_only_into_the_same_language(self):
        """A Chinese page links to a Chinese twin, never to a Malay one."""
        pages = [_canonical(), _canonical().model_copy(update={"slug": "membership"})]
        scaffolds = [
            _scaffold(slug="zh/committee", locale="zh-Hans"),
            _scaffold(slug="bm/membership", of="membership"),
        ]
        built = await self._build(_FakeLlm({}), scaffolds=scaffolds, pages=pages)

        committee = next(p for p in built if p.slug == "zh/committee")
        self.assertEqual(committee.blocks[0].primary_cta_href, "/membership")

    async def test_links_to_untranslated_pages_are_left_alone(self):
        [page] = await self._build(_FakeLlm())

        # Only /bm/committee exists; /membership has no Malay version, so
        # pointing at /bm/membership would be a 404.
        self.assertEqual(page.blocks[0].primary_cta_href, "/membership")

    async def test_a_translation_without_its_counterpart_is_skipped(self):
        llm = _FakeLlm()
        built = await self._build(llm, scaffolds=[_scaffold(slug="bm/gallery", of="gallery")])

        self.assertEqual(built, [])
        self.assertEqual(llm.calls, 0)

    async def test_the_owners_own_wording_is_given_to_the_model(self):
        llm = _FakeLlm()
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my/bm/committee",
            title="Jawatankuasa",
            raw_text="Jawatankuasa MMTA terdiri daripada pakar terapi muzik.",
            url_path="/bm/committee",
        )
        [page] = await self._build(llm, sources={"bm/committee": source})

        self.assertIn("Jawatankuasa MMTA terdiri daripada", llm.last_user_prompt)
        self.assertEqual(page.translation_status, "source")

    async def test_a_page_with_no_owner_version_is_marked_ai_translated(self):
        [page] = await self._build(_FakeLlm())
        self.assertEqual(page.translation_status, "ai")

    async def test_a_localized_home_is_a_page_not_the_homepage(self):
        home = _canonical().model_copy(update={"slug": "", "is_homepage": True})
        [page] = await self._build(_FakeLlm(), pages=[home], scaffolds=[_scaffold(slug="bm", of="")])

        self.assertFalse(page.is_homepage)
        self.assertEqual(page.slug, "bm")
        self.assertEqual(page.translation_of, "")

    async def test_nothing_to_do_makes_no_llm_call(self):
        llm = _FakeLlm()
        self.assertEqual(await self._build(llm, scaffolds=[]), [])
        self.assertEqual(llm.calls, 0)


class CtaOnlyBlockTest(unittest.IsolatedAsyncioTestCase):
    async def test_cta_href_is_localized_but_its_label_is_translated(self):
        canonical = PagePlan(
            page_type="landing",
            slug="about",
            title="About",
            blocks=[CtaBlock(kind="cta", headline="Join us", cta_href="/about")],
            seo_title="About",
            seo_description="About us.",
        )
        [page] = await build_translated_pages(
            [canonical],
            [PageScaffold(
                page_type="landing",
                slug="bm/about",
                title="About (Bahasa Malaysia)",
                sections=["cta"],
                locale="ms",
                translation_of="about",
            )],
            {},
            client=_FakeLlm({"Join us": "Sertai kami", "About": "Tentang"}),
        )

        self.assertEqual(page.blocks[0].headline, "Sertai kami")
        self.assertEqual(page.blocks[0].cta_href, "/bm/about")
