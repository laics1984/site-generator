"""Design-brain batching: one LLM call for the whole site, with per-page
index namespacing so one page's pick never lands on another page's section."""

import asyncio
import unittest
from typing import get_args

from app.config import settings
from app.models.brand import (
    INDUSTRY_HERO_HEIGHT,
    MOOD_HERO_HEIGHT,
    BrandMood,
)
from app.models.content_blocks import INDUSTRY_MOOD
from app.services.design_direction import resolve_hero_height
from app.services.design_brain import (
    SiteDesignRecipe,
    SiteSectionChoice,
    _page_blurb,
    generate_site_design_recipe,
)


def _block(kind: str):
    """A minimal, feasible block of `kind` — the design-brain menu is built from
    real content now, so a bare kind string is no longer enough."""
    from app.models.content_blocks import CtaBlock, FeatureItem, FeaturesBlock, HeroBlock

    if kind == "hero":
        return HeroBlock(headline="Ship faster", subheadline="Everything you need")
    if kind == "cta":
        return CtaBlock(headline="Ready to start?")
    if kind == "features":
        return FeaturesBlock(
            heading="Why us",
            items=[FeatureItem(title=f"Feature {i}", description="…") for i in range(3)],
        )
    raise AssertionError(f"no test block for {kind!r}")


def _page(*kinds: str) -> list:
    return [_block(k) for k in kinds]


class RecipeSlicingTest(unittest.TestCase):
    def test_recipe_for_slices_by_page_index(self):
        site = SiteDesignRecipe(
            sections=[
                SiteSectionChoice(page_index=0, section_index=0, template_id="hero-bento"),
                SiteSectionChoice(page_index=0, section_index=1, template_id="features-card-grid"),
                SiteSectionChoice(page_index=1, section_index=0, template_id="hero-editorial"),
            ]
        )
        p0 = site.recipe_for(0)
        self.assertEqual(p0.template_for(0), "hero-bento")
        self.assertEqual(p0.template_for(1), "features-card-grid")
        # Page 1's section 0 must NOT leak into page 0's section 0.
        self.assertEqual(site.recipe_for(1).template_for(0), "hero-editorial")
        # An unknown page → empty recipe → deterministic fallback downstream.
        self.assertEqual(site.recipe_for(2).sections, [])


class BatchedCallTest(unittest.TestCase):
    def test_one_call_covers_all_pages_and_namespaces_picks(self):
        calls: list[str] = []

        class FakeLLM:
            async def chat_json(self, *, user_prompt, schema, **_):
                calls.append(user_prompt)
                return schema(
                    sections=[
                        SiteSectionChoice(page_index=0, section_index=1, template_id="cta-banner"),
                        SiteSectionChoice(page_index=1, section_index=1, template_id="cta-minimal"),
                    ]
                )

        original = settings.design_brain_enabled
        settings.design_brain_enabled = True
        try:
            recipe = asyncio.run(
                generate_site_design_recipe(
                    mood="modern",
                    industry="saas",
                    pages=[_page("hero", "cta"), _page("hero", "cta")],
                    llm=FakeLLM(),
                )
            )
        finally:
            settings.design_brain_enabled = original

        self.assertEqual(len(calls), 1)  # ONE round-trip for the whole site
        self.assertIn("Page 0", calls[0])
        self.assertIn("Page 1", calls[0])
        # Heroes are pre-assigned by the hero director — never offered to the LLM.
        self.assertNotIn("hero", calls[0])
        self.assertEqual(recipe.recipe_for(0).template_for(1), "cta-banner")
        self.assertEqual(recipe.recipe_for(1).template_for(1), "cta-minimal")


class HeroExclusionTest(unittest.TestCase):
    def test_page_blurb_omits_hero_sections(self):
        # A page whose only multi-template section is the hero yields no blurb
        # at all (harmless: empty recipe → deterministic fallback downstream).
        self.assertIsNone(_page_blurb(0, _page("hero")))
        blurb = _page_blurb(0, _page("hero", "cta"))
        assert blurb is not None
        self.assertNotIn('"hero"', blurb)
        self.assertIn('"cta"', blurb)


