"""Copy is inked for the surface it actually sits on — never a hard-coded white.

Two defects shipped white copy on a light hero:

- **The split hero.** The luminance pass froze the hero's band from the
  PLANNER's layout ("background" → a dark photo band) before the hero director
  picked a split template, then wrote a dark `backgroundColor` that the split's
  own light `background` gradient paints over, and recoloured every line white
  for the colour nobody could see: 1.02:1.
- **The photo hero.** The copy scrim was sized from the photo's AVERAGE, and
  every ink on it chosen against a constant assumed backdrop. Stock photography
  puts its brightest region (a white wall, a white shirt) behind the copy: 28 of
  45 measured stock heroes left a white paragraph under 4.5:1.

The fix is one surface model (`section_content._own_fill` / `surface_of`), a
scrim solved for the copy zone (`image_styling._copy_scrim`), the painter
recording what it painted (`BuilderElement.paint_surface`), and one ink rule
(`theme.ink_on`) that every pass uses instead of a literal.
"""

from __future__ import annotations

import io
import re
import unittest
from unittest import mock

from PIL import Image

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.models.content_blocks import (
    AboutBlock,
    CtaBlock,
    FeatureItem,
    FeaturesBlock,
    HeroBlock,
    ImageMetadata,
    PagePlan,
    SitePlan,
)
from app.services import media
from app.services.design_schemes import DESIGN_SCHEMES
from app.services.image_sampling import _measure
from app.services.image_styling import (
    COPY_MIN_CONTRAST,
    COPY_ZONES,
    PhotoLuminance,
    _copy_scrim,
    _split_layers,
    is_edge_fade_layer,
    overlay_alpha,
    photo_background,
    photo_surface_hex,
    relative_luminance,
    text_scrim_gradient,
)
from app.services.pexels import PhotoResult
from app.services.schema_builder import plan_to_site
from app.services.section_content import (
    _LITERAL_WHITE,
    _NO_FILL,
    _own_fill,
    _to_hex,
    apply_luminance_rhythm,
    apply_section_title_inks,
    enforce_text_contrast,
    painted_band,
    section_visual_input_for,
    surface_of,
    SectionVisualInput,
)
from app.services.template_filler import fill_template, get_template
from app.services.theme import _contrast, build_theme, ink_css, ink_on, light_ink

_BRIGHT_CENTRE = PhotoLuminance(
    4, 4,
    (0.05, 0.05, 0.05, 0.05,
     0.05, 0.95, 0.95, 0.05,
     0.05, 0.95, 0.95, 0.05,
     0.05, 0.05, 0.05, 0.05),
)


def _paints(node, theme):
    """The hex a node's written colour (hex or `var(--builder-*)` token) paints."""
    from app.services.section_content import _parse_color

    return _to_hex(_parse_color(node.styles["color"], theme)[0])


def _text(name, inner, **styles):
    return BuilderElement(
        name=name, type="text", styles=styles, content=BuilderElementContent(innerText=inner)
    )


def _box(name, children, **styles):
    return BuilderElement(name=name, type="container", styles=styles, content=children)


class InkOnTest(unittest.TestCase):
    """`ink_on` is the one answer to "what does copy on this surface wear"."""

    def setUp(self):
        self.palette = build_theme("#0284c7").palette

    def test_a_light_surface_gets_the_palettes_dark_ink(self):
        ink = ink_on(self.palette, "#f8fafc")
        self.assertLess(relative_luminance(ink), 0.1)
        self.assertGreaterEqual(_contrast(ink, "#f8fafc"), 4.5)

    def test_a_dark_surface_gets_the_palettes_brand_tinted_light_ink(self):
        """A light build's light ink is its surface tint (the old photo-hero
        intent: "brand-tinted near-white instead of hardcoded #ffffff")."""
        ink = ink_on(self.palette, "#0f172a")
        self.assertEqual(ink, light_ink(self.palette))
        self.assertEqual(ink, self.palette.surface)
        self.assertNotEqual(ink.lower(), "#ffffff")

    def test_a_mid_tone_surface_pushes_the_winning_ink_away_from_it(self):
        """On a mid-tone where the DARK ink reads better, the nudge must darken
        it — the shared `_ensure_contrast_against` steers by the surface's side
        of 0.5 and walked the dark ink toward a mid surface."""
        surface = "#2f8fd0"
        ink = ink_on(self.palette, surface)
        lightest = light_ink(self.palette)
        self.assertGreaterEqual(_contrast(ink, surface), _contrast(lightest, surface))
        self.assertLess(relative_luminance(ink), relative_luminance(surface))

    def test_a_dark_palette_uses_its_own_text_as_light_ink(self):
        palette = build_theme("#7c3aed", color_scheme="dark").palette
        self.assertEqual(light_ink(palette), palette.text)


