"""Childcare / kindergarten industry wiring.

What the industry still owns is its CONTENT shape and its safety rules: the
page set and landing journey parents need, the book-a-tour conversion, a light
scheme by default, honest imagery. What it no longer owns is the LOOK. The
pastel-band, rainbow-title, wave-seam, rounded-type, 68px-logo brief used to be
forced on every school from ten industry-keyed tables, so ten kindergartens with
ten logo colours shipped in three colourways and one typeface
(docs/design-identity-plan.md). That look is now the `playroom` design scheme —
one direction among several, chosen on evidence — and the logo leads the
palette like everywhere else.
"""

import re
import unittest
from contextlib import contextmanager
from unittest import mock

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.models.content_blocks import (
    HeroBlock,
    PagePlan,
    industry_default_mood,
    resolve_mood,
)
from app.services.design_schemes import by_slug, candidates_for
from app.services.header_footer import build_header
from app.services.hero_director import _MOOD_SPECS, plan_site_heroes
from app.services.image_styling import photo_background
from app.services.industry_personality import personality_for
from app.services.industry_templates import get_template
from app.services.landing_patterns import homepage_sections
from app.services.media import _INDUSTRY_CONTEXT_QUERIES, _contextual_non_person_query
from app.services.schema_builder import (
    _NEUTRAL_PHOTO_CAST,
    RenderContext,
    _apply_hero_typography,
    _midpoint_word_split,
    apply_section_dividers,
    make_style_tokens,
)
from app.services.section_content import apply_band_tints, apply_section_title_inks
from app.services.style_tokens import _accent_ink_for, band_tints, section_title_inks
from app.services.theme import (
    _CURATED_PALETTES,
    _contrast,
    build_theme,
    resolve_color_scheme,
)

KINDERGARTENS = (
    "Little Acorns", "Tadika Murni", "Sunbeam House", "Bright Harbour",
    "Glory Fields", "Montessori House KL", "Kiddyland Lane", "Rainbow Yard",
    "The Learning Tree", "Pelangi Studio",
)


@contextmanager
def schemes_on():
    with mock.patch.object(settings, "design_schemes_enabled", True):
        yield


def _find_by_name(node, name):
    if getattr(node, "name", None) == name:
        return node
    content = getattr(node, "content", None)
    if isinstance(content, list):
        for ch in content:
            found = _find_by_name(ch, name)
            if found is not None:
                return found
    return None


class ChildcarePersonalityTest(unittest.TestCase):
    def test_has_its_own_personality(self):
        p = personality_for("childcare")
        self.assertNotEqual(p, personality_for("other"))
        # Parent-first tone, not child-facing.
        self.assertIn("parent", p.voice)

    def test_the_design_brief_does_not_prescribe_a_look(self):
        """The personality line is injected into the design and copy prompts.
        It used to name the pastel palette and rounded type outright, so the
        model reached for the same look on every school; it now points at the
        school's own evidence."""
        design = personality_for("childcare").design.lower()
        for prescription in ("pastel", "cream/sky", "rounded friendly display"):
            self.assertNotIn(prescription, design)
        self.assertIn("logo", design)


class ChildcareMoodTest(unittest.TestCase):
    def test_defaults_to_friendly_not_playful(self):
        # Joyful without being childish: the audience is parents. Still the
        # last-resort default when nothing else is known.
        self.assertEqual(industry_default_mood("childcare"), "friendly")

    def test_detection_is_no_longer_overruled(self):
        """The industry used to LOCK the mood to friendly, overruling what the
        source said about itself. Detection now wins over the default."""
        self.assertEqual(
            resolve_mood(override=None, brand_mood=None, detected="editorial", industry="childcare"),
            "editorial",
        )
        self.assertEqual(
            resolve_mood(override=None, brand_mood=None, detected=None, industry="childcare"),
            "friendly",
        )

    def test_explicit_choices_still_win(self):
        self.assertEqual(
            resolve_mood(override="luxury", brand_mood="playful", detected="editorial", industry="childcare"),
            "luxury",
        )
        self.assertEqual(
            resolve_mood(override=None, brand_mood="playful", detected="editorial", industry="childcare"),
            "playful",
        )


