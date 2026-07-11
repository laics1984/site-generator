import unittest
from datetime import datetime, timezone

from app.models.content_blocks import SourceContent
from app.services.content_collections import (
    ArticleEntry,
    EventEntry,
    _candidate_urls,
    _dedupe_slugs,
    _extract_entry,
    _is_detail_path,
    _listing_pages,
)


def _jsonld_article_html() -> str:
    return """
    <html><head>
      <title>My Post | Acme Co</title>
      <script type="application/ld+json">
      {"@context": "https://schema.org", "@type": "BlogPosting",
       "headline": "Grand Opening Recap",
       "datePublished": "2026-03-15T10:00:00+08:00",
       "description": "We opened our second branch.",
       "image": {"@type": "ImageObject", "url": "https://acme.my/img/opening.jpg"}}
      </script>
    </head><body>
      <nav><a href="/">Home</a></nav>
      <article>
        <h1>Grand Opening Recap</h1>
        <p>We opened our second branch in March with over two hundred guests
        attending the ribbon cutting ceremony downtown.</p>
        <script>trackPageView()</script>
        <p>Thanks to <strong>everyone</strong> who came.</p>
      </article>
      <footer>Copyright</footer>
    </body></html>
    """


def _meta_only_article_html() -> str:
    return """
    <html><head>
      <title>Care Tips for Winter — Acme Co</title>
      <meta property="og:title" content="Care Tips for Winter" />
      <meta property="og:description" content="Five ways to prepare." />
      <meta property="og:image" content="https://acme.my/img/winter.jpg" />
      <meta property="article:published_time" content="2026-01-02T00:00:00Z" />
    </head><body>
      <main>
        <h1>Care Tips for Winter</h1>
        <p>Winter is coming and your garden needs attention before the frost
        arrives. Here are the five steps we recommend to every customer.</p>
      </main>
    </body></html>
    """


def _jsonld_event_html() -> str:
    return """
    <html><head>
      <script type="application/ld+json">
      [{"@context": "https://schema.org", "@type": "Event",
        "name": "Annual Charity Dinner",
        "startDate": "2026-09-12T19:00:00+08:00",
        "endDate": "2026-09-12T23:00:00+08:00",
        "description": "Our biggest fundraiser of the year.",
        "location": {"@type": "Place", "name": "Grand Ballroom",
                     "address": {"streetAddress": "1 Jalan Ampang",
                                 "addressLocality": "Kuala Lumpur"}},
        "image": ["https://acme.my/img/dinner.jpg"]}]
      </script>
    </head><body>
      <article>
        <h1>Annual Charity Dinner</h1>
        <p>Join us for an evening of food and fundraising in support of the
        local community shelter programme we run every year.</p>
      </article>
    </body></html>
    """


