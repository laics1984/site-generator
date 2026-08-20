"""Directory-page roster fidelity (routers/generate.py).

A scraped profile directory ("find a music therapist") must ship its FULL
page-scoped roster as a team grid — not the LLM's partial retelling, and
never as FAQ items manufactured from the profile names.
"""

import unittest

from app.models.content_blocks import (
    AboutBlock,
    FaqBlock,
    FaqItem,
    HeroBlock,
    ImageMetadata,
    PagePlan,
    ProfileBlock,
    ProfileCandidate,
    SitePlan,
    SourceContent,
    TeamBlock,
    TeamMember,
)
from app.routers.generate import (
    _directory_roster_members,
    _enrich_plan_profile_photos,
    _ensure_scraped_team_blocks,
    _profile_page_block,
    _prune_dead_profile_links,
    _roster_members,
    _strip_profile_faq_items,
    _url_path_names_person,
)
from app.services.image_refs import bind_image_refs
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


class PageScopedRosterTest(unittest.TestCase):
    """A page's own cards outrank the site-wide pool.

    ``_profile_pool_for`` flattens every crawled page, so an /about page that
    asks for a team section used to be filled with whatever roster the crawl
    found anywhere. On LumiBright that was product tiles scraped off
    /SECA-TRAC; but even when every card is a real person, one department's
    roster is not another page's team.
    """

    def test_a_page_with_its_own_roster_is_not_filled_from_elsewhere(self):
        own = _profiles(3, last="Rahman", tag="own")
        other = _profiles(9, last="Tanaka", tag="other")
        about_source = _source(own, url_path="/about-us")
        entry = _source([], discovered=[about_source, _source(other, url_path="/staff")])
        page = _page("about-us", [], page_type="about")
        plan = SitePlan(site_name="T", pages=[page])

        _ensure_scraped_team_blocks(
            plan,
            entry,
            team_section_slugs={"about-us"},
            source_map={"about-us": about_source},
        )

        block = next(b for b in page.blocks if getattr(b, "kind", None) == "team")
        self.assertEqual(
            [m.name for m in block.members], [p.name for p in own]
        )

    def test_a_page_with_no_cards_of_its_own_still_gets_the_site_roster(self):
        """The fallback the pool exists for — don't trade one bug for another."""
        staff = _profiles(4, last="Tanaka", tag="staff")
        entry = _source([], discovered=[_source(staff, url_path="/staff")])
        page = _page("about-us", [], page_type="about")
        plan = SitePlan(site_name="T", pages=[page])

        _ensure_scraped_team_blocks(
            plan,
            entry,
            team_section_slugs={"about-us"},
            source_map={"about-us": _source([], url_path="/about-us")},
        )

        block = next(b for b in page.blocks if getattr(b, "kind", None) == "team")
        self.assertEqual([m.name for m in block.members], [p.name for p in staff])


