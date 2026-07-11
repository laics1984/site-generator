"""Childcare / kindergarten industry wiring.

The childcare design brief (parent-first trust, soft pastel palette, rounded
friendly type, authentic children-at-play photography, book-a-tour conversion
structure) is spread across the industry-keyed registries. These tests pin
each registry's childcare entry so the brief keeps flowing into generation.
"""

import unittest

from app.models.brand import BrandIdentity
from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.models.content_blocks import (
    HeroBlock,
    PagePlan,
    industry_default_mood,
    industry_locked_mood,
)
from app.services.header_footer import build_header
from app.services.hero_director import _CHILDCARE_SPEC, plan_site_heroes
from app.services.industry_personality import personality_for
from app.services.industry_templates import get_template
from app.services.landing_patterns import homepage_sections
from app.services.media import _contextual_non_person_query, _INDUSTRY_CONTEXT_QUERIES
from app.services.schema_builder import (
    _CHILDCARE_HERO_INKS,
    _CHILDCARE_HERO_SCRIM,
    _apply_hero_typography,
    _midpoint_word_split,
    apply_section_dividers,
    make_style_tokens,
    modernize_sections,
    RenderContext,
)
from app.services.section_content import (
    _CHILDCARE_HEADING_INKS,
    _CHILDCARE_PASTELS,
    apply_childcare_heading_colors,
    apply_childcare_pastel_rhythm,
)
from app.services.theme import _CURATED_PALETTES, build_theme, resolve_color_scheme


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
        # Signature visual moves from the design brief.
        self.assertIn("rounded", p.design)
        self.assertIn("pastel", p.design)


class ChildcareMoodTest(unittest.TestCase):
    def test_defaults_to_friendly_not_playful(self):
        # Joyful without being childish: the audience is parents.
        self.assertEqual(industry_default_mood("childcare"), "friendly")

    def test_mood_is_locked_against_detection_noise(self):
        # The detected mood is advisory for childcare — a stray `playful`
        # (loud type, dramatic shadows) must not restyle a kindergarten.
        self.assertEqual(industry_locked_mood("childcare"), "friendly")

    def test_other_industries_leave_mood_to_detection(self):
        self.assertIsNone(industry_locked_mood("restaurant"))
        self.assertIsNone(industry_locked_mood(None))


class ChildcareColorSchemeTest(unittest.TestCase):
    def test_light_logo_never_defaults_childcare_to_dark(self):
        # The Glorykids failure: a bright logo read as "light" flipped the
        # whole site dark, bypassing the pastel palettes. Childcare is
        # light-only by brief.
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
        # Parent-first conversion journey: why-us → philosophy → programs →
        # social proof → book-a-tour. (Process/teachers/FAQ/gallery moved to
        # their own pages to keep the homepage generatable in one call.)
        for kind in ("features", "about", "services", "testimonials"):
            self.assertIn(kind, sections)

    def test_homepage_stays_within_single_call_budget(self):
        # The pattern plus any merged extras must never exceed the ceiling a
        # single LLM call can generate — the root cause of the 502.
        self.assertLessEqual(len(homepage_sections("childcare")), 8)


class ChildcareHeroDirectionTest(unittest.TestCase):
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
            industry="childcare",
            has_source_background=False,
            seed="Sunny Days Kindergarten",
        )
        self.assertEqual(directives["home"].template_id, "hero-background-bold")
        self.assertEqual(directives["home"].layout, "background")

    def test_transactional_pages_stay_calm_and_centered_in_legacy_rotation(self):
        # The compact-interior art direction survives behind the site-wide
        # full-bleed policy flag (hero_fullbleed_all_pages=False).
        from unittest import mock

        from app.config import settings

        with mock.patch.object(settings, "hero_fullbleed_all_pages", False):
            directives = plan_site_heroes(
                self._pages(),
                mood="friendly",
                industry="childcare",
                has_source_background=False,
                seed="Sunny Days Kindergarten",
            )
        self.assertEqual(directives["contact"].template_id, "hero-centered-minimal")

    def test_default_policy_directs_every_childcare_page_full_bleed(self):
        # Site-wide transparent header: every page opens with a background hero.
        directives = plan_site_heroes(
            self._pages(),
            mood="friendly",
            industry="childcare",
            has_source_background=False,
            seed="Sunny Days Kindergarten",
        )
        for directive in directives.values():
            self.assertEqual(directive.template_id, "hero-background-bold")


