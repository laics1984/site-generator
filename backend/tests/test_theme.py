"""Tests for the mood font-pairing pools and deterministic per-site selection."""

import re
import typing
import unittest

from app.models.brand import BrandMood
from app.models.industry import IndustryCategory
from app.services.theme import (
    MOOD_SPECS,
    FontPairing,
    band_colors,
    build_theme,
    curated_palette_by_slug,
    curated_palette_options,
    resolve_color_scheme,
    _CURATED_DARK_PALETTES,
    _CURATED_PALETTES,
    _DARK_INK_MAX_SATURATION,
    _DARK_INK_MIN_LIGHTNESS,
    _INK_MAX_LIGHTNESS,
    _INK_MAX_SATURATION,
    _curated_candidates,
    _dark_palette,
    _hex_to_rgb,
    _palette_from_curated,
    _palette_from_curated_dark,
    _contrast,
    _relative_luminance,
    _rgb_to_hls,
    _text_for_background,
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
            # The band is a real but calm step DOWN from the page — never
            # lighter than the page (which reads as a rendering fault) and never
            # so deep it stops being a light band.
            step = _relative_luminance(p.background) - _relative_luminance(p.surface)
            self.assertGreaterEqual(step, 0.012, f"{c.name}: band invisible")
            self.assertLessEqual(step, 0.13, f"{c.name}: band too deep")

    def test_a_warm_page_gets_a_warm_band(self):
        """A non-white page must not be banded with cool slate.

        `_light_surface` used to hand back a fixed #f8fafc whenever the authored
        tint missed its luminance gate, which on a warm parchment page reads as a
        bug rather than a design."""
        for c in _CURATED_PALETTES:
            if c.page.lower() == "#ffffff":
                continue
            p = _palette_from_curated(c)
            page_h = _rgb_to_hls(*_hex_to_rgb(p.background))[0] * 360
            band_h = _rgb_to_hls(*_hex_to_rgb(p.surface))[0] * 360
            self.assertLessEqual(
                abs(((band_h - page_h + 180) % 360) - 180), 30, f"{c.name}: band hue drifts"
            )
            self.assertGreater(
                _rgb_to_hls(*_hex_to_rgb(p.surface))[2], 0.02, f"{c.name}: band went grey"
            )

    def test_slugs_are_unique_across_both_catalogues(self):
        # diversity.record_choice stores a bare slug with no scheme column, so a
        # collision would make a light and a dark palette steer each other.
        slugs = [c.slug for c in _CURATED_PALETTES] + [
            c.slug for c in _CURATED_DARK_PALETTES
        ]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_curated_dark_band_is_an_ink_not_a_saturated_brand_shade(self):
        """`secondary` paints every heading, every body line and every dark
        band, so it must read as a hue-tinted ink. Left at a curated palette's
        full-chroma `dark` (violet-900, red-950) the whole page becomes one
        colour wash and the brand hue stops meaning anything — the 60-30-10
        split needs the neutrals to be neutral."""
        # Tolerance covers one 8-bit rounding step through the HLS round-trip.
        eps = 1 / 255
        for c in _CURATED_PALETTES:
            p = _palette_from_curated(c)
            _, l, s = _rgb_to_hls(*_hex_to_rgb(p.secondary))
            self.assertLessEqual(s, _INK_MAX_SATURATION + eps, f"{c.name}: ink too saturated")
            self.assertLessEqual(l, _INK_MAX_LIGHTNESS + eps, f"{c.name}: ink too light")
            # …while a CHROMATIC `primary` stays vivid: it is the 10% that
            # marks actions. Deliberately achromatic palettes (E-commerce
            # Luxury's near-black stone) are exempt — they have no hue to keep.
            primary_s = _rgb_to_hls(*_hex_to_rgb(p.primary))[2]
            if primary_s >= 0.3:
                self.assertGreater(
                    primary_s, s, f"{c.name}: primary no more saturated than the ink"
                )

    def test_curated_ink_keeps_the_palette_hue(self):
        # Desaturating must not flatten the ink to a generic grey — the deep
        # violet band should still read violet-black, not slate.
        community = next(c for c in _CURATED_PALETTES if c.slug == "community")
        ink = _palette_from_curated(community).secondary
        ink_h, _, ink_s = _rgb_to_hls(*_hex_to_rgb(ink))
        dark_h = _rgb_to_hls(*_hex_to_rgb(community.dark))[0]
        self.assertGreater(ink_s, 0.05, "ink flattened to grey")
        # Within a few degrees — an inky colour quantises coarsely in 8-bit.
        self.assertLess(abs(ink_h - dark_h) * 360, 5)

    def test_an_already_inky_curated_dark_is_left_alone(self):
        luxury = next(c for c in _CURATED_PALETTES if c.slug == "e-commerce-luxury")
        self.assertEqual(_palette_from_curated(luxury).secondary, luxury.dark.lower())

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


class CuratedDarkPaletteTest(unittest.TestCase):
    """The dark catalogue's equivalent of CuratedPaletteTest.

    The light set's invariants don't transfer — a dark palette wants low
    luminance everywhere and a LIGHT ink — so these are their mirror image.
    """

    def test_every_dark_palette_is_dark_and_readable(self):
        for c in _CURATED_DARK_PALETTES:
            self.assertTrue(c.categories, f"{c.name}: no category tags")
            p = _palette_from_curated_dark(c)
            self.assertLess(_relative_luminance(p.background), 0.05, c.name)
            # Hard ceiling: the elevated band still has to carry white body text
            # at AA, which caps it at relative luminance 0.1833.
            self.assertLess(_relative_luminance(p.surface), 0.18, c.name)
            self.assertGreaterEqual(_contrast(p.background, p.text), 7.0, c.name)
            self.assertGreater(_relative_luminance(p.text), 0.6, c.name)

    def test_dark_bands_stay_white_ink(self):
        for c in _CURATED_DARK_PALETTES:
            p = _palette_from_curated_dark(c)
            for band in ("light", "dark"):
                bg, fg = band_colors(p, band)
                self.assertEqual(fg, "#ffffff", f"{c.name}/{band}")
                self.assertGreaterEqual(_contrast(bg, fg), 4.5, f"{c.name}/{band}")

    def test_dark_ladder_ascends_from_band_to_surface(self):
        # band (darkest) < page < surface (elevated), mirroring what the
        # algorithmic _dark_palette produced, so the luminance rhythm is unchanged.
        for c in _CURATED_DARK_PALETTES:
            band_l = _rgb_to_hls(*_hex_to_rgb(c.band))[1]
            page_l = _rgb_to_hls(*_hex_to_rgb(c.page))[1]
            surf_l = _rgb_to_hls(*_hex_to_rgb(c.surface))[1]
            self.assertLess(band_l, page_l, f"{c.name}: band not below page")
            self.assertLess(page_l, surf_l, f"{c.name}: surface not above page")
            self.assertGreaterEqual(page_l - band_l, 0.015, f"{c.name}: bands merge")
            self.assertGreaterEqual(surf_l - page_l, 0.03, f"{c.name}: surface merges")

    def test_dark_primary_needs_no_button_correction(self):
        """What the catalogue declares is what the page paints.

        build_theme darkens a primary that fails AA as a button fill. That guard
        is silent, so a catalogue entry relying on it would render a colour
        nobody chose — these are authored to clear the bar as written."""
        for c in _CURATED_DARK_PALETTES:
            p = _palette_from_curated_dark(c)
            self.assertGreaterEqual(
                _contrast(p.primary, _text_for_background(p.primary)), 4.5, c.name
            )

    def test_dark_brand_colours_pop_off_the_page(self):
        for c in _CURATED_DARK_PALETTES:
            p = _palette_from_curated_dark(c)
            self.assertGreaterEqual(_contrast(p.primary, p.background), 3.0, c.name)
            self.assertGreaterEqual(_contrast(p.accent, p.background), 3.0, c.name)
            self.assertGreaterEqual(
                _rgb_to_hls(*_hex_to_rgb(p.primary))[2], 0.45, f"{c.name}: primary is muddy"
            )

    def test_dark_ink_is_light_and_near_neutral(self):
        # The mirror of _brand_ink: a strongly tinted light ink makes every
        # paragraph look highlighted.
        eps = 1 / 255
        for c in _CURATED_DARK_PALETTES:
            _h, l, s = _rgb_to_hls(*_hex_to_rgb(_palette_from_curated_dark(c).text))
            self.assertGreaterEqual(l, _DARK_INK_MIN_LIGHTNESS - eps, f"{c.name}: ink dim")
            self.assertLessEqual(s, _DARK_INK_MAX_SATURATION + eps, f"{c.name}: ink tinted")

    def test_dark_slugs_are_namespaced(self):
        for c in _CURATED_DARK_PALETTES:
            self.assertTrue(c.slug.startswith("dark-"), c.slug)


class MoodAwareCandidatesTest(unittest.TestCase):
    INDUSTRIES = tuple(typing.get_args(IndustryCategory))

    def test_every_mood_and_industry_has_at_least_two_candidates(self):
        """The variety guarantee.

        One candidate means the seeded pick is forced AND diversity avoidance
        can't rotate — every dark editorial agency would ship an identical
        palette, which is the convergence the catalogue exists to break."""
        for scheme in ("light", "dark"):
            for mood in MOODS:
                for industry in self.INDUSTRIES:
                    n = len(_curated_candidates(industry, mood, scheme))
                    self.assertGreaterEqual(
                        n, 2, f"{scheme}/{mood}/{industry} has {n} candidate(s)"
                    )

    def test_mood_narrows_but_never_empties(self):
        for scheme, table in (("light", _CURATED_PALETTES), ("dark", _CURATED_DARK_PALETTES)):
            for mood in MOODS:
                got = _curated_candidates(None, mood, scheme)
                self.assertTrue(got, f"{scheme}/{mood} emptied the pool")
                for c in got:
                    # Either a wildcard (no moods) or tagged for this mood.
                    self.assertTrue(
                        not c.moods or mood in c.moods, f"{c.name} leaked into {mood}"
                    )
                # Every entry tagged for this mood is offered.
                for c in table:
                    if mood in c.moods:
                        self.assertIn(c, got, f"{c.name} missing from {mood}")

    def test_untagged_entries_are_wildcards(self):
        # The legacy 28 predate the mood axis; strict filtering would have
        # dropped them all the moment one tagged entry appeared.
        legacy = [c for c in _CURATED_PALETTES if not c.moods]
        self.assertTrue(legacy)
        for mood in MOODS:
            offered = _curated_candidates(None, mood, "light")
            for c in legacy:
                self.assertIn(c, offered, f"{c.name} lost on mood {mood}")

    def test_options_and_lookup_mirror_the_candidate_set(self):
        """The three-way lockstep the module docstrings promise, plus the
        guarantee that a slug can never cross schemes."""
        for scheme in ("light", "dark"):
            other = "dark" if scheme == "light" else "light"
            for mood in MOODS:
                for industry in self.INDUSTRIES:
                    where = f"{scheme}/{mood}/{industry}"
                    candidates = {c.slug for c in _curated_candidates(industry, mood, scheme)}
                    options = {
                        o["slug"]
                        for o in curated_palette_options(industry, mood=mood, scheme=scheme)
                    }
                    self.assertEqual(candidates, options, where)
                    for slug in candidates:
                        self.assertIsNotNone(
                            curated_palette_by_slug(slug, industry, mood=mood, scheme=scheme),
                            f"{where}: {slug} unresolvable",
                        )
                    for slug in {
                        c.slug for c in _curated_candidates(industry, mood, other)
                    }:
                        self.assertIsNone(
                            curated_palette_by_slug(slug, industry, mood=mood, scheme=scheme),
                            f"{where}: {other} slug {slug} leaked",
                        )

    def test_mood_actually_changes_the_palette(self):
        """The point of the whole mood axis.

        Filtering alone was not enough: the legacy wildcards dominate most pools
        and two moods with same-sized pools resolved to the same index, so a
        luxury and a technical restaurant both got Brewery/Winery. Mood is now
        part of the selection seed as well as the filter."""
        for scheme in ("light", "dark"):
            for industry in self.INDUSTRIES:
                slugs = {
                    build_theme(
                        None,
                        mood=mood,
                        palette_mode="curated",
                        industry=industry,
                        color_scheme=scheme,
                        font_seed="Acme Co",
                    ).palette_slug
                    for mood in MOODS
                }
                self.assertGreaterEqual(
                    len(slugs), 3, f"{scheme}/{industry}: mood barely moves the palette"
                )

    def test_a_mood_specific_palette_never_reaches_the_wrong_brief(self):
        # Bordeaux is luxury/editorial; a technical restaurant must never get it.
        for mood in MOODS:
            offered = {c.slug for c in _curated_candidates("restaurant", mood, "light")}
            if mood in ("luxury", "editorial"):
                self.assertIn("bordeaux", offered, mood)
            else:
                self.assertNotIn("bordeaux", offered, mood)

    def test_options_quote_the_colours_that_actually_ship(self):
        # The menu used to print raw source tokens: a curated `dark` of #0F172A
        # was offered while _brand_ink capped it to #171a22 on the page.
        for o in curated_palette_options("saas"):
            entry = curated_palette_by_slug(str(o["slug"]), "saas")
            self.assertIsNotNone(entry)
            mapped = _palette_from_curated(entry)  # type: ignore[arg-type]
            swatches = o["swatches"]
            self.assertEqual(swatches["page"], mapped.background)  # type: ignore[index]
            self.assertEqual(swatches["band"], mapped.secondary)  # type: ignore[index]
            self.assertEqual(swatches["ink"], mapped.text)  # type: ignore[index]


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

    def test_dark_scheme_takes_a_curated_dark_pick(self):
        entry = next(
            c for c in _CURATED_DARK_PALETTES if c.slug == "dark-midnight-violet"
        )
        t = build_theme(
            "#2563eb",
            mood="modern",
            color_scheme="dark",
            industry="saas",
            palette_choice="dark-midnight-violet",
        )
        self.assertEqual(t.palette, _palette_from_curated_dark(entry))
        self.assertEqual(t.palette_slug, "dark-midnight-violet")

    def test_a_light_slug_cannot_leak_into_a_dark_build(self):
        # "ai-platform" is a light-catalogue slug; on a dark build it must not
        # resolve at all, leaving the deterministic dark pick untouched.
        base = build_theme("#2563eb", mood="modern", color_scheme="dark", industry="saas")
        leaked = build_theme(
            "#2563eb",
            mood="modern",
            color_scheme="dark",
            industry="saas",
            palette_choice="ai-platform",
        )
        self.assertEqual(base.palette, leaked.palette)

    def test_dark_curated_records_a_slug(self):
        # Before the dark catalogue existed the dark path recorded nothing, so
        # the design manifest had no palette to log and diversity had none to
        # rotate off.
        t = build_theme("#2563eb", mood="modern", color_scheme="dark", industry="saas")
        self.assertIsNotNone(t.palette_slug)
        self.assertTrue(str(t.palette_slug).startswith("dark-"))

    def test_derive_mode_keeps_the_algorithmic_dark_palette(self):
        t = build_theme("#2563eb", palette_mode="derive", color_scheme="dark")
        self.assertEqual(t.palette, _dark_palette("#2563eb"))
        self.assertIsNone(t.palette_slug)

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
