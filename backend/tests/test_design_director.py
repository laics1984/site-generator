"""Design director + diversity engine + chrome archetypes.

Covers the three new design-engine seams:
  * pick_diverse — seeded determinism, avoidance, saturation fallback
  * compose_design_manifest — fit tables, industry pins, decision log, overlay
  * build_header / build_footer archetype branches — structural contracts the
    renderers rely on (root chrome keys, ink markers, degrade paths)
"""

import unittest

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.design_manifest import (
    OVERLAY_CAPABLE_HEADERS,
    DesignManifest,
)
from app.services.design_director import compose_design_manifest
from app.services.diversity import pick_diverse, seeded_index
from app.services.header_footer import (
    HEADER_DIVIDER_SUBTLE,
    build_footer,
    build_header,
)
from app.services.theme import build_theme


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


class PickDiverseTest(unittest.TestCase):
    CANDIDATES = ["a", "b", "c"]

    def test_seeded_pick_is_deterministic(self):
        first = pick_diverse(self.CANDIDATES, seed="Acme", salt="header")
        second = pick_diverse(self.CANDIDATES, seed="Acme", salt="header")
        self.assertEqual(first, second)

    def test_different_seeds_can_diverge(self):
        picks = {
            pick_diverse(self.CANDIDATES, seed=seed, salt="header")
            for seed in ("Acme", "Globex", "Initech", "Hooli", "Umbrella")
        }
        self.assertGreater(len(picks), 1, "five brands should not all pick one archetype")

    def test_avoid_steps_to_next_candidate(self):
        base = pick_diverse(self.CANDIDATES, seed="Acme", salt="header")
        shifted = pick_diverse(
            self.CANDIDATES, seed="Acme", salt="header", avoid={base}
        )
        self.assertNotEqual(shifted, base)
        self.assertIn(shifted, self.CANDIDATES)

    def test_saturated_avoid_falls_back_to_seeded_pick(self):
        base = pick_diverse(self.CANDIDATES, seed="Acme", salt="header")
        saturated = pick_diverse(
            self.CANDIDATES, seed="Acme", salt="header", avoid=set(self.CANDIDATES)
        )
        self.assertEqual(saturated, base)

    def test_seeded_index_stable_across_calls(self):
        self.assertEqual(
            seeded_index("Acme", "header", 3), seeded_index("Acme", "header", 3)
        )


class ComposeManifestTest(unittest.IsolatedAsyncioTestCase):
    async def test_same_brand_is_idempotent(self):
        a = await compose_design_manifest(
            brand_name="Acme", mood="modern", industry=None
        )
        b = await compose_design_manifest(
            brand_name="Acme", mood="modern", industry=None
        )
        self.assertEqual(a.header_archetype, b.header_archetype)
        self.assertEqual(a.footer_archetype, b.footer_archetype)

    async def test_industry_pin_restricts_vocabulary_and_raises_confidence(self):
        manifest = await compose_design_manifest(
            brand_name="Little Sprouts", mood="playful", industry="childcare"
        )
        self.assertIn(manifest.header_archetype, ("classic", "floating-pill"))
        decision = manifest.decision_for("header")
        self.assertIsNotNone(decision)
        self.assertGreaterEqual(decision.confidence, 0.9)

    async def test_every_choice_carries_a_decision(self):
        manifest = await compose_design_manifest(
            brand_name="Acme", mood="luxury", industry=None
        )
        self.assertIsNotNone(manifest.decision_for("header"))
        self.assertIsNotNone(manifest.decision_for("footer"))
        for decision in manifest.decisions:
            self.assertTrue(decision.rationale)

    async def test_pill_archetype_records_overlay_native_decision(self):
        # Find a brand whose seeded pick is the floating pill (playful mood
        # leads with it), then assert the overlay-native semantics: the pill IS
        # overlay-capable (it floats over full-bleed heroes) but never reveals
        # a background — its bar chromes itself.
        for name in ("Bounce", "Pop", "Zippy", "Waggle", "Frolic", "Boing"):
            manifest = await compose_design_manifest(
                brand_name=name, mood="playful", industry=None
            )
            if manifest.header_archetype == "floating-pill":
                self.assertTrue(manifest.header_overlay_capable)
                self.assertFalse(manifest.header_overlay_reveals)
                decision = manifest.decision_for("header-overlay")
                self.assertIsNotNone(decision)
                self.assertEqual(decision.choice, "floating")
                return
        self.fail("no playful seed picked floating-pill — fit table changed?")

    async def test_default_manifest_is_legacy_chrome(self):
        manifest = DesignManifest()
        self.assertEqual(manifest.header_archetype, "classic")
        self.assertEqual(manifest.footer_archetype, "mega")
        self.assertTrue(manifest.header_overlay_capable)
        self.assertTrue(manifest.header_overlay_reveals)


