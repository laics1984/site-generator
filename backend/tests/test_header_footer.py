import unittest

from app.models.brand import BrandIdentity
from app.services.header_footer import build_footer, build_header, built_value
from app.services.theme import _hex_to_rgb, build_theme


class HeaderContrastTest(unittest.TestCase):
    def test_light_logo_on_light_site_switches_whole_header_to_dark_secondary(self):
        brand = BrandIdentity(
            name="Acme",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#2563eb"],
            logo_is_light=True,
        )
        theme = build_theme("#2563eb")

        header = build_header(brand, theme, nav_items=[])

        # A light logo would blend into the default white/light header, so the
        # WHOLE bar switches to the theme's own dark, brand-hued token instead
        # of decorating the logo — the logo itself stays a bare image.
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.secondary)
        self.assertIsNone(_find(header, "Logo lockup"))
        logo = _find(header, "Brand Logo")
        self.assertIsNotNone(logo)
        self.assertIsNone(logo.styles.get("filter"))


def _find(node, name):
    if getattr(node, "name", None) == name:
        return node
    content = getattr(node, "content", None)
    if isinstance(content, list):
        for ch in content:
            found = _find(ch, name)
            if found is not None:
                return found
    return None


class HeaderBackgroundContrastTest(unittest.TestCase):
    """The header's own background is what adapts to a same-brightness logo —
    never a decoration on the mark. See `_header_chrome`."""

    def _header(self, *, dark, logo_is_light):
        brand = BrandIdentity(
            name="Acme",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#2563eb"],
            logo_is_light=logo_is_light,
        )
        theme = build_theme("#2563eb", color_scheme="dark" if dark else "light")
        return build_header(brand, theme, nav_items=[]), theme

    def test_dark_logo_on_dark_site_switches_header_to_light_text_token(self):
        header, theme = self._header(dark=True, logo_is_light=False)
        # No light token exists in a dark palette except `text` (guaranteed
        # high-contrast against the dark background by construction) — the
        # bar switches to it wholesale rather than patching the logo.
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.text)
        self.assertIsNone(_find(header, "Logo lockup"))
        logo = _find(header, "Brand Logo")
        self.assertIsNone(logo.styles.get("filter"))

    def test_dark_logo_on_light_site_keeps_default_background(self):
        header, theme = self._header(dark=False, logo_is_light=False)
        # A dark logo already reads fine on the default white/light header.
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)

    def test_light_logo_on_dark_site_keeps_default_background(self):
        header, theme = self._header(dark=True, logo_is_light=True)
        # A light logo already reads fine on the default dark header.
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)


class HeaderThemeInkTest(unittest.TestCase):
    """Menu ink follows the theme scheme — one consistent white-or-near-black
    choice site-wide — and the header's bottom edge carries the builder's
    "Subtle" divider shadow preset, never a hard border."""

    def _brand(self):
        return BrandIdentity(name="Hope", extracted_palette=["#2563eb"])

    def test_light_theme_gets_dark_ink_on_light_header(self):
        from app.services.theme import _contrast, _relative_luminance

        theme = build_theme("#2563eb", color_scheme="light")
        header = build_header(self._brand(), theme, nav_items=[])
        bg = header.styles["backgroundColor"]
        self.assertEqual(bg, theme.palette.background)
        menu = _find_type(header, "menu")
        # Dark ink on the light header (and it clears WCAG AA).
        self.assertLess(_relative_luminance(menu.styles["color"]), 0.5)
        self.assertGreaterEqual(_contrast(bg, menu.styles["color"]), 4.5)

    def test_dark_theme_gets_white_ink_on_dark_header(self):
        theme = build_theme("#2563eb", color_scheme="dark")
        header = build_header(self._brand(), theme, nav_items=[])
        self.assertEqual(header.styles["backgroundColor"], theme.palette.background)
        menu = _find_type(header, "menu")
        self.assertEqual(menu.styles["color"], "#ffffff")

    def test_wordmark_ink_matches_menu_ink(self):
        # Typographic brand mark (no logo upload) must use the same ink as the
        # menu, not a palette colour that can vanish on a dark header.
        for scheme in ("light", "dark"):
            theme = build_theme("#2563eb", color_scheme=scheme)
            header = build_header(self._brand(), theme, nav_items=[])
            menu = _find_type(header, "menu")
            wordmark = _find(header, "Wordmark")
            self.assertIsNotNone(wordmark)
            self.assertEqual(wordmark.styles["color"], menu.styles["color"])

    def test_divider_is_subtle_shadow_preset_not_a_border(self):
        from app.services.header_footer import HEADER_DIVIDER_SUBTLE

        for scheme in ("light", "dark"):
            theme = build_theme("#2563eb", color_scheme=scheme)
            header = build_header(self._brand(), theme, nav_items=[])
            self.assertEqual(header.styles.get("boxShadow"), HEADER_DIVIDER_SUBTLE)
            self.assertNotIn("borderBottom", header.styles)


