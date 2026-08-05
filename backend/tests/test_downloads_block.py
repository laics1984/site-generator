import unittest

from bs4 import BeautifulSoup

from app.models.content_blocks import (
    DocumentCardCandidate,
    DocumentCardLink,
    PagePlan,
    SourceContent,
)
from app.routers.generate import _inject_downloads
from app.services.scraper import _extract_document_cards

BASE = "https://mmta.org.my/infocard"

# Real structure of mmta.org.my's Info Cards page (mmta/resources/views/
# infocard.blade.php): four .icard divs — one with a single download link (no
# language split), three with a title + thumbnail + 3 per-language PDF links.
# No class-name assumptions in the extractor itself; this fixture just mirrors
# a real, previously-mishandled page.
INFO_CARDS_HTML = """
<html><body>
<section><div>
<div class="container"><p>Want to find out more about who we are and what we do?</p></div>
<div class="container contact_list_dv">
  <div class="col-md-6 icard">
    <p>Music Therapy General Flyer</p>
    <img src="/assets/infocard/general_flyer.jpg">
    <div>What is Music Therapy? Domains and goals <a href="/assets/infocard/cards/general/MMTA_flyer.pdf">Download Flyer</a></div>
  </div>
  <div class="col-md-6 icard">
    <p>Music Therapy for Mental Health</p>
    <img src="/assets/infocard/mentalhealth.jpg">
    <div>Download <a href="/assets/infocard/cards/mentalhealth/MT_in_Mental_Health.pdf">English</a> <a href="/assets/infocard/cards/mentalhealth/Terapi_Muzik.pdf">Bahasa Malaysia</a> <a href="/assets/infocard/cards/mentalhealth/MT_zh.pdf">中文</a></div>
  </div>
  <div class="col-md-6 icard">
    <p>Music Therapy for Aged Care</p>
    <img src="/assets/infocard/agedcare.jpg">
    <div>Download <a href="/assets/infocard/cards/agedcare/MT_Aged.pdf">English</a> <a href="/assets/infocard/cards/agedcare/Terapi.pdf">Bahasa Malaysia</a> <a href="/assets/infocard/cards/agedcare/MT_zh.pdf">中文</a></div>
  </div>
  <div class="col-md-6 icard">
    <p>Music Therapy for Special Education</p>
    <img src="/assets/infocard/specialed.jpg">
    <div>Download <a href="/assets/infocard/cards/specialneeds/MT_Special.pdf">English</a> <a href="/assets/infocard/cards/specialneeds/Terapi.pdf">Bahasa Malaysia</a> <a href="/assets/infocard/cards/specialneeds/MT_zh.pdf">中文</a></div>
  </div>
</div>
</div></section>
</body></html>
"""


