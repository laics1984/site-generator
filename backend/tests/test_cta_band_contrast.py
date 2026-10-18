"""A CTA band stands apart from the bands around it and the footer under it
(schema_builder.contrast_cta_bands), and every shaped edge is filled with the
final colour of the section it reveals (sync_divider_fills).

watr: a closing CTA on the footer's own colour, and a navy wedge (`photo-scrim`)
cut into a brown-black band (`surface-inverse`) — the edge bound in its photo
carrier's context, the band in its own."""

import unittest

from app.models.brand import BrandIdentity
from app.models.builder_schema import (
    BuilderElement,
    BuilderElementContent,
    SectionDivider,
    SectionDividerEdge,
)
from app.services.header_footer import build_footer, footer_band
from app.services.palette_styles import bind_theme_styles
from app.services.palette_styles import rendering_theme
from app.services.schema_builder import (
    _BAND_DISTINCT_CONTRAST,
    _boundary_edge,
    contrast_cta_bands,
    sync_divider_fills,
)
from app.services.theme import _contrast, apply_creative_palette, build_theme
from app.models.builder_schema import PageNode


def _text(inner):
    return BuilderElement(name="Heading", type="text", styles={"color": "#ffffff"},
                          content=BuilderElementContent(innerText=inner))


def _cta(band="#ffffff", card=None):
    content = [_text("Ready?")]
    if card:
        content = [BuilderElement(name="CTA Banner", type="container", content=content,
                                  styles={"backgroundColor": card, "borderRadius": "28px"})]
    return BuilderElement(name="CTA - Banner" if card else "CTA - Minimal", type="container",
                          content=content, styles={"backgroundColor": band})


def _band(name, color):
    return BuilderElement(name=name, type="container", content=[_text(name)],
                          styles={"backgroundColor": color})


def _photo(name="Hero - Background"):
    return BuilderElement(name=name, type="container", content=[_text("Hi")], styles={
        "backgroundColor": "var(--builder-color-secondary, #2b2620)",
        "backgroundImage": "url(https://x/p.jpg)", "backgroundSize": "cover"})


class CtaBandStandsApartTest(unittest.TestCase):
    def setUp(self):
        self.theme = build_theme("#b91c1c", color_scheme="light")

    def _bg(self, section):
        return section.styles["backgroundColor"]

    def test_a_cta_on_the_section_befores_colour_moves(self):
        about, cta = _band("About", "#ffffff"), _cta("#ffffff")
        contrast_cta_bands([about, cta], self.theme, footer_bg=self.theme.palette.secondary)
        self.assertGreaterEqual(_contrast(self._bg(cta), "#ffffff"), _BAND_DISTINCT_CONTRAST)

    def test_a_closing_cta_on_the_footers_colour_moves(self):
        footer = self.theme.palette.secondary
        about, cta = _band("About", "#ffffff"), _cta(footer)
        contrast_cta_bands([about, cta], self.theme, footer_bg=footer)
        self.assertGreaterEqual(_contrast(self._bg(cta), footer), _BAND_DISTINCT_CONTRAST)
        self.assertGreaterEqual(_contrast(self._bg(cta), "#ffffff"), _BAND_DISTINCT_CONTRAST)

    def test_a_band_that_already_stands_apart_is_kept(self):
        cta = _cta(self.theme.palette.primary)
        contrast_cta_bands([_band("About", "#ffffff"), cta], self.theme, footer_bg="#fef2f2")
        self.assertEqual(self._bg(cta), self.theme.palette.primary)

    def test_a_tint_is_not_a_contrast(self):
        cta = _cta("#f4ebe0")  # cream on white: ~1.17:1
        contrast_cta_bands([_band("About", "#ffffff"), cta], self.theme, footer_bg=None)
        self.assertNotEqual(self._bg(cta), "#f4ebe0")

    def test_the_card_still_stands_off_the_new_band(self):
        card = "#171009"
        cta = _cta("#ffffff", card=card)
        contrast_cta_bands([_band("About", "#ffffff"), cta], self.theme,
                           footer_bg=self.theme.palette.secondary)
        self.assertGreaterEqual(_contrast(card, self._bg(cta)), 1.5)
        self.assertGreaterEqual(_contrast(self._bg(cta), "#ffffff"), _BAND_DISTINCT_CONTRAST)

    def test_a_photo_neighbour_sets_no_constraint(self):
        cta = _cta("#ffffff")
        contrast_cta_bands([_photo(), cta], self.theme, footer_bg=None)
        self.assertEqual(self._bg(cta), "#ffffff")

    def test_a_photo_cta_keeps_its_photo(self):
        cta = _photo("CTA - Background")
        contrast_cta_bands([_band("About", "#2b2620"), cta], self.theme, footer_bg="#2b2620")
        self.assertIn("url(", cta.styles["backgroundImage"])
        self.assertEqual(cta.styles["backgroundColor"], "var(--builder-color-secondary, #2b2620)")

    def test_a_section_that_is_not_a_cta_keeps_its_band(self):
        about = _band("About", "#ffffff")
        contrast_cta_bands([_band("Hero", "#ffffff"), about], self.theme, footer_bg="#ffffff")
        self.assertEqual(self._bg(about), "#ffffff")