class ProfileDetailPageTest(unittest.TestCase):
    """A committee member's own page shows that member's portrait.

    The site-wide pool blocks a person whose name titles their own page, and
    a detail page's section rhythm has no team slot — so the portrait had
    nowhere to land and the page shipped photoless.
    """

    def _member_page_source(
        self,
        *,
        headings=("Ashley Jinivon",),
        profiles=None,
        url_path="/committee/ashley",
        subject_name=None,
    ):
        return SourceContent(
            source_kind="url",
            source_ref=f"https://x{url_path}",
            title="About MMTA",  # the template title all nine members share
            raw_text="Ashley chairs the committee.",
            headings=list(headings),
            url_path=url_path,
            subject_name=subject_name,
            profile_candidates=list(
                profiles
                if profiles is not None
                else [
                    ProfileCandidate(
                        name="Ashley Jinivon",
                        role="Chairperson",
                        bio="Chairs the committee",
                        photo_url="https://x/ashley.jpg",
                        photo_alt="Ashley Jinivon",
                        confidence=0.9,
                    )
                ]
            ),
        )

    def _roster_page(self):
        """The committee grid that links to the member pages."""
        return [_source(
            [
                ProfileCandidate(
                    name="Ashley Jinivon", role="Chairperson",
                    photo_url="https://x/grid/ashley.jpg", confidence=0.9,
                ),
                ProfileCandidate(
                    name="Marcus Ong", role="Treasurer",
                    photo_url="https://x/grid/marcus.jpg", confidence=0.9,
                ),
            ],
            url_path="/committee",
        )]

    def _run(self, page_source, *, discovered=None):
        slug = (page_source.url_path or "").strip("/")
        page = _page(
            slug,
            [HeroBlock(headline="Ashley Jinivon"), AboutBlock(body="Chairs …")],
            page_type="landing",
        )
        plan = SitePlan(site_name="T", pages=[page])
        _ensure_scraped_team_blocks(
            plan,
            _source(
                [],
                discovered=[
                    page_source,
                    *(self._roster_page() if discovered is None else discovered),
                ],
            ),
            team_section_slugs=set(),
            source_map={slug: page_source},
            directory_slugs=set(),
        )
        return page

    def test_single_profile_card_renders_under_the_hero(self):
        page = self._run(self._member_page_source())

        self.assertEqual([b.kind for b in page.blocks], ["hero", "profile", "about"])
        profile = page.blocks[1]
        self.assertEqual(profile.photo_url, "https://x/ashley.jpg")
        self.assertEqual(profile.role, "Chairperson")
        # The block owns the story now — a profile page has no about section.
        self.assertEqual(profile.bio, "Chairs the committee")

    def test_profile_page_carries_email_and_social_from_the_source(self):
        # The roster grid (_roster_page) supplies the structural pairing; the
        # member page's OWN card carries the contact details that should
        # surface on the profile block.
        page_source = self._member_page_source(
            profiles=[
                ProfileCandidate(
                    name="Ashley Jinivon",
                    role="Chairperson",
                    bio="Chairs the committee",
                    photo_url="https://x/ashley.jpg",
                    photo_alt="Ashley Jinivon",
                    email="ashley@example.my",
                    social_links=[("LinkedIn", "https://linkedin.com/in/ashleyj")],
                    confidence=0.9,
                )
            ]
        )

        page = self._run(page_source)

        profile = page.blocks[1]
        self.assertEqual(
            [(c.label, c.href) for c in profile.contacts],
            [
                ("ashley@example.my", "mailto:ashley@example.my"),
                ("LinkedIn", "https://linkedin.com/in/ashleyj"),
            ],
        )

    def test_card_the_page_is_not_named_after_is_ignored(self):
        # An inline author/contact card on an ordinary page names someone the
        # page isn't about — no profile block. Neither the headings nor the URL
        # claims the page is about her.
        page_source = self._member_page_source(
            headings=("Our Services",), url_path="/services"
        )

        page = self._run(page_source)

        self.assertEqual([b.kind for b in page.blocks], ["hero", "about"])

    def test_template_titled_page_is_named_by_its_url(self):
        # The real MMTA shape: every committee page is <title>About MMTA</title>
        # under an <h1>The Committee</h1>, and the card names its person in a
        # plain div, not a heading. /profile/ashley is the only part of the page
        # that says whose page it is.
        page_source = self._member_page_source(
            headings=("The Committee",), url_path="/profile/ashley"
        )

        page = self._run(page_source)

        self.assertEqual([b.kind for b in page.blocks], ["hero", "profile", "about"])
        self.assertEqual(page.blocks[1].photo_url, "https://x/ashley.jpg")

    def test_template_titled_page_is_named_by_its_body(self):
        # Nothing above the body says who this is: template title, banner
        # heading, opaque URL. The name element the page leads with does.
        page_source = self._member_page_source(
            headings=("The Committee",),
            url_path="/member/4417",
            subject_name="Ashley Jinivon",
        )

        page = self._run(page_source)

        self.assertEqual([b.kind for b in page.blocks], ["hero", "profile", "about"])
        self.assertEqual(page.blocks[1].photo_url, "https://x/ashley.jpg")

    def test_body_leading_someone_else_is_not_this_person(self):
        page_source = self._member_page_source(
            headings=("The Committee",),
            url_path="/member/4417",
            subject_name="Sandra Cheah",
        )

        self.assertIsNone(
            _profile_page_block(page_source, None, {"ashley jinivon"})
        )

    def test_unrelated_url_does_not_name_the_person(self):
        # Neither of the three readings claims her: template title, banner
        # heading, opaque URL, and no name leading the body.
        page_source = self._member_page_source(
            headings=("The Committee",), url_path="/about-us"
        )

        self.assertIsNone(
            _profile_page_block(page_source, None, {"ashley jinivon"})
        )

    def test_two_cards_are_not_a_profile_page(self):
        page_source = self._member_page_source(
            headings=("Ashley Jinivon",), profiles=_profiles(2)
        )

        self.assertIsNone(
            _profile_page_block(page_source, None, {"ashley jinivon"})
        )

    def test_photoless_card_is_ignored(self):
        page_source = self._member_page_source(
            profiles=[ProfileCandidate(name="Ashley Jinivon", role="Chairperson")]
        )

        self.assertIsNone(
            _profile_page_block(page_source, None, {"ashley jinivon"})
        )

    def test_person_no_roster_lists_is_ignored(self):
        # "Annual General Meeting" reads as a name and the page has one photo —
        # only the roster tells a member page from an ordinary content page.
        page_source = self._member_page_source()

        self.assertIsNone(_profile_page_block(page_source, None, set()))

    def test_roster_page_itself_supplies_the_names(self):
        page = self._run(self._member_page_source(), discovered=[])

        # No roster crawled → nothing structural to confirm the pairing.
        self.assertEqual([b.kind for b in page.blocks], ["hero", "about"])

    def test_portrait_is_not_bound_twice_on_the_page(self):
        page_source = self._member_page_source()
        page_source.image_metadata = [
            ImageMetadata(url="https://x/ashley.jpg", alt="Ashley", width=600, height=600)
        ]
        page = self._run(page_source)
        # The page's only photo is the portrait; the LLM bound it to the about
        # section too, which would render the same face twice.
        page.blocks[2].image_ref = 0

        bind_image_refs([page], {"committee/ashley": page_source})

        self.assertIsNone(page.blocks[2].image_url)
        self.assertIsNone(page.blocks[2].image_ref)

    def test_contacts_are_backfilled_even_when_the_llm_already_has_a_photo(self):
        # The common case in practice: _enrich_plan_profile_photos (which runs
        # right before _ensure_scraped_team_blocks) has already matched the
        # LLM's own profile block to its portrait, so only contacts are still
        # missing. The refill must not skip just because the photo is already
        # there.
        page_source = self._member_page_source(
            profiles=[
                ProfileCandidate(
                    name="Ashley Jinivon",
                    role="Chairperson",
                    bio="Chairs the committee",
                    photo_url="https://x/ashley.jpg",
                    photo_alt="Ashley Jinivon",
                    email="ashley@example.my",
                    social_links=[("LinkedIn", "https://linkedin.com/in/ashleyj")],
                    confidence=0.9,
                )
            ]
        )
        page = _page(
            "committee/ashley",
            [
                HeroBlock(headline="Ashley Jinivon"),
                ProfileBlock(
                    name="Ashley Jinivon",
                    role="Chairperson",
                    photo_url="https://x/ashley.jpg",
                ),
            ],
            page_type="landing",
        )
        plan = SitePlan(site_name="T", pages=[page])

        _ensure_scraped_team_blocks(
            plan,
            _source([], discovered=[page_source, *self._roster_page()]),
            team_section_slugs=set(),
            source_map={"committee/ashley": page_source},
            directory_slugs=set(),
        )

        profile = next(b for b in page.blocks if b.kind == "profile")
        self.assertEqual(profile.photo_url, "https://x/ashley.jpg")
        self.assertEqual(
            [(c.label, c.href) for c in profile.contacts],
            [
                ("ashley@example.my", "mailto:ashley@example.my"),
                ("LinkedIn", "https://linkedin.com/in/ashleyj"),
            ],
        )


