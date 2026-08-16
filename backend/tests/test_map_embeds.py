"""Source map embeds: parsing, extraction, injection, and rendering.

The pipeline was blind to embedded maps. brightkids' /esp/franchise-opportunity.php
frames a Google Map of its HQ; the generated page carried none, because the only
iframe whitelist was the YouTube/Vimeo one and a map URL fell straight through
it. These tests cover the whole path — URL → DOM → SourceContent → MapBlock →
BuilderElement — plus the rules that keep it honest: the provider whitelist, the
guarantee that the LLM can never author one, and the deference to an authored
locations section so no page ships two maps of the same place.

Sibling of test_video_embeds.py, deliberately: the two features share one DOM
walk (``scraper._extract_embeds``) and one injection spine
(``services.source_injection``), so the suites are structured alike and a change
that breaks the shared half fails in both.
"""

import asyncio
import unittest

from bs4 import BeautifulSoup

from app.models.content_blocks import (
    DETERMINISTIC_SECTION_KINDS,
    ContactBlock,
    CtaBlock,
    HeroBlock,
    LocationItem,
    LocationsBlock,
    MapBlock,
    MapEmbed,
    MapItem,
    PagePlan,
    SourceContent,
)
from app.models.industry import PageScaffold
from app.routers.generate import _inject_maps
from app.services.map_embed import parse_map_src
from app.services.page_inference import _sections_for
from app.services.scaffold_enforcement import align_page_to_scaffold
from app.services.scraper import _extract_embeds, _extract_maps
from app.services.section_content import _map_content, select_template
from app.services.seo import extract_video_items
from app.services.template_filler import fill_template

FR = "https://www.brightkids.com.my/esp/franchise-opportunity.php"

# The exact src on brightkids' franchise page — the bug this feature fixes.
BRIGHTKIDS_PB = (
    "https://www.google.com/maps/embed?pb=!1m14!1m8!1m3!1d3983.532757991638"
    "!2d101.62741600000001!3d3.2165489999999997!3m2!1i1024!2i768!4f13.1!3m3!1m2"
    "!1s0x31cc466e0167ea45%3A0xa29b85f66d526668!2sBright+Kids+HQ!5e0!3m2!1sen"
    "!2smy!4v1416634492186"
)


# --- fixtures ------------------------------------------------------------------


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _embed(i: int, title: str = "", heading: str = "") -> MapEmbed:
    return MapEmbed(
        provider="google",
        embed_url=f"https://maps.google.com/maps?q=Branch+{i}&output=embed",
        title=title,
        context_heading=heading,
    )


def _source(url_path: str, embeds: list[MapEmbed], **kw) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref=f"https://x.test{url_path}",
        raw_text="",
        url_path=url_path,
        map_embeds=embeds,
        **kw,
    )


