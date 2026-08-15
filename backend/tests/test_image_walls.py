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
from app.routers.generate import _drop_unbound_gallery_items, _inject_image_walls
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


# A PHP album viewer: the album <h3> sits in its own grid column, a SIBLING of
# the tiles, and each tile is a bare <div> of <a><img width="100%;">. The card
# scan therefore reads the page's SIDEBAR as the card group and demotes the
# album title to a card — so the section tree offers no rack at all, and the
# model can't fill the gap either (width="100%;" parses as 100, below
# source_router's 200px floor, so these photos are never in the prompt).
ALBUM_VIEWER_HTML = """
<html><body>
<section>
  <h1 class="subtitle">PHOTO GALLERY</h1>
  <div class="grid grid_4">
    <h5>Kindergarten Gallery</h5>
    <p><ul><li><a href="gallery-photo.php?id=107">Mid Year Party</a></li></ul></p>
    <p><ul><li><a href="gallery-photo.php?id=106">Teachers Day</a></li></ul></p>
  </div>
  <div class="grid grid_8">
    <div class="grid grid_8"><h3>Mid Year Party</h3></div>
    <div class="grid grid_8">
      <div class="grid grid_2"><a href="p/1.jpg"><img src="photo/107/small/a1.jpg" width="100%;"></a></div>
      <div class="grid grid_2"><a href="p/2.jpg"><img src="photo/107/small/a2.jpg" width="100%;"></a></div>
      <div class="grid grid_2"><a href="p/3.jpg"><img src="photo/107/small/a3.jpg" width="100%;"></a></div>
      <div class="grid grid_2"><a href="p/4.jpg"><img src="photo/107/small/a4.jpg" width="100%;"></a></div>
    </div>
  </div>
</section>
<footer>
  <h4>BRIGHT KIDS GALLERY</h4>
  <div class="grid"><img src="img/footer/f1.jpg"><img src="img/footer/f2.jpg"><img src="img/footer/f3.jpg"></div>
</footer>
</body></html>
"""


def _album_source(album_pages, *, extra_pages=()):
    """A crawl where every album record reports the SAME url_path.

    That collision is the defect under test: gallery-photo.php?id=107 and
    ?id=106 are distinct crawl URLs, but url_path carries no query, so both
    normalize to "gallery-photo" and first-wins indexing dropped all but one.
    """
    discovered = []
    for ref, heading in album_pages:
        html = ALBUM_VIEWER_HTML.replace("Mid Year Party", heading).replace(
            "photo/107/small/a", f"photo/{heading[:3].lower()}/small/a"
        )
        page = _parse_rendered_html(html, ref, require_text=False).source_content
        page.url_path = "/gallery-photo.php"
        discovered.append(page)
    for ref, path, html in extra_pages:
        page = _parse_rendered_html(html, ref, require_text=False).source_content
        page.url_path = path
        discovered.append(page)
    return SourceContent(
        source_kind="url",
        source_ref="https://example.com/",
        title="Home",
        raw_text="Home",
        discovered_pages=discovered,
    )


def _gallery_page(slug="gallery-photo", *, with_slot=False):
    """A gallery page as alignment leaves it.

    By default there is NO gallery block: _sanitize_gallery_block drops the
    model's stock-only gallery, and alignment runs before injection.
    """
    blocks = [HeroBlock(headline="Always Bright Memories", subheadline="", image_query="children")]
    if with_slot:
        blocks.append(
            GalleryBlock(heading="Our Gallery", items=[GalleryItem(image_query="happy children")])
        )
    blocks.append(CtaBlock(headline="Visit us", button_text="Contact", button_href="/contact"))
    return PagePlan(title="Photo Gallery", slug=slug, page_type="gallery", blocks=blocks)


