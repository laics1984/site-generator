"""Stock-images-only generation: no source photo reaches the tree.

The mechanism is a supply cut, not a filter — `without_source_imagery` empties
the fields every scraped photo travels on, once, as the source enters
generation, and every downstream consumer inherits it. These tests pin both
halves: what the cut removes (and, just as importantly, what it must NOT), and
that a whole site built from a cut source carries no source URL anywhere.
"""

from __future__ import annotations

import unittest

from app.models.brand import BrandIdentity
from app.models.content_blocks import (
    CtaBlock,
    DocumentCardCandidate,
    DocumentCardLink,
    GalleryBlock,
    GalleryItem,
    HeroBlock,
    ImageMetadata,
    MapEmbed,
    PagePlan,
    ProfileCandidate,
    SectionCandidate,
    SitePlan,
    SourceCard,
    SourceContent,
    TeamBlock,
    TeamMember,
    VideoEmbed,
)
from app.services.source_images import without_source_imagery

_SRC_HOST = "https://source.example"


def _metadata(name: str, **kw) -> ImageMetadata:
    return ImageMetadata(
        url=f"{_SRC_HOST}/{name}.jpg",
        alt=name.replace("-", " "),
        intent=kw.pop("intent", "generic"),
        width=kw.pop("width", 1600),
        height=kw.pop("height", 1000),
        **kw,
    )


def _source(**kw) -> SourceContent:
    """A photo-rich crawl: entry page + one sub-page, every image channel used."""
    sub = SourceContent(
        source_kind="url",
        source_ref=f"{_SRC_HOST}/about",
        url_path="/about",
        raw_text="About our cafe.",
        images=[f"{_SRC_HOST}/team-shot.jpg"],
        image_metadata=[_metadata("team-shot")],
    )
    return SourceContent(
        source_kind="url",
        source_ref=f"{_SRC_HOST}/",
        raw_text="Our cafe serves brunch daily.",
        images=[f"{_SRC_HOST}/interior.jpg", f"{_SRC_HOST}/latte.jpg"],
        image_metadata=[_metadata("interior", intent="hero"), _metadata("latte")],
        discovered_pages=[sub],
        section_candidates=[
            SectionCandidate(
                heading="Our space",
                level=2,
                card_kind="gallery",
                image_urls=[f"{_SRC_HOST}/wall-1.jpg", f"{_SRC_HOST}/wall-2.jpg"],
                cards=[
                    SourceCard(title="Corner table", image_url=f"{_SRC_HOST}/wall-1.jpg"),
                    SourceCard(title="Counter", image_url=f"{_SRC_HOST}/wall-2.jpg"),
                ],
            )
        ],
        profile_candidates=[
            ProfileCandidate(
                name="Jane Smith",
                role="Head Barista",
                photo_url=f"{_SRC_HOST}/jane.jpg",
            ),
            ProfileCandidate(
                name="Amir Lee",
                role="Roaster",
                photo_url=f"{_SRC_HOST}/amir.jpg",
            ),
        ],
        document_cards=[
            DocumentCardCandidate(
                title="Menu 2026",
                image_url=f"{_SRC_HOST}/menu-thumb.jpg",
                links=[DocumentCardLink(label="PDF", href=f"{_SRC_HOST}/menu.pdf")],
            )
        ],
        video_embeds=[
            VideoEmbed(
                provider="youtube",
                video_id="abc123",
                embed_url="https://www.youtube.com/embed/abc123",
            )
        ],
        map_embeds=[
            MapEmbed(provider="google", embed_url="https://maps.google.com/?output=embed")
        ],
        **kw,
    )


class WithoutSourceImageryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.original = _source()
        self.stripped = without_source_imagery(self.original)

    def test_clears_every_photo_channel_on_every_page(self) -> None:
        for page in (self.stripped, *self.stripped.discovered_pages):
            self.assertEqual(page.images, [])
            self.assertEqual(page.image_metadata, [])

    def test_clears_section_candidate_photography(self) -> None:
        """The channel _inject_image_walls reads FIRST — image_metadata is its fallback."""
        for section in self.stripped.section_candidates:
            self.assertEqual(section.image_urls, [])
            self.assertTrue(all(card.image_url is None for card in section.cards))
            # The section itself survives: headings and card titles are content.
            self.assertEqual(section.heading, "Our space")
            self.assertEqual([c.title for c in section.cards], ["Corner table", "Counter"])

    def test_keeps_profile_photos(self) -> None:
        """Load-bearing, not an oversight.

        Both roster passes skip a candidate with no photo outright, so cutting
        the portrait here would delete the roster and let _drop_hollow_team_pages
        take the whole team page. Portraits go one layer later, on the plan.
        """
        self.assertEqual(
            [p.photo_url for p in self.stripped.profile_candidates],
            [f"{_SRC_HOST}/jane.jpg", f"{_SRC_HOST}/amir.jpg"],
        )

    def test_keeps_artifact_imagery_and_embeds(self) -> None:
        """Scope: content imagery only. These depict one specific thing each."""
        self.assertEqual(
            self.stripped.document_cards[0].image_url, f"{_SRC_HOST}/menu-thumb.jpg"
        )
        self.assertEqual(len(self.stripped.video_embeds), 1)
        self.assertEqual(len(self.stripped.map_embeds), 1)

    def test_keeps_all_text(self) -> None:
        self.assertEqual(self.stripped.raw_text, self.original.raw_text)
        self.assertEqual(self.stripped.title, self.original.title)

    def test_original_is_not_mutated(self) -> None:
        """model_copy is shallow — every nested model that changes must be rebuilt."""
        self.assertEqual(len(self.original.images), 2)
        self.assertEqual(len(self.original.image_metadata), 2)
        self.assertEqual(len(self.original.discovered_pages[0].image_metadata), 1)
        self.assertEqual(
            self.original.section_candidates[0].image_urls,
            [f"{_SRC_HOST}/wall-1.jpg", f"{_SRC_HOST}/wall-2.jpg"],
        )
        self.assertEqual(
            self.original.section_candidates[0].cards[0].image_url,
            f"{_SRC_HOST}/wall-1.jpg",
        )


class NothingReachesThePromptTest(unittest.TestCase):
    def test_no_promptable_images(self) -> None:
        """No images in the prompt ⇒ the model cannot emit an image_ref at all."""
        from app.services.source_router import promptable_images

        stripped = without_source_imagery(_source())
        self.assertEqual(promptable_images(stripped), [])

    def test_image_pool_is_empty(self) -> None:
        from app.routers.generate import _image_pool_for, _page_images_by_slug

        stripped = without_source_imagery(_source())
        self.assertEqual(_image_pool_for(stripped), ([], []))
        self.assertEqual(_page_images_by_slug(stripped), {})

    def test_no_photo_walls_are_injected(self) -> None:
        """Guards the channel image_metadata alone does not close."""
        from app.routers.generate import _inject_image_walls

        stripped = without_source_imagery(_source())
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[HeroBlock(headline="Hi", image_query="cafe")],
            seo_title="Home",
            seo_description="Home",
        )
        _inject_image_walls([page], stripped)

        self.assertFalse(any(isinstance(b, GalleryBlock) for b in page.blocks))


class LocaleSurvivesTheCutTest(unittest.TestCase):
    def test_market_cue_reads_the_source_as_received(self) -> None:
        """The one bug the supply cut invites.

        _market_cues_for feeds image URLs to detect_market as domain evidence,
        and the cues steer every Pexels query. Under stock-only they are the ONLY
        thing keeping imagery on-market, so the handlers compute them BEFORE the
        strip. Cutting first would silently weaken exactly the mode that needs
        them most.
        """
        from app.routers.generate import _market_cues_for

        # The CDN carries the ccTLD; the site's own domain does not. Note the
        # digit before ".my": locale._bounded is `(?<![a-z])\.my(?![a-z])`, so a
        # letter immediately before the dot defeats it and most real domains
        # never match. That is a pre-existing quirk of locale detection and not
        # this mode's business — the point here is only that when image URLs DO
        # carry the signal, the strip must not be what removes it.
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.com/",
            raw_text="Our kopitiam serves brunch daily.",
            images=["https://cdn24.my/interior.jpg"],
            image_metadata=[
                ImageMetadata(url="https://cdn24.my/interior.jpg", alt="", intent="hero")
            ],
        )

        before = _market_cues_for(source)
        stripped = without_source_imagery(source)
        after = _market_cues_for(stripped)

        self.assertEqual(before, ("Southeast Asian", "Malaysia"))
        # The strip really does destroy the evidence...
        self.assertEqual(after, ("", ""))
        # ...which is why both handlers read the cues from the source as
        # RECEIVED. Mirror that order here: this is the assertion that fails if
        # anyone moves _market_cues_for below the strip.
        self.assertEqual(
            _market_cues_for(source),
            ("Southeast Asian", "Malaysia"),
            "the handlers must compute market cues before without_source_imagery",
        )


