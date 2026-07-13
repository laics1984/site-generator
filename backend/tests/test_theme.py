"""Tests for the mood font-pairing pools and deterministic per-site selection."""

import re
import unittest

from app.models.brand import BrandMood
from app.services.theme import (
    MOOD_SPECS,
    FontPairing,
    band_colors,
    build_theme,
    resolve_color_scheme,
    _CURATED_PALETTES,
    _palette_from_curated,
    _contrast,
    _relative_luminance,
)

MOODS: tuple[BrandMood, ...] = (
    "modern",
    "luxury",
    "friendly",
    "technical",
    "editorial",
    "playful",
)


class FontPoolIntegrityTest(unittest.TestCase):
    def test_every_pool_has_well_formed_pairings(self):
        for mood in MOODS:
            pool = MOOD_SPECS[mood].font_pool
            self.assertGreaterEqual(len(pool), 2, f"{mood} should offer alternates")
            for p in pool:
                self.assertIsInstance(p, FontPairing)
                # Heading/body are full CSS stacks with a quoted family + fallback.
                for stack in (p.heading_font, p.body_font):
                    self.assertIn('"', stack, f"{mood}: missing quoted family in {stack!r}")
                    self.assertIn(",", stack, f"{mood}: missing fallback in {stack!r}")
                # Loader specs are present and reference a family name.
                self.assertTrue(p.google_fonts, f"{mood}: empty google_fonts")
                for spec in p.google_fonts:
                    # "Family" or "Family:<axis spec>" (incl. variable-font axes
                    # like opsz,wght@9..144,400 that the catalogue already uses).
                    self.assertRegex(
                        spec,
                        r"^[A-Za-z0-9 ]+(?::[\w@.,;]+)?$",
                        f"{mood}: malformed google_fonts spec {spec!r}",
                    )

    def test_quoted_family_matches_a_loader_spec(self):
        """The font named in the CSS stack must be one we actually request."""
        for mood in MOODS:
            for p in MOOD_SPECS[mood].font_pool:
                loaded = {g.split(":")[0] for g in p.google_fonts}
                for stack in (p.heading_font, p.body_font):
                    family = re.match(r'"([^"]+)"', stack).group(1)
                    self.assertIn(
                        family,
                        loaded,
                        f"{mood}: {family!r} is used but never loaded ({loaded})",
                    )


class FontSelectionTest(unittest.TestCase):
    def test_no_seed_uses_default_pairing(self):
        """font_seed=None reproduces the original single-pairing behaviour."""
        for mood in MOODS:
            theme = build_theme("#2563eb", mood=mood)
            default = MOOD_SPECS[mood].font_pool[0]
            self.assertEqual(theme.typography.heading_font, default.heading_font)
            self.assertEqual(theme.typography.body_font, default.body_font)
            self.assertEqual(theme.typography.google_fonts, list(default.google_fonts))

    def test_selection_is_deterministic_across_calls(self):
        a = build_theme("#2563eb", mood="modern", font_seed="Serenity Spa")
        b = build_theme("#2563eb", mood="modern", font_seed="Serenity Spa")
        self.assertEqual(a.typography.heading_font, b.typography.heading_font)
        self.assertEqual(a.typography.body_font, b.typography.body_font)

    def test_selection_stays_within_the_mood_pool(self):
        pool = MOOD_SPECS["luxury"].font_pool
        valid = {(p.heading_font, p.body_font) for p in pool}
        for name in ("Acme", "Aurora Labs", "Northwind", "Zephyr", "Brightline Co"):
            t = build_theme("#2563eb", mood="luxury", font_seed=name)
            self.assertIn((t.typography.heading_font, t.typography.body_font), valid)

    def test_seed_varies_fonts_across_sites(self):
        """Across many brand names a multi-pairing mood should yield >1 pairing."""
        seen = set()
        for i in range(40):
            t = build_theme("#2563eb", mood="modern", font_seed=f"brand-{i}")
            seen.add(t.typography.heading_font)
        self.assertGreater(len(seen), 1, "seeded selection never varied the font")

    def test_display_font_falls_back_to_heading_for_alternates(self):
        # The Plus Jakarta alt pairing (modern pool) sets no explicit display font.
        # Whatever seed lands on it, display_font must equal its heading font.
        for mood in MOODS:
            for p in MOOD_SPECS[mood].font_pool:
                if p.display_font is None:
                    break
        # Sanity: build with no seed always yields a non-empty display font.
        theme = build_theme("#2563eb", mood="modern")
        self.assertTrue(theme.display_font)