class ChildcareColorSchemeTest(unittest.TestCase):
    """Light by default is legibility for a parent audience, not a look — it
    stays an industry lean (decision recorded in the plan's Phase 1)."""

    def test_light_logo_never_defaults_childcare_to_dark(self):
        # The Glorykids failure: a bright logo read as "light" flipped the
        # whole site dark.
        self.assertEqual(
            resolve_color_scheme(None, None, True, industry="childcare"), "light"
        )

    def test_explicit_dark_choice_still_wins(self):
        self.assertEqual(
            resolve_color_scheme("dark", None, True, industry="childcare"), "dark"
        )
        self.assertEqual(
            resolve_color_scheme(None, "dark", True, industry="childcare"), "dark"
        )

    def test_other_industries_keep_the_logo_smart_default(self):
        self.assertEqual(
            resolve_color_scheme(None, None, True, industry="restaurant"), "dark"
        )
        self.assertEqual(resolve_color_scheme(None, None, True), "dark")


class ChildcareTemplateTest(unittest.TestCase):
    def test_template_registered(self):
        tpl = get_template("childcare")
        self.assertEqual(tpl.industry, "childcare")

    def test_suggested_pages_cover_trust_builders(self):
        tpl = get_template("childcare")
        slugs = {p.slug for p in tpl.suggested_pages}
        self.assertIn("programs", slugs)  # programs by age
        self.assertIn("teachers", slugs)  # meet our teachers
        self.assertIn("gallery", slugs)   # authentic daily moments
        self.assertIn("faq", slugs)       # admissions / fees / safety

    def test_homepage_is_generatable_size(self):
        # A homepage is one atomic scaffold generated in ONE LLM call. The
        # original 10-section childcare homepage overflowed the MLX token budget
        # (empty stream → 502). It must stay within a size a single call can
        # produce; the deeper story lives on the dedicated pages instead.
        home = get_template("childcare").core_pages[0]
        self.assertTrue(home.is_homepage)
        self.assertLessEqual(len(home.sections), 8)


class ChildcareLandingPatternTest(unittest.TestCase):
    def test_default_pattern_is_parent_trust_journey(self):
        sections = homepage_sections("childcare")
        self.assertEqual(sections[0], "hero")
        self.assertEqual(sections[-1], "cta")
        for kind in ("features", "about", "services", "testimonials"):
            self.assertIn(kind, sections)

    def test_homepage_stays_within_single_call_budget(self):
        self.assertLessEqual(len(homepage_sections("childcare")), 8)


class ChildcareHeroDirectionTest(unittest.TestCase):
    """No industry has a hero spec any more: a school's heroes are its mood's
    and its scheme's (design-identity plan, Phase 4)."""

    def _pages(self):
        def page(slug, page_type, homepage=False):
            return PagePlan(
                page_type=page_type,
                slug=slug,
                title=slug.title(),
                is_homepage=homepage,
                blocks=[],
                seo_title=slug,
                seo_description=slug,
            )

        return [
            page("home", "home", homepage=True),
            page("programs", "services"),
            page("teachers", "team"),
            page("contact", "contact"),
        ]

    def test_homepage_leads_with_authentic_photography(self):
        directives = plan_site_heroes(
            self._pages(),
            mood="friendly",
            has_source_background=False,
            seed="Sunny Days Kindergarten",
        )
        self.assertEqual(directives["home"].template_id, "hero-background-bold")
        self.assertEqual(directives["home"].layout, "background")

    def test_transactional_pages_stay_calm_and_centered_in_legacy_rotation(self):
        with mock.patch.object(settings, "hero_fullbleed_all_pages", False):
            directives = plan_site_heroes(
                self._pages(),
                mood="friendly",
                has_source_background=False,
                seed="Sunny Days Kindergarten",
            )
        self.assertEqual(directives["contact"].template_id, "hero-centered-minimal")

    def test_default_policy_directs_every_childcare_page_full_bleed(self):
        directives = plan_site_heroes(
            self._pages(),
            mood="friendly",
            has_source_background=False,
            seed="Sunny Days Kindergarten",
        )
        for directive in directives.values():
            self.assertEqual(directive.template_id, "hero-background-bold")


