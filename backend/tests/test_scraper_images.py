import json
import unittest

from bs4 import BeautifulSoup

import app.services.scraper as scraper
from app.services.scraper import _extract_images


def _stamp(**overrides) -> str:
    """Render-evidence JSON like _stamp_render_evidence produces (1280x800)."""
    base = {"nw": 0, "nh": 0, "x": 0, "y": 0, "w": 0, "h": 0, "vw": 1280, "vh": 800}
    base.update(overrides)
    return json.dumps(base)


class ScraperImageExtractionTest(unittest.TestCase):
    def test_extract_images_uses_srcset_when_img_src_is_placeholder(self):
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <section class="hero">
                  <img
                    src="/placeholder.svg"
                    srcset="/small.jpg 480w, /hero-sea-team.jpg 1200w"
                    alt="Southeast Asian clinic team"
                    width="1200"
                    height="800"
                  />
                </section>
              </body>
            </html>
            """,
            "lxml",
        )

        images = _extract_images(soup, "https://example.my")

        self.assertEqual(images[0].url, "https://example.my/hero-sea-team.jpg")
        self.assertEqual(images[0].intent, "hero")

    def test_responsive_figure_prefers_largest_srcset_over_small_src(self):
        # A normal responsive <figure> keeps a small fallback in `src` while the
        # full-resolution variant lives in `srcset`. Capture the large one.
        soup = BeautifulSoup(
            """
            <html><body>
              <figure>
                <img src="/photo-800.jpg"
                     srcset="/photo-400.jpg 400w, /photo-800.jpg 800w, /photo-2000.jpg 2000w"
                     width="1200" height="800" alt="community clinic" />
                <figcaption>Our clinic</figcaption>
              </figure>
            </body></html>
            """,
            "lxml",
        )
        images = _extract_images(soup, "https://example.my")
        self.assertEqual(images[0].url, "https://example.my/photo-2000.jpg")

    def test_descriptorless_srcset_does_not_override_good_src(self):
        # A single-URL srcset with no width/density descriptor is no better than
        # the base `src`, so keep `src`.
        soup = BeautifulSoup(
            """
            <html><body>
              <img src="/real-photo.jpg" srcset="/same-photo.jpg"
                   width="1200" height="800" alt="storefront" />
            </body></html>
            """,
            "lxml",
        )
        images = _extract_images(soup, "https://example.my")
        self.assertEqual(images[0].url, "https://example.my/real-photo.jpg")

    def test_wix_blur_placeholder_url_is_upgraded_to_full_resolution(self):
        # Wix bakes w_/h_/blur into the URL path; the scraper must capture the
        # untransformed original, not the tiny blurred blur-up placeholder.
        media_id = "b90fa9_31305eee~mv2_d_7360_4912_s_4_2.jpg"
        wix = (
            f"https://static.wixstatic.com/media/{media_id}"
            "/v1/fill/w_119,h_79,al_c,q_80,usm_0.66_1.00_0.01,blur_2,"
            f"enc_avif,quality_auto/{media_id}"
        )
        soup = BeautifulSoup(
            f'<html><body><figure><img src="{wix}" width="1200" height="800" '
            'alt="classroom" /></figure></body></html>',
            "lxml",
        )
        images = _extract_images(soup, "https://www.glorykids.edu.my")
        # 7360x4912 capped to a 2560 long edge, blur dropped, aspect preserved.
        self.assertEqual(
            images[0].url,
            f"https://static.wixstatic.com/media/{media_id}/v1/fill/w_2560,h_1708,al_c,q_90/{media_id}",
        )

    def test_comma_bearing_srcset_url_is_not_shattered(self):
        # Wix bakes a comma-laden transform into the srcset URL; the parser must
        # keep each URL whole instead of splitting on the internal commas (which
        # used to yield a dead …/quality_auto/logo.png fragment resolved against
        # the page domain -> 404).
        mid = "2b31dd_1743cc~mv2.png"
        srcset = (
            f"https://static.wixstatic.com/media/{mid}/v1/fill/"
            f"w_461,h_161,al_c,q_85,usm_0.66_1.00_0.01,enc_avif,quality_auto/logo.png 1x, "
            f"https://static.wixstatic.com/media/{mid}/v1/fill/"
            f"w_759,h_265,al_c,lg_1,q_85,enc_avif,quality_auto/logo.png 2x"
        )
        soup = BeautifulSoup(
            f'<html><body><img srcset="{srcset}" width="461" height="161" '
            'alt="logo" /></body></html>',
            "lxml",
        )
        images = _extract_images(soup, "https://www.glorykids.edu.my")
        # No dimensions in this media id → bare original; crucially on the Wix
        # host, never the page domain.
        self.assertEqual(
            images[0].url,
            f"https://static.wixstatic.com/media/{mid}",
        )
        self.assertNotIn("glorykids.edu.my", images[0].url)

    def test_non_wix_url_is_left_unchanged(self):
        soup = BeautifulSoup(
            '<html><body><img src="https://cdn.example.com/photo.jpg" '
            'width="1200" height="800" alt="x" /></body></html>',
            "lxml",
        )
        images = _extract_images(soup, "https://example.com")
        self.assertEqual(images[0].url, "https://cdn.example.com/photo.jpg")

    def test_extract_images_uses_stamped_computed_backgrounds(self):
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <section class="hero" data-webtree-bg-image="/computed-hero.jpg">
                  <h1>Malaysia clinic</h1>
                </section>
              </body>
            </html>
            """,
            "lxml",
        )

        images = _extract_images(soup, "https://example.my")

        self.assertEqual(images[0].url, "https://example.my/computed-hero.jpg")
        self.assertEqual(images[0].intent, "hero")

    def test_evidence_picks_measured_hero_over_dom_order(self):
        # DOM order says the small inline photo comes first; measurement says
        # the second image is the lead visual. Evidence must win.
        soup = BeautifulSoup(
            f"""
            <html>
              <body>
                <img src="/inline-story.jpg"
                     data-webtree-evidence='{_stamp(nw=600, nh=400, x=40, y=1500, w=300, h=200)}' />
                <img src="/big-hero.jpg"
                     data-webtree-evidence='{_stamp(nw=1920, nh=900, x=0, y=0, w=1280, h=640)}' />
              </body>
            </html>
            """,
            "lxml",
        )

        images = _extract_images(soup, "https://example.my")

        by_url = {c.url.rsplit("/", 1)[-1]: c for c in images}
        self.assertEqual(by_url["big-hero.jpg"].intent, "hero")
        self.assertEqual(by_url["big-hero.jpg"].role, "hero")
        self.assertEqual(by_url["inline-story.jpg"].intent, "generic")
        self.assertEqual(by_url["inline-story.jpg"].role, "content")

    def test_evidence_decoration_is_dropped(self):
        soup = BeautifulSoup(
            f"""
            <html>
              <body>
                <img src="/badge.jpg"
                     data-webtree-evidence='{_stamp(nw=320, nh=320, y=300, w=40, h=40)}' />
                <img src="/award-strip.jpg"
                     data-webtree-evidence='{_stamp(nw=2400, nh=200, y=900, w=1200, h=100)}' />
                <img src="/real-photo.jpg"
                     data-webtree-evidence='{_stamp(nw=800, nh=600, y=1200, w=600, h=450)}' />
              </body>
            </html>
            """,
            "lxml",
        )

        images = _extract_images(soup, "https://example.my")

        urls = [c.url.rsplit("/", 1)[-1] for c in images]
        self.assertEqual(urls, ["real-photo.jpg"])

    def test_evidence_natural_size_fills_missing_dimensions(self):
        soup = BeautifulSoup(
            f"""
            <html>
              <body>
                <img src="/no-declared-size.jpg"
                     data-webtree-evidence='{_stamp(nw=1600, nh=900, y=2000, w=700, h=394)}' />
              </body>
            </html>
            """,
            "lxml",
        )

        images = _extract_images(soup, "https://example.my")

        self.assertEqual(images[0].width, 1600)
        self.assertEqual(images[0].height, 900)

    def test_bg_evidence_with_text_is_background_and_promotable_to_hero(self):
        soup = BeautifulSoup(
            f"""
            <html>
              <body>
                <section
                  data-webtree-bg-image="/backdrop.jpg"
                  data-webtree-bg-evidence='{_stamp(y=0, w=1280, h=600, text=85)}'>
                  <h1>Community dental care in Penang</h1>
                </section>
              </body>
            </html>
            """,
            "lxml",
        )

        images = _extract_images(soup, "https://example.my")

        self.assertEqual(images[0].url, "https://example.my/backdrop.jpg")
        self.assertEqual(images[0].role, "background")
        self.assertEqual(images[0].intent, "hero")

    def test_evidence_team_grid_keeps_portrait_role(self):
        # 400px headshots rendered as 112px circles in a 4-up grid.
        cell = _stamp(nw=400, nh=400, y=1800, w=112, h=112, grid=4)
        soup = BeautifulSoup(
            f"""
            <html>
              <body>
                <img src="/aisha.jpg" data-webtree-evidence='{cell}' />
                <img src="/marcus.jpg" data-webtree-evidence='{cell}' />
                <img src="/siti.jpg" data-webtree-evidence='{cell}' />
                <img src="/wei.jpg" data-webtree-evidence='{cell}' />
              </body>
            </html>
            """,
            "lxml",
        )

        images = _extract_images(soup, "https://example.my")

        self.assertEqual(len(images), 4)
        self.assertEqual({c.role for c in images}, {"portrait"})
        self.assertNotIn("hero", {c.intent for c in images})

    def test_no_evidence_keeps_legacy_first_image_hero(self):
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <img src="/first.jpg" alt="storefront" />
                <img src="/second.jpg" alt="our team at work" />
              </body>
            </html>
            """,
            "lxml",
        )

        images = _extract_images(soup, "https://example.my")

        self.assertEqual(images[0].url, "https://example.my/first.jpg")
        self.assertEqual(images[0].intent, "hero")
        self.assertEqual(images[0].role, "unknown")

    def test_profile_extraction_skips_icon_size_rendered_images(self):
        soup = BeautifulSoup(
            f"""
            <html>
              <body>
                <section class="committee">
                  <article class="committee-member">
                    <img src="/icons/linkedin.jpg"
                         data-webtree-evidence='{_stamp(nw=48, nh=48, y=1800, w=24, h=24)}' />
                    <h3>Dr Aisha Rahman</h3>
                    <p class="role">Chairperson</p>
                  </article>
                </section>
              </body>
            </html>
            """,
            "lxml",
        )

        profiles = scraper._extract_profile_candidates(soup, "https://example.my/about")

        self.assertEqual(profiles, [])

    def test_profile_extraction_ignores_content_section_headings(self):
        # An "Our story" content section with an inline photo must not become
        # a team member named "Our story" — neither via the exact blocklist
        # nor via the determiner-led heading path ("Our Community Programmes").
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <section class="story">
                  <h2>Our story</h2>
                  <p>We provide affordable community dental care across Penang.
                  Serving Penang since 1998 with mobile clinics.</p>
                  <img src="/inline-story.jpg" alt="" />
                </section>
                <section class="programmes">
                  <h2>Our Community Programmes</h2>
                  <p>School screenings every first Saturday of the month.</p>
                  <img src="/programme.jpg" alt="" />
                </section>
              </body>
            </html>
            """,
            "lxml",
        )

        profiles = scraper._extract_profile_candidates(soup, "https://example.my")

        self.assertEqual(profiles, [])

    def test_member_detail_page_yields_its_subject(self):
        # A committee member's own page cards nobody: the name is the h1 and
        # the portrait sits loose in the content column, so the card walk finds
        # no container — and never reads an h1 anyway.
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <main>
                  <h1>Dr Aisha Rahman</h1>
                  <div class="entry-content">
                    <img src="/aisha.jpg" alt="" width="400" height="400" />
                    <p>Aisha has served on the committee since 2018 and chairs
                    the education subcommittee. She holds a Masters in Music
                    Therapy and works with children with special needs across
                    the northern region, running weekly group sessions.</p>
                  </div>
                </main>
              </body>
            </html>
            """,
            "lxml",
        )

        profiles = scraper._extract_profile_candidates(soup, "https://example.my/committee/aisha")

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, "Dr Aisha Rahman")
        self.assertEqual(profiles[0].photo_url, "https://example.my/aisha.jpg")
        # Positional pairing, so it ranks below a real card's 0.8.
        self.assertEqual(profiles[0].confidence, 0.75)

    def test_member_detail_page_picks_the_photo_named_after_her(self):
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <main>
                  <h1>Dr Aisha Rahman</h1>
                  <img src="/workshop.jpg" alt="workshop" width="400" height="400" />
                  <img src="/aisha.jpg" alt="Dr Aisha Rahman" width="400" height="400" />
                  <p>Chairs the education subcommittee since 2018.</p>
                </main>
              </body>
            </html>
            """,
            "lxml",
        )

        profiles = scraper._extract_profile_candidates(soup, "https://example.my/committee/aisha")

        self.assertEqual([p.photo_url for p in profiles], ["https://example.my/aisha.jpg"])

    def test_person_page_with_ambiguous_photos_yields_nothing(self):
        # Several photos and no alt naming her: which one is her is a guess.
        soup = BeautifulSoup(
            """
            <html>
              <body>
                <main>
                  <h1>Dr Aisha Rahman</h1>
                  <img src="/workshop.jpg" alt="workshop" width="400" height="400" />
                  <img src="/concert.jpg" alt="concert" width="400" height="400" />
                  <p>Chairs the education subcommittee since 2018.</p>
                </main>
              </body>
            </html>
            """,
            "lxml",
        )

        self.assertEqual(
            scraper._extract_profile_candidates(soup, "https://example.my/committee/aisha"), []
        )

    def test_logo_url_heuristic_matches_filenames_only(self):
        self.assertTrue(scraper._looks_like_logo_url("https://x/assets/logo.png?v=4"))
        self.assertTrue(scraper._looks_like_logo_url("https://x/site-logo.svg"))
        self.assertFalse(scraper._looks_like_logo_url("https://x/logos/partner-wall.jpg"))
        self.assertFalse(scraper._looks_like_logo_url("https://x/assets/contact.jpg"))

    def test_profile_bio_preserves_card_line_boundaries(self):
        # Directory cards pack credentials / specialties / address as separate
        # lines — the bio must keep them as lines (rendered pre-line), not one
        # run-on sentence.
        soup = BeautifulSoup(
            """
            <div class="member">
              <h3>Aisha Rahman</h3>
              <p class="role">Music Therapist</p>
              <ul>
                <li>Children with special needs</li>
                <li>Palliative care</li>
              </ul>
              <p>Home visits (KL and Selangor)</p>
            </div>
            """,
            "lxml",
        )
        container = soup.find("div")

        bio = scraper._extract_profile_bio(container, "Aisha Rahman", "Music Therapist")

        self.assertEqual(
            bio.splitlines(),
            [
                "Children with special needs",
                "Palliative care",
                "Home visits (KL and Selangor)",
            ],
        )

    def test_profile_bio_caps_at_480_chars(self):
        items = "".join(
            f"<li>Specialty line number {i} with enough text to add up</li>"
            for i in range(12)
        )
        soup = BeautifulSoup(
            f'<div class="member"><h3>Aisha Rahman</h3><ul>{items}</ul></div>',
            "lxml",
        )
        container = soup.find("div")

        bio = scraper._extract_profile_bio(container, "Aisha Rahman", None)

        self.assertEqual(len(bio), 480)

    def test_looks_like_person_name_rejects_headings_keeps_names(self):
        rejected = [
            "Our Story",
            "Our Mission",
            "Meet Your Dentists",
            "Why Families Trust Us",
            "What We Do",
            "Get in Touch",
            "Serving Penang since 1998",
            "Book an Appointment",
            "Getting Involved",
            "Good Food",
            "Latest Events",
            "Upcoming Programs",
            "Building Futures",
            "Clean Water",
            "Best Practice",
            # Facility / offering labels. A photo + two capitalised words + a
            # paragraph is the same markup a team card uses, so the trailing
            # noun is the only thing that says "room", not "person". These six
            # are Glorykids' /school-life verbatim: they cleared the old test,
            # hit DIRECTORY_MIN_PROFILES exactly, and coerced the page type to
            # `team` — the programme content never rendered.
            "Innovation Centre",
            "Science Centre",
            "ICT Centre",
            "Domestic-Science Centre",
            "Full Programme :",
            "Basic Programme :",
            "Holiday Workshop",
            "Opening Hours:",
        ]
        accepted = [
            "Dr Aisha Rahman",
            "Marcus Ong",
            "Siti binti Rahman",
            "Jan van der Berg",
            "MARCUS ONG",
            # Surnames that are also facility nouns. The tail set must never
            # grow to cover these — dropping a real person from a roster is the
            # worse failure.
            "Melissa Hall",
            "Andrew Cook",
            "Rachel Park",
            "Colleen Camp",
        ]

        for value in rejected:
            self.assertFalse(scraper._looks_like_person_name(value), value)
        for value in accepted:
            self.assertTrue(scraper._looks_like_person_name(value), value)

    def test_facility_card_grid_is_not_a_profile_roster(self):
        """A room/programme grid must yield no profile candidates.

        End-to-end over the extractor rather than the name predicate alone:
        `page_inference._coerce_directory_type` reads the COUNT, so anything
        that lets these cards through retypes the whole page as `team`.
        """
        cards = "".join(
            f"""
            <div class="card">
              <img src="/img/{n}.jpg" alt="{t}" width="400" height="400">
              <h3>{t}</h3>
              <p>Here, our students will learn and explore together.</p>
            </div>
            """
            for n, t in enumerate(
                [
                    "Innovation Centre",
                    "Science Centre",
                    "ICT Centre",
                    "Domestic-Science Centre",
                    "Full Programme :",
                    "Basic Programme :",
                ]
            )
        )
        soup = BeautifulSoup(f"<html><body>{cards}</body></html>", "lxml")

        found = scraper._extract_profile_candidates(soup, "https://example.com/school-life")

        self.assertEqual([c.name for c in found], [])

    def test_extract_profile_candidates_links_committee_portrait_to_profile_text(self):
        extractor = getattr(scraper, "_extract_profile_candidates", None)
        self.assertIsNotNone(extractor)
        if extractor is None:
            return

        soup = BeautifulSoup(
            """
            <html>
              <body>
                <section class="committee">
                  <article class="committee-member">
                    <img src="/portraits/dr-aisha-rahman.jpg" alt="Dr Aisha Rahman portrait" />
                    <h3>Dr Aisha Rahman</h3>
                    <p class="role">Chairperson</p>
                    <p>Guides clinical governance and community partnerships.</p>
                  </article>
                </section>
              </body>
            </html>
            """,
            "lxml",
        )

        profiles = extractor(soup, "https://example.my/about/committee")

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, "Dr Aisha Rahman")
        self.assertEqual(profiles[0].role, "Chairperson")
        self.assertEqual(
            profiles[0].photo_url,
            "https://example.my/portraits/dr-aisha-rahman.jpg",
        )
        self.assertEqual(profiles[0].source_url, "https://example.my/about/committee")

    def test_extract_profile_candidates_does_not_merge_whole_team_grid(self):
        extractor = getattr(scraper, "_extract_profile_candidates", None)
        self.assertIsNotNone(extractor)
        if extractor is None:
            return

        soup = BeautifulSoup(
            """
            <html>
              <body>
                <section class="team-grid">
                  <div class="card">
                    <img src="/aisha.jpg" alt="Aisha" />
                    <h3>Dr Aisha Rahman</h3>
                    <p>Chairperson</p>
                  </div>
                  <div class="card">
                    <img src="/marcus.jpg" alt="Marcus" />
                    <h3>Marcus Ong</h3>
                    <p>Treasurer</p>
                  </div>
                </section>
              </body>
            </html>
            """,
            "lxml",
        )

        profiles = extractor(soup, "https://example.my/team")

        self.assertEqual([p.name for p in profiles], ["Dr Aisha Rahman", "Marcus Ong"])
        self.assertEqual(
            [p.photo_url for p in profiles],
            ["https://example.my/aisha.jpg", "https://example.my/marcus.jpg"],
        )

    # Portrait in one subtree, name and copy in a sibling — the split-column
    # profile, in the markup families real sites actually ship it as. Matching
    # on `row`/`col`/`grid` class names recognised only the page builders and
    # silently dropped the rest, so the pairing is asserted structurally here:
    # one <img>, one sibling holding the text, no shared card wrapper.
    _SPLIT_COLUMN_LAYOUTS = {
        # Divi: the portrait is buried under a text-less `et_pb_image_wrap`
        # span, and the name sits in an unheaded text module.
        "page_builder": """
            <div class="et_pb_section"><div class="et_pb_row">
              <div class="et_pb_column et_pb_column_2_5">
                <div class="et_pb_module et_pb_image">
                  <span class="et_pb_image_wrap">{img}</span>
                </div>
              </div>
              <div class="et_pb_column et_pb_column_3_5">
                <div class="et_pb_module et_pb_text">Dr Aisha Rahman</div>
                <div class="et_pb_module et_pb_text">{bio}</div>
              </div>
            </div></div>
        """,
        "bootstrap": """
            <div class="row">
              <div class="col-md-4">{img}</div>
              <div class="col-md-8"><h3>Dr Aisha Rahman</h3><p>{bio}</p></div>
            </div>
        """,
        # Flex utilities: no layout words in the class names at all.
        "utility_classes": """
            <div class="flex gap-8">
              <div>{img}</div>
              <div><p>Dr Aisha Rahman</p><p>{bio}</p></div>
            </div>
        """,
        # Hashed CSS-module class names carry no meaning whatsoever.
        "css_modules": """
            <div class="_wrap_1a2b3">
              <div class="_media_9f8">{img}</div>
              <div class="_body_4c1"><p>Dr Aisha Rahman</p><p>{bio}</p></div>
            </div>
        """,
        "semantic_elements": """
            <div class="about-split">
              <figure>{img}</figure>
              <div class="copy"><h3>Dr Aisha Rahman</h3><p>{bio}</p></div>
            </div>
        """,
        "figcaption": "<figure>{img}<figcaption><b>Dr Aisha Rahman</b><p>{bio}</p></figcaption></figure>",
        "table": "<table><tr><td>{img}</td><td><h4>Dr Aisha Rahman</h4><p>{bio}</p></td></tr></table>",
        "definition_list": "<dl><dt>{img}</dt><dd><h4>Dr Aisha Rahman</h4><p>{bio}</p></dd></dl>",
    }

    def test_split_column_layouts_keep_portraits(self):
        img = '<img src="/uploads/aisha.jpg" width="720" height="900" alt="" />'
        bio = (
            "Aisha spent two decades in clinical governance before founding "
            "the community partnerships programme."
        )
        for label, template in self._SPLIT_COLUMN_LAYOUTS.items():
            with self.subTest(layout=label):
                soup = BeautifulSoup(
                    f"<html><body><section>{template.format(img=img, bio=bio)}"
                    "</section></body></html>",
                    "lxml",
                )

                profiles = scraper._extract_profile_candidates(
                    soup, "https://example.my/about"
                )

                self.assertEqual([p.name for p in profiles], ["Dr Aisha Rahman"])
                self.assertEqual(
                    profiles[0].photo_url, "https://example.my/uploads/aisha.jpg"
                )
                self.assertIn("clinical governance", profiles[0].bio or "")

    def test_flat_grid_pairs_each_portrait_with_its_own_copy(self):
        """No per-person wrapper: photo/text/photo/text as flat siblings.

        The pairing has to come from position, and reading backwards would
        caption Marcus's portrait with Aisha's name.
        """
        soup = BeautifulSoup(
            """
            <html><body><section class="team"><div class="grid">
              <div><img src="/aisha.jpg" width="400" height="500" alt="" /></div>
              <div><p>Aisha Rahman</p><p>Aisha chairs the clinical governance committee.</p></div>
              <div><img src="/marcus.jpg" width="400" height="500" alt="" /></div>
              <div><p>Marcus Ong</p><p>Marcus has led community programmes for two decades.</p></div>
            </div></section></body></html>
            """,
            "lxml",
        )

        profiles = scraper._extract_profile_candidates(soup, "https://example.my/team")

        self.assertEqual(
            [(p.name, p.photo_url) for p in profiles],
            [
                ("Aisha Rahman", "https://example.my/aisha.jpg"),
                ("Marcus Ong", "https://example.my/marcus.jpg"),
            ],
        )

    def test_split_section_with_own_heading_is_not_a_person(self):
        """An about split is shaped exactly like a split-column profile.

        What separates them is the h2: a card names one person, a section names
        itself and then talks about something else.
        """
        soup = BeautifulSoup(
            """
            <html><body><section>
              <div class="flex">
                <div><img src="/office.jpg" width="600" height="700" alt="" /></div>
                <div>
                  <h2>Our Story</h2>
                  <p>We have served the community since 1998 with care and dedication.</p>
                </div>
              </div>
            </section></body></html>
            """,
            "lxml",
        )

        self.assertEqual(
            scraper._extract_profile_candidates(soup, "https://example.my/about"), []
        )

    def test_bio_sentence_after_the_name_is_not_a_role(self):
        """Most cards carry no role, and the line after the name is the bio.

        Short, capitalised and contact-free, it clears every other filter and
        would otherwise be printed as a job title under the person's name.
        """
        soup = BeautifulSoup(
            """
            <html><body><section>
              <article class="team-member">
                <img src="/claudia.jpg" width="720" height="900" alt="" />
                <h3>Claudia Lee</h3>
                <p>Her interests include music, reading and travelling.</p>
              </article>
            </section></body></html>
            """,
            "lxml",
        )

        profiles = scraper._extract_profile_candidates(soup, "https://example.my/team")

        self.assertEqual([p.name for p in profiles], ["Claudia Lee"])
        self.assertIsNone(profiles[0].role)
        self.assertIn("interests include music", profiles[0].bio or "")

    def test_role_line_accepts_job_titles_and_rejects_prose(self):
        for title in (
            "Chairperson",
            "Founder & Speaker",
            "Pastor & Mentor",
            "Head of Clinical Services",
            "Senior Consultant, Cardiology",
            "Ph.D.",
        ):
            with self.subTest(title=title):
                self.assertTrue(scraper._looks_like_role_line(title))
        for prose in (
            "Her interests include music, reading and travelling.",
            "He is married to his wife Fiona.",
            "Aisha has led the programme since 1998 and continues to do so today.",
        ):
            with self.subTest(prose=prose):
                self.assertFalse(scraper._looks_like_role_line(prose))

    def test_page_builder_footer_image_is_not_a_person(self):
        """A theme-builder footer is a <div>, so the chrome tag guard misses it.

        Its link column reads exactly like a card's text column to the sibling
        walk — without a class-level footer check the footer image plus the menu
        labels beside it become a "person" named after a nav item.
        """
        soup = BeautifulSoup(
            """
            <html>
              <body class="et-tb-has-footer">
                <div class="et-l et-l--footer">
                  <div class="et_pb_row">
                    <div class="et_pb_column et_pb_column_1_4">
                      <span class="et_pb_image_wrap">
                        <img src="/uploads/promo.jpg" width="771" height="894" alt="" />
                      </span>
                    </div>
                    <div class="et_pb_column et_pb_column_1_4_tb_footer">
                      <div class="et_pb_text">Empowered Work Life</div>
                      <div class="et_pb_text">Explore Life</div>
                      <div class="et_pb_text">Alpha</div>
                    </div>
                  </div>
                </div>
              </body>
            </html>
            """,
            "lxml",
        )

        self.assertEqual(
            scraper._extract_profile_candidates(soup, "https://example.my"), []
        )