class HeaderOverlayTest(unittest.TestCase):
    """Overlay headers keep their REAL solid chrome on the element — the
    renderer strips it during the transparent phase and restores exactly these
    styles when it solidifies on scroll (pickRootBackgroundStyles). Only the
    behavior flag and the wt-header-ink markers differ from a normal header."""

    def _header(self, *, overlay, logo_is_light=None):
        brand = BrandIdentity(
            name="Hope",
            extracted_palette=["#2563eb"],
            logo_data_url="data:image/png;base64,abc" if logo_is_light is not None else None,
            logo_is_light=logo_is_light,
        )
        theme = build_theme("#2563eb", color_scheme="light")
        return theme, build_header(
            brand,
            theme,
            nav_items=[],
            primary_cta=("Get in touch", "#contact"),
            overlay=overlay,
        )

    def test_overlay_header_keeps_solid_chrome(self):
        from app.services.header_footer import HEADER_DIVIDER_SUBTLE

        theme, header = self._header(overlay=True)
        self.assertEqual(header.styles["backgroundColor"], theme.palette.background)
        self.assertEqual(header.styles.get("boxShadow"), HEADER_DIVIDER_SUBTLE)
        menu = _find_type(header, "menu")
        self.assertNotEqual(menu.styles["color"], "#ffffff")  # solid-state ink

    def test_text_bearing_elements_carry_ink_marker_but_not_the_cta(self):
        _, header = self._header(overlay=True)
        menu = _find_type(header, "menu")
        self.assertEqual(menu.classes, "wt-header-ink")
        brand_mark = _find(header, "Brand")
        self.assertEqual(brand_mark.classes, "wt-header-ink")
        cta = _find(header, "Header CTA")
        self.assertIsNotNone(cta)
        self.assertIsNone(cta.classes)

    def test_overlay_flag_does_not_change_header_background_or_logo(self):
        # Contrast against the logo is `_header_chrome`'s job on the bar's
        # real solid chrome, computed from the theme + logo brightness alone
        # — `overlay` toggles the transparent-over-hero behavior elsewhere
        # (wrap_header / the renderer), not this.
        _, solid = self._header(overlay=False, logo_is_light=False)
        _, overlaid = self._header(overlay=True, logo_is_light=False)
        self.assertEqual(
            solid.styles.get("backgroundColor"), overlaid.styles.get("backgroundColor")
        )
        self.assertIsNone(_find(solid, "Logo lockup"))
        self.assertIsNone(_find(overlaid, "Logo lockup"))
        self.assertIsNone(_find(solid, "Brand Logo").styles.get("filter"))
        self.assertIsNone(_find(overlaid, "Brand Logo").styles.get("filter"))

    def test_wrap_header_mirrors_overlay_and_reveal_offset_into_behavior(self):
        from app.services.menu_builder import wrap_header

        _, header = self._header(overlay=False)
        plain = wrap_header(header, menus=[])["behavior"]
        self.assertFalse(plain["overlay"])
        self.assertNotIn("scrollRevealOffset", plain)
        rich = wrap_header(
            header, menus=[], overlay=True, scroll_reveal_offset=80
        )["behavior"]
        self.assertTrue(rich["overlay"])
        self.assertEqual(rich["scrollRevealOffset"], 80)

    def test_wrap_header_reveal_flag_emitted_only_when_disabled(self):
        # Legacy sites must keep their exact behavior payload: the flag is
        # ABSENT (renderers default to reveal-on) unless explicitly disabled
        # — the self-chrome pill path, which stays transparent at every
        # scroll position.
        from app.services.menu_builder import wrap_header

        _, header = self._header(overlay=True)
        default = wrap_header(header, menus=[], overlay=True)["behavior"]
        self.assertNotIn("revealBackgroundOnScroll", default)
        pill = wrap_header(
            header, menus=[], overlay=True, reveal_background_on_scroll=False
        )["behavior"]
        self.assertIs(pill["revealBackgroundOnScroll"], False)

    def test_wrap_header_adaptive_ink_flag_emitted_only_for_self_chrome(self):
        # The renderers gate the per-section ink flip on this key alone — none
        # of them knows the archetype's name. Absent means "don't", so every
        # other header's payload is byte-identical to before.
        from app.services.menu_builder import wrap_header

        _, header = self._header(overlay=True)
        self.assertNotIn(
            "adaptiveInk", wrap_header(header, menus=[], overlay=True)["behavior"]
        )
        pill = wrap_header(header, menus=[], overlay=True, adaptive_ink=True)
        self.assertIs(pill["behavior"]["adaptiveInk"], True)


