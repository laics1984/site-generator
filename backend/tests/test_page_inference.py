import unittest

from app.models.content_blocks import ImageMetadata, ProfileCandidate, SourceContent
from app.services.page_inference import (
    _MAX_PAGE_SECTIONS,
    DIRECTORY_MIN_PROFILES,
    infer_page_scaffolds,
)


def _profiles(n: int) -> list[ProfileCandidate]:
    return [
        ProfileCandidate(
            name=f"Aisha Rahman{i}",
            role="Music Therapist",
            photo_url=f"https://example.my/photos/p{i}.jpg",
            confidence=0.9,
        )
        for i in range(n)
    ]


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

    def test_profile_rich_unmatched_page_becomes_team_directory(self):
        # "Find a Music Therapist" matches no team keyword by slug alone in the
        # pre-hint world — the profile-card evidence must classify it, and the
        # recipe must render the roster, not FAQ-ify it behind a contact form.
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/therapist-listing",
                    title="Our Practitioner Listing",
                    raw_text="Aisha Rahman Music Therapist ...",
                    url_path="/therapist-listing",
                    profile_candidates=_profiles(DIRECTORY_MIN_PROFILES + 2),
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        directory = next(s for s in scaffolds if s.slug == "therapist-listing")

        self.assertEqual(directory.page_type, "team")
        self.assertIn("team", directory.sections)
        self.assertNotIn("contact", directory.sections)
        self.assertNotIn("faq", directory.sections)

    def test_find_a_slug_keyword_maps_to_team(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/find-a-music-therapist",
                    title="Find a Music Therapist",
                    raw_text="Aisha Rahman Music Therapist ...",
                    url_path="/find-a-music-therapist",
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        directory = next(s for s in scaffolds if s.slug == "find-a-music-therapist")

        self.assertEqual(directory.page_type, "team")
        self.assertEqual(directory.sections, ["hero", "team", "cta"])

    def test_profile_rich_contact_slug_becomes_directory(self):
        # MMTA parks its "Find a Music Therapist" directory at /contact — a
        # slug-typed contact page whose body is a roster must render the
        # roster, not an invented form + FAQ.
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/contact",
                    title="Find a Music Therapist",
                    raw_text="Aisha Rahman Music Therapist ...",
                    url_path="/contact",
                    profile_candidates=_profiles(DIRECTORY_MIN_PROFILES + 2),
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        directory = next(s for s in scaffolds if s.slug == "contact")

        self.assertEqual(directory.page_type, "team")
        self.assertEqual(directory.sections, ["hero", "team", "cta"])

    def test_genuine_contact_page_keeps_contact_recipe(self):
        # A contact page with a couple of inline staff cards (below the
        # directory threshold) must keep its form.
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/contact",
                    title="Contact",
                    raw_text="Reach our team.",
                    url_path="/contact",
                    profile_candidates=_profiles(2),
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        contact = next(s for s in scaffolds if s.slug == "contact")

        self.assertEqual(contact.page_type, "contact")
        self.assertIn("contact", contact.sections)

    def test_few_profiles_keep_services_classification(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/programmes",
                    title="Programmes",
                    raw_text="Our programmes.",
                    url_path="/programmes",
                    profile_candidates=_profiles(DIRECTORY_MIN_PROFILES - 3),
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        programmes = next(s for s in scaffolds if s.slug == "programmes")

        self.assertEqual(programmes.page_type, "services")

    def test_no_crawl_directory_entry_gets_source_team_scaffold(self):
        # Scraping the directory URL directly with no crawl: the roster page
        # must exist in the fallback template set, source-evidenced.
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my/find-a-music-therapist",
            title="Find a Music Therapist",
            raw_text="Aisha Rahman Music Therapist ...",
            url_path="/find-a-music-therapist",
            profile_candidates=_profiles(DIRECTORY_MIN_PROFILES + 2),
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        directory = next(
            (s for s in scaffolds if s.page_type == "team" and s.from_source), None
        )

        self.assertIsNotNone(directory)
        self.assertEqual(directory.slug, "find-a-music-therapist")
        self.assertIn("team", directory.sections)

    def test_crawled_directory_entry_weaves_team_into_homepage(self):
        # Crawl present, entry page IS the directory, and nothing else claims
        # a team section — the homepage surfaces the roster.
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my/find-a-music-therapist",
            title="Find a Music Therapist",
            raw_text="Aisha Rahman Music Therapist ...",
            profile_candidates=_profiles(DIRECTORY_MIN_PROFILES + 2),
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/pricing",
                    title="Pricing",
                    raw_text="Plans.",
                    url_path="/pricing",
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        home = next(s for s in scaffolds if s.is_homepage)

        self.assertIn("team", home.sections)

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

    def test_evidenced_extras_never_balloon_a_page_past_the_ceiling(self):
        # A signal-rich homepage on an already-long pattern (childcare) must not
        # grow into an over-large single-call page — evidenced extras are trimmed
        # to the per-page ceiling, but the core hero/cta frame always survives.
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text=(
                "Established in 2004. An award-winning, accredited 5-star centre "
                "with over 20 years of experience nurturing young minds."
            ),
        )
        scaffolds = infer_page_scaffolds(source, industry="childcare")
        home = next(s for s in scaffolds if s.is_homepage)

        self.assertLessEqual(len(home.sections), _MAX_PAGE_SECTIONS)
        self.assertEqual(home.sections[0], "hero")
        self.assertEqual(home.sections[-1], "cta")

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


class StoryPageRhythmTest(unittest.TestCase):
    """A source page that narrates section-by-section (heading + paragraph +
    photo, repeated) gets a story rhythm — one image+text `about` per source
    section — instead of the fixed text-only card rhythm."""

    @staticmethod
    def _story_page(slug: str = "school-life", sections: int = 4) -> SourceContent:
        headings = [f"Programme {i}" for i in range(sections)]
        return SourceContent(
            source_kind="url",
            source_ref=f"https://example.my/{slug}",
            title="School Life",
            raw_text="\n".join(
                f"{h}\nLearning domains for this age group." for h in headings
            ),
            url_path=f"/{slug}",
            headings=headings,
            image_metadata=[
                ImageMetadata(
                    url=f"https://example.my/photo-{i}.jpg",
                    alt=f"Children in {h}",
                    role="content",
                    context_heading=h,
                )
                for i, h in enumerate(headings)
            ],
        )

    def _scaffold_for(self, page: SourceContent):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[page],
        )
        scaffolds = infer_page_scaffolds(source, industry="childcare")
        return next(s for s in scaffolds if s.slug == "school-life")

    def test_story_page_gets_one_about_per_source_section(self):
        scaffold = self._scaffold_for(self._story_page(sections=4))
        self.assertEqual(scaffold.sections[0], "hero")
        self.assertEqual(scaffold.sections.count("about"), 4)
        self.assertIn("cta", scaffold.sections)
        self.assertNotIn("features", scaffold.sections)

    def test_story_rhythm_respects_the_page_section_ceiling(self):
        scaffold = self._scaffold_for(self._story_page(sections=12))
        self.assertLessEqual(len(scaffold.sections), _MAX_PAGE_SECTIONS)
        self.assertEqual(scaffold.sections.count("about"), _MAX_PAGE_SECTIONS - 2)

    def test_two_matched_photos_weave_abouts_but_keep_the_card_rhythm(self):
        # Below the story threshold the page keeps its fixed rhythm, but its
        # matched photos are woven in as image+text about splits.
        scaffold = self._scaffold_for(self._story_page(sections=2))
        self.assertEqual(scaffold.sections.count("about"), 2)
        self.assertEqual(scaffold.sections[0], "hero")
        # The base rhythm's card sections survive — this is weaving, not a
        # story override.
        self.assertTrue(
            {"features", "process"} & set(scaffold.sections),
            scaffold.sections,
        )

    def test_contact_page_never_becomes_a_story_page(self):
        page = self._story_page(slug="contact")
        page.title = "Contact"
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[page],
        )
        scaffolds = infer_page_scaffolds(source, industry="childcare")
        contact = next(s for s in scaffolds if s.slug == "contact")
        self.assertLessEqual(contact.sections.count("about"), 1)