class ArchetypeOverrideTest(unittest.IsolatedAsyncioTestCase):
    """Explicit generation overrides win over fit/seed/diversity and are the
    only way to reach a chrome the fit table would never surface for a brand."""

    async def test_header_override_beats_industry_pin(self):
        # floating-pill is NOT in the nonprofit header pin
        # (classic/glass-blur/centered-stack) — only an explicit pin reaches it.
        manifest = await compose_design_manifest(
            brand_name="MMTA",
            mood="friendly",
            industry="nonprofit",
            header_override="floating-pill",
        )
        self.assertEqual(manifest.header_archetype, "floating-pill")
        decision = manifest.decision_for("header")
        self.assertEqual(decision.confidence, 1.0)
        self.assertIn("override", decision.rationale.lower())

    async def test_footer_override_beats_industry_pin(self):
        # editorial is NOT in the nonprofit footer pin (cta-banner/mega).
        manifest = await compose_design_manifest(
            brand_name="MMTA",
            mood="friendly",
            industry="nonprofit",
            footer_override="editorial",
        )
        self.assertEqual(manifest.footer_archetype, "editorial")
        self.assertEqual(manifest.decision_for("footer").confidence, 1.0)

    async def test_pill_override_is_overlay_native(self):
        # Overriding to the pill must carry its overlay-native semantics — the
        # coupling holds regardless of how the pill was chosen: overlay-capable
        # but no background reveal, recorded as the 'floating' decision.
        manifest = await compose_design_manifest(
            brand_name="Acme",
            mood="modern",
            industry=None,
            header_override="floating-pill",
        )
        self.assertTrue(manifest.header_overlay_capable)
        self.assertFalse(manifest.header_overlay_reveals)
        decision = manifest.decision_for("header-overlay")
        self.assertIsNotNone(decision)
        self.assertEqual(decision.choice, "floating")

    async def test_no_override_matches_fit_pick(self):
        # None overrides → identical to the plain fit/seed pick (no behaviour
        # change for existing callers).
        plain = await compose_design_manifest(
            brand_name="Acme", mood="modern", industry=None
        )
        explicit_none = await compose_design_manifest(
            brand_name="Acme",
            mood="modern",
            industry=None,
            header_override=None,
            footer_override=None,
        )
        self.assertEqual(plain.header_archetype, explicit_none.header_archetype)
        self.assertEqual(plain.footer_archetype, explicit_none.footer_archetype)

    async def test_override_only_touches_the_area_given(self):
        # A header override leaves the footer on its normal fit/seed pick.
        pinned = await compose_design_manifest(
            brand_name="Acme",
            mood="modern",
            industry=None,
            header_override="minimal-line",
        )
        plain = await compose_design_manifest(
            brand_name="Acme", mood="modern", industry=None
        )
        self.assertEqual(pinned.header_archetype, "minimal-line")
        self.assertEqual(pinned.footer_archetype, plain.footer_archetype)
        # footer decision keeps its normal (sub-1.0) confidence
        self.assertLess(pinned.decision_for("footer").confidence, 1.0)