class ChildcareThemeTest(unittest.TestCase):
    def test_curated_palette_stays_soft_and_light(self):
        theme = build_theme(
            None,
            mood="friendly",
            palette_mode="auto",  # no logo colour → curated industry palette
            font_seed="Sunny Days Kindergarten",
            industry="childcare",
        )
        # Light, warm surfaces — the brief bans dark themes.
        self.assertEqual(theme.palette.background.lower(), "#ffffff")
        self.assertIn(
            theme.palette.primary.upper(), {"#0284C7", "#059669", "#8B5CF6"}
        )

    def test_font_pairing_is_rounded_kids_pairing(self):
        theme = build_theme(
            None,
            mood="friendly",
            palette_mode="auto",
            font_seed="Sunny Days Kindergarten",
            industry="childcare",
        )
        # The friendly pool's children/kids-tagged pairings are the rounded ones.
        self.assertTrue(
            any(
                face in theme.typography.heading_font
                for face in ("Fredoka", "Varela Round")
            ),
            theme.typography.heading_font,
        )

    def test_explicit_playful_mood_still_gets_a_rounded_face(self):
        # Safety net: a kindergarten explicitly set to playful must land on
        # the pool's child-tagged rounded pairing, not the seeded vintage
        # serif (Abril Fatface) that produced the Glorykids look.
        theme = build_theme(
            None,
            mood="playful",
            palette_mode="auto",
            font_seed="Glorykids",
            industry="childcare",
        )
        self.assertIn("Baloo 2", theme.typography.heading_font)


class ChildcareBackgroundStrategyTest(unittest.TestCase):
    def test_childcare_uses_pastel_bands_not_a_mono_mesh(self):
        # "Layered backgrounds" now come from the multi-pastel section rhythm,
        # not a single-hue mesh — so the theme strategy stays flat.
        theme = build_theme(None, mood="friendly", industry="childcare")
        self.assertEqual(theme.background_strategy, "flat")

    def test_other_friendly_industries_stay_flat(self):
        theme = build_theme(None, mood="friendly", industry="nonprofit")
        self.assertEqual(theme.background_strategy, "flat")


class ChildcareDividerTest(unittest.TestCase):
    """Flowing wave seams are the industry's signature — guaranteed whatever
    mood the detection LLM picked (peak/slant read sharp, luxury has none)."""

    @staticmethod
    def _sections():
        return [
            BuilderElement(
                name=name, type="section",
                styles={"backgroundColor": "#ffffff", "width": "100%"}, content=[],
            )
            for name in ("Hero", "Features", "CTA")
        ]

    def test_wave_seams_under_any_mood(self):
        for mood in ("playful", "luxury", "modern", "friendly"):
            sections = self._sections()
            apply_section_dividers(sections, mood, "childcare")
            hero, _, cta = sections
            self.assertIsNotNone(hero.divider, mood)
            self.assertEqual(hero.divider.bottom.shape, "wave", mood)
            self.assertEqual(cta.divider.top.shape, "wave", mood)

    def test_other_industries_keep_mood_shape(self):
        sections = self._sections()
        apply_section_dividers(sections, "luxury", "restaurant")
        self.assertIsNone(sections[0].divider)  # luxury has no shaped edge

    def test_pastel_bands_and_wave_seams_coexist(self):
        # Real build order: pastel rhythm colours the flat sections, then the
        # dividers read those pastel colours for their seam fill.
        theme = build_theme(None, mood="friendly", industry="childcare")
        page_bg = theme.page.background
        sections = [
            BuilderElement(
                name=name, type="section",
                styles={"backgroundColor": page_bg, "width": "100%"}, content=[],
            )
            for name in ("Hero", "Features", "About", "Testimonials", "CTA")
        ]

        apply_childcare_pastel_rhythm(sections, theme.palette.text)
        apply_section_dividers(sections, "friendly", "childcare")

        # Distinct pastels landed on the flat bands.
        bgs = [s.styles["backgroundColor"] for s in sections]
        self.assertEqual(len(set(bgs)), len(bgs))
        self.assertTrue(all(bg in _CHILDCARE_PASTELS for bg in bgs))
        # The wave seam fill reads the revealed neighbour's pastel colour.
        self.assertEqual(sections[0].divider.bottom.shape, "wave")
        self.assertEqual(sections[0].divider.bottom.color, sections[1].styles["backgroundColor"])