class ProfileUrlNamingTest(unittest.TestCase):
    """Every slug shape MMTA's nine committee pages are written in."""

    def test_slug_shapes_that_name_their_person(self):
        cases = [
            ("/profile/ashley", "Ashley Jinivon"),  # one given name
            ("/profile/ivy", "Ivy Tan"),  # the shortest one
            ("/profile/kevinleong", "Kevin Leong"),  # run together
            ("/profile/kueksersheentse", "Kuek Ser Sheen Tse"),
            ("/profile/kevin-leong", "Kevin Leong"),  # hyphenated
            # "Low" is spelled "Loh" in the slug — the rest of the name still
            # identifies the page.
            ("/profile/lohmingyuan", "Low Ming Yuan"),
        ]
        for url_path, name in cases:
            with self.subTest(url_path=url_path):
                self.assertTrue(_url_path_names_person(url_path, name))

    def test_slugs_that_name_someone_else_or_no_one(self):
        cases = [
            ("/about-us", "Ashley Jinivon"),
            ("/profile/sandra", "Ashley Jinivon"),
            ("/services", "Ashley Jinivon"),
            # A two-letter family name is too short to carry a page on its own.
            ("/ng", "Nathan Ng"),
            (None, "Ashley Jinivon"),
            ("/profile/ashley", ""),
        ]
        for url_path, name in cases:
            with self.subTest(url_path=url_path, name=name):
                self.assertFalse(_url_path_names_person(url_path, name))


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
            [
                ProfileCandidate(
                    name="Dr. Sandra Cheah",
                    photo_url="https://x/s.jpg",
                    confidence=0.8,  # a structurally extracted card
                )
            ]
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

    def test_page_subject_candidate_never_strips_a_question(self):
        # A page headed "Annual General Meeting" reads as name-shaped, so the
        # positional fallback offers it as a person — it must not delete the
        # site's genuine question about the AGM.
        entry = _source(
            [
                ProfileCandidate(
                    name="Annual General Meeting",
                    photo_url="https://x/agm.jpg",
                    confidence=0.75,
                )
            ]
        )
        faq = FaqBlock(
            items=[FaqItem(question="When is the Annual General Meeting?", answer="May.")]
        )
        page = _page("faqs", [faq], page_type="faq")
        plan = SitePlan(site_name="T", pages=[page])

        _strip_profile_faq_items(plan, entry)

        self.assertEqual(len(page.blocks[0].items), 1)

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


