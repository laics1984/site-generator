"""A children's site is never dark (services/friendly_envelope.py).

Seven independent paths shipped a kindergarten with a dark surface — 28 of 28
stress builds over the childcare schemes: a light logo voting the canvas dark,
the AI palette, the footer's `secondary` band, the header's logo band, the
full-bleed photo hero under its near-black scrim, the rhythm's dark band and
the CTA pool. The envelope states the rule once; these tests pin each half
from the rule's side, then end to end over every scheme.
"""

from __future__ import annotations

import base64
import io
import unittest
from unittest import mock

from PIL import Image, ImageDraw

from app.config import settings
from app.models.brand import BrandIdentity, CreativePalette
from app.models.builder_schema import BuilderElement
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
from app.models.design_manifest import SELF_CHROME_HEADERS
from app.services import friendly_envelope, media
from app.services.design_director import compose_design_manifest
from app.services.design_fingerprint import template_id_of
from app.services.design_schemes import DESIGN_SCHEMES, NEUTRAL_SCHEME
from app.services.header_footer import build_header, footer_band
from app.services.hero_director import _MOOD_SPECS, plan_site_heroes
from app.services.pexels import PhotoResult
from app.services.schema_builder import apply_friendly_bands, plan_to_site
from app.services.section_content import surface_of
from app.services.template_filler import get_template, load_catalog
from app.services.theme import apply_creative_palette, build_theme, resolve_color_scheme


