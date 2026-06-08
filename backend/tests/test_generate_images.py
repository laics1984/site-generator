import unittest

import app.routers.generate as generate
from app.models.content_blocks import (
    ImageMetadata,
    PagePlan,
    ProfileCandidate,
    SitePlan,
    SourceContent,
    TeamBlock,
    TeamMember,
)
from app.routers.generate import _image_pool_for


class GenerateImagePoolTest(unittest.TestCase):
    def test_source_content_profile_candidates_are_backward_compatible(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Existing payload without profile candidates.",
        )

        self.assertEqual(source.profile_candidates, [])

    def test_image_pool_includes_discovered_page_metadata_without_duplicates(self):
        entry = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Entry page text for a Malaysian services company.",
            images=["https://example.my/hero.jpg"],
            image_metadata=[
                ImageMetadata(
                    url="https://example.my/hero.jpg",
                    alt="Homepage hero",
                    intent="hero",
                    width=1200,
                    height=800,
                )
            ],
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/services",
                    title="Services",
                    raw_text="Services page text.",
                    url_path="/services",
                    images=[
                        "https://example.my/services-team.jpg",
                        "https://example.my/hero.jpg",
                    ],
                    image_metadata=[
                        ImageMetadata(
                            url="https://example.my/services-team.jpg",
                            alt="Southeast Asian service team",
                            intent="about",
                            width=1000,
                            height=700,
                        )
                    ],
                )
            ],
        )

        images, metadata = _image_pool_for(entry)

        self.assertEqual(
            images,
            [
                "https://example.my/hero.jpg",
                "https://example.my/services-team.jpg",
            ],
        )
        self.assertEqual(
            [m.url for m in metadata],
            [
                "https://example.my/hero.jpg",
                "https://example.my/services-team.jpg",
            ],
        )
        self.assertEqual(metadata[1].alt, "Southeast Asian service team")

    def test_profile_portraits_are_enriched_by_member_name_match(self):
        enrich = getattr(generate, "_enrich_plan_profile_photos", None)
        self.assertIsNotNone(enrich)
        if enrich is None:
            return

        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Committee members include Dr Aisha Rahman, Chairperson.",
            profile_candidates=[
                ProfileCandidate(
                    name="Dr Aisha Rahman",
                    role="Chairperson",
                    bio="Guides clinical governance.",
                    photo_url="https://example.my/aisha.jpg",
                    photo_alt="Dr Aisha Rahman portrait",
                    source_url="https://example.my/about/committee",
                    confidence=0.9,
                )
            ],
        )
        plan = SitePlan(
            site_name="Example",
            pages=[
                PagePlan(
                    page_type="team",
                    slug="committee",
                    title="Committee",
                    blocks=[
                        TeamBlock(
                            heading="Committee",
                            members=[
                                TeamMember(
                                    name="Dr Aisha Rahman",
                                    role="Chairperson",
                                    bio="Guides clinical governance.",
                                    photo_query="professional portrait",
                                )
                            ],
                        )
                    ],
                    seo_title="Committee - Example",
                    seo_description="Meet the committee.",
                )
            ],
        )

        enrich(plan, source)

        member = plan.pages[0].blocks[0].members[0]
        self.assertEqual(member.photo_url, "https://example.my/aisha.jpg")
        self.assertEqual(member.photo_alt, "Dr Aisha Rahman portrait")