class MenuHonestyTest(unittest.TestCase):
    """Every id the prompt offers must be one selection can actually land on.

    The prompt used to list every mood-allowed variant while `select_template`
    additionally required `is_feasible`, so an unfit pick was accepted by the
    menu and dropped by the selector — leaving that section on its deterministic
    default with nothing in the output to show for it."""

    MOODS = ("modern", "luxury", "friendly", "technical", "editorial", "playful")

    def _blocks(self):
        from app.models.content_blocks import (
            AboutBlock,
            CtaBlock,
            ProcessBlock,
            ProcessStep,
            TestimonialItem,
            TestimonialsBlock,
        )

        return [
            AboutBlock(heading="About us", body="We build things."),
            CtaBlock(headline="Ready?"),
            ProcessBlock(
                heading="How",
                steps=[
                    ProcessStep(title="One", description="x"),
                    ProcessStep(title="Two", description="y"),
                ],
            ),
            TestimonialsBlock(
                heading="Loved",
                items=[TestimonialItem(quote="Great", author="A")],
            ),
        ]

    def test_an_offered_id_is_always_honoured(self):
        from app.services.section_content import block_to_section, selectable_templates

        checked = 0
        for block in self._blocks():
            for mood in self.MOODS:
                for template in selectable_templates(block, mood=mood):
                    got = block_to_section(block, explicit_id=template["id"], mood=mood)
                    assert got is not None
                    self.assertEqual(
                        got[0]["id"],
                        template["id"],
                        f"{block.kind}/{mood}: offered {template['id']} but rendered "
                        f"{got[0]['id']}",
                    )
                    checked += 1
        self.assertGreater(checked, 0, "nothing was offered — the test proves nothing")

    def test_an_image_hungry_variant_is_withheld_until_there_is_an_image(self):
        """The clearest case: a text-only About makes four of its five templates
        infeasible, so the old menu offered five options of which exactly one
        could ever be honoured."""
        from app.models.content_blocks import AboutBlock
        from app.services.section_content import selectable_templates

        def ids(block) -> set[str]:
            return {t["id"] for t in selectable_templates(block, mood="modern")}

        text_only = ids(AboutBlock(heading="About us", body="We build things."))
        self.assertEqual(text_only, {"about-story"})

        with_image = ids(
            AboutBlock(
                heading="About us", body="We build things.", image_query="our workshop"
            )
        )
        self.assertIn("about-image-split", with_image)
        self.assertGreater(len(with_image), len(text_only))

    def test_the_photo_policy_only_governs_its_own_layout_family(self):
        """The rule is "don't re-select the same grid minus its photos".

        Its own rationale says the design brain "would otherwise happily
        re-select the text-only grid" — a same-family concern.
        `features-card-grid` really is `features-image-cards` with the pictures
        taken out, so it stays overruled. A bento's mixed-size tiles and an
        editorial list are different objects, and each family carries its own
        photo variant for when photos are what's wanted; overruling those
        killed four of six features layouts and three of six services layouts
        for no benefit."""
        from app.models.content_blocks import FeatureItem, FeaturesBlock
        from app.services.section_content import (
            _layout_family,
            _leads_with_photo,
            block_to_section,
            selectable_templates,
        )
        from app.services.template_filler import get_template

        block = FeaturesBlock(
            heading="Why us",
            items=[
                FeatureItem(title=f"F{i}", description="…", image_query="team at work")
                for i in range(3)
            ],
        )
        offered = {t["id"] for t in selectable_templates(block, mood="modern", industry="saas")}
        self.assertTrue(offered, "the photo rule should not empty the pool outright")

        # Every offered variant either leads with photos or belongs to a
        # different compositional family than the forced grid.
        forced_family = _layout_family("features-image-cards")
        for template_id in offered:
            template = get_template(template_id)
            self.assertTrue(
                _leads_with_photo(template) or template["layoutVariant"] != forced_family,
                f"{template_id} is a photo-less member of the forced family",
            )

        # The text bento and the editorial list are the point of the change.
        self.assertIn("features-bento", offered)
        self.assertIn("features-editorial", offered)
        # …while the same-family text grids stay overruled.
        for same_family in ("features-card-grid", "features-two-col"):
            self.assertNotIn(same_family, offered)
            got = block_to_section(
                block, explicit_id=same_family, mood="modern", industry="saas"
            )
            assert got is not None
            self.assertEqual(got[0]["id"], "features-image-cards", same_family)

    def test_a_hard_locked_section_offers_no_choice_at_all(self):
        """A founders band is decided by who the people ARE, not by imagery, so
        nothing competes and the section is never put to the model."""
        from app.models.content_blocks import TeamBlock, TeamMember
        from app.services.section_content import block_to_section, selectable_templates

        block = TeamBlock(
            heading="Our founders",
            members=[
                TeamMember(name="Ana", role="Co-founder"),
                TeamMember(name="Ben", role="Founder & CEO"),
            ],
        )
        self.assertEqual(selectable_templates(block, mood="modern"), [])
        self.assertIsNone(_page_blurb(0, [block], "modern"))
        got = block_to_section(block, explicit_id="team-grid", mood="modern")
        assert got is not None
        self.assertEqual(got[0]["id"], "team-founders")