class HeaderArchetypeTest(unittest.TestCase):
    def setUp(self):
        self.brand = BrandIdentity(name="Acme", extracted_palette=["#2563eb"])
        self.theme = build_theme("#2563eb")

    def _header(self, archetype, **kwargs):
        return build_header(
            self.brand,
            self.theme,
            nav_items=[],
            primary_cta=("Get in touch", "#contact"),
            archetype=archetype,
            **kwargs,
        )

    def test_classic_keeps_legacy_root_chrome(self):
        header = self._header("classic")
        self.assertEqual(
            header.styles["backgroundColor"], self.theme.palette.background
        )
        self.assertEqual(header.styles["boxShadow"], HEADER_DIVIDER_SUBTLE)

    def test_glass_blur_is_translucent_with_backdrop_filter(self):
        header = self._header("glass-blur")
        self.assertTrue(header.styles["backgroundColor"].startswith("rgba("))
        self.assertIn("blur", header.styles["backdropFilter"])
        self.assertEqual(header.styles["boxShadow"], HEADER_DIVIDER_SUBTLE)

    def test_floating_pill_root_is_transparent_and_bar_carries_chrome(self):
        header = self._header("floating-pill")
        self.assertEqual(header.styles["backgroundColor"], "transparent")
        # Absent, not "none" — the builder Divider panel maps only ''/absent
        # to its "None" preset.
        self.assertNotIn("boxShadow", header.styles)
        bar = _find(header, "Header bar")
        self.assertTrue(bar.styles["backgroundColor"].startswith("rgba("))
        self.assertIn("borderRadius", bar.styles)
        self.assertIn("backdropFilter", bar.styles)

    def test_floating_pill_carries_no_ink_markers(self):
        # Overlay-native: the pill floats over heroes with its own chrome, so
        # NO node may carry wt-header-ink — the renderer's overlay phase would
        # force it white on the light pill bar (.wt-page-header--overlay
        # .wt-header-ink). Logo, menu, CTA: all unmarked.
        header = self._header("floating-pill")

        def collect_classes(node, out):
            if getattr(node, "classes", None):
                out.append((node.name, node.classes))
            content = getattr(node, "content", None)
            if isinstance(content, list):
                for ch in content:
                    collect_classes(ch, out)

        marked: list[tuple[str, str]] = []
        collect_classes(header, marked)
        inked = [(n, c) for n, c in marked if "wt-header-ink" in c]
        self.assertEqual(inked, [], "pill must not carry ink markers")

    def test_centered_stack_has_two_rows_with_centered_brand(self):
        header = self._header("centered-stack")
        brand_row = _find(header, "Header brand row")
        nav_row = _find(header, "Header nav row")
        self.assertIsNotNone(brand_row)
        self.assertIsNotNone(nav_row)
        # The brand is centered by equal flex spacers on either side (spacer +
        # logo + actions), so the logo stays dead-center while the CTA sits at
        # the top-right of the same row.
        self.assertIsNotNone(_find(brand_row, "Brand row spacer"))
        actions = _find(brand_row, "Header actions")
        self.assertIsNotNone(actions)
        self.assertIsNotNone(_find(actions, "Header CTA"))
        # The nav row is now just the full-width centered menu — no CTA — so the
        # navigation sits truly centered below the logo.
        self.assertEqual(nav_row.styles["justifyContent"], "center")
        self.assertIsNotNone(_find_type(nav_row, "menu"))
        self.assertIsNone(_find(nav_row, "Header CTA"))

    def test_minimal_line_uses_hairline_rule_and_ghost_cta(self):
        header = self._header("minimal-line")
        self.assertNotIn("boxShadow", header.styles)
        self.assertIn("borderBottom", header.styles)
        cta = _find(header, "Header CTA")
        self.assertEqual(cta.styles["backgroundColor"], "transparent")
        self.assertIn("border", cta.styles)
        # Ghost CTA borrows the header ink, so it must flip during overlay.
        self.assertEqual(cta.classes, "wt-header-ink")

    def test_solid_cta_never_carries_ink_marker(self):
        for archetype in ("classic", "glass-blur", "floating-pill", "centered-stack"):
            cta = _find(self._header(archetype), "Header CTA")
            self.assertIsNone(cta.classes, archetype)

    def test_every_archetype_keeps_menu_and_logo(self):
        for archetype in (
            "classic",
            "glass-blur",
            "floating-pill",
            "centered-stack",
            "minimal-line",
        ):
            header = self._header(archetype)
            self.assertIsNotNone(_find_type(header, "menu"), archetype)
            self.assertIsNotNone(_find(header, "Brand"), archetype)
            self.assertEqual(header.type, "__header", archetype)