class FooterBandIsOneAnswerTest(unittest.TestCase):
    def _footer_bg(self, footer):
        return footer.styles.get("backgroundColor")

    def test_the_early_answer_is_what_the_footer_paints(self):
        theme = build_theme("#0284c7", color_scheme="light")
        brand = BrandIdentity(name="Acme")
        flat = [PageNode(slug="", title="Home", is_homepage=True), PageNode(slug="about", title="About")]
        grouped = flat + [PageNode(slug="services", title="Services", children=[
            PageNode(slug="services/a", title="A")])]
        for archetype in ("mega", "cta-banner", "minimal-centered", "editorial"):
            for tree in (flat, grouped):
                footer = build_footer(brand, theme, nav_items=[("About", "/about")], page_tree=tree,
                                      archetype=archetype, primary_cta=("Go", "/contact"))
                with self.subTest(archetype=archetype, grouped=tree is grouped):
                    self.assertEqual(self._footer_bg(footer),
                                     footer_band(brand, theme, archetype, tree))


class DividerMatchesItsNeighbourTest(unittest.TestCase):
    def test_a_photo_carriers_edge_binds_to_the_neighbours_role(self):
        canonical = apply_creative_palette(
            build_theme("#b91c1c", mood="friendly"),
            {"primary": "#b91c1c", "secondary": "#2b2620", "accent": "#d97706", "surface": "#f5ece3"},
        )
        rendered = rendering_theme(canonical)
        dark = f"var(--builder-color-secondary, {rendered.palette.secondary})"
        hero = _photo()
        hero.divider = SectionDivider(bottom=SectionDividerEdge(shape="wave", color=dark))
        services = _band("Services", dark)
        sections = [hero, services]
        bind_theme_styles(sections, canonical, rendered)
        sync_divider_fills(sections)
        self.assertEqual(hero.divider.bottom.color, services.styles["backgroundColor"])
        self.assertNotIn("photo-scrim", hero.divider.bottom.color)

    def test_an_edge_follows_a_neighbour_recoloured_after_the_dividers(self):
        hero = _photo()
        hero.divider = SectionDivider(bottom=SectionDividerEdge(shape="wave", color="#ffffff"))
        cta = _band("CTA - Minimal", "#b91c1c")
        sync_divider_fills([hero, cta])
        self.assertEqual(hero.divider.bottom.color, "#b91c1c")

    def test_an_edge_whose_neighbour_is_a_photo_is_dropped(self):
        hero = _photo()
        hero.divider = SectionDivider(top=None, bottom=SectionDividerEdge(shape="wave", color="#ffffff"))
        sync_divider_fills([hero, _photo("CTA - Background")])
        self.assertIsNone(hero.divider)