class BentoVariantTest(unittest.TestCase):
    """The bento layouts are reachable, and reachable for the right reasons.

    `features-bento` (text tiles) is deliberately NOT reachable: the photo-topped
    card policy overrules it, which is the rule working as specified. Its photo
    sibling is reachable precisely because it satisfies that rule."""

    def _blocks(self):
        from app.models.content_blocks import (
            FeatureItem,
            FeaturesBlock,
            GalleryBlock,
            GalleryItem,
            ServiceItem,
            ServicesBlock,
            StatItem,
            StatsBlock,
        )

        return {
            "features-bento-photo": FeaturesBlock(
                heading="Why us",
                items=[FeatureItem(title=f"F{i}", description="d") for i in range(4)],
            ),
            "services-bento-photo": ServicesBlock(
                heading="Services",
                items=[ServiceItem(title=f"S{i}", description="d") for i in range(4)],
            ),
            "gallery-bento": GalleryBlock(
                heading="Gallery",
                items=[GalleryItem(image_query=f"q{i}") for i in range(5)],
            ),
            "stats-bento": StatsBlock(
                heading="Numbers",
                items=[StatItem(value=f"{i}0+", label=f"L{i}") for i in range(4)],
            ),
        }

    # Bento is gated on BOTH mood and industry, so a reachability check has to
    # supply both. "other" is the planner's default industry_category, so it is
    # the value an unclassified site actually renders with.
    BENTO_INDUSTRY = "other"

    def test_every_new_bento_is_reachable(self):
        from app.services.section_content import block_to_section, selectable_templates

        for template_id, block in self._blocks().items():
            offered = {
                t["id"]
                for t in selectable_templates(
                    block, mood="modern", industry=self.BENTO_INDUSTRY
                )
            }
            self.assertIn(template_id, offered, f"{template_id} is never offered")
            got = block_to_section(
                block,
                explicit_id=template_id,
                mood="modern",
                industry=self.BENTO_INDUSTRY,
            )
            assert got is not None
            self.assertEqual(got[0]["id"], template_id, f"{template_id} was discarded")

    def test_a_gated_bento_is_withheld_from_an_off_brief_industry(self):
        # Childcare runs its own pastel rhythm and leads with warmth, so the
        # modular tile grid is deliberately out of reach there.
        from app.services.section_content import selectable_templates

        block = self._blocks()["stats-bento"]
        offered = {
            t["id"] for t in selectable_templates(block, mood="modern", industry="childcare")
        }
        self.assertNotIn("stats-bento", offered)

    def test_every_bento_kind_now_has_a_real_choice(self):
        # stats and gallery each had exactly one template, so the design brain
        # could never be asked about them at all.
        from app.services.section_content import selectable_templates

        for block in self._blocks().values():
            self.assertGreaterEqual(
                len(
                    selectable_templates(
                        block, mood="modern", industry=self.BENTO_INDUSTRY
                    )
                ),
                2,
                block.kind,
            )

    def test_a_source_without_feature_photos_can_stay_on_the_text_bento(self):
        """The rule this encodes: if the source site had no feature imagery,
        a text layout is a legitimate outcome.

        `_item_image` still backfills a stock query from each card's title, so a
        photo layout stays *available* — it just no longer forces itself and
        fill a grid with stock images searched on phrases like "24/7 Support"."""
        from app.models.content_blocks import FeatureItem, FeaturesBlock
        from app.services.section_content import block_to_section, selectable_templates

        no_source_photos = FeaturesBlock(
            heading="Why us",
            items=[FeatureItem(title=f"F{i}", description="d") for i in range(4)],
        )
        offered = {
            t["id"]
            for t in selectable_templates(no_source_photos, mood="modern", industry="saas")
        }
        self.assertIn("features-bento", offered)
        got = block_to_section(
            no_source_photos, explicit_id="features-bento", mood="modern", industry="saas"
        )
        assert got is not None
        self.assertEqual(got[0]["id"], "features-bento")

    def test_real_source_photos_still_force_a_photo_layout(self):
        # The other half of the rule: when the source DID supply imagery, the
        # photo-topped policy is unchanged.
        from app.models.content_blocks import FeatureItem, FeaturesBlock
        from app.services.section_content import block_to_section

        with_source_photos = FeaturesBlock(
            heading="Why us",
            items=[
                FeatureItem(title=f"F{i}", description="d", image_query="team at work")
                for i in range(4)
            ],
        )
        got = block_to_section(
            with_source_photos, explicit_id="features-card-grid", mood="modern", industry="saas"
        )
        assert got is not None
        self.assertEqual(got[0]["id"], "features-image-cards")

    def test_the_childcare_programme_cards_are_never_displaced(self):
        """The friendly/playful services rule is more specific than the photo
        rule: those cards carry an age badge no other variant declares."""
        from app.models.content_blocks import ServiceItem, ServicesBlock
        from app.services.section_content import block_to_section, selectable_templates

        block = ServicesBlock(
            heading="Programmes",
            items=[
                ServiceItem(
                    title=f"S{i}",
                    description="d",
                    audience="Ages 2-4",
                    image_query="children in a classroom",
                )
                for i in range(4)
            ],
        )
        for mood in ("friendly", "playful"):
            self.assertEqual(selectable_templates(block, mood=mood), [], mood)
            got = block_to_section(block, explicit_id="services-bento-photo", mood=mood)
            assert got is not None
            self.assertEqual(got[0]["id"], "services-programs-age", mood)

    def test_bento_tiles_mix_photos_and_colour_fills(self):
        """A tile whose item has imagery gets a photo background; one without
        keeps its brand-dark fill. One template, both treatments."""
        import asyncio

        from app.services.template_filler import fill_template, get_template

        async def resolve(q):
            return f"https://cdn/{q.replace(' ', '-')}.jpg", "#334155"

        sample = {
            "heading": "Why us",
            "items": [
                {"title": "A", "description": "d", "image": {"query": "sunlit studio"}},
                {"title": "B", "description": "d"},  # no imagery at all
                {"title": "C", "description": "d", "image": {"src": "https://s/c.jpg"}},
            ],
        }
        el = asyncio.run(
            fill_template(
                get_template("features-bento-photo"), sample, resolve_image=resolve
            )
        )
        tiles = self._tiles(el)
        self.assertEqual(len(tiles), 3)
        backgrounds = [(t.styles or {}).get("backgroundImage") for t in tiles]
        self.assertIn("sunlit-studio", backgrounds[0] or "")
        self.assertIsNone(backgrounds[1], "tile without imagery should stay a colour fill")
        self.assertIn("s/c.jpg", backgrounds[2] or "")
        # Every tile keeps a fill, so the one white ink reads on all of them.
        for t in tiles:
            self.assertTrue((t.styles or {}).get("backgroundColor"), "tile lost its fill")

    def test_the_bento_gallery_is_click_to_enlarge(self):
        # Without the marker the tiles render but are never interactive, and
        # nothing errors — see CLAUDE.md's gallery lightbox contract.
        import asyncio

        from app.services.template_filler import fill_template, get_template

        async def resolve(q):
            return f"https://cdn/{q}.jpg", None

        template = get_template("gallery-bento")
        el = asyncio.run(
            fill_template(template, template["sampleContent"], resolve_image=resolve)
        )
        groups = self._find(el, lambda n: getattr(n, "lightbox", None))
        self.assertTrue(groups, "gallery-bento lost its lightbox marker")
        # The lightbox binds to descendant IMAGE elements, so CSS backgrounds
        # would render but never enlarge.
        images = self._find(groups[0], lambda n: n.type == "image")
        self.assertGreaterEqual(len(images), 3)

    def _tiles(self, el):
        from app.services.template_filler import get_template  # noqa: F401

        found = self._find(el, lambda n: n.name == "Bento Tile")
        return found

    def _find(self, el, pred):
        out = []
        if pred(el):
            out.append(el)
        if isinstance(el.content, list):
            for child in el.content:
                if hasattr(child, "content") or hasattr(child, "type"):
                    out.extend(self._find(child, pred))
        return out