class ChildcarePastelRhythmTest(unittest.TestCase):
    """The multi-pastel section rhythm: what actually makes a childcare page read
    cheerful and multi-coloured (cream/sky/mint/peach/...) instead of one hue."""

    @staticmethod
    def _flat(name):
        return BuilderElement(
            name=name, type="section",
            styles={"backgroundColor": "#ffffff", "width": "100%"}, content=[],
        )

    def test_flat_sections_rotate_through_distinct_pastels(self):
        sections = [self._flat(n) for n in ("Features", "About", "Services", "Gallery")]
        apply_childcare_pastel_rhythm(sections, "#0c4a6e")
        bgs = [s.styles["backgroundColor"] for s in sections]
        self.assertEqual(bgs, list(_CHILDCARE_PASTELS[:4]))
        self.assertEqual(len(set(bgs)), 4)  # multi-coloured, not one hue

    def test_photo_and_gradient_sections_are_left_untouched(self):
        photo = BuilderElement(
            name="Hero", type="section",
            styles={"backgroundImage": "url('x.jpg')"}, content=[],
        )
        gradient = BuilderElement(
            name="CTA", type="section",
            styles={"background": "linear-gradient(#000,#111)"}, content=[],
        )
        flat = self._flat("Features")
        apply_childcare_pastel_rhythm([photo, gradient, flat], "#0c4a6e")
        # The designed photo/gradient moments survive; only the flat band recolours.
        self.assertNotIn("backgroundColor", photo.styles)
        self.assertEqual(gradient.styles.get("background"), "linear-gradient(#000,#111)")
        self.assertEqual(flat.styles["backgroundColor"], _CHILDCARE_PASTELS[0])

    def test_dark_band_is_replaced_by_pastel_with_dark_ink(self):
        # A former dark band: dark bg + light text. The pastel pass flips it to a
        # light pastel and recolours the text dark so it stays legible.
        text = BuilderElement(
            name="Heading", type="text",
            styles={"color": "#ffffff"},
            content=BuilderElementContent(innerText="Our Services"),
        )
        band = BuilderElement(
            name="Services", type="section",
            styles={"backgroundColor": "#0c4a6e", "borderTop": "1px solid #222"},
            content=[text],
        )
        apply_childcare_pastel_rhythm([band], "#0c4a6e")
        self.assertEqual(band.styles["backgroundColor"], _CHILDCARE_PASTELS[0])
        self.assertNotIn("borderTop", band.styles)  # stray dark hairline dropped
        self.assertEqual(text.styles["color"], "#0c4a6e")  # ink flipped dark

    def test_pastels_are_light_enough_for_dark_ink(self):
        # Every pastel must clear WCAG AA (4.5:1) against the dark ink so no band
        # ever renders low-contrast text.
        from app.services.theme import _contrast
        for pastel in _CHILDCARE_PASTELS:
            self.assertGreaterEqual(_contrast(pastel, "#0c4a6e"), 4.5, pastel)


class ChildcarePaletteColorfulnessTest(unittest.TestCase):
    def _childcare_primaries(self):
        return {
            c.primary.upper()
            for c in _CURATED_PALETTES
            if "childcare" in c.categories
        }

    def test_brand_logo_hue_is_ignored_for_the_pastel_set(self):
        # The Glorykids failure: an orange logo snapped to a muted mono Tailwind
        # palette. Childcare now always uses the curated pastel set instead.
        theme = build_theme(
            "#F97316",  # orange logo hue — would previously drive the snap
            mood="friendly",
            palette_mode="auto",
            font_seed="Glorykids",
            industry="childcare",
        )
        self.assertIn(theme.palette.primary.upper(), self._childcare_primaries())

    def test_secondary_is_not_the_near_black_slate(self):
        # The dull dark bands came from secondary = slate-900. Curated pastel
        # palettes carry a softer dark token instead.
        theme = build_theme(
            "#F97316", mood="friendly", palette_mode="auto",
            font_seed="Glorykids", industry="childcare",
        )
        self.assertNotIn(theme.palette.secondary.upper(), {"#0F172A", "#020617"})