class DocumentCardExtractionTest(unittest.TestCase):
    def test_real_info_cards_page_yields_four_grouped_cards(self):
        soup = BeautifulSoup(INFO_CARDS_HTML, "lxml")
        cards = _extract_document_cards(soup, BASE)
        self.assertEqual(len(cards), 4)

        by_title = {c.title: c for c in cards}
        self.assertEqual(
            set(by_title),
            {
                "Music Therapy General Flyer",
                "Music Therapy for Mental Health",
                "Music Therapy for Aged Care",
                "Music Therapy for Special Education",
            },
        )

        flyer = by_title["Music Therapy General Flyer"]
        self.assertEqual(len(flyer.links), 1)
        self.assertTrue(flyer.image_url.startswith("https://mmta.org.my/"))

        mental_health = by_title["Music Therapy for Mental Health"]
        self.assertEqual(
            [link.label for link in mental_health.links],
            ["English", "Bahasa Malaysia", "中文"],
        )
        self.assertTrue(
            mental_health.links[0].href.endswith("MT_in_Mental_Health.pdf")
        )

    def test_titleless_multi_language_cluster_still_qualifies(self):
        # A bare "download in 3 languages" card with no separate title/image —
        # the >1-link branch of the qualifying bar must still accept it.
        html = """
        <main><div class="info-card">
          <a href="/files/card-en.pdf">English</a>
          <a href="/files/card-zh.pdf">中文</a>
          <a href="/files/card-ms.pdf">Bahasa Melayu</a>
        </div></main>
        """
        soup = BeautifulSoup(html, "lxml")
        cards = _extract_document_cards(soup, "https://example.com")
        self.assertEqual(len(cards), 1)
        self.assertIsNone(cards[0].title)
        self.assertEqual(len(cards[0].links), 3)

    def test_bare_nav_item_linking_a_pdf_is_not_a_card(self):
        html = """
        <main><ul class="menu">
          <li><a href="/about">About</a></li>
          <li><a href="/services">Services</a></li>
          <li><a href="/files/brochure.pdf">Brochure</a></li>
        </ul></main>
        """
        soup = BeautifulSoup(html, "lxml")
        cards = _extract_document_cards(soup, "https://example.com")
        self.assertEqual(cards, [])

    def test_pdf_mentioned_inline_in_prose_is_not_a_card(self):
        html = """
        <main><p>Some prose mentioning a report you can
        <a href="/files/report.pdf">download here</a> in passing.</p></main>
        """
        soup = BeautifulSoup(html, "lxml")
        cards = _extract_document_cards(soup, "https://example.com")
        self.assertEqual(cards, [])

    def test_nav_and_footer_are_excluded(self):
        html = """
        <html><body>
        <nav><a href="/files/menu-brochure.pdf">Brochure</a>
             <a href="/files/menu-brochure2.pdf">Brochure 2</a>
             <a href="/files/menu-brochure3.pdf">Brochure 3</a></nav>
        <footer><a href="/files/footer-terms.pdf">Terms</a>
                <a href="/files/footer-terms2.pdf">Terms 2</a>
                <a href="/files/footer-terms3.pdf">Terms 3</a></footer>
        <main><p>No downloads here.</p></main>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        cards = _extract_document_cards(soup, "https://example.com")
        self.assertEqual(cards, [])


def _page(slug: str, *, is_homepage: bool = False) -> PagePlan:
    return PagePlan(
        page_type="home" if is_homepage else "landing",
        slug=slug,
        title=slug or "Home",
        is_homepage=is_homepage,
        blocks=[],
        seo_title="t",
        seo_description="d",
    )


def _source(cards, *, url_path=None, discovered=None, ref="https://mmta.org.my") -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref=ref,
        raw_text="",
        url_path=url_path,
        document_cards=cards,
        discovered_pages=discovered or [],
    )


def _card(title, *links, image_url=None) -> DocumentCardCandidate:
    return DocumentCardCandidate(
        title=title,
        image_url=image_url,
        links=[DocumentCardLink(label=lbl, href=href) for lbl, href in links],
    )


class DownloadsInjectionTest(unittest.TestCase):
    def test_multiple_document_cards_become_one_block_not_several(self):
        """The actual bug being fixed: 4 documents on one page must land as 4
        items of ONE DownloadsBlock, not 4 separate blocks."""
        home = _page("", is_homepage=True)
        source = _source(
            [
                _card("General Flyer", ("Download Flyer", "https://mmta.org.my/a.pdf")),
                _card(
                    "Mental Health",
                    ("English", "https://mmta.org.my/mh-en.pdf"),
                    ("中文", "https://mmta.org.my/mh-zh.pdf"),
                    image_url="https://mmta.org.my/mh.jpg",
                ),
            ]
        )
        _inject_downloads([home], source)

        self.assertEqual(len(home.blocks), 1)
        block = home.blocks[0]
        self.assertEqual(block.kind, "downloads")
        self.assertEqual(len(block.items), 2)
        self.assertEqual(block.items[0].title, "General Flyer")
        self.assertEqual(len(block.items[0].links), 1)
        self.assertEqual(block.items[1].title, "Mental Health")
        self.assertEqual(block.items[1].image_url, "https://mmta.org.my/mh.jpg")
        self.assertEqual(
            [link.label for link in block.items[1].links], ["English", "中文"]
        )

    def test_injected_onto_matching_subpage_not_homepage(self):
        home = _page("", is_homepage=True)
        resources = _page("resources")
        sub = _source(
            [_card("Brochure", ("Download", "https://mmta.org.my/b.pdf"))],
            url_path="/resources",
        )
        source = _source([], discovered=[sub])

        _inject_downloads([home, resources], source)

        self.assertEqual(len(home.blocks), 0)
        self.assertEqual(len(resources.blocks), 1)
        self.assertEqual(resources.blocks[0].items[0].title, "Brochure")

    def test_page_with_no_matching_generated_page_is_skipped(self):
        home = _page("", is_homepage=True)
        sub = _source(
            [_card("Brochure", ("Download", "https://mmta.org.my/b.pdf"))],
            url_path="/nonexistent",
        )
        source = _source([], discovered=[sub])

        _inject_downloads([home], source)

        self.assertEqual(len(home.blocks), 0)

    def test_no_document_cards_is_a_noop(self):
        home = _page("", is_homepage=True)
        _inject_downloads([home], _source([]))
        self.assertEqual(len(home.blocks), 0)


if __name__ == "__main__":
    unittest.main()
