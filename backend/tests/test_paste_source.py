"""
The paste reader: markup and copy → the same parsed shape a document upload
produces, and the merge that folds a paste into a source already read.

The load-bearing claims here are (a) a paste splits into pages through the ONE
splitter documents use, so "## Contact" behaves identically whichever way it
arrived, and (b) merging joins by page topic, so pasted Contact copy lands on
the site's own Contact page instead of creating a second one.
"""

import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.models.content_blocks import ImageMetadata, NavLink, SourceContent
from app.services.doc_structure import MAX_DISCOVERED_PAGES, split_into_pages
from app.services.paste_source import (
    looks_like_html,
    merge_sources,
    read_paste,
)


def _pages(text: str, *, title: str | None = None) -> SourceContent:
    """A paste, read and split exactly as the endpoint does."""
    parsed = read_paste(text, title=title)
    return split_into_pages(
        parsed.document, images=parsed.images, description=parsed.description
    )


class MarkupDetectionTest(unittest.TestCase):
    def test_markup_is_recognised(self):
        self.assertTrue(looks_like_html("<p>Hello</p>"))
        self.assertTrue(looks_like_html('<div class="hero"><h1>Acme</h1></div>'))
        self.assertTrue(looks_like_html("<!DOCTYPE html><html><body>x</body></html>"))
        self.assertTrue(looks_like_html("Intro text<br>more text"))

    def test_prose_is_not_markup(self):
        # Comparisons and bracketed addresses must not read as tags — a paste
        # misread as markup loses its line structure entirely.
        self.assertFalse(looks_like_html("Pricing: a < b and c > d"))
        self.assertFalse(looks_like_html("Email <hello@acme.example> for a quote"))
        self.assertFalse(looks_like_html("# Acme\n\nWe roast beans."))
        self.assertFalse(looks_like_html(""))


class MarkupOutlineTest(unittest.TestCase):
    HTML = """
        <!-- internal note -->
        <script>var x = 1;</script>
        <nav><a href="/">Home</a></nav>
        <h1>Acme Coffee</h1>
        <p>We roast small batches.</p>
        <img src="https://cdn.acme.example/roastery.jpg" alt="Our roastery"
             width="1200" height="800">
        <h2>Our Services</h2>
        <ul><li>Wholesale beans</li></ul>
        <table><tr><td>Espresso</td><td>RM12</td></tr></table>
        <img src="/local/only.jpg" alt="skipped">
        <img src="https://cdn.acme.example/favicon.png" alt="icon">
        <footer>&copy; Acme</footer>
    """

    def setUp(self):
        self.parsed = read_paste(self.HTML)

    def test_chrome_and_scripts_are_dropped(self):
        text = self.parsed.document.raw_text
        self.assertNotIn("var x", text)
        self.assertNotIn("internal note", text)
        self.assertNotIn("Home", text)
        self.assertNotIn("Acme\n", text.split("Acme Coffee")[-1])  # no footer line

    def test_headings_keep_their_level(self):
        levels = {b.text: b.level for b in self.parsed.document.outline}
        self.assertEqual(levels["Acme Coffee"], 1)
        self.assertEqual(levels["Our Services"], 2)
        self.assertEqual(levels["We roast small batches."], 0)

    def test_list_and_table_text_survives(self):
        text = self.parsed.document.raw_text
        self.assertIn("Wholesale beans", text)
        self.assertIn("Espresso", text)

    def test_absolute_images_kept_with_their_alt(self):
        self.assertEqual(len(self.parsed.images), 1)
        image = self.parsed.images[0]
        self.assertEqual(image.url, "https://cdn.acme.example/roastery.jpg")
        self.assertEqual(image.alt, "Our roastery")
        self.assertEqual((image.width, image.height), (1200, 800))

    def test_relative_images_are_counted_not_invented(self):
        # A site-relative src has no origin here; emitting it would 404 on every
        # page it landed on, so it is reported instead. The favicon is dropped
        # as an icon and is not part of that count.
        self.assertEqual(self.parsed.unresolved_images, 1)

    def test_declared_base_resolves_relative_images(self):
        parsed = read_paste(
            '<base href="https://acme.example/"><p>x</p>'
            '<img src="/team.jpg" alt="Team">'
        )
        self.assertEqual(parsed.unresolved_images, 0)
        self.assertEqual(parsed.images[0].url, "https://acme.example/team.jpg")

    def test_markup_title_and_description_are_read(self):
        parsed = read_paste(
            "<html><head><title>Acme Coffee</title>"
            '<meta name="description" content="Small-batch roasters"></head>'
            "<body><p>Hello</p></body></html>"
        )
        self.assertEqual(parsed.document.title, "Acme Coffee")
        self.assertEqual(parsed.description, "Small-batch roasters")


