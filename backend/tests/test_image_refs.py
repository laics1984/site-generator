"""image_ref pipeline: the planner prompt lists each page's real scraped
photos (source_router.promptable_images), the LLM binds sections to them via
`image_ref`, and services/image_refs resolves those refs back to URLs after
alignment. These tests cover the filter, the prompt payload, the healing
validator, the binding pass, and the resolver's pinned-URL short-circuit."""

import asyncio
import unittest

from app.models.content_blocks import (
    AboutBlock,
    GalleryBlock,
    GalleryItem,
    HeroBlock,
    ImageMetadata,
    PagePlan,
    SourceContent,
)
from app.services.image_refs import bind_image_refs
from app.services.media import ImageResolver
from app.services.source_router import (
    MAX_PROMPT_IMAGES,
    excerpt_for_prompt,
    promptable_images,
)


def _meta(url: str, **kw) -> ImageMetadata:
    return ImageMetadata(url=url, **kw)


def _source(images: list[ImageMetadata], slug: str = "school-life") -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref=f"https://example.my/{slug}",
        raw_text="Section one.\nSection two.",
        url_path=f"/{slug}",
        image_metadata=images,
    )


class PromptableImagesTest(unittest.TestCase):
    def test_logos_and_decorations_are_excluded(self):
        images = [
            _meta("https://x/logo.png", role="logo"),
            _meta("https://x/deco.png", role="decoration"),
            _meta("https://x/brand.png", intent="logo"),
            _meta("https://x/photo.jpg", role="content", alt="Kids painting"),
        ]
        kept = promptable_images(_source(images))
        self.assertEqual([m.url for m in kept], ["https://x/photo.jpg"])

    def test_tiny_images_are_excluded(self):
        images = [
            _meta("https://x/icon.jpg", width=64, height=64),
            _meta("https://x/photo.jpg", width=1200, height=800),
        ]
        kept = promptable_images(_source(images))
        self.assertEqual([m.url for m in kept], ["https://x/photo.jpg"])

    def test_capped_and_deterministic(self):
        images = [_meta(f"https://x/p{i}.jpg") for i in range(20)]
        kept = promptable_images(_source(images))
        self.assertEqual(len(kept), MAX_PROMPT_IMAGES)
        # Same input → same list, same order (the binding pass recomputes it).
        again = promptable_images(_source(images))
        self.assertEqual([m.url for m in kept], [m.url for m in again])

    def test_excerpt_carries_ref_alt_role_and_near(self):
        images = [
            _meta(
                "https://x/photo.jpg",
                alt="Children at the science centre",
                role="content",
                context_heading="Science Centre",
            )
        ]
        payload = excerpt_for_prompt(_source(images))
        self.assertEqual(
            payload["images"],
            [
                {
                    "ref": 0,
                    "alt": "Children at the science centre",
                    "role": "content",
                    "near": "Science Centre",
                }
            ],
        )

    def test_excerpt_omits_images_key_when_none_qualify(self):
        payload = excerpt_for_prompt(_source([_meta("https://x/logo.png", role="logo")]))
        self.assertNotIn("images", payload)


class ImageRefHealingTest(unittest.TestCase):
    def test_junk_refs_heal_to_none(self):
        for junk in ("nope", -3, 2.5, True, {"ref": 1}):
            block = HeroBlock(headline="H", image_ref=junk)
            self.assertIsNone(block.image_ref, f"image_ref={junk!r} should heal to None")

    def test_valid_refs_survive_including_numeric_strings(self):
        self.assertEqual(HeroBlock(headline="H", image_ref=3).image_ref, 3)
        self.assertEqual(HeroBlock(headline="H", image_ref="2").image_ref, 2)