class SourceUsageProvenanceTest(unittest.TestCase):
    """An image's source usage (CSS background vs inline <img>) must survive
    extraction — downstream it keeps backgrounds out of side-image slots."""

    def test_plain_img_is_inline(self):
        soup = BeautifulSoup(
            '<html><body><img src="/team.jpg" alt="team" width="1200" height="800" />'
            "</body></html>",
            "lxml",
        )
        images = _extract_images(soup, "https://example.my")
        self.assertEqual(images[0].source_usage, "inline")

    def test_stamped_computed_background_is_css_background(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="hero" data-webtree-bg-image="/computed-hero.jpg">
                <h1>Malaysia clinic</h1>
              </section>
            </body></html>
            """,
            "lxml",
        )
        images = _extract_images(soup, "https://example.my")
        bg = next(i for i in images if i.url.endswith("computed-hero.jpg"))
        self.assertEqual(bg.source_usage, "css_background")

    def test_inline_style_background_is_css_background(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="hero" style="background-image: url('/style-hero.jpg')">
                <h1>Welcome</h1>
              </section>
            </body></html>
            """,
            "lxml",
        )
        images = _extract_images(soup, "https://example.my")
        bg = next(i for i in images if i.url.endswith("style-hero.jpg"))
        self.assertEqual(bg.source_usage, "css_background")

    def test_style_block_background_is_css_background(self):
        soup = BeautifulSoup(
            """
            <html><head>
              <style>.hero { background-image: url('/css-hero.jpg'); }</style>
            </head><body><h1>Welcome</h1></body></html>
            """,
            "lxml",
        )
        images = _extract_images(soup, "https://example.my")
        bg = next(i for i in images if i.url.endswith("css-hero.jpg"))
        self.assertEqual(bg.source_usage, "css_background")

    def test_hero_promotion_preserves_provenance(self):
        # A stamped, above-fold CSS background wins the measured-hero promotion
        # but must still be flagged as a background.
        soup = BeautifulSoup(
            f"""
            <html><body>
              <section data-webtree-bg-image="/bg-hero.jpg"
                       data-webtree-bg-evidence='{{"x": 0, "y": 0, "w": 1280, "h": 640,
                           "vw": 1280, "vh": 800, "text": 120}}'>
                <h1>A headline rendered over the background image, long enough.</h1>
              </section>
            </body></html>
            """,
            "lxml",
        )
        images = _extract_images(soup, "https://example.my")
        bg = next(i for i in images if i.url.endswith("bg-hero.jpg"))
        self.assertEqual(bg.source_usage, "css_background")
        self.assertEqual(bg.intent, "hero")  # promoted by evidence

    def test_source_content_metadata_carries_source_usage(self):
        parsed = scraper._parse_rendered_html(
            """
            <html><head><title>Clinic</title></head><body>
              <section class="hero" data-webtree-bg-image="/computed-hero.jpg">
                <h1>Malaysia clinic</h1>
              </section>
              <img src="/team.jpg" alt="our team" width="1200" height="800" />
              <p>{}</p>
            </body></html>
            """.format("Real page copy. " * 20),
            "https://example.my",
        )
        by_url = {m.url: m for m in parsed.source_content.image_metadata}
        self.assertEqual(
            by_url["https://example.my/computed-hero.jpg"].source_usage,
            "css_background",
        )
        self.assertEqual(by_url["https://example.my/team.jpg"].source_usage, "inline")