class PhotoSectionWeavingTest(unittest.TestCase):
    """Image-rich pages below the story threshold weave image+text about
    splits into their fixed rhythm — photo support everywhere, not only on
    detected story pages."""

    @staticmethod
    def _page(slug: str, image_metadata=None, headings=None) -> SourceContent:
        return SourceContent(
            source_kind="url",
            source_ref=f"https://example.my/{slug}",
            title=slug.replace("-", " ").title(),
            raw_text="Section text.\nMore text.",
            url_path=f"/{slug}",
            headings=headings or [],
            image_metadata=image_metadata or [],
        )

    @staticmethod
    def _source(pages) -> SourceContent:
        return SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=pages,
        )

    def test_homepage_weaves_an_about_when_source_home_is_image_rich(self):
        photos = [
            ImageMetadata(url=f"https://x/p{i}.jpg", role="content")
            for i in range(5)
        ]
        source = self._source([])
        source.image_metadata = photos
        source.headings = []

        scaffolds = infer_page_scaffolds(source, industry="childcare")
        home = next(s for s in scaffolds if s.is_homepage)
        self.assertGreaterEqual(home.sections.count("about"), 1)

    def test_text_only_page_gets_one_stock_backed_about(self):
        # Baseline landing-page practice: even a text-only source page carries
        # one image+text section — its photo resolves from stock (image_query).
        source = self._source([self._page("services")])
        scaffolds = infer_page_scaffolds(source, industry="childcare")
        services = next(s for s in scaffolds if s.slug == "services")
        self.assertEqual(services.sections.count("about"), 1)
        # The card rhythm survives — this is one woven moment, not an override.
        self.assertIn("services", services.sections)

    def test_four_loose_photos_without_heading_matches_weave_one_about(self):
        photos = [
            ImageMetadata(url=f"https://x/p{i}.jpg", role="content")
            for i in range(4)
        ]
        source = self._source([self._page("our-work", image_metadata=photos)])
        scaffolds = infer_page_scaffolds(source, industry="childcare")
        work = next(s for s in scaffolds if s.slug == "our-work")
        self.assertEqual(work.sections.count("about"), 1)

    def test_contact_page_is_never_woven(self):
        photos = [
            ImageMetadata(url=f"https://x/p{i}.jpg", role="content")
            for i in range(6)
        ]
        source = self._source([self._page("contact", image_metadata=photos)])
        scaffolds = infer_page_scaffolds(source, industry="childcare")
        contact = next(s for s in scaffolds if s.slug == "contact")
        self.assertEqual(contact.sections.count("about"), 0)

    def test_woven_abouts_sit_before_the_closing_cta(self):
        headings = ["Programme A", "Programme B"]
        photos = [
            ImageMetadata(
                url=f"https://x/p{i}.jpg", role="content", context_heading=h
            )
            for i, h in enumerate(headings)
        ]
        source = self._source(
            [self._page("programmes", image_metadata=photos, headings=headings)]
        )
        scaffolds = infer_page_scaffolds(source, industry="childcare")
        page = next(s for s in scaffolds if s.slug == "programmes")
        if "cta" in page.sections:
            last_about = max(
                i for i, s in enumerate(page.sections) if s == "about"
            )
            self.assertLess(last_about, page.sections.index("cta"))

    def test_events_page_is_inferred_with_hero_only_scaffold(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/events",
                    title="Upcoming Events",
                    raw_text="Annual dinner. Charity run.",
                    url_path="/events",
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        events = next(s for s in scaffolds if s.slug == "events")

        self.assertEqual(events.page_type, "events")
        self.assertEqual(events.sections, ["hero"])

    def test_blog_and_event_detail_subpages_are_not_scaffolded(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/blog",
                    title="Blog",
                    raw_text="Latest posts.",
                    url_path="/blog",
                ),
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/blog/my-first-post",
                    title="My First Post",
                    raw_text="Post body text.",
                    url_path="/blog/my-first-post",
                ),
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/events",
                    title="Events",
                    raw_text="What's on.",
                    url_path="/events",
                ),
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/events/annual-dinner",
                    title="Annual Dinner",
                    raw_text="Join us for dinner.",
                    url_path="/events/annual-dinner",
                ),
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        slugs = {s.slug for s in scaffolds}

        self.assertIn("blog", slugs)
        self.assertIn("events", slugs)
        self.assertNotIn("blog/my-first-post", slugs)
        self.assertNotIn("events/annual-dinner", slugs)

    def test_prevention_page_is_not_misread_as_events(self):
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.my",
            raw_text="Home page text.",
            discovered_pages=[
                SourceContent(
                    source_kind="url",
                    source_ref="https://example.my/prevention",
                    title="Fire Prevention",
                    raw_text="Prevention services.",
                    url_path="/prevention",
                )
            ],
        )

        scaffolds = infer_page_scaffolds(source, industry="other")
        prevention = next(s for s in scaffolds if s.slug == "prevention")

        self.assertNotEqual(prevention.page_type, "events")