class AlbumIndexGalleryTest(unittest.TestCase):
    """A gallery page whose photos live behind album links still gets them."""

    def test_photos_group_into_albums_by_their_context_heading(self):
        # The fallback for a real album viewer, whose <h3> is a SIBLING of the
        # tiles: section_extraction reads the page's sidebar as the card group
        # and finds no rack, and the model can't help either — width="100%;"
        # parses as 100, under source_router's 200px prompt floor. What is
        # never in doubt is which heading each photo sits under, because
        # scraper._image_context already recorded it.
        from app.routers.generate import _photo_walls_from_metadata

        page = _parse_rendered_html(
            ALBUM_VIEWER_HTML,
            "https://example.com/gallery-photo.php?id=107",
            require_text=False,
        ).source_content

        walls = _photo_walls_from_metadata(page, chrome=set())

        by_heading = {w.heading: w for w in walls}
        self.assertIn("Mid Year Party", by_heading)
        self.assertTrue(
            all("photo/" in u for u in by_heading["Mid Year Party"].image_urls)
        )
        self.assertTrue(all(w.card_kind == "gallery" for w in walls))

    def test_a_group_below_the_album_floor_is_not_an_album(self):
        # Two photos under a heading is an illustrated paragraph, not a rack.
        from app.routers.generate import _MIN_ALBUM_PHOTOS, _photo_walls_from_metadata

        html = ALBUM_VIEWER_HTML
        for i in range(_MIN_ALBUM_PHOTOS, 5):
            html = html.replace(
                f'<div class="grid grid_2"><a href="p/{i}.jpg">'
                f'<img src="photo/107/small/a{i}.jpg" width="100%;"></a></div>',
                "",
            )
        page = _parse_rendered_html(
            html, "https://example.com/gallery-photo.php?id=107", require_text=False
        ).source_content

        walls = _photo_walls_from_metadata(page, chrome=set())

        self.assertNotIn("Mid Year Party", {w.heading for w in walls})

    def test_a_paginated_album_is_not_mistaken_for_chrome(self):
        # Chrome is counted per SLUG. Counting per crawled record would make a
        # paginated album's own photos look repeated — one page's content
        # appearing on one page.
        from app.routers.generate import _repeated_image_urls

        source = _album_source([
            ("https://example.com/gallery-photo.php?id=107", "Mid Year Party"),
            ("https://example.com/gallery-photo.php?id=107&gspg=2", "Mid Year Party"),
        ])

        chrome = _repeated_image_urls(source)

        self.assertEqual([u for u in chrome if "photo/" in u], [])

    def test_every_album_sharing_a_slug_lands_on_the_one_page(self):
        # The regression test for first-wins indexing: 27 of 28 albums were
        # silently dropped, and the surviving slot was overwritten repeatedly.
        source = _album_source([
            ("https://example.com/gallery-photo.php?id=107", "Mid Year Party"),
            ("https://example.com/gallery-photo.php?id=106", "Teachers Day"),
            ("https://example.com/gallery-photo.php?id=104", "Art And Craft"),
        ])
        page = _gallery_page()

        _inject_image_walls([page], source, gallery_section_slugs={"gallery-photo"})

        galleries = [b for b in page.blocks if isinstance(b, GalleryBlock)]
        self.assertEqual(
            [g.heading for g in galleries],
            ["Mid Year Party", "Teachers Day", "Art And Craft"],
        )
        urls = [i.image_url for g in galleries for i in g.items]
        self.assertEqual(len(urls), len(set(urls)))
        self.assertTrue(all(u and "photo/" in u for u in urls))

    def test_the_albums_lead_the_page_and_the_cta_still_closes_it(self):
        # The pictures ARE the page, so they sit under the hero — not below an
        # invented paragraph, and never after the closing CTA.
        source = _album_source([
            ("https://example.com/gallery-photo.php?id=107", "Mid Year Party"),
        ])
        page = _gallery_page()

        _inject_image_walls([page], source, gallery_section_slugs={"gallery-photo"})

        self.assertEqual([b.kind for b in page.blocks], ["hero", "gallery", "cta"])

    def test_a_paginated_album_merges_into_one_block(self):
        # ?id=107 and ?id=107&gspg=2 are two crawl URLs of ONE album.
        source = _album_source([
            ("https://example.com/gallery-photo.php?id=107", "Mid Year Party"),
            ("https://example.com/gallery-photo.php?id=107&gspg=2", "Mid Year Party"),
        ])
        page = _gallery_page()

        _inject_image_walls([page], source, gallery_section_slugs={"gallery-photo"})

        galleries = [b for b in page.blocks if isinstance(b, GalleryBlock)]
        self.assertEqual(len(galleries), 1)
        self.assertEqual(galleries[0].heading, "Mid Year Party")

    def test_the_repeated_footer_strip_is_never_the_gallery(self):
        # The footer strip parses as a perfectly good rack — tiles, no prose,
        # empty alt — and was the ONLY rack the gallery page's tree offered.
        source = _album_source([
            ("https://example.com/gallery-photo.php?id=107", "Mid Year Party"),
            ("https://example.com/gallery-photo.php?id=106", "Teachers Day"),
        ])
        page = _gallery_page()

        _inject_image_walls([page], source, gallery_section_slugs={"gallery-photo"})

        urls = [
            i.image_url
            for b in page.blocks
            if isinstance(b, GalleryBlock)
            for i in b.items
        ]
        self.assertTrue(urls)
        self.assertEqual([u for u in urls if "img/footer" in (u or "")], [])

    def test_a_page_the_scaffold_never_gated_gains_nothing(self):
        # Without the gate, any photo-rich page would grow a gallery nobody
        # asked for — a programmes page with six classroom photos, say.
        source = _album_source([
            ("https://example.com/gallery-photo.php?id=107", "Mid Year Party"),
        ])
        page = _gallery_page()
        before = list(page.blocks)

        _inject_image_walls([page], source, gallery_section_slugs=set())

        self.assertEqual(page.blocks, before)

    def test_an_existing_slot_is_replaced_before_any_splicing(self):
        source = _album_source([
            ("https://example.com/gallery-photo.php?id=107", "Mid Year Party"),
            ("https://example.com/gallery-photo.php?id=106", "Teachers Day"),
        ])
        page = _gallery_page(with_slot=True)

        _inject_image_walls([page], source, gallery_section_slugs={"gallery-photo"})

        self.assertEqual([b.kind for b in page.blocks], ["hero", "gallery", "gallery", "cta"])

    def test_caps_bound_the_blocks_and_the_total_uploads(self):
        from app.routers.generate import _MAX_GALLERY_BLOCKS, _MAX_PAGE_GALLERY_IMAGES

        source = _album_source([
            (f"https://example.com/gallery-photo.php?id={i}", f"Album Number {i}")
            for i in range(_MAX_GALLERY_BLOCKS + 4)
        ])
        page = _gallery_page()

        _inject_image_walls([page], source, gallery_section_slugs={"gallery-photo"})

        galleries = [b for b in page.blocks if isinstance(b, GalleryBlock)]
        self.assertLessEqual(len(galleries), _MAX_GALLERY_BLOCKS)
        total = sum(len(g.items) for g in galleries)
        self.assertLessEqual(total, _MAX_PAGE_GALLERY_IMAGES)

    def test_the_album_name_rides_on_caption_not_title(self):
        # schema_builder._build_gallery matches child pages on item.title, so
        # one album name across nine tiles would link all nine at a child page.
        source = _album_source([
            ("https://example.com/gallery-photo.php?id=107", "Mid Year Party"),
        ])
        page = _gallery_page()

        _inject_image_walls([page], source, gallery_section_slugs={"gallery-photo"})

        gallery = next(b for b in page.blocks if isinstance(b, GalleryBlock))
        self.assertTrue(all(i.caption == "Mid Year Party" for i in gallery.items))
        self.assertTrue(all(not i.title for i in gallery.items))


