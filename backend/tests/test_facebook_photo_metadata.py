"""Measured Facebook hero eligibility works without image-model calls."""

import asyncio
import hashlib
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.config import settings
from app.models.content_blocks import ImageMetadata, SourceContent
from app.models.facebook import FacebookPage
from app.models.facebook_photos import FacebookPhotoReview
from app.services import facebook_photo_metadata as metadata_service
from app.services.facebook_photo_metadata import assess_photo_metadata, caption_hints
from app.services.facebook_photo_profiles import PROFILES
from app.services.facebook_photo_selection import photo_rejection_reason, review_photo_library
from app.services.image_match import rank_candidates
from app.services.image_refs import _unfit_for_kind
from app.services.planner import DetectedBrand, _build_scaffolded_user_prompt, _build_user_prompt
from app.models.industry import PageScaffold
from app.services.source_router import image_prompt_payload


@pytest.fixture(autouse=True)
def clear_cache():
    metadata_service._PIXEL_CACHE.clear()
    yield
    metadata_service._PIXEL_CACHE.clear()


def _image_bytes(width=1600, height=1000):
    output = BytesIO()
    Image.new("RGB", (width, height), "#ac7852").save(output, format="PNG")
    return output.getvalue()


def _meta(url="https://cdn.example/activity.jpg", **kwargs):
    return ImageMetadata(url=url, selection_context="facebook_childcare",
                         caption="Children painting during our art activity.", **kwargs)


async def _ocr(pool, *, prefetched, max_images):
    for meta in pool:
        assert isinstance(prefetched[meta.url], bytes)
        meta.ocr_has_text = False
    return {}


async def _graphics(pool, *, prefetched, max_images):
    assert all(isinstance(prefetched[meta.url], bytes) for meta in pool)
    return {meta.url: False for meta in pool}


def test_provisional_facebook_gallery_role_is_screened_before_final_selection():
    from app.services import image_graphics, text_detection

    meta = _meta(url="https://cdn.example/new-gallery.jpg", role="gallery")

    async def ocr(pool, **kwargs):
        assert [item.url for item in text_detection._candidates(pool, 12)] == [meta.url]
        return await _ocr(pool, **kwargs)

    async def graphics(pool, **kwargs):
        assert [item.url for item in image_graphics._candidates(pool, 12)] == [meta.url]
        return await _graphics(pool, **kwargs)

    with patch.object(metadata_service, "_fetch_image_bytes", AsyncMock(return_value=_image_bytes())), \
         patch.object(metadata_service, "prefetch_text_flags", ocr), \
         patch.object(metadata_service, "screen_source_images_for_graphics", graphics):
        asyncio.run(assess_photo_metadata([meta]))
    assert meta.role == "gallery"
    assert meta.ocr_has_text is False
    assert meta.facebook_photo_review.pixel_checks_complete is True
    assert photo_rejection_reason(meta, "hero") is None


def test_one_download_reused_for_checks_and_repeated_scrapes_are_cached():
    raw = _image_bytes()
    fetch = AsyncMock(return_value=raw)
    async def run():
        first = _meta()
        await assess_photo_metadata([first])
        second = _meta()
        await assess_photo_metadata([second])
        return first, second
    with patch.object(metadata_service, "_fetch_image_bytes", fetch), \
         patch.object(metadata_service, "prefetch_text_flags", _ocr), \
         patch.object(metadata_service, "screen_source_images_for_graphics", _graphics):
        first, second = asyncio.run(run())
    assert fetch.await_count == 1
    assert (first.width, first.height) == (1600, 1000)
    assert first.content_hash == hashlib.sha256(raw).hexdigest()
    assert second.facebook_photo_review.method == "metadata"
    assert photo_rejection_reason(second, "hero") is None
    assert _unfit_for_kind(second, "hero", layout="background") is None


@pytest.mark.parametrize("caption,expected", [
    ("Children painting in our classroom", True),
    ("Graduation class photo after painting", False),
    ("Our registration fee announcement and painting programme", False),
    ("STEM experiments", True),
    ("A system update", False),  # 'stem' must not match inside 'system'
    ("", False),
])
def test_caption_hints_are_bounded_and_event_exclusions_are_conservative(caption, expected):
    _, hero_context = caption_hints(caption, PROFILES["facebook_childcare"])
    assert hero_context is expected


