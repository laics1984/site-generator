"""Understanding a Facebook Page: offerings, facilities and stale posts.

The understanding call answers in unit numbers; these tests pin what the
deterministic side does with a reply — keep a grounded name, drop an invented
one, discard a misnumbered reply whole, take what is no longer true out of
the grounding text — and what runs with the call off.
"""

import asyncio
from datetime import date

import pytest

from app.config import settings
from app.models.content_blocks import ImageMetadata
from app.models.facebook import FacebookEvent, FacebookPage, FacebookPhoto, FacebookPost
from app.models.facebook_photos import FacebookPhotoReview
from app.services.facebook_knowledge import KnowledgeReply, NamedReading, UnitReading, understand_page
from app.services.facebook_source import build_raw_text, homepage_sections_for, to_source_content

_TODAY = date(2026, 10, 19)
_ABOUT = ("Little Steps runs a Nursery Programme for toddlers and a Preschool Programme "
          "for ages four to six, with a garden and an outdoor playground.")


class _FakeClient:
    def __init__(self, reply: KnowledgeReply):
        self.reply = reply
        self.calls = []

    async def chat_json(self, **kwargs):
        self.calls.append(kwargs)
        return self.reply


@pytest.fixture(autouse=True)
def _on():
    original = settings.facebook_knowledge_enabled
    settings.facebook_knowledge_enabled = True
    yield
    settings.facebook_knowledge_enabled = original


def _photo(name: str) -> FacebookPhoto:
    return FacebookPhoto(url=f"https://cdn.example/{name}.jpg")


def _page(**kwargs) -> FacebookPage:
    fields = dict(
        name="Little Steps", canonical_url="https://www.facebook.com/littlesteps",
        category="Kindergarten", about=_ABOUT, fetched_via="render",
        posts=[
            FacebookPost(post_id="1", created_time="2026-10-10T08:00:00+0000",
                         message="Our Nursery Programme children planted seeds in the garden today.",
                         photos=[_photo("seeds"), _photo("garden")]),
            FacebookPost(post_id="2", created_time="2026-06-01T08:00:00+0000",
                         message="Enrol before 30 June and get 20% off the registration fee!",
                         photos=[_photo("promo")]),
            FacebookPost(post_id="3", created_time="2026-05-01T08:00:00+0000",
                         message="Preschool Programme reading time with our teachers.",
                         photos=[_photo("reading")]),
        ],
    )
    fields.update(kwargs)
    return FacebookPage(**fields)


def _understand(page: FacebookPage, reply: KnowledgeReply | None):
    source = to_source_content(page)
    client = _FakeClient(reply) if reply is not None else None
    knowledge = asyncio.run(understand_page(page, source, client=client, today=_TODAY))
    return source, knowledge, client


def _reply(**kwargs) -> KnowledgeReply:
    fields = dict(
        units=[UnitReading(unit=0), UnitReading(unit=1, category="outdoor_learning"),
               UnitReading(unit=2, temporal="time_bound"), UnitReading(unit=3, category="literacy")],
        offerings=[
            NamedReading(name="Nursery Programme", kind="programme", units=[0, 1]),
            NamedReading(name="Preschool Programme", kind="programme", units=[0, 3]),
            NamedReading(name="Montessori Programme", kind="programme", units=[0]),
        ],
        facilities=[NamedReading(name="outdoor playground", units=[0])],
    )
    fields.update(kwargs)
    return KnowledgeReply(**fields)


def test_a_grounded_offering_is_kept_and_an_invented_one_dropped():
    _, knowledge, _ = _understand(_page(), _reply())
    assert [o.name for o in knowledge.offerings] == ["Nursery Programme", "Preschool Programme"]
    assert knowledge.structured_by == "llm"
    nursery = knowledge.offerings[0]
    assert nursery.kind == "programme"
    # Its body is its own units' words, verbatim — nothing the call wrote.
    assert nursery.body == _ABOUT + "\n\n" + "Our Nursery Programme children planted seeds in the garden today."
    assert [f.name for f in knowledge.facilities] == ["outdoor playground"]


def test_evidence_shared_by_two_offerings_is_not_either_ones_own():
    _, knowledge, _ = _understand(_page(), _reply())
    nursery, preschool = knowledge.offerings
    assert nursery.own_chars == len("Our Nursery Programme children planted seeds in the garden today.")
    assert preschool.own_chars == len("Preschool Programme reading time with our teachers.")