class ChildcareLogoTest(unittest.TestCase):
    def _brand(self):
        return BrandIdentity(
            name="Glorykids",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#F97316"],
            logo_is_light=True,
        )

    def test_no_contrast_chip_border_around_logo(self):
        theme = build_theme("#F97316", industry="childcare")
        header = build_header(self._brand(), theme, nav_items=[], industry="childcare")
        # The "border" the user saw was the Logo lockup chip — gone for childcare.
        self.assertIsNone(_find_by_name(header, "Logo lockup"))

    def test_logo_is_enlarged(self):
        theme = build_theme("#F97316", industry="childcare")
        header = build_header(self._brand(), theme, nav_items=[], industry="childcare")
        logo = _find_by_name(header, "Brand Logo")
        self.assertIsNotNone(logo)
        self.assertEqual(logo.styles.get("height"), "68px")

    def test_other_industries_keep_chip_and_default_size(self):
        theme = build_theme("#2563eb")  # light theme, light logo
        header = build_header(self._brand(), theme, nav_items=[], industry="restaurant")
        self.assertIsNotNone(_find_by_name(header, "Logo lockup"))
        logo = _find_by_name(header, "Brand Logo")
        self.assertEqual(logo.styles.get("height"), "52px")


class ChildcareHeroContrastTest(unittest.TestCase):
    def test_white_text_gradient_hero_excluded_from_rotation(self):
        # hero-gradient hardcodes white text; over childcare's pastel primary
        # its light end left the copy illegible. It must not be in the rotation.
        rotation_ids = {d.template_id for d in _CHILDCARE_SPEC.rotation}
        self.assertNotIn("hero-gradient", rotation_ids)

    def test_interior_heroes_are_light_bg_dark_text_variants(self):
        # Every childcare interior hero should be a light-background/dark-ink
        # variant (split-washed, centered-minimal, editorial) — never the
        # full-bleed white-text templates reserved for the homepage.
        allowed = {"hero-modern-split", "hero-centered-minimal", "hero-editorial"}
        ids = {d.template_id for d in _CHILDCARE_SPEC.rotation}
        ids |= {d.template_id for d in _CHILDCARE_SPEC.by_page_type.values()}
        self.assertTrue(ids <= allowed, ids - allowed)


class ChildcareHeroColorTest(unittest.TestCase):
    """The hero title should be multi-coloured (not just white + one theme hue),
    and the hero photo should show its real colours (no brand-tint filter)."""

    def _ctx(self, industry="childcare"):
        theme = build_theme(None, mood="friendly", palette_mode="auto",
                            font_seed="Glorykids", industry="childcare")
        return RenderContext(
            theme=theme, resolver=None, styles=make_style_tokens(theme),
            industry=industry,
        )

    @staticmethod
    def _hero_el(headline, eyebrow=None):
        kids = []
        if eyebrow:
            kids.append(BuilderElement(
                name="Eyebrow", type="text", styles={"color": "#ffffff"},
                content=BuilderElementContent(innerText=eyebrow),
            ))
        kids.append(BuilderElement(
            name="Heading", type="text", styles={"color": "#ffffff", "textAlign": "center"},
            content=BuilderElementContent(innerText=headline),
        ))
        return BuilderElement(name="Hero", type="section", styles={}, content=kids)

    def _texts(self, el, out=None):
        out = [] if out is None else out
        if el.type == "text" and not isinstance(el.content, list):
            out.append(el)
        if isinstance(el.content, list):
            for c in el.content:
                self._texts(c, out)
        return out

    def test_scrim_constant_is_neutral_not_brand_tinted(self):
        # No theme-colour filter: the childcare hero scrim is a plain slate
        # gradient with no brand CSS variables or hues.
        self.assertNotIn("var(--builder", _CHILDCARE_HERO_SCRIM)
        self.assertIn("15,23,42", _CHILDCARE_HERO_SCRIM)  # neutral slate only

    def test_title_uses_multiple_bright_inks(self):
        el = self._hero_el("Where little minds grow", eyebrow="Established 2004")
        block = HeroBlock(headline="Where little minds grow",
                          headline_accent="grow", eyebrow="Established 2004")
        _apply_hero_typography(el, block, self._ctx(), template_id="hero-background-bold")
        colors = {t.styles.get("color") for t in self._texts(el)}
        # Butter lead, coral accent, sky eyebrow — none is white or the theme hue.
        self.assertIn(_CHILDCARE_HERO_INKS[0], colors)  # butter lead
        self.assertIn(_CHILDCARE_HERO_INKS[1], colors)  # coral accent
        self.assertIn(_CHILDCARE_HERO_INKS[2], colors)  # sky eyebrow
        self.assertNotIn("#ffffff", colors)

    def test_plain_headline_is_split_into_two_colours(self):
        # No explicit accent phrase → midpoint split still yields two colours.
        el = self._hero_el("Welcome to Glorykids")
        block = HeroBlock(headline="Welcome to Glorykids")
        _apply_hero_typography(el, block, self._ctx(), template_id="hero-background-bold")
        line_colors = [t.styles.get("color") for t in self._texts(el)]
        self.assertIn(_CHILDCARE_HERO_INKS[0], line_colors)
        self.assertIn(_CHILDCARE_HERO_INKS[1], line_colors)

    def test_other_industries_hero_is_unchanged(self):
        el = self._hero_el("Bread baked the slow way", eyebrow="Since 1998")
        block = HeroBlock(headline="Bread baked the slow way",
                          headline_accent="the slow way", eyebrow="Since 1998")
        _apply_hero_typography(el, block, self._ctx(industry="restaurant"),
                              template_id="hero-background-bold")
        colors = {t.styles.get("color") for t in self._texts(el)}
        # No childcare inks leak into other industries.
        self.assertFalse(colors & set(_CHILDCARE_HERO_INKS))


