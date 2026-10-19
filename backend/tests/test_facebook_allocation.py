"""Where a Facebook Page's photos land: grouped galleries, offering cards, heroes.

Three allocation rules, each deterministic: the earned Gallery page groups its
photos by what their posts say they show; an offering card shows a photo from
its own posts; and among hero-eligible photos the measured best one leads.
"""

from app.models.content_blocks import (
    FeatureItem, FeaturesBlock, ImageMetadata, PagePlan, ServiceItem, ServicesBlock, SourceContent,
)
from app.models.page_knowledge import Offering, PageKnowledge
from app.routers.generate import _category_walls
from app.services.image_match import rank_candidates
from app.services.knowledge_cards import pin_knowledge_photos


def _photo(name, *, category=None, post=None, quality=0.5, context="facebook_childcare"):
    return ImageMetadata(url=f"https://cdn.example/{name}.jpg", width=1600, height=1000,
                         role="content", selection_context=context, category=category,
                         source_post_id=post, quality=quality)


def test_two_categories_of_four_photos_become_headed_walls_best_first():
    photos = [
        *[_photo(f"art{i}", category="creative_activity", post=f"a{i}", quality=i / 10) for i in range(4)],
        *[_photo(f"out{i}", category="outdoor_learning", post=f"o{i}") for i in range(5)],
        *[_photo(f"misc{i}") for i in range(3)],
    ]
    walls = _category_walls(photos)
    assert [w.heading for w in walls] == ["Outdoor learning", "Arts and crafts", "More photos"]
    arts = walls[1]
    assert arts.image_urls[0] == "https://cdn.example/art3.jpg"  # the best-measured first
    assert len(walls[2].image_urls) == 3


def test_one_category_is_not_a_grouping():
    photos = [_photo(f"art{i}", category="creative_activity", post=f"a{i}") for i in range(8)]
    assert _category_walls(photos) == []


def test_one_post_cannot_fill_a_group_alone():
    burst = [_photo(f"burst{i}", category="creative_activity", post="same") for i in range(6)]
    others = [_photo(f"out{i}", category="outdoor_learning", post=f"o{i}") for i in range(4)]
    walls = _category_walls(burst + others)
    arts = next(w for w in walls if w.heading == "Arts and crafts")
    assert len(arts.image_urls) == 4


def _source(*photos, offerings=()):
    return SourceContent(source_kind="facebook", source_ref="https://www.facebook.com/x",
                         raw_text="x", image_metadata=list(photos),
                         page_knowledge=PageKnowledge(offerings=list(offerings), structured_by="llm"))


def test_an_offering_card_takes_its_own_posts_photo_over_the_models_pick():
    garden, reading = _photo("garden"), _photo("reading")
    outdoor = Offering(name="Outdoor Learning", photo_ids=[garden.url])
    source = _source(reading, garden, offerings=[outdoor])
    page = PagePlan(slug="programmes", title="Programmes", page_type="services", blocks=[
        ServicesBlock(items=[
            ServiceItem(title="Outdoor learning", description="d", image_ref=0),
            ServiceItem(title="Something else", description="d", image_ref=0),
        ]),
    ])
    assert pin_knowledge_photos([page], {"programmes": source}) == 1
    items = page.blocks[0].items
    assert items[0].image_ref == 1  # garden, its own post's photo
    assert items[1].image_ref == 0  # not an offering: the model's choice stands


def test_two_cards_never_share_one_pinned_photo():
    garden = _photo("garden")
    a = Offering(name="Nursery", photo_ids=[garden.url])
    b = Offering(name="Preschool", photo_ids=[garden.url])
    source = _source(garden, offerings=[a, b])
    page = PagePlan(slug="", title="Home", page_type="home", blocks=[FeaturesBlock(items=[
        FeatureItem(title="Nursery", description="d"), FeatureItem(title="Preschool", description="d"),
    ])])
    assert pin_knowledge_photos([page], {"": source}) == 1


def test_a_sharper_hero_photo_leads_within_one_size_bucket():
    soft = ImageMetadata(url="https://cdn.example/soft.jpg", width=2000, height=1200, intent="hero",
                         role="content", quality=0.3)
    sharp = ImageMetadata(url="https://cdn.example/sharp.jpg", width=1800, height=1100, intent="hero",
                          role="content", quality=0.9)
    assert rank_candidates("kids", "hero", [soft, sharp]).chosen.url == sharp.url