class ImageContextExtractionTest(unittest.TestCase):
    """Each image candidate carries the nearest preceding heading (and
    figcaption when present) so the planner prompt can tie photos back to the
    source sections they illustrated."""

    def test_img_gets_nearest_preceding_heading(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h2>Science Centre</h2>
              <p>Hands-on experiments.</p>
              <img src="/science.jpg" width="800" height="600" alt="">
              <h2>ICT Centre</h2>
              <img src="/ict.jpg" width="800" height="600" alt="">
            </body></html>
            """,
            "lxml",
        )
        candidates = _extract_images(soup, "https://example.my/school-life")
        by_url = {c.url.rsplit("/", 1)[-1]: c for c in candidates}
        self.assertEqual(by_url["science.jpg"].context_heading, "Science Centre")
        self.assertEqual(by_url["ict.jpg"].context_heading, "ICT Centre")

    def test_figure_caption_is_captured(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <h2>Sports Day</h2>
              <figure>
                <img src="/sports.jpg" width="800" height="600" alt="">
                <figcaption>Annual sports day 2025</figcaption>
              </figure>
            </body></html>
            """,
            "lxml",
        )
        candidates = _extract_images(soup, "https://example.my/school-life")
        sports = next(c for c in candidates if c.url.endswith("sports.jpg"))
        self.assertEqual(sports.caption, "Annual sports day 2025")
        self.assertEqual(sports.context_heading, "Sports Day")

    def test_image_before_any_heading_has_empty_context(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <img src="/lead.jpg" width="800" height="600" alt="">
              <h2>Later Heading</h2>
            </body></html>
            """,
            "lxml",
        )
        candidates = _extract_images(soup, "https://example.my/")
        lead = next(c for c in candidates if c.url.endswith("lead.jpg"))
        self.assertEqual(lead.context_heading, "")


class ProfileCardScopingTest(unittest.TestCase):
    """A profile card must contribute only its OWN person's details.

    The bug these guard: the container fallback used to accept any ancestor
    holding an h2-h5, so on a site without profile class names the whole
    section became "the card" and every line in it became the person's bio.
    """

    def test_bio_drops_cta_and_contact_lines_from_the_card(self):
        soup = BeautifulSoup(
            """
            <div class="member">
              <h3>Aisha Rahman</h3>
              <p class="role">Music Therapist</p>
              <p>Twelve years working with paediatric palliative care teams.</p>
              <p>Call us on +60 4-226 1234</p>
              <p>hello@example.my</p>
              <a href="/team/aisha">Read More</a>
            </div>
            """,
            "lxml",
        )

        bio = scraper._extract_profile_bio(
            soup.find("div"), "Aisha Rahman", "Music Therapist"
        )

        self.assertEqual(
            bio.splitlines(),
            ["Twelve years working with paediatric palliative care teams."],
        )

    def test_bio_keeps_short_factual_card_lines(self):
        # Regression guard for the fix itself: the filter is by line KIND, not
        # length. Directory cards pack credentials as short separate lines.
        soup = BeautifulSoup(
            """
            <div class="member">
              <h3>Aisha Rahman</h3>
              <ul><li>Palliative care</li><li>Children with special needs</li></ul>
            </div>
            """,
            "lxml",
        )

        bio = scraper._extract_profile_bio(soup.find("div"), "Aisha Rahman", None)

        self.assertEqual(
            bio.splitlines(), ["Palliative care", "Children with special needs"]
        )

    def test_role_rejects_cta_and_phone_fallbacks(self):
        soup = BeautifulSoup(
            """
            <div class="member">
              <h3>Aisha Rahman</h3>
              <a href="/team/aisha">Read More</a>
              <p>+60 4-226 1234</p>
              <p>Senior music therapist</p>
            </div>
            """,
            "lxml",
        )

        role = scraper._extract_profile_role(soup.find("div"), "Aisha Rahman")

        self.assertEqual(role, "Senior music therapist")

    def test_no_candidate_when_photo_has_no_card_boundary(self):
        # No profile class names anywhere and no card-shaped wrapper: the old
        # h2-h5 fallback returned this whole section.
        soup = BeautifulSoup(
            """
            <html><body>
              <section>
                <h2>Rahman Wellness</h2>
                <img src="/banner.jpg" />
                <p>Rahman Wellness has served families across Penang since 1998,
                   growing from a single consulting room into a network of
                   community clinics staffed by therapists and social workers.</p>
                <p>Our programmes reach children with special needs, adults in
                   palliative care, and the carers who support them, and we work
                   alongside three local hospitals on referral pathways.</p>
                <p>We keep a sliding-scale fee structure so that cost is never
                   the reason a family goes without care, and we run home visits
                   throughout the island by arrangement.</p>
              </section>
            </body></html>
            """,
            "lxml",
        )

        self.assertEqual(
            scraper._extract_profile_candidates(soup, "https://example.my"), []
        )

    def test_banner_and_logo_images_are_not_portraits(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="team">
                <article class="team-member">
                  <img src="/wide-banner.jpg" width="1600" height="400" />
                  <h3>Aisha Rahman</h3>
                </article>
                <article class="team-member">
                  <img src="/assets/logo.png" width="200" height="200" />
                  <h3>Marcus Ong</h3>
                </article>
              </section>
            </body></html>
            """,
            "lxml",
        )

        self.assertEqual(
            scraper._extract_profile_candidates(soup, "https://example.my"), []
        )

    def test_well_formed_card_still_extracts_all_three_fields(self):
        # Guards against over-tightening.
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="team">
                <article class="team-member">
                  <img src="/portraits/aisha.jpg" alt="Aisha Rahman" />
                  <h3>Aisha Rahman</h3>
                  <p class="role">Music Therapist</p>
                  <p>Leads the paediatric programme.</p>
                </article>
              </section>
            </body></html>
            """,
            "lxml",
        )

        profiles = scraper._extract_profile_candidates(soup, "https://example.my")

        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].name, "Aisha Rahman")
        self.assertEqual(profiles[0].role, "Music Therapist")
        self.assertEqual(profiles[0].bio, "Leads the paediatric programme.")


class LeadingPersonNameTest(unittest.TestCase):
    """Who the page's BODY says it is about, read off the DOM hierarchy.

    A detail page on a template-driven CMS gives its <title> and its h1 to the
    section, not the person — the name is a designated element further down.
    """

    def test_name_below_a_banner_heading_is_the_subject(self):
        # MMTA's committee pages verbatim: the h1 is the section banner and the
        # person is named in a div the markup labels as the name.
        soup = BeautifulSoup(
            """
            <html><body>
              <section class="title_div"><div><h1 class="h_ttl">The Committee</h1></div></section>
              <section>
                <div class="container"><p>The committee members consist of
                   clinicians with diverse backgrounds.</p></div>
                <div class="committee_profile_box row">
                  <div class="col-md-3">
                    <figure class="circle"><img src="/assets/profilephoto/ashley2.jpg" /></figure>
                    <div class="desc_dv">
                      <div class="name">Ashley Jinivon</div>
                      <div class="designation">Treasurer</div>
                    </div>
                  </div>
                </div>
              </section>
            </body></html>
            """,
            "lxml",
        )

        self.assertEqual(scraper._leading_person_name(soup), "Ashley Jinivon")

    def test_heading_named_subject_still_wins_when_it_comes_first(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <article>
                <h1>Aisha Rahman</h1>
                <div class="name">Marcus Ong</div>
              </article>
            </body></html>
            """,
            "lxml",
        )

        self.assertEqual(scraper._leading_person_name(soup), "Aisha Rahman")

    def test_names_in_chrome_are_not_the_subject(self):
        # A footer byline and a nav account label name someone on every page of
        # the site — neither says anything about this one.
        soup = BeautifulSoup(
            """
            <html><body>
              <nav><span class="name">Sandra Cheah</span></nav>
              <header><h2>Aisha Rahman</h2></header>
              <section><h1>Our Services</h1><p>What we do.</p></section>
              <footer><div class="name">Marcus Ong</div></footer>
            </body></html>
            """,
            "lxml",
        )

        self.assertIsNone(scraper._leading_person_name(soup))

    def test_a_wrapper_holding_the_whole_card_does_not_swallow_the_name(self):
        # The outer element is examined first and reads as a paragraph, not a
        # name; the walk continues inward rather than giving up.
        soup = BeautifulSoup(
            """
            <html><body>
              <div class="member-name-card">
                <div class="name">Aisha Rahman</div>
                <p>Aisha leads the paediatric programme and has worked across
                   three hospitals in Penang since 2009.</p>
              </div>
            </body></html>
            """,
            "lxml",
        )

        self.assertEqual(scraper._leading_person_name(soup), "Aisha Rahman")

    def test_page_naming_no_one_has_no_subject(self):
        soup = BeautifulSoup(
            """
            <html><body>
              <section><h1>Membership</h1><p>Join the association.</p></section>
            </body></html>
            """,
            "lxml",
        )

        self.assertIsNone(scraper._leading_person_name(soup))


