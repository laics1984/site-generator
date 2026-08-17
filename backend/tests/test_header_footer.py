import unittest

from app.models.brand import BrandIdentity
from app.services.header_footer import build_footer, build_header, built_value
from app.services.theme import _hex_to_rgb, build_theme


class HeaderContrastTest(unittest.TestCase):
    def test_light_logo_on_light_site_keeps_light_header_and_gets_dark_lockup(self):
        brand = BrandIdentity(
            name="Acme",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#2563eb"],
            logo_is_light=True,
        )
        theme = build_theme("#2563eb")

        header = build_header(brand, theme, nav_items=[])

        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)
        chip = _find(header, "Logo lockup")
        self.assertIsNotNone(chip)
        self.assertEqual(chip.styles.get("backgroundColor"), theme.palette.secondary)


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


class HeaderLogoLockupTest(unittest.TestCase):
    def _header(self, *, dark, logo_is_light):
        brand = BrandIdentity(
            name="Acme",
            logo_data_url="data:image/png;base64,abc",
            extracted_palette=["#2563eb"],
            logo_is_light=logo_is_light,
        )
        theme = build_theme("#2563eb", color_scheme="dark" if dark else "light")
        return build_header(brand, theme, nav_items=[])

    def test_dark_logo_on_dark_site_gets_light_lockup(self):
        header = self._header(dark=True, logo_is_light=False)
        theme = build_theme("#2563eb", color_scheme="dark")
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)
        chip = _find(header, "Logo lockup")
        self.assertIsNotNone(chip)
        self.assertEqual(chip.styles.get("backgroundColor"), "#ffffff")

    def test_dark_logo_on_light_site_has_no_lockup(self):
        header = self._header(dark=False, logo_is_light=False)
        theme = build_theme("#2563eb", color_scheme="light")
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)
        self.assertIsNone(_find(header, "Logo lockup"))

    def test_light_logo_on_dark_site_has_no_lockup(self):
        header = self._header(dark=True, logo_is_light=True)
        theme = build_theme("#2563eb", color_scheme="dark")
        self.assertEqual(header.styles.get("backgroundColor"), theme.palette.background)
        self.assertIsNone(_find(header, "Logo lockup"))


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

    def test_dark_logo_gets_lockup_chip_in_overlay_mode(self):
        # On a light solid header a dark logo needs no chip — but floating
        # over a dark hero it does; overlay mode forces it.
        _, solid = self._header(overlay=False, logo_is_light=False)
        self.assertIsNone(_find(solid, "Logo lockup"))
        _, overlaid = self._header(overlay=True, logo_is_light=False)
        chip = _find(overlaid, "Logo lockup")
        self.assertIsNotNone(chip)
        self.assertEqual(chip.styles.get("backgroundColor"), "#ffffff")

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
        header_bg, header_fg, _ = _header_chrome(brand, theme)
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
        # A bitmap cannot be recoloured; a dark logo over a dark section is the
        # `lockup` contrast chip's problem, not the ink var's.
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

    def test_nothing_of_the_backdrop_s_colour_survives_the_pane(self):
        """Desaturation has to be TOTAL, not merely strong.

        The blur flattens the backdrop into one large even wash before the eye
        gets to it, so a residual fraction of hue does not read as "a hint of
        colour" — it reads as a solid tinted panel, which is the whole
        complaint. A hero carrying the brand's cast came through a
        `grayscale(0.8)` pane as a blue-grey; only full desaturation is neutral.
        """
        import colorsys

        _, _, bar = self._pill("light")
        amount = _grayscale_amount(bar)
        self.assertEqual(amount, 1.0)
        for backdrop in ("#3d5a80", "#b08050", "#0e7490"):  # brand cast, warm, teal
            with self.subTest(backdrop=backdrop):
                seen = _composite(
                    built_value(bar.styles["backgroundColor"]),
                    _grayscale(backdrop, amount),
                )
                r, g, b = (c / 255 for c in _hex_to_rgb(seen))
                self.assertAlmostEqual(colorsys.rgb_to_hls(r, g, b)[2], 0.0, places=2)

    def test_desaturating_the_backdrop_costs_no_nav_contrast(self):
        # grayscale() is a luma projection, so it moves a backdrop's luminance
        # barely at all — which is why the pane can neutralise colour without
        # eating into GLASS_ALPHA's readability bound.
        from app.services.theme import _contrast

        _, _, bar = self._pill("light")
        ink = built_value(_find_type(bar, "menu").styles["color"])
        amount = _grayscale_amount(bar)
        for backdrop in ("#0e7490", "#20262f", "#b0b4bc", "#fdf0dd"):
            with self.subTest(backdrop=backdrop):
                plain = _contrast(
                    ink, _composite(built_value(bar.styles["backgroundColor"]), backdrop)
                )
                grey = _contrast(
                    ink,
                    _composite(
                        built_value(bar.styles["backgroundColor"]),
                        _grayscale(backdrop, amount),
                    ),
                )
                self.assertAlmostEqual(plain, grey, delta=0.25)
                self.assertGreaterEqual(grey, 4.5)

    def test_bar_opts_out_of_the_theme_s_decorative_section_texture(self):
        """The pane must stay glass, not become a brand-tinted panel.

        The renderers live-recompute a grain/mesh `backgroundImage` for any
        `container` whose background resolves to a plain theme colour, and give
        it the theme's `backgroundTexture` default. The pill's veil resolves to
        exactly `palette.background`, so it reads as a plain section and gets
        the site's aurora mesh painted ON it — four brand-hued radial gradients
        that `backdrop-filter` cannot touch, because the filter only affects
        what is BEHIND an element, never its own background-image.

        The gate that was supposed to keep chrome out excludes header/footer
        ROOTS by node type; the pill is the one archetype whose painted surface
        is a plain container child, so it walked straight through. An explicit
        `flat` override wins over the theme default in all three renderers
        (`resolveSectionBackgroundImage`), which strip the image outright.
        """
        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                theme, _, bar = self._pill(scheme)
                # The default this is opting out of is real, not hypothetical.
                self.assertEqual(theme.to_builder_styles()["backgroundTexture"], "mesh")
                self.assertEqual(bar.backgroundTexture, "flat")
                self.assertEqual(bar.model_dump(mode="json")["backgroundTexture"], "flat")
                # And the generator bakes no image of its own either.
                self.assertNotIn("backgroundImage", bar.styles)

    def test_bar_carries_the_headerBar_marker(self):
        # Rides the catalog field whitelist: dropped by _base_fields, the
        # renderers stop recognising which node paints the chrome and the
        # builder falls back to matching the bar by its name.
        _, _, bar = self._pill("light")
        self.assertIs(bar.headerBar, True)
        self.assertIs(bar.model_dump(mode="json")["headerBar"], True)

    def test_nav_ink_holds_AA_over_every_backdrop_the_pill_reaches(self):
        """The glass is as thin as this bound allows — so hold the bound.

        The pill never solidifies, so it is sticky over the WHOLE page and its
        nav has to survive every surface underneath: the hero's scrimmed photo,
        an unscrimmed photo mid-page, and the theme's own bands. Those bands
        are read off the palette rather than assumed — a dark-scheme site has
        no white section for the bar to cross, so testing one would be
        inventing a failure mode instead of covering a real one.
        """
        from app.services.theme import _contrast

        SCRIMMED_HERO = "#20262f"  # photo under the hero's legibility overlay
        BRIGHT_PHOTO = "#b0b4bc"  # the brightest an in-page photo tends to run

        for scheme in ("light", "dark"):
            theme, _, bar = self._pill(scheme)
            ink = built_value(_find_type(bar, "menu").styles["color"])
            backdrops = (
                SCRIMMED_HERO,
                BRIGHT_PHOTO,
                theme.palette.background,
                theme.palette.surface,
            )
            for backdrop in backdrops:
                with self.subTest(scheme=scheme, backdrop=backdrop):
                    surface = _composite(
                        built_value(bar.styles["backgroundColor"]), backdrop
                    )
                    self.assertGreaterEqual(_contrast(ink, surface), 4.5)

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


def _grayscale(hex_color, amount):
    """CSS `grayscale(amount)`: interpolate toward the luma projection, on the
    gamma-encoded channels, exactly as the filter spec defines it."""
    from app.services.theme import _hex_to_rgb, _rgb_to_hex

    r, g, b = _hex_to_rgb(hex_color)
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return _rgb_to_hex(*[round(c + (luma - c) * amount) for c in (r, g, b)])


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
        theme = build_theme("#0284c7", mood="playful", industry="childcare")
        pill = self._header("floating-pill", theme).content[0].styles
        classic = self._header("classic", theme).content[0].styles
        self.assertLess(_px(pill["paddingTop"]), _px(classic["paddingTop"]))
        self.assertLess(_px(pill["paddingLeft"]), _px(classic["paddingLeft"]))
        # Bar height is driven by its tallest child, so the capped logo is what
        # actually makes it compact — assert the whole stack, not just padding.
        logo = _px(_find(self._header("floating-pill", theme), "Brand Logo").styles["height"])
        self.assertLessEqual(logo + 2 * _px(pill["paddingTop"]), 60)


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