class CopyScrimTest(unittest.TestCase):
    """The scrim is solved for the region behind the copy, not the frame."""

    def test_the_copy_zone_reads_its_bright_tail_not_the_frame_average(self):
        self.assertGreater(_BRIGHT_CENTRE.behind("center"), 0.9)
        frame_average = sum(_BRIGHT_CENTRE.cells) / len(_BRIGHT_CENTRE.cells)
        self.assertLess(frame_average, 0.3)

    def test_a_bright_copy_zone_gets_a_scrim_the_light_ink_reads_on(self):
        """The Core Learning Centre's hero: a mid average (#AEA296) over a
        near-white centre. The average-sized scrim left the paragraph at
        2.97:1; the solved one clears AA."""
        ink = light_ink(build_theme("#0284c7").palette)
        legacy = _copy_scrim("#AEA296", "#0f172a", anchor="center", luminance=None,
                             ink_hex=ink, wash_alpha_scale=1.0)
        solved = _copy_scrim("#AEA296", "#0f172a", anchor="center",
                             luminance=PhotoLuminance.uniform(0.85), ink_hex=ink,
                             wash_alpha_scale=1.0)
        self.assertGreater(solved.scrim, legacy.scrim)
        self.assertGreaterEqual(_contrast(ink, solved.surface_hex), COPY_MIN_CONTRAST)

    def test_a_dark_copy_zone_keeps_the_light_ramp(self):
        """Measuring never makes a scrim lighter than the frame-average ramp."""
        legacy = _copy_scrim("#3a3a3a", "#0f172a", anchor="center", luminance=None,
                             ink_hex=None, wash_alpha_scale=1.0)
        solved = _copy_scrim("#3a3a3a", "#0f172a", anchor="center",
                             luminance=PhotoLuminance.uniform(0.04), ink_hex=None,
                             wash_alpha_scale=1.0)
        self.assertEqual(solved.scrim, legacy.scrim)

    def test_no_hero_is_darkened_past_the_old_single_sheet(self):
        for scale in (1.0, 0.45):
            plan = _copy_scrim("#f0f0f0", "#0f172a", anchor="center",
                               luminance=PhotoLuminance.unreadable(), ink_hex=None,
                               wash_alpha_scale=scale)
            cover = 1 - (1 - plan.cast) * (1 - plan.scrim)
            self.assertLessEqual(cover, 0.63, scale)

    def test_the_homepage_wash_scale_is_made_up_by_the_scrim(self):
        """A lighter homepage cast used to cost the copy contrast; solved, the
        scrim takes up the difference."""
        ink = light_ink(build_theme("#0284c7").palette)
        bright = PhotoLuminance.uniform(0.8)
        for scale in (1.0, 0.45):
            surface = photo_surface_hex("#a0a0a0", "#0f172a", luminance=bright,
                                        ink_hex=ink, wash_alpha_scale=scale)
            self.assertGreaterEqual(_contrast(ink, surface), COPY_MIN_CONTRAST, scale)

    def test_the_painted_scrim_is_the_planned_scrim(self):
        """`photo_background` (the painter) and `photo_surface_hex` (the
        reader) share one plan, so the CSS carries the solved alpha."""
        lum = PhotoLuminance.uniform(0.9)
        plan = _copy_scrim("#9a9a9a", "#0f172a", anchor="center", luminance=lum,
                           ink_hex=None, wash_alpha_scale=1.0)
        css = photo_background("#9a9a9a", "https://x/p.jpg", "#0f172a", "#0284c7",
                               luminance=lum)["backgroundImage"]
        scrim_layer = next(l for l in _split_layers(css) if l.startswith("radial-gradient(115%"))
        self.assertIn(f"rgba(15,23,42,{plan.scrim}) ", scrim_layer)

    def test_unmeasured_is_the_frame_average_ramp_unchanged(self):
        """PHOTO_SAMPLING_ENABLED=false is a true no-op: no luminance, the
        historical ramp."""
        plain = photo_background("#808080", "https://x/p.jpg", "#221d2b", "#7c3aed")
        scrim_layer = next(
            l for l in _split_layers(plain["backgroundImage"]) if "115% 88%" in l
        )
        self.assertEqual(scrim_layer, text_scrim_gradient("#221d2b", overlay_alpha("#808080")))

    def test_the_scrim_holds_full_strength_across_the_copy(self):
        """A plateau over the whole copy zone: the last word of a line gets the
        backdrop its first word got."""
        x0, y0, x1, y1 = COPY_ZONES["center"]
        css = text_scrim_gradient("#0f172a", 0.2)
        first_stop = float(re.search(r"0\.25\) (\d+)%", css).group(1)) / 100
        corner = (((x1 - 0.5) / 1.15) ** 2 + ((y1 - 0.5) / 0.88) ** 2) ** 0.5
        self.assertGreaterEqual(first_stop, round(corner, 2))

    def test_copy_at_the_bottom_never_sits_on_the_page_fade(self):
        """The edge fade dissolves toward the PAGE colour — the colour light ink
        cannot read on — so it is left out wherever the copy reaches it."""
        for anchor, faded in (("center", True), ("left", True), ("bottom", False), ("bottom-left", False)):
            css = photo_background("#808080", "https://x/p.jpg", "#0f172a", "#0284c7",
                                   anchor=anchor, page_bg_hex="#ffffff")["backgroundImage"]
            self.assertEqual(any(is_edge_fade_layer(l) for l in _split_layers(css)), faded, anchor)


