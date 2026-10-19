"""The opt-in vision step (D5): a Claude second look at strong uncaptioned photos.

Childcare calls a photo a hero only when the Page says what the children are
doing, so a strong photo posted without words could never lead the site. With
a Claude model picked for reasoning, the uncaptioned photos whose measurements
alone qualify are judged by it — capped, best first — and nothing else is.
"""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.config import settings
from app.models.content_blocks import SourceContent
from app.models.facebook import FacebookPage, FacebookPhoto, FacebookPost
from app.models.facebook_photos import FacebookPhotoReview, PhotoScores
from app.services import facebook_photo_metadata as metadata_service
from app.services import image_vision, llm_choice
from app.services.facebook_photo_selection import photo_rejection_reason, review_photo_library
from app.services.facebook_source import to_image_metadata
from app.services.image_vision import VisionAnnotation

_STRONG = "https://cdn.example/strong.jpg"
_CAPTIONED = "https://cdn.example/captioned.jpg"
_SMALL = "https://cdn.example/small.jpg"
_SIZES = {_STRONG: (2000, 1200), _CAPTIONED: (2000, 1200), _SMALL: (900, 600)}
# Distinct pixels, or three flat frames are one picture to the dedupe.
_COLOURS = {_STRONG: "#ac7852", _CAPTIONED: "#3b6e8f", _SMALL: "#7a9a3d"}


@pytest.fixture(autouse=True)
def _clean():
    metadata_service._PIXEL_CACHE.clear()
    image_vision._ANNOTATION_CACHE.clear()
    yield
    metadata_service._PIXEL_CACHE.clear()
    image_vision._ANNOTATION_CACHE.clear()


def _png(url):
    out = BytesIO()
    Image.new("RGB", _SIZES[url], _COLOURS[url]).save(out, format="PNG")
    return out.getvalue()


def _page():
    return FacebookPage(
        name="Little School", canonical_url="https://www.facebook.com/little", category="Kindergarten",
        posts=[FacebookPost(photos=[FacebookPhoto(url=_STRONG)]),
               FacebookPost(message="Children painting during art activity",
                            photos=[FacebookPhoto(url=_CAPTIONED)]),
               FacebookPost(photos=[FacebookPhoto(url=_SMALL)])],
    )


def _strong_review():
    return FacebookPhotoReview(
        classification=["creative_activity"], recommended_sections=["hero", "gallery"],
        decision="accept", orientation="landscape", candid=True, crop_safe=True,
        negative_space=True, hero_candidate=True,
        scores=PhotoScores(**{name: 8 for name in PhotoScores.model_fields}),
    )


def _run(reasoning: str, llm: AsyncMock) -> SourceContent:
    page = _page()
    source = SourceContent(source_kind="facebook", source_ref=page.canonical_url, title=page.name,
                           raw_text="x", image_metadata=to_image_metadata(page))

    async def fetch(url, client):
        return _png(url)

    async def ocr(pool, *, prefetched, max_images):
        for meta in pool:
            meta.ocr_has_text = False
        return {}

    async def graphics(pool, *, prefetched, max_images):
        return {meta.url: False for meta in pool}

    token = llm_choice.use(llm_choice.LlmChoice(reasoning=reasoning))
    try:
        with patch.object(metadata_service, "_fetch_image_bytes", fetch), \
             patch.object(metadata_service, "prefetch_text_flags", ocr), \
             patch.object(metadata_service, "screen_source_images_for_graphics", graphics), \
             patch.object(image_vision, "_fetch_image_b64", AsyncMock(return_value="ZmFrZQ==")), \
             patch("app.services.llm.get_reasoning_llm", return_value=llm), \
             patch.object(settings, "hero_min_background_dim", 1600):
            asyncio.run(review_photo_library(source, page))
    finally:
        llm_choice.reset(token)
    return source


def _vision_llm():
    llm = AsyncMock()
    llm.chat_json.return_value = VisionAnnotation(
        caption="children painting at a table", kind="photo", people_count=3,
        facebook_photo_review=_strong_review(),
    )
    return llm


def test_a_claude_choice_judges_only_the_strong_uncaptioned_photo():
    llm = _vision_llm()
    source = _run(llm_choice.CLAUDE_MODELS[0].id, llm)
    assert llm.chat_json.await_count == 1
    by_url = {meta.url: meta for meta in source.image_metadata}
    assert by_url[_STRONG].facebook_photo_review.method == "vision"
    assert photo_rejection_reason(by_url[_STRONG], "hero") is None
    # The captioned photo keeps its measured review; the small one was never sent.
    assert by_url[_CAPTIONED].facebook_photo_review.method == "metadata"
    assert by_url[_SMALL].facebook_photo_review.method == "metadata"


def test_a_local_choice_makes_no_vision_call():
    llm = _vision_llm()
    source = _run(llm_choice.LOCAL, llm)
    assert llm.chat_json.await_count == 0
    strong = next(meta for meta in source.image_metadata if meta.url == _STRONG)
    assert photo_rejection_reason(strong, "hero") is not None


def test_a_failed_call_keeps_the_measured_review():
    llm = AsyncMock()
    llm.chat_json.side_effect = RuntimeError("API down")
    source = _run(llm_choice.CLAUDE_MODELS[0].id, llm)
    strong = next(meta for meta in source.image_metadata if meta.url == _STRONG)
    assert strong.facebook_photo_review.method == "metadata"
