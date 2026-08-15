"""A section whose content is PICTURES must survive to the generated page.

Award badges, accreditation seals, certification marks, registration bodies,
partner logos, press "as featured in" strips, sponsor walls — one shape, and it
was dropped by three separate layers: extraction filtered the badges out as
decoration, the section tree refused to call a text-free tile a card, and the
page-shape heuristics then read the leftover headings as a narrative and had
the model write prose for a page that had none.

The reproducer is brightkids.com.my/about-awards-and-recognition.php, whose nine
award images came out as three invented paragraphs and one stock photo. These
tests are written per SHAPE rather than per site, because the reproducer is one
instance of something ordinary.
"""

import json
import unittest

from bs4 import BeautifulSoup

from app.models.content_blocks import (
    CtaBlock,
    GalleryBlock,
    GalleryItem,
    HeroBlock,
    PagePlan,
    SourceContent,
)
from app.models.industry import PageScaffold
from app.routers.generate import _inject_image_walls
from app.services import page_inference as pi
from app.services.image_evidence import classify_role, parse_evidence
from app.services.image_urls import descriptive_name_from_url, looks_like_logo_url
from app.services.nav_extraction import strip_chrome_sections
from app.services.scraper import _looks_like_person_name, _parse_rendered_html
from app.services.section_extraction import extract_section_candidates
from app.services.source_path import normalize_source_slug
from app.services.source_router import match_scaffolds_to_pages

BASE = "https://example.com/about-awards.php"


def _sections(html: str, base: str = BASE):
    # person_name is injected the same way scraper._parse_rendered_html does it;
    # without it the classifier's people branch cannot fire at all.
    return extract_section_candidates(
        BeautifulSoup(html, "html.parser"), base, person_name=_looks_like_person_name
    )


def _evidence(**overrides) -> str:
    base = {"nw": 0, "nh": 0, "x": 0, "y": 0, "w": 0, "h": 0, "vw": 1280, "vh": 800}
    base.update(overrides)
    return json.dumps(base)


# --- fixtures: one per real-world shape -----------------------------------------

# The reproducer's own markup. The heading sits in its OWN column, a SIBLING of
# the tiles rather than their parent, which is the layout that made the card
# rack unreachable: the scope walk runs forward from the heading and can never
# reach the container above it. Titles are commented out in the real page, so
# the tiles carry nothing but a picture.
BADGE_WALL_HTML = """
<html><body><section><div class="container">
  <div class="grid grid_12"><h1>Winning Awards</h1></div>
  <div class="grid grid_3"><div class="card"><div class="filter">
    <span><img alt="" src="img/awards/Asia Pacific Top Excellence Brand 2008.jpg"></span>
  </div></div></div>
  <div class="grid grid_3"><div class="card"><div class="filter">
    <span><img alt="" src="img/awards/Excellence.png"></span>
  </div></div></div>
  <div class="grid grid_3"><div class="card"><div class="filter">
    <span><img alt="" src="img/awards/MFA2.png"></span>
  </div></div></div>
  <div class="grid grid_3"><div class="card"><div class="filter">
    <span><img alt="" src="img/awards/KPM.png"></span>
  </div></div></div>
</div></section></body></html>
"""

# Same wall, but the site wrote real alt text. The alt is an authored
# description and must win over every fallback.
BADGE_WALL_WITH_ALT_HTML = """
<html><body><section><div class="container">
  <div class="col"><h2>Our Accreditations</h2></div>
  <div class="col-3"><img alt="ISO 9001 Certified" src="/i/a.png"></div>
  <div class="col-3"><img alt="Halal Certified" src="/i/b.png"></div>
  <div class="col-3"><img alt="MOE Registered" src="/i/c.png"></div>
</div></section></body></html>
"""

BADGE_WALL_FIGCAPTION_HTML = """
<html><body><section><div class="container">
  <div class="col"><h2>Recognised By</h2></div>
  <figure class="col-3"><img src="/i/a.png"><figcaption>Best Bakery 2022</figcaption></figure>
  <figure class="col-3"><img src="/i/b.png"><figcaption>Best Bakery 2023</figcaption></figure>
  <figure class="col-3"><img src="/i/c.png"><figcaption>Best Bakery 2024</figcaption></figure>
</div></section></body></html>
"""

# Prose introducing the wall. The paragraph is the section's, the badges are
# still a rack — one must not swallow the other.
PROSE_PLUS_WALL_HTML = """
<html><body><section><div class="container">
  <div class="col">
    <h2>Awards</h2>
    <p>Over the years our work has been recognised by a number of industry bodies
       across the region, and we are proud of every one of them.</p>
  </div>
  <div class="col-3"><img alt="" src="/i/a.png"></div>
  <div class="col-3"><img alt="" src="/i/b.png"></div>
  <div class="col-3"><img alt="" src="/i/c.png"></div>
</div></section></body></html>
"""