class BindImageRefsTest(unittest.TestCase):
    def _pages(self, blocks) -> list[PagePlan]:
        return [
            PagePlan(
                page_type="landing",
                slug="school-life",
                title="School Life",
                description="",
                is_homepage=False,
                blocks=blocks,
                seo_title="t",
                seo_description="d",
            )
        ]

    def test_valid_ref_binds_url_and_alt(self):
        images = [_meta("https://x/p0.jpg", alt="Circle time")]
        pages = self._pages([AboutBlock(heading="18 months", body="…", image_ref=0)])
        bound = bind_image_refs(pages, {"school-life": _source(images)})

        block = pages[0].blocks[0]
        self.assertEqual(block.image_url, "https://x/p0.jpg")
        self.assertEqual(block.image_alt, "Circle time")
        self.assertEqual(bound, {"https://x/p0.jpg"})

    def test_out_of_range_ref_is_dropped(self):
        images = [_meta("https://x/p0.jpg")]
        pages = self._pages([AboutBlock(heading="A", body="", image_ref=7)])
        bound = bind_image_refs(pages, {"school-life": _source(images)})

        block = pages[0].blocks[0]
        self.assertIsNone(block.image_ref)
        self.assertIsNone(block.image_url)
        self.assertEqual(bound, set())

    def test_duplicate_ref_binds_only_first_use(self):
        images = [_meta("https://x/p0.jpg")]
        pages = self._pages(
            [
                HeroBlock(headline="H", image_ref=0),
                AboutBlock(heading="A", body="", image_ref=0),
            ]
        )
        bind_image_refs(pages, {"school-life": _source(images)})

        hero, about = pages[0].blocks
        self.assertEqual(hero.image_url, "https://x/p0.jpg")
        self.assertIsNone(about.image_url)  # falls back to its image_query

    def test_item_level_refs_bind_for_gallery(self):
        images = [_meta("https://x/p0.jpg"), _meta("https://x/p1.jpg")]
        pages = self._pages(
            [
                GalleryBlock(
                    heading="Gallery",
                    items=[
                        GalleryItem(image_query="kids", image_ref=1),
                        GalleryItem(image_query="art"),
                    ],
                )
            ]
        )
        bound = bind_image_refs(pages, {"school-life": _source(images)})
        items = pages[0].blocks[0].items
        self.assertEqual(items[0].image_url, "https://x/p1.jpg")
        self.assertIsNone(items[1].image_url)
        self.assertEqual(bound, {"https://x/p1.jpg"})

    def test_ref_resolves_against_the_filtered_list_the_prompt_showed(self):
        # A logo sits FIRST in image_metadata but is filtered from the prompt
        # list, so ref 0 must resolve to the first PROMPTABLE image.
        images = [
            _meta("https://x/logo.png", role="logo"),
            _meta("https://x/photo.jpg", alt="Sports day"),
        ]
        pages = self._pages([AboutBlock(heading="A", body="", image_ref=0)])
        bind_image_refs(pages, {"school-life": _source(images)})
        self.assertEqual(pages[0].blocks[0].image_url, "https://x/photo.jpg")


class PinnedResolveTest(unittest.TestCase):
    def test_pinned_url_wins_and_reports_scraped_source(self):
        meta = _meta("https://x/p0.jpg", alt="Circle time")
        resolver = ImageResolver(scraped_metadata=[meta])
        photo = asyncio.run(
            resolver.resolve("anything", intent="about", pinned_url="https://x/p0.jpg")
        )
        self.assertEqual(photo.url, "https://x/p0.jpg")
        self.assertEqual(photo.alt, "Circle time")
        self.assertEqual(photo.source, "scraped")

    def test_marked_used_urls_are_skipped_by_free_ranking_but_still_pinnable(self):
        meta = _meta(
            "https://x/p0.jpg", alt="kindergarten classroom", role="content"
        )
        resolver = ImageResolver(scraped_metadata=[meta])
        resolver.mark_used({"https://x/p0.jpg"})

        free = asyncio.run(resolver.resolve("kindergarten classroom", intent="about"))
        self.assertNotEqual(free.url, "https://x/p0.jpg")

        pinned = asyncio.run(
            resolver.resolve(None, intent="about", pinned_url="https://x/p0.jpg")
        )
        self.assertEqual(pinned.url, "https://x/p0.jpg")


if __name__ == "__main__":
    unittest.main()