class FooterArchetypeTest(unittest.TestCase):
    def setUp(self):
        self.brand = BrandIdentity(
            name="Acme", tagline="We make things.", extracted_palette=["#2563eb"]
        )
        self.theme = build_theme("#2563eb")

    def _footer(self, archetype, **kwargs):
        return build_footer(
            self.brand,
            self.theme,
            nav_items=[("About", "/about")],
            archetype=archetype,
            **kwargs,
        )

    def test_mega_keeps_legacy_dark_chrome(self):
        footer = self._footer("mega")
        self.assertEqual(
            footer.styles["backgroundColor"], self.theme.palette.secondary
        )
        self.assertIsNotNone(_find(footer, "Footer grid"))
        self.assertIsNotNone(_find(footer, "Legal bar"))

    def test_cta_banner_renders_headline_and_button(self):
        footer = self._footer("cta-banner", primary_cta=("Get in touch", "#contact"))
        banner = _find(footer, "Footer CTA banner")
        self.assertIsNotNone(banner)
        cta = _find(footer, "Footer CTA")
        self.assertEqual(cta.styles["backgroundColor"], self.theme.buttons.background)
        headline = _find(footer, "Footer CTA headline")
        self.assertEqual(headline.content.innerText, "We make things.")

    def test_cta_banner_degrades_to_mega_without_cta(self):
        footer = self._footer("cta-banner")  # no primary_cta
        self.assertIsNone(_find(footer, "Footer CTA banner"))
        self.assertEqual(
            footer.styles["backgroundColor"], self.theme.palette.secondary
        )

    def test_minimal_centered_sits_on_light_band_with_correct_ink(self):
        from app.services.theme import _contrast

        footer = self._footer("minimal-centered")
        bg = footer.styles["backgroundColor"]
        self.assertEqual(bg, self.theme.palette.surface)
        ink = footer.styles["color"]
        self.assertGreaterEqual(_contrast(bg, ink), 4.5)
        self.assertIsNotNone(_find(footer, "Footer stack"))

    def test_editorial_renders_ghost_wordmark(self):
        footer = self._footer("editorial")
        wordmark = _find(footer, "Footer wordmark")
        self.assertIsNotNone(wordmark)
        self.assertEqual(wordmark.content.innerText, "Acme")
        self.assertTrue(wordmark.styles["color"].startswith("rgba("))


class DiversityHistoryTest(unittest.IsolatedAsyncioTestCase):
    """End-to-end through the SQLite history with an isolated DB file."""

    async def test_record_then_avoid_roundtrip(self):
        import tempfile
        from pathlib import Path

        from app.services import db as db_module
        from app.services import diversity as diversity_module
        from app.services.diversity import record_choice, recent_choices

        original_flag = settings.diversity_engine_enabled
        original_path = db_module.DB_PATH
        original_boot = db_module._BOOTSTRAPPED
        original_schema = diversity_module._SCHEMA_READY
        with tempfile.TemporaryDirectory() as tmp:
            db_module.DB_PATH = Path(tmp) / "diversity-test.db"
            db_module._BOOTSTRAPPED = False
            diversity_module._SCHEMA_READY = False
            settings.diversity_engine_enabled = True
            try:
                await record_choice("header", "glass-blur", site_key="Acme")
                avoid = await recent_choices("header", site_key="Acme")
                self.assertIn("glass-blur", avoid)
                # A different site also avoids the most recent global pick.
                other = await recent_choices("header", site_key="Globex")
                self.assertIn("glass-blur", other)
            finally:
                settings.diversity_engine_enabled = original_flag
                db_module.DB_PATH = original_path
                db_module._BOOTSTRAPPED = original_boot
                diversity_module._SCHEMA_READY = original_schema

    async def test_disabled_engine_returns_no_history(self):
        from app.services.diversity import recent_choices

        self.assertFalse(settings.diversity_engine_enabled)  # conftest default
        self.assertEqual(await recent_choices("header", site_key="Acme"), set())


