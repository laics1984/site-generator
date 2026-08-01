"""Tests for cloning a page into another language (services/translations.py).

A multilingual source (mmta.org.my ships /bm and /zh copies of every page) must
not be generated twice. The translated page is a clone of its counterpart with
only the prose swapped, so the guarantee under test is mostly a *negative* one:
whatever the model returns, the photo, the layout, the links and the people's
names are the counterpart's.
"""

from __future__ import annotations

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
    TranslatedPageContent,
    build_translated_pages,
)


class _FakeLlm:
    """Returns a canned TranslatedPageContent (or raises)."""

    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls = 0

    async def chat_json(self, *, system_prompt, user_prompt, schema, **kwargs):
        self.calls += 1
        self.last_user_prompt = user_prompt
        if self.error is not None:
            raise self.error
        return self.response

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


def _scaffold(slug="bm/committee", locale="bm", of="committee") -> PageScaffold:
    return PageScaffold(
        page_type="team",
        slug=slug,
        title="Committee (Bahasa Malaysia)",
        sections=["hero", "team"],
        locale=locale,
        translation_of=of,
    )


def _translated_response(**overrides) -> TranslatedPageContent:
    """What a well-behaved model returns: same shape, Malay prose."""
    hero = HeroBlock(
        kind="hero",
        headline="Temui jawatankuasa",
        subheadline="Orang di sebalik MMTA.",
        layout="background",
        image_url="https://cdn.example.my/hero-committee.jpg",
        image_ref=3,
        primary_cta_label="Sertai kami",
        primary_cta_href="/membership",
    )
    team = TeamBlock(
        kind="team",
        heading="Jawatankuasa kami",
        members=[
            TeamMember(
                name="Sandra Cheah",
                role="Presiden",
                bio="Sandra mengetuai persatuan.",
                photo_url="https://cdn.example.my/sandra.jpg",
            )
        ],
    )
    return TranslatedPageContent(
        title=overrides.get("title", "Jawatankuasa"),
        seo_title="Jawatankuasa | MMTA",
        seo_description="Jawatankuasa MMTA.",
        blocks=overrides.get("blocks", [hero, team]),
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
        llm = _FakeLlm(_translated_response())
        [page] = await self._build(llm)

        self.assertEqual(page.slug, "bm/committee")
        self.assertEqual(page.locale, "bm")
        self.assertEqual(page.translation_of, "committee")
        self.assertEqual(page.blocks[0].headline, "Temui jawatankuasa")
        self.assertEqual(page.blocks[1].members[0].role, "Presiden")

    async def test_the_counterpart_is_left_untouched(self):
        canonical = _canonical()
        llm = _FakeLlm(_translated_response())
        await self._build(llm, pages=[canonical])

        self.assertEqual(canonical.blocks[0].headline, "Meet the committee")
        self.assertEqual(canonical.slug, "committee")

    async def test_photos_and_layout_survive_a_model_that_rewrites_them(self):
        # The prompt says don't touch these; this makes it true.
        rogue_hero = HeroBlock(
            kind="hero",
            headline="Temui jawatankuasa",
            layout="split",                                   # changed
            image_url="https://cdn.example.my/WRONG.jpg",      # changed
            image_ref=99,                                      # changed
        )
        rogue_team = TeamBlock(
            kind="team",
            heading="Jawatankuasa kami",
            members=[
                TeamMember(
                    name="Sandra C.",                          # mangled name
                    role="Presiden",
                    photo_url="https://cdn.example.my/WRONG-face.jpg",
                )
            ],
        )
        llm = _FakeLlm(_translated_response(blocks=[rogue_hero, rogue_team]))
        [page] = await self._build(llm)

        hero, team = page.blocks
        self.assertEqual(hero.image_url, "https://cdn.example.my/hero-committee.jpg")
        self.assertEqual(hero.image_ref, 3)
        self.assertEqual(hero.layout, "background")
        self.assertEqual(team.members[0].name, "Sandra Cheah")
        self.assertEqual(
            team.members[0].photo_url, "https://cdn.example.my/sandra.jpg"
        )
        # ...while the prose still came through.
        self.assertEqual(hero.headline, "Temui jawatankuasa")
        self.assertEqual(team.members[0].role, "Presiden")

    async def test_a_blank_translation_keeps_the_original_string(self):
        blank_hero = HeroBlock(kind="hero", headline="   ", subheadline="")
        llm = _FakeLlm(
            _translated_response(blocks=[blank_hero, _translated_response().blocks[1]])
        )
        [page] = await self._build(llm)

        self.assertEqual(page.blocks[0].headline, "Meet the committee")

    async def test_wrong_block_count_falls_back_to_the_source_language(self):
        llm = _FakeLlm(_translated_response(blocks=[_translated_response().blocks[0]]))
        [page] = await self._build(llm)

        # The clone still ships — in English rather than half a page.
        self.assertEqual(len(page.blocks), 2)
        self.assertEqual(page.blocks[0].headline, "Meet the committee")

    async def test_llm_failure_still_ships_the_page(self):
        llm = _FakeLlm(error=LlmError("model exploded"))
        [page] = await self._build(llm)

        self.assertEqual(page.slug, "bm/committee")
        self.assertEqual(page.blocks[0].headline, "Meet the committee")

    async def test_internal_links_follow_the_reader_into_their_language(self):
        pages = [_canonical(), _canonical().model_copy(update={"slug": "membership"})]
        scaffolds = [
            _scaffold(),
            _scaffold(slug="bm/membership", of="membership"),
        ]
        llm = _FakeLlm(_translated_response())
        built = await self._build(llm, scaffolds=scaffolds, pages=pages)

        committee = next(p for p in built if p.slug == "bm/committee")
        # /membership was translated too, so the Malay CTA stays in Malay.
        self.assertEqual(committee.blocks[0].primary_cta_href, "/bm/membership")

    async def test_links_to_untranslated_pages_are_left_alone(self):
        llm = _FakeLlm(_translated_response())
        [page] = await self._build(llm)

        # Only /bm/committee exists; /membership has no Malay version, so
        # pointing at /bm/membership would be a 404.
        self.assertEqual(page.blocks[0].primary_cta_href, "/membership")

    async def test_a_translation_without_its_counterpart_is_skipped(self):
        llm = _FakeLlm(_translated_response())
        built = await self._build(
            llm, scaffolds=[_scaffold(slug="bm/gallery", of="gallery")]
        )

        self.assertEqual(built, [])
        self.assertEqual(llm.calls, 0)

    async def test_the_owners_own_wording_is_given_to_the_model(self):
        llm = _FakeLlm(_translated_response())
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my/bm/committee",
            title="Jawatankuasa",
            raw_text="Jawatankuasa MMTA terdiri daripada pakar terapi muzik.",
            url_path="/bm/committee",
        )
        await self._build(llm, sources={"bm/committee": source})

        self.assertIn("Jawatankuasa MMTA terdiri daripada", llm.last_user_prompt)

    async def test_a_localized_home_is_a_page_not_the_homepage(self):
        home = _canonical().model_copy(update={"slug": "", "is_homepage": True})
        llm = _FakeLlm(_translated_response())
        [page] = await self._build(
            llm, pages=[home], scaffolds=[_scaffold(slug="bm", of="")]
        )

        self.assertFalse(page.is_homepage)
        self.assertEqual(page.slug, "bm")
        self.assertEqual(page.translation_of, "")

    async def test_nothing_to_do_makes_no_llm_call(self):
        llm = _FakeLlm(_translated_response())
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
        llm = _FakeLlm(
            TranslatedPageContent(
                title="Tentang",
                seo_title="Tentang",
                seo_description="Tentang kami.",
                blocks=[CtaBlock(kind="cta", headline="Sertai kami", cta_href="/about")],
            )
        )
        [page] = await build_translated_pages(
            [canonical],
            [PageScaffold(
                page_type="landing",
                slug="bm/about",
                title="About (Bahasa Malaysia)",
                sections=["cta"],
                locale="bm",
                translation_of="about",
            )],
            {},
            client=llm,
        )

        self.assertEqual(page.blocks[0].headline, "Sertai kami")
        self.assertEqual(page.blocks[0].cta_href, "/bm/about")
