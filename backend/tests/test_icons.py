"""Tile icons: the glyph set, the keyword matcher, and the all-or-nothing rule.

The design constraint these pin: a glyph must render identically in the builder
(React), webtree-public (Nuxt/Vue) and the mirrored preview port. It does so by
being an inline SVG data URI carried inside the document, so no renderer needs
an icon package and there is nothing to keep in lockstep.
"""

import asyncio
import typing
import unittest

from app.models.brand import BrandMood
from app.models.content_blocks import FeatureItem, FeaturesBlock, StatItem, StatsBlock
from app.models.industry import IndustryCategory
from app.services.icons import (
    GLYPHS,
    _KEYWORDS,
    icon_data_url,
    icon_name_for,
    icons_for_items,
)
from app.services.section_content import (
    _MAPPERS,
    _leads_with_photo,
    block_to_section,
    industry_allows,
    mood_allows,
)
from app.services.template_filler import fill_template, get_template, load_catalog


class GlyphSetTest(unittest.TestCase):
    def test_every_keyword_points_at_a_real_glyph(self):
        dangling = {k: v for k, v in _KEYWORDS.items() if v not in GLYPHS}
        self.assertEqual(dangling, {})

    def test_every_glyph_renders_a_well_formed_data_uri(self):
        for name in GLYPHS:
            url = icon_data_url(name)
            assert url is not None
            self.assertTrue(url.startswith("data:image/svg+xml,"), name)
            # URL-encoded, not base64: small and greppable in a pushed document.
            self.assertNotIn(";base64", url)
            self.assertIn("viewBox", url.replace("%20", " ").replace("%3D", "="))

    def test_an_unknown_name_yields_nothing_rather_than_a_broken_image(self):
        self.assertIsNone(icon_data_url("no-such-glyph"))

    def test_the_fill_colour_is_baked_in(self):
        # Colour comes from the theme at fill time, so one glyph definition
        # serves every brand without a per-palette variant.
        url = icon_data_url("clock", "#1e293b")
        assert url is not None
        self.assertIn("%231e293b", url)


class KeywordMatchTest(unittest.TestCase):
    def test_copy_resolves_to_a_sensible_glyph(self):
        cases = {
            "Bank-grade security": "shield",
            "Sustainable materials": "leaf",
            "Certified experts": "award",
            "Client satisfaction": "smile",
            "Branches nationwide": "map-pin",
        }
        for text, expected in cases.items():
            self.assertEqual(icon_name_for(text), expected, text)

    def test_a_longer_keyword_outranks_a_substring_of_another(self):
        # "Projects delivered" is work completed, not a shipping company:
        # "projects" (8) must beat "deliver" (7).
        self.assertEqual(icon_name_for("Projects delivered"), "check")

    def test_unmatched_copy_returns_none(self):
        self.assertIsNone(icon_name_for("Purple monkey dishwasher"))
        self.assertIsNone(icon_name_for(None, "", "   "))


class AllOrNothingTest(unittest.TestCase):
    """A grid where some tiles carry a glyph and others don't reads as a
    rendering fault, not a design — so the section commits to icons only when
    the whole set works."""

    def test_all_items_matching_yields_one_glyph_each(self):
        got = icons_for_items(
            [{"title": "24/7 support"}, {"title": "Bank-grade security"}, {"title": "Fast setup"}]
        )
        assert got is not None
        self.assertEqual(len(got), 3)

    def test_one_unmatched_item_drops_icons_from_the_whole_set(self):
        self.assertIsNone(
            icons_for_items(
                [{"title": "24/7 support"}, {"title": "Zzz"}, {"title": "Fast setup"}]
            )
        )

    def test_an_all_identical_set_is_dropped(self):
        # Carries no information and reads as a copy-paste error.
        self.assertIsNone(
            icons_for_items(
                [{"label": "Team members"}, {"label": "Our staff"}, {"label": "People"}]
            )
        )

    def test_partial_repetition_is_allowed(self):
        got = icons_for_items(
            [{"label": "Team members"}, {"label": "Our staff"}, {"label": "Years experience"}]
        )
        assert got is not None
        self.assertEqual(got.count("users"), 2)


