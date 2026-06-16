"""Page-local image preference.

The source places each image on the page it appears under; the resolver should
rank a page's OWN images ahead of the site-wide pool so a page's hero/section
uses its own photo, not the biggest image found anywhere on the site. It still
falls through to the pool when the page has nothing that fits the slot.
"""

import unittest

from app.models.content_blocks import ImageMetadata, SourceContent
from app.routers.generate import _page_images_by_slug
from app.services.media import ImageResolver


class UnconfiguredPexels:
    """No stock photos — isolates the scraped-pool ranking under test."""

    configured = False

    async def search_many(self, *args, **kwargs):
        return []


def _meta(url, w, h, intent="hero"):
    return ImageMetadata(url=url, alt="", intent=intent, width=w, height=h)


def _resolver():
    # Two hero-eligible images; services.jpg is larger (a higher size-bonus band:
    # >=800px vs 400-800px), so size-based intent pinning prefers it absent any
    # page-local preference.
    pool = [
        _meta("https://x/home.jpg", 700, 500),
        _meta("https://x/services.jpg", 1600, 1200),
    ]
    return ImageResolver(
        scraped_metadata=pool, pexels=UnconfiguredPexels(), use_llm_tiebreaker=False
    )


class PageLocalPreferenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_without_preference_picks_largest(self):
        photo = await _resolver().resolve(None, intent="hero")
        self.assertEqual(photo.url, "https://x/services.jpg")

    async def test_prefers_page_local_image_over_larger_other_page(self):
        photo = await _resolver().resolve(
            None, intent="hero", prefer=[_meta("https://x/home.jpg", 800, 600)]
        )
        self.assertEqual(photo.url, "https://x/home.jpg")

    async def test_falls_back_to_pool_when_page_has_no_fit(self):
        # The page's "own" image isn't in the pool / isn't a hero candidate, so
        # resolution falls through to the site-wide pool rather than returning none.
        photo = await _resolver().resolve(
            None, intent="hero", prefer=[_meta("https://x/missing.jpg", 100, 100)]
        )
        self.assertEqual(photo.url, "https://x/services.jpg")


class PageImagesBySlugTest(unittest.TestCase):
    def test_keys_home_empty_and_pages_by_slug(self):
        home = SourceContent(
            source_kind="docx",
            source_ref="doc",
            title="Acme",
            raw_text="",
            image_metadata=[_meta("https://x/home.jpg", 800, 600)],
            discovered_pages=[
                SourceContent(
                    source_kind="docx",
                    source_ref="doc",
                    title="Services",
                    raw_text="",
                    url_path="/services",
                    image_metadata=[_meta("https://x/services.jpg", 1600, 1200)],
                ),
                SourceContent(
                    source_kind="docx",
                    source_ref="doc",
                    title="Empty",
                    raw_text="",
                    url_path="/empty",
                ),
            ],
        )

        mapping = _page_images_by_slug(home)

        self.assertEqual([m.url for m in mapping[""]], ["https://x/home.jpg"])
        self.assertEqual([m.url for m in mapping["services"]], ["https://x/services.jpg"])
        # A page with no images contributes no key (resolution stays pool-wide).
        self.assertNotIn("empty", mapping)


if __name__ == "__main__":
    unittest.main()