# The single most common awards page: ONE heading, a rack of badges, nothing
# else on the page. Below _TREE_MIN_SECTIONS, so the tree used to lose.
SINGLE_WALL_PAGE_HTML = """
<html><body><section><div class="container">
  <div class="col"><h1>Our Awards</h1></div>
  <div class="col-3"><img alt="" src="/i/a.png"></div>
  <div class="col-3"><img alt="" src="/i/b.png"></div>
  <div class="col-3"><img alt="" src="/i/c.png"></div>
  <div class="col-3"><img alt="" src="/i/d.png"></div>
</div></section></body></html>
"""

# Badges as CSS background-images — a page-builder's styled div, no <img>.
CSS_BACKGROUND_WALL_HTML = """
<html><body><section><div class="container">
  <div class="col"><h2>Partners</h2></div>
  <div class="col-3"><div style="background-image: url('/i/a.png')"></div></div>
  <div class="col-3"><div style="background-image: url('/i/b.png')"></div></div>
  <div class="col-3"><div style="background-image: url('/i/c.png')"></div></div>
</div></section></body></html>
"""

# The guard the body-text rule was written for: a repeated label/value row
# matches a sibling signature just as well as a card rack does. It carries no
# images, so admitting picture-dominated groups must leave it rejected.
SCHEDULE_ROWS_HTML = """
<html><body><section><div class="container">
  <div class="col"><h2>Opening Hours</h2></div>
  <div class="row"><span>Monday</span><span>8:30 am - 3:00 pm</span></div>
  <div class="row"><span>Tuesday</span><span>8:30 am - 3:00 pm</span></div>
  <div class="row"><span>Wednesday</span><span>8:30 am - 3:00 pm</span></div>
  <div class="row"><span>Thursday</span><span>8:30 am - 3:00 pm</span></div>
</div></section></body></html>
"""

# A roster. Feeding more groups into the classifier must not turn people into
# a gallery — the people branch runs first and has to keep winning.
TEAM_ROSTER_HTML = """
<html><body><section><div class="container">
  <div class="col"><h2>Our Team</h2></div>
  <div class="col-3"><img src="/i/1.jpg" width="300" height="300"><h3>Marcus Ong</h3>
    <p>Marcus leads our early-years programme and has taught for eleven years.</p>
    <a href="mailto:marcus@example.com">Email</a></div>
  <div class="col-3"><img src="/i/2.jpg" width="300" height="300"><h3>Siti binti Rahman</h3>
    <p>Siti runs the language studio and mentors our new teaching staff.</p>
    <a href="mailto:siti@example.com">Email</a></div>
  <div class="col-3"><img src="/i/3.jpg" width="300" height="300"><h3>Wei Lin Tan</h3>
    <p>Wei Lin coordinates enrichment across all of our centres nationwide.</p>
    <a href="mailto:weilin@example.com">Email</a></div>
</div></section></body></html>
"""


class BadgeWallBecomesAGalleryTest(unittest.TestCase):
    """Shapes 1-3, 6, 8: a rack of pictures is a gallery section."""

    def test_textless_badge_wall_is_a_gallery_with_every_image(self):
        sections = _sections(BADGE_WALL_HTML)

        self.assertEqual([s.heading for s in sections], ["Winning Awards"])
        wall = sections[0]
        self.assertEqual(wall.card_kind, "gallery")
        self.assertEqual(len(wall.cards), 4)
        self.assertEqual(
            [u.rsplit("/", 1)[-1] for u in wall.image_urls],
            [
                "Asia Pacific Top Excellence Brand 2008.jpg",
                "Excellence.png",
                "MFA2.png",
                "KPM.png",
            ],
        )

    def test_authored_alt_becomes_the_card_title(self):
        wall = _sections(BADGE_WALL_WITH_ALT_HTML)[0]

        self.assertEqual(wall.card_kind, "gallery")
        self.assertEqual(
            [c.title for c in wall.cards],
            ["ISO 9001 Certified", "Halal Certified", "MOE Registered"],
        )

    def test_figcaption_becomes_the_card_title(self):
        wall = _sections(BADGE_WALL_FIGCAPTION_HTML)[0]

        self.assertEqual(wall.card_kind, "gallery")
        self.assertEqual(
            [c.title for c in wall.cards],
            ["Best Bakery 2022", "Best Bakery 2023", "Best Bakery 2024"],
        )

    def test_a_descriptive_filename_is_the_last_resort_title(self):
        wall = _sections(BADGE_WALL_HTML)[0]

        # Named for the award -> usable. Named MFA2/KPM -> nothing to say, and
        # guessing would be worse than leaving the tile untitled.
        self.assertEqual(wall.cards[0].title, "Asia Pacific Top Excellence Brand 2008")
        self.assertEqual([c.title for c in wall.cards[1:]], ["", "", ""])

    def test_intro_prose_survives_alongside_the_wall(self):
        section = _sections(PROSE_PLUS_WALL_HTML)[0]

        self.assertEqual(section.card_kind, "gallery")
        self.assertEqual(len(section.cards), 3)
        self.assertIn("recognised by a number of industry bodies", section.prose)

    def test_css_background_badges_are_found(self):
        wall = _sections(CSS_BACKGROUND_WALL_HTML)[0]

        self.assertEqual(wall.card_kind, "gallery")
        self.assertEqual(
            [u.rsplit("/", 1)[-1] for u in wall.image_urls], ["a.png", "b.png", "c.png"]
        )