class FriendlyArtDirectionTest(unittest.TestCase):
    """What childcare and nonprofit used to pin for themselves is the friendly
    mood's art direction now — every warm brand gets it, no industry owns it."""

    def test_story_pages_lead_editorial_and_transactional_pages_stay_calm(self):
        friendly = _MOOD_SPECS["friendly"]
        self.assertEqual(friendly.by_page_type["about"].template_id, "hero-editorial")
        self.assertEqual(friendly.by_page_type["team"].template_id, "hero-editorial")
        self.assertEqual(friendly.by_page_type["contact"].template_id, "hero-centered-minimal")
        self.assertEqual(friendly.by_page_type["faq"].template_id, "hero-centered-minimal")

    def test_the_gradient_hero_is_back_in_the_rotation(self):
        """It was excluded for childcare because white type over a PASTEL
        primary's light end was illegible. Palettes are logo-led now and
        enforce_fill_contrast resolves a gradient fill to AA for white ink, so
        the exclusion guarded a failure that cannot happen."""
        ids = {d.template_id for d in _MOOD_SPECS["friendly"].rotation}
        self.assertIn("hero-gradient", ids)
        self.assertIn("hero-playful-split", ids)


class LogoLeadsThePaletteTest(unittest.TestCase):
    """Decision Q2: a usable logo hue leads the palette for every industry,
    childcare included. This file used to pin the opposite
    (`test_brand_logo_hue_is_ignored_for_the_pastel_set`)."""

    def _childcare_primaries(self):
        return {c.primary.upper() for c in _CURATED_PALETTES if "childcare" in c.categories}

    def test_an_orange_logo_gets_an_orange_site(self):
        theme = build_theme(
            "#F97316", mood="friendly", palette_mode="auto",
            font_seed="Glorykids", industry="childcare",
        )
        self.assertIsNone(theme.palette_slug)  # the logo-led snap, not a curated set
        self.assertNotIn(theme.palette.primary.upper(), self._childcare_primaries())
        restaurant = build_theme(
            "#F97316", mood="friendly", palette_mode="auto",
            font_seed="Glorykids", industry="restaurant",
        )
        self.assertEqual(theme.palette, restaurant.palette)

    def test_ten_logo_colours_give_ten_kindergartens_distinct_primaries(self):
        logos = (
            "#e11d48", "#16a34a", "#f59e0b", "#1d4ed8", "#7c3aed",
            "#0f766e", "#ea580c", "#db2777", "#0891b2", "#65a30d",
        )
        primaries = {
            build_theme(
                logo, mood="friendly", palette_mode="auto", font_seed=name, industry="childcare"
            ).palette.primary
            for name, logo in zip(KINDERGARTENS, logos)
        }
        self.assertGreaterEqual(len(primaries), 8)

    def test_a_black_wordmark_does_not_lead_as_navy(self):
        """`#111827` has 39% HLS saturation and ~9% chroma: it is black to any
        visitor. It used to count as a brand hue and snap the site to blue."""
        theme = build_theme(
            "#111827", mood="friendly", palette_mode="auto",
            font_seed="The Learning Tree", industry="childcare",
        )
        self.assertIsNotNone(theme.palette_slug)  # curated, because there is no hue

    def test_no_logo_hue_draws_from_more_than_the_pastel_set(self):
        slugs = {
            build_theme(
                None, mood="friendly", palette_mode="auto", font_seed=name, industry="childcare"
            ).palette_slug
            for name in KINDERGARTENS
        }
        self.assertGreaterEqual(len(slugs), 4)

    def test_a_schemes_strategy_narrows_the_curated_pick(self):
        with schemes_on():
            for name in KINDERGARTENS:
                theme = build_theme(
                    None, mood="friendly", palette_mode="auto", font_seed=name,
                    industry="childcare", scheme_choice="nature-studio",
                )
                entry = next(c for c in _CURATED_PALETTES if c.slug == theme.palette_slug)
                self.assertTrue(
                    set(entry.strategies) & set(by_slug("nature-studio").palette_strategies),
                    (name, entry.slug),
                )