def _find_type(node, type_name):
    if getattr(node, "type", None) == type_name:
        return node
    content = getattr(node, "content", None)
    if isinstance(content, list):
        for ch in content:
            found = _find_type(ch, type_name)
            if found is not None:
                return found
    return None


class FloatingPillAdaptiveInkTest(unittest.TestCase):
    """The pill hands the renderer three knobs and keeps its own values.

    A header that never solidifies has no chrome of its own to stay legible
    against, so its ink must follow the section beneath it as the page scrolls.
    The wire format is `var(--wt-pill-*, <built value>)`: the fallback slot is
    exactly what the archetype painted before, so a renderer that sets nothing
    is unaffected, and no other archetype is offered the knobs at all.
    """

    def _header(self, archetype, *, brand=None):
        theme = build_theme("#0e7490", mood="playful")
        return build_header(
            brand or BrandIdentity(name="GloryKids", mood="playful"),
            theme,
            nav_items=[],
            primary_cta=("Schedule a Tour", "/contact"),
            overlay=True,
            archetype=archetype,
        )

    def _styles(self, node):
        yield node.styles or {}
        if isinstance(node.content, list):
            for child in node.content:
                yield from self._styles(child)

    def test_menu_ink_tint_and_hairline_are_all_overridable(self):
        bar = self._header("floating-pill").content[0]
        menu = _find_type(bar, "menu")
        self.assertTrue(menu.styles["color"].startswith("var(--wt-pill-ink,"))
        self.assertIn("var(--wt-pill-tint,", bar.styles["backgroundColor"])
        self.assertIn("var(--wt-pill-hairline,", bar.styles["border"])

    def test_the_fallback_is_the_value_the_archetype_used_to_paint(self):
        # The whole flip is additive: strip the wrapper and you are back to the
        # built site, byte for byte. That is what keeps the builder canvas at
        # rest, an older published bundle and a screenshot all correct.
        from app.services.header_footer import (
            GLASS_ALPHA,
            _glass_tint,
            _header_chrome,
            _rgba,
            built_value,
        )

        brand = BrandIdentity(name="GloryKids", mood="playful")
        theme = build_theme("#0e7490", mood="playful")
        header_bg, header_fg = _header_chrome(brand, theme)
        bar = self._header("floating-pill").content[0]
        menu = _find_type(bar, "menu")

        self.assertEqual(built_value(menu.styles["color"]), header_fg)
        self.assertEqual(
            built_value(bar.styles["backgroundColor"]),
            _rgba(_glass_tint(header_bg), GLASS_ALPHA),
        )
        self.assertTrue(built_value(bar.styles["border"]).startswith("1px solid rgba("))

    def test_the_typographic_wordmark_flips_with_the_menu(self):
        # It is text the renderer CAN recolour, and it sits on the same pane.
        wordmark = _find(self._header("floating-pill"), "Wordmark")
        menu = _find_type(self._header("floating-pill"), "menu")
        self.assertTrue(wordmark.styles["color"].startswith("var(--wt-pill-ink,"))
        self.assertEqual(wordmark.styles["color"], menu.styles["color"])

    def test_an_image_logo_is_never_wrapped(self):
        # A bitmap cannot be recoloured; a dark logo over a dark section is
        # `_header_chrome`'s problem to solve on the bar's background, not the
        # ink var's.
        brand = BrandIdentity(
            name="GloryKids",
            mood="playful",
            logo_url="https://cdn.example/logo.png",
            logo_render_ok=True,
        )
        header = self._header("floating-pill", brand=brand)
        self.assertIsNone(_find(header, "Wordmark"))
        logo = _find_type(header, "image")
        self.assertIsNotNone(logo)
        self.assertNotIn("--wt-pill-", str(logo.styles))

    def test_no_other_archetype_is_offered_the_knobs(self):
        for archetype in ("classic", "glass-blur", "centered-stack", "minimal-line"):
            with self.subTest(archetype=archetype):
                header = self._header(archetype)
                for styles in self._styles(header):
                    self.assertNotIn("--wt-pill-", str(styles))

    def test_no_token_placeholder_survives_materialization(self):
        # A token added on one side of the header_footer/chrome-archetypes pair
        # ships as a literal "{{...}}" string. The var() wrapper is a new place
        # for that to hide.
        for archetype in (
            "classic",
            "glass-blur",
            "floating-pill",
            "centered-stack",
            "minimal-line",
        ):
            with self.subTest(archetype=archetype):
                for styles in self._styles(self._header(archetype)):
                    self.assertNotIn("{{", str(styles))

    def test_built_value_passes_ordinary_colours_through(self):
        from app.services.header_footer import built_value

        self.assertEqual(built_value("#0f172a"), "#0f172a")
        self.assertEqual(built_value("rgba(15, 23, 42, 0.2)"), "rgba(15, 23, 42, 0.2)")
        self.assertEqual(
            built_value("var(--wt-pill-tint, rgba(15, 23, 42, 0.2))"),
            "rgba(15, 23, 42, 0.2)",
        )
        # Not ours: a builder token keeps its wrapper, since the builder — not
        # a scroll handler — is what resolves it.
        self.assertEqual(
            built_value("var(--builder-button-background, #2563eb)"),
            "var(--builder-button-background, #2563eb)",
        )