class RosterMemberCleaningTest(unittest.TestCase):
    """The roster paths replace the team block AFTER align_page_to_scaffold, so
    they never pass through _sanitize_team_block and must clean role/bio
    themselves."""

    def test_cta_role_and_chrome_bio_lines_are_dropped(self):
        page = SourceContent(
            source_kind="url",
            source_ref="https://x/team",
            raw_text="team",
            profile_candidates=[
                ProfileCandidate(
                    name="Aisha Rahman",
                    role="Read More",
                    bio=(
                        "Palliative care\n"
                        "Call us on +60 4-226 1234\n"
                        "Book an appointment"
                    ),
                    photo_url="https://x/a.jpg",
                    confidence=0.9,
                )
            ],
        )

        members = _directory_roster_members(page)

        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].role, "")
        self.assertEqual(members[0].bio, "Palliative care")

    def test_short_factual_bio_lines_survive(self):
        members = _directory_roster_members(
            SourceContent(
                source_kind="url",
                source_ref="https://x/team",
                raw_text="team",
                profile_candidates=_profiles(2),
            )
        )

        self.assertEqual(
            [m.bio for m in members],
            ["Children with special needs\nHome visits"] * 2,
        )
        self.assertEqual([m.role for m in members], ["Music Therapist"] * 2)

    def test_bio_naming_another_roster_member_is_dropped(self):
        page = SourceContent(
            source_kind="url",
            source_ref="https://x/team",
            raw_text="team",
            profile_candidates=[
                ProfileCandidate(
                    name="Aisha Rahman",
                    role="Therapist",
                    bio="Palliative care\nSee also Marcus Ong",
                    photo_url="https://x/a.jpg",
                    confidence=0.9,
                ),
                ProfileCandidate(
                    name="Marcus Ong",
                    role="Treasurer",
                    bio="Keeps the accounts",
                    photo_url="https://x/m.jpg",
                    confidence=0.9,
                ),
            ],
        )

        members = _directory_roster_members(page)

        self.assertEqual(members[0].bio, "Palliative care")
        self.assertEqual(members[1].bio, "Keeps the accounts")


if __name__ == "__main__":
    unittest.main()