def test_an_offering_takes_the_photos_of_its_own_posts():
    _, knowledge, _ = _understand(_page(), _reply())
    nursery, preschool = knowledge.offerings
    assert set(nursery.photo_ids) == {"https://cdn.example/seeds.jpg", "https://cdn.example/garden.jpg"}
    assert preschool.photo_ids == ["https://cdn.example/reading.jpg"]


def test_a_reply_naming_a_unit_out_of_range_is_discarded_whole():
    reply = _reply(offerings=[NamedReading(name="Nursery Programme", units=[0, 9])])
    _, knowledge, _ = _understand(_page(), reply)
    assert knowledge.offerings == [] and knowledge.structured_by == "none"


def test_a_stale_promotion_leaves_the_grounding_text_and_its_photo_loses_the_caption():
    source, knowledge, _ = _understand(_page(), _reply())
    assert "20% off" not in source.raw_text
    # A lasting post older than the window stays: an everyday activity is still true.
    assert "Preschool Programme reading time" in source.raw_text
    promo = next(meta for meta in source.image_metadata if meta.url.endswith("promo.jpg"))
    assert promo.caption == "" and "20%" not in promo.alt
    assert [item.date for item in knowledge.stale] == ["2026-06-01"]


def test_a_recent_time_bound_post_is_still_true():
    page = _page()
    page.posts[1].created_time = "2026-10-15T08:00:00+0000"
    source, knowledge, _ = _understand(page, _reply())
    assert "20% off" in source.raw_text and knowledge.stale == []


def test_a_past_event_is_stale_with_the_call_off():
    settings.facebook_knowledge_enabled = False
    page = _page(events=[
        FacebookEvent(name="Sports Day", start_time="2026-07-12T09:00:00+0800"),
        FacebookEvent(name="Open Day", start_time="2026-11-02T09:00:00+0800"),
    ])
    source, knowledge, _ = _understand(page, None)
    assert knowledge.structured_by == "none" and knowledge.offerings == []
    assert [item.label for item in knowledge.stale] == ["Sports Day"]
    assert "Open Day" in source.raw_text and "Sports Day" not in source.raw_text


def test_a_photo_takes_its_posts_category_and_an_unknown_one_is_dropped():
    reply = _reply(units=[UnitReading(unit=1, category="outdoor_learning"),
                          UnitReading(unit=3, category="underwater_basket_weaving")])
    source, _, _ = _understand(_page(), reply)
    by_name = {meta.url.rsplit("/", 1)[-1]: meta for meta in source.image_metadata}
    assert by_name["seeds.jpg"].category == "outdoor_learning"
    assert by_name["reading.jpg"].category is None


def test_with_the_call_off_a_caption_hint_is_the_category():
    settings.facebook_knowledge_enabled = False
    page = _page()
    source = to_source_content(page)
    reading = next(meta for meta in source.image_metadata if meta.url.endswith("reading.jpg"))
    reading.facebook_photo_review = FacebookPhotoReview(method="metadata", classification=["literacy"])
    asyncio.run(understand_page(page, source, today=_TODAY))
    assert reading.category == "literacy"


def test_a_pasted_assistant_preamble_and_a_tag_wall_never_reach_the_digest():
    page = _page(posts=[FacebookPost(message="ChatGPT said: A little wood and leaves! #kids #play #fun")])
    digest = build_raw_text(page, today=_TODAY)
    assert "ChatGPT" not in digest and "#kids" not in digest
    assert "A little wood and leaves!" in digest


def test_features_are_earned_by_two_offerings_once_the_page_is_read():
    page = _page(products=None)
    assert "features" in homepage_sections_for(page, photo_count=0, offering_count=2)
    assert "features" not in homepage_sections_for(page, photo_count=0, offering_count=1)
    # Unread (call off or failed), three captioned posts still earn it.
    assert "features" in homepage_sections_for(page, photo_count=0, offering_count=None)


def test_the_units_are_numbered_evidence_and_the_categories_are_the_profiles():
    _, _, client = _understand(_page(), _reply())
    prompt = client.calls[0]
    assert prompt["user_prompt"].startswith("[0] About: Little Steps runs")
    assert "[2] Post 2026-06-01 (1 photo): Enrol before" in prompt["user_prompt"]
    assert "outdoor_learning" in prompt["system_prompt"]