class WallDetectionLeavesOtherGroupsAloneTest(unittest.TestCase):
    """The guards the picture rule must not trample."""

    def test_repeated_label_value_rows_are_still_not_cards(self):
        sections = _sections(SCHEDULE_ROWS_HTML)

        # Either the section keeps its prose with no card rack, or it is dropped
        # entirely — what it must never be is a rack of four "cards".
        for section in sections:
            self.assertNotEqual(section.card_kind, "gallery")
            self.assertLessEqual(len(section.cards), 1)

    def test_a_roster_is_still_people(self):
        roster = _sections(TEAM_ROSTER_HTML)[0]

        self.assertEqual(roster.card_kind, "people")
        self.assertEqual(
            [c.title for c in roster.cards],
            ["Marcus Ong", "Siti binti Rahman", "Wei Lin Tan"],
        )


class PageShapeTest(unittest.TestCase):
    """The section list a wall page produces."""

    @staticmethod
    def _source(html: str, url_path: str) -> SourceContent:
        return _parse_rendered_html(
            html, f"https://example.com{url_path}", require_text=False
        ).source_content

    def test_a_wall_heading_is_not_counted_as_a_story_beat(self):
        source = self._source(BADGE_WALL_HTML, "/about-awards.php")

        # One heading with four images used to count as one narrative section.
        self.assertEqual(pi._story_section_count(source), 0)

    def test_single_wall_page_still_lets_the_tree_win(self):
        source = self._source(SINGLE_WALL_PAGE_HTML, "/awards.php")

        # One section is below _TREE_MIN_SECTIONS; an image-led tree overrides
        # the floor, because the recipe has nowhere to put a badge rack.
        self.assertTrue(pi._tree_is_image_led(source.section_candidates))
        self.assertEqual(pi._sections_from_tree(source), ["hero", "gallery", "cta"])

    def test_a_narrative_story_page_keeps_its_story_rhythm(self):
        html = """
        <html><body>
        <section><h2>Our Classrooms</h2><p>Bright, open rooms where the youngest
          children spend their mornings exploring and building together.</p>
          <img src="/i/rooms.jpg" width="900" height="600"></section>
        <section><h2>Our Garden</h2><p>An outdoor space for messy play, growing
          vegetables and running about in the afternoon sunshine.</p>
          <img src="/i/garden.jpg" width="900" height="600"></section>
        <section><h2>Our Kitchen</h2><p>Every meal is cooked on site each morning
          by our own kitchen team, using produce from local suppliers.</p>
          <img src="/i/kitchen.jpg" width="900" height="600"></section>
        </body></html>
        """
        source = self._source(html, "/school-life")

        # One photo per heading is a narrative, not a wall.
        self.assertEqual(pi._story_section_count(source), 3)
        self.assertFalse(pi._tree_is_image_led(source.section_candidates))


