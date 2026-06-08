import unittest

from app.models.content_blocks import SourceContent
from app.services.page_inference import infer_page_scaffolds


class PageInferenceTest(unittest.TestCase):
    def test_committee_page_is_inferred_as_team_page(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/committee",
                    title="Committee",
                    raw_text="Dr Aisha Rahman Chairperson",
                    url_path="/committee",
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        committee = next(s for s in scaffolds if s.slug == "committee")

        self.assertEqual(committee.page_type, "team")
        self.assertIn("team", committee.sections)

    def test_discovered_page_titles_drop_dangling_pipe_suffixes(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/about-us",
                    title="About Us |",
                    raw_text="About page text.",
                    url_path="/about-us",
                ),
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/contact-us",
                    title="Contact Us |",
                    raw_text="Contact page text.",
                    url_path="/contact-us",
                ),
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")

        about = next(s for s in scaffolds if s.slug == "about-us")
        contact = next(s for s in scaffolds if s.slug == "contact-us")

        self.assertEqual(about.title, "About Us")
        self.assertEqual(contact.title, "Contact Us")