class IconSlotWiringTest(unittest.TestCase):
    def _stats(self):
        return StatsBlock(
            heading="By the numbers",
            items=[
                StatItem(value="500+", label="Projects delivered"),
                StatItem(value="12", label="Years experience"),
                StatItem(value="98%", label="Client satisfaction"),
                StatItem(value="40", label="Team members"),
            ],
        )

    def test_the_mapper_attaches_an_icon_to_every_item(self):
        content = _MAPPERS["stats"](self._stats())
        names = [i.get("icon", {}).get("icon") for i in content["items"]]
        self.assertTrue(all(names), names)
        self.assertGreaterEqual(len(set(names)), 2)

    def test_icons_render_as_image_elements_in_the_brand_ink(self):
        async def resolve(query: str):
            return "https://x/img.jpg", None

        content = _MAPPERS["stats"](self._stats())
        el = asyncio.run(
            fill_template(
                get_template("stats-bento"),
                content,
                resolve_image=resolve,
                theme={"primary": "#2563eb", "secondary": "#1e293b"},
            )
        )
        icons = self._find(el, lambda n: n.name == "Tile Icon")
        self.assertEqual(len(icons), 4)
        for icon in icons:
            self.assertEqual(icon.type, "image")
            src = icon.content.src
            self.assertTrue(src.startswith("data:image/svg+xml,"))
            # Inked from the theme, so it matches the tile's heading colour.
            self.assertIn("%231e293b", src)

    def test_an_unmatchable_section_renders_no_icon_nodes_at_all(self):
        async def resolve(query: str):
            return "https://x/img.jpg", None

        block = StatsBlock(
            heading="x",
            items=[StatItem(value="1", label="Zzz"), StatItem(value="2", label="Qqq")],
        )
        el = asyncio.run(
            fill_template(
                get_template("stats-bento"), _MAPPERS["stats"](block), resolve_image=resolve
            )
        )
        # An unbound $slot node is dropped, so the tile simply has no glyph.
        self.assertEqual(self._find(el, lambda n: n.name == "Tile Icon"), [])

    def test_an_icon_slot_does_not_make_a_text_grid_photo_leading(self):
        """The regression this guards: `icon` is an image-kind slot, so a naive
        "does any item slot hold an image" test would let a text-tile grid slip
        past the photo-topped card policy and be selected for a section whose
        cards are supposed to lead with a photograph."""
        self.assertFalse(_leads_with_photo(get_template("features-bento")))
        self.assertFalse(_leads_with_photo(get_template("stats-bento")))
        self.assertTrue(_leads_with_photo(get_template("features-image-cards")))
        self.assertTrue(_leads_with_photo(get_template("features-bento-photo")))

        # …and end to end: when the source DID supply imagery, an icon-bearing
        # text bento cannot pose as the photo layout.
        block = FeaturesBlock(
            heading="Why us",
            items=[
                FeatureItem(title=f"F{i}", description="d", image_query="team at work")
                for i in range(3)
            ],
        )
        got = block_to_section(
            block, explicit_id="features-card-grid", mood="modern", industry="saas"
        )
        assert got is not None
        self.assertEqual(got[0]["id"], "features-image-cards")

    def test_sample_icons_are_baked_uris_the_builder_can_render(self):
        """The builder's materializeTemplate binds an image slot from `src`
        alone — it has no name->glyph resolver and must never need one, or the
        glyph set would have to exist in three frameworks at once. So
        sampleContent (the builder's insert path) carries a finished data URI,
        while the generator's runtime path passes `{"icon": name}` and colours
        it from the theme."""
        for template_id in ("features-bento", "stats-bento"):
            template = get_template(template_id)
            assert template is not None
            items = template["sampleContent"]["items"]
            self.assertTrue(items, template_id)
            for item in items:
                icon = item.get("icon")
                self.assertIsInstance(icon, dict, f"{template_id}: sample item has no icon")
                src = icon.get("src", "")
                self.assertTrue(
                    src.startswith("data:image/svg+xml,"),
                    f"{template_id}: sample icon is not a baked data URI",
                )
                self.assertNotIn(
                    "icon",
                    {k for k in icon if k != "icon"} - {"src", "alt"},
                    f"{template_id}: unresolved icon name left in sampleContent",
                )

    def _find(self, el, pred):
        out = []
        if pred(el):
            out.append(el)
        if isinstance(el.content, list):
            for child in el.content:
                out.extend(self._find(child, pred))
        return out


