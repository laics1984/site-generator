"""Source video embeds: parsing, extraction, injection, and rendering.

The pipeline was blind to embedded video. brightkids' /esp/gallery-video.php
carries four real YouTube players; the generated page carried none, and filled
the empty space with stock photography under invented headings. These tests
cover the whole path — URL → DOM → SourceContent → VideoBlock → BuilderElement —
plus the two rules that make it safe: the provider whitelist, and the guarantee
that the LLM can never author one of these blocks.
"""

import asyncio
import re
import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.models.content_blocks import (
    DETERMINISTIC_SECTION_KINDS,
    CtaBlock,
    HeroBlock,
    PagePlan,
    SourceContent,
    VideoBlock,
    VideoEmbed,
    VideoItem,
)
from app.models.industry import PageScaffold
from app.routers.generate import _MAX_VIDEO_BLOCKS, _inject_videos
from app.services.page_inference import _sections_for
from app.services.scaffold_enforcement import align_page_to_scaffold
from app.services.scraper import _extract_videos
from app.services.section_content import _video_content, select_template
from app.services.seo import build_structured_data, extract_video_items
from app.services.template_filler import fill_template
from app.services.video_embed import parse_video_src

GV = "https://www.brightkids.com.my/esp/gallery-video.php"


# --- fixtures ------------------------------------------------------------------


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _embed(i: int, title: str = "", heading: str = "") -> VideoEmbed:
    return VideoEmbed(
        provider="youtube",
        video_id=f"vid{i:07d}",
        embed_url=f"https://www.youtube.com/embed/vid{i:07d}",
        thumbnail_url=f"https://i.ytimg.com/vi/vid{i:07d}/hqdefault.jpg",
        title=title,
        context_heading=heading,
    )


def _source(url_path: str, embeds: list[VideoEmbed], **kw) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref=f"https://x.test{url_path}",
        raw_text="",
        url_path=url_path,
        video_embeds=embeds,
        **kw,
    )


def _page(slug: str) -> PagePlan:
    return PagePlan(
        page_type="gallery",
        slug=slug,
        title="Videos",
        description="",
        is_homepage=slug == "",
        blocks=[
            HeroBlock(headline="Videos", primary_cta_label="Go", primary_cta_href="/"),
            CtaBlock(headline="Come see us", cta_label="Visit", cta_href="/contact",
                     background_query="bright room"),
        ],
        seo_title="t",
        seo_description="d",
    )


async def _stub_image(query: str):
    return f"https://images.example/{query}.jpg", "#888888"


def _fill(template, content):
    return asyncio.run(fill_template(template, content, resolve_image=_stub_image))


def _walk(el):
    yield el
    if isinstance(el.content, list):
        for child in el.content:
            yield from _walk(child)


# --- 1. URL canonicalisation + the whitelist -----------------------------------