class ExtractionKeepsBadgeSizedImagesTest(unittest.TestCase):
    """Fix A: the decoration filters used to delete exactly these images."""

    def test_small_declared_logos_survive_when_they_repeat(self):
        html = """
        <html><body><section><div class="container">
          <div class="col"><h2>As Featured In</h2></div>
          <div class="col-3"><img src="/i/a.png" width="150" height="60"></div>
          <div class="col-3"><img src="/i/b.png" width="150" height="60"></div>
          <div class="col-3"><img src="/i/c.png" width="150" height="60"></div>
          <div class="col-3"><img src="/i/d.png" width="150" height="60"></div>
        </div></section></body></html>
        """
        source = _parse_rendered_html(
            html, "https://example.com/press", require_text=False
        ).source_content

        found = [m.url.rsplit("/", 1)[-1] for m in source.image_metadata]
        self.assertEqual(found, ["a.png", "b.png", "c.png", "d.png"])

    def test_a_lone_small_image_is_still_dropped(self):
        html = """
        <html><body><section><h2>Contact</h2>
          <img src="/i/divider.png" width="150" height="60">
        </section></body></html>
        """
        source = _parse_rendered_html(
            html, "https://example.com/contact", require_text=False
        ).source_content

        self.assertEqual(source.image_metadata, [])

    def test_measured_grid_cell_outranks_the_size_disqualifier(self):
        cell = parse_evidence(_evidence(w=150, h=60, y=900, grid=4))
        assert cell is not None

        self.assertEqual(classify_role(cell), "gallery")

    def test_measured_wide_strip_outside_a_grid_is_still_decoration(self):
        strip = parse_evidence(_evidence(w=1200, h=80, y=900))
        assert strip is not None

        self.assertEqual(classify_role(strip), "decoration")

    def test_a_grid_of_sprites_is_still_decoration(self):
        sprite = parse_evidence(_evidence(w=16, h=16, y=900, grid=6))
        assert sprite is not None

        self.assertEqual(classify_role(sprite), "decoration")

    def test_a_grid_cell_is_never_the_hero(self):
        source = _parse_rendered_html(
            BADGE_WALL_HTML, "https://example.com/about-awards.php", require_text=False
        ).source_content

        self.assertEqual(
            [m.intent for m in source.image_metadata], ["generic"] * 4
        )