class ManifestPipelineIntegrationTest(unittest.IsolatedAsyncioTestCase):
    """Offline end-to-end: the manifest rides GeneratedSite with the full
    decision log (chrome + palette + typography + per-page heroes)."""

    async def _site(self):
        from app.models.content_blocks import CtaBlock, HeroBlock, PagePlan, SitePlan
        from app.services.schema_builder import plan_to_site

        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            plan = SitePlan(
                site_name="Acme Studio",
                brand_mood="modern",
                pages=[
                    PagePlan(
                        page_type="home",
                        slug="home",
                        title="Home",
                        is_homepage=True,
                        blocks=[
                            HeroBlock(headline="Hello", subheadline="World"),
                            CtaBlock(
                                headline="Go",
                                body="Now.",
                                cta_label="Start",
                                cta_href="/contact",
                            ),
                        ],
                        seo_title="Home",
                        seo_description="Home",
                    )
                ],
            )
            return await plan_to_site(
                plan, brand=BrandIdentity(name="Acme Studio", mood="modern")
            )
        finally:
            settings.design_brain_enabled = original

    async def test_manifest_rides_builder_styles_into_the_cms(self):
        # The CMS/builder receive builderStyles wholesale; the manifest rides
        # it (same flexible-JSON channel as googleFonts) so a builder save
        # round-trip can preserve the decision record.
        site = await self._site()
        carried = site.builder_styles.get("designManifest")
        self.assertIsNotNone(carried)
        self.assertEqual(carried, site.design_manifest)

    async def test_palette_decision_records_curated_slug_when_curated(self):
        # "Acme Studio" has no logo hue → palette_mode="auto" takes the curated
        # path → the manifest's palette decision is a real curated slug (not a
        # hex), which is what the diversity history steers on.
        site = await self._site()
        decision = next(
            d for d in site.design_manifest["decisions"] if d["area"] == "palette"
        )
        self.assertFalse(decision["choice"].startswith("#"))
        self.assertEqual(decision["choice"], site.theme.palette_slug)

    async def test_manifest_rides_generated_site_with_decisions(self):
        site = await self._site()
        manifest = site.design_manifest
        self.assertIsNotNone(manifest)
        self.assertEqual(manifest["version"], 1)
        self.assertEqual(manifest["seed"], "Acme Studio")
        areas = {d["area"] for d in manifest["decisions"]}
        self.assertIn("header", areas)
        self.assertIn("footer", areas)
        self.assertIn("palette", areas)
        self.assertIn("typography", areas)
        self.assertIn("hero-homepage", areas)
        for decision in manifest["decisions"]:
            self.assertTrue(decision["rationale"], decision["area"])

    async def test_header_archetype_in_manifest_matches_emitted_chrome(self):
        site = await self._site()
        archetype = site.design_manifest["header_archetype"]
        root_styles = site.header_schema.styles
        if archetype == "glass-blur":
            self.assertIn("backdropFilter", root_styles)
        elif archetype == "floating-pill":
            self.assertEqual(root_styles["backgroundColor"], "transparent")
        elif archetype == "minimal-line":
            self.assertIn("borderBottom", root_styles)
        else:
            self.assertNotIn("backdropFilter", root_styles)

    async def test_design_engine_off_yields_legacy_chrome(self):
        original = settings.design_engine_enabled
        settings.design_engine_enabled = False
        try:
            site = await self._site()
        finally:
            settings.design_engine_enabled = original
        self.assertEqual(site.design_manifest["header_archetype"], "classic")
        self.assertEqual(site.design_manifest["footer_archetype"], "mega")
        self.assertEqual(site.design_manifest["decisions"], [])


class TemplateVarietySeedTest(unittest.TestCase):
    """block_to_section's variety_seed rotates the candidate head for TEXT-ONLY
    sections only — imagery-led preference (a hard signal) and explicit ids
    still lead, and no seed means legacy order. A photo-less CTA is the
    canonical text-only case (features/services synthesize card imagery from
    titles, so their photo policy always leads — deliberately untouched)."""

    def _cta_block(self):
        from app.models.content_blocks import CtaBlock

        return CtaBlock(
            headline="Ready to start?",
            body="Talk to us today.",
            cta_label="Get in touch",
            cta_href="/contact",
        )

    def test_no_seed_keeps_legacy_pick(self):
        from app.services.section_content import block_to_section

        a, _ = block_to_section(self._cta_block(), mood="modern")
        b, _ = block_to_section(self._cta_block(), mood="modern")
        self.assertEqual(a["id"], b["id"])
        self.assertEqual(a["id"], "cta-banner")  # legacy deterministic head

    def test_seed_is_idempotent_and_seeds_can_diverge(self):
        from app.services.section_content import block_to_section

        first, _ = block_to_section(
            self._cta_block(), mood="modern", variety_seed="Acme"
        )
        again, _ = block_to_section(
            self._cta_block(), mood="modern", variety_seed="Acme"
        )
        self.assertEqual(first["id"], again["id"])

        picks = set()
        for seed in ("Acme", "Globex", "Initech", "Hooli", "Umbrella", "Stark"):
            t, _ = block_to_section(
                self._cta_block(), mood="modern", variety_seed=seed
            )
            picks.add(t["id"])
        self.assertGreater(
            len(picks), 1, "six brands should not all land on one CTA layout"
        )
        # Rotation never smuggles in an infeasible variant: cta-background
        # needs an image this block doesn't have.
        self.assertNotIn("cta-background", picks)

    def test_explicit_id_beats_variety_seed(self):
        from app.services.section_content import block_to_section

        t, _ = block_to_section(
            self._cta_block(),
            mood="modern",
            variety_seed="Acme",
            explicit_id="cta-editorial",
        )
        self.assertEqual(t["id"], "cta-editorial")

    def test_imagery_led_features_ignore_variety_seed(self):
        from app.models.content_blocks import FeatureItem, FeaturesBlock
        from app.services.section_content import block_to_section

        block = FeaturesBlock(
            heading="What we do",
            items=[
                FeatureItem(title=f"Thing {i}", description="Useful.")
                for i in range(3)
            ],
        )
        # Card imagery is synthesized from titles, so the photo-topped policy
        # is a hard site rule — every seed must yield the same image-card grid.
        for seed in ("Acme", "Globex", "Initech"):
            t, _ = block_to_section(block, mood="modern", variety_seed=seed)
            self.assertEqual(t["id"], "features-image-cards", seed)


