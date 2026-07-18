"""Directory-page roster fidelity (routers/generate.py).

A scraped profile directory ("find a music therapist") must ship its FULL
page-scoped roster as a team grid — not the LLM's partial retelling, and
never as FAQ items manufactured from the profile names.
"""

import unittest

from app.models.content_blocks import (
    FaqBlock,
    FaqItem,
    ImageMetadata,
    PagePlan,
    ProfileCandidate,
    SitePlan,
    SourceContent,
    TeamBlock,
    TeamMember,
)
from app.routers.generate import (
    _directory_roster_members,
    _ensure_scraped_team_blocks,
    _strip_profile_faq_items,
)
from app.services.source_router import promptable_images

_FIRST_NAMES = [
    "Aisha", "Ivy", "Mei", "Sandra", "Nathan", "Grace", "Joanne", "Cheryl",
    "Doris", "Ashley", "Carmen", "Sherrene", "Alia", "Farah", "Hannah",
    "Elaine", "Priya", "Wendy", "Karen", "Lydia",
]


def _profiles(n: int, *, last: str = "Rahman", tag: str = "a") -> list[ProfileCandidate]:
    return [
        ProfileCandidate(
            name=f"{_FIRST_NAMES[i]} {last}",
            role="Music Therapist",
            bio="Children with special needs\nHome visits",
            photo_url=f"https://x/{tag}/p{i}.jpg",
            photo_alt=f"{_FIRST_NAMES[i]} {last}",
            confidence=0.9,
        )
        for i in range(n)
    ]


def _source(profiles, *, url_path=None, discovered=()) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref="https://x",
        raw_text="Directory page text.",
        url_path=url_path,
        profile_candidates=list(profiles),
        discovered_pages=list(discovered),
    )


def _page(slug: str, blocks, page_type: str = "team") -> PagePlan:
    return PagePlan(
        page_type=page_type,
        slug=slug,
        title=slug.replace("-", " ").title(),
        blocks=blocks,
        seo_title="seo",
        seo_description="seo",
    )


class DirectoryRosterFillTest(unittest.TestCase):
    def test_partial_llm_block_replaced_with_full_page_roster(self):
        roster = _profiles(17)
        page_source = _source(roster, url_path="/find-a-music-therapist")
        # The LLM kept 5 of 17 people, and the photo-enrichment pass attached
        # a photo to one of them — which used to short-circuit the refill.
        llm_members = [
            TeamMember(
                name=p.name,
                role="Therapist",
                photo_url=p.photo_url if i == 0 else None,
            )
            for i, p in enumerate(roster[:5])
        ]
        page = _page(
            "find-a-music-therapist",
            [TeamBlock(heading="Our therapists", members=llm_members)],
        )
        plan = SitePlan(site_name="T", pages=[page])
        entry = _source([], discovered=[page_source])

        _ensure_scraped_team_blocks(
            plan,
            entry,
            team_section_slugs={"find-a-music-therapist"},
            source_map={"find-a-music-therapist": page_source},
            directory_slugs={"find-a-music-therapist"},
        )

        block = next(b for b in page.blocks if getattr(b, "kind", None) == "team")
        self.assertEqual(len(block.members), 17)
        self.assertEqual(block.heading, "Our therapists")
        self.assertTrue(all(m.photo_url for m in block.members))

    def test_roster_inserted_when_llm_dropped_the_block(self):
        roster = _profiles(8)
        page_source = _source(roster, url_path="/find-a-music-therapist")
        page = _page("find-a-music-therapist", [])
        plan = SitePlan(site_name="T", pages=[page])
        entry = _source([], discovered=[page_source])

        _ensure_scraped_team_blocks(
            plan,
            entry,
            team_section_slugs={"find-a-music-therapist"},
            source_map={"find-a-music-therapist": page_source},
            directory_slugs={"find-a-music-therapist"},
        )

        block = next(b for b in page.blocks if getattr(b, "kind", None) == "team")
        self.assertEqual(len(block.members), 8)

    def test_two_directories_stay_page_scoped(self):
        therapists = _profiles(8, last="Rahman", tag="therapists")
        board = _profiles(6, last="Tanaka", tag="board")
        therapists_source = _source(therapists, url_path="/find-a-therapist")
        board_source = _source(board, url_path="/committee")
        pages = [
            _page("find-a-therapist", []),
            _page("committee", []),
        ]
        plan = SitePlan(site_name="T", pages=pages)
        entry = _source([], discovered=[therapists_source, board_source])

        _ensure_scraped_team_blocks(
            plan,
            entry,
            team_section_slugs={"find-a-therapist", "committee"},
            source_map={
                "find-a-therapist": therapists_source,
                "committee": board_source,
            },
            directory_slugs={"find-a-therapist", "committee"},
        )

        for page, tag, count in ((pages[0], "therapists", 8), (pages[1], "board", 6)):
            block = next(b for b in page.blocks if getattr(b, "kind", None) == "team")
            self.assertEqual(len(block.members), count, page.slug)
            self.assertTrue(
                all(f"/{tag}/" in m.photo_url for m in block.members), page.slug
            )

    def test_non_directory_pages_keep_legacy_behaviour(self):
        # A team block that already carries photos is left alone outside
        # directory_slugs (the wholesale replacement is directory-only).
        member = TeamMember(
            name="Aisha Rahman", role="Chair", photo_url="https://x/keep.jpg"
        )
        page = _page("about", [TeamBlock(heading="Board", members=[member])], page_type="about")
        plan = SitePlan(site_name="T", pages=[page])
        entry = _source(_profiles(8))

        _ensure_scraped_team_blocks(plan, entry, team_section_slugs=set())

        block = page.blocks[0]
        self.assertEqual(len(block.members), 1)
        self.assertEqual(block.members[0].photo_url, "https://x/keep.jpg")

    def test_team_block_accepts_24_members(self):
        members = [
            TeamMember(name=f"{_FIRST_NAMES[i % 20]} Rahman{i}", role="r")
            for i in range(24)
        ]
        block = TeamBlock(heading="Directory", members=members)
        self.assertEqual(len(block.members), 24)

    def test_directory_roster_members_dedupes_and_gates_names(self):
        profiles = [
            *_profiles(3),
            _profiles(3)[0],  # duplicate of the first person
            ProfileCandidate(name="Our Story", photo_url="https://x/story.jpg"),
            ProfileCandidate(name="Aisha Binti", role="r", photo_url=None),  # no photo
        ]
        members = _directory_roster_members(_source(profiles))
        self.assertEqual(len(members), 3)


