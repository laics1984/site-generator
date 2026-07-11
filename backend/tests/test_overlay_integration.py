"""Offline end-to-end: overlay header defaults through plan_to_site.

A nonprofit (friendly-mood) site with a full-bleed homepage hero must come out
with: GeneratedSite.header_overlay=True, a header that keeps its SOLID chrome
(the renderer strips it during the transparent phase and restores it on
scroll), the `headerOverlaySafe` marker on every page whose hero genuinely
rendered a full-bleed photo (site-wide full-bleed policy), and full-width
builder styles. A page whose hero couldn't resolve a genuine photo degrades to
a compact hero WITHOUT the marker, so its header stays solid and readable.
Runs with Pexels unconfigured, design brain off — no network, no LLM.
"""

import unittest

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import (
    CtaBlock,
    HeroBlock,
    ImageMetadata,
    PagePlan,
    SitePlan,
)
from app.services.schema_builder import plan_to_site


def _hero(headline):
    return HeroBlock(
        headline=headline,
        subheadline="Together we build stronger communities.",
        image_query="community volunteers",
        primary_cta_label="Donate",
        primary_cta_href="/donate",
    )


def _page(slug, page_type, title, *, homepage=False):
    return PagePlan(
        page_type=page_type,
        slug=slug,
        title=title,
        is_homepage=homepage,
        blocks=[
            _hero(f"{title} headline"),
            CtaBlock(
                headline="Join us",
                body="Volunteer today.",
                cta_label="Get involved",
                cta_href="/contact",
            ),
        ],
        seo_title=title,
        seo_description=title,
    )


def _plan():
    return SitePlan(
        site_name="Hope Foundation",
        brand_mood="friendly",
        industry_category="nonprofit",
        pages=[
            _page("home", "home", "Home", homepage=True),
            _page("programs", "services", "Programs"),
            _page("about", "about", "About"),
        ],
    )


_METADATA = [
    ImageMetadata(
        url="https://source.example/hero-bg.jpg", intent="hero", role="background",
        source_usage="css_background", width=2400, height=1400,
    ),
    ImageMetadata(
        url="https://source.example/volunteers.jpg", alt="community volunteers helping",
        intent="generic", role="content", source_usage="inline", width=1600, height=1000,
    ),
]


def _first_section(page):
    for el in page.body_schema.elements:
        if el.name != "Breadcrumb":
            return el
    return None


class MenuInkHeroContrastTest(unittest.IsolatedAsyncioTestCase):
    """Menu ink is theme-driven and the homepage's first hero surface sits in
    the opposite luminance band: light ink ↔ dark hero, dark ink ↔ light hero.
    (Full-bleed photo heroes are always dark; there the overlay renderer forces
    white ink, which this generator marks via header_overlay.)"""

    async def _site(self, scheme):
        from app.services.theme import build_theme

        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            plan = _plan()
            for page in plan.pages:
                page.blocks[0].layout = "split"  # non-overlay hero on page surface
            return await plan_to_site(
                plan,
                brand=BrandIdentity(name="Hope Foundation", mood="friendly"),
                theme=build_theme("#0e7490", color_scheme=scheme),
            )
        finally:
            settings.design_brain_enabled = original

    async def test_ink_and_first_hero_land_in_opposite_bands(self):
        from app.services.theme import _relative_luminance

        for scheme in ("light", "dark"):
            site = await self._site(scheme)
            menu = _find_menu(site.header_schema)
            ink_lum = _relative_luminance(menu.styles["color"])
            home = next(p for p in site.pages if p.is_homepage)
            hero_bg = _first_section(home).styles.get("backgroundColor")
            self.assertIsInstance(hero_bg, str)
            hero_lum = _relative_luminance(hero_bg)
            if ink_lum > 0.5:  # light menu text → dark hero surface
                self.assertLess(hero_lum, 0.5, f"{scheme}: light ink needs dark hero")
            else:  # dark menu text → light hero surface
                self.assertGreater(hero_lum, 0.5, f"{scheme}: dark ink needs light hero")


def _find_menu(el):
    if getattr(el, "type", None) == "menu":
        return el
    content = getattr(el, "content", None)
    if isinstance(content, list):
        for child in content:
            found = _find_menu(child)
            if found is not None:
                return found
    return None


class OverlayDefaultIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def _build(self):
        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            return await plan_to_site(
                _plan(),
                brand=BrandIdentity(
                    name="Hope Foundation", mood="friendly",
                    extracted_palette=["#0e7490"],
                ),
                scraped_metadata=[m.model_copy() for m in _METADATA],
            )
        finally:
            settings.design_brain_enabled = original

    async def test_nonprofit_site_gets_overlay_defaults(self):
        site = await self._build()

        # Site-wide intent: homepage hero is full-bleed -> overlay on.
        self.assertTrue(site.header_overlay)

        # Header element carries SOLID chrome — never transparent (the
        # solidified state restores exactly these styles).
        header_styles = site.header_schema.styles
        self.assertNotEqual(header_styles.get("backgroundColor"), "transparent")
        # The bottom edge is the builder's "Subtle" divider shadow, not a border.
        from app.services.header_footer import HEADER_DIVIDER_SUBTLE

        self.assertEqual(header_styles.get("boxShadow"), HEADER_DIVIDER_SUBTLE)
        self.assertNotIn("borderBottom", header_styles)

        # Marker: every page whose hero rendered a genuine full-bleed photo.
        # The scraped pool has two photos → home + one interior get full-bleed
        # heroes with the marker; the remaining page (no photo, Pexels off)
        # degrades to a compact hero without it, keeping its header solid.
        home = next(p for p in site.pages if p.is_homepage)
        home_hero = _first_section(home)
        self.assertTrue(getattr(home_hero, "headerOverlaySafe", None))
        # It survives the JSON dump that feeds the CMS payload.
        self.assertTrue(home_hero.model_dump(mode="json").get("headerOverlaySafe"))
        interior_safe = [
            page.slug
            for page in site.pages
            if not page.is_homepage
            and getattr(_first_section(page), "headerOverlaySafe", None)
        ]
        self.assertTrue(
            interior_safe,
            "at least one interior page with a resolved photo must be overlay-safe",
        )

        # Full-width frame with contained content.
        self.assertEqual(site.builder_styles["page"]["widthMode"], "full")

    async def test_overlay_off_when_homepage_hero_cannot_go_dark(self):
        # No scraped metadata + Pexels unconfigured in tests → the homepage hero
        # can't resolve a photo and falls back to a light layout. The header must
        # then stay SOLID (overlay off) so the nav keeps its dark theme ink
        # instead of unreadable white-on-light. Rule: white nav ⇒ dark hero.
        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            site = await plan_to_site(
                _plan(),
                brand=BrandIdentity(
                    name="Hope Foundation", mood="friendly",
                    extracted_palette=["#0e7490"],
                ),
                # deliberately no scraped_metadata
            )
        finally:
            settings.design_brain_enabled = original

        home = next(p for p in site.pages if p.is_homepage)
        home_hero = _first_section(home)
        # Hero isn't a dark full-bleed → not overlay-safe → header stays solid.
        self.assertIsNone(getattr(home_hero, "headerOverlaySafe", None))
        self.assertFalse(site.header_overlay)

    async def test_kill_switch_disables_overlay(self):
        original = settings.header_overlay_enabled
        settings.header_overlay_enabled = False
        try:
            site = await self._build()
        finally:
            settings.header_overlay_enabled = original
        self.assertFalse(site.header_overlay)


if __name__ == "__main__":
    unittest.main()