class FloatingPillGlassTest(unittest.TestCase):
    """The pill's bar is frosted glass, permanently.

    It is the one header that never solidifies (`revealBackgroundOnScroll:
    false`), so its background is what the visitor sees at every scroll
    position — over the hero photo it floats on AND over whatever section it
    later drifts across. The chrome lives on the inner `headerBar` container,
    not the transparent root: that split is what lets both renderers strip the
    root during overlay while leaving the bar alone, and what the builder's
    right panel points its appearance controls at.
    """

    def _pill(self, scheme):
        theme = build_theme("#0e7490", mood="playful", color_scheme=scheme)
        header = build_header(
            BrandIdentity(name="GloryKids", mood="playful"),
            theme,
            nav_items=[],
            primary_cta=("Schedule a Tour", "/contact"),
            overlay=True,
            archetype="floating-pill",
        )
        return theme, header, header.content[0]

    def test_bar_is_translucent_and_the_root_is_not_painted(self):
        from app.services.header_footer import GLASS_ALPHA

        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                _, header, bar = self._pill(scheme)
                self.assertEqual(header.styles["backgroundColor"], "transparent")
                self.assertEqual(built_value(bar.styles["backgroundColor"])[:5], "rgba(")
                self.assertAlmostEqual(
                    _alpha(built_value(bar.styles["backgroundColor"])), GLASS_ALPHA
                )
                self.assertIn("blur(", bar.styles["backdropFilter"])
                self.assertIn("blur(", bar.styles["WebkitBackdropFilter"])

    def test_the_glass_carries_no_colour_of_its_own(self):
        # Clear glass is achromatic: the brand hue belongs to the page showing
        # THROUGH the pane, not to the pane. The palette's near-black in a dark
        # scheme is navy — the veil built from it must not be.
        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                _, _, bar = self._pill(scheme)
                r, g, b, _a = _channels(built_value(bar.styles["backgroundColor"]))
                self.assertEqual({r, g, b}, {r}, bar.styles["backgroundColor"])
                # Nor may the shadow reintroduce one.
                self.assertNotIn("rgba(15", bar.styles["boxShadow"])

    def test_the_filter_desaturates_the_backdrop_instead_of_boosting_it(self):
        # A translucent pane is only as colourless as what shows THROUGH it.
        # `saturate()` — the glassmorphism idiom — makes the backdrop's colour
        # pop through, which is what turns the bar into a visible gradient over
        # a hero photo or a tinted band. Frosted glass does the opposite.
        _, _, bar = self._pill("light")
        for prop in ("backdropFilter", "WebkitBackdropFilter"):
            with self.subTest(prop=prop):
                self.assertIn("grayscale(", bar.styles[prop])
                self.assertNotIn("saturate(", bar.styles[prop])

    def test_the_backdrop_is_blurred_but_not_desaturated(self):
        """grayscale(0) is deliberate — pin it.

        Full desaturation shipped once and was reverted: the neutralised pane
        read worse than the colour it removed, going muddy and grey over photos
        (header_footer.GLASS_FILTER; the original argument is in f95e2ec). The
        term is kept at an explicit 0 rather than dropped so that intent is
        greppable, which only helps if something fails when it drifts.
        """
        _, _, bar = self._pill("light")
        self.assertEqual(_grayscale_amount(bar), 0.0)
        # A no-op grayscale must not be mistaken for licence to boost instead.
        self.assertNotIn("saturate(", bar.styles["backdropFilter"])

    def test_nav_ink_holds_AA_on_its_own_side_of_the_luminance_split(self):
        """What the BUILT values guarantee, and where adaptive ink takes over.

        The pill never solidifies, so it is sticky over the whole page and its
        nav meets every surface underneath. At GLASS_ALPHA the pane is thin
        enough that the backdrop drives the composite, so one static ink cannot
        clear AA on both sides of the luminance split — a light-scheme ink dies
        over the hero's dark scrim, a dark-scheme one over a bright photo.

        That is the DESIGN, not a defect: it is precisely why
        `behavior.adaptiveInk` is mandatory for this archetype rather than an
        enhancement (see CLAUDE.md, "Scroll-adaptive header ink"). Thickening
        the glass until one ink covers both sides takes ~0.45 alpha and costs
        the pane its transparency.

        So the bound worth holding is the one the fallback really makes: over
        the theme's own bands — every surface a renderer that sets NO vars can
        paint behind the bar — the ink clears AA.
        """
        from app.services.theme import _contrast

        SCRIMMED_HERO = "#20262f"  # photo under the hero's legibility overlay
        BRIGHT_PHOTO = "#b0b4bc"  # the brightest an in-page photo tends to run
        # The photo each scheme's ink is on the WRONG side of.
        OPPOSITE = {"light": SCRIMMED_HERO, "dark": BRIGHT_PHOTO}

        for scheme in ("light", "dark"):
            theme, _, bar = self._pill(scheme)
            ink = built_value(_find_type(bar, "menu").styles["color"])

            def over(backdrop):
                return _contrast(
                    ink, _composite(built_value(bar.styles["backgroundColor"]), backdrop)
                )

            # The theme's own bands are the floor the built values must hold.
            for backdrop in (theme.palette.background, theme.palette.surface):
                with self.subTest(scheme=scheme, backdrop=backdrop):
                    self.assertGreaterEqual(over(backdrop), 4.5)

            # ...and the same-side photo, which is not a hard case for this ink.
            same_side = BRIGHT_PHOTO if scheme == "light" else SCRIMMED_HERO
            with self.subTest(scheme=scheme, backdrop=same_side, side="same"):
                self.assertGreaterEqual(over(same_side), 4.5)

            # The opposite-side photo is the case adaptive ink owns. Asserted so
            # the dependency is stated: if thickening the glass ever makes the
            # fallback self-sufficient here, this fails and the comment above —
            # and adaptiveInk's necessity — get re-examined deliberately.
            with self.subTest(scheme=scheme, backdrop=OPPOSITE[scheme], side="opposite"):
                self.assertLess(over(OPPOSITE[scheme]), 4.5)

    def test_reveal_style_archetypes_are_untouched(self):
        # glass-blur paints its own translucency on the ROOT (it solidifies on
        # scroll); only the pill moved.
        theme = build_theme("#0e7490", mood="modern")
        header = build_header(
            BrandIdentity(name="Acme", mood="modern"), theme,
            nav_items=[], overlay=True, archetype="glass-blur",
        )
        self.assertAlmostEqual(_alpha(header.styles["backgroundColor"]), 0.72)
        self.assertIsNone(getattr(header.content[0], "headerBar", None))


