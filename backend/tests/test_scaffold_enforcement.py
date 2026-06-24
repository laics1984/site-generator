import unittest

from app.models.content_blocks import (
    AwardItem,
    AwardsBlock,
    ClientItem,
    ClientsBlock,
    PagePlan,
    StatItem,
    StatsBlock,
    TeamBlock,
    TeamMember,
    TestimonialItem,
    TestimonialsBlock,
)
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

    def test_testimonials_block_with_only_placeholder_author_is_omitted(self):
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                TestimonialsBlock(
                    heading="What clients say",
                    items=[
                        TestimonialItem(
                            quote="Great service, would recommend.",
                            author="John Doe",
                        ),
                    ],
                )
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            sections=["hero", "testimonials", "cta"],
        )

        aligned = align_page_to_scaffold(page, scaffold, brand_name="Example")

        self.assertNotIn("testimonials", [block.kind for block in aligned.blocks])

    def test_testimonials_block_keeps_real_reviews_and_drops_placeholder_ones(self):
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                TestimonialsBlock(
                    heading="What clients say",
                    items=[
                        TestimonialItem(quote="Loved the bread.", author="Maria Lopez"),
                        TestimonialItem(quote="Generic praise.", author="Jane Doe"),
                    ],
                )
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            sections=["hero", "testimonials", "cta"],
        )

        aligned = align_page_to_scaffold(page, scaffold, brand_name="Example")

        testimonials = next(
            block for block in aligned.blocks if block.kind == "testimonials"
        )
        self.assertEqual([item.author for item in testimonials.items], ["Maria Lopez"])

    def test_testimonial_with_no_match_in_source_is_dropped_even_with_plausible_name(self):
        # "Sarah Chen" isn't a textbook placeholder — the only way to catch this
        # fabrication is checking it against the actual page source text.
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                TestimonialsBlock(
                    heading="What clients say",
                    items=[
                        TestimonialItem(
                            quote="An absolutely life-changing experience, highly recommend to everyone.",
                            author="Sarah Chen",
                        ),
                    ],
                )
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            sections=["hero", "testimonials", "cta"],
        )
        source_text = "We are a family-owned bakery serving fresh bread daily since 1998."

        aligned = align_page_to_scaffold(
            page, scaffold, brand_name="Example", source_text=source_text
        )

        self.assertNotIn("testimonials", [block.kind for block in aligned.blocks])

    def test_testimonial_quote_found_verbatim_in_source_is_kept(self):
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                TestimonialsBlock(
                    heading="What clients say",
                    items=[
                        TestimonialItem(
                            quote="Best sourdough I have ever tasted in this town.",
                            author="Priya Nair",
                        ),
                    ],
                )
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            sections=["hero", "testimonials", "cta"],
        )
        source_text = (
            'Customer review — Priya Nair: "Best sourdough I have ever tasted '
            'in this town." Posted on our Google listing.'
        )

        aligned = align_page_to_scaffold(
            page, scaffold, brand_name="Example", source_text=source_text
        )

        testimonials = next(
            block for block in aligned.blocks if block.kind == "testimonials"
        )
        self.assertEqual([item.author for item in testimonials.items], ["Priya Nair"])

    def test_testimonial_grounding_skipped_when_no_source_text_available(self):
        # No source_text passed (e.g. a scaffold with no matched scraped page) →
        # only the placeholder-name check applies, content isn't dropped just
        # because we have nothing to verify it against.
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                TestimonialsBlock(
                    heading="What clients say",
                    items=[
                        TestimonialItem(quote="Loved it.", author="Priya Nair"),
                    ],
                )
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            sections=["hero", "testimonials", "cta"],
        )

        aligned = align_page_to_scaffold(page, scaffold, brand_name="Example")

        self.assertIn("testimonials", [block.kind for block in aligned.blocks])

    def test_award_with_no_match_in_source_is_dropped(self):
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                AwardsBlock(items=[AwardItem(title="Best Bakery in the Region", issuer="Foodie Awards")]),
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home", slug="", title="Home", is_homepage=True,
            sections=["hero", "awards", "cta"],
        )
        source_text = "We bake fresh sourdough bread every morning."

        aligned = align_page_to_scaffold(
            page, scaffold, brand_name="Example", source_text=source_text
        )

        self.assertNotIn("awards", [block.kind for block in aligned.blocks])

    def test_award_found_in_source_is_kept(self):
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                AwardsBlock(items=[AwardItem(title="Best Bakery in the Region", issuer="Foodie Awards")]),
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home", slug="", title="Home", is_homepage=True,
            sections=["hero", "awards", "cta"],
        )
        source_text = "Winner: Best Bakery in the Region, Foodie Awards 2022."

        aligned = align_page_to_scaffold(
            page, scaffold, brand_name="Example", source_text=source_text
        )

        self.assertIn("awards", [block.kind for block in aligned.blocks])

    def test_client_with_no_match_in_source_is_dropped(self):
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                ClientsBlock(items=[ClientItem(name="Acme Corp"), ClientItem(name="Globex")]),
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home", slug="", title="Home", is_homepage=True,
            sections=["hero", "clients", "cta"],
        )
        source_text = "Trusted by Acme Corp and other businesses across the region."

        aligned = align_page_to_scaffold(
            page, scaffold, brand_name="Example", source_text=source_text
        )

        clients = next(block for block in aligned.blocks if block.kind == "clients")
        self.assertEqual([item.name for item in clients.items], ["Acme Corp"])

    def test_stats_with_no_match_in_source_is_dropped(self):
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                StatsBlock(items=[StatItem(value="200+", label="Projects delivered")]),
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home", slug="", title="Home", is_homepage=True,
            sections=["hero", "stats", "cta"],
        )
        source_text = "We bake fresh sourdough bread every morning."

        aligned = align_page_to_scaffold(
            page, scaffold, brand_name="Example", source_text=source_text
        )

        self.assertNotIn("stats", [block.kind for block in aligned.blocks])

    def test_stats_value_found_in_source_is_kept(self):
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                StatsBlock(items=[StatItem(value="200+", label="Projects delivered")]),
            ],
            seo_title="Home - Example",
            seo_description="Welcome.",
        )
        scaffold = PageScaffold(
            page_type="home", slug="", title="Home", is_homepage=True,
            sections=["hero", "stats", "cta"],
        )
        source_text = "We have delivered 200+ projects for happy clients since 2010."

        aligned = align_page_to_scaffold(
            page, scaffold, brand_name="Example", source_text=source_text
        )

        self.assertIn("stats", [block.kind for block in aligned.blocks])


if __name__ == "__main__":
    unittest.main()