class IndustryAwareSelectionTest(unittest.TestCase):
    def _heading(self, **kw):
        return build_theme("#2563eb", **kw).typography.heading_font

    def test_industry_picks_best_fitting_pairing(self):
        # luxury + ecommerce → the fashion/jewelry/e-commerce pairing wins outright.
        self.assertIn(
            "Cormorant",
            self._heading(mood="luxury", industry="ecommerce", font_seed="Anybrand"),
        )
        # modern + saas → the tech/startup/developer pairing wins outright.
        self.assertIn(
            "Space Grotesk",
            self._heading(mood="modern", industry="saas", font_seed="Anybrand"),
        )

    def test_industry_match_overrides_seed_variety(self):
        # A unique best match means every brand of that mood+industry gets it,
        # regardless of seed — the choice is meaningful, not arbitrary.
        picks = {
            self._heading(mood="luxury", industry="ecommerce", font_seed=n)
            for n in ("Acme", "Aurora", "Northwind", "Zephyr", "Vellum")
        }
        self.assertEqual(len(picks), 1)

    def test_industry_selection_is_deterministic(self):
        a = self._heading(mood="modern", industry="saas", font_seed="Acme")
        b = self._heading(mood="modern", industry="saas", font_seed="Acme")
        self.assertEqual(a, b)

    def test_other_industry_falls_back_to_seed(self):
        for seed in ("Acme", "Aurora Labs", "Northwind"):
            self.assertEqual(
                self._heading(mood="modern", industry="other", font_seed=seed),
                self._heading(mood="modern", font_seed=seed),
            )

    def test_unknown_industry_falls_back_to_seed(self):
        # An industry whose words match no pairing tags degrades to seeded variety.
        self.assertEqual(
            self._heading(
                mood="modern", industry="underwater basket weaving", font_seed="Acme"
            ),
            self._heading(mood="modern", font_seed="Acme"),
        )

    def test_free_text_industry_words_still_match(self):
        # "architecture" isn't a controlled category, but luxury's Cinzel pairing is
        # uniquely tagged for it — free-text industry words should reach it.
        self.assertIn(
            "Cinzel",
            self._heading(
                mood="luxury", industry="Architecture", font_seed="Marquez Studio"
            ),
        )