class TypeDirectionTest(unittest.TestCase):
    def test_kindergartens_no_longer_share_one_typeface(self):
        """Industry keywords ("children", "kids") scored Fredoka uniquely
        highest, so every school got it. With a scheme, the type direction
        decides, seeded per brand."""
        with schemes_on():
            faces = {
                build_theme(
                    None, mood="friendly", font_seed=name, industry="childcare"
                ).typography.heading_font
                for name in KINDERGARTENS
            }
        self.assertGreaterEqual(len(faces), 4)

    def test_a_scheme_speaks_only_in_its_own_directions(self):
        from app.services.theme import MOOD_SPECS

        pairings = {
            p.heading_font: p.direction
            for spec in MOOD_SPECS.values()
            for p in (*spec.font_pool, *spec.direction_pairings)
        }
        with schemes_on():
            for slug in candidates_for("friendly", "childcare"):
                for name in KINDERGARTENS[:4]:
                    theme = build_theme(
                        None, mood="friendly", font_seed=name,
                        industry="childcare", scheme_choice=slug,
                    )
                    self.assertIn(
                        pairings[theme.typography.heading_font],
                        by_slug(slug).type_directions,
                        (slug, name),
                    )

    def test_schemes_off_keeps_the_legacy_pick(self):
        # The neutral scheme states no direction, so the mood pool's industry
        # keyword pick stands — the byte-identical kill-switch contract.
        theme = build_theme(None, mood="friendly", font_seed="Sunny Days", industry="childcare")
        self.assertIn("Fredoka", theme.typography.heading_font)


class ChildcareSchemesTest(unittest.TestCase):
    def test_a_school_can_be_built_in_several_distinct_directions(self):
        cell = candidates_for("friendly", "childcare")
        self.assertGreaterEqual(len(cell), 5)
        # Not all soft: at least one scheme spends no seams, and one sets serifs.
        self.assertTrue(any(by_slug(s).divider_shape is None for s in cell))
        self.assertTrue(any("editorial-serif" in by_slug(s).type_directions for s in cell))
        # The old look is still one of them.
        self.assertIn("playroom", cell)

    def test_playroom_carries_the_former_childcare_look(self):
        playroom = by_slug("playroom")
        self.assertEqual(playroom.band_tint, "multi-tint")
        self.assertEqual(playroom.heading_ink, "per-section-accent")
        self.assertTrue(playroom.hero_title_accent)
        self.assertEqual(playroom.photo_cast, "neutral")
        self.assertEqual(playroom.divider_shape, "wave")
        self.assertEqual(playroom.logo_height, "68px")
        self.assertEqual(playroom.background_strategy, "flat")


class ChildcareDividerTest(unittest.TestCase):
    """No industry pins a seam shape any more: the wave is playroom's (and
    soft-warm's) voice, and a luxury-mood school gets the mood's no-seam."""

    @staticmethod
    def _sections():
        return [
            BuilderElement(
                name=name, type="section",
                styles={"backgroundColor": bg, "width": "100%"}, content=[],
            )
            # Two colours, or there is no seam to shape.
            for name, bg in (("Hero", "#ffffff"), ("Features", "#fdf2f8"), ("CTA", "#ffffff"))
        ]

    def test_mood_decides_without_a_scheme(self):
        sections = self._sections()
        apply_section_dividers(sections, "luxury")
        self.assertIsNone(sections[0].divider)  # luxury has no shaped edge

    def test_playroom_seams_are_waves_under_any_mood(self):
        for mood in ("playful", "luxury", "modern", "friendly"):
            sections = self._sections()
            apply_section_dividers(sections, mood, by_slug("playroom"))
            hero, _, cta = sections
            self.assertEqual(hero.divider.bottom.shape, "wave", mood)
            self.assertEqual(cta.divider.top.shape, "wave", mood)


