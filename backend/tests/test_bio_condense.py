"""Fitting a staff biography onto a team card, in the source's own sentences.

`truncate_bio` is a bound, not an edit: it keeps whatever comes first, so a bio
opening with where someone grew up loses the qualification that made them worth
introducing. Choosing WHICH sentences a card keeps is editorial judgement, and
that is what the model is asked for.

**It answers in sentence numbers, never text.** Everything these tests pin
follows from that one contract:

  * a condensed bio is a SUBSEQUENCE of the original, so every word on the card
    is verbatim source text and passes every grounding gate untouched;
  * a reply that reorders sentences is put back in source order — reordering
    would be rewriting the bio with the source's words, which is the one thing
    indices exist to prevent;
  * every failure keeps the full bio, because a shortened card is an
    improvement and a missing one is a regression;
  * a `profile` block — the page that is ABOUT this person — is never touched.
"""

import asyncio
import unittest

from app.config import settings
from app.models.content_blocks import (
    PagePlan,
    ProfileBlock,
    SitePlan,
    TeamBlock,
    TeamMember,
)
from app.routers.generate import _condense_team_card_bios
from app.services.bio_condense import BioCondensePlan, CondensedBio, condense_bios
from app.services.llm import LlmError

# Four sentences, two source lines — the shape watr.org.my's bios actually have.
BIO = (
    "Sum Keong practised as an obstetrician and gynaecologist. "
    "He later became a gynaecologic oncologist.\n"
    "He enjoys road cycling and travelling. "
    "He is married with four children."
)


class _FakeLLM:
    def __init__(self, plan: BioCondensePlan | None = None, error: Exception | None = None):
        self.plan = plan
        self.error = error
        self.prompts: list[str] = []

    async def chat_json(self, *, user_prompt, schema, **_):
        self.prompts.append(user_prompt)
        if self.error is not None:
            raise self.error
        return self.plan


def _plan(*blocks) -> SitePlan:
    return SitePlan(
        site_name="X",
        industry_category="professional_services",
        pages=[
            PagePlan(
                page_type="team", slug="team", title="Team",
                blocks=list(blocks), seo_title="s", seo_description="s",
            )
        ],
    )


def _team(bio: str = BIO, name: str = "Sum Keong Wong") -> TeamBlock:
    return TeamBlock(
        heading="Our Team",
        members=[TeamMember(name=name, role="Pastor", bio=bio)],
    )


class _CondenseCase(unittest.TestCase):
    def setUp(self):
        self.original = settings.team_bio_condense_enabled
        self.original_target = settings.team_bio_card_max_chars
        settings.team_bio_condense_enabled = True
        settings.team_bio_card_max_chars = 100

    def tearDown(self):
        settings.team_bio_condense_enabled = self.original
        settings.team_bio_card_max_chars = self.original_target

    def run_pass(self, plan, llm):
        asyncio.run(_condense_team_card_bios(plan, llm=llm))
        return plan


class ContractTest(_CondenseCase):
    def test_the_model_sees_numbered_sentences_per_person(self):
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0])]))
        self.run_pass(_plan(_team()), llm)

        prompt = llm.prompts[0]
        self.assertIn("Person 0: Sum Keong Wong", prompt)
        self.assertIn("Sum Keong practised as an obstetrician and gynaecologist.", prompt)
        self.assertIn("He is married with four children.", prompt)
        # Each sentence carries its length: the budget is arithmetic, and a
        # model asked to hit a character target without the numbers returned
        # 1110 against a 400-char one.
        self.assertIn("[0] (57 chars)", prompt)
        self.assertIn("[3] (33 chars)", prompt)

    def test_the_card_keeps_only_the_sentences_the_model_chose(self):
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0, 1])]))

        plan = self.run_pass(_plan(_team()), llm)

        member = plan.pages[0].blocks[0].members[0]
        self.assertEqual(
            member.bio,
            "Sum Keong practised as an obstetrician and gynaecologist. "
            "He later became a gynaecologic oncologist.",
        )
        # The deprecated alias is read as `bio or description`; leaving it
        # behind would resurrect the full text on the card.
        self.assertEqual(member.description, member.bio)

    def test_every_kept_sentence_is_verbatim_source_text(self):
        # The property the whole design rests on: a condensed bio is a
        # subsequence, so it passes clean_team_bio's verbatim haystack with no
        # exemption anywhere downstream.
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0, 3])]))

        plan = self.run_pass(_plan(_team()), llm)

        bio = plan.pages[0].blocks[0].members[0].bio
        for sentence in bio.replace("\n", " ").split(". "):
            self.assertIn(sentence.rstrip(". "), BIO)

    def test_a_reordered_reply_is_put_back_in_source_order(self):
        # Both indices are on the SAME source line, which is the case only
        # `_apply`'s sort can fix — `join_bio_segments` orders lines, not the
        # sentences within one, so a cross-line pair would pass either way.
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[1, 0])]))

        plan = self.run_pass(_plan(_team()), llm)

        self.assertEqual(
            plan.pages[0].blocks[0].members[0].bio,
            "Sum Keong practised as an obstetrician and gynaecologist. "
            "He later became a gynaecologic oncologist.",
        )

    def test_a_duplicated_index_is_not_printed_twice(self):
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0, 0, 1])]))

        plan = self.run_pass(_plan(_team()), llm)

        bio = plan.pages[0].blocks[0].members[0].bio
        self.assertEqual(bio.count("Sum Keong practised"), 1)

    def test_the_source_line_structure_survives(self):
        # Bios render `white-space: pre-line`, and a directory card's separate
        # facts are separate lines — so what comes apart has to go back the
        # same shape.
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0, 2])]))

        plan = self.run_pass(_plan(_team()), llm)

        self.assertEqual(
            plan.pages[0].blocks[0].members[0].bio,
            "Sum Keong practised as an obstetrician and gynaecologist.\n"
            "He enjoys road cycling and travelling.",
        )


