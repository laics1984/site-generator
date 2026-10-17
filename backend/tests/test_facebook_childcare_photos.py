"""Facebook childcare photos stay suitable from scraping through resolution."""

import asyncio
from unittest.mock import AsyncMock, patch


from app.models.content_blocks import ImageMetadata, SourceContent
from app.config import settings
from app.models.facebook import FacebookPage
from app.models.facebook_photos import FacebookPhotoReview, PhotoScores
from app.services.facebook_graph import _parse_posts
from app.services.facebook_photo_selection import photo_rejection_reason, review_photo_library
from app.services.facebook_dom import load_view, read_timeline
from app.services.facebook_source import to_image_metadata, to_preview_payload
from app.services.image_match import rank_candidates
from app.services.image_refs import _unfit_for_kind
from app.services.image_vision import VisionAnnotation, annotate_image_pool
from app.services import image_vision
from app.services.media import ImageResolver
from app.services.prompts import CHILDCARE_FACEBOOK_PHOTO_PROMPT


def _page(**kwargs):
    return FacebookPage(name="Little School", canonical_url="https://www.facebook.com/little",
                        category="Kindergarten", about="A warm learning environment for children, " * 8,
                        **kwargs)


def _review(**kwargs):
    fields = dict(classification=["teacher_child_interaction"], recommended_sections=["hero", "about"],
                  decision="accept", orientation="landscape", candid=True, crop_safe=True,
                  negative_space=True, hero_candidate=True,
                  scores=PhotoScores(**{name: 8 for name in PhotoScores.model_fields}))
    fields.update(kwargs)
    return FacebookPhotoReview(**fields)


def _meta(url="https://cdn.example/photo.jpg", **kwargs):
    fields = dict(url=url, selection_context="facebook_childcare", width=1600, height=1000,
                  vision_kind="photo", vision_people=3, vision_caption="teacher helping children learn",
                  facebook_photo_review=_review())
    fields.update(kwargs)
    return ImageMetadata(**fields)


def test_graph_collects_multi_photo_originals_and_provenance():
    posts = _parse_posts({"data": [{"id": "123_456", "permalink_url": "https://facebook.com/p",
        "attachments": {"data": [{"type": "album", "subattachments": {"data": [
            {"type": "photo", "media": {"image": {"src": "https://cdn/a.jpg", "width": 1600, "height": 1000}}},
            {"type": "photo", "media": {"image": {"src": "https://cdn/b.jpg"}}},
            {"type": "share", "media": {"image": {"src": "https://cdn/advert.jpg"}}},
        ]}}]}}]}, 10)
    assert posts[0].post_id == "123_456"
    assert [photo.url for photo in posts[0].photo_sources] == ["https://cdn/a.jpg", "https://cdn/b.jpg"]
    assert posts[0].photos[0].width == 1600
    assert len(to_image_metadata(_page(posts=posts))) == 2


def test_render_uses_visible_photo_attachments_not_avatars_hidden_posts_or_remote_links():
    view = load_view('''
      <img src="https://x.fbcdn.net/avatar.jpg">
      <div role="article"><a href="/photo/?fbid=123"><img src="https://x.fbcdn.net/real.jpg"></a>
        <a href="https://other.test/photo/"><img src="https://x.fbcdn.net/ad.jpg"></a></div>
      <div aria-hidden="true"><div role="article"><a href="/photo/?fbid=456">
        <img src="https://x.fbcdn.net/hidden.jpg"></a></div></div>
    ''', "https://www.facebook.com/little")
    posts = read_timeline(view).posts
    assert [photo.url for post in posts for photo in post.photos] == ["https://x.fbcdn.net/real.jpg"]


def test_review_rejections_and_weak_heroes_cannot_be_bound_or_ranked():
    for meta in (_meta(facebook_photo_review=_review(privacy_risk=True)),
                 _meta(facebook_photo_review=_review(hero_candidate=False)),
                 _meta(facebook_photo_review=None), _meta(vision_has_text=True)):
        assert photo_rejection_reason(meta, "hero")
        assert _unfit_for_kind(meta, "hero", layout="background")
        assert rank_candidates("teacher children learn", "hero", [meta]).chosen is None
    strong = _meta()
    assert photo_rejection_reason(strong, "hero") is None
    assert photo_rejection_reason(strong, "team")
    assert photo_rejection_reason(strong, "testimonials")
    assert photo_rejection_reason(ImageMetadata(url="https://cdn/a.jpg"), "hero") is None