@pytest.mark.parametrize("changes", [
    {"width": 800}, {"width": 700, "height": 1600},
    {"ocr_has_text": True}, {"ocr_has_text": None}, {"qr_payload": "private QR"},
    {"role": "logo"},
])
def test_metadata_hero_gate_covers_pins_refs_and_ranking(changes):
    values = dict(width=1600, height=1000, ocr_has_text=False,
                  facebook_photo_review=FacebookPhotoReview(
                      method="metadata", pixel_checks_complete=True, caption_hero_context=True))
    values.update(changes)
    meta = _meta(**values)
    assert photo_rejection_reason(meta, "hero")
    assert _unfit_for_kind(meta, "hero", layout="background")
    assert rank_candidates("children painting", "hero", [meta]).chosen is None


def test_default_library_uses_metadata_even_when_a_vision_model_is_enabled():
    page = FacebookPage(name="School", canonical_url="https://www.facebook.com/school")
    source = SourceContent(source_kind="facebook", source_ref=page.canonical_url,
                           title="School", raw_text="Art learning", image_metadata=[_meta()])
    with patch.object(settings, "facebook_photo_review_mode", "metadata"), \
         patch.object(metadata_service, "_fetch_image_bytes", AsyncMock(return_value=_image_bytes())), \
         patch.object(metadata_service, "prefetch_text_flags", _ocr), \
         patch.object(metadata_service, "screen_source_images_for_graphics", _graphics), \
         patch("app.services.image_vision.vision_enabled", return_value=True), \
         patch("app.services.image_vision.annotate_image_pool", AsyncMock()) as vision:
        report = asyncio.run(review_photo_library(source, page))
    vision.assert_not_awaited()
    assert report["assessment_method"] == "metadata"
    assert report["review_status"] == "metadata_assessed"
    assert report["hero_stock_fallback"] is False
    assert source.image_metadata[0].intent == "hero"
    assert report["images"][0]["scores"] == {}  # no invented aesthetic scores
    assert source.image_metadata[0].facebook_photo_review.scores is None


def test_missing_ocr_or_download_cannot_qualify_a_hero():
    async def unavailable_ocr(pool, **kwargs):
        return {}
    for payload in (_image_bytes(), None):
        meta = _meta()
        with patch.object(metadata_service, "_fetch_image_bytes", AsyncMock(return_value=payload)) as fetch, \
             patch.object(metadata_service, "prefetch_text_flags", unavailable_ocr), \
             patch.object(metadata_service, "screen_source_images_for_graphics", _graphics):
            asyncio.run(assess_photo_metadata([meta]))
        assert fetch.await_count == 1
        assert meta.facebook_photo_review.decision == "backup"
        assert photo_rejection_reason(meta, "hero")


def test_caption_context_reaches_the_existing_scaffold_and_legacy_prompts():
    import json

    meta = _meta(facebook_photo_review=FacebookPhotoReview(method="metadata"))
    source = SourceContent(source_kind="facebook", source_ref="https://www.facebook.com/school",
                           title="School", raw_text="Art learning", image_metadata=[meta])
    scaffold = PageScaffold(page_type="home", slug="", title="Home", sections=["hero"])
    payload = json.loads(_build_scaffolded_user_prompt(
        source, DetectedBrand(site_name="School", industry_category="childcare"), [scaffold],
        source_map={"": source},
    ))
    images = payload["pages_requested"][0]["page_source"]["images"]
    assert images[0]["source_caption"] == meta.caption
    assert images[0]["assessment_method"] == "metadata"
    assert json.loads(_build_user_prompt(source))["facebook_photos"] == image_prompt_payload(source)


def test_check_budget_prioritises_activity_context_and_marks_remaining_backups():
    activity = _meta()
    unrelated = ImageMetadata(url="https://cdn.example/cover.jpg", selection_context="facebook_childcare")
    with patch.object(settings, "facebook_photo_max_images", 1), \
         patch.object(metadata_service, "_fetch_image_bytes", AsyncMock(return_value=_image_bytes())) as fetch, \
         patch.object(metadata_service, "prefetch_text_flags", _ocr), \
         patch.object(metadata_service, "screen_source_images_for_graphics", _graphics):
        asyncio.run(assess_photo_metadata([unrelated, activity]))
    assert fetch.await_count == 1
    assert fetch.call_args.args[0] == activity.url
    assert activity.facebook_photo_review.pixel_checks_complete
    assert unrelated.facebook_photo_review.decision == "backup"