class UnboundGalleryItemsTest(unittest.TestCase):
    """Where the "no stock in a gallery" guarantee actually becomes true.

    Alignment leaves a gallery alone so _inject_image_walls can fill it in its
    scaffolded position. This runs after that AND after bind_image_refs, which
    is the first moment "this tile has no photo behind it" is knowable — a ref
    the binder rejects (out of range, already used, wrong shape) leaves an item
    with nothing, and it would resolve a Pexels photo at render.
    """

    @staticmethod
    def _page(items):
        return PagePlan(
            title="Photo Gallery",
            slug="gallery-photo",
            page_type="gallery",
            blocks=[
                HeroBlock(headline="Memories", subheadline="", image_query="children"),
                GalleryBlock(heading="Moments", items=items),
                CtaBlock(headline="Visit", button_text="Contact", button_href="/contact"),
            ],
        )

    def test_a_stock_only_gallery_is_dropped(self):
        page = self._page([
            GalleryItem(image_query="happy children playing"),
            GalleryItem(image_query="children painting"),
        ])

        _drop_unbound_gallery_items([page])

        self.assertEqual([b.kind for b in page.blocks], ["hero", "cta"])

    def test_a_ref_that_never_bound_is_dropped_with_the_rest(self):
        page = self._page([
            GalleryItem(image_query="real", image_url="https://x.test/photo/1.jpg"),
            GalleryItem(image_query="rejected ref"),
        ])

        _drop_unbound_gallery_items([page])

        gallery = next(b for b in page.blocks if isinstance(b, GalleryBlock))
        self.assertEqual([i.image_url for i in gallery.items], ["https://x.test/photo/1.jpg"])

    def test_injected_albums_are_untouched(self):
        page = self._page([
            GalleryItem(image_query="album", image_url=f"https://x.test/photo/{i}.jpg")
            for i in range(3)
        ])
        before = [i.image_url for i in page.blocks[1].items]

        _drop_unbound_gallery_items([page])

        gallery = next(b for b in page.blocks if isinstance(b, GalleryBlock))
        self.assertEqual([i.image_url for i in gallery.items], before)

    def test_other_block_kinds_are_never_touched(self):
        page = self._page([GalleryItem(image_query="stock")])

        _drop_unbound_gallery_items([page])

        self.assertEqual([b.kind for b in page.blocks], ["hero", "cta"])


if __name__ == "__main__":
    unittest.main()