class VideoSrcParsingTest(unittest.TestCase):
    def test_protocol_relative_embed_resolves(self):
        """The form brightkids' whole video gallery is written in."""
        parsed = parse_video_src("//www.youtube.com/embed/egJMCQkEOHc", GV)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.embed_url, "https://www.youtube.com/embed/egJMCQkEOHc")
        self.assertEqual(parsed.video_id, "egJMCQkEOHc")

    def test_every_youtube_form_canonicalises_to_embed(self):
        for raw in (
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://youtu.be/dQw4w9WgXcQ",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            "https://www.youtube.com/v/dQw4w9WgXcQ",
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ",
            "https://www.youtube.com/shorts/dQw4w9WgXcQ",
        ):
            with self.subTest(raw=raw):
                parsed = parse_video_src(raw)
                self.assertIsNotNone(parsed, raw)
                self.assertEqual(
                    parsed.embed_url, "https://www.youtube.com/embed/dQw4w9WgXcQ"
                )

    def test_a_video_inside_a_playlist_keeps_the_video(self):
        parsed = parse_video_src("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLab")
        self.assertEqual(parsed.embed_url, "https://www.youtube.com/embed/dQw4w9WgXcQ")

    def test_a_bare_playlist_is_rejected(self):
        """`videoseries` is a marker, not an id — the shared TS regex matches it."""
        self.assertIsNone(
            parse_video_src("https://www.youtube.com/embed/videoseries?list=PLabcdef")
        )

    def test_vimeo_forms(self):
        for raw in ("https://vimeo.com/123456789", "https://player.vimeo.com/video/123456789"):
            with self.subTest(raw=raw):
                parsed = parse_video_src(raw)
                self.assertEqual(parsed.provider, "vimeo")
                self.assertEqual(parsed.embed_url, "https://player.vimeo.com/video/123456789")
                self.assertIsNone(parsed.thumbnail_url)

    def test_youtube_carries_a_poster(self):
        parsed = parse_video_src("https://youtu.be/dQw4w9WgXcQ")
        self.assertEqual(
            parsed.thumbnail_url, "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
        )

    def test_pasted_iframe_snippet(self):
        parsed = parse_video_src('<iframe width="560" src="//youtu.be/dQw4w9WgXcQ"></iframe>', GV)
        self.assertEqual(parsed.embed_url, "https://www.youtube.com/embed/dQw4w9WgXcQ")

    def test_non_video_iframes_are_rejected(self):
        """The whitelist IS the feature — these all appear on real pages."""
        for raw in (
            "https://www.googletagmanager.com/ns.html?id=GTM-MX9RMH7G",
            "http://www.facebook.com/plugins/likebox.php?href=x",
            "https://www.google.com/recaptcha/api2/anchor",
            "https://maps.google.com/maps?q=Kepong&output=embed",
            "https://embed.tawk.to/abc/default",
            "https://googleads.g.doubleclick.net/pagead/viewthroughconversion/1",
            "/media/tour.mp4",
            "",
            None,
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(parse_video_src(raw, GV))


class VideoEmbedParityTest(unittest.TestCase):
    """The backend decides what goes in content.src; the renderer parses it.

    If they disagree, a video extracted here silently fails to render there.
    Four copies of these rules exist (webtree-public, the vendored preview port,
    the builder, and now Python), so this asserts the in-repo TS port and the
    Python patterns describe the same providers.
    """

    def test_every_ts_pattern_has_a_python_counterpart(self):
        ts = Path(__file__).resolve().parents[2] / "frontend/src/preview/lib/videoEmbed.ts"
        if not ts.exists():  # pragma: no cover - vendored copy absent
            self.skipTest(f"preview videoEmbed.ts not found at {ts}")
        # The TS builds its patterns from template literals, so a regex `\.`
        # appears in the file as `\\.`. Strip backslashes from both sides and
        # compare the plain host/path fragments.
        source = ts.read_text(encoding="utf-8").replace("\\", "")
        for fragment, sample in (
            ("youtu.be/", "https://youtu.be/dQw4w9WgXcQ"),
            ("youtube(?:-nocookie)?.com/embed/", "https://www.youtube.com/embed/dQw4w9WgXcQ"),
            ("youtube.com/shorts/", "https://www.youtube.com/shorts/dQw4w9WgXcQ"),
            ("youtube.com/watch?", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
            ("youtube.com/v/", "https://www.youtube.com/v/dQw4w9WgXcQ"),
            ("vimeo.com/", "https://vimeo.com/123456789"),
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(
                    fragment, source,
                    f"{fragment} missing from the TS renderer — Python would extract "
                    "a video the renderer cannot play",
                )
                self.assertIsNotNone(
                    parse_video_src(sample),
                    f"TS handles {fragment} but Python does not",
                )

    def test_python_does_not_inherit_the_other_provider_fallback(self):
        """The renderer accepts any https URL; the scraper must not."""
        ts = Path(__file__).resolve().parents[2] / "frontend/src/preview/lib/videoEmbed.ts"
        if ts.exists():
            self.assertIn("'other'", ts.read_text(encoding="utf-8"))
        self.assertIsNone(parse_video_src("https://example.com/whatever"))


# --- 2. DOM extraction ----------------------------------------------------------


class VideoExtractionTest(unittest.TestCase):
    def _gallery_html(self) -> str:
        """The real shape of brightkids' video gallery: caption AFTER the player."""
        cards = "".join(
            f"""
            <div class="grid grid_3"><div class="card">
              <iframe width="100%" src="//www.youtube.com/embed/{vid}"
                      frameborder="0" allowfullscreen></iframe>
              <div class="cap"><h5>{title}</h5></div>
            </div></div>"""
            for vid, title in (
                ("egJMCQkEOHc", "Super ESP Foundation Level"),
                ("k4mbuVLIDa4", "Super ESP Enhancement Program"),
                ("oZcES7ZiZdU", "Super ESP Stage Performance"),
                ("SQWeI3UggWA", "Super ESP Enhancement Level (b)"),
            )
        )
        return f"""
        <html><body><section>
          <h1>VIDEO GALLERY</h1>
          <div class="grid grid_12"><h3>Super ESP</h3></div>
          {cards}
        </section></body></html>"""

    def test_all_four_players_are_found_in_dom_order(self):
        videos = _extract_videos(_soup(self._gallery_html()), GV)
        self.assertEqual(
            [v.video_id for v in videos],
            ["egJMCQkEOHc", "k4mbuVLIDa4", "oZcES7ZiZdU", "SQWeI3UggWA"],
        )

    def test_each_player_keeps_its_OWN_caption(self):
        """The off-by-one guard.

        `_image_context` takes the nearest PRECEDING heading. These captions
        follow their player, so that rule gave every video the previous card's
        title. Shifted captions are worse than none: they mislabel real content.
        """
        videos = _extract_videos(_soup(self._gallery_html()), GV)
        self.assertEqual(
            [v.title for v in videos],
            [
                "Super ESP Foundation Level",
                "Super ESP Enhancement Program",
                "Super ESP Stage Performance",
                "Super ESP Enhancement Level (b)",
            ],
        )

    def test_all_players_share_one_group_heading(self):
        """Grouping must not split one wall into four sections of one."""
        videos = _extract_videos(_soup(self._gallery_html()), GV)
        self.assertEqual({v.context_heading for v in videos}, {"VIDEO GALLERY"})

    def test_trackers_and_widgets_are_not_content(self):
        html = """
        <html><body>
          <noscript><iframe src="https://www.googletagmanager.com/ns.html?id=GTM-X"
                            height="0" width="0" style="display:none;"></iframe></noscript>
          <section>
            <iframe src="http://www.facebook.com/plugins/likebox.php?href=x"></iframe>
            <div><iframe width="560" src="https://www.youtube.com/embed/qdADYy4L5Kg"></iframe></div>
          </section>
        </body></html>"""
        videos = _extract_videos(_soup(html), "https://x.test/")
        self.assertEqual([v.video_id for v in videos], ["qdADYy4L5Kg"])

    def test_a_player_with_no_nearby_heading_gets_no_title(self):
        """An empty title is correct. A borrowed one is a lie about the content."""
        html = """
        <html><body>
          <section><h2>Announcement</h2><p>Unrelated news.</p></section>
          <section><div class="video-container">
            <iframe src="https://www.youtube.com/embed/qdADYy4L5Kg"></iframe>
          </div></section>
        </body></html>"""
        videos = _extract_videos(_soup(html), "https://x.test/")
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0].title, "")
        self.assertEqual(videos[0].context_heading, "")

    def test_generic_youtube_title_attribute_is_ignored(self):
        html = """<html><body><section><div>
          <iframe title="YouTube video player"
                  src="https://www.youtube.com/embed/qdADYy4L5Kg"></iframe>
        </div></section></body></html>"""
        self.assertEqual(_extract_videos(_soup(html), "https://x.test/")[0].title, "")

    def test_a_real_title_attribute_is_used(self):
        html = """<html><body><section><div>
          <iframe title="Our 2024 open day"
                  src="https://www.youtube.com/embed/qdADYy4L5Kg"></iframe>
        </div></section></body></html>"""
        self.assertEqual(
            _extract_videos(_soup(html), "https://x.test/")[0].title, "Our 2024 open day"
        )

    def test_lazy_loaded_src_is_found(self):
        html = """<html><body><section><div>
          <iframe data-src="https://www.youtube.com/embed/qdADYy4L5Kg"></iframe>
        </div></section></body></html>"""
        self.assertEqual(
            _extract_videos(_soup(html), "https://x.test/")[0].video_id, "qdADYy4L5Kg"
        )

    def test_lite_youtube_facade_is_found(self):
        html = """<html><body><section><div>
          <lite-youtube videoid="qdADYy4L5Kg"></lite-youtube>
        </div></section></body></html>"""
        self.assertEqual(
            _extract_videos(_soup(html), "https://x.test/")[0].video_id, "qdADYy4L5Kg"
        )

    def test_hero_background_loop_is_decoration_not_content(self):
        html = """<html><body><section><div>
          <iframe src="https://www.youtube.com/embed/bgLOOPvid?autoplay=1&mute=1&loop=1&controls=0"></iframe>
        </div></section></body></html>"""
        self.assertEqual(_extract_videos(_soup(html), "https://x.test/"), [])

    def test_footer_and_nav_players_are_chrome(self):
        html = """<html><body>
          <section><iframe src="https://www.youtube.com/embed/realVIDEO01"></iframe></section>
          <footer><iframe src="https://www.youtube.com/embed/footerPROMO"></iframe></footer>
        </body></html>"""
        videos = _extract_videos(_soup(html), "https://x.test/")
        self.assertEqual([v.video_id for v in videos], ["realVIDEO01"])

    def test_the_same_video_twice_is_one_video(self):
        html = """<html><body><section>
          <iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe>
          <iframe src="https://youtu.be/dQw4w9WgXcQ"></iframe>
        </section></body></html>"""
        self.assertEqual(len(_extract_videos(_soup(html), "https://x.test/")), 1)

    def test_a_link_to_a_video_is_not_an_embed(self):
        html = """<html><body><section>
          <a href="https://www.youtube.com/watch?v=dQw4w9WgXcQ">Watch on YouTube</a>
        </section></body></html>"""
        self.assertEqual(_extract_videos(_soup(html), "https://x.test/"), [])

    def test_a_page_with_no_video_yields_nothing(self):
        html = "<html><body><section><p>Just words.</p></section></body></html>"
        self.assertEqual(_extract_videos(_soup(html), "https://x.test/"), [])


# --- 3. Injection ---------------------------------------------------------------


class VideoInjectionTest(unittest.TestCase):
    def test_embeds_become_a_video_block_after_the_hero(self):
        page = _page("gallery-video")
        source = _source("/", [], discovered_pages=[
            _source("/gallery-video.php", [_embed(1, "One"), _embed(2, "Two")])
        ])
        _inject_videos([page], source)
        self.assertEqual([b.kind for b in page.blocks], ["hero", "video", "cta"])
        self.assertEqual(
            [i.embed_url for i in page.blocks[1].items],
            [_embed(1).embed_url, _embed(2).embed_url],
        )

    def test_no_stock_query_anywhere_in_the_block(self):
        """The whole point: nothing here can degrade to a stock photo."""
        page = _page("gallery-video")
        _inject_videos([page], _source("/", [], discovered_pages=[
            _source("/gallery-video.php", [_embed(1, "One")])
        ]))
        block = page.blocks[1]
        for item in block.items:
            self.assertFalse(hasattr(item, "image_query"))
        self.assertNotIn("query", block.model_dump_json())

    def test_records_sharing_one_slug_all_land_on_the_one_page(self):
        """A paginated viewer serves ?gspg=2 from the same path.

        Placing each record as it was read overwrote the slot and shipped the
        last one — the bug _inject_image_walls already paid for.
        """
        page = _page("gallery-video")
        source = _source("/", [], discovered_pages=[
            _source("/gallery-video.php", [_embed(1)]),
            _source("/gallery-video.php", [_embed(2)]),
            _source("/gallery-video.php", [_embed(3)]),
        ])
        _inject_videos([page], source)
        videos = [b for b in page.blocks if b.kind == "video"]
        self.assertEqual(len(videos), 1)
        self.assertEqual(len(videos[0].items), 3)

    def test_a_sitewide_video_is_kept_off_every_interior_page(self):
        """A sidebar/footer reel is on MOST of the site, not merely on two pages.

        It still reaches the homepage (see the demotion test below); what the
        chrome rule buys is that it does not repeat on all nine pages.
        """
        slugs = [f"p{i}" for i in range(8)]
        pages = [_page("")] + [_page(s) for s in slugs]
        promo = _embed(9)
        source = _source("/", [promo, _embed(1)], discovered_pages=[
            _source(f"/{s}", [promo]) for s in slugs
        ])
        _inject_videos(pages, source)
        home_items = [
            i.embed_url for b in pages[0].blocks if b.kind == "video" for i in b.items
        ]
        self.assertIn(_embed(1).embed_url, home_items)
        for page in pages[1:]:
            self.assertEqual([b for b in page.blocks if b.kind == "video"], [])

    def test_one_promo_video_on_every_page_survives_on_the_homepage(self):
        """Sitewide video is demoted, never deleted.

        A small business embedding one clip on every page is common, and
        treating it as pure furniture loses the site's ONLY video — the same
        end state ("no videos anywhere") the chrome rule already caused once.
        """
        slugs = [f"p{i}" for i in range(8)]
        promo = _embed(9, "Welcome to our school")
        pages = [_page("")] + [_page(s) for s in slugs]
        pages[0].is_homepage = True
        source = _source("/", [promo], discovered_pages=[
            _source(f"/{s}", [promo]) for s in slugs
        ])
        _inject_videos(pages, source)
        home = [b for b in pages[0].blocks if b.kind == "video"]
        self.assertEqual([i.embed_url for i in home[0].items], [promo.embed_url])
        for page in pages[1:]:
            self.assertEqual([b for b in page.blocks if b.kind == "video"], [])

    def test_an_index_page_may_reshow_its_topic_pages_videos(self):
        """The regression that shipped: every video on the site was deleted.

        brightkids' /gallery-video.php is an index — all 14 of its videos also
        live on the topic page they belong to (/testimony.php, /Super_Brain.php,
        …). At a flat two-slug chrome rule every one of them counted as template
        furniture, so the video gallery generated as hero + cta with no videos
        at all, which is exactly what a "no videos" bug looks like from outside.
        """
        shared = [_embed(i, f"Clip {i}", "VIDEO GALLERY") for i in range(6)]
        others = [_source(f"/filler{i}", []) for i in range(12)]
        source = _source("/", [], discovered_pages=[
            _source("/gallery-video.php", shared),
            _source("/testimony.php", shared[:3]),
            _source("/super-brain.php", shared[3:]),
            *others,
        ])
        pages = [_page("gallery-video"), _page("testimony"), _page("super-brain")]
        _inject_videos(pages, source)
        gallery = [b for b in pages[0].blocks if b.kind == "video"]
        self.assertEqual(len(gallery), 1)
        self.assertEqual(len(gallery[0].items), 6)
        # And the topic pages keep their own copies — those are their content too.
        self.assertEqual(len([b for b in pages[1].blocks if b.kind == "video"]), 1)
        self.assertEqual(len([b for b in pages[2].blocks if b.kind == "video"]), 1)

    def test_the_entry_page_crawled_twice_does_not_look_repeated(self):
        """`/` and `/index.php` normalize to "" and "index" — one page, two slugs.

        That alone pushed every homepage video over a two-slug threshold, which
        is how brightkids' homepage lost its single video as well.
        """
        promo = _embed(1)
        source = _source("/", [promo], discovered_pages=[
            _source("/index.php", [promo]),
            *[_source(f"/p{i}", []) for i in range(6)],
        ])
        pages = [_page("")]
        _inject_videos(pages, source)
        videos = [b for b in pages[0].blocks if b.kind == "video"]
        self.assertEqual([i.embed_url for i in videos[0].items], [promo.embed_url])

    def test_a_long_video_index_is_not_truncated_to_twelve(self):
        page = _page("gallery-video")
        _inject_videos([page], _source("/", [], discovered_pages=[
            _source("/gallery-video.php", [_embed(i, f"Clip {i}") for i in range(14)])
        ]))
        self.assertEqual(len(page.blocks[1].items), 14)

    def test_two_source_groups_stay_two_sections(self):
        page = _page("gallery-video")
        _inject_videos([page], _source("/", [], discovered_pages=[
            _source("/gallery-video.php", [
                _embed(1, "a", "Concerts"), _embed(2, "b", "Concerts"),
                _embed(3, "c", "Open Days"),
            ])
        ]))
        videos = [b for b in page.blocks if b.kind == "video"]
        self.assertEqual([b.heading for b in videos], ["Concerts", "Open Days"])

    def test_ungrouped_videos_get_the_default_heading(self):
        page = _page("gallery-video")
        _inject_videos([page], _source("/", [], discovered_pages=[
            _source("/gallery-video.php", [_embed(1)])
        ]))
        self.assertEqual(page.blocks[1].heading, "Videos")

    def test_no_videos_is_a_noop(self):
        page = _page("gallery-video")
        _inject_videos([page], _source("/", [], discovered_pages=[
            _source("/gallery-video.php", [])
        ]))
        self.assertEqual([b.kind for b in page.blocks], ["hero", "cta"])

    def test_a_source_page_with_no_generated_page_is_skipped(self):
        page = _page("")
        _inject_videos([page], _source("/", [], discovered_pages=[
            _source("/nowhere", [_embed(1)])
        ]))
        self.assertEqual([b.kind for b in page.blocks], ["hero", "cta"])


class VideoTopicRegroupingTest(unittest.TestCase):
    """An index page borrows its sections from the topic pages it aggregates."""

    def _site(self):
        brain = [_embed(i, f"Brain {i}", "Super BRAIN") for i in range(8)]
        press = [_embed(20 + i, f"Press {i}", "Media Interview") for i in range(3)]
        says = [_embed(30 + i, f"Says {i}", "Testimony") for i in range(3)]
        index = [
            e.model_copy(update={"context_heading": "VIDEO GALLERY"})
            for e in [*brain, *press, *says]
        ]
        return _source("/", [], discovered_pages=[
            _source("/gallery-video.php", index),
            _source("/super-brain.php", brain),
            _source("/media-interview.php", press),
            _source("/testimony.php", says),
            *[_source(f"/filler{i}", []) for i in range(10)],
        ])

    def test_a_flat_index_becomes_the_topic_sections(self):
        page = _page("gallery-video")
        _inject_videos([page], self._site())
        blocks = [b for b in page.blocks if b.kind == "video"]
        self.assertEqual(
            [(b.heading, len(b.items)) for b in blocks],
            [("Super BRAIN", 8), ("Media Interview", 3), ("Testimony", 3)],
        )

    def test_regrouping_loses_no_videos(self):
        page = _page("gallery-video")
        _inject_videos([page], self._site())
        placed = sum(len(b.items) for b in page.blocks if b.kind == "video")
        self.assertEqual(placed, 14)

    def test_a_topic_page_does_not_adopt_the_index_heading(self):
        """The rule is symmetric, so the two pages must not relabel each other."""
        page = _page("super-brain")
        _inject_videos([page], self._site())
        blocks = [b for b in page.blocks if b.kind == "video"]
        self.assertEqual([b.heading for b in blocks], ["Super BRAIN"])

    def test_a_page_that_grouped_its_own_videos_keeps_its_grouping(self):
        page = _page("gallery-video")
        source = _source("/", [], discovered_pages=[
            _source("/gallery-video.php", [
                _embed(1, "a", "Concerts"), _embed(2, "b", "Open Days"),
            ]),
            _source("/elsewhere.php", [
                _embed(1, "a", "Something Else"), _embed(2, "b", "Another Thing"),
            ]),
            *[_source(f"/filler{i}", []) for i in range(8)],
        ])
        _inject_videos([page], source)
        blocks = [b for b in page.blocks if b.kind == "video"]
        self.assertEqual([b.heading for b in blocks], ["Concerts", "Open Days"])

    def test_regrouping_declines_rather_than_drop_videos_past_the_cap(self):
        """group_by_heading truncates, so organising must never cost content."""
        topics = [f"Topic {i}" for i in range(_MAX_VIDEO_BLOCKS + 2)]
        per_topic = {t: [_embed(10 * i + j, f"v{j}", t) for j in range(2)]
                     for i, t in enumerate(topics)}
        flat = [
            e.model_copy(update={"context_heading": "ALL VIDEOS"})
            for group in per_topic.values() for e in group
        ]
        source = _source("/", [], discovered_pages=[
            _source("/index-page.php", flat),
            *[_source(f"/t{i}.php", g) for i, g in enumerate(per_topic.values())],
            *[_source(f"/filler{i}", []) for i in range(10)],
        ])
        page = _page("index-page")
        _inject_videos([page], source)
        blocks = [b for b in page.blocks if b.kind == "video"]
        self.assertEqual([b.heading for b in blocks], ["ALL VIDEOS"])
        self.assertEqual(sum(len(b.items) for b in blocks), len(flat))


# --- 4. The LLM can never author one --------------------------------------------


class VideoIsNeverAuthoredTest(unittest.TestCase):
    def test_video_is_a_deterministic_kind(self):
        self.assertIn("video", DETERMINISTIC_SECTION_KINDS)

    def test_the_model_is_not_told_the_page_wants_a_video_section(self):
        from app.services.planner import _scaffolds_to_prompt_payload

        payload = _scaffolds_to_prompt_payload([
            PageScaffold(slug="gallery-video", title="Videos", page_type="gallery",
                         sections=["hero", "video", "cta"], is_homepage=False)
        ])
        self.assertEqual(payload[0]["required_sections"], ["hero", "cta"])

    def test_a_volunteered_video_block_is_discarded(self):
        """The half that does not depend on the model behaving.

        A guessed VideoBlock validates — it is in the ContentBlock union — and
        an invented 11-character id is a *different video*, embedded on a
        client's site under their own caption.
        """
        page = PagePlan(
            page_type="gallery", slug="gallery-video", title="Videos", description="",
            is_homepage=False, seo_title="t", seo_description="d",
            blocks=[
                HeroBlock(headline="Videos", primary_cta_label="Go", primary_cta_href="/"),
                VideoBlock(heading="Our Videos", items=[
                    VideoItem(embed_url="https://www.youtube.com/embed/dQw4w9WgXcQ")
                ]),
            ],
        )
        aligned = align_page_to_scaffold(
            page,
            PageScaffold(slug="gallery-video", title="Videos", page_type="gallery",
                         sections=["hero", "video"], is_homepage=False),
        )
        self.assertEqual([b.kind for b in aligned.blocks], ["hero"])

    def test_the_prompt_carries_no_video_schema(self):
        from app.services.prompts import _SCAFFOLD_BLOCK_SCHEMAS

        self.assertNotIn("video", _SCAFFOLD_BLOCK_SCHEMAS)


# --- 5. Page inference ----------------------------------------------------------


class VideoPageInferenceTest(unittest.TestCase):
    def test_a_video_gallery_asks_for_video_not_an_unfillable_gallery(self):
        page = _source("/esp/gallery-video.php", [_embed(1), _embed(2)])
        self.assertEqual(_sections_for("gallery", None, page), ["hero", "video", "cta"])

    def test_a_gallery_with_real_photos_keeps_its_gallery(self):
        from app.models.content_blocks import ImageMetadata

        page = _source(
            "/gallery.php", [_embed(1)],
            image_metadata=[
                ImageMetadata(url=f"https://x/{i}.jpg", alt="", intent="generic",
                              role="gallery")
                for i in range(6)
            ],
        )
        sections = _sections_for("gallery", None, page)
        self.assertIn("gallery", sections)
        self.assertIn("video", sections)

    def test_a_gallery_with_no_videos_is_untouched(self):
        page = _source("/gallery.php", [])
        self.assertEqual(_sections_for("gallery", None, page), ["hero", "gallery", "cta"])

    def test_a_homepage_video_earns_a_section(self):
        self.assertIn("video", _sections_for("home", None, _source("/", [_embed(1)])))

    def test_video_is_not_woven_into_a_contact_page(self):
        self.assertNotIn(
            "video", _sections_for("contact", None, _source("/contact", [_embed(1)]))
        )


# --- 6. Catalog round-trip ------------------------------------------------------


class VideoCatalogTest(unittest.TestCase):
    def _block(self, n: int) -> VideoBlock:
        return VideoBlock(
            heading="Video Gallery",
            items=[
                VideoItem(embed_url=f"https://www.youtube.com/embed/id{i:07d}",
                          title=f"Clip {i}")
                for i in range(n)
            ],
        )

    def test_one_video_gets_the_feature_layout(self):
        template = select_template("video", _video_content(self._block(1)))
        self.assertEqual(template["id"], "video-feature")

    def test_several_videos_get_the_grid(self):
        """Selection is the catalog's own minItems gate, not Python branching."""
        for n in (2, 4, 9):
            with self.subTest(n=n):
                template = select_template("video", _video_content(self._block(n)))
                self.assertEqual(template["id"], "video-grid")

    def test_fill_emits_real_video_nodes_with_canonical_srcs(self):
        content = _video_content(self._block(4))
        element = _fill(select_template("video", content), content)
        videos = [el for el in _walk(element) if el.type == "video"]
        self.assertEqual(len(videos), 4)
        self.assertEqual(
            [v.content.src for v in videos],
            [f"https://www.youtube.com/embed/id{i:07d}" for i in range(4)],
        )

    def test_captions_survive_to_the_tree(self):
        content = _video_content(self._block(2))
        element = _fill(select_template("video", content), content)
        captions = [
            el.content.innerText for el in _walk(element) if el.name == "Video Caption"
        ]
        self.assertEqual(captions, ["Clip 0", "Clip 1"])

    def test_no_video_template_uses_css_grid(self):
        """Catalog rule: the builder owns the grid (2Col/3Col/flex only)."""
        for n in (1, 4):
            content = _video_content(self._block(n))
            element = _fill(select_template("video", content), content)
            for el in _walk(element):
                self.assertNotEqual((el.styles or {}).get("display"), "grid")


# --- 7. SEO ---------------------------------------------------------------------


class VideoSeoTest(unittest.TestCase):
    def _structured(self, blocks):
        return build_structured_data(
            page_slug="gallery-video", page_title="Videos", page_description="d",
            page_type="gallery", is_homepage=False, site_name="Bright Kids",
            brand_name="Bright Kids", logo_url=None, industry_category=None,
            contact=None, blocks=blocks, breadcrumb_slugs=[],
        ) or []

    def test_video_object_per_item(self):
        block = VideoBlock(heading="Videos", items=[
            VideoItem(embed_url="https://www.youtube.com/embed/abc12345678",
                      title="Open Day",
                      thumbnail_url="https://i.ytimg.com/vi/abc12345678/hqdefault.jpg")
        ])
        videos = [s for s in self._structured([block]) if s["@type"] == "VideoObject"]
        self.assertEqual(len(videos), 1)
        self.assertEqual(videos[0]["name"], "Open Day")
        self.assertEqual(videos[0]["embedUrl"], "https://www.youtube.com/embed/abc12345678")
        self.assertEqual(
            videos[0]["thumbnailUrl"], "https://i.ytimg.com/vi/abc12345678/hqdefault.jpg"
        )

    def test_upload_date_is_never_invented(self):
        block = VideoBlock(heading="Videos", items=[
            VideoItem(embed_url="https://www.youtube.com/embed/abc12345678")
        ])
        for schema in self._structured([block]):
            self.assertNotIn("uploadDate", schema)

    def test_a_vimeo_video_omits_the_thumbnail_rather_than_faking_one(self):
        block = VideoBlock(heading="Videos", items=[
            VideoItem(embed_url="https://player.vimeo.com/video/123456789")
        ])
        videos = [s for s in self._structured([block]) if s["@type"] == "VideoObject"]
        self.assertNotIn("thumbnailUrl", videos[0])

    def test_a_page_with_no_video_emits_none(self):
        self.assertEqual(extract_video_items([]), [])


class OgImageIsAlwaysAnImageTest(unittest.TestCase):
    """B2: `content.src` is not image-specific — a video node holds an embed URL."""

    def test_a_video_node_is_never_the_og_image(self):
        from app.models.builder_schema import BuilderElement, BuilderElementContent
        from app.services.seo import extract_og_image

        video = BuilderElement(
            id="v", name="Video Player", type="video", styles={},
            content=BuilderElementContent(src="https://www.youtube.com/embed/abc12345678"),
        )
        section = BuilderElement(id="s", name="X", type="container", styles={}, content=[video])
        self.assertIsNone(extract_og_image([section]))

    def test_a_real_image_after_a_video_is_still_found(self):
        from app.models.builder_schema import BuilderElement, BuilderElementContent
        from app.services.seo import extract_og_image

        video = BuilderElement(
            id="v", name="Video Player", type="video", styles={},
            content=BuilderElementContent(src="https://maps.google.com/maps?output=embed"),
        )
        image = BuilderElement(
            id="i", name="Photo", type="image", styles={},
            content=BuilderElementContent(src="https://cdn.example/real.jpg"),
        )
        section = BuilderElement(
            id="s", name="X", type="container", styles={}, content=[video, image]
        )
        self.assertEqual(extract_og_image([section]), "https://cdn.example/real.jpg")


if __name__ == "__main__":
    unittest.main()