def _theme(logo, name="Glorykids"):
    with schemes_on():
        return build_theme(
            logo, mood="friendly", palette_mode="auto", font_seed=name,
            industry="childcare", scheme_choice="playroom",
        )


class BandTintTest(unittest.TestCase):
    """Multi-tint bands: light tints of THIS site's hues, not a fixed rainbow."""

    @staticmethod
    def _flat(name, bg="#ffffff"):
        return BuilderElement(
            name=name, type="section",
            styles={"backgroundColor": bg, "width": "100%"}, content=[],
        )

    def test_tints_are_distinct_light_and_legible_under_the_theme_ink(self):
        theme = _theme("#16a34a")
        tints = band_tints(theme)
        self.assertGreaterEqual(len(tints), 2)
        self.assertEqual(len(set(tints)), len(tints))
        for tint in tints:
            self.assertGreaterEqual(_contrast(tint, theme.palette.text), 7.0, tint)

    def test_two_brands_get_two_tint_sets(self):
        self.assertNotEqual(band_tints(_theme("#16a34a")), band_tints(_theme("#7c3aed")))

    def test_flat_sections_rotate_through_the_tints(self):
        theme = _theme("#ea580c")
        tints = band_tints(theme)
        sections = [self._flat(n) for n in ("Features", "About", "Services", "Gallery")]
        apply_band_tints(sections, tints, theme.palette.text)
        bgs = [s.styles["backgroundColor"] for s in sections]
        self.assertEqual(bgs, [tints[i % len(tints)] for i in range(4)])

    def test_photo_and_gradient_sections_are_left_untouched(self):
        photo = BuilderElement(
            name="Hero", type="section", styles={"backgroundImage": "url('x.jpg')"}, content=[],
        )
        gradient = BuilderElement(
            name="CTA", type="section",
            styles={"background": "linear-gradient(#000,#111)"}, content=[],
        )
        flat = self._flat("Features")
        apply_band_tints([photo, gradient, flat], ("#eef6ff",), "#0c4a6e")
        self.assertNotIn("backgroundColor", photo.styles)
        self.assertEqual(gradient.styles.get("background"), "linear-gradient(#000,#111)")
        self.assertEqual(flat.styles["backgroundColor"], "#eef6ff")

    def test_dark_band_is_replaced_by_a_tint_with_dark_ink(self):
        text = BuilderElement(
            name="Heading", type="text", styles={"color": "#ffffff"},
            content=BuilderElementContent(innerText="Our Services"),
        )
        band = BuilderElement(
            name="Services", type="section",
            styles={"backgroundColor": "#0c4a6e", "borderTop": "1px solid #222"},
            content=[text],
        )
        apply_band_tints([band], ("#eef6ff",), "#0c4a6e")
        self.assertEqual(band.styles["backgroundColor"], "#eef6ff")
        self.assertNotIn("borderTop", band.styles)  # stray dark hairline dropped
        self.assertEqual(text.styles["color"], "#0c4a6e")  # ink flipped dark


