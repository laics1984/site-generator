"""One measured photo pass for every industry; the profile row says how."""

import asyncio
from io import BytesIO
from typing import get_args
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from app.models.content_blocks import ImageMetadata, PhotoContext, SourceContent
from app.models.facebook import FacebookPage, FacebookPhoto, FacebookPost
from app.services import facebook_photo_metadata as metadata_service
from app.services.facebook_photo_profiles import PROFILES, context_for
from app.services.facebook_photo_selection import photo_rejection_reason, review_photo_library
from app.services.facebook_source import to_image_metadata
from app.services.menu_assets import classify_menu_image


@pytest.fixture(autouse=True)
def clear_cache():
    metadata_service._PIXEL_CACHE.clear()
    yield
    metadata_service._PIXEL_CACHE.clear()


def _png(width: int, height: int) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), "#ac7852").save(output, format="PNG")
    return output.getvalue()


def _screens(text: dict[str, str] | None = None, qr: dict[str, str] | None = None):
    """Stand-ins for the shared OCR and graphic screens, keyed by URL."""
    text, qr = text or {}, qr or {}

    async def ocr(pool, *, prefetched, max_images):
        for meta in pool:
            meta.ocr_text = text.get(meta.url, "")
            meta.ocr_has_text = bool(meta.ocr_text)
            meta.qr_payload = qr.get(meta.url)
        return {}

    async def graphics(pool, *, prefetched, max_images):
        return {meta.url: False for meta in pool}

    return ocr, graphics


def _review(page: FacebookPage, sizes: dict[str, tuple[int, int]], **screens) -> SourceContent:
    source = SourceContent(source_kind="facebook", source_ref=page.canonical_url,
                           title=page.name, raw_text="x", image_metadata=to_image_metadata(page))
    ocr, graphics = _screens(**screens)

    async def fetch(url, client):
        return _png(*sizes[url])

    with patch.object(metadata_service, "_fetch_image_bytes", fetch), \
         patch.object(metadata_service, "prefetch_text_flags", ocr), \
         patch.object(metadata_service, "screen_source_images_for_graphics", graphics):
        asyncio.run(review_photo_library(source, page))
    return source


def _caterer(*posts: FacebookPost) -> FacebookPage:
    return FacebookPage(name="Sunny Catering", canonical_url="https://www.facebook.com/sunny",
                        category="Caterer", posts=list(posts))


def _post(url: str, message: str | None = None) -> FacebookPost:
    return FacebookPost(message=message, photos=[FacebookPhoto(url=url)])


def test_every_photo_context_has_a_profile_row():
    assert set(PROFILES) == set(get_args(PhotoContext))


def test_an_industry_without_a_row_reads_the_default():
    assert context_for("agency") == "facebook"
    assert context_for("childcare") == "facebook_childcare"
    assert context_for("restaurant") == "facebook_restaurant"


def test_every_profile_subject_is_a_classification_and_has_a_label():
    """A caption hint lands in `FacebookPhotoReview.classification`, so a
    subject outside that vocabulary fails validation the first time it fires;
    its label is the heading a gallery group of it takes."""
    from app.models.facebook_photos import FacebookPhotoReview

    vocabulary = set(get_args(get_args(FacebookPhotoReview.model_fields["classification"].annotation)[0]))
    for context, profile in PROFILES.items():
        names = [subject.name for subject in profile.subject_hints]
        assert len(names) == len(set(names)), context
        for subject in profile.subject_hints:
            assert subject.name in vocabulary, (context, subject.name)
            assert subject.label and subject.phrases, (context, subject.name)
        assert profile.setting_subjects <= set(names), context


def test_a_generic_caption_keeps_its_own_words_as_the_photo_context():
    url = "https://cdn.example/workshop.jpg"
    source = _review(FacebookPage(name="Bright Studio", canonical_url="https://www.facebook.com/bright",
                                  category="Agency", posts=[_post(url, "Our workshop on brand strategy")]),
                     {url: (1600, 1000)})
    meta = source.image_metadata[0]
    assert meta.facebook_photo_review.classification == ["event"]
    assert meta.context_heading == "Our workshop on brand strategy"


def test_a_generic_hero_is_decided_by_measurement_not_by_a_caption():
    landscape, portrait = "https://cdn.example/wide.jpg", "https://cdn.example/tall.jpg"
    source = _review(_caterer(_post(landscape), _post(portrait)),
                     {landscape: (2048, 1536), portrait: (1294, 2000)})
    by_url = {meta.url: meta for meta in source.image_metadata}
    assert photo_rejection_reason(by_url[landscape], "hero") is None
    assert by_url[landscape].intent == "hero"
    assert photo_rejection_reason(by_url[portrait], "hero") == "not a suitable landscape shape"
    assert photo_rejection_reason(by_url[portrait], "gallery") is None


def test_a_caption_can_still_rule_out_a_generic_hero():
    flyer = "https://cdn.example/flyer.jpg"
    source = _review(_caterer(_post(flyer, "Our new menu flyer is out!")), {flyer: (2048, 1536)})
    assert photo_rejection_reason(source.image_metadata[0], "hero") == "caption rules out a hero"


def test_no_facebook_photo_ever_stands_for_a_named_person():
    url = "https://cdn.example/wide.jpg"
    source = _review(_caterer(_post(url)), {url: (2048, 1536)})
    for section in ("team", "testimonials", "avatar"):
        assert photo_rejection_reason(source.image_metadata[0], section)


def test_a_menu_sheet_and_a_payment_code_stay_in_the_pool_for_their_placers():
    """The generic row leaves words-in-pixels to the passes that place them:
    `menu_assets` gets the menu, `legible_images` the code."""
    menu, code = "https://cdn.example/menu.jpg", "https://cdn.example/qr.jpg"
    source = _review(
        _caterer(_post(menu), _post(code)),
        {menu: (1200, 1700), code: (800, 800)},
        text={menu: "Menu\nChicken chop RM18\nFish and chips RM20"},
        qr={code: "00020101021126580014A000000615000101065887330204HELLO6304ABCD"},
    )
    by_url = {meta.url: meta for meta in source.image_metadata}
    assert set(by_url) == {menu, code}
    assert by_url[code].role == "content"
    classify_menu_image(by_url[menu])
    assert by_url[menu].content_purpose == "menu"
    assert photo_rejection_reason(by_url[menu], "hero") is not None


def test_childcare_still_withdraws_words_in_pixels():
    url = "https://cdn.example/poster.jpg"
    page = FacebookPage(name="Little School", canonical_url="https://www.facebook.com/little",
                        category="Kindergarten", posts=[_post(url)])
    source = _review(page, {url: (1600, 1000)}, text={url: "Registration open"})
    assert source.image_metadata == []


def test_the_generic_profile_never_asks_the_vision_model():
    url = "https://cdn.example/wide.jpg"
    annotate = AsyncMock()
    with patch("app.services.image_vision.annotate_image_pool", annotate), \
         patch("app.services.image_vision.vision_enabled", return_value=True), \
         patch("app.config.settings.facebook_photo_review_mode", "vision"):
        source = _review(_caterer(_post(url)), {url: (2048, 1536)})
    annotate.assert_not_called()
    assert source.image_metadata[0].facebook_photo_review.method == "metadata"


def test_a_caption_is_kept_as_evidence_for_the_planner():
    url = "https://cdn.example/wide.jpg"
    meta = to_image_metadata(_caterer(_post(url, "Grilled chicken platter for a wedding")))[0]
    assert isinstance(meta, ImageMetadata)
    assert meta.caption == "Grilled chicken platter for a wedding"