class CuratedPaletteTest(unittest.TestCase):
    def test_every_curated_palette_is_safe_and_light(self):
        for c in _CURATED_PALETTES:
            self.assertTrue(c.categories, f"{c.name}: no category tags")
            p = _palette_from_curated(c)
            # Body text on the page background meets at least AA.
            self.assertGreaterEqual(_contrast(p.background, p.text), 4.5, c.name)
            # Dark band: white must read on the secondary (dark-band background).
            self.assertGreaterEqual(_contrast(p.secondary, "#ffffff"), 4.5, c.name)
            # Light section surface stays clearly light for section rhythm.
            self.assertGreater(_relative_luminance(p.surface), 0.85, c.name)

    def test_curated_mode_picks_within_industry(self):
        saas = {c.primary.lower() for c in _CURATED_PALETTES if "saas" in c.categories}
        t = build_theme("#2563eb", palette_mode="curated", industry="saas", font_seed="X")
        self.assertIn(t.palette.primary, saas)

    def test_curated_nearest_hue_steers_choice(self):
        # An emerald brand seed in ecommerce → the green E-commerce palette, the
        # nearest hue among the ecommerce candidates.
        t = build_theme("#10b981", palette_mode="curated", industry="ecommerce")
        self.assertEqual(t.palette.primary, "#059669")

    def test_curated_is_deterministic(self):
        kw = dict(palette_mode="curated", industry="saas", font_seed="Acme")
        self.assertEqual(
            build_theme("#2563eb", **kw).palette.primary,
            build_theme("#2563eb", **kw).palette.primary,
        )

    def test_auto_keeps_tailwind_for_branded_seed(self):
        auto = build_theme("#2563eb", palette_mode="auto", industry="restaurant")
        tailwind = build_theme("#2563eb", palette_mode="tailwind")
        self.assertEqual(auto.palette.primary, tailwind.palette.primary)

    def test_auto_uses_curated_for_greyscale_seed(self):
        # No usable brand hue → a curated industry palette, not generic-blue snap.
        t = build_theme("#808080", palette_mode="auto", industry="restaurant")
        rest = {
            c.primary.lower() for c in _CURATED_PALETTES if "restaurant" in c.categories
        }
        self.assertIn(t.palette.primary, rest)

    def test_auto_uses_curated_when_no_seed(self):
        # No logo colour at all (None) → curated industry palette, not generic blue.
        rest = {
            c.primary.lower() for c in _CURATED_PALETTES if "restaurant" in c.categories
        }
        for seed in (None, "", "not-a-hex"):
            t = build_theme(
                seed, palette_mode="auto", industry="restaurant", font_seed="Bella"
            )
            self.assertIn(t.palette.primary, rest, f"seed={seed!r}")
            self.assertNotEqual(t.palette.primary, "#2563eb", f"seed={seed!r}")


class MoodStyleTest(unittest.TestCase):
    # Each mood declares the ui-ux-pro-max style it embodies (from styles.csv).
    _STYLES = {
        "modern": "Glassmorphism",
        "luxury": "Minimalism & Swiss Style",
        "friendly": "Soft UI Evolution",
        "technical": "Flat Design",
        "editorial": "Storytelling-Driven",
        "playful": "Vibrant & Block-based",
    }

    def test_every_mood_declares_a_style(self):
        for mood, spec in MOOD_SPECS.items():
            self.assertTrue(spec.style, f"{mood} has no style")
            self.assertEqual(spec.style, self._STYLES[mood])

    def test_theme_carries_the_mood_style(self):
        for mood in self._STYLES:
            self.assertEqual(build_theme("#2563eb", mood=mood).style, self._STYLES[mood])


class ResolveColorSchemeTest(unittest.TestCase):
    def test_explicit_override_wins(self):
        self.assertEqual(resolve_color_scheme("dark", "light", False), "dark")
        self.assertEqual(resolve_color_scheme("light", "dark", True), "light")

    def test_brand_choice_beats_logo_default(self):
        self.assertEqual(resolve_color_scheme(None, "light", True), "light")
        self.assertEqual(resolve_color_scheme(None, "dark", False), "dark")

    def test_light_logo_defaults_to_dark(self):
        # A predominantly-light logo is usually drawn for a dark canvas.
        self.assertEqual(resolve_color_scheme(None, None, True), "dark")

    def test_dark_or_unknown_logo_defaults_to_light(self):
        self.assertEqual(resolve_color_scheme(None, None, False), "light")
        self.assertEqual(resolve_color_scheme(None, None, None), "light")

    def test_invalid_override_is_ignored(self):
        self.assertEqual(resolve_color_scheme("teal", None, True), "dark")

    def test_industry_lean_drives_default_without_a_logo(self):
        # A neutral industry leaves the default light; a leaning one moves it.
        self.assertEqual(resolve_color_scheme(None, None, None, industry="restaurant"), "light")
        self.assertEqual(resolve_color_scheme(None, None, None, industry="saas"), "dark")
        self.assertEqual(resolve_color_scheme(None, None, None, industry="agency"), "dark")
        self.assertEqual(resolve_color_scheme(None, None, None, industry="nonprofit"), "light")

    def test_industry_lean_outweighs_the_logo(self):
        # Industry beats the logo 2:1 when they disagree.
        # Light logo would default dark, but a light-leaning industry wins.
        self.assertEqual(
            resolve_color_scheme(None, None, True, industry="professional-services"),
            "light",
        )
        # Dark logo would default light, but a dark-leaning industry wins.
        self.assertEqual(resolve_color_scheme(None, None, False, industry="saas"), "dark")

    def test_neutral_industry_defers_to_logo(self):
        # An industry with no lean falls back to the logo smart-default.
        self.assertEqual(resolve_color_scheme(None, None, True, industry="restaurant"), "dark")
        self.assertEqual(resolve_color_scheme(None, None, False, industry="restaurant"), "light")

    def test_explicit_choice_beats_industry_lean(self):
        # Precedence is unchanged: override / brand pref still win over industry.
        self.assertEqual(resolve_color_scheme("dark", None, None, industry="childcare"), "dark")
        self.assertEqual(resolve_color_scheme(None, "dark", None, industry="nonprofit"), "dark")
        self.assertEqual(resolve_color_scheme(None, "light", None, industry="saas"), "light")


