"""Founders on the homepage, and the size rule that keeps rosters off it.

Two halves of one decision, and both need the REAL roster — the members that
survived portrait/vision gating, with their job titles — so the decision lives
in ``generate._apply_homepage_team_policy``, not at scaffold time. The scaffold
only decides whether the homepage ASKS for a people section
(``page_inference``); the template choice follows from the block's content
(``section_content``).
"""

import unittest

from app.models.content_blocks import (
    CtaBlock,
    HeroBlock,
    PagePlan,
    ProfileCandidate,
    SitePlan,
    SourceContent,
    TeamBlock,
    TeamMember,
)
from app.routers.generate import _apply_homepage_team_policy
from app.services.page_inference import infer_page_scaffolds
from app.services.profile_text import looks_like_founder_role
from app.services.section_content import _is_founders_band, block_to_section


def _member(name: str, role: str) -> TeamMember:
    return TeamMember(name=name, role=role, photo_url=f"https://x/{name}.jpg")


def _founders(n: int = 2) -> list[TeamMember]:
    return [_member(f"Alicia Tan{i}", "Co-Founder") for i in range(n)]


def _staff(n: int) -> list[TeamMember]:
    return [_member(f"Wendy Lim{i}", "Music Therapist") for i in range(n)]


def _page(page_type: str, slug: str, blocks: list) -> PagePlan:
    return PagePlan(
        page_type=page_type,
        slug=slug,
        title=slug.replace("-", " ").title() or "Home",
        blocks=blocks,
        seo_title="seo",
        seo_description="seo",
    )


def _plan(home_blocks: list, other_blocks: list | None = None) -> SitePlan:
    pages = [_page("home", "", home_blocks)]
    if other_blocks is not None:
        pages.append(_page("team", "team", other_blocks))
    return SitePlan(site_name="Studio", pages=pages)


def _team_blocks(page: PagePlan) -> list[TeamBlock]:
    return [b for b in page.blocks if getattr(b, "kind", None) == "team"]


class FounderRoleTest(unittest.TestCase):
    def test_accepts_founder_and_owner_titles(self):
        for role in (
            "Founder",
            "Co-Founder",
            "Cofounder",
            "Founding Partner",
            "Owner",
            "Co-Owner",
            "Proprietor",
            "Managing Director",
            "Managing Partner",
        ):
            self.assertTrue(looks_like_founder_role(role), role)

    def test_rejects_staff_and_ambiguous_titles(self):
        # "Principal" is a school head in childcare, and bare Partner/Director
        # are staff titles — calling any of them a founder would put a factual
        # error in the section heading.
        for role in (
            "Principal",
            "Partner",
            "Director",
            "Director of Nursing",
            "Music Therapist",
            "Senior Consultant",
            "",
            None,
        ):
            self.assertFalse(looks_like_founder_role(role), role)


class HomepageTeamPolicyTest(unittest.TestCase):
    def test_narrows_home_to_founders_when_roster_lives_on_another_page(self):
        roster = _founders(2) + _staff(8)
        plan = _plan(
            [HeroBlock(headline="H"), TeamBlock(heading="Meet the team", members=roster)],
            other_blocks=[TeamBlock(heading="Meet the team", members=roster)],
        )

        _apply_homepage_team_policy(plan)

        home_team = _team_blocks(plan.pages[0])
        self.assertEqual(len(home_team), 1)
        self.assertEqual([m.name for m in home_team[0].members], ["Alicia Tan0", "Alicia Tan1"])
        # The generic default is retitled; the Team page keeps the full roster.
        self.assertEqual(home_team[0].heading, "Meet the founders")
        self.assertEqual(len(_team_blocks(plan.pages[1])[0].members), 10)

    def test_keeps_an_llm_written_heading_when_narrowing(self):
        roster = _founders(2) + _staff(8)
        plan = _plan(
            [TeamBlock(heading="The two of us", members=roster)],
            other_blocks=[TeamBlock(members=roster)],
        )

        _apply_homepage_team_policy(plan)

        self.assertEqual(_team_blocks(plan.pages[0])[0].heading, "The two of us")

    def test_drops_a_large_roster_already_shown_elsewhere(self):
        roster = _staff(12)
        plan = _plan(
            [HeroBlock(headline="H"), TeamBlock(members=roster), CtaBlock(headline="C")],
            other_blocks=[TeamBlock(members=roster)],
        )

        _apply_homepage_team_policy(plan)

        self.assertEqual(_team_blocks(plan.pages[0]), [])
        # Only the team band goes — the rest of the homepage is untouched.
        self.assertEqual([b.kind for b in plan.pages[0].blocks], ["hero", "cta"])
        self.assertEqual(len(_team_blocks(plan.pages[1])[0].members), 12)

    def test_keeps_a_small_roster_shown_elsewhere(self):
        # At or under the cap two pages showing the same four faces is not the
        # wall of strangers the rule exists to prevent.
        roster = _staff(4)
        plan = _plan([TeamBlock(members=roster)], other_blocks=[TeamBlock(members=roster)])

        _apply_homepage_team_policy(plan)

        self.assertEqual(len(_team_blocks(plan.pages[0])[0].members), 4)

    def test_leaves_home_alone_when_nothing_else_carries_the_roster(self):
        # The directory-entry weave: home IS the roster. Narrowing to the two
        # founders here would silently delete ten people with nowhere to go.
        roster = _founders(2) + _staff(10)
        plan = _plan([TeamBlock(members=roster)])

        _apply_homepage_team_policy(plan)

        self.assertEqual(len(_team_blocks(plan.pages[0])[0].members), 12)

    def test_collapses_duplicate_home_team_blocks(self):
        roster = _founders(2)
        plan = _plan(
            [TeamBlock(members=roster), TeamBlock(members=roster)],
            other_blocks=[TeamBlock(members=_staff(9))],
        )

        _apply_homepage_team_policy(plan)

        self.assertEqual(len(_team_blocks(plan.pages[0])), 1)


