"""Design schemes — the style-pack layer (services/design_schemes.py).

Two things need pinning and they pull in opposite directions:

* with the kill switch OFF the generator must be byte-identical to what it was
  before schemes existed — every other test module in this suite asserts the
  deferring path, so a regression there is a regression everywhere;
* with it ON, two sites in the same (mood, industry) cell must actually come
  out different, which is the whole point and is not something the rest of the
  suite can see.

Plus the constraints the RENDERERS impose, which are invisible from Python and
therefore the ones most likely to be broken by a future edit.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import re
import unittest
from typing import get_args

from app.config import settings
from app.models.brand import BrandIdentity, BrandMood
from app.models.builder_schema import BuilderElement
from app.models.content_blocks import (
    AboutBlock,
    CtaBlock,
    FeatureItem,
    FeaturesBlock,
    HeroBlock,
    PagePlan,
    ServiceItem,
    ServicesBlock,
    SitePlan,
)
from app.models.industry import IndustryCategory
from app.services import design_schemes
from app.services.design_schemes import (
    DESIGN_SCHEMES,
    NEUTRAL_SCHEME,
    RENDERER_PINNED_GAP_NAMES,
    RENDERER_PINNED_MIN_HEIGHT_NAMES,
    by_slug,
    candidates_for,
    select_scheme,
)
from app.services.schema_builder import plan_to_site
from app.services.style_tokens import make_style_tokens, tracking_ramp
from app.services.theme import build_theme

MOODS: tuple[str, ...] = get_args(BrandMood)
INDUSTRIES: tuple[str, ...] = get_args(IndustryCategory)

# Enough distinct brand names to see the seeded rotation spread. Real-shaped,
# because the seed is an md5 of the string and short/similar names cluster less
# than you would guess.
BRANDS = (
    "Acme Studio",
    "Bluebird Collective",
    "Northwind Partners",
    "Kopitiam Jaya",
    "Vertex Labs",
    "Lumen Health",
    "Ridgeline Outfitters",
    "Paper Lantern",
    "Sable & Finch",
    "Quarry House",
    "Meridian Foundry",
    "Tessellate",
)


def _plan(name: str, mood: str, industry: str) -> SitePlan:
    return SitePlan(
        site_name=name,
        brand_mood=mood,
        industry_category=industry,
        pages=[
            PagePlan(
                page_type="home",
                slug="home",
                title="Home",
                is_homepage=True,
                seo_title=f"{name} — Home",
                seo_description="A description that is long enough to pass the audit.",
                blocks=[
                    HeroBlock(headline="Build better", subheadline="We ship fast."),
                    AboutBlock(heading="About us", body="A team of people who care."),
                    FeaturesBlock(
                        heading="What you get",
                        items=[
                            FeatureItem(title="Speed", description="Very fast indeed."),
                            FeatureItem(title="Care", description="Handled with attention."),
                            FeatureItem(title="Proof", description="Numbers that hold up."),
                        ],
                    ),
                    ServicesBlock(
                        heading="Services",
                        items=[
                            ServiceItem(title="Design", description="Interfaces that convert."),
                            ServiceItem(title="Build", description="Code that lasts."),
                            ServiceItem(title="Run", description="We keep it alive."),
                        ],
                    ),
                    CtaBlock(
                        headline="Ready?", body="Let's talk.",
                        cta_label="Start", cta_href="/contact",
                    ),
                ],
            )
        ],
    )


def _build(name: str, mood: str, industry: str, *, scheme_slug: str | None = None):
    """Build one site end-to-end. `scheme_slug` pins a scheme by intercepting
    the selector — there is deliberately no API pin (selection is fit → seed →
    diversity), so a test that needs a specific scheme says so here."""
    async def run():
        return await plan_to_site(
            _plan(name, mood, industry),
            brand=BrandIdentity(name=name, mood=mood),
        )

    original = design_schemes.select_scheme
    if scheme_slug is not None:
        design_schemes.select_scheme = lambda **_kw: by_slug(scheme_slug)
    brain, language = settings.design_brain_enabled, settings.design_language_enabled
    settings.design_brain_enabled = False
    settings.design_language_enabled = False
    try:
        return asyncio.run(run())
    finally:
        design_schemes.select_scheme = original
        settings.design_brain_enabled = brain
        settings.design_language_enabled = language


def _walk(elements: list[BuilderElement]):
    for element in elements:
        yield element
        content = element.content
        if isinstance(content, list):
            yield from _walk(content)


def _sections(site) -> list[BuilderElement]:
    return list(site.pages[0].body_schema.elements)


class SchemesOffAreByteIdenticalTest(unittest.TestCase):
    """The kill switch must be a no-op, not merely "close".

    Every geometric decision now routes through the neutral scheme's deferring
    branch. If one of those branches drifts, this suite's other 1600 tests fail
    in ways that look unrelated — so the guarantee is asserted directly, on the
    serialized tree, which is what the CMS actually receives.
    """

    def test_disabled_theme_carries_no_scheme(self):
        settings.design_schemes_enabled = False
        theme = build_theme("#2563eb", mood="modern", font_seed="Acme", industry="saas")
        self.assertIsNone(theme.design_scheme)
        self.assertIs(design_schemes.for_theme(theme), NEUTRAL_SCHEME)

    def test_disabled_style_tokens_are_the_historical_literals(self):
        settings.design_schemes_enabled = False
        tokens = make_style_tokens(
            build_theme("#2563eb", mood="modern", font_seed="Acme", industry="saas")
        )
        # The exact values that were inline in make_style_tokens before the
        # TypeRamp/SpacingScale refactor, including dict key ORDER — these are
        # serialized into BuilderElement.styles, so order is part of the output.
        self.assertEqual(
            list(tokens.card),
            ["padding", "borderRadius", "backgroundColor", "border", "gap", "boxShadow"],
        )
        self.assertEqual(tokens.card["padding"], "28px")
        self.assertEqual(tokens.card["gap"], "12px")
        self.assertEqual(tokens.subhead["fontSize"], "19px")
        self.assertEqual(tokens.subhead["maxWidth"], "640px")
        self.assertEqual(tokens.body["fontSize"], "16px")
        self.assertEqual(tokens.heading_mobile, {"fontSize": "28px"})
        self.assertEqual(tokens.eyebrow["fontSize"], "13px")
        self.assertEqual(tokens.eyebrow["letterSpacing"], "0.14em")
        for heading, tracking in (
            (tokens.heading_xl, "-0.02em"),
            (tokens.heading_lg, "-0.015em"),
            (tokens.heading_md, "-0.01em"),
        ):
            self.assertEqual(heading["fontWeight"], 700)
            self.assertEqual(heading["letterSpacing"], tracking)
        self.assertEqual(tokens.primary_button_styles["paddingTop"], "13px")
        self.assertEqual(tokens.primary_button_styles["paddingLeft"], "26px")
        self.assertEqual(tokens.secondary_button_styles["paddingTop"], "12px")
        self.assertEqual(tokens.secondary_button_styles["paddingLeft"], "22px")

    def test_tracking_ramp_reproduces_the_historical_trio(self):
        # -0.02 / -0.015 / -0.01 is exactly 1.0 / 0.75 / 0.5 of the first, which
        # is why one scheme field can drive all three tiers without flattening
        # the graded ramp the old literals encoded.
        self.assertEqual(tracking_ramp("-0.02em"), ("-0.02em", "-0.015em", "-0.01em"))

    def test_unparseable_tracking_degrades_instead_of_raising(self):
        self.assertEqual(tracking_ramp("normal"), ("normal", "normal", "normal"))

    def test_disabled_build_records_no_scheme_on_the_manifest(self):
        settings.design_schemes_enabled = False
        site = _build("Acme Studio", "modern", "saas")
        self.assertEqual((site.design_manifest or {}).get("design_scheme"), "")
        areas = {d["area"] for d in (site.design_manifest or {}).get("decisions", [])}
        self.assertNotIn("scheme", areas)


class GateVocabularyTest(unittest.TestCase):
    """A gate written in the wrong vocabulary is a silent off switch, not a gate.

    Exactly the `profile-centered` failure recorded in CLAUDE.md, where a
    catalog entry declared moods that were not BrandMoods and was unreachable
    for its entire lifetime while its own test passed by fabricating them.
    """

    def test_every_mood_gate_is_a_real_brand_mood(self):
        for scheme in DESIGN_SCHEMES:
            for mood in scheme.moods:
                self.assertIn(mood, MOODS, f"{scheme.slug} gates on unknown mood {mood!r}")

    def test_every_industry_gate_is_a_real_industry(self):
        for scheme in DESIGN_SCHEMES:
            for field in ("industries", "industry_affinity"):
                for industry in getattr(scheme, field):
                    self.assertIn(
                        industry,
                        INDUSTRIES,
                        f"{scheme.slug}.{field} names unknown industry {industry!r}",
                    )

    def test_every_scheme_is_reachable(self):
        reachable: set[str] = set()
        for mood in MOODS:
            for industry in INDUSTRIES:
                reachable.update(candidates_for(mood, industry))
        for scheme in DESIGN_SCHEMES:
            self.assertIn(scheme.slug, reachable, f"{scheme.slug} is unreachable")

    def test_slugs_are_unique_and_not_the_neutral_sentinel(self):
        slugs = [s.slug for s in DESIGN_SCHEMES]
        self.assertEqual(len(slugs), len(set(slugs)))
        self.assertNotIn(NEUTRAL_SCHEME.slug, slugs)

    def test_chrome_affinities_name_real_archetypes(self):
        from app.models.design_manifest import FooterArchetype, HeaderArchetype

        headers, footers = get_args(HeaderArchetype), get_args(FooterArchetype)
        for scheme in DESIGN_SCHEMES:
            for name in scheme.header_affinity:
                self.assertIn(name, headers, f"{scheme.slug}: bad header {name!r}")
            for name in scheme.footer_affinity:
                self.assertIn(name, footers, f"{scheme.slug}: bad footer {name!r}")

    def test_divider_shapes_are_ones_the_renderers_know(self):
        # webtree-public/lib/sectionDivider.ts has exactly four. A fifth needs
        # renderer changes in three repos, so it cannot be introduced here.
        known = {"slant", "curve", "wave", "peak", None, "inherit"}
        for scheme in DESIGN_SCHEMES:
            self.assertIn(scheme.divider_shape, known, scheme.slug)
            if scheme.divider_height is not None:
                self.assertTrue(8 <= scheme.divider_height <= 400, scheme.slug)

    def test_scales_stay_inside_the_theme_token_bounds(self):
        # ThemeTokens validates these; a scheme that violates one would raise at
        # generation time on whichever brand happened to hash to it, which is a
        # bug that only shows up for some customers.
        for scheme in DESIGN_SCHEMES:
            self.assertTrue(320 <= scheme.container_max_width <= 1920, scheme.slug)
            if scheme.type_scale_ratio is not None:
                self.assertTrue(1.1 <= scheme.type_scale_ratio <= 1.6, scheme.slug)
            self.assertTrue(0.0 < scheme.padding_scale <= 2.0, scheme.slug)
            self.assertTrue(0.0 < scheme.card_padding_scale <= 2.0, scheme.slug)
            self.assertTrue(0.0 <= scheme.radius_scale <= 2.5, scheme.slug)


class CoverageTest(unittest.TestCase):
    """Every cell needs several schemes, or the feature has not fixed anything
    for the brands that land in it."""

    def test_every_mood_industry_cell_offers_at_least_three(self):
        for mood in MOODS:
            for industry in INDUSTRIES:
                with self.subTest(mood=mood, industry=industry):
                    self.assertGreaterEqual(
                        len(candidates_for(mood, industry)), 3,
                        f"({mood}, {industry}) has too few schemes",
                    )

    def test_candidates_never_repeat(self):
        for mood in MOODS:
            for industry in INDUSTRIES:
                slugs = candidates_for(mood, industry)
                self.assertEqual(len(slugs), len(set(slugs)))

    def test_a_hard_industry_gate_excludes_other_industries(self):
        # playroom is childcare/nonprofit only; it must not surface on a law firm
        # even though its moods admit one.
        self.assertNotIn("playroom", candidates_for("friendly", "professional-services"))
        self.assertIn("playroom", candidates_for("friendly", "childcare"))

    def test_industry_briefs_lead_their_own_cell(self):
        # A scheme written FOR an industry outranks the neutral ones there —
        # otherwise the researched brief is just another lottery ticket. Which
        # brief leads among several is declaration order, i.e. taste, so the
        # assertion is on the rank, not on one slug.
        for mood, industry in (
            ("friendly", "childcare"),
            ("playful", "childcare"),
            ("friendly", "ecommerce"),
            ("editorial", "agency"),
        ):
            leader = by_slug(candidates_for(mood, industry)[0])
            with self.subTest(mood=mood, industry=industry):
                self.assertTrue(
                    industry in leader.industries or industry in leader.industry_affinity,
                    f"({mood}, {industry}) leads with the unrelated {leader.slug}",
                )


class SelectionTest(unittest.TestCase):
    def test_selection_is_idempotent_per_brand(self):
        # md5, not hash() — the pick must survive an interpreter restart or a
        # regeneration silently redesigns the site.
        for brand in BRANDS[:4]:
            first = select_scheme(seed=brand, mood="modern", industry="saas")
            second = select_scheme(seed=brand, mood="modern", industry="saas")
            self.assertEqual(first.slug, second.slug)

    def test_different_brands_spread_across_the_cell(self):
        for mood in MOODS:
            for industry in INDUSTRIES:
                picked = {
                    select_scheme(seed=b, mood=mood, industry=industry).slug
                    for b in BRANDS
                }
                with self.subTest(mood=mood, industry=industry):
                    self.assertGreaterEqual(
                        len(picked), 3,
                        f"({mood}, {industry}) collapsed onto {picked}",
                    )

    def test_selection_stays_inside_the_fit_list(self):
        for mood in MOODS:
            for industry in INDUSTRIES:
                allowed = set(candidates_for(mood, industry))
                for brand in BRANDS:
                    self.assertIn(
                        select_scheme(seed=brand, mood=mood, industry=industry).slug,
                        allowed,
                    )

    def test_diversity_steers_off_a_recent_pick(self):
        chosen = select_scheme(seed="Acme Studio", mood="modern", industry="saas")
        steered = select_scheme(
            seed="Acme Studio", mood="modern", industry="saas", avoid={chosen.slug}
        )
        self.assertNotEqual(steered.slug, chosen.slug)

    def test_an_unknown_slug_is_a_no_op_not_an_error(self):
        # Same contract as a hallucinated palette slug: ignored, never raised.
        picked = select_scheme(
            seed="Acme", mood="modern", industry="saas", choice="not-a-scheme"
        )
        self.assertIn(picked.slug, candidates_for("modern", "saas"))
        self.assertIs(by_slug("not-a-scheme"), NEUTRAL_SCHEME)
        self.assertIs(by_slug(None), NEUTRAL_SCHEME)


class RendererConstraintTest(unittest.TestCase):
    """Rules the renderers impose that Python cannot see."""

    def setUp(self):
        settings.design_schemes_enabled = True

    def test_density_touches_padding_and_nothing_else(self):
        # webtree-public/lib/responsiveRuntime.ts overrides `gap` on 17 node
        # names and card `minHeight` on 4, with !important, at desktop and
        # tablet. A value the density pass wrote for either would lose on the
        # published site while winning in the builder and the preview — a
        # three-way divergence. The catalogue's own gaps are fine and stay; the
        # rule is that the PASS must not touch them, so the assertion compares
        # the tree either side of the pass rather than looking for absence.
        from app.services.schema_builder import apply_density_scale

        site = _build("Airy Brand", "luxury", "other", scheme_slug="dense-technical")
        theme_tokens = build_theme(
            "#2563eb", mood="luxury", font_seed="Airy Brand", industry="other",
            scheme_choice="airy-luxe",
        )
        sections = _sections(site)

        def snapshot(elements):
            out = {}
            for i, element in enumerate(_walk(elements)):
                responsive = getattr(element, "responsiveStyles", None)
                for label, styles in [("base", element.styles)] + [
                    (device, getattr(responsive, device, None) if responsive else None)
                    for device in ("mobile", "tablet")
                ]:
                    if isinstance(styles, dict):
                        out[(i, label)] = (styles.get("gap"), styles.get("minHeight"))
            return out

        before = snapshot(sections)
        apply_density_scale(sections, theme_tokens)
        self.assertEqual(before, snapshot(sections), "density pass moved gap/minHeight")
        # …and it did do its actual job.
        self.assertTrue(
            any(s.styles.get("paddingTop") for s in sections),
            "density pass left every section without vertical padding",
        )

    def test_pinned_name_mirror_matches_the_renderer(self):
        # The two sets are a hand-mirror of TS constants in a sibling repo.
        # Skipped rather than failed when that repo is absent, since the backend
        # must remain testable on its own.
        source = pathlib.Path(
            __file__
        ).resolve().parents[3] / "webtree-public" / "lib" / "responsiveRuntime.ts"
        if not source.exists():
            self.skipTest("webtree-public not checked out beside this repo")
        text = source.read_text()
        names: set[str] = set()
        for block in re.findall(
            r"GENERATED_(?:SECTION_SHELL_NAMES|GRID_NAMES)\s*=\s*new Set\(\[(.*?)\]\)",
            text,
            re.S,
        ):
            names.update(re.findall(r"'([^']+)'", block))
        for block in re.findall(
            r"GENERATED_CARD_MIN_HEIGHTS[^=]*=\s*\{(.*?)\}", text, re.S
        ):
            names.update(re.findall(r"'([^']+)':", block))
        # The individually-named nodes are inline comparisons, not constants.
        names.update(re.findall(r"nodeName === '([^']+)'", text))
        self.assertTrue(names, "could not parse the renderer's pinned names")

        mirrored = RENDERER_PINNED_GAP_NAMES | RENDERER_PINNED_MIN_HEIGHT_NAMES
        self.assertEqual(
            mirrored,
            names,
            "the renderer's pinned-name table and design_schemes' mirror have drifted",
        )

    def test_no_numeric_css_length_reaches_the_tree(self):
        # Vue's :style assigns raw values, so `width: 24` becomes the invalid
        # string "24" and is dropped, while React (builder + preview) appends
        # "px". A numeric length is therefore a silent divergence between the
        # published site and both editors. Only genuinely unitless properties
        # may be numbers.
        unitless = {
            "fontWeight", "lineHeight", "opacity", "zIndex", "flex", "flexGrow",
            "flexShrink", "order", "aspectRatio", "WebkitLineClamp", "lineClamp",
            "animationIterationCount", "columnCount", "flexBasis", "zoom",
        }
        for slug in ("airy-luxe", "dense-technical", "playroom", "studio-mono"):
            site = _build(f"Brand {slug}", "modern", "saas", scheme_slug=slug)
            for element in _walk(_sections(site)):
                for key, value in (element.styles or {}).items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        self.assertIn(key, unitless, f"{slug}: numeric {key}={value}")

    def test_texture_and_gradient_never_share_a_section(self):
        # SectionBlock.vue deletes a gradient outright when backgroundTexture is
        # set, replacing it with var(--builder-color-primary). A scheme that
        # paired the two would render as a flat brand block on the live site and
        # as a gradient in both editors.
        for slug in ("playroom", "glass-lab", "bold-block", "editorial-broadsheet"):
            site = _build(f"Brand {slug}", "playful", "childcare", scheme_slug=slug)
            for element in _walk(_sections(site)):
                texture = getattr(element, "backgroundTexture", None)
                if not texture or texture == "flat":
                    continue
                styles = element.styles or {}
                for key in ("background", "backgroundImage"):
                    value = styles.get(key)
                    if isinstance(value, str):
                        self.assertNotIn(
                            "linear-gradient", value,
                            f"{slug}: {element.name} pairs {texture} with a gradient",
                        )


class SchemesOnDivergeTest(unittest.TestCase):
    """The point of the feature: same cell, different designs."""

    def setUp(self):
        settings.design_schemes_enabled = True

    def _fingerprint(self, site) -> tuple:
        theme_scheme = (site.design_manifest or {}).get("design_scheme")
        styles = site.builder_styles or {}
        sections = _sections(site)
        return (
            theme_scheme,
            styles.get("buttons", {}).get("radius"),
            styles.get("page", {}).get("maxWidth"),
            styles.get("backgroundTexture"),
            styles.get("motion", {}).get("intensity"),
            (site.design_manifest or {}).get("header_archetype"),
            (site.design_manifest or {}).get("footer_archetype"),
            tuple(s.styles.get("paddingTop") for s in sections),
            tuple((s.name or "") for s in sections),
        )

    def test_same_cell_different_brands_produce_different_designs(self):
        seen = {}
        for brand in BRANDS[:6]:
            site = _build(brand, "modern", "saas")
            seen[brand] = self._fingerprint(site)
        self.assertGreaterEqual(
            len({f[0] for f in seen.values()}), 3,
            f"schemes collapsed: {[f[0] for f in seen.values()]}",
        )
        self.assertGreaterEqual(
            len(set(seen.values())), 3,
            "distinct schemes produced indistinguishable pages",
        )

    def test_a_scheme_moves_at_least_four_independent_axes(self):
        # A scheme that only recolours is the problem, not the fix.
        airy = _build("Same Brand", "luxury", "other", scheme_slug="airy-luxe")
        dense = _build("Same Brand", "luxury", "other", scheme_slug="dense-technical")
        moved = sum(a != b for a, b in zip(self._fingerprint(airy), self._fingerprint(dense)))
        self.assertGreaterEqual(moved, 4, "schemes barely differ")

    def test_the_manifest_explains_the_choice(self):
        site = _build("Acme Studio", "modern", "saas")
        manifest = site.design_manifest or {}
        self.assertTrue(manifest.get("design_scheme"))
        decision = next(
            (d for d in manifest.get("decisions", []) if d["area"] == "scheme"), None
        )
        self.assertIsNotNone(decision, "no scheme decision was recorded")
        self.assertEqual(decision["choice"], manifest["design_scheme"])
        self.assertIn("fit list", decision["rationale"])
        # It rides into the CMS on the same flexible-JSON channel as the rest of
        # the manifest, so a builder round-trip keeps it.
        self.assertEqual(site.builder_styles.get("designManifest"), manifest)

    def test_the_scheme_slug_stays_out_of_the_wire_theme(self):
        # design_scheme is internal, exactly like palette_slug: the CMS payload
        # shape must not change, or every consumer needs a migration.
        settings.design_schemes_enabled = True
        theme = build_theme("#2563eb", mood="modern", font_seed="Acme", industry="saas")
        self.assertIsNotNone(theme.design_scheme)
        self.assertNotIn("design_scheme", json.dumps(theme.to_builder_styles()))
        self.assertNotIn("designScheme", json.dumps(theme.to_builder_styles()))

    def test_chrome_affinity_narrows_but_never_invents(self):
        from app.services.design_director import _HEADER_FIT, _apply_affinity

        for mood in MOODS:
            fit = _HEADER_FIT[mood]
            for scheme in DESIGN_SCHEMES:
                narrowed = _apply_affinity(list(fit), scheme.header_affinity)
                self.assertTrue(set(narrowed) <= set(fit), scheme.slug)
                self.assertGreaterEqual(len(narrowed), min(2, len(fit)), scheme.slug)


class ContrastSurvivesEverySchemeTest(unittest.TestCase):
    """No scheme may ship an unreadable or structurally broken page.

    The contrast and band passes all run downstream of the scheme's own passes
    and read final styles, so this is a check that the ordering still holds —
    not a second implementation of the rules.
    """

    def setUp(self):
        settings.design_schemes_enabled = True

    def test_every_scheme_builds_a_structurally_sound_page(self):
        for scheme in DESIGN_SCHEMES:
            mood = sorted(scheme.moods)[0] if scheme.moods else "modern"
            industry = sorted(scheme.industries)[0] if scheme.industries else "saas"
            with self.subTest(scheme=scheme.slug):
                site = _build(
                    f"Brand {scheme.slug}", mood, industry, scheme_slug=scheme.slug
                )
                sections = _sections(site)
                self.assertTrue(sections, f"{scheme.slug} produced no sections")
                # Band markers are what the floating pill's adaptive ink reads;
                # they are stamped after every styling pass, so a scheme pass
                # that ran too late would show up here first.
                for section in sections:
                    classes = section.classes or ""
                    self.assertTrue(
                        "wt-band-light" in classes or "wt-band-dark" in classes,
                        f"{scheme.slug}: {section.name} lost its band marker",
                    )
                # Exactly one h1 per page (seo.py's rule; audited, not enforced).
                h1s = [
                    e for e in _walk(sections)
                    if getattr(e, "htmlTag", None) == "h1"
                ]
                self.assertLessEqual(len(h1s), 1, f"{scheme.slug} has {len(h1s)} h1s")

    def test_accent_rules_inherit_their_ink_instead_of_pinning_a_hex(self):
        # An eyebrow's rule/underline is decoration, and `enforce_text_contrast`
        # only retargets `color`. A hex computed against the page background
        # would be a pale rule on a pale band wherever a section resolves to the
        # opposite one; currentColor gets corrected along with the text.
        for slug in ("swiss-quiet", "poster-display", "studio-mono", "consult-formal"):
            site = _build(f"Brand {slug}", "modern", "saas", scheme_slug=slug)
            found = False
            for element in _walk(_sections(site)):
                if (element.name or "") != "Eyebrow":
                    continue
                for key in ("borderLeft", "borderBottom"):
                    value = (element.styles or {}).get(key)
                    if value:
                        found = True
                        self.assertIn("currentColor", value, f"{slug}: {key}={value}")
            self.assertTrue(found, f"{slug} produced no ruled eyebrow to check")

    def test_a_flat_card_scheme_leaves_no_invisible_filled_card(self):
        # "flat" removes the fill AND the frame together. A card that kept its
        # fill but lost its border/shadow is the one combination that can vanish
        # against a matching band, so the treatment must never produce it.
        site = _build("Airy Brand", "luxury", "other", scheme_slug="airy-luxe")
        for element in _walk(_sections(site)):
            if not (element.name or "").lower().endswith("card"):
                continue
            styles = element.styles or {}
            if not any(k in styles for k in ("border", "boxShadow", "backgroundColor")):
                continue
            framed = styles.get("border") not in (None, "none") or styles.get(
                "boxShadow"
            ) not in (None, "none")
            filled = styles.get("backgroundColor") not in (None, "transparent")
            self.assertTrue(
                framed or not filled,
                f"{element.name} is filled but has no frame",
            )


if __name__ == "__main__":
    unittest.main()
