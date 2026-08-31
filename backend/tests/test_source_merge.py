"""
Joining two independently-read SourceContent trees into one — the machinery
behind "paste added to a crawl" (routers/paste.py), generalized to "a document
added to a crawl" and any other pairing (routers/source.py). The load-bearing
claims: pages join by topic/slug so the same page from two readers becomes
one page, not two; and a page that TWO readers each described keeps BOTH
sides' profile cards, embeds and nav evidence rather than silently keeping
only the base's.
"""

import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.models.content_blocks import (
    DocumentCardCandidate,
    DocumentCardLink,
    ImageMetadata,
    LinkCluster,
    MapEmbed,
    NavLink,
    ProfileCandidate,
    SectionCandidate,
    SourceContent,
    VideoEmbed,
)
from app.services.doc_structure import MAX_DISCOVERED_PAGES
from app.services.paste_source import read_paste
from app.services.doc_structure import split_into_pages
from app.services.source_merge import merge_sources


def _pages(text: str, *, title: str | None = None) -> SourceContent:
    """A paste, read and split exactly as the paste endpoint does."""
    parsed = read_paste(text, title=title)
    return split_into_pages(
        parsed.document, images=parsed.images, description=parsed.description
    )


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


class MergeSourcesTest(unittest.TestCase):
    """Relocated from test_paste_source.py — merge_sources is generic across
    any two SourceContent trees, not just "paste added to a crawl", even
    though these cases exercise it via a paste."""

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


class RichFieldUnionTest(unittest.TestCase):
    """The gap that was harmless while `addition` was always a thin paste:
    once it's a second RICH reader (a document upload, another crawl), a page
    matched by slug/topic must keep BOTH sides' profile cards, document cards,
    embeds, nav links and subject name — not just base's."""

    def _contact_page(self, **overrides) -> SourceContent:
        fields = {
            "source_kind": "url",
            "source_ref": "https://acme.example/contact",
            "title": "Contact",
            "raw_text": "Reach us anytime.",
            "url_path": "/contact",
        }
        fields.update(overrides)
        return SourceContent(**fields)

    def _base(self, contact_page: SourceContent) -> SourceContent:
        return SourceContent(
            source_kind="url",
            source_ref="https://acme.example/",
            title="Acme Coffee",
            raw_text="We roast small batches.",
            discovered_pages=[contact_page],
        )

    def _addition(self, contact_page: SourceContent) -> SourceContent:
        return SourceContent(
            source_kind="docx",
            source_ref="brochure.docx",
            title="Acme Coffee Brochure",
            raw_text="A brochure about our roastery.",
            discovered_pages=[contact_page],
        )

    def test_profile_candidates_union_across_a_matched_page(self):
        base_contact = self._contact_page(
            profile_candidates=[ProfileCandidate(name="Mei Lin", role="Manager")]
        )
        addition_contact = self._contact_page(
            profile_candidates=[ProfileCandidate(name="Amir Hassan", role="Barista")]
        )
        merged = merge_sources(self._base(base_contact), self._addition(addition_contact))
        names = {p.name for p in merged.discovered_pages[0].profile_candidates}
        self.assertEqual(names, {"Mei Lin", "Amir Hassan"})

    def test_profile_candidates_dedupe_by_name(self):
        base_contact = self._contact_page(
            profile_candidates=[ProfileCandidate(name="Mei Lin", role="Manager")]
        )
        addition_contact = self._contact_page(
            profile_candidates=[ProfileCandidate(name="Mei Lin", role="Manager")]
        )
        merged = merge_sources(self._base(base_contact), self._addition(addition_contact))
        self.assertEqual(len(merged.discovered_pages[0].profile_candidates), 1)

    def test_section_candidates_union(self):
        base_contact = self._contact_page(
            section_candidates=[SectionCandidate(heading="Opening Hours", level=2)]
        )
        addition_contact = self._contact_page(
            section_candidates=[SectionCandidate(heading="Directions", level=2)]
        )
        merged = merge_sources(self._base(base_contact), self._addition(addition_contact))
        headings = {s.heading for s in merged.discovered_pages[0].section_candidates}
        self.assertEqual(headings, {"Opening Hours", "Directions"})

    def test_document_cards_union(self):
        base_contact = self._contact_page(
            document_cards=[
                DocumentCardCandidate(
                    title="Price List",
                    links=[DocumentCardLink(label="PDF", href="/price.pdf")],
                )
            ]
        )
        addition_contact = self._contact_page(
            document_cards=[
                DocumentCardCandidate(
                    title="Brochure",
                    links=[DocumentCardLink(label="PDF", href="/brochure.pdf")],
                )
            ]
        )
        merged = merge_sources(self._base(base_contact), self._addition(addition_contact))
        titles = {c.title for c in merged.discovered_pages[0].document_cards}
        self.assertEqual(titles, {"Price List", "Brochure"})

    def test_video_and_map_embeds_union(self):
        base_contact = self._contact_page(
            map_embeds=[MapEmbed(provider="google", embed_url="https://maps.example/a")]
        )
        addition_contact = self._contact_page(
            video_embeds=[
                VideoEmbed(provider="youtube", video_id="abc123", embed_url="https://yt.example/abc123")
            ]
        )
        merged = merge_sources(self._base(base_contact), self._addition(addition_contact))
        page = merged.discovered_pages[0]
        self.assertEqual(len(page.map_embeds), 1)
        self.assertEqual(len(page.video_embeds), 1)

    def test_nav_links_and_subject_name_union(self):
        base_contact = self._contact_page(
            nav_links=[NavLink(label="Home", href="/")],
            body_link_clusters=[LinkCluster(links=[NavLink(label="Home", href="/")], href_key="k1")],
        )
        addition_contact = self._contact_page(
            nav_links=[NavLink(label="Shop", href="/shop")],
            subject_name="Amir Hassan",
        )
        merged = merge_sources(self._base(base_contact), self._addition(addition_contact))
        page = merged.discovered_pages[0]
        self.assertEqual({n.href for n in page.nav_links}, {"/", "/shop"})
        self.assertEqual(len(page.body_link_clusters), 1)
        self.assertEqual(page.subject_name, "Amir Hassan")