class EverySchemeBandsTest(unittest.IsolatedAsyncioTestCase):
    """End to end, a photo hero (so the hero carries the edge), every scheme, on
    the AI palette path (v2) and the seeded one: no edge differs from the band
    it reveals, no CTA band sits within 1.25:1 of the section before it or the
    footer, and no copy or button fails the ink audit."""

    async def test_no_scheme_ships_a_mismatched_edge_or_a_blended_cta(self):
        import json
        from unittest import mock

        from app.config import settings
        from app.models.builder_schema import GeneratedSite
        from app.models.content_blocks import (
            AboutBlock, CtaBlock, FeaturesBlock, FeatureItem, HeroBlock, PagePlan, SitePlan,
        )
        from app.services import media
        from app.services.design_schemes import DESIGN_SCHEMES
        from app.services.image_sampling import PhotoLuminance
        from app.services.pexels import PhotoResult
        from app.services.schema_builder import plan_to_site
        from scripts.audit_site_inks import audit

        photo = PhotoResult(url="https://x/p.jpg", alt="people", photographer=None,
                            photographer_url=None, source="pexels", avg_color="#5a4a40",
                            luminance=0.2, band="dark", luminance_map=PhotoLuminance.uniform(0.4))
        original = media.ImageResolver.resolve

        async def resolve(self, query, *, intent="generic", **kw):
            if intent == "hero":
                return photo
            return await original(self, query, intent=intent, **kw)

        blocks = lambda: [
            HeroBlock(headline="Workplace Transformation", subheadline="Equipping professionals.",
                      image_query="people", primary_cta_label="Join", primary_cta_href="/",
                      layout="background"),
            FeaturesBlock(heading="Programs", items=[FeatureItem(title="Life", description="d")] * 3),
            AboutBlock(heading="About us", body="We walk with professionals."),
            CtaBlock(headline="Ready?", subheadline="Join us.", cta_label="Join", cta_href="/"),
        ]
        failures = {}
        with mock.patch.object(media.ImageResolver, "resolve", resolve), \
             mock.patch.object(settings, "design_schemes_enabled", True), \
             mock.patch.object(settings, "design_brain_enabled", False):
            for scheme in DESIGN_SCHEMES:
                for v2 in (True, False):
                    theme = build_theme("#b91c1c", mood="friendly", industry="nonprofit",
                                        scheme_choice=scheme.slug)
                    if v2:
                        theme = apply_creative_palette(theme, {
                            "primary": "#b91c1c", "secondary": "#2b2620",
                            "accent": "#d97706", "surface": "#f5ece3"})
                    site = await plan_to_site(
                        SitePlan(site_name="WATR", brand_mood="friendly",
                                 industry_category="nonprofit",
                                 pages=[PagePlan(page_type="home", slug="home", title="Home",
                                                 is_homepage=True, blocks=blocks(),
                                                 seo_title="H", seo_description="H")]),
                        brand=BrandIdentity(name="WATR", mood="friendly"), theme=theme,
                    )
                    lines = audit(GeneratedSite.model_validate(json.loads(site.model_dump_json())))
                    bad = [l.splitlines()[0] for l in lines
                           if l.startswith(("TEXT", "BUTTON", "DIVIDER", "CTA-BAND"))]
                    if bad:
                        failures[f"{scheme.slug}/{'v2' if v2 else 'v1'}"] = bad[:3]
        self.assertEqual(failures, {})


class NoSeamBetweenOneColourTest(unittest.TestCase):
    def test_two_flat_sections_of_one_colour_get_no_divider(self):
        self.assertIsNone(_boundary_edge([_band("A", "#fffdfc"), _band("B", "#fffdfc")], 0, "top"))

    def test_two_flat_sections_of_different_colours_still_do(self):
        self.assertEqual(_boundary_edge([_band("A", "#fffdfc"), _band("B", "#eedfd0")], 0, "top"),
                         (1, "top", 0))


if __name__ == "__main__":
    unittest.main()