class FallbackTest(_CondenseCase):
    def _assert_untouched(self, plan):
        self.assertEqual(plan.pages[0].blocks[0].members[0].bio, BIO)

    def test_an_llm_error_keeps_the_full_bio(self):
        self._assert_untouched(self.run_pass(_plan(_team()), _FakeLLM(error=LlmError("down"))))

    def test_out_of_range_indices_keep_the_full_bio(self):
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[9, 12])]))
        self._assert_untouched(self.run_pass(_plan(_team()), llm))

    def test_an_empty_selection_keeps_the_full_bio(self):
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[])]))
        self._assert_untouched(self.run_pass(_plan(_team()), llm))

    def test_a_reply_that_keeps_everything_is_still_brought_within_budget(self):
        # The model declining to choose must not make the pass a no-op — that
        # is exactly how it silently under-delivered before the budget moved
        # into code.
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0, 1, 2, 3])]))

        plan = self.run_pass(_plan(_team()), llm)

        bio = plan.pages[0].blocks[0].members[0].bio
        self.assertLessEqual(len(bio), settings.team_bio_card_max_chars)
        self.assertTrue(bio.startswith("Sum Keong practised"))

    def test_the_first_kept_sentence_is_never_trimmed_away(self):
        # A card that says nothing about the person has failed at its one job,
        # so the budget stops at one sentence however long that sentence is.
        # WHICH sentence that is stays the model's call.
        settings.team_bio_card_max_chars = 5
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0, 1, 2])]))

        plan = self.run_pass(_plan(_team()), llm)

        self.assertEqual(
            plan.pages[0].blocks[0].members[0].bio,
            "Sum Keong practised as an obstetrician and gynaecologist.",
        )

    def test_an_index_naming_nobody_is_ignored(self):
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=7, keep=[0])]))
        self._assert_untouched(self.run_pass(_plan(_team()), llm))

    def test_the_flag_off_makes_no_call_at_all(self):
        settings.team_bio_condense_enabled = False
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0])]))

        self._assert_untouched(self.run_pass(_plan(_team()), llm))
        self.assertEqual(llm.prompts, [])


class ScopeTest(_CondenseCase):
    def test_a_bio_already_short_enough_never_reaches_the_model(self):
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0])]))
        short = "She counsels families across the region."

        plan = self.run_pass(_plan(_team(bio=short)), llm)

        self.assertEqual(plan.pages[0].blocks[0].members[0].bio, short)
        self.assertEqual(llm.prompts, [])

    def test_a_profile_block_keeps_the_complete_biography(self):
        # A grid introduces people; a profile page tells the story. The person's
        # own page is the one surface that must never be shortened.
        llm = _FakeLLM(BioCondensePlan(bios=[CondensedBio(member=0, keep=[0])]))
        profile = ProfileBlock(name="Sum Keong Wong", role="Pastor", bio=BIO)

        plan = self.run_pass(_plan(profile), llm)

        self.assertEqual(plan.pages[0].blocks[0].bio, BIO)
        self.assertEqual(llm.prompts, [])

    def test_one_call_covers_a_whole_block(self):
        llm = _FakeLLM(BioCondensePlan(bios=[
            CondensedBio(member=0, keep=[0]),
            CondensedBio(member=1, keep=[1]),
        ]))
        block = TeamBlock(heading="Team", members=[
            TeamMember(name="Sum Keong Wong", role="Pastor", bio=BIO),
            TeamMember(name="Joel Vergis", role="Pastor", bio=BIO),
        ])

        plan = self.run_pass(_plan(block), llm)

        self.assertEqual(len(llm.prompts), 1)
        members = plan.pages[0].blocks[0].members
        self.assertTrue(members[0].bio.startswith("Sum Keong practised"))
        self.assertEqual(members[1].bio, "He later became a gynaecologic oncologist.")


class ModuleTest(unittest.TestCase):
    def test_no_client_configured_is_a_fallback_not_an_error(self):
        result = asyncio.run(condense_bios([("A", BIO)], target_chars=100, client=None))
        self.assertEqual(result, {})

    def test_no_entries_makes_no_call(self):
        llm = _FakeLLM(BioCondensePlan())
        self.assertEqual(asyncio.run(condense_bios([], target_chars=100, client=llm)), {})
        self.assertEqual(llm.prompts, [])