class NoOpTest(unittest.TestCase):
    def test_disabled_returns_empty_without_calling_llm(self):
        class BoomLLM:
            async def chat_json(self, *_, **__):
                raise AssertionError("LLM must not be called when design brain is off")

        original = settings.design_brain_enabled
        settings.design_brain_enabled = False
        try:
            recipe = asyncio.run(
                generate_site_design_recipe(
                    mood="modern",
                    industry="saas",
                    pages=[_page("hero", "features")],
                    llm=BoomLLM(),
                )
            )
        finally:
            settings.design_brain_enabled = original
        self.assertEqual(recipe.sections, [])


class HeroHeightPrecedenceTest(unittest.TestCase):
    """explicit user pick → LLM pick → industry default → mood default → full."""

    def test_explicit_user_pick_beats_everything(self):
        self.assertEqual(
            resolve_hero_height("banded", "full", mood="luxury", industry="restaurant"),
            "banded",
        )

    def test_llm_pick_wins_when_the_user_chose_auto(self):
        self.assertEqual(
            resolve_hero_height(None, "banded", mood="luxury", industry="restaurant"),
            "banded",
        )

    def test_industry_default_beats_mood(self):
        # A "modern" restaurant still leads with photography.
        self.assertEqual(
            resolve_hero_height(None, None, mood="modern", industry="restaurant"), "full"
        )
        # …and a "friendly" SaaS still gets to its copy.
        self.assertEqual(
            resolve_hero_height(None, None, mood="friendly", industry="saas"), "banded"
        )

    def test_mood_default_applies_when_the_industry_has_no_lean(self):
        self.assertEqual(
            resolve_hero_height(None, None, mood="technical", industry="other"), "banded"
        )
        self.assertEqual(
            resolve_hero_height(None, None, mood="luxury", industry="other"), "full"
        )

    def test_result_is_always_determined_with_nothing_known(self):
        # A disabled or failed pass must never leave the theme unset.
        self.assertEqual(
            resolve_hero_height(None, None, mood=None, industry=None), "full"
        )

    def test_industry_map_only_uses_real_categories(self):
        # Keys are IndustryCategory values; a typo'd slug would silently never
        # match and the industry would quietly fall through to its mood.
        self.assertTrue(set(INDUSTRY_HERO_HEIGHT) <= set(INDUSTRY_MOOD))

    def test_every_mood_has_a_default(self):
        self.assertEqual(set(MOOD_HERO_HEIGHT), set(get_args(BrandMood)))


if __name__ == "__main__":
    unittest.main()