class PlainTextOutlineTest(unittest.TestCase):
    def test_markdown_headings_set_levels(self):
        parsed = read_paste("# Acme\n\ntext\n\n## Services\n\nmore\n\n#### Detail")
        levels = {b.text: b.level for b in parsed.document.outline}
        self.assertEqual(levels["Acme"], 1)
        self.assertEqual(levels["Services"], 2)
        self.assertEqual(levels["Detail"], 3)  # h4+ collapses, as in the PDF reader

    def test_setext_underlines_are_headings_and_are_consumed(self):
        parsed = read_paste("Acme Coffee\n===========\n\nWe roast.\n\nServices\n--------\n\nBeans.")
        texts = [b.text for b in parsed.document.outline]
        self.assertNotIn("===========", texts)
        self.assertNotIn("--------", texts)
        levels = {b.text: b.level for b in parsed.document.outline}
        self.assertEqual(levels["Acme Coffee"], 1)
        self.assertEqual(levels["Services"], 2)

    def test_bare_lines_read_as_headings_when_nothing_is_marked(self):
        parsed = read_paste(
            "Acme Coffee\n\nWe roast small batches in Penang.\n\n"
            "Contact\n\nCall 012-345 6789."
        )
        levels = {b.text: b.level for b in parsed.document.outline}
        self.assertEqual(levels["Contact"], 2)
        self.assertEqual(levels["We roast small batches in Penang."], 0)

    def test_explicit_markers_suppress_the_guess(self):
        # The author already said where the headings are. A short unpunctuated
        # line elsewhere is their prose, not a page they asked for.
        parsed = read_paste("# Acme\n\nOpen daily\n\nWe roast small batches.")
        levels = {b.text: b.level for b in parsed.document.outline}
        self.assertEqual(levels["Acme"], 1)
        self.assertEqual(levels["Open daily"], 0)

    def test_bullets_and_sentences_are_never_headings(self):
        parsed = read_paste("Acme\n\n- Wholesale\n\nWe deliver.\n\nend")
        levels = {b.text: b.level for b in parsed.document.outline}
        self.assertEqual(levels["Wholesale"], 0)
        self.assertEqual(levels["We deliver."], 0)

    def test_inline_markdown_is_stripped(self):
        parsed = read_paste("Call **012-345 6789** or visit [our shop](https://a.example/s).")
        self.assertEqual(
            parsed.document.raw_text, "Call 012-345 6789 or visit our shop."
        )

    def test_markdown_images_become_image_refs(self):
        parsed = read_paste("![Our roastery](https://cdn.acme.example/r.jpg)\n\nWe roast.")
        self.assertEqual(parsed.images[0].url, "https://cdn.acme.example/r.jpg")
        self.assertEqual(parsed.images[0].alt, "Our roastery")
        self.assertNotIn("http", parsed.document.raw_text)


class PasteSplitsIntoPagesTest(unittest.TestCase):
    """One splitter for every source: a pasted topic heading opens a page."""

    def test_topic_headings_open_pages(self):
        source = _pages("# Acme\n\nWe roast.\n\n## Contact\n\nCall us.\n\n## About Us\n\nSince 2011.")
        self.assertEqual(source.source_kind, "paste")
        self.assertEqual(
            [p.url_path for p in source.discovered_pages], ["/contact", "/about-us"]
        )

    def test_in_page_headings_do_not_fracture(self):
        source = _pages("# Acme\n\n## Why Choose Us\n\nWe care.\n\n## Our Promise\n\nAlways fresh.")
        self.assertEqual(source.discovered_pages, [])
        self.assertIn("Why Choose Us", source.headings)

    def test_images_land_on_the_page_they_appear_under(self):
        source = _pages(
            "<h1>Acme</h1><p>Hi</p>"
            '<h2>Our Team</h2><img src="https://cdn.acme.example/team.jpg" alt="The team">'
        )
        team = source.discovered_pages[0]
        self.assertEqual(team.url_path, "/our-team")
        self.assertEqual(team.images, ["https://cdn.acme.example/team.jpg"])
        self.assertEqual(team.image_metadata[0].alt, "The team")

    def test_paste_title_wins_over_the_first_heading(self):
        source = _pages("# Welcome\n\nHello.", title="Acme Coffee")
        self.assertEqual(source.title, "Acme Coffee")


def _crawled(**overrides) -> SourceContent:
    base = {
        "source_kind": "url",
        "source_ref": "https://acme.example/",
        "title": "Acme Coffee",
        "raw_text": "We roast small batches.",
        "headings": ["Acme Coffee"],
        "images": ["https://acme.example/hero.jpg"],
        "image_metadata": [ImageMetadata(url="https://acme.example/hero.jpg", alt="Hero")],
        "nav_links": [NavLink(label="Contact", href="https://acme.example/contact")],
        "discovered_pages": [
            SourceContent(
                source_kind="url",
                source_ref="https://acme.example/contact",
                title="Contact",
                raw_text="Old number: 03-111 2222",
                url_path="/contact",
            )
        ],
    }
    base.update(overrides)
    return SourceContent(**base)