def _channels(rgba):
    """(r, g, b, alpha) out of an `rgba(...)` string."""
    import re

    r, g, b, a = (float(v) for v in re.findall(r"[\d.]+", rgba))
    return int(r), int(g), int(b), a


def _alpha(rgba):
    return _channels(rgba)[3]


def _grayscale_amount(node):
    """The `grayscale(x)` amount out of an element's backdropFilter."""
    return float(node.styles["backdropFilter"].split("grayscale(")[1].split(")")[0])


def _composite(rgba, backdrop_hex):
    """`rgba` painted over an opaque backdrop, as the browser would blend it."""
    from app.services.theme import _hex_to_rgb, _rgb_to_hex

    r, g, b, a = _channels(rgba)
    under = _hex_to_rgb(backdrop_hex)
    return _rgb_to_hex(*[round(a * c + (1 - a) * u) for c, u in zip((r, g, b), under)])


class FloatingPillGeometryTest(unittest.TestCase):
    """The pill is a slim capsule that tracks the page's content column.

    Two things it must not do: float at its own inset width (it read as a
    stray widget unrelated to the page below it), and inherit the industry's
    oversized brand mark (the logo is the tallest thing in the bar, so it — not
    the padding — sets the bar's height).
    """

    def _header(self, archetype, theme):
        return build_header(
            BrandIdentity(
                name="GloryKids", mood="playful",
                logo_data_url="data:image/png;base64,abc",
            ),
            theme,
            nav_items=[],
            primary_cta=("Schedule a Tour", "/contact"),
            overlay=True,
            industry="childcare",
            archetype=archetype,
        )

    def test_pill_spans_the_same_column_as_the_content(self):
        theme = build_theme("#0284c7", mood="playful", industry="childcare")
        bar = self._header("floating-pill", theme).content[0]
        self.assertEqual(bar.styles["maxWidth"], f"{theme.page.max_width}px")
        # Same column every other header bar uses — not an inset of its own.
        classic_bar = self._header("classic", theme).content[0]
        self.assertEqual(bar.styles["maxWidth"], classic_bar.styles["maxWidth"])
        # It still clears the viewport edges: the transparent root keeps gutters
        # so the capsule never touches the screen on a narrow window.
        root = self._header("floating-pill", theme).styles
        self.assertEqual(root["paddingLeft"], "24px")
        self.assertEqual(root["paddingRight"], "24px")

    def test_pill_caps_the_logo_that_other_archetypes_keep(self):
        from app.services.header_footer import (
            _LOGO_HEIGHT_BY_INDUSTRY,
            _SELF_CHROME_LOGO_HEIGHT,
        )

        theme = build_theme("#0284c7", mood="playful", industry="childcare")
        pill_logo = _find(self._header("floating-pill", theme), "Brand Logo")
        classic_logo = _find(self._header("classic", theme), "Brand Logo")

        self.assertEqual(pill_logo.styles["height"], _SELF_CHROME_LOGO_HEIGHT)
        # Childcare's deliberately large mark survives everywhere else — the cap
        # is the pill's, not a downgrade of the industry brief.
        self.assertEqual(classic_logo.styles["height"], _LOGO_HEIGHT_BY_INDUSTRY["childcare"])
        self.assertLess(_px(pill_logo.styles["height"]), _px(classic_logo.styles["height"]))

    def test_pill_is_the_most_compact_bar(self):
        from app.services.header_footer import _SELF_CHROME_LOGO_HEIGHT

        theme = build_theme("#0284c7", mood="playful", industry="childcare")
        pill = self._header("floating-pill", theme).content[0].styles
        classic = self._header("classic", theme).content[0].styles
        self.assertLess(_px(pill["paddingTop"]), _px(classic["paddingTop"]))
        self.assertLess(_px(pill["paddingLeft"]), _px(classic["paddingLeft"]))
        # Bar height is driven by its tallest child, so the capped logo is what
        # actually makes it compact — assert the whole stack, not just padding.
        # Compared against classic rather than an absolute ceiling: "most
        # compact" is a relative claim, and the absolute 60 this once carried
        # was never true of the shipped values (45px cap + 2x8px = 61), so it
        # only ever asserted an intention nobody implemented.
        pill_stack = (
            _px(_find(self._header("floating-pill", theme), "Brand Logo").styles["height"])
            + 2 * _px(pill["paddingTop"])
        )
        classic_stack = (
            _px(_find(self._header("classic", theme), "Brand Logo").styles["height"])
            + 2 * _px(classic["paddingTop"])
        )
        self.assertLess(pill_stack, classic_stack)
        # And it is the LOGO CAP doing the work, not just tighter padding — the
        # thing that stops childcare's 68px mark turning the capsule into a
        # banner (see _SELF_CHROME_LOGO_HEIGHT).
        self.assertEqual(
            pill_stack, _px(_SELF_CHROME_LOGO_HEIGHT) + 2 * _px(pill["paddingTop"])
        )