class PhotoSamplingTest(unittest.TestCase):
    def test_a_luminance_map_says_where_the_photo_is_bright(self):
        """Same average, different answer: what matters is whether the bright
        region is where the copy goes."""
        def measured(box):
            img = Image.new("RGB", (240, 120), (10, 10, 10))
            img.paste((250, 250, 250), box)
            buf = io.BytesIO()
            img.save(buf, "PNG")
            return _measure(buf.getvalue()).luminance_map

        centre = measured((60, 30, 180, 90))
        corner = measured((0, 0, 120, 60))  # same area, top-left
        self.assertGreater(centre.behind("center"), 0.9)
        self.assertLess(corner.behind("bottom"), 0.1)


class ResolverLuminanceTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_placeholder_answers_for_itself(self):
        resolver = media.ImageResolver()
        photo = media._placeholder_photo("seed", "landscape", None,
                                          primary_hex="#0284c7", secondary_hex="#0f172a")
        lum = await resolver.photo_luminance(photo)
        self.assertEqual(lum, PhotoLuminance.uniform(relative_luminance(photo.avg_color)))

    async def test_the_kill_switch_keeps_the_frame_average(self):
        resolver = media.ImageResolver()
        with mock.patch.object(settings, "photo_sampling_enabled", False):
            self.assertIsNone(await resolver.photo_luminance("https://x/p.jpg"))

    async def test_an_unreadable_photo_is_assumed_bright_and_read_once(self):
        resolver = media.ImageResolver()
        calls = []

        async def fake_sample(url, client=None):
            calls.append(url)
            return None

        with mock.patch.object(settings, "photo_sampling_enabled", True), \
             mock.patch.object(media, "sample_photo", fake_sample):
            first = await resolver.photo_luminance("https://x/p.jpg")
            await resolver.photo_luminance("https://x/p.jpg")
        self.assertEqual(first, PhotoLuminance.unreadable())
        self.assertEqual(calls, ["https://x/p.jpg"])