def test_resolver_rejects_a_weak_explicit_hero_pin():
    class NoStock:
        configured = False
    weak = _meta(facebook_photo_review=_review(hero_candidate=False))
    resolver = ImageResolver(scraped_metadata=[weak], pexels=NoStock(), use_llm_tiebreaker=False)
    photo = asyncio.run(resolver.resolve("teacher learning", intent="hero", pinned_url=weak.url))
    # A featured slot with no stock and a refused pin shows no image at all
    # (resolve returns None) — never the weak pin, and never a framed gradient.
    assert photo is None or photo.source != "scraped"


def test_library_filters_privacy_and_keeps_strongest_near_duplicate():
    strong = _meta("https://cdn.example/strong.jpg", facebook_photo_hash="abcdef1234567890")
    duplicate = _meta("https://cdn.example/copy.jpg", facebook_photo_hash="abcdef1234567891",
                      facebook_photo_review=_review(scores=PhotoScores(**{name: 7 for name in PhotoScores.model_fields})))
    privacy = _meta("https://cdn.example/names.jpg", facebook_photo_review=_review(privacy_risk=True))
    source = SourceContent(source_kind="facebook", source_ref=_page().canonical_url,
                           title="School", raw_text="Learning", image_metadata=[duplicate, privacy, strong])
    with patch.object(image_vision, "annotate_image_pool", AsyncMock(return_value={})), \
         patch.object(image_vision, "vision_enabled", return_value=True), \
         patch.object(settings, "facebook_photo_review_mode", "vision"):
        report = asyncio.run(review_photo_library(source, _page()))
    assert source.images == [strong.url]
    assert report["hero_stock_fallback"] is False
    assert report["rejected_summary"]["duplicate"] == 1
    assert report["rejected_summary"]["privacy_risk"] == 1
    assert any(item["scores"].get("overall") == 8 for item in report["images"])


def test_resized_facebook_url_duplicates_are_removed_before_vision():
    small = _meta("https://x.fbcdn.net/123456789_987654_n.jpg?stp=s320", width=320, height=300)
    large = _meta("https://x.fbcdn.net/123456789_987654_n.jpg?stp=s1600")
    source = SourceContent(source_kind="facebook", source_ref=_page().canonical_url,
                           title="School", raw_text="Learning", image_metadata=[small, large])
    with patch.object(image_vision, "annotate_image_pool", AsyncMock(return_value={})) as annotate, \
         patch.object(image_vision, "vision_enabled", return_value=True), \
         patch.object(settings, "facebook_photo_review_mode", "vision"):
        report = asyncio.run(review_photo_library(source, _page()))
    assert annotate.call_args.args[0] == [large]
    assert source.images == [large.url]
    assert report["rejected_summary"]["duplicate"] == 1


def test_preview_without_vision_labels_backups_and_does_not_force_cover_as_hero():
    page = _page(cover_photo_url="https://cdn.example/cover.jpg")
    with patch.object(image_vision, "vision_enabled", return_value=False), \
         patch("app.services.facebook_source.build_brand", AsyncMock(return_value=None)), \
         patch.object(settings, "facebook_photo_review_mode", "vision"):
        payload = asyncio.run(to_preview_payload(page))
    assert payload["facebook_photo_selection"]["hero_stock_fallback"] is True
    assert payload["facebook_photo_selection"]["images"][0]["decision"] == "backup"
    assert payload["source_content"]["image_metadata"][0]["intent"] == "generic"
    assert "gallery" not in payload["facebook_sections"]


def test_childcare_review_cache_is_separate_and_persisted_reviews_skip_calls():
    image_vision._ANNOTATION_CACHE.clear()
    llm = AsyncMock()
    llm.chat_json.side_effect = [VisionAnnotation(caption="generic", kind="photo"),
                               VisionAnnotation(caption="learning", kind="photo", facebook_photo_review=_review())]
    url = "https://cdn.example/same.jpg"
    childcare = ImageMetadata(url=url, selection_context="facebook_childcare")
    async def run():
        await annotate_image_pool([ImageMetadata(url=url)], llm=llm)
        await annotate_image_pool([childcare], llm=llm)
        image_vision._ANNOTATION_CACHE.clear()
        restored = ImageMetadata.model_validate(childcare.model_dump())
        await annotate_image_pool([restored], llm=llm)
    with patch.object(image_vision, "vision_enabled", return_value=True), \
         patch.object(image_vision, "_fetch_image_b64", AsyncMock(return_value="ZmFrZQ==")):
        asyncio.run(run())
    assert llm.chat_json.await_count == 2
    assert llm.chat_json.call_args.kwargs["system_prompt"] == CHILDCARE_FACEBOOK_PHOTO_PROMPT
    image_vision._ANNOTATION_CACHE.clear()