class ProfileCardLinkTest(unittest.TestCase):
    """The page a roster card points at — the source's own index of who has a
    profile page, and the evidence both the page hierarchy and the rendered
    card's link are built from."""

    ROSTER = """
    <html><body>
      <section class="committee_profile_dv">
        <div class="committee_box">
          <figure class="circle"><img src="/assets/profilephoto/ashley2.jpg" /></figure>
          <div class="desc_dv">
            <div class="name">Ashley Jinivon</div>
            <div class="designation">Treasurer</div>
          </div>
          <div class="badge_dv">
            <a href="https://example.my/profile/ashley" class="badge">badge</a>
            <a href="mailto: ashley@example.my" class="email">email</a>
          </div>
        </div>
      </section>
    </body></html>
    """

    def _one(self, html, url="https://example.my/committee"):
        profiles = scraper._extract_profile_candidates(BeautifulSoup(html, "lxml"), url)
        self.assertEqual(len(profiles), 1)
        return profiles[0]

    def test_card_link_is_captured_and_mail_is_not(self):
        profile = self._one(self.ROSTER)

        self.assertEqual(profile.name, "Ashley Jinivon")
        self.assertEqual(profile.profile_url, "https://example.my/profile/ashley")

    def test_card_with_only_an_email_links_nowhere(self):
        # A directory whose people have no pages of their own — the cards must
        # not become links to something.
        html = self.ROSTER.replace(
            '<a href="https://example.my/profile/ashley" class="badge">badge</a>', ""
        )

        self.assertIsNone(self._one(html).profile_url)

    def test_a_portrait_wrapped_in_its_link_is_read(self):
        html = """
        <html><body>
          <section class="team">
            <article class="team-member">
              <a href="/team/aisha"><img src="/portraits/aisha.jpg" alt="Aisha Rahman" /></a>
              <h3>Aisha Rahman</h3>
            </article>
          </section>
        </body></html>
        """

        self.assertEqual(
            self._one(html, "https://example.my/team").profile_url,
            "https://example.my/team/aisha",
        )

    def test_a_link_back_to_this_page_is_not_a_detail_link(self):
        # The "Back" control on a member's own page points at the roster; it
        # says nothing about where this person's page is.
        html = self.ROSTER.replace(
            'href="https://example.my/profile/ashley"', 'href="/committee"'
        )

        self.assertIsNone(self._one(html).profile_url)

    def test_offsite_and_asset_links_are_not_pages(self):
        for href in ("https://elsewhere.example/ashley", "/files/ashley-cv.pdf"):
            with self.subTest(href=href):
                html = self.ROSTER.replace(
                    'href="https://example.my/profile/ashley"', f'href="{href}"'
                )

                self.assertIsNone(self._one(html).profile_url)


