"""The floating pill's site-wide contract: a photo hero on EVERY page.

A self-chrome header (``floating-pill``) floats over the first section and
never solidifies — it carries its own chrome at every scroll position. That
only reads as designed over a full-screen or banded photo hero, so the
archetype imposes one invariant on the whole site: every page opens with one,
legal pages included.

plan_to_site meets that invariant by GIVING pages the hero (forced background
directives + a prepended banded hero on privacy/terms), and demotes the
archetype only when a page still can't resolve a genuine photo. Offline: Pexels
is disabled suite-wide by conftest, so hero photos come from the scraped pool
and "no photo left in the pool" is how a failure is staged.
"""

import unittest

from app.config import settings
from app.models.brand import HERO_BANDED_MIN_HEIGHT, BrandIdentity
from app.models.content_blocks import (
    CtaBlock,
    HeroBlock,
    ImageMetadata,
    PagePlan,
    SitePlan,
)
from app.services.legal_pages import build_privacy_page, build_terms_page
from app.services.schema_builder import plan_to_site
from app.services.theme import build_theme


def _page(slug, page_type, title, *, homepage=False):
    return PagePlan(
        page_type=page_type,
        slug=slug,
        title=title,
        is_homepage=homepage,
        blocks=[
            HeroBlock(
                headline=f"{title} headline",
                subheadline="Together we build stronger communities.",
                image_query="community volunteers",
                primary_cta_label="Donate",
                primary_cta_href="/donate",
            ),
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


def _photos(count):
    """`count` distinct scraped photos, each big/wide enough for a hero
    background (see settings.hero_min_background_dim / hero_bg_min_aspect)."""
    return [
        ImageMetadata(
            url=f"https://source.example/photo-{i}.jpg",
            alt="community volunteers helping",
            intent="hero",
            role="background",
            source_usage="css_background",
            width=2400,
            height=1400,
        )
        for i in range(count)
    ]


def _first_section(page):
    for el in page.body_schema.elements:
        if el.name != "Breadcrumb":
            return el
    return None


def _legal_pages(theme):
    return [
        build_privacy_page("Hope Foundation", theme),
        build_terms_page("Hope Foundation", theme),
    ]


def _walk(el):
    yield el
    if isinstance(el.content, list):
        for child in el.content:
            yield from _walk(child)


def _h1_count(page):
    return sum(
        1
        for root in page.body_schema.elements
        for el in _walk(root)
        if getattr(el, "htmlTag", None) == "h1"
    )


async def _build(*, photos, header="floating-pill", with_legal=True, **overrides):
    return await _build_plan(
        _plan(), photos=photos, header=header, with_legal=with_legal, **overrides
    )


async def _build_plan(
    plan, *, photos, header="floating-pill", with_legal=True, **overrides
):
    theme = build_theme("#0e7490", mood="friendly")
    originals = {k: getattr(settings, k) for k in overrides}
    settings.design_brain_enabled = False
    for key, value in overrides.items():
        setattr(settings, key, value)
    try:
        return await plan_to_site(
            plan,
            brand=BrandIdentity(name="Hope Foundation", mood="friendly"),
            theme=theme,
            scraped_metadata=_photos(photos),
            extra_pages=_legal_pages(theme) if with_legal else None,
            header_override=header,
        )
    finally:
        settings.design_brain_enabled = True
        for key, value in originals.items():
            setattr(settings, key, value)


class FloatingPillHeroInvariantTest(unittest.IsolatedAsyncioTestCase):
    async def test_every_page_including_legal_opens_on_a_photo_hero(self):
        site = await _build(photos=5)

        self.assertEqual(site.design_manifest["header_archetype"], "floating-pill")
        self.assertTrue(site.header_overlay)
        self.assertEqual({p.slug for p in site.pages} >= {"privacy", "terms"}, True)
        for page in site.pages:
            self.assertIs(
                getattr(_first_section(page), "headerOverlaySafe", None),
                True,
                f"page '{page.slug}' must open on a photo hero under the pill",
            )

    async def test_legal_hero_is_banded_and_carries_the_page_title(self):
        site = await _build(photos=5)

        privacy = next(p for p in site.pages if p.slug == "privacy")
        hero = _first_section(privacy)
        self.assertTrue(hero.name.startswith("Hero"), hero.name)
        # Banded, not a full screen of photography — a legal page has nothing
        # to sell. The var resolves to 460px on a banded site and falls back to
        # the same literal on a full-height one.
        self.assertEqual(
            hero.styles["minHeight"],
            f"var(--builder-hero-min-height, {HERO_BANDED_MIN_HEIGHT})",
        )
        self.assertTrue(hero.styles.get("backgroundImage"))
        titles = [
            el.content.innerText
            for el in _walk(hero)
            if getattr(el.content, "innerText", None)
        ]
        self.assertIn("Privacy Policy", titles)

    async def test_legal_page_keeps_exactly_one_h1(self):
        # The hero headline becomes the h1; the body's own title heading goes,
        # so the page neither stutters nor trips the one-h1 audit rule.
        site = await _build(photos=5)
        for slug in ("privacy", "terms"):
            page = next(p for p in site.pages if p.slug == slug)
            self.assertEqual(_h1_count(page), 1, slug)
            body = page.body_schema.elements[1]
            self.assertNotIn(
                "Privacy Policy",
                [
                    el.content.innerText
                    for el in _walk(body)
                    if getattr(el.content, "innerText", None)
                ],
            )

    async def test_pill_forces_background_heroes_when_the_site_policy_is_off(self):
        # With the site-wide full-bleed policy off, interiors would rotate onto
        # compact split/gradient heroes — nothing for the pill to float over.
        site = await _build(photos=5, hero_fullbleed_all_pages=False)

        self.assertEqual(site.design_manifest["header_archetype"], "floating-pill")
        for page in site.pages:
            self.assertIs(
                getattr(_first_section(page), "headerOverlaySafe", None), True, page.slug
            )


class FloatingPillDemotionTest(unittest.IsolatedAsyncioTestCase):
    async def test_pill_is_demoted_when_a_page_cannot_get_a_photo(self):
        # Three content pages + two legal pages need five photos; four means the
        # last page opens on a flat band, which the pill cannot float over.
        site = await _build(photos=4)

        self.assertNotEqual(site.design_manifest["header_archetype"], "floating-pill")
        decision = next(
            d for d in site.design_manifest["decisions"] if d["area"] == "header"
        )
        self.assertIn("floating-pill", decision["rationale"])
        self.assertIn("demoted", decision["rationale"])
        # The demotion reaches the renderers: a non-self-chrome header reveals
        # its background on scroll (menu_builder.build_layout_payload).
        from app.services.menu_builder import build_layout_payload

        behavior = build_layout_payload(site).header["behavior"]
        self.assertNotIn("revealBackgroundOnScroll", behavior)

    async def test_demoted_pill_leaves_legal_pages_as_they_were(self):
        # No hero was shippable, so privacy/terms keep the boilerplate body they
        # have always had — including their own h1.
        site = await _build(photos=0)

        privacy = next(p for p in site.pages if p.slug == "privacy")
        self.assertEqual(len(privacy.body_schema.elements), 1)
        self.assertEqual(_h1_count(privacy), 1)

    async def test_a_non_pill_header_never_touches_legal_pages(self):
        site = await _build(photos=5, header="glass-blur")

        self.assertEqual(site.design_manifest["header_archetype"], "glass-blur")
        privacy = next(p for p in site.pages if p.slug == "privacy")
        self.assertEqual(len(privacy.body_schema.elements), 1)
        self.assertEqual(_first_section(privacy).name, "Privacy")

    async def test_overlay_kill_switch_leaves_the_pill_alone(self):
        # header_overlay_enabled=False means no header floats over anything, so
        # the invariant doesn't apply and the archetype is not second-guessed.
        site = await _build(photos=0, header_overlay_enabled=False)

        self.assertEqual(site.design_manifest["header_archetype"], "floating-pill")
        self.assertFalse(site.header_overlay)


class BandMarkerTest(unittest.IsolatedAsyncioTestCase):
    """Every top-level section states its own luminance band.

    The pill never solidifies, so its ink has to follow whatever section is
    under it as the page scrolls. A renderer cannot work that out for itself —
    a photo's luminance isn't in the DOM and the scrim that makes it dark is a
    generator decision — so the generator states it, once, per section.
    """

    async def test_every_top_level_section_carries_exactly_one_band(self):
        site = await _build(photos=6)

        for page in site.pages:
            for el in page.body_schema.elements:
                classes = (el.classes or "").split()
                bands = [c for c in classes if c.startswith("wt-band-")]
                self.assertEqual(
                    bands,
                    [bands[0]] if bands else [],
                    f"{page.slug}/{el.name}: {el.classes}",
                )
                self.assertIn(
                    bands[0] if bands else None,
                    ("wt-band-light", "wt-band-dark"),
                    f"{page.slug}/{el.name} has no band",
                )

    async def test_a_photo_hero_the_header_floats_over_is_dark(self):
        # The pill's whole reason for existing: it floats over this section,
        # and this section carries the legibility scrim that makes white ink
        # the right answer.
        site = await _build(photos=6)

        for page in site.pages:
            hero = _first_section(page)
            if getattr(hero, "headerOverlaySafe", None) is not True:
                continue
            self.assertIn("wt-band-dark", (hero.classes or "").split(), page.slug)

    async def test_a_light_section_is_marked_light(self):
        from app.services.image_styling import band_for_color
        from app.services.section_content import _parse_color
        from app.services.theme import build_theme

        theme = build_theme("#0e7490", mood="friendly")
        site = await _build(photos=6)
        checked = 0
        for page in site.pages:
            for el in page.body_schema.elements:
                styles = el.styles or {}
                if getattr(el, "headerOverlaySafe", None) is True:
                    continue
                if styles.get("backgroundImage") or not styles.get("backgroundColor"):
                    continue
                parsed = _parse_color(styles["backgroundColor"], theme)
                if parsed is None or parsed[1] < 0.999:
                    continue
                expected = "wt-band-" + band_for_color(
                    "#%02x%02x%02x" % parsed[0]
                )
                self.assertIn(expected, (el.classes or "").split(), el.name)
                checked += 1
        self.assertGreater(checked, 0, "no flat-coloured section to check")

    async def test_the_dynamic_cms_list_is_marked_from_the_page_surface(self):
        # It is skipped by every styling pass because the CMS paints its cards,
        # but it is still a screenful the pill scrolls across, and it sits on
        # the page surface — so it gets that band rather than a gap, which
        # would leave the pill holding the previous section's ink.
        from app.models.content_blocks import PagePlan

        plan = _plan()
        plan.pages.append(
            PagePlan(
                page_type="blog",
                slug="blog",
                title="Blog",
                blocks=list(plan.pages[0].blocks),
                seo_title="Blog",
                seo_description="Blog",
            )
        )
        site = await _build_plan(plan, photos=8)

        blog = next(p for p in site.pages if p.slug == "blog")
        cms = blog.body_schema.elements[-1]
        self.assertEqual(cms.type, "articlesList")
        theme = build_theme("#0e7490", mood="friendly")
        from app.services.image_styling import band_for_color

        self.assertIn(
            "wt-band-" + band_for_color(theme.palette.background),
            (cms.classes or "").split(),
        )


class BandClassificationTest(unittest.TestCase):
    """`_band_class_for` reads the section's FINAL styles, in that order."""

    def setUp(self):
        from app.services.theme import build_theme

        self.theme = build_theme("#0e7490", mood="friendly")

    def _band(self, styles, **fields):
        from app.models.builder_schema import BuilderElement
        from app.services.schema_builder import _band_class_for

        el = BuilderElement(
            id="x", name="Section", type="container", styles=styles, content=[], **fields
        )
        return _band_class_for(el, self.theme)

    def test_overlay_safe_hero_beats_its_own_background_colour(self):
        # The scrim paints over it; the colour underneath is not the surface.
        self.assertEqual(
            self._band({"backgroundColor": "#ffffff"}, headerOverlaySafe=True),
            "wt-band-dark",
        )

    def test_hex_backgrounds_split_on_luminance(self):
        self.assertEqual(self._band({"backgroundColor": "#ffffff"}), "wt-band-light")
        self.assertEqual(self._band({"backgroundColor": "#0f172a"}), "wt-band-dark")

    def test_a_translucent_colour_is_composited_over_the_page(self):
        # 12% black over a white page is still a light band — reading the
        # colour alone would call it dark.
        self.assertEqual(
            self._band({"backgroundColor": "rgba(15, 23, 42, 0.12)"}), "wt-band-light"
        )

    def test_a_photo_covers_whatever_colour_the_node_also_declares(self):
        self.assertEqual(
            self._band(
                {
                    "backgroundColor": "#ffffff",
                    "backgroundImage": "url(https://source.example/p.jpg)",
                }
            ),
            "wt-band-dark",
        )

    def test_a_bare_section_falls_back_to_the_page_background(self):
        expected = (
            "wt-band-light"
            if self.theme.palette.background.lower() in ("#ffffff", "#fff")
            else "wt-band-dark"
        )
        self.assertEqual(self._band({}), expected)


if __name__ == "__main__":
    unittest.main()