class ProfileFaqStripTest(unittest.TestCase):
    def test_profile_questions_removed_genuine_kept(self):
        entry = _source(_profiles(6))
        faq = FaqBlock(
            items=[
                FaqItem(
                    question="Who is Ivy Rahman and what are her credentials?",
                    answer="…",
                ),
                FaqItem(question="Do therapists offer home visits?", answer="Yes."),
            ]
        )
        page = _page("faqs", [faq], page_type="faq")
        plan = SitePlan(site_name="T", pages=[page])

        _strip_profile_faq_items(plan, entry)

        block = page.blocks[0]
        self.assertEqual(len(block.items), 1)
        self.assertEqual(block.items[0].question, "Do therapists offer home visits?")

    def test_title_stripped_name_still_matches(self):
        # Scraped "Dr. Sandra Cheah" — the model drops the honorific.
        entry = _source(
            [ProfileCandidate(name="Dr. Sandra Cheah", photo_url="https://x/s.jpg")]
        )
        faq = FaqBlock(
            items=[
                FaqItem(question="Who is Sandra Cheah?", answer="…"),
                FaqItem(question="How do I book a session?", answer="Call us."),
            ]
        )
        page = _page("faqs", [faq], page_type="faq")
        plan = SitePlan(site_name="T", pages=[page])

        _strip_profile_faq_items(plan, entry)

        self.assertEqual(len(page.blocks[0].items), 1)

    def test_fully_manufactured_faq_block_is_dropped(self):
        entry = _source(_profiles(3))
        faq = FaqBlock(
            items=[
                FaqItem(question="Who is Aisha Rahman?", answer="…"),
                FaqItem(question="Where can I find Ivy Rahman?", answer="…"),
            ]
        )
        page = _page("contact", [faq], page_type="contact")
        plan = SitePlan(site_name="T", pages=[page])

        _strip_profile_faq_items(plan, entry)

        self.assertEqual(page.blocks, [])

    def test_no_profiles_means_no_stripping(self):
        entry = _source([])
        faq = FaqBlock(items=[FaqItem(question="Who is John Smith?", answer="…")])
        page = _page("faqs", [faq], page_type="faq")
        plan = SitePlan(site_name="T", pages=[page])

        _strip_profile_faq_items(plan, entry)

        self.assertEqual(len(page.blocks[0].items), 1)


class PromptableImagesPortraitTest(unittest.TestCase):
    def test_portraits_excluded_from_prompt_pool(self):
        # On a directory page 15+ headshots used to crowd the real banner out
        # of the MAX_PROMPT_IMAGES budget — and were LLM-pinnable as heroes.
        source = SourceContent(
            source_kind="url",
            source_ref="https://x",
            raw_text="t",
            image_metadata=[
                *[
                    ImageMetadata(
                        url=f"https://x/face{i}.jpg",
                        alt="therapist headshot",
                        role="portrait",
                        width=400,
                        height=400,
                    )
                    for i in range(15)
                ],
                ImageMetadata(
                    url="https://x/banner.jpg",
                    alt="hands on piano",
                    role="hero",
                    width=1920,
                    height=800,
                ),
            ],
        )

        urls = [m.url for m in promptable_images(source)]

        self.assertIn("https://x/banner.jpg", urls)
        self.assertFalse(any("face" in u for u in urls))


if __name__ == "__main__":
    unittest.main()