class RosterCardLinksTest(unittest.TestCase):
    """A roster card points at the person's own page, where the source did."""

    def _roster_source(self, *, links=True, count=3):
        profiles = _profiles(count)
        for i, profile in enumerate(profiles):
            profile.profile_url = f"https://x/profile/m{i}" if links else None
        return _source(profiles, url_path="/committee")

    def _run(self, page_source, *, plan_slugs=("committee", "profile/m0", "profile/m1", "profile/m2")):
        pages = [
            _page(slug, [HeroBlock(headline=slug)], page_type="landing")
            for slug in plan_slugs
        ]
        plan = SitePlan(site_name="T", pages=pages)
        _ensure_scraped_team_blocks(
            plan,
            _source([], discovered=[page_source]),
            team_section_slugs={"committee"},
            source_map={"committee": page_source},
            directory_slugs=set(),
        )
        _prune_dead_profile_links(plan)
        block = next(
            b for p in plan.pages for b in p.blocks if getattr(b, "kind", None) == "team"
        )
        return block

    def test_members_link_to_their_own_pages(self):
        block = self._run(self._roster_source())

        self.assertEqual(
            [m.profile_href for m in block.members],
            ["/profile/m0", "/profile/m1", "/profile/m2"],
        )

    def test_a_roster_whose_cards_link_nowhere_leaves_plain_cards(self):
        block = self._run(self._roster_source(links=False))

        self.assertEqual([m.profile_href for m in block.members], [None, None, None])

    def test_links_to_pages_the_site_does_not_have_are_dropped(self):
        # The user deselected two of the three member pages — a card pointing
        # at one would be a 404 in the middle of the grid.
        block = self._run(
            self._roster_source(), plan_slugs=("committee", "profile/m1")
        )

        self.assertEqual(
            [m.profile_href for m in block.members], [None, "/profile/m1", None]
        )

    def test_a_lone_card_never_links(self):
        # A person's own page carries one card, and its link points BACK to the
        # roster — that is not this person's page.
        member = _roster_members(
            [
                ProfileCandidate(
                    name="Ashley Jinivon",
                    photo_url="https://x/a.jpg",
                    profile_url="https://x/committee",
                    confidence=0.9,
                )
            ]
        )[0]

        self.assertIsNone(member.profile_href)


class PortraitReuseAcrossPagesTest(unittest.TestCase):
    """A person's portrait belongs on their roster card AND on their own page.

    The "already used" guard stops a grid showing one face twice. Applied across
    the whole plan it did the opposite: the roster claimed every portrait and
    each member's own page, rendered later, fell back to a monogram.
    """

    def _plan_and_source(self):
        roster = _profiles(3)
        for i, profile in enumerate(roster):
            profile.profile_url = f"https://x/profile/m{i}"
        roster_source = _source(roster, url_path="/committee")
        pages = [
            _page(
                "committee",
                [TeamBlock(heading="The Committee",
                           members=[TeamMember(name=p.name, role="Member") for p in roster])],
            ),
            *(
                _page(
                    f"profile/m{i}",
                    [HeroBlock(headline=p.name), ProfileBlock(name=p.name, role="Member")],
                    page_type="landing",
                )
                for i, p in enumerate(roster)
            ),
        ]
        return SitePlan(site_name="T", pages=pages), _source([], discovered=[roster_source])

    def test_roster_and_member_pages_both_get_the_portrait(self):
        plan, entry = self._plan_and_source()

        _enrich_plan_profile_photos(plan, entry)

        team = next(b for p in plan.pages for b in p.blocks if getattr(b, "kind", None) == "team")
        profiles = [b for p in plan.pages for b in p.blocks if getattr(b, "kind", None) == "profile"]

        self.assertTrue(all(m.photo_url for m in team.members))
        self.assertTrue(all(b.photo_url for b in profiles))
        # And each page shows the RIGHT person.
        self.assertEqual(
            [b.photo_url for b in profiles], [m.photo_url for m in team.members]
        )

    def test_one_face_is_still_never_repeated_within_a_page(self):
        # The rule the guard actually exists for: two members of one grid must
        # not resolve to the same portrait.
        roster = _profiles(2)
        roster[1].photo_url = roster[0].photo_url  # the source reuses one photo
        page = _page(
            "team",
            [TeamBlock(heading="Team",
                       members=[TeamMember(name=p.name, role="r") for p in roster])],
        )
        plan = SitePlan(site_name="T", pages=[page])

        _enrich_plan_profile_photos(plan, _source(roster))

        urls = [m.photo_url for m in page.blocks[0].members if m.photo_url]
        self.assertEqual(len(urls), len(set(urls)))