class FillerPaintsAndRecordsTest(unittest.IsolatedAsyncioTestCase):
    """The filler measures every photo it paints behind copy and records the
    surface that leaves."""

    async def _fill(self, template_id, content):
        measured = []

        async def resolve(query):
            return "https://x/p.jpg", "#9a9a9a"

        async def measure(url):
            measured.append(url)
            return _BRIGHT_CENTRE

        el = await fill_template(
            get_template(template_id), content, resolve_image=resolve,
            theme={"primary": "#0284c7", "secondary": "#0f172a", "background": "#ffffff",
                   "ink": "#f0f9ff"},
            measure_photo=measure,
        )
        return el, measured

    async def test_a_section_photo_records_its_surface_and_keeps_its_fade(self):
        el, measured = await self._fill("cta-background", {
            "heading": "Come and see us", "body": "Visits every weekday.",
            "image": {"query": "classroom"},
            "primary_cta": {"innerText": "Book", "href": "/contact"},
        })
        self.assertEqual(measured, ["https://x/p.jpg"])
        self.assertIsNotNone(el.painted_surface)
        self.assertGreaterEqual(_contrast("#f0f9ff", el.painted_surface), COPY_MIN_CONTRAST)
        layers = _split_layers(el.styles["backgroundImage"])
        self.assertTrue(any(is_edge_fade_layer(l) for l in layers))

    async def test_a_bento_tile_never_fades_to_the_page_under_its_title(self):
        """The tile's title sits on its bottom edge. The page dissolve every
        photo used to get put opaque page colour exactly there."""
        el, _ = await self._fill("features-bento-photo", {
            "heading": "Why us",
            "items": [{"title": "Small classes", "description": "Known by name",
                       "image": {"query": "kids"}}] * 3,
        })
        tiles = [n for n in _walk(el) if n.name == "Bento Tile"]
        self.assertTrue(tiles)
        for tile in tiles:
            layers = _split_layers(tile.styles["backgroundImage"])
            self.assertFalse(any(is_edge_fade_layer(l) for l in layers))
            self.assertTrue(any(l.startswith("linear-gradient(to top") for l in layers))
            self.assertIsNotNone(tile.painted_surface)


class SurfaceModelTest(unittest.TestCase):
    def setUp(self):
        self.theme = build_theme("#0284c7")

    def test_a_background_shorthand_hides_the_colour_it_resets(self):
        """The split hero: a dark backgroundColor under a light `background`
        gradient. The visitor sees the gradient."""
        hero = _box(
            "Hero - Modern Split", [],
            backgroundColor="#0f172a",
            background=(
                "linear-gradient(180deg, color-mix(in srgb, var(--builder-color-surface, "
                "#f8fafc) 95%, transparent), var(--builder-color-background, #ffffff))"
            ),
        )
        self.assertGreater(relative_luminance(surface_of(hero, self.theme)), 0.8)
        self.assertEqual(painted_band(hero, self.theme), "light")

    def test_a_painted_surface_is_the_answer_outright(self):
        hero = _box("Hero", [], backgroundImage="url(https://x/p.jpg)", backgroundColor="#ffffff")
        self.assertIsNone(surface_of(hero, self.theme))  # a photo nobody measured
        hero.paint_surface("#2b2e36")
        self.assertEqual(surface_of(hero, self.theme), "#2b2e36")
        self.assertEqual(painted_band(hero, self.theme), "dark")

    def test_a_flat_section_is_not_self_painted(self):
        band = _box("About", [], backgroundColor="#ffffff")
        self.assertIsNone(painted_band(band, self.theme))
        self.assertIs(_own_fill(_box("x", []), self.theme, (255, 255, 255)), _NO_FILL)