class ExtractEntryTest(unittest.TestCase):
    def test_jsonld_article_extraction(self):
        entry = _extract_entry(
            _jsonld_article_html(), "https://acme.my/blog/grand-opening-recap", "blog"
        )
        self.assertIsInstance(entry, ArticleEntry)
        self.assertEqual(entry.title, "Grand Opening Recap")
        self.assertEqual(entry.slug, "grand-opening-recap")
        self.assertEqual(entry.excerpt, "We opened our second branch.")
        self.assertEqual(entry.image_url, "https://acme.my/img/opening.jpg")
        # 2026-03-15T10:00+08:00 → 02:00 UTC
        self.assertEqual(
            entry.published_at, datetime(2026, 3, 15, 2, 0, tzinfo=timezone.utc)
        )
        self.assertIn("<p>", entry.body_html)
        self.assertIn("<strong>everyone</strong>", entry.body_html)
        self.assertNotIn("<script", entry.body_html)
        self.assertNotIn("trackPageView", entry.body_html)
        self.assertNotIn("<nav", entry.body_html)

    def test_meta_only_article_extraction(self):
        entry = _extract_entry(
            _meta_only_article_html(), "https://acme.my/blog/care-tips", "blog"
        )
        self.assertIsInstance(entry, ArticleEntry)
        self.assertEqual(entry.title, "Care Tips for Winter")
        self.assertEqual(entry.excerpt, "Five ways to prepare.")
        self.assertEqual(entry.image_url, "https://acme.my/img/winter.jpg")
        self.assertEqual(
            entry.published_at, datetime(2026, 1, 2, tzinfo=timezone.utc)
        )

    def test_jsonld_event_extraction(self):
        entry = _extract_entry(
            _jsonld_event_html(), "https://acme.my/events/annual-dinner", "events"
        )
        self.assertIsInstance(entry, EventEntry)
        self.assertEqual(entry.title, "Annual Charity Dinner")
        self.assertEqual(
            entry.start, datetime(2026, 9, 12, 11, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(
            entry.end, datetime(2026, 9, 12, 15, 0, tzinfo=timezone.utc)
        )
        self.assertEqual(
            entry.location, "Grand Ballroom, 1 Jalan Ampang, Kuala Lumpur"
        )
        self.assertEqual(entry.image_url, "https://acme.my/img/dinner.jpg")

    def test_event_jsonld_overrides_blog_listing_classification(self):
        entry = _extract_entry(
            _jsonld_event_html(), "https://acme.my/news/annual-dinner", "blog"
        )
        self.assertIsInstance(entry, EventEntry)

    def test_navigation_stub_is_rejected(self):
        html = "<html><body><main><p>Read more</p></main></body></html>"
        entry = _extract_entry(html, "https://acme.my/blog/stub", "blog")
        self.assertIsNone(entry)

    def test_relative_body_links_and_images_become_absolute(self):
        html = """
        <html><body><article>
          <h1>With Assets</h1>
          <p>This paragraph is long enough to pass the minimum body length
          check for a real detail page on the source website.</p>
          <p><a href="/about">about</a> <img src="/img/pic.jpg" alt="pic"/></p>
        </article></body></html>
        """
        entry = _extract_entry(html, "https://acme.my/blog/with-assets", "blog")
        self.assertIn('href="https://acme.my/about"', entry.body_html)
        self.assertIn('src="https://acme.my/img/pic.jpg"', entry.body_html)


class CandidateUrlTest(unittest.TestCase):
    def test_detail_paths_under_listing(self):
        self.assertTrue(_is_detail_path("/blog/my-post", "/blog"))
        self.assertTrue(_is_detail_path("/blog/2026/03/my-post", "/blog"))
        self.assertFalse(_is_detail_path("/blog", "/blog"))
        self.assertFalse(_is_detail_path("/blog/page/2", "/blog"))
        self.assertFalse(_is_detail_path("/blog/category/tips", "/blog"))
        self.assertFalse(_is_detail_path("/blog/tag/frost", "/blog"))
        self.assertFalse(_is_detail_path("/about", "/blog"))

    def test_dated_permalinks_off_root_are_accepted(self):
        self.assertTrue(_is_detail_path("/2026/03/my-post", "/blog"))

    def test_candidate_urls_filters_and_dedupes(self):
        listing = SourceContent(
            source_kind="url",
            source_ref="https://acme.my/blog",
            raw_text="Blog",
            url_path="/blog",
            links=[
                "https://acme.my/blog/post-one",
                "https://acme.my/blog/post-one#comments",
                "https://acme.my/blog/page/2",
                "https://acme.my/about",
                "https://other.site/blog/external-post",
                "https://acme.my/blog/post-two",
            ],
        )
        source = SourceContent(
            source_kind="url",
            source_ref="https://acme.my",
            raw_text="Home",
            discovered_pages=[listing],
        )
        urls = _candidate_urls(listing, source, [])
        self.assertEqual(
            urls,
            ["https://acme.my/blog/post-one", "https://acme.my/blog/post-two"],
        )

    def test_listing_pages_classify_blog_and_events(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://acme.my",
            raw_text="Home",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://acme.my/news",
                    title="News",
                    raw_text="News list",
                    url_path="/news",
                ),
                SourceContent(
                    source_kind="url",
                    source_ref="https://acme.my/events",
                    title="Events",
                    raw_text="Events list",
                    url_path="/events",
                ),
                SourceContent(
                    source_kind="url",
                    source_ref="https://acme.my/about",
                    title="About",
                    raw_text="About us",
                    url_path="/about",
                ),
            ],
        )
        listings = _listing_pages(source)
        self.assertEqual(len(listings["blog"]), 1)
        self.assertEqual(len(listings["events"]), 1)


class DedupeSlugTest(unittest.TestCase):
    def test_duplicate_slugs_get_suffixes(self):
        entries = [
            ArticleEntry(
                title="A", slug="post", excerpt="x", body_html="<p>x</p>",
                source_url="https://a/1",
            ),
            ArticleEntry(
                title="B", slug="post", excerpt="x", body_html="<p>x</p>",
                source_url="https://a/2",
            ),
        ]
        _dedupe_slugs(entries)
        self.assertEqual([e.slug for e in entries], ["post", "post-2"])


if __name__ == "__main__":
    unittest.main()
