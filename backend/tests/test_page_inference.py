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

    def test_discovered_team_page_removes_full_team_section_from_about(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/about",
                    title="About",
                    raw_text="About page text.",
                    url_path="/about",
                ),
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/team",
                    title="Team",
                    raw_text="Dr Aisha Rahman Chairperson.",
                    url_path="/team",
                ),
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")

        about = next(s for s in scaffolds if s.slug == "about")
        team = next(s for s in scaffolds if s.slug == "team")
        self.assertNotIn("team", about.sections)
        self.assertEqual(team.sections, ["hero", "team", "cta"])

    def test_homepage_with_founding_history_gets_timeline_section(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Founded in 1998, we have grown into a regional name.",
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        home = next(s for s in scaffolds if s.is_homepage)

        self.assertIn("timeline", home.sections)

    def test_homepage_with_award_copy_gets_awards_section(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="We are an award-winning bakery, certified by the guild.",
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        home = next(s for s in scaffolds if s.is_homepage)

        self.assertIn("awards", home.sections)

    def test_homepage_with_client_roster_gets_clients_section(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Trusted by some of the world's best known brands.",
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        home = next(s for s in scaffolds if s.is_homepage)

        self.assertIn("clients", home.sections)

    def test_homepage_with_no_signals_skips_new_sections(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="We make great coffee and serve it with a smile.",
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        home = next(s for s in scaffolds if s.is_homepage)

        for kind in ("timeline", "awards", "clients", "stats"):
            self.assertNotIn(kind, home.sections)

    def test_contact_page_never_gets_clients_section_even_with_signal(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/contact",
                    title="Contact",
                    raw_text="Trusted by some of the world's best known brands.",
                    url_path="/contact",
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        contact = next(s for s in scaffolds if s.slug == "contact")

        self.assertNotIn("clients", contact.sections)

    def test_template_fallback_keeps_team_under_about_without_team_evidence(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
        )

        scaffolds = infer_page_scaffolds(source, industry="professional-services")

        self.assertIsNone(next((s for s in scaffolds if s.slug == "team"), None))
        about = next(s for s in scaffolds if s.slug == "about")
        self.assertIn("team", about.sections)
