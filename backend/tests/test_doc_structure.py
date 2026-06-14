import unittest

from app.services.doc_contract import (
    classify_page_title,
    is_home_title,
    slug_for_title,
)
from app.services.doc_parser import OutlineBlock, ParsedDocument
from app.services.doc_structure import split_into_pages
from app.services.page_inference import infer_page_scaffolds


def _doc(outline, title="Acme Studio"):
    return ParsedDocument(
        source_kind="docx",
        source_ref="acme.docx",
        title=title,
        raw_text="\n".join(b.text for b in outline),
        headings=[],
        outline=outline,
    )


class TitleExaminationTest(unittest.TestCase):
    def test_page_topics_classified(self):
        self.assertEqual(classify_page_title("About Us"), "about")
        self.assertEqual(classify_page_title("Our Services"), "services")
        self.assertEqual(classify_page_title("Contact"), "contact")
        self.assertEqual(classify_page_title("Meet the Team"), "team")
        self.assertEqual(classify_page_title("Pricing Plans"), "pricing")

    def test_generic_headings_are_not_pages(self):
        # In-page headings must stay content, never spawn a page.
        self.assertIsNone(classify_page_title("Why Choose Us"))
        self.assertIsNone(classify_page_title("Our Promise"))
        self.assertIsNone(classify_page_title("Features"))

    def test_home_titles_recognized(self):
        self.assertTrue(is_home_title("Welcome"))
        self.assertTrue(is_home_title("Overview"))
        self.assertFalse(is_home_title("About Us"))

    def test_slug_synthesis(self):
        self.assertEqual(slug_for_title("About Us"), "about-us")
        self.assertEqual(slug_for_title("Our Services!"), "our-services")


class SplitIntoPagesTest(unittest.TestCase):
    def test_h2_subsections_do_not_fracture_pages(self):
        # "Our Story" is an H2 inside About and matches the about keyword — it must
        # remain part of the About page, not become its own page.
        sc = split_into_pages(
            _doc(
                [
                    OutlineBlock(1, "Acme Studio"),
                    OutlineBlock(0, "We craft brands."),
                    OutlineBlock(1, "About Us"),
                    OutlineBlock(0, "Founded 2010."),
                    OutlineBlock(2, "Our Story"),
                    OutlineBlock(0, "We started small."),
                    OutlineBlock(1, "Contact"),
                    OutlineBlock(0, "hello@acme.studio"),
                ]
            )
        )
        slugs = [p.url_path for p in sc.discovered_pages]
        self.assertEqual(slugs, ["/about-us", "/contact"])
        about = sc.discovered_pages[0]
        self.assertIn("We started small.", about.raw_text)

    def test_h2_top_level_opens_pages(self):
        # When the doc's shallowest topic level is H2, H2 headings open pages.
        sc = split_into_pages(
            _doc(
                [
                    OutlineBlock(1, "Acme Studio"),
                    OutlineBlock(0, "Intro."),
                    OutlineBlock(2, "About"),
                    OutlineBlock(0, "About copy."),
                    OutlineBlock(2, "Services"),
                    OutlineBlock(0, "Services copy."),
                ]
            )
        )
        self.assertEqual(
            [p.url_path for p in sc.discovered_pages], ["/about", "/services"]
        )

    def test_no_headings_stays_single_page(self):
        sc = split_into_pages(
            _doc([OutlineBlock(0, "One blob."), OutlineBlock(0, "More.")], title=None)
        )
        self.assertEqual(sc.discovered_pages, [])
        self.assertIn("One blob.", sc.raw_text)

    def test_duplicate_topic_folds_into_one_page(self):
        sc = split_into_pages(
            _doc(
                [
                    OutlineBlock(1, "Services"),
                    OutlineBlock(0, "First."),
                    OutlineBlock(1, "Solutions"),
                    OutlineBlock(0, "Second."),
                    OutlineBlock(1, "Contact"),
                    OutlineBlock(0, "Reach us."),
                ]
            )
        )
        slugs = [p.url_path for p in sc.discovered_pages]
        self.assertEqual(slugs, ["/services", "/contact"])
        self.assertIn("Second.", sc.discovered_pages[0].raw_text)


class DocToScaffoldsTest(unittest.TestCase):
    def test_planner_owns_sections_not_the_document(self):
        # The document supplies which pages exist; the planner supplies each
        # page's sections. Proven by the About page getting the template's
        # section rhythm rather than anything derived from the doc's headings.
        sc = split_into_pages(
            _doc(
                [
                    OutlineBlock(1, "Acme Studio"),
                    OutlineBlock(0, "Intro."),
                    OutlineBlock(1, "About Us"),
                    OutlineBlock(0, "Our history."),
                    OutlineBlock(1, "Contact"),
                    OutlineBlock(0, "hello@acme.studio"),
                ]
            )
        )
        scaffolds = infer_page_scaffolds(sc, industry="other", site_name="Acme Studio")
        slugs = {s.slug for s in scaffolds}
        self.assertIn("about-us", slugs)
        about = next(s for s in scaffolds if s.slug == "about-us")
        self.assertEqual(about.page_type, "about")
        # Sections come from the planner's catalog, so the page is composed, not
        # a literal mirror of the single "About Us" heading.
        self.assertGreater(len(about.sections), 1)


if __name__ == "__main__":
    unittest.main()