class IndustryGateTest(unittest.TestCase):
    """`industries` is the sibling of `moods`, and exists because mood was the
    only lever: an industry-specific layout could previously only be gated by
    the mood its industry leans toward, which leaks to every brand sharing it."""

    def _template(self, **extra):
        return {"id": "x", "sectionType": "features", **extra}

    def test_an_ungated_template_suits_every_industry(self):
        for industry in ("restaurant", "saas", "childcare", None, "", "some free text"):
            self.assertTrue(industry_allows(self._template(), industry), repr(industry))

    def test_a_gated_template_is_confined_to_its_industries(self):
        t = self._template(industries=["childcare"])
        self.assertTrue(industry_allows(t, "childcare"))
        self.assertTrue(industry_allows(t, "Childcare"))  # case-insensitive
        for other in ("restaurant", "saas", "professional-services"):
            self.assertFalse(industry_allows(t, other), other)

    def test_an_unknown_industry_matches_nothing_gated(self):
        # The safe direction: a free-text industry never inherits a gated
        # layout it was not designed for.
        t = self._template(industries=["childcare"])
        self.assertFalse(industry_allows(t, "artisanal cheese"))
        self.assertFalse(industry_allows(t, None))

    def test_the_gate_is_wired_into_selection_not_merely_defined(self):
        """No catalog entry declares `industries` yet, so the gate would sit
        inert and untested. Gate a real template for the duration of the test
        and prove both selection paths honour it."""
        from unittest import mock

        from app.models.content_blocks import ProcessBlock, ProcessStep
        from app.services import section_content

        real = section_content.templates_for_type("process")
        gated = [
            {**t, "industries": ["childcare"]} if t["id"] == "process-steps" else t
            for t in real
        ]
        block = ProcessBlock(
            heading="How it works",
            steps=[ProcessStep(title=f"S{i}", description="d") for i in range(4)],
        )
        with mock.patch.object(
            section_content, "templates_for_type", return_value=gated
        ):
            childcare = {
                t["id"]
                for t in section_content.selectable_templates(
                    block, mood="friendly", industry="childcare"
                )
            }
            restaurant = {
                t["id"]
                for t in section_content.selectable_templates(
                    block, mood="friendly", industry="restaurant"
                )
            }
            picked = section_content.select_template(
                "process", section_content._MAPPERS["process"](block),
                explicit_id="process-steps", mood="friendly", industry="restaurant",
            )

        self.assertIn("process-steps", childcare)
        self.assertNotIn("process-steps", restaurant)
        # …and an explicit pick can't smuggle a gated template in either.
        assert picked is not None
        self.assertNotEqual(picked["id"], "process-steps")


class CatalogGateVocabularyTest(unittest.TestCase):
    """A gate written in a vocabulary the generator doesn't speak is a silent
    off switch, not a gate.

    `profile-centered` declared moods `classic`/`elegant`/`trustworthy`/`calm`
    and `profile-banner` added `bold`/`vibrant` — none are BrandMood values, so
    `mood_allows` could never match and the formal profile layout was
    unreachable on every site. Its own test passed because it fabricated those
    moods. These two checks are cheap and would have caught it at authoring
    time."""

    def _catalog(self):
        return load_catalog()["sections"]

    def test_every_declared_mood_is_a_real_brand_mood(self):
        valid = set(typing.get_args(BrandMood))
        offenders = {
            s["id"]: sorted(set(s["moods"]) - valid)
            for s in self._catalog()
            if s.get("moods") and set(s["moods"]) - valid
        }
        self.assertEqual(offenders, {})

    def test_every_declared_industry_is_a_real_industry_category(self):
        valid = set(typing.get_args(IndustryCategory))
        offenders = {
            s["id"]: sorted(set(s["industries"]) - valid)
            for s in self._catalog()
            if s.get("industries") and set(s["industries"]) - valid
        }
        self.assertEqual(offenders, {})

    def test_every_gated_template_is_reachable_by_some_brand(self):
        """The generalisation: a gated template nobody can reach is dead weight
        that still passes every test written about its internals.

        Both axes are checked together, and against the moods an industry can
        actually carry — `industry_locked_mood` pins childcare to `friendly`, so
        a template gated to `industries: [childcare]` with `moods: [modern]`
        would be unreachable in production while looking perfectly sensible in
        the catalog."""
        from app.models.content_blocks import industry_locked_mood

        moods = typing.get_args(BrandMood)
        industries = typing.get_args(IndustryCategory)
        for section in self._catalog():
            if not (section.get("moods") or section.get("industries")):
                continue
            reachable = [
                (m, i)
                for i in industries
                for m in ([industry_locked_mood(i)] if industry_locked_mood(i) else moods)
                if mood_allows(section, m) and industry_allows(section, i)
            ]
            self.assertTrue(
                reachable, f"{section['id']} is unreachable for every brand"
            )


class ForcedProgrammeCardsTest(unittest.TestCase):
    def _services(self, *, audience: str | None):
        from app.models.content_blocks import ServiceItem, ServicesBlock

        return ServicesBlock(
            heading="What we offer",
            items=[
                ServiceItem(
                    title=f"Item {i}",
                    description="d",
                    image_query="a photo",
                    audience=audience,
                )
                for i in range(3)
            ],
        )

    def test_badges_present_keeps_the_programme_variant(self):
        got = block_to_section(self._services(audience="Ages 2-4"), mood="friendly")
        assert got is not None
        self.assertEqual(got[0]["id"], "services-programs-age")

    def test_badges_absent_falls_back_to_the_image_cards(self):
        got = block_to_section(self._services(audience=None), mood="friendly")
        assert got is not None
        self.assertEqual(got[0]["id"], "services-image-cards")


if __name__ == "__main__":
    unittest.main()
