import unittest

from app.models.content_blocks import PagePlan, TeamBlock, TeamMember
from app.models.industry import PageScaffold
from app.services.scaffold_enforcement import align_page_to_scaffold


class ScaffoldEnforcementTest(unittest.TestCase):
    def test_team_block_with_only_listing_items_is_omitted(self):
        page = PagePlan(
            page_type="team",
            slug="team",
            title="Team",
            blocks=[
                TeamBlock(
                    heading="Meet the team",
                    members=[
                        TeamMember(
                            name="Management",
                            role="Leadership group",
                            bio="An overview of management responsibilities.",
                        ),
                        TeamMember(
                            name="Web Design",
                            role="Service category",
                            bio="Website design and development services.",
                        ),
                    ],
                )
            ],
            seo_title="Team - Example",
            seo_description="Meet the team.",
        )
        scaffold = PageScaffold(
            page_type="team",
            slug="team",
            title="Team",
            sections=["hero", "team", "cta"],
        )

        aligned = align_page_to_scaffold(page, scaffold, brand_name="Example")

        self.assertEqual([block.kind for block in aligned.blocks], ["hero", "cta"])

    def test_team_block_keeps_person_members_and_drops_listing_items(self):
        page = PagePlan(
            page_type="team",
            slug="team",
            title="Team",
            blocks=[
                TeamBlock(
                    heading="Meet the team",
                    members=[
                        TeamMember(
                            name="Dr Aisha Rahman",
                            role="Chairperson",
                            bio="Guides clinical governance.",
                        ),
                        TeamMember(
                            name="Marketing Team",
                            role="Department",
                            bio="Marketing and communication responsibilities.",
                        ),
                    ],
                )
            ],
            seo_title="Team - Example",
            seo_description="Meet the team.",
        )
        scaffold = PageScaffold(
            page_type="team",
            slug="team",
            title="Team",
            sections=["team"],
        )

        aligned = align_page_to_scaffold(page, scaffold, brand_name="Example")

        self.assertEqual(len(aligned.blocks), 1)
        block = aligned.blocks[0]
        self.assertIsInstance(block, TeamBlock)
        self.assertEqual([member.name for member in block.members], ["Dr Aisha Rahman"])


if __name__ == "__main__":
    unittest.main()