class ProfileCardContactsTest(unittest.TestCase):
    """A card's own email, phone and social links — not just its detail link."""

    def test_card_email_and_social_links_are_captured(self):
        html = """
        <html><body>
          <section class="committee_profile_dv">
            <div class="committee_box">
              <figure class="circle"><img src="/assets/profilephoto/ashley2.jpg" /></figure>
              <div class="desc_dv">
                <div class="name">Ashley Jinivon</div>
                <div class="designation">Treasurer</div>
              </div>
              <div class="badge_dv">
                <a href="mailto: ashley@example.my" class="email">email</a>
                <a href="https://www.linkedin.com/in/ashleyj">LinkedIn</a>
                <a href="https://instagram.com/ashleyj">Instagram</a>
              </div>
            </div>
          </section>
        </body></html>
        """
        profiles = scraper._extract_profile_candidates(
            BeautifulSoup(html, "lxml"), "https://example.my/committee"
        )

        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile.email, "ashley@example.my")
        self.assertEqual(
            profile.social_links,
            [
                ("LinkedIn", "https://www.linkedin.com/in/ashleyj"),
                ("Instagram", "https://instagram.com/ashleyj"),
            ],
        )

    def test_social_share_and_homepage_links_are_not_a_profile(self):
        html = """
        <html><body>
          <section class="committee_profile_dv">
            <div class="committee_box">
              <figure class="circle"><img src="/assets/profilephoto/ashley2.jpg" /></figure>
              <div class="desc_dv">
                <div class="name">Ashley Jinivon</div>
                <div class="designation">Treasurer</div>
              </div>
              <div class="badge_dv">
                <a href="https://facebook.com/sharer/sharer.php?u=x">Share</a>
                <a href="https://twitter.com/">Twitter home</a>
              </div>
            </div>
          </section>
        </body></html>
        """
        profiles = scraper._extract_profile_candidates(
            BeautifulSoup(html, "lxml"), "https://example.my/committee"
        )

        self.assertEqual(profiles[0].social_links, [])