class ThreeWayMergeTest(unittest.TestCase):
    """A URL crawl, a document and a paste all contribute to one merged tree —
    the shape the composed-source flow relies on."""

    def test_url_then_document_then_paste_all_contribute(self):
        url_source = _crawled()
        doc_source = SourceContent(
            source_kind="docx",
            source_ref="menu.docx",
            title="Acme Coffee",
            raw_text="Full menu inside.",
            discovered_pages=[
                SourceContent(
                    source_kind="docx",
                    source_ref="menu.docx#contact",
                    title="Contact",
                    raw_text="Fax: 03-999 8888",
                    url_path="/contact",
                )
            ],
        )
        step1 = merge_sources(url_source, doc_source)
        step2 = merge_sources(step1, _pages("# Notes\n\n## Contact\n\nWhatsApp: 012-000 1111"))

        self.assertEqual(step2.source_kind, "url")  # URL identity still wins
        contact = step2.discovered_pages[0]
        self.assertIn("Old number", contact.raw_text)  # from the URL crawl
        self.assertIn("Fax: 03-999 8888", contact.raw_text)  # from the document
        self.assertIn("WhatsApp", contact.raw_text)  # from the paste


class SourceMergeEndpointTest(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_merge_endpoint_joins_two_sources(self):
        response = self.client.post(
            "/api/source/merge",
            json={
                "base": _crawled().model_dump(mode="json"),
                "addition": _pages("# Notes\n\n## Our Team\n\nMei and Amir.").model_dump(
                    mode="json"
                ),
            },
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source_content"]["source_kind"], "url")
        self.assertEqual(
            [p["url_path"] for p in body["source_content"]["discovered_pages"]],
            ["/contact", "/our-team"],
        )
        self.assertIsNone(body["brand_candidate"])


if __name__ == "__main__":
    unittest.main()