class InkPassTest(unittest.TestCase):
    def setUp(self):
        self.theme = build_theme("#0284c7")

    def test_white_copy_on_a_light_template_gradient_becomes_dark(self):
        """The reported bug, at the ink pass."""
        heading = _text("Heading", "Where curious minds grow", color="#ffffff")
        body = _text("Body", "Small classes.", color="rgba(255,255,255,0.92)")
        hero = _box(
            "Hero - Modern Split", [heading, body],
            backgroundColor="#0f172a",
            background="linear-gradient(180deg, #f0f9ff, #ffffff)",
        )
        enforce_text_contrast([hero], self.theme)
        surface = surface_of(hero, self.theme)
        for node in (heading, body):
            self.assertGreaterEqual(_contrast(_paints(node, self.theme), surface), 4.5)

    def test_no_literal_white_survives_on_a_known_surface(self):
        heading = _text("Heading", "Hi", color="#ffffff")
        hero = _box("Hero", [heading], backgroundImage="url(https://x/p.jpg)")
        hero.paint_surface("#2b2e36")
        enforce_text_contrast([hero], self.theme)
        self.assertIsNone(_LITERAL_WHITE.match(heading.styles["color"]))
        # Written as the palette's ink TOKEN, so a palette edit in the builder
        # carries it along; its fallback is the palette's colour, not white.
        self.assertEqual(
            heading.styles["color"],
            ink_css(self.theme.palette, ink_on(self.theme.palette, "#2b2e36")),
        )
        self.assertTrue(heading.styles["color"].startswith("var(--builder-color-ink-light"))

    def test_translucent_copy_turns_opaque_where_its_alpha_costs_aa(self):
        """The scrim is solved for the OPAQUE light ink. The catalog's 92%
        paragraph on that backdrop measured 4.13:1; the ink is kept, the
        translucency dropped."""
        body = _text(
            "Body", "Small classes.",
            color="color-mix(in srgb, var(--builder-color-ink-light, #ffffff) 92%, transparent)",
        )
        hero = _box("Hero", [body], backgroundImage="url(https://x/p.jpg)")
        hero.paint_surface("#727682")  # a capped scrim over a bright photo
        enforce_text_contrast([hero], self.theme)
        self.assertNotIn("opacity", body.styles)
        self.assertGreaterEqual(_contrast(_paints(body, self.theme), "#727682"), 4.5)

    def test_translucent_copy_keeps_its_alpha_where_it_still_reads(self):
        body = _text(
            "Body", "Small classes.",
            color="color-mix(in srgb, var(--builder-color-ink-light, #ffffff) 92%, transparent)",
        )
        hero = _box("Hero", [body], backgroundColor="#0f172a")
        self.assertEqual(enforce_text_contrast([hero], self.theme), 0)

    def test_a_white_step_number_on_a_light_brand_badge_turns_dark(self):
        """Measured in the survey: `process-enrollment-steps` prints its number
        in white on the primary; on a yellow brand that was 2.94:1."""
        theme = build_theme("#facc15")
        number = _text("Step Number", "01", color="#ffffff")
        badge = _box("Badge", [number], backgroundColor=theme.palette.primary)
        enforce_text_contrast([_box("Section", [badge], backgroundColor="#ffffff")], theme)
        self.assertGreaterEqual(_contrast(_paints(number, theme), theme.palette.primary), 4.5)


class BandPassTest(unittest.TestCase):
    def setUp(self):
        self.theme = build_theme("#0284c7")

    def test_the_rhythm_never_repaints_a_section_that_paints_itself(self):
        heading = _text("Heading", "x", color="var(--builder-color-heading, #0f172a)")
        hero = _box("Hero - Modern Split", [heading],
                    backgroundColor="var(--builder-page-background, #ffffff)",
                    background="linear-gradient(180deg, #f0f9ff, #ffffff)")
        apply_luminance_rhythm(
            [hero], [SectionVisualInput(participates=True, is_photo_background=True)],
            self.theme.palette,
        )
        self.assertEqual(hero.styles["backgroundColor"], "var(--builder-page-background, #ffffff)")
        self.assertEqual(heading.styles["color"], "var(--builder-color-heading, #0f172a)")

    def test_the_rendered_surface_outranks_the_planners_layout(self):
        block = HeroBlock(headline="h", layout="background", primary_cta_label="Go",
                          primary_cta_href="/x")
        from app.services.section_content import assign_visual_policies, resolve_section_bands

        assign_visual_policies([block])
        stale = section_visual_input_for(block)
        self.assertTrue(stale.is_photo_background)  # what the planner's layout said
        fixed = section_visual_input_for(block, painted="light")
        self.assertEqual(resolve_section_bands([fixed])[0].band, "light")

    def test_a_title_inside_a_filled_panel_keeps_its_panel_ink(self):
        """`cta-banner`: the band is a light tint, the title sits on a navy
        gradient panel. Holding the title ink to the band put brand blue on navy
        at 1.08:1."""
        title = _text("Heading", "Come and see us", color="#ffffff")
        panel = _box("CTA Banner", [title], background="linear-gradient(135deg, #0f172a, #026295)",
                     borderRadius="24px")
        section = _box("CTA - Banner", [panel], backgroundColor="#e3e3fd")
        apply_section_title_inks([section], ("#0302c7",))
        self.assertEqual(title.styles["color"], "#ffffff")  # untouched; the ink pass owns it


def _walk(node):
    yield node
    if isinstance(node.content, list):
        for child in node.content:
            yield from _walk(child)


