import json
import unittest

from bs4 import BeautifulSoup

from app.services.entry_markup import declared_entry
from app.services.scraper import _parse_rendered_html


def _page(*, head: str = "", body_class: str = "", ld: object = None) -> str:
    script = (
        f'<script type="application/ld+json">{json.dumps(ld)}</script>' if ld is not None else ""
    )
    return (
        f"<html><head>{head}{script}</head>"
        f'<body class="{body_class}"><main><h1>Title</h1>'
        "<p>Enough body copy for the page to read as a page of its own.</p></main></body></html>"
    )


def _declared(html: str):
    return declared_entry(BeautifulSoup(html, "lxml"))


class WordPressCoreTest(unittest.TestCase):
    """Measured on mykiddyland.com: its posts state nothing but these two."""

    def test_a_post_at_the_root_is_an_article(self):
        html = _page(
            head='<meta property="og:type" content="article">',
            body_class="wp-singular post-template-default single single-post postid-92 "
            "single-format-standard",
        )
        self.assertEqual(_declared(html), "article")

    def test_a_page_is_never_an_entry_whatever_its_seo_plugin_says(self):
        # Yoast and Rank Math write og:type=article — Rank Math an Article
        # schema and a publish date too — on every page. Core's own class wins.
        html = _page(
            head='<meta property="og:type" content="article">'
            '<meta property="article:published_time" content="2024-01-02T00:00:00Z">',
            body_class="wp-singular page-template-default page page-id-18",
            ld={"@graph": [{"@type": "WebPage"}, {"@type": "Article", "headline": "About Us"}]},
        )
        self.assertIsNone(_declared(html))

    def test_a_product_is_not_a_post(self):
        # postid-N is on every singular post TYPE; only single-post is a post.
        html = _page(
            body_class="single single-product postid-511",
            ld={"@type": "Product", "name": "Tricycle"},
        )
        self.assertIsNone(_declared(html))


class StatedElsewhereTest(unittest.TestCase):
    def test_og_type_article_alone_declares_nothing(self):
        html = _page(head='<meta property="og:type" content="article">')
        self.assertIsNone(_declared(html))

    def test_one_article_in_json_ld_is_an_article(self):
        html = _page(ld={"@type": "BlogPosting", "headline": "Grand Opening Recap"})
        self.assertEqual(_declared(html), "article")

    def test_one_entry_described_twice_is_still_one_entry(self):
        html = _page(
            ld=[
                {"@type": "Article", "headline": "Grand Opening Recap"},
                {"@type": "BlogPosting", "headline": "Grand opening recap"},
            ]
        )
        self.assertEqual(_declared(html), "article")

    def test_published_time_is_an_article(self):
        html = _page(head='<meta property="article:published_time" content="2026-01-02">')
        self.assertEqual(_declared(html), "article")

    def test_one_event_is_an_event_even_on_a_post(self):
        html = _page(
            body_class="single single-post postid-7",
            ld={"@type": "Event", "name": "Open Day", "startDate": "2026-11-01"},
        )
        self.assertEqual(_declared(html), "event")

    def test_a_calendar_of_events_is_a_listing(self):
        html = _page(
            ld=[
                {"@type": "Event", "name": "Open Day"},
                {"@type": "Event", "name": "Sports Day"},
            ]
        )
        self.assertIsNone(_declared(html))

    def test_a_plain_page_declares_nothing(self):
        html = _page(
            head='<meta property="og:type" content="website">',
            ld={"@type": "WebSite", "name": "MyKiddyLand"},
        )
        self.assertIsNone(_declared(html))


class CrawlRecordsTheDeclarationTest(unittest.TestCase):
    def test_a_crawled_page_carries_its_declaration(self):
        html = _page(body_class="single single-post postid-92")
        parsed = _parse_rendered_html(
            html, "https://example.my/kindergarten-selangor/", require_text=False
        )
        self.assertEqual(parsed.source_content.declared_entry, "article")

    def test_json_ld_is_read_before_the_body_text_pass_strips_scripts(self):
        # No text blocks, so _extract_body_text falls to its last resort, which
        # decomposes every <script> on the shared soup.
        ld = json.dumps({"@type": "NewsArticle", "headline": "We moved"})
        html = f'<html><head><script type="application/ld+json">{ld}</script></head><body></body></html>'
        parsed = _parse_rendered_html(html, "https://example.my/we-moved", require_text=False)
        self.assertEqual(parsed.source_content.declared_entry, "article")


if __name__ == "__main__":
    unittest.main()
