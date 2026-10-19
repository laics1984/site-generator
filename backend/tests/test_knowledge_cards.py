"""A card that names one of the business's facilities never shows a stock photo.

`services/knowledge_cards.py`: a card whose title names a facility the Page
states takes that facility's own photo when the page has one, and otherwise
shows no image — a stock playground under "Our playground" is a false claim
about the premises. Offerings keep the stock fallback: an activity can be
illustrated honestly.
"""

import json

from app.models.content_blocks import (
    AboutBlock, FeatureItem, FeaturesBlock, ImageMetadata, PagePlan, ServiceItem, ServicesBlock,
    SourceContent,
)
from app.models.page_knowledge import Facility, Offering, PageKnowledge
from app.services.image_refs import bind_image_refs
from app.services.knowledge_cards import named_in, pin_knowledge_photos, withhold_stock_photos
from app.services.section_content import block_to_section


def _photo(name, *, role="content"):
    return ImageMetadata(url=f"https://cdn.example/{name}.jpg", width=1600, height=1000,
                         role=role, selection_context="facebook_childcare")


def _source(*photos, facilities=(), offerings=()):
    return SourceContent(
        source_kind="facebook", source_ref="https://www.facebook.com/x", raw_text="x",
        image_metadata=list(photos),
        page_knowledge=PageKnowledge(
            facilities=list(facilities), offerings=list(offerings), structured_by="llm",
        ),
    )


def _home(*blocks):
    return PagePlan(slug="", title="Home", page_type="home", blocks=list(blocks))


def _run(pages, source_map):
    """The generate order: pin, bind, then withhold."""
    pin_knowledge_photos(pages, source_map)
    bind_image_refs(pages, source_map)
    return withhold_stock_photos(pages, source_map)


class TestNamedIn:
    def test_a_title_names_a_thing_whose_words_it_contains(self):
        assert named_in("Our Outdoor Playground", ["outdoor playground"]) == "outdoor playground"

    def test_words_not_substrings(self):
        assert named_in("Carpool service", ["pool"]) is None
        assert named_in("Swimming pool", ["pool"]) == "pool"

    def test_the_words_must_be_adjacent_and_in_order(self):
        assert named_in("Playground for outdoor games", ["outdoor playground"]) is None

    def test_the_longest_name_wins(self):
        assert named_in("Infant care room", ["care", "infant care room"]) == "infant care room"

    def test_nothing_named(self):
        assert named_in("Qualified teachers", ["outdoor playground"]) is None
        assert named_in("", ["outdoor playground"]) is None


class TestFacilityPhoto:
    def test_a_facility_card_shows_the_facilitys_own_photo(self):
        reading, yard = _photo("reading"), _photo("yard")
        source = _source(reading, yard, facilities=[
            Facility(name="outdoor playground", photo_ids=[yard.url]),
        ])
        card = FeatureItem(title="Our outdoor playground", description="d",
                           image_ref=0, image_query="kids playground")
        page = _home(FeaturesBlock(items=[card, FeatureItem(title="Teachers", description="d")]))
        assert _run([page], {"": source}) == 0
        assert card.image_url == yard.url  # its own photo, over the model's pick

    def test_an_about_section_naming_a_facility_shows_its_photo(self):
        yard = _photo("yard")
        source = _source(yard, facilities=[Facility(name="outdoor playground", photo_ids=[yard.url])])
        about = AboutBlock(heading="Our outdoor playground", body="b", image_query="playground")
        _run([_home(about)], {"": source})
        assert about.image_url == yard.url


