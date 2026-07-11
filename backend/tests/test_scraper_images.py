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
        ]
        accepted = [
            "Dr Aisha Rahman",
            "Marcus Ong",
            "Siti binti Rahman",
            "Jan van der Berg",
            "MARCUS ONG",
        ]

        for value in rejected:
            self.assertFalse(scraper._looks_like_person_name(value), value)
        for value in accepted:
            self.assertTrue(scraper._looks_like_person_name(value), value)

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