class PersonPhotoCutTest(unittest.TestCase):
    def _plan_with_people(self) -> SitePlan:
        team = TeamBlock(
            heading="Our team",
            members=[
                TeamMember(
                    name="Jane Smith",
                    role="Head Barista",
                    bio="Ten years behind the bar.",
                    photo_url=f"{_SRC_HOST}/jane.jpg",
                    photo_alt="Jane Smith",
                    photo_query="smiling barista portrait",
                ),
                TeamMember(name="Amir Lee", role="Roaster", photo_url=f"{_SRC_HOST}/amir.jpg"),
            ],
        )
        page = PagePlan(
            page_type="about",
            slug="about",
            title="About",
            blocks=[HeroBlock(headline="About", image_query="cafe"), team],
            seo_title="About",
            seo_description="About",
        )
        return SitePlan(site_name="Cafe", tagline="Brunch daily", pages=[page])

    def test_portraits_go_but_the_people_stay(self) -> None:
        from app.routers.generate import _drop_person_photos

        plan = self._plan_with_people()
        _drop_person_photos(plan.pages)

        team = plan.pages[0].blocks[1]
        self.assertEqual([m.name for m in team.members], ["Jane Smith", "Amir Lee"])
        self.assertEqual([m.role for m in team.members], ["Head Barista", "Roaster"])
        self.assertEqual(team.members[0].bio, "Ten years behind the bar.")
        for member in team.members:
            self.assertIsNone(member.photo_url)
            self.assertIsNone(member.photo_alt)
            # Nothing renders photo_query; only the stock prewarm reads it, and
            # warming portraits no slot can use is a wasted round trip.
            self.assertIsNone(member.photo_query)


class GalleryPolicyTest(unittest.TestCase):
    def _gallery(self) -> GalleryBlock:
        return GalleryBlock(
            heading="Our space",
            items=[
                GalleryItem(title="Corner table", image_query="cafe corner table"),
                GalleryItem(title="Counter", image_query="cafe counter espresso"),
            ],
        )

    def test_query_only_tiles_survive_under_stock_only(self) -> None:
        from app.services.scaffold_enforcement import sanitize_blocks_against_source

        kept = sanitize_blocks_against_source(
            [self._gallery()], "Our space", allow_stock_gallery=True
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(len(kept[0].items), 2)

    def test_query_only_tiles_are_still_dropped_by_default(self) -> None:
        """The honesty rule is suspended by the mode, not deleted."""
        from app.services.scaffold_enforcement import sanitize_blocks_against_source

        kept = sanitize_blocks_against_source([self._gallery()], "Our space")

        self.assertEqual(kept, [])


class NoSourceUrlSurvivesTest(unittest.IsolatedAsyncioTestCase):
    """Whole-tree assertion, at the last moment provenance exists.

    `push_orchestrator._needs_upload` is provenance-blind — by push time a photo
    is a bare `src` string — so `plan_to_site`'s return is the boundary this has
    to hold at. Walked with the orchestrator's own collector so CSS background
    layers are covered too, not just <img> nodes.
    """

    def _plan(self) -> SitePlan:
        page = PagePlan(
            page_type="home",
            slug="",
            title="Home",
            is_homepage=True,
            blocks=[
                HeroBlock(headline="Brunch daily", image_query="cafe interior"),
                TeamBlock(
                    heading="Our team",
                    members=[TeamMember(name="Jane Smith", role="Head Barista")],
                ),
                GalleryBlock(
                    heading="Our space",
                    items=[GalleryItem(title="Counter", image_query="cafe counter")],
                ),
                CtaBlock(
                    headline="Visit us",
                    body="Open 8am.",
                    cta_label="Directions",
                    cta_href="/contact",
                ),
            ],
            seo_title="Home",
            seo_description="Home",
        )
        return SitePlan(site_name="Cafe", tagline="Brunch daily", pages=[page])

    async def test_tree_carries_no_source_photo(self) -> None:
        from unittest.mock import patch

        from app.config import settings
        from app.services.push_orchestrator import _collect_image_srcs
        from app.services.schema_builder import plan_to_site

        from tests.test_media import FakePexels, _photo

        pexels = FakePexels({})
        # Any query resolves to the same stock photo — we assert provenance, not
        # relevance, so a single catch-all keeps the fixture honest and small.
        pexels.results = _AnyQuery(_photo("https://images.pexels.com/stock.jpg", "stock"))

        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            with patch(
                "app.services.media.get_pexels_client", return_value=pexels
            ):
                site = await plan_to_site(
                    self._plan(),
                    brand=BrandIdentity(name="Cafe", mood="friendly"),
                    scraped_metadata=[_metadata("interior", intent="hero")],
                    stock_only=True,
                )
        finally:
            settings.design_brain_enabled = original

        srcs: dict = {}
        for page in site.pages:
            for el in page.body_schema.elements:
                _collect_image_srcs(el, srcs)

        self.assertTrue(srcs, "the site should render at least one image")
        for src in srcs:
            self.assertNotIn(
                _SRC_HOST,
                src,
                f"a source photo survived into the tree: {src}",
            )


class _AnyQuery(dict):
    """Every lookup hits — FakePexels reads its results with .get()."""

    def __init__(self, photo):
        super().__init__()
        self._photo = photo

    def get(self, _key, _default=None):
        return [self._photo]


if __name__ == "__main__":
    unittest.main()