class MergeTest(unittest.TestCase):
    def test_pasted_topic_joins_the_existing_page(self):
        merged = merge_sources(_crawled(), _pages("# Notes\n\n## Contact\n\nNew number: 012-345 6789"))
        self.assertEqual(len(merged.discovered_pages), 1)
        contact = merged.discovered_pages[0]
        self.assertEqual(contact.url_path, "/contact")
        self.assertIn("Old number", contact.raw_text)
        self.assertIn("New number", contact.raw_text)

    def test_unmatched_pages_are_appended(self):
        merged = merge_sources(_crawled(), _pages("# Notes\n\n## Our Team\n\nMei, Amir, Sara."))
        self.assertEqual(
            [p.url_path for p in merged.discovered_pages], ["/contact", "/our-team"]
        )

    def test_a_sub_page_does_not_swallow_a_topic(self):
        # /services/emergency-contact classifies as "contact" too, but a paste
        # about contacting the business belongs on a top-level page.
        base = _crawled(
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://acme.example/services/emergency-contact",
                    title="Emergency Contact",
                    raw_text="24h line",
                    url_path="/services/emergency-contact",
                )
            ]
        )
        merged = merge_sources(base, _pages("# Notes\n\n## Contact\n\nCall 012-345 6789"))
        self.assertEqual(
            [p.url_path for p in merged.discovered_pages],
            ["/services/emergency-contact", "/contact"],
        )

    def test_primary_content_is_joined_and_identity_is_the_base_source(self):
        merged = merge_sources(_crawled(), _pages("Extra homepage copy about our roastery."))
        self.assertEqual(merged.source_kind, "url")
        self.assertEqual(merged.source_ref, "https://acme.example/")
        self.assertEqual(merged.title, "Acme Coffee")
        self.assertIn("We roast small batches.", merged.raw_text)
        self.assertIn("Extra homepage copy", merged.raw_text)
        # Everything only a real reader can measure rides through untouched.
        self.assertEqual([n.label for n in merged.nav_links], ["Contact"])

    def test_headings_that_left_for_another_page_do_not_stay_on_the_homepage(self):
        # The paste is nothing but page sections. Its copy goes to those pages,
        # so the site's homepage must not claim their headings — a heading with
        # no copy behind it is what produces a hollow section.
        merged = merge_sources(
            _crawled(), _pages("## Contact\n\nCall 012.\n\n## Our Team\n\nMei and Amir.")
        )
        self.assertEqual(merged.headings, ["Acme Coffee"])
        self.assertIn("Contact", merged.discovered_pages[0].headings)

    def test_the_pastes_own_homepage_headings_are_kept(self):
        merged = merge_sources(_crawled(), _pages("## Why Choose Us\n\nWe care."))
        self.assertIn("Why Choose Us", merged.headings)

    def test_imagery_is_appended_without_duplicates(self):
        addition = _pages(
            '<p>x</p><img src="https://acme.example/hero.jpg" alt="Hero">'
            '<img src="https://cdn.acme.example/new.jpg" alt="New">'
        )
        merged = merge_sources(_crawled(), addition)
        self.assertEqual(
            merged.images,
            ["https://acme.example/hero.jpg", "https://cdn.acme.example/new.jpg"],
        )
        self.assertEqual(len(merged.image_metadata), 2)

    def test_page_cap_is_held(self):
        base = _crawled(
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref=f"https://acme.example/p{i}",
                    title=f"Page {i}",
                    raw_text="x",
                    url_path=f"/p{i}",
                )
                for i in range(MAX_DISCOVERED_PAGES)
            ]
        )
        merged = merge_sources(base, _pages("# Notes\n\n## Our Team\n\nMei and Amir."))
        self.assertEqual(len(merged.discovered_pages), MAX_DISCOVERED_PAGES)


class PasteEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_standalone_paste_returns_a_preview_payload(self):
        response = self.client.post(
            "/api/paste/preview",
            json={"text": "# Acme\n\nWe roast.\n\n## Contact\n\nCall 012.", "title": "Acme"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source_content"]["source_kind"], "paste")
        self.assertEqual(body["discovered_count"], 1)
        self.assertIsNone(body["brand_candidate"])
        self.assertEqual(body["paste"]["added_pages"], 1)
        self.assertFalse(body["paste"]["merged"])

    def test_merged_paste_returns_the_merged_source_and_its_own_images(self):
        response = self.client.post(
            "/api/paste/preview",
            json={
                "text": '<h2>Contact</h2><p>012-345 6789</p>'
                        '<img src="https://cdn.acme.example/shop.jpg" alt="Shop">',
                "base": _crawled().model_dump(mode="json"),
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source_content"]["source_kind"], "url")
        self.assertTrue(body["paste"]["merged"])
        self.assertTrue(body["paste"]["is_html"])
        # The paste's images only — the caller keeps the reader's own candidates.
        self.assertEqual(
            [c["url"] for c in body["image_candidates"]],
            ["https://cdn.acme.example/shop.jpg"],
        )

    def test_empty_paste_is_rejected(self):
        response = self.client.post("/api/paste/preview", json={"text": "   "})
        self.assertEqual(response.status_code, 400)

    def test_oversized_paste_is_rejected(self):
        response = self.client.post("/api/paste/preview", json={"text": "x" * 400_001})
        self.assertEqual(response.status_code, 413)

    def test_markup_with_no_readable_text_is_rejected(self):
        response = self.client.post(
            "/api/paste/preview",
            json={"text": "<div><script>var a=1;</script><style>p{}</style></div>"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
