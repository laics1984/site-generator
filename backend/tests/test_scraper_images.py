import unittest

from bs4 import BeautifulSoup

import app.services.scraper as scraper
from app.services.scraper import _extract_images


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