class PageSubjectProfileContactsTest(unittest.TestCase):
    """A solo member page (no card, no grid) still states its own contacts."""

    def test_lone_detail_page_email_and_social_are_attributed_to_the_subject(self):
        html = """
        <html><body>
          <header><a href="mailto:info@example.my">Contact us</a></header>
          <main>
            <h1>Ashley Jinivon</h1>
            <img src="/portraits/ashley.jpg" width="400" height="500" alt="Ashley" />
            <p>Chairs the committee.</p>
            <a href="mailto:ashley@example.my">Email Ashley</a>
            <a href="https://www.linkedin.com/in/ashleyj">LinkedIn</a>
          </main>
          <footer><a href="https://facebook.com/examplemy">Facebook</a></footer>
        </body></html>
        """
        profiles = scraper._extract_profile_candidates(
            BeautifulSoup(html, "lxml"), "https://example.my/member/ashley"
        )

        self.assertEqual(len(profiles), 1)
        profile = profiles[0]
        self.assertEqual(profile.name, "Ashley Jinivon")
        # The header's site-wide mailto and the footer's Facebook are chrome —
        # only the body's own links are this person's.
        self.assertEqual(profile.email, "ashley@example.my")
        self.assertEqual(
            profile.social_links, [("LinkedIn", "https://www.linkedin.com/in/ashleyj")]
        )