class ChildcareHeadingColorTest(unittest.TestCase):
    """Section titles rotate through vivid, legible colours (rose/violet/blue/...)
    so headings read multi-coloured, not one dark theme hue."""

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
        return BuilderElement(name=name, type="section",
                              styles={"backgroundColor": bg}, content=kids)

    def _heading_color(self, section):
        for c in section.content:
            if c.name == "Heading":
                return c.styles.get("color")
        return None

    def test_each_section_title_gets_a_distinct_vivid_colour(self):
        secs = [
            self._section("Features", heading="Why Parents Choose Us"),
            self._section("About", heading="Our Philosophy"),
            self._section("Services", heading="Programs by Age"),
        ]
        apply_childcare_heading_colors(secs)
        colors = [self._heading_color(s) for s in secs]
        self.assertEqual(colors, list(_CHILDCARE_HEADING_INKS[:3]))
        self.assertEqual(len(set(colors)), 3)  # multi-coloured

    def test_card_titles_and_body_are_left_dark(self):
        sec = self._section("Features", heading="Why Us", card_title=True)
        apply_childcare_heading_colors([sec])
        card = next(c for c in sec.content if c.name == "Feature title")
        self.assertEqual(card.styles.get("color"), "#0c4a6e")  # untouched

    def test_hero_and_photo_sections_are_skipped(self):
        hero = self._section("Hero", heading="Welcome")
        photo = BuilderElement(
            name="CTA", type="section",
            styles={"backgroundImage": "url('x.jpg')"},
            content=[BuilderElement(name="Heading", type="text", styles={"color": "#fff"},
                                    content=BuilderElementContent(innerText="Join Us"))],
        )
        apply_childcare_heading_colors([hero, photo])
        self.assertEqual(self._heading_color(hero), "#0c4a6e")  # hero untouched
        self.assertEqual(self._heading_color(photo), "#fff")    # photo title stays

    def test_heading_inks_clear_wcag_on_every_pastel(self):
        from app.services.theme import _contrast
        for ink in _CHILDCARE_HEADING_INKS:
            for pastel in _CHILDCARE_PASTELS:
                self.assertGreaterEqual(_contrast(ink, pastel), 4.5, (ink, pastel))


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
        # The brief bans empty classrooms — kindergarten queries must resolve
        # to candid children-at-play imagery, not the generic school bucket.
        q = _contextual_non_person_query("kindergarten classroom activities")
        self.assertIn("children", q)


if __name__ == "__main__":
    unittest.main()