class TestNoStockForAFacility:
    def _withheld_card(self):
        source = _source(_photo("reading"), facilities=[Facility(name="outdoor playground")])
        card = FeatureItem(title="Outdoor Playground", description="d", image_query="kids playground")
        other = FeatureItem(title="Qualified teachers", description="d", image_query="teacher")
        block = FeaturesBlock(items=[card, other])
        assert _run([_home(block)], {"": source}) == 1
        return block, card, other

    def test_a_facility_card_without_its_photo_loses_its_stock_query(self):
        _, card, other = self._withheld_card()
        assert card.image_query is None and card.source_photo_only
        assert other.image_query == "teacher" and not other.source_photo_only

    def test_the_card_maps_to_no_image_while_its_neighbour_keeps_one(self):
        block, _, _ = self._withheld_card()
        _, content = block_to_section(block)
        assert content["items"][0]["image"] is None  # not a title-searched stock photo
        assert content["items"][1]["image"]["query"] == "teacher"

    def test_a_services_card_too(self):
        source = _source(facilities=[Facility(name="swimming pool")])
        card = ServiceItem(title="Swimming pool", description="d")
        block = ServicesBlock(items=[card, ServiceItem(title="Lessons", description="d")])
        _run([_home(block)], {"": source})
        _, content = block_to_section(block)
        assert content["items"][0]["image"] is None

    def test_a_photo_the_binder_refuses_is_not_replaced_by_stock(self):
        # The facility's only photo is a headshot, which a services card may not
        # take: the ref is dropped at bind time, and the card shows nothing.
        face = _photo("face", role="portrait")
        source = _source(face, facilities=[Facility(name="music room", photo_ids=[face.url])])
        card = FeatureItem(title="Music room", description="d", image_query="music room")
        _run([_home(FeaturesBlock(items=[card, FeatureItem(title="x", description="d")]))],
             {"": source})
        assert card.image_url is None and card.image_query is None and card.source_photo_only

    def test_a_source_photo_the_model_bound_stands(self):
        hall = _photo("hall")
        source = _source(hall, facilities=[Facility(name="music room")])
        card = FeatureItem(title="Music room", description="d", image_ref=0)
        assert _run([_home(FeaturesBlock(items=[card, FeatureItem(title="x", description="d")]))],
                    {"": source}) == 0
        assert card.image_url == hall.url and not card.source_photo_only

    def test_an_about_section_falls_to_an_imageless_layout(self):
        source = _source(facilities=[Facility(name="outdoor playground")])
        about = AboutBlock(heading="Our outdoor playground", body="We play outside.",
                           image_query="playground")
        _run([_home(about)], {"": source})
        assert about.image_query is None
        template, content = block_to_section(about)
        assert content["image"] is None
        assert not any(slot["kind"] == "image" for slot in template["slots"])

    def test_an_offering_keeps_the_stock_fallback(self):
        source = _source(offerings=[Offering(name="Nursery")])
        card = FeatureItem(title="Nursery", description="d", image_query="toddlers")
        assert _run([_home(FeaturesBlock(items=[card, FeatureItem(title="x", description="d")]))],
                    {"": source}) == 0
        assert card.image_query == "toddlers"

    def test_a_source_without_knowledge_is_untouched(self):
        source = _source()
        source.page_knowledge = None
        card = FeatureItem(title="Outdoor playground", description="d", image_query="playground")
        assert withhold_stock_photos([_home(FeaturesBlock(items=[card]))], {"": source}) == 0
        assert card.image_query == "playground"


class TestAWithheldCardMakesTheSectionTextOnly:
    """The user's choice (2026-10-19): a section holding a withheld card goes
    text-only rather than showing a photo grid with a hole in it."""

    MOODS = (None, "modern", "luxury", "friendly", "technical", "editorial", "playful")

    def _block(self, cls, item, *, withheld):
        facility = item(title="Outdoor playground", description="d",
                        **({"source_photo_only": True} if withheld else {"image_query": "playground"}))
        return cls(items=[
            facility,
            item(title="Nursery", description="d", image_url="https://cdn.example/a.jpg"),
            item(title="Preschool", description="d", image_url="https://cdn.example/b.jpg"),
        ])

    def test_no_mood_lands_on_a_photo_layout(self):
        from app.services.section_content import _leads_with_photo

        for cls, item in ((FeaturesBlock, FeatureItem), (ServicesBlock, ServiceItem)):
            for mood in self.MOODS:
                template, _ = block_to_section(self._block(cls, item, withheld=True),
                                               mood=mood, variety_seed="Tadika Murni")
                assert not _leads_with_photo(template), (cls.__name__, mood, template["id"])

    def test_the_design_brain_cannot_pick_one_either(self):
        from app.services.section_content import _leads_with_photo, selectable_templates

        for cls, item in ((FeaturesBlock, FeatureItem), (ServicesBlock, ServiceItem)):
            block = self._block(cls, item, withheld=True)
            for mood in self.MOODS:
                assert not [t["id"] for t in selectable_templates(block, mood=mood)
                            if _leads_with_photo(t)]
                template, _ = block_to_section(block, explicit_id=f"{block.kind}-image-cards",
                                               mood=mood)
                assert not _leads_with_photo(template)

    def test_without_a_withheld_card_the_photo_grid_stands(self):
        for cls, item, kind in ((FeaturesBlock, FeatureItem, "features"),
                                (ServicesBlock, ServiceItem, "services")):
            template, _ = block_to_section(self._block(cls, item, withheld=False))
            assert template["id"] == f"{kind}-image-cards"


class TestTheMarkIsThePipelines:
    def test_the_model_is_never_asked_for_it(self):
        for model in (FeatureItem, ServiceItem):
            assert "source_photo_only" not in json.dumps(model.model_json_schema())

    def test_it_survives_a_plan_round_trip(self):
        card = FeatureItem(title="Pool", description="d", source_photo_only=True)
        assert FeatureItem.model_validate_json(card.model_dump_json()).source_photo_only