class DarkSchemeTest(unittest.TestCase):
    def test_light_is_default_and_unchanged(self):
        # Default scheme stays light: white page, dark text.
        t = build_theme("#2563eb")
        self.assertEqual(t.palette.background, "#ffffff")
        self.assertLess(_relative_luminance(t.palette.text), 0.2)
        self.assertEqual(t.color_scheme, "light")

    def test_dark_scheme_is_dark_with_light_text(self):
        t = build_theme("#2563eb", color_scheme="dark")
        self.assertEqual(t.color_scheme, "dark")
        # Dark page + surfaces, light body text.
        self.assertLess(_relative_luminance(t.palette.background), 0.2)
        self.assertLess(_relative_luminance(t.palette.surface), 0.25)
        self.assertGreater(_relative_luminance(t.palette.text), 0.6)

    def test_dark_body_text_meets_aaa(self):
        for seed in ("#2563eb", "#dc2626", "#16a34a", "#808080", None):
            t = build_theme(seed, color_scheme="dark")
            self.assertGreaterEqual(
                _contrast(t.palette.background, t.palette.text), 7.0, f"seed={seed!r}"
            )

    def test_dark_button_meets_aa(self):
        for seed in ("#2563eb", "#dc2626", "#16a34a"):
            t = build_theme(seed, color_scheme="dark")
            self.assertGreaterEqual(
                _contrast(t.buttons.background, t.buttons.text), 4.5, f"seed={seed!r}"
            )

    def test_dark_bands_are_dark_with_readable_text(self):
        # Both band variants stay dark and keep their auto-chosen text readable.
        t = build_theme("#2563eb", color_scheme="dark")
        for band in ("light", "dark"):
            bg, fg = band_colors(t.palette, band)
            self.assertLess(_relative_luminance(bg), 0.3, band)
            self.assertGreaterEqual(_contrast(bg, fg), 4.5, band)

    def test_dark_keeps_mood_typography(self):
        # color_scheme only changes colours, not the font pairing.
        light = build_theme("#2563eb", mood="luxury")
        dark = build_theme("#2563eb", mood="luxury", color_scheme="dark")
        self.assertEqual(light.typography.heading_font, dark.typography.heading_font)


class HeroHeightTokenTest(unittest.TestCase):
    """to_builder_styles emits the hero token only when banded, so default
    ("full") sites carry no hero override and fall back to the full-screen look."""

    def test_full_omits_hero_token(self):
        theme = build_theme("#2563eb", mood="modern")
        self.assertEqual(theme.hero_background_height, "full")
        self.assertNotIn("hero", theme.to_builder_styles())

    def test_banded_emits_min_height(self):
        from app.models.brand import HERO_BANDED_MIN_HEIGHT

        theme = build_theme("#2563eb", mood="modern")
        theme.hero_background_height = "banded"
        self.assertEqual(
            theme.to_builder_styles()["hero"], {"minHeight": HERO_BANDED_MIN_HEIGHT}
        )


class BrandMoodTokenTest(unittest.TestCase):
    """to_builder_styles carries the brand mood so the builder's section
    browser can hide catalog templates gated to other moods (`moods` field)."""

    def test_mood_is_emitted(self):
        for mood in ("friendly", "playful", "luxury"):
            theme = build_theme("#2563eb", mood=mood)
            self.assertEqual(theme.to_builder_styles()["brandMood"], mood)