class FilenameAndLogoNamingTest(unittest.TestCase):
    def test_descriptive_filenames_only(self):
        cases = {
            "https://e.com/img/awards/Asia Pacific Top Excellence Brand 2008.jpg":
                "Asia Pacific Top Excellence Brand 2008",
            "https://e.com/best-bakery-award-2022.png": "best bakery award 2022",
            "https://e.com/iso-9001-certified.svg": "iso 9001 certified",
            # Nothing a site chose to say — better uncaptioned than guessed.
            "https://e.com/MFA2.png": "",
            "https://e.com/KPM.png": "",
            "https://e.com/IMG_4821.jpg": "",
            "https://e.com/DSC_0001.JPG": "",
            "https://e.com/img3.jpg": "",
            "https://e.com/a3f9c1d24b8e7f60a1b2c3d4e5f6a7b8.jpg": "",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                self.assertEqual(descriptive_name_from_url(url), expected)

    def test_an_award_named_logo_is_not_the_site_logo(self):
        # The file that made an award medal the site's header mark.
        self.assertFalse(
            looks_like_logo_url(
                "https://e.com/img/awards/Logo of the 21st century the prestigious brand.png"
            )
        )
        self.assertFalse(looks_like_logo_url("https://e.com/img/partners/acme-logo.png"))

    def test_real_logo_filenames_still_match(self):
        for url in (
            "https://e.com/assets/logo.png",
            "https://e.com/assets/site-logo.svg",
            "https://e.com/assets/logo@2x.png",
            "https://e.com/img/header-logo-white.svg",
        ):
            with self.subTest(url=url):
                self.assertTrue(looks_like_logo_url(url))


class SourceSlugTest(unittest.TestCase):
    """Fix F: three call sites share one rule, or routing silently breaks."""

    def test_page_extensions_are_stripped_from_the_last_segment_only(self):
        cases = {
            "/about-awards-and-recognition.php": "about-awards-and-recognition",
            "/Media_Interview.php": "media_interview",
            "/services/web-design/": "services/web-design",
            "/news.php/archive.html": "news.php/archive",
            "/brochure.pdf": "brochure.pdf",  # a file, not a page
            "/": "",
            None: "",
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(normalize_source_slug(path), expected)

    def test_a_php_page_still_matches_its_scaffold(self):
        page = SourceContent(
            source_kind="url",
            source_ref="https://example.com/about-awards.php",
            title="Awards",
            raw_text="Winning Awards",
            url_path="/about-awards.php",
        )
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.com/",
            title="Home",
            raw_text="Home",
            discovered_pages=[page],
        )
        scaffold = PageScaffold(
            title="Awards", slug="about-awards", page_type="about", sections=["hero"]
        )

        matched = match_scaffolds_to_pages([scaffold], source)

        # The homepage would be the silent fallback — that is the failure this
        # guards, since every .php page would then be built from home's content.
        self.assertIs(matched["about-awards"], page)


class ChromeSectionsTest(unittest.TestCase):
    """A footer widget on a site with no <footer> tag reads as a section.

    brightkids.com.my has none — its footer is a plain <section> — so
    ``_in_chrome`` cannot see it and "Welcome to Bright Kids" arrived as page
    content. Repetition across the crawl is what gives it away.
    """

    @staticmethod
    def _page(url_path: str | None, html: str) -> SourceContent:
        return SourceContent(
            source_kind="url",
            source_ref=f"https://example.com{url_path or '/'}",
            title="T",
            raw_text="t",
            url_path=url_path,
            section_candidates=_sections(html),
        )

    def test_the_repeated_widget_goes_and_the_page_s_own_wall_stays(self):
        # Every page carries the "Partners" widget; each also has its own wall.
        source = self._page(None, SINGLE_WALL_PAGE_HTML + CSS_BACKGROUND_WALL_HTML)
        awards = self._page("/awards", BADGE_WALL_HTML + CSS_BACKGROUND_WALL_HTML)
        source.discovered_pages = [awards, self._page("/contact", CSS_BACKGROUND_WALL_HTML)]

        strip_chrome_sections(source)

        self.assertEqual([s.heading for s in awards.section_candidates], ["Winning Awards"])
        self.assertEqual([s.heading for s in source.section_candidates], ["Our Awards"])

    def test_a_page_is_never_stripped_to_nothing(self):
        # The same content served at two URLs (an alias, a print view) makes
        # every section look repeated. Keeping it beats an empty page.
        source = self._page(None, BADGE_WALL_HTML)
        alias = self._page("/awards-2", BADGE_WALL_HTML)
        source.discovered_pages = [alias]

        strip_chrome_sections(source)

        self.assertEqual([s.heading for s in alias.section_candidates], ["Winning Awards"])

    def test_a_single_page_crawl_is_left_alone(self):
        source = self._page(None, BADGE_WALL_HTML)

        strip_chrome_sections(source)

        self.assertEqual([s.heading for s in source.section_candidates], ["Winning Awards"])


class InjectImageWallsTest(unittest.TestCase):
    """Fix D: the images are placed, not described."""

    @staticmethod
    def _plan_page(slug: str) -> PagePlan:
        return PagePlan(
            title="Awards",
            slug=slug,
            page_type="about",
            blocks=[
                HeroBlock(headline="Awards", subheadline="", image_query="awards"),
                # What the model produced for the slot: plausible, and wrong.
                GalleryBlock(
                    heading="Our Achievements",
                    items=[GalleryItem(image_query="award trophies on a shelf")],
                ),
                CtaBlock(headline="Visit us", button_text="Contact", button_href="/contact"),
            ],
        )

    def test_the_source_wall_replaces_the_models_gallery(self):
        source_page = _parse_rendered_html(
            BADGE_WALL_HTML, "https://example.com/about-awards.php", require_text=False
        ).source_content
        source_page.url_path = "/about-awards.php"
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.com/",
            title="Home",
            raw_text="Home",
            discovered_pages=[source_page],
        )
        page = self._plan_page("about-awards")

        _inject_image_walls([page], source)

        gallery = page.blocks[1]
        assert isinstance(gallery, GalleryBlock)
        self.assertEqual(gallery.heading, "Winning Awards")
        self.assertEqual(
            [(i.image_url or "").rsplit("/", 1)[-1] for i in gallery.items],
            [
                "Asia Pacific Top Excellence Brand 2008.jpg",
                "Excellence.png",
                "MFA2.png",
                "KPM.png",
            ],
        )
        # A bound URL is what makes this deterministic: nothing downstream
        # resolves a stock photo for a slot that already has its picture.
        self.assertTrue(all(i.image_url for i in gallery.items))
        self.assertEqual(page.blocks[0].kind, "hero")
        self.assertEqual(page.blocks[2].kind, "cta")

    def test_a_page_with_no_wall_is_untouched(self):
        source_page = _parse_rendered_html(
            TEAM_ROSTER_HTML, "https://example.com/team", require_text=False
        ).source_content
        source_page.url_path = "/team"
        source = SourceContent(
            source_kind="url",
            source_ref="https://example.com/",
            title="Home",
            raw_text="Home",
            discovered_pages=[source_page],
        )
        page = self._plan_page("team")
        before = page.blocks[1]

        _inject_image_walls([page], source)

        self.assertIs(page.blocks[1], before)


if __name__ == "__main__":
    unittest.main()
