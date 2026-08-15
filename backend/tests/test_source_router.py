"""Routing a scaffold to the crawled page(s) that should generate it.

Normally one crawled page per slug. Not always: a PHP album viewer serves every
album from ``gallery-photo.php?id=NNN``, and ``url_path`` carries no query, so
28 albums all normalize to ``gallery-photo``. Indexing first-wins dropped 27 of
them along with every photo they held — the page shipped as invented prose
because the only album that survived was whichever the crawl happened to reach
first.
"""

import unittest

from app.models.content_blocks import ImageMetadata, SourceContent
from app.models.industry import PageScaffold
from app.services.source_router import (
    match_scaffolds_to_pages,
    pages_by_source_slug,
)


def _album(album_id: int, heading: str, *, photos: int = 3) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref=f"https://example.com/gallery-photo.php?id={album_id}",
        title=heading,
        headings=[heading],
        raw_text=f"{heading}\n",
        url_path="/gallery-photo.php",
        image_metadata=[
            ImageMetadata(url=f"https://example.com/photo/{album_id}/{i}.jpg")
            for i in range(photos)
        ],
    )


def _index() -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref="https://example.com/gallery-photo.php",
        title="Photo Gallery",
        headings=["PHOTO GALLERY"],
        raw_text="PHOTO GALLERY\n",
        url_path="/gallery-photo.php",
    )


def _source(discovered) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref="https://example.com/",
        title="Home",
        raw_text="Home page text.",
        discovered_pages=list(discovered),
    )


class PagesBySourceSlugTest(unittest.TestCase):
    def test_pages_sharing_a_slug_are_all_kept(self):
        grouped = pages_by_source_slug(
            [_index(), _album(107, "Mid Year Party"), _album(106, "Teachers Day")]
        )

        self.assertEqual(len(grouped["gallery-photo"]), 3)

    def test_the_query_less_url_is_the_representative(self):
        # Otherwise /gallery-photo takes its title from whichever album was
        # crawled first — "Mid Year Party" instead of "Photo Gallery".
        grouped = pages_by_source_slug(
            [_album(107, "Mid Year Party"), _index(), _album(106, "Teachers Day")]
        )

        self.assertEqual(grouped["gallery-photo"][0].title, "Photo Gallery")

    def test_crawl_order_is_kept_among_the_records(self):
        grouped = pages_by_source_slug(
            [_index(), _album(107, "Mid Year Party"), _album(106, "Teachers Day")]
        )

        self.assertEqual(
            [p.title for p in grouped["gallery-photo"][1:]],
            ["Mid Year Party", "Teachers Day"],
        )

    def test_a_page_with_no_url_path_is_skipped(self):
        page = SourceContent(source_kind="url", source_ref="https://example.com/", raw_text="x")

        self.assertEqual(pages_by_source_slug([page]), {})


class GalleryAggregationTest(unittest.TestCase):
    SCAFFOLD = PageScaffold(
        page_type="gallery",
        slug="gallery-photo",
        title="Photo Gallery",
        sections=["hero", "gallery", "cta"],
    )

    def test_every_album_record_is_merged_into_the_gallery_scaffold(self):
        source = _source(
            [_index(), _album(107, "Mid Year Party"), _album(106, "Teachers Day")]
        )

        routed = match_scaffolds_to_pages([self.SCAFFOLD], source)["gallery-photo"]

        # The album titles ground the page's own copy...
        self.assertIn("Mid Year Party", routed.raw_text)
        self.assertIn("Teachers Day", routed.raw_text)
        self.assertIn("Teachers Day", routed.headings)
        # ...and the photos are what the page is actually for.
        self.assertEqual(len(routed.image_metadata), 6)

    def test_the_merged_page_keeps_its_own_identity(self):
        source = _source([_index(), _album(107, "Mid Year Party")])

        routed = match_scaffolds_to_pages([self.SCAFFOLD], source)["gallery-photo"]

        self.assertEqual(routed.title, "Photo Gallery")
        self.assertEqual(routed.url_path, "/gallery-photo.php")

    def test_duplicate_photos_across_records_are_merged_once(self):
        # A paginated album repeats tiles across ?gspg=1 and ?gspg=2.
        source = _source([_index(), _album(107, "Mid Year Party"), _album(107, "Mid Year Party")])

        routed = match_scaffolds_to_pages([self.SCAFFOLD], source)["gallery-photo"]

        urls = [m.url for m in routed.image_metadata]
        self.assertEqual(len(urls), len(set(urls)))

    def test_a_non_gallery_scaffold_is_not_media_merged(self):
        about = PageScaffold(
            page_type="about", slug="about", title="About", sections=["hero", "about", "cta"]
        )
        page = SourceContent(
            source_kind="url",
            source_ref="https://example.com/about",
            title="About",
            raw_text="About us.",
            url_path="/about",
            image_metadata=[ImageMetadata(url="https://example.com/a.jpg")],
        )
        source = _source([page, _album(107, "Mid Year Party")])

        routed = match_scaffolds_to_pages([about], source)["about"]

        self.assertEqual([m.url for m in routed.image_metadata], ["https://example.com/a.jpg"])


class FaqCombiningIsUnchangedTest(unittest.TestCase):
    """The FAQ merge predates this and must keep its no-media contract.

    A merged /support page's images belong to a DIFFERENT page — they are its
    own furniture, and attributing them to /faq would misplace them. An album
    record is the opposite case: it is not another page, it is the gallery
    showing one of its albums.
    """

    def test_faq_text_is_combined_but_media_is_not(self):
        faq = SourceContent(
            source_kind="url",
            source_ref="https://example.com/faq",
            title="FAQ",
            headings=["FAQ"],
            raw_text="Frequently asked questions.",
            url_path="/faq",
            image_metadata=[ImageMetadata(url="https://example.com/faq.jpg")],
        )
        support = SourceContent(
            source_kind="url",
            source_ref="https://example.com/support",
            title="Support",
            headings=["Support FAQ"],
            raw_text="How do I reset my password?",
            url_path="/support",
            image_metadata=[ImageMetadata(url="https://example.com/support.jpg")],
        )
        scaffold = PageScaffold(
            page_type="faq", slug="faq", title="FAQ", sections=["hero", "faq", "cta"]
        )

        routed = match_scaffolds_to_pages([scaffold], _source([faq, support]))["faq"]

        self.assertIn("reset my password", routed.raw_text)
        self.assertEqual([m.url for m in routed.image_metadata], ["https://example.com/faq.jpg"])


if __name__ == "__main__":
    unittest.main()