def _bad_inks(site, theme):
    """Every body text node that is a white the palette did not choose for its
    surface (a hard-coded one), or reads under 3:1 on the surface it actually
    sits on (where that surface is known)."""
    bad = []

    def walk(node, bg):
        fill = _own_fill(node, theme, bg)
        if isinstance(fill, tuple):
            bg = fill
        elif fill is not _NO_FILL:
            bg = None
        if node.type in ("text", "link") and isinstance(node.content, BuilderElementContent):
            raw = (node.styles or {}).get("color")
            if (
                isinstance(raw, str)
                and _LITERAL_WHITE.match(raw)
                and bg is not None
                and raw.lower() != ink_on(theme.palette, _to_hex(bg)).lower()
            ):
                bad.append((node.name, raw, "hard-coded white"))
            elif isinstance(raw, str) and raw.startswith("#") and bg is not None:
                ratio = _contrast(raw, _to_hex(bg))
                if ratio < 3.0:
                    bad.append((node.name, raw, f"{ratio:.2f}:1 on {_to_hex(bg)}"))
        if isinstance(node.content, list):
            for child in node.content:
                walk(child, bg)

    page = tuple(int(theme.page.background[i : i + 2], 16) for i in (1, 3, 5))
    for p in site.pages:
        for el in p.body_schema.elements:
            walk(el, page)
    return bad


class EverySchemeInksForItsSurfaceTest(unittest.IsolatedAsyncioTestCase):
    """End to end, over every design scheme, a hero planned "background" (which
    the director may turn into any template), with a hero photo whose copy zone
    is near-white: no body copy is a literal white, and none reads under 3:1 on
    the surface it actually sits on."""

    async def test_no_scheme_ships_unreadable_copy(self):
        bright = PhotoResult(
            url="https://x/bright.jpg", alt="hero", photographer=None, photographer_url=None,
            source="pexels", avg_color="#AEA296", luminance=0.36, band="dark",
            luminance_map=PhotoLuminance.uniform(0.9),
        )
        original = media.ImageResolver.resolve

        async def resolve(self, query, *, intent="generic", **kw):
            if intent == "hero":
                return bright
            return await original(self, query, intent=intent, **kw)

        plan_blocks = lambda: [
            HeroBlock(eyebrow="Since 2012", headline="Where curious minds grow every day",
                      subheadline="Small classes and caring teachers.", image_query="kids",
                      primary_cta_label="Book", primary_cta_href="/contact",
                      secondary_cta_label="Programmes", secondary_cta_href="/p",
                      layout="background"),
            AboutBlock(heading="About us", body="Ten years in Subang Jaya.", image_query="class"),
            FeaturesBlock(heading="Why us", items=[FeatureItem(title="Small", description="d")] * 3),
            CtaBlock(headline="Come and see us", subheadline="Weekdays.", cta_label="Book",
                     cta_href="/contact"),
        ]
        meta = [ImageMetadata(url="https://source.example/real.jpg", alt="children", intent="hero",
                              role="content", source_usage="inline", width=2400, height=1600)]
        failures = {}
        with mock.patch.object(media.ImageResolver, "resolve", resolve), \
             mock.patch.object(settings, "design_schemes_enabled", True), \
             mock.patch.object(settings, "design_brain_enabled", False), \
             mock.patch.object(settings, "hero_fullbleed_all_pages", False):
            for scheme in DESIGN_SCHEMES:
                for seed, color_scheme in (("#0284c7", "light"), ("#facc15", "light"),
                                           ("#0284c7", "dark")):
                    theme = build_theme(seed, mood="friendly", industry="childcare",
                                        scheme_choice=scheme.slug, color_scheme=color_scheme)
                    site = await plan_to_site(
                        SitePlan(site_name="The Core", brand_mood="friendly",
                                 industry_category="childcare",
                                 pages=[PagePlan(page_type="home", slug="home", title="Home",
                                                 is_homepage=True, blocks=plan_blocks(),
                                                 seo_title="Home", seo_description="Home")]),
                        brand=BrandIdentity(name="The Core", mood="friendly"),
                        theme=theme, scraped_metadata=meta,
                    )
                    bad = _bad_inks(site, theme)
                    if bad:
                        failures[f"{scheme.slug}/{seed}/{color_scheme}"] = bad[:3]
        self.assertEqual(failures, {})


if __name__ == "__main__":
    unittest.main()