def _white_mark() -> str:
    """A white wordmark on transparency — the logo that used to move a
    kindergarten's header and footer onto a near-black bar."""
    img = Image.new("RGBA", (240, 80), (0, 0, 0, 0))
    ImageDraw.Draw(img).rectangle((10, 20, 230, 60), fill=(255, 255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _white_logo_brand(name: str = "Little Stars") -> BrandIdentity:
    return BrandIdentity(
        name=name, mood="friendly", industry="childcare",
        logo_data_url=_white_mark(), logo_url="https://x.example/logo.png",
        logo_render_ok=True, logo_is_light=True,
    )


def _childcare_theme(**kw) -> object:
    return build_theme("#0284c7", mood="friendly", industry="childcare", **kw)


class ColorSchemeTest(unittest.TestCase):
    def test_nothing_but_the_operator_makes_a_childcare_site_dark(self):
        self.assertEqual(resolve_color_scheme(None, None, True, industry="childcare"), "light")
        self.assertEqual(resolve_color_scheme(None, "dark", True, industry="childcare"), "light")
        direction = mock.Mock(color_scheme_lean="dark")
        self.assertEqual(
            resolve_color_scheme(None, None, True, industry="childcare", direction=direction), "light"
        )

    def test_the_explicit_dark_choice_is_honoured(self):
        self.assertEqual(resolve_color_scheme("dark", None, None, industry="childcare"), "dark")

    def test_other_industries_still_vote(self):
        self.assertEqual(resolve_color_scheme(None, None, True, industry="restaurant"), "dark")


class PaletteTest(unittest.TestCase):
    def _proposal(self, **colors):
        base = {"primary": "#db2777", "secondary": "#7c3aed", "accent": "#22c55e", "surface": "#fdf2f8"}
        return apply_creative_palette(_childcare_theme(), CreativePalette(**{**base, **colors}))

    def test_a_dark_or_hueless_proposal_is_refused(self):
        current = _childcare_theme()
        self.assertFalse(friendly_envelope.keeps(self._proposal(surface="#111827"), current))
        self.assertFalse(friendly_envelope.keeps(self._proposal(primary="#27272a"), current))

    def test_a_bright_light_proposal_is_kept(self):
        self.assertTrue(friendly_envelope.keeps(self._proposal(), _childcare_theme()))

    def test_an_explicitly_dark_site_keeps_any_proposal(self):
        current = _childcare_theme(color_scheme="dark")
        self.assertTrue(friendly_envelope.keeps(self._proposal(surface="#111827"), current))

    def test_no_curated_childcare_palette_is_hueless(self):
        from app.services.color_system import BAND_MIN_SOURCE_CHROMA, oklch
        from app.services.theme import _CURATED_PALETTES

        dull = [p.name for p in _CURATED_PALETTES
                if "childcare" in p.categories and oklch(p.primary)[1] < BAND_MIN_SOURCE_CHROMA]
        self.assertEqual(dull, [])


class CompositionTest(unittest.TestCase):
    def test_the_refused_templates_are_read_from_the_catalog(self):
        """Structural, not a name list: a template whose root takes a photo, or
        a gradient through the dark neutral, carries light copy on dark. A new
        one shows up here rather than slipping past the gate."""
        refused = {
            t["id"] for t in load_catalog()["sections"]
            if friendly_envelope.refuses_template("childcare", t)
        }
        self.assertEqual(
            refused,
            {"hero-background-bold", "hero-gradient", "about-background", "cta-background", "cta-gradient"},
        )
        self.assertFalse(friendly_envelope.refuses_template("restaurant", get_template("hero-background-bold")))

    def test_every_mood_keeps_a_hero_to_open_on(self):
        admitted = lambda d: not friendly_envelope.refuses_template("childcare", get_template(d.template_id))
        for mood, spec in _MOOD_SPECS.items():
            self.assertTrue([d for d in spec.rotation if admitted(d)], mood)

    def test_no_page_opens_on_a_dark_backdrop(self):
        pages = [
            PagePlan(page_type="home", slug="", title="Home", is_homepage=True,
                     seo_title="Home", seo_description="Home"),
            *[PagePlan(page_type=kind, slug=kind, title=kind, seo_title=kind, seo_description=kind)
              for kind in ("about", "services", "contact", "gallery", "faq")],
        ]
        for scheme in (NEUTRAL_SCHEME, *DESIGN_SCHEMES):
            for mood in _MOOD_SPECS:
                for asks in ({}, {"force_background": True}, {"has_source_background": True},
                             {"hero_height": "banded"}, {"has_hero_photo": False}):
                    kwargs = {"has_source_background": False, **asks}
                    directives = plan_site_heroes(
                        pages, mood=mood, seed="Little Stars", scheme=scheme,
                        direction_leads=("background", "gradient", "playful-split"),
                        industry="childcare", **kwargs,
                    )
                    for slug, directive in directives.items():
                        self.assertFalse(
                            friendly_envelope.refuses_template("childcare", get_template(directive.template_id)),
                            (scheme.slug, mood, asks, slug, directive),
                        )

    def test_the_floating_pill_never_heads_a_childcare_site(self):
        async def headers():
            return {
                (await compose_design_manifest(brand_name=f"School {i}", mood=mood, industry="childcare",
                                               scheme=scheme)).header_archetype
                for scheme in DESIGN_SCHEMES for mood in _MOOD_SPECS for i in range(3)
            }

        import asyncio
        self.assertFalse(asyncio.run(headers()) & SELF_CHROME_HEADERS)

    def test_a_hero_query_is_made_to_show_children(self):
        self.assertEqual(friendly_envelope.hero_query("childcare", "our reception desk"),
                         "children our reception desk")
        self.assertEqual(friendly_envelope.hero_query("childcare", "toddlers painting"), "toddlers painting")
        self.assertEqual(friendly_envelope.hero_query("restaurant", "our dining room"), "our dining room")
        self.assertIsNone(friendly_envelope.hero_query("childcare", None))


class ChromeTest(unittest.TestCase):
    def test_every_footer_sits_on_the_brand_pastel(self):
        theme = _childcare_theme()
        for archetype in ("mega", "cta-banner", "minimal-centered", "editorial"):
            self.assertEqual(
                footer_band(BrandIdentity(name="Little Stars"), theme, archetype, None),
                friendly_envelope.pastel(theme), archetype,
            )

    def test_a_white_logo_gives_way_to_the_wordmark_not_a_dark_bar(self):
        theme = _childcare_theme()
        brand = _white_logo_brand()
        band = footer_band(brand, theme, "mega", None)
        self.assertTrue(friendly_envelope.is_pastel(band))
        header = build_header(brand, theme, [("Home", "/")])
        self.assertFalse([n for n in header.walk() if n.type == "image"])
        # The bar; the monogram circle inside the wordmark is the brand mark.
        self.assertTrue(friendly_envelope.is_pastel(surface_of(header, theme)))

    def test_an_explicitly_dark_site_keeps_its_chrome(self):
        theme = _childcare_theme(color_scheme="dark")
        self.assertEqual(
            footer_band(BrandIdentity(name="x"), theme, "minimal-centered", None), theme.palette.surface
        )


def _section(color: str) -> BuilderElement:
    return BuilderElement(name="Section", type="section", styles={"backgroundColor": color})


class BandsTest(unittest.TestCase):
    def test_one_hue_in_three_steps_whatever_the_rhythm_painted(self):
        """Tint, page, tint, page — the dark rhythm band and `surface` (1.05:1
        off the tint) alike take their place in the order."""
        theme = _childcare_theme()
        tint, page = friendly_envelope.pastel(theme), theme.palette.background
        sections = [_section(theme.palette.surface), _section("#0f172a"),
                    _section(theme.palette.surface), _section("#0f172a")]
        apply_friendly_bands(sections, theme)
        self.assertEqual([s.styles["backgroundColor"] for s in sections], [tint, page, tint, page])

    def test_a_scheme_that_speaks_in_colour_takes_one_companion_never_side_by_side(self):
        from app.services.color_system import derive

        theme = build_theme("#db2777", mood="friendly", industry="childcare")
        roles = derive(theme.palette.model_dump())
        tint, companion, page = roles["band-1"], roles["band-2"], theme.palette.background
        self.assertEqual(
            friendly_envelope.band_sequence(theme, 7, two_hue=True),
            [tint, page, companion, page, tint, page, companion],
        )
        self.assertEqual(
            friendly_envelope.band_sequence(theme, 5, two_hue=False), [tint, page, tint, page, tint]
        )

    def test_a_picture_keeps_its_surface_and_its_place(self):
        theme = _childcare_theme()
        photo = BuilderElement(name="Photo", type="section",
                               styles={"backgroundImage": "url('https://x/kids.jpg')"})
        sections = [_section("#ffffff"), photo, _section("#0f172a")]
        apply_friendly_bands(sections, theme)
        self.assertNotIn("backgroundColor", photo.styles)
        self.assertEqual(sections[2].styles["backgroundColor"], friendly_envelope.pastel(theme))

    def test_a_vivid_band_is_not_a_pastel(self):
        """A #db2777 band carries dark ink and is not dark — and it is still the
        loud slab a friendly page does not want."""
        self.assertFalse(friendly_envelope.is_pastel("#db2777"))
        self.assertFalse(friendly_envelope.is_pastel("#facc15"))
        self.assertTrue(friendly_envelope.is_pastel(friendly_envelope.pastel(_childcare_theme())))

    def test_the_openings_own_light_gradient_gives_way_to_the_pastel(self):
        theme = _childcare_theme()
        hero = BuilderElement(name="Hero", type="section", styles={
            "backgroundColor": "#ffffff",
            "background": "linear-gradient(180deg, #f0f9ff, #ffffff)",
        })
        apply_friendly_bands([hero], theme)
        self.assertNotIn("background", hero.styles)
        self.assertEqual(hero.styles["backgroundColor"], friendly_envelope.pastel(theme))

    def test_the_deep_pastel_stands_apart_and_carries_the_page(self):
        from app.services.color_system import contrast, derive

        for seed in ("#0284c7", "#db2777", "#16a34a", "#facc15", "#7c3aed", "#0d9488"):
            theme = build_theme(seed, mood="friendly", industry="childcare")
            deep, roles = friendly_envelope.deep_pastel(theme), derive(theme.palette.model_dump())
            self.assertTrue(friendly_envelope.is_pastel(deep), seed)
            for band in (roles["band-1"], theme.palette.background, theme.palette.surface):
                self.assertGreaterEqual(contrast(deep, band), 1.25, (seed, band))
            self.assertGreaterEqual(contrast(deep, roles["ink-dark"]), 4.5, seed)
            self.assertGreaterEqual(contrast(deep, theme.buttons.background), 3.0, seed)

    def test_other_sites_are_untouched(self):
        theme = build_theme("#0284c7", mood="friendly", industry="restaurant")
        sections = [_section("#0f172a")]
        apply_friendly_bands(sections, theme)
        self.assertEqual(sections[0].styles["backgroundColor"], "#0f172a")


class EverySchemeStaysLightTest(unittest.IsolatedAsyncioTestCase):
    """End to end over every scheme that can build a childcare site: a white
    logo, a grouped page tree (the mega footer's own case), a photo for every
    hero: every header, footer and section is a pastel, every opening that is
    not a picture wears the brand's pastel, every CTA the deep pastel, and a
    page paints three tones (four on a multi-tint scheme)."""

    async def test_no_scheme_ships_a_dark_surface(self):
        photo = PhotoResult(
            url="https://x/kids.jpg", alt="children playing", photographer=None, photographer_url=None,
            source="pexels", avg_color="#6b5b4b",
        )
        original = media.ImageResolver.resolve

        async def resolve(self, query, *, intent="generic", **kw):
            return photo if intent == "hero" else await original(self, query, intent=intent, **kw)

        def page(slug: str, parent: str | None = None, home: bool = False) -> PagePlan:
            return PagePlan(
                page_type="home" if home else "services", slug=slug, title=slug or "Home",
                is_homepage=home, parent_slug=parent, seo_title=slug or "Home", seo_description="d",
                blocks=[
                    HeroBlock(headline="Where curious minds grow", image_query="classroom",
                              primary_cta_label="Visit", primary_cta_href="/contact", layout="background"),
                    AboutBlock(heading="About us", body="Ten years in Subang Jaya.", image_query="class"),
                    FeaturesBlock(heading="Why us", items=[FeatureItem(title="Small", description="d")] * 3),
                    CtaBlock(headline="Come and see us", cta_label="Book", cta_href="/contact",
                             background_query="school"),
                ],
            )

        meta = [ImageMetadata(url="https://source.example/real.jpg", alt="children", intent="hero",
                              role="content", source_usage="inline", width=2400, height=1600)]
        failures = {}
        with mock.patch.object(media.ImageResolver, "resolve", resolve), \
             mock.patch.object(settings, "design_schemes_enabled", True), \
             mock.patch.object(settings, "design_brain_enabled", False):
            for scheme in DESIGN_SCHEMES:
                theme = build_theme("#0284c7", mood="friendly", industry="childcare", scheme_choice=scheme.slug)
                pastel = friendly_envelope.pastel(theme)
                site = await plan_to_site(
                    SitePlan(site_name="Little Stars", brand_mood="friendly", industry_category="childcare",
                             pages=[page("", home=True), page("programmes"),
                                    page("programmes/toddlers", "programmes"), page("contact")]),
                    brand=_white_logo_brand(), theme=theme, scraped_metadata=meta,
                )
                bad = [
                    f"{where}:{template_id_of(node) or node.name}"
                    for where, nodes in (
                        ("header", [site.header_schema]), ("footer", [site.footer_schema]),
                        *((p.slug or "/", p.body_schema.elements) for p in site.pages),
                    )
                    for node in nodes
                    if (fill := surface_of(node, theme)) is None and node.styles
                    and "url(" in str(node.styles.get("backgroundImage", "")) and not node.painted_surface
                    or fill is not None and not friendly_envelope.is_pastel(fill)
                ]
                bad += [
                    f"{p.slug or '/'}:opening={fill}"
                    for p in site.pages
                    if not (opening := p.body_schema.elements[0]).painted_surface
                    and (fill := surface_of(opening, theme)) not in (None, pastel)
                ]
                bad += [
                    f"{p.slug or '/'}:{template_id_of(s)}={fill}"
                    for p in site.pages for s in p.body_schema.elements
                    if template_id_of(s).startswith("cta")
                    and (fill := surface_of(s, theme)) != friendly_envelope.deep_pastel(theme)
                ]
                # One hue in three steps; a scheme that speaks in colour adds one.
                # The header is left out: a glass bar is translucent over what
                # scrolls under it (the tinted hero), which no single fill states.
                ceiling = 4 if scheme.band_tint == "multi-tint" else 3
                for p in site.pages:
                    tones = {surface_of(site.footer_schema, theme)} | {
                        surface_of(s, theme) for s in p.body_schema.elements
                        if s.backgroundTexture == "flat" and not s.painted_surface
                    }
                    if len(tones - {None}) > ceiling:
                        bad.append(f"{p.slug or '/'}:{len(tones - {None})} tones")
                if bad:
                    failures[scheme.slug] = bad[:4]
        self.assertEqual(failures, {})


if __name__ == "__main__":
    unittest.main()