class FoundersScaffoldWeaveTest(unittest.TestCase):
    def _source(self, roles: list[str]) -> SourceContent:
        return SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="A design studio in Kuala Lumpur.",
            profile_candidates=[
                ProfileCandidate(
                    name=f"Alicia Tan{i}",
                    role=role,
                    photo_url=f"https://x/p{i}.jpg",
                    confidence=0.9,
                )
                for i, role in enumerate(roles)
            ],
        )

    def test_two_founders_put_a_people_section_on_the_homepage(self):
        scaffolds = infer_page_scaffolds(
            self._source(["Co-Founder", "Co-Founder"]), industry="agency"
        )

        home = next(s for s in scaffolds if s.is_homepage)
        self.assertIn("team", home.sections)
        # Placed before the closing CTA, not after it.
        self.assertLess(home.sections.index("team"), home.sections.index("cta"))

    def test_a_leadership_roster_does_not(self):
        # Five founder-titled people is a leadership page, not the pair who
        # started the business.
        scaffolds = infer_page_scaffolds(self._source(["Founder"] * 5), industry="agency")

        home = next(s for s in scaffolds if s.is_homepage)
        self.assertNotIn("team", home.sections)

    def test_staff_titles_do_not(self):
        scaffolds = infer_page_scaffolds(
            self._source(["Music Therapist", "Principal"]), industry="agency"
        )

        home = next(s for s in scaffolds if s.is_homepage)
        self.assertNotIn("team", home.sections)


class FoundersTemplateTest(unittest.TestCase):
    def test_all_founder_short_roster_is_a_band(self):
        self.assertTrue(_is_founders_band(TeamBlock(members=_founders(2))))

    def test_a_mixed_roster_is_not(self):
        # A slice of staff that happens to include the founder is still staff.
        self.assertFalse(_is_founders_band(TeamBlock(members=_founders(1) + _staff(2))))

    def test_a_long_founder_roster_is_not(self):
        self.assertFalse(_is_founders_band(TeamBlock(members=_founders(4))))

    def test_a_founders_band_selects_the_founders_template(self):
        template, _ = block_to_section(TeamBlock(members=_founders(2)), mood="modern")
        self.assertEqual(template["id"], "team-founders")

    def test_a_staff_roster_selects_the_grid(self):
        template, _ = block_to_section(TeamBlock(members=_staff(6)), mood="modern")
        self.assertEqual(template["id"], "team-grid")

    def test_the_founders_choice_survives_the_variety_rotation(self):
        # The rotation reorders text-only preference heads per brand; an
        # explicit content policy must not be rotatable.
        for seed in ("Studio A", "Studio B", "Studio C", "Studio D"):
            template, _ = block_to_section(
                TeamBlock(members=_founders(2)), mood="modern", variety_seed=seed
            )
            self.assertEqual(template["id"], "team-founders", seed)


if __name__ == "__main__":
    unittest.main()