def _page(slug: str, *, blocks=None) -> PagePlan:
    return PagePlan(
        page_type="contact",
        slug=slug,
        title="Franchise",
        description="",
        is_homepage=slug == "",
        blocks=blocks
        if blocks is not None
        else [
            HeroBlock(headline="Franchise", primary_cta_label="Go", primary_cta_href="/"),
            CtaBlock(
                headline="Talk to us",
                cta_label="Enquire",
                cta_href="/contact",
                background_query="bright room",
            ),
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


class MapSrcParsingTest(unittest.TestCase):
    def test_the_brightkids_embed_survives_verbatim(self):
        """The `pb` payload encodes the owner's exact pin, zoom and place id."""
        parsed = parse_map_src(BRIGHTKIDS_PB, FR)
        assert parsed is not None
        self.assertEqual(parsed.provider, "google")
        self.assertEqual(parsed.embed_url, BRIGHTKIDS_PB)

    def test_pb_payload_is_never_rebuilt_from_parts(self):
        """Rebuilding would silently move the map somewhere near, not on, the pin."""
        parsed = parse_map_src(BRIGHTKIDS_PB, FR)
        assert parsed is not None
        self.assertIn("!1d3983.532757991638", parsed.embed_url)
        self.assertNotIn("output=embed", parsed.embed_url)

    def test_output_embed_viewer_url_passes_through(self):
        parsed = parse_map_src("https://maps.google.com/maps?q=Kepong&output=embed")
        assert parsed is not None
        self.assertEqual(parsed.embed_url, "https://maps.google.com/maps?q=Kepong&output=embed")

    def test_protocol_relative_src_is_promoted(self):
        """Page builders of a certain vintage emit `//maps.google.com/...`."""
        parsed = parse_map_src("//maps.google.com/maps?q=Kepong&output=embed", FR)
        assert parsed is not None
        self.assertTrue(parsed.embed_url.startswith("https://"))

    def test_a_viewer_url_is_rebuilt_into_a_frameable_one(self):
        """Framed as-is Google refuses the connection, so the place is re-searched."""
        parsed = parse_map_src(
            "https://www.google.com/maps/place/Bright+Kids+HQ/@3.2165489,101.627416,17z"
        )
        assert parsed is not None
        self.assertEqual(
            parsed.embed_url, "https://maps.google.com/maps?q=Bright+Kids+HQ&output=embed"
        )
        self.assertEqual(parsed.place, "Bright Kids HQ")

    def test_a_coordinate_only_viewer_url_still_yields_a_map(self):
        parsed = parse_map_src("https://www.google.com/maps/@3.2165489,101.627416,17z")
        assert parsed is not None
        self.assertIn("q=3.2165489%2C101.627416", parsed.embed_url)

    def test_bare_coordinates_never_become_a_caption(self):
        """"3.2165,101.6274" under a map is worse than no caption at all."""
        parsed = parse_map_src("https://www.google.com/maps/@3.2165489,101.627416,17z")
        assert parsed is not None
        self.assertIsNone(parsed.place)

    def test_commas_are_percent_encoded(self):
        """The CMS splits any `src` on top-level commas (MediaUrlResolver::normalize)."""
        parsed = parse_map_src(
            "https://www.google.com/maps/embed/v1/view?key=K&center=3.2,101.6&zoom=17"
        )
        assert parsed is not None
        self.assertNotIn(",", parsed.embed_url)
        self.assertIn("center=3.2%2C101.6", parsed.embed_url)

    def test_a_pasted_iframe_snippet_is_unwrapped(self):
        parsed = parse_map_src(
            f'<iframe src="{BRIGHTKIDS_PB}" width="100%" height="250"></iframe>'
        )
        assert parsed is not None
        self.assertEqual(parsed.embed_url, BRIGHTKIDS_PB)

    def test_openstreetmap_export_widget_is_a_map(self):
        parsed = parse_map_src(
            "https://www.openstreetmap.org/export/embed.html?bbox=1,2,3,4&layer=mapnik"
        )
        assert parsed is not None
        self.assertEqual(parsed.provider, "osm")

    def test_non_maps_are_rejected(self):
        """The whitelist IS the feature — most iframes on a real page are junk."""
        for src in (
            "https://www.google.com/recaptcha/api2/anchor?k=x",
            "https://www.googletagmanager.com/ns.html?id=GTM-ABC",
            "https://www.facebook.com/plugins/page.php?href=x",
            "https://www.youtube.com/embed/dQw4w9WgXcQ",
            "https://player.vimeo.com/video/123456",
            # Not frameable, and not a map: the JS SDK and the static-image API.
            "https://www.google.com/maps/api/staticmap?center=1,2",
            "https://maps.googleapis.com/maps/api/js?key=K",
            # The main OSM viewer sets X-Frame-Options.
            "https://www.openstreetmap.org/#map=17/3.2/101.6",
            "",
            None,
        ):
            with self.subTest(src=src):
                self.assertIsNone(parse_map_src(src))

    def test_lookalike_hosts_are_rejected(self):
        """`google.com` in the netloc is not the same as being google.com."""
        for src in (
            "https://google.com.evil.test/maps?q=x",
            "https://google.com@evil.test/maps?q=x",
            "https://notgoogle.com/maps?q=x",
        ):
            with self.subTest(src=src):
                self.assertIsNone(parse_map_src(src))


# --- 2. DOM extraction ---------------------------------------------------------


class MapExtractionTest(unittest.TestCase):
    def test_the_brightkids_franchise_page_yields_its_map(self):
        html = f"""
        <body><main><section>
          <h2>Bright Kids HQ</h2>
          <iframe src="{BRIGHTKIDS_PB}" width="100%" height="250"
                  frameborder="0" style="border:0"></iframe>
        </section></main></body>
        """
        maps = _extract_maps(_soup(html), FR)
        self.assertEqual(len(maps), 1)
        self.assertEqual(maps[0].embed_url, BRIGHTKIDS_PB)
        # The section's own heading names the map, so it arrives as the GROUP
        # heading (which becomes the section's) rather than a per-tile caption.
        # A lone map under one heading needs no second label repeating it.
        self.assertEqual(maps[0].context_heading, "Bright Kids HQ")
        self.assertEqual(maps[0].title, "")

    def test_percentage_width_is_not_a_hidden_embed(self):
        """`width="100%"` must not parse as a 0-2px tracking pixel."""
        html = f'<body><main><iframe src="{BRIGHTKIDS_PB}" width="100%"></iframe></main></body>'
        self.assertEqual(len(_extract_maps(_soup(html), FR)), 1)

    def test_hidden_and_tracker_frames_are_dropped(self):
        html = f"""
        <body><main>
          <iframe src="https://www.googletagmanager.com/ns.html?id=GTM-A"
                  height="0" width="0" style="display:none"></iframe>
          <iframe src="{BRIGHTKIDS_PB}"></iframe>
          <iframe src="https://maps.google.com/maps?q=Hidden&output=embed"
                  style="display:none"></iframe>
        </main></body>
        """
        maps = _extract_maps(_soup(html), FR)
        self.assertEqual([m.embed_url for m in maps], [BRIGHTKIDS_PB])

    def test_footer_chrome_is_stripped(self):
        """A footer map is the template's; a body map is this page's content."""
        html = f"""
        <body><main><p>Nothing here</p></main>
        <footer><iframe src="{BRIGHTKIDS_PB}"></iframe></footer></body>
        """
        self.assertEqual(_extract_maps(_soup(html), FR), [])

    def test_duplicate_maps_are_deduped(self):
        html = f"""
        <body><main>
          <iframe src="{BRIGHTKIDS_PB}"></iframe>
          <iframe src="{BRIGHTKIDS_PB}"></iframe>
        </main></body>
        """
        self.assertEqual(len(_extract_maps(_soup(html), FR)), 1)

    def test_several_branches_share_a_group_heading(self):
        html = """
        <body><main><section>
          <h2>Our centres</h2>
          <div><h3>Kepong</h3>
            <iframe src="https://maps.google.com/maps?q=Kepong&output=embed"></iframe></div>
          <div><h3>Menjalara</h3>
            <iframe src="https://maps.google.com/maps?q=Menjalara&output=embed"></iframe></div>
        </section></main></body>
        """
        maps = _extract_maps(_soup(html), "https://x.test/")
        self.assertEqual([m.title for m in maps], ["Kepong", "Menjalara"])
        self.assertEqual({m.context_heading for m in maps}, {"Our centres"})

    def test_the_url_names_the_place_when_the_dom_does_not(self):
        html = (
            '<body><main><iframe '
            'src="https://maps.google.com/maps?q=Bright+Kids+HQ&output=embed">'
            "</iframe></main></body>"
        )
        self.assertEqual(_extract_maps(_soup(html), "https://x.test/")[0].title, "Bright Kids HQ")

    def test_a_map_link_is_not_an_embed(self):
        html = '<body><main><a href="https://maps.google.com/maps?q=X">Directions</a></main></body>'
        self.assertEqual(_extract_maps(_soup(html), "https://x.test/"), [])

    def test_empty_page(self):
        self.assertEqual(_extract_maps(_soup("<body><p>hi</p></body>"), "https://x.test/"), [])


class OneWalkTwoWhitelistsTest(unittest.TestCase):
    """Videos and maps come out of the SAME traversal, correctly separated."""

    HTML = f"""
    <body><main><section>
      <h2>About us</h2>
      <iframe src="https://www.youtube.com/embed/qdADYy4L5Kg"></iframe>
      <iframe src="{BRIGHTKIDS_PB}"></iframe>
      <iframe src="https://www.googletagmanager.com/ns.html?id=GTM-A"></iframe>
    </section></main></body>
    """

    def test_each_frame_lands_in_exactly_one_bucket(self):
        embeds = _extract_embeds(_soup(self.HTML), FR)
        self.assertEqual([v.video_id for v in embeds.videos], ["qdADYy4L5Kg"])
        self.assertEqual([m.embed_url for m in embeds.maps], [BRIGHTKIDS_PB])

    def test_a_map_never_parses_as_a_video(self):
        embeds = _extract_embeds(_soup(self.HTML), FR)
        self.assertNotIn(BRIGHTKIDS_PB, [v.embed_url for v in embeds.videos])

    def test_the_convenience_wrappers_agree_with_the_walk(self):
        embeds = _extract_embeds(_soup(self.HTML), FR)
        self.assertEqual(_extract_maps(_soup(self.HTML), FR), embeds.maps)


# --- 3. Injection --------------------------------------------------------------


class MapInjectionTest(unittest.TestCase):
    def test_a_map_section_lands_on_the_page(self):
        # `.php` is stripped from the last segment; the directory is not.
        pages = [_page("esp/franchise-opportunity")]
        source = _source(
            "/",
            [],
            discovered_pages=[
                _source("/esp/franchise-opportunity.php", [_embed(1, "Bright Kids HQ")])
            ],
        )
        _inject_maps(pages, source)
        self.assertEqual([b.kind for b in pages[0].blocks], ["hero", "map", "cta"])

    def test_it_sits_beside_the_contact_section_not_above_it(self):
        """A pin belongs with the address that names it."""
        page = _page(
            "contact",
            blocks=[
                HeroBlock(headline="Contact", primary_cta_label="Go", primary_cta_href="/"),
                ContactBlock(heading="Reach us", email="a@b.test"),
                CtaBlock(
                    headline="Visit",
                    cta_label="Go",
                    cta_href="/",
                    background_query="room",
                ),
            ],
        )
        source = _source("/", [], discovered_pages=[_source("/contact", [_embed(1)])])
        _inject_maps([page], source)
        self.assertEqual([b.kind for b in page.blocks], ["hero", "contact", "map", "cta"])

    def test_an_authored_locations_section_keeps_the_page(self):
        """locations-map-cards already draws a map; two of the same place is a bug."""
        page = _page(
            "contact",
            blocks=[
                HeroBlock(headline="Contact", primary_cta_label="Go", primary_cta_href="/"),
                LocationsBlock(
                    heading="Our centres",
                    items=[LocationItem(name="HQ", address="1 Jalan Ipoh, KL")],
                ),
            ],
        )
        source = _source("/", [], discovered_pages=[_source("/contact", [_embed(1)])])
        _inject_maps([page], source)
        self.assertEqual([b.kind for b in page.blocks], ["hero", "locations"])

    def test_records_sharing_a_slug_accumulate_into_one_block(self):
        """A PHP viewer serves every branch from one path; url_path carries no query."""
        pages = [_page("branch")]
        source = _source(
            "/",
            [],
            discovered_pages=[
                _source("/branch.php", [_embed(1)]),
                _source("/branch.php", [_embed(2)]),
                _source("/branch.php", [_embed(3)]),
            ],
        )
        _inject_maps(pages, source)
        maps = [b for b in pages[0].blocks if b.kind == "map"]
        self.assertEqual(len(maps), 1)
        self.assertEqual(len(maps[0].items), 3)

    def test_a_map_repeated_across_pages_is_kept(self):
        """One address stated on every page is what a one-shopfront business does.

        The inverse of the video rule, and load-bearing: chrome-filtering here
        would delete the map on exactly the sites that state it most clearly.
        """
        pages = [_page("a"), _page("b")]
        shared = _embed(1, "HQ")
        source = _source(
            "/",
            [],
            discovered_pages=[_source("/a", [shared]), _source("/b", [shared])],
        )
        _inject_maps(pages, source)
        for page in pages:
            self.assertIn("map", [b.kind for b in page.blocks])

    def test_two_headed_groups_ship_two_sections(self):
        pages = [_page("branches")]
        source = _source(
            "/",
            [],
            discovered_pages=[
                _source(
                    "/branches",
                    [
                        _embed(1, "Kepong", "Klang Valley"),
                        _embed(2, "Menjalara", "Klang Valley"),
                        _embed(3, "Ipoh", "Perak"),
                    ],
                )
            ],
        )
        _inject_maps(pages, source)
        maps = [b for b in pages[0].blocks if b.kind == "map"]
        self.assertEqual([b.heading for b in maps], ["Klang Valley", "Perak"])
        self.assertEqual([len(b.items) for b in maps], [2, 1])

    def test_a_headless_group_heals_to_the_default(self):
        pages = [_page("x")]
        source = _source("/", [], discovered_pages=[_source("/x", [_embed(1)])])
        _inject_maps(pages, source)
        self.assertEqual([b for b in pages[0].blocks if b.kind == "map"][0].heading, "Find us")

    def test_no_maps_is_a_no_op(self):
        pages = [_page("x")]
        _inject_maps(pages, _source("/", [], discovered_pages=[_source("/x", [])]))
        self.assertEqual([b.kind for b in pages[0].blocks], ["hero", "cta"])

    def test_an_unmatched_slug_is_skipped(self):
        pages = [_page("x")]
        _inject_maps(pages, _source("/", [], discovered_pages=[_source("/nope", [_embed(1)])]))
        self.assertEqual([b.kind for b in pages[0].blocks], ["hero", "cta"])

    def test_no_stock_image_query_is_ever_written(self):
        """There is no stock stand-in for a place. An unpinned map is not a map."""
        pages = [_page("x")]
        _inject_maps(pages, _source("/", [], discovered_pages=[_source("/x", [_embed(1)])]))
        block = [b for b in pages[0].blocks if b.kind == "map"][0]
        self.assertNotIn("query", block.model_dump_json())


# --- 4. The LLM can never author one -------------------------------------------


class MapIsNeverAuthoredTest(unittest.TestCase):
    def test_map_is_a_deterministic_kind(self):
        self.assertIn("map", DETERMINISTIC_SECTION_KINDS)

    def test_the_planner_never_asks_for_one(self):
        from app.services.planner import _scaffolds_to_prompt_payload

        payload = _scaffolds_to_prompt_payload(
            [
                PageScaffold(
                    page_type="contact",
                    slug="contact",
                    title="Contact",
                    sections=["hero", "map", "cta"],
                    is_homepage=False,
                )
            ]
        )
        self.assertEqual(payload[0]["required_sections"], ["hero", "cta"])

    def test_the_model_has_no_schema_for_one(self):
        from app.services.prompts import _SCAFFOLD_BLOCK_SCHEMAS

        self.assertNotIn("map", _SCAFFOLD_BLOCK_SCHEMAS)

    def test_a_volunteered_block_is_discarded(self):
        """A guessed shape validates against the union — dropping it is the guard."""
        page = _page(
            "contact",
            blocks=[
                HeroBlock(headline="C", primary_cta_label="Go", primary_cta_href="/"),
                MapBlock(
                    heading="Find us",
                    items=[MapItem(embed_url="https://maps.google.com/maps?q=Invented")],
                ),
            ],
        )
        aligned = align_page_to_scaffold(
            page,
            PageScaffold(
                page_type="contact",
                slug="contact",
                title="Contact",
                sections=["hero", "map"],
                is_homepage=False,
            ),
        )
        self.assertNotIn("map", [b.kind for b in aligned.blocks])


# --- 5. Section inference ------------------------------------------------------


class MapPageInferenceTest(unittest.TestCase):
    def test_a_page_that_frames_a_map_earns_a_map_section(self):
        page = _source("/franchise-opportunity.php", [_embed(1)])
        self.assertIn("map", _sections_for("services", None, page))

    def test_a_page_with_no_map_does_not(self):
        page = _source("/services", [])
        self.assertNotIn("map", _sections_for("services", None, page))

    def test_a_contact_page_earns_one(self):
        page = _source("/contact", [_embed(1)])
        self.assertIn("map", _sections_for("contact", None, page))


# --- 6. Catalog + rendering ----------------------------------------------------


class MapCatalogTest(unittest.TestCase):
    def _block(self, n: int) -> MapBlock:
        return MapBlock(
            heading="Find us",
            items=[
                MapItem(embed_url=f"https://maps.google.com/maps?q=B{i}&output=embed",
                        title=f"Branch {i}")
                for i in range(n)
            ],
        )

    def test_one_map_picks_the_feature_layout(self):
        template = select_template("map", _map_content(self._block(1)))
        assert template is not None
        self.assertEqual(template["id"], "map-feature")

    def test_several_maps_pick_the_grid(self):
        for n in (2, 3, 4):
            with self.subTest(n=n):
                template = select_template("map", _map_content(self._block(n)))
                assert template is not None
                self.assertEqual(template["id"], "map-grid")

    def test_every_map_template_is_feasible_so_generation_never_raises(self):
        """No `_DISPATCH` fallback exists for `map` — an infeasible template raises."""
        for n in (1, 2, 8):
            with self.subTest(n=n):
                self.assertIsNotNone(select_template("map", _map_content(self._block(n))))

    def test_the_embed_url_reaches_a_video_node_verbatim(self):
        """`video` is the builder's iframe primitive; a map is one of its uses."""
        block = MapBlock(heading="Find us", items=[MapItem(embed_url=BRIGHTKIDS_PB)])
        content = _map_content(block)
        template = select_template("map", content)
        assert template is not None
        element = _fill(template, content)
        frames = [el for el in _walk(element) if el.type == "video"]
        self.assertEqual([f.content.src for f in frames], [BRIGHTKIDS_PB])

    def test_the_frame_gets_an_accessible_name_that_is_not_video(self):
        """The renderer defaults to "Embedded video", which is wrong for a map."""
        block = MapBlock(
            heading="Find us",
            items=[MapItem(embed_url=BRIGHTKIDS_PB, title="Bright Kids HQ")],
        )
        content = _map_content(block)
        template = select_template("map", content)
        assert template is not None
        element = _fill(template, content)
        frame = next(el for el in _walk(element) if el.type == "video")
        self.assertEqual(frame.content.title, "Map: Bright Kids HQ")

    def test_captions_survive_into_the_tree(self):
        block = self._block(2)
        content = _map_content(block)
        template = select_template("map", content)
        assert template is not None
        element = _fill(template, content)
        captions = [
            el.content.innerText for el in _walk(element) if el.name == "Map Caption"
        ]
        self.assertEqual(captions, ["Branch 0", "Branch 1"])

    def test_no_map_template_uses_display_grid(self):
        """The builder owns the grid — 2Col/3Col only (see check_catalog_contract)."""
        for n in (1, 2):
            content = _map_content(self._block(n))
            template = select_template("map", content)
            assert template is not None
            for el in _walk(_fill(template, content)):
                self.assertNotEqual((el.styles or {}).get("display"), "grid")


class MapIsNotAVideoTest(unittest.TestCase):
    def test_a_map_never_becomes_a_videoobject_in_json_ld(self):
        """VideoObject on a map would be a fabricated fact in <head>."""
        block = MapBlock(heading="Find us", items=[MapItem(embed_url=BRIGHTKIDS_PB)])
        self.assertEqual(extract_video_items([block]), [])


if __name__ == "__main__":
    unittest.main()