class SectionTitleInkTest(unittest.TestCase):
    @staticmethod
    def _section(name, *, heading=None, bg="#FFF7ED", card_title=False):
        kids = []
        if heading is not None:
            kids.append(BuilderElement(
                name="Heading", type="text", styles={"color": "#0c4a6e"},
                content=BuilderElementContent(innerText=heading),
            ))
        if card_title:
            kids.append(BuilderElement(
                name="Feature title", type="text", styles={"color": "#0c4a6e"},
                content=BuilderElementContent(innerText="A card title"),
            ))
        return BuilderElement(name=name, type="section", styles={"backgroundColor": bg}, content=kids)

    @staticmethod
    def _heading_color(section):
        return next(c.styles.get("color") for c in section.content if c.name == "Heading")

    def test_titles_take_distinct_brand_inks_each_aa_on_its_band(self):
        theme = _theme("#0891b2")
        bands = ("#FFF7ED", "#EAF6FB", "#FDF2F8")
        secs = [
            self._section(n, heading=n, bg=bg)
            for n, bg in zip(("Features", "About", "Services"), bands)
        ]
        apply_section_title_inks(secs, section_title_inks(theme))
        colors = [self._heading_color(s) for s in secs]
        self.assertGreaterEqual(len(set(colors)), 3)
        for color, bg in zip(colors, bands):
            self.assertGreaterEqual(_contrast(color, bg), 4.5, (color, bg))

    def test_card_titles_are_left_alone(self):
        sec = self._section("Features", heading="Why Us", card_title=True)
        apply_section_title_inks([sec], ("#be123c",))
        card = next(c for c in sec.content if c.name == "Feature title")
        self.assertEqual(card.styles.get("color"), "#0c4a6e")

    def test_hero_and_photo_sections_are_skipped(self):
        hero = self._section("Hero", heading="Welcome")
        photo = BuilderElement(
            name="CTA", type="section", styles={"backgroundImage": "url('x.jpg')"},
            content=[BuilderElement(name="Heading", type="text", styles={"color": "#fff"},
                                    content=BuilderElementContent(innerText="Join Us"))],
        )
        apply_section_title_inks([hero, photo], ("#be123c",))
        self.assertEqual(self._heading_color(hero), "#0c4a6e")
        self.assertEqual(self._heading_color(photo), "#fff")


class HeroTitleAccentTest(unittest.TestCase):
    """A scheme with `hero_title_accent` two-tones a plain headline in the
    BRAND's colours; it used to be childcare-only, in a fixed butter/coral set."""

    @staticmethod
    def _hero_el(headline):
        heading = BuilderElement(
            name="Heading", type="text", styles={"color": "#ffffff", "textAlign": "center"},
            content=BuilderElementContent(innerText=headline),
        )
        return BuilderElement(name="Hero", type="section", styles={}, content=[heading])

    def _texts(self, el, out=None):
        out = [] if out is None else out
        if el.type == "text" and not isinstance(el.content, list):
            out.append(el)
        if isinstance(el.content, list):
            for c in el.content:
                self._texts(c, out)
        return out

    def _ctx(self, theme):
        return RenderContext(theme=theme, resolver=None, styles=make_style_tokens(theme))

    def test_playroom_splits_a_plain_headline_into_the_brands_two_inks(self):
        """The accent line is the brand accent lifted for the surface the photo
        hero MEASURED (its painted surface), not an assumed backdrop; the lead
        line keeps the template's neutral ink for the ink pass to restate."""
        from app.services.section_content import enforce_text_contrast
        from app.services.theme import ink_css, ink_on

        theme = _theme("#7c3aed")
        ctx = self._ctx(theme)
        el = self._hero_el("Welcome to Glorykids")
        el.paint_surface("#2b2e36")  # what the photo's solved scrim left
        _apply_hero_typography(el, HeroBlock(headline="Welcome to Glorykids"), ctx,
                               template_id="hero-background-bold")
        lines = [t for t in self._texts(el) if t.content.innerText]
        self.assertEqual([t.content.innerText for t in lines], ["Welcome to", "Glorykids"])
        self.assertEqual(lines[1].styles["color"], _accent_ink_for("#2b2e36", theme.palette.accent))
        enforce_text_contrast([el], theme)
        self.assertEqual(
            lines[0].styles["color"], ink_css(theme.palette, ink_on(theme.palette, "#2b2e36"))
        )
        self.assertNotIn("#ffffff", lines[0].styles["color"].lower())

    def test_without_the_scheme_a_plain_headline_stays_one_line(self):
        theme = build_theme("#7c3aed", mood="friendly", industry="childcare")
        el = self._hero_el("Welcome to Glorykids")
        _apply_hero_typography(el, HeroBlock(headline="Welcome to Glorykids"), self._ctx(theme),
                               template_id="hero-background-bold")
        self.assertEqual(len(self._texts(el)), 1)

    def test_neutral_cast_constant_does_not_tint_the_photo(self):
        styles = photo_background(
            "#808080", "https://x/p.jpg", _NEUTRAL_PHOTO_CAST, _NEUTRAL_PHOTO_CAST
        )
        css = styles["backgroundImage"]
        self.assertNotIn("var(--builder", css)
        hues = set(re.findall(r"rgba\((\d+),(\d+),(\d+),", css.replace(" ", "")))
        self.assertEqual(hues, {("15", "23", "42")})