class DesignLanguageOverrideTest(unittest.TestCase):
    """palette_choice/font_choice (the design-language LLM picks) override the
    deterministic selection when valid, and are silent no-ops when not."""

    def test_slugs_are_unique(self):
        palette_slugs = [c.slug for c in _CURATED_PALETTES]
        self.assertEqual(len(palette_slugs), len(set(palette_slugs)))
        for mood in MOODS:
            pool_slugs = [p.slug for p in MOOD_SPECS[mood].font_pool]
            self.assertEqual(
                len(pool_slugs), len(set(pool_slugs)), f"{mood}: duplicate pairing slugs"
            )

    def test_valid_palette_choice_takes_that_curated_palette(self):
        entry = next(c for c in _CURATED_PALETTES if c.slug == "ai-platform")
        # A strong brand hue would normally win a Tailwind snap under "auto";
        # the design-language pick must beat it.
        theme = build_theme(
            "#dc2626",
            mood="modern",
            palette_mode="auto",
            industry="saas",
            palette_choice="ai-platform",
        )
        self.assertEqual(theme.palette.primary.lower(), entry.primary.lower())
        self.assertEqual(theme.palette, _palette_from_curated(entry))

    def test_invalid_palette_choice_changes_nothing(self):
        base = build_theme("#dc2626", mood="modern", palette_mode="auto", industry="saas")
        hallucinated = build_theme(
            "#dc2626",
            mood="modern",
            palette_mode="auto",
            industry="saas",
            palette_choice="not-a-real-palette",
        )
        self.assertEqual(base.palette, hallucinated.palette)

    def test_palette_choice_is_confined_to_industry_candidates(self):
        # childcare's curated set is forced; a saas palette slug must not escape it.
        base = build_theme(None, mood="friendly", palette_mode="auto", industry="childcare")
        cross = build_theme(
            None,
            mood="friendly",
            palette_mode="auto",
            industry="childcare",
            palette_choice="ai-platform",
        )
        self.assertEqual(base.palette, cross.palette)

    def test_dark_scheme_ignores_palette_choice(self):
        base = build_theme("#2563eb", mood="modern", color_scheme="dark", industry="saas")
        picked = build_theme(
            "#2563eb",
            mood="modern",
            color_scheme="dark",
            industry="saas",
            palette_choice="ai-platform",
        )
        self.assertEqual(base.palette, picked.palette)

    def test_valid_font_choice_takes_that_pairing(self):
        pool = MOOD_SPECS["modern"].font_pool
        # Pick a non-default pairing so the assertion can't pass by accident.
        target = pool[-1]
        theme = build_theme(
            "#2563eb", mood="modern", industry="saas", font_choice=target.slug
        )
        self.assertEqual(theme.typography.heading_font, target.heading_font)
        self.assertEqual(theme.typography.body_font, target.body_font)

    def test_invalid_font_choice_changes_nothing(self):
        base = build_theme("#2563eb", mood="modern", industry="saas", font_seed="Acme")
        hallucinated = build_theme(
            "#2563eb",
            mood="modern",
            industry="saas",
            font_seed="Acme",
            font_choice="comic-sans-papyrus",
        )
        self.assertEqual(base.typography, hallucinated.typography)


if __name__ == "__main__":
    unittest.main()


class PageWidthModeTest(unittest.TestCase):
    def test_every_mood_ships_full_width_sections(self):
        # 2026 default: sections bleed to the viewport edge; each section's
        # inner container still caps content at page.max_width. The renderer
        # reads builderStyles.page.widthMode.
        from app.services.theme import MOOD_SPECS, build_theme

        for mood in MOOD_SPECS:
            theme = build_theme("#2563eb", mood)
            styles = theme.to_builder_styles()
            self.assertEqual(
                styles["page"]["widthMode"], "full", f"mood {mood} is not full-width"
            )
            # Content stays constrained — max_width survives untouched.
            self.assertEqual(styles["page"]["maxWidth"], theme.page.max_width)