class PaletteAvoidanceTest(unittest.TestCase):
    """build_theme(avoid_palettes=…) rotates the curated pick within its fit
    group; empty avoidance is byte-identical to the legacy pick, and the
    non-curated paths never react to history."""

    def test_no_avoidance_keeps_legacy_pick_and_records_slug(self):
        a = build_theme(None, palette_mode="curated", font_seed="Acme")
        b = build_theme(None, palette_mode="curated", font_seed="Acme")
        self.assertIsNotNone(a.palette_slug)
        self.assertEqual(a.palette_slug, b.palette_slug)
        self.assertEqual(a.palette.primary, b.palette.primary)

    def test_avoiding_the_base_pick_rotates_to_another_curated_palette(self):
        base = build_theme(None, palette_mode="curated", font_seed="Acme")
        shifted = build_theme(
            None,
            palette_mode="curated",
            font_seed="Acme",
            avoid_palettes={base.palette_slug},
        )
        self.assertIsNotNone(shifted.palette_slug)
        self.assertNotEqual(shifted.palette_slug, base.palette_slug)

    def test_saturated_avoidance_falls_back_to_base_pick(self):
        from app.services.theme import curated_palette_options

        base = build_theme(None, palette_mode="curated", font_seed="Acme")
        every_slug = {str(o["slug"]) for o in curated_palette_options(None)}
        saturated = build_theme(
            None, palette_mode="curated", font_seed="Acme", avoid_palettes=every_slug
        )
        self.assertEqual(saturated.palette_slug, base.palette_slug)

    def test_brand_hue_snap_path_ignores_history_and_has_no_slug(self):
        theme = build_theme(
            "#2563eb", palette_mode="auto", font_seed="Acme",
            avoid_palettes={"any-slug"},
        )
        self.assertIsNone(theme.palette_slug)

    def test_explicit_design_language_pick_is_never_overridden(self):
        from app.services.theme import curated_palette_options

        slug = str(curated_palette_options(None)[0]["slug"])
        theme = build_theme(
            None,
            palette_mode="curated",
            font_seed="Acme",
            palette_choice=slug,
            avoid_palettes={slug},  # history says avoid — the LLM pick still wins
        )
        self.assertEqual(theme.palette_slug, slug)


class OverlayCapabilityContractTest(unittest.TestCase):
    def test_every_archetype_can_overlay(self):
        self.assertEqual(
            set(OVERLAY_CAPABLE_HEADERS),
            {"classic", "glass-blur", "floating-pill", "centered-stack", "minimal-line"},
        )

    def test_floating_pill_is_the_only_self_chrome_archetype(self):
        # Self-chrome = overlays without the transparent phase: no ink flip,
        # no background reveal. Anything added here must ALSO be in
        # OVERLAY_CAPABLE_HEADERS and carry no wt-header-ink markers in its
        # catalog template.
        from app.models.design_manifest import SELF_CHROME_HEADERS

        self.assertEqual(set(SELF_CHROME_HEADERS), {"floating-pill"})
        self.assertTrue(SELF_CHROME_HEADERS <= OVERLAY_CAPABLE_HEADERS)


if __name__ == "__main__":
    unittest.main()