class ChildcareLogoTest(unittest.TestCase):
    def _brand(self):
        return BrandIdentity(
            name="Glorykids",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#F97316"],
            logo_is_light=True,
        )

    def test_no_box_around_logo(self):
        theme = build_theme("#F97316", industry="childcare")
        header = build_header(self._brand(), theme, nav_items=[])
        self.assertIsNone(_find_by_name(header, "Logo lockup"))

    def test_light_logo_switches_header_to_secondary_not_slate(self):
        theme = build_theme("#F97316", industry="childcare")
        header = build_header(self._brand(), theme, nav_items=[])
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.secondary)

    def test_logo_size_is_the_schemes_not_the_industrys(self):
        default = _find_by_name(
            build_header(self._brand(), build_theme("#F97316", industry="childcare"), nav_items=[]),
            "Brand Logo",
        )
        self.assertEqual(default.styles.get("height"), "52px")
        playroom = _find_by_name(
            build_header(self._brand(), _theme("#F97316"), nav_items=[]), "Brand Logo"
        )
        self.assertEqual(playroom.styles.get("height"), "68px")


class ChildcareMidpointSplitTest(unittest.TestCase):
    def test_splits_near_the_middle(self):
        self.assertEqual(_midpoint_word_split("Welcome to Glorykids"),
                         ("Welcome to", "Glorykids"))

    def test_single_word_is_not_split(self):
        self.assertIsNone(_midpoint_word_split("Glorykids"))

    def test_collapses_whitespace(self):
        self.assertEqual(_midpoint_word_split("  a   b  "), ("a", "b"))


class ChildcareImageryTest(unittest.TestCase):
    def test_industry_context_query_registered(self):
        self.assertIn("childcare", _INDUSTRY_CONTEXT_QUERIES)
        self.assertIn("children", _INDUSTRY_CONTEXT_QUERIES["childcare"])

    def test_childcare_tokens_prefer_children_over_empty_classrooms(self):
        q = _contextual_non_person_query("kindergarten classroom activities")
        self.assertIn("children", q)


class PlayroomIsAChoiceNotADefaultTest(unittest.TestCase):
    """End to end through plan_to_site: the multi-tint look appears when the
    scheme is playroom and not otherwise, whatever the industry."""

    def _build(self, scheme_slug, industry="childcare"):
        import asyncio

        from scripts.measure_design_diversity import fixture_brand, fixture_plan, offline_generation
        from app.services import design_schemes
        from app.services.design_fingerprint import fingerprint_of
        from app.services.schema_builder import plan_to_site

        original = design_schemes.select_scheme
        design_schemes.select_scheme = lambda **_kw: by_slug(scheme_slug)
        try:
            with offline_generation(schemes=True, history=False):
                site = asyncio.run(plan_to_site(
                    fixture_plan("Sunbeam House", industry),
                    brand=fixture_brand("Sunbeam House", "#0891b2", industry),
                ))
        finally:
            design_schemes.select_scheme = original
        return fingerprint_of(site)

    def test_playroom_paints_tints_and_coloured_titles(self):
        fp = self._build("playroom")
        self.assertTrue(all(b in ("tint", "photo") for b in fp.bands), fp.bands)
        self.assertGreaterEqual(fp.title_inks, 2)
        self.assertEqual(fp.divider, "wave")

    def test_another_childcare_scheme_does_not(self):
        fp = self._build("civic-trust")
        self.assertIn("light", fp.bands)
        self.assertEqual(fp.title_inks, 0)
        self.assertNotEqual(fp.divider, "wave")

    def test_playroom_is_not_childcare_property(self):
        fp = self._build("playroom", industry="nonprofit")
        self.assertGreaterEqual(fp.title_inks, 2)


if __name__ == "__main__":
    unittest.main()