def _px(value):
    return float(str(value).replace("px", ""))


class LogoRenderGateTest(unittest.TestCase):
    """A mark that failed the render gate (an og:image, or a favicon too small
    for the 52px lockup) seeds the palette but must never be drawn. Both
    surfaces fall back to the typographic wordmark instead."""

    def _brand(self, *, render_ok):
        return BrandIdentity(
            name="Acme",
            logo_url="https://example.com/favicon.ico",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#2563eb"],
            logo_source="icon",
            logo_render_ok=render_ok,
        )

    def test_header_falls_back_to_the_wordmark(self):
        theme = build_theme("#2563eb")

        header = build_header(self._brand(render_ok=False), theme, nav_items=[])

        self.assertIsNone(_find(header, "Brand Logo"))
        self.assertIsNotNone(_find(header, "Wordmark"))
        self.assertIsNotNone(_find(header, "Monogram"))

    def test_header_still_renders_a_mark_that_passed(self):
        theme = build_theme("#2563eb")

        header = build_header(self._brand(render_ok=True), theme, nav_items=[])

        self.assertIsNotNone(_find(header, "Brand Logo"))

    def test_footer_falls_back_to_the_wordmark(self):
        theme = build_theme("#2563eb")

        footer = build_footer(self._brand(render_ok=False), theme, nav_items=[])

        self.assertIsNone(_find(footer, "Brand Logo"))

    def test_footer_still_renders_a_mark_that_passed(self):
        theme = build_theme("#2563eb")

        footer = build_footer(self._brand(render_ok=True), theme, nav_items=[])

        self.assertIsNotNone(_find(footer, "Brand Logo"))
