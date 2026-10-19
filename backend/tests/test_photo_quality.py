"""Measured photo quality ranks a Facebook Page's photos; it never rejects one.

`photo_quality` reads sharpness and exposure from bytes the pixel pass already
holds. These tests pin the two measurements on synthetic photographs (a
detailed frame, the same frame blurred, darkened, blown out), the ranking it
feeds, the provenance each photo carries into the inventory, and the pixel
cache surviving Facebook's per-read re-signing of every URL.
"""

import asyncio
from io import BytesIO
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest
from PIL import Image, ImageEnhance, ImageFilter

from app.models.content_blocks import ImageMetadata
from app.models.facebook import FacebookPage, FacebookPhoto, FacebookPost
from app.services import facebook_photo_metadata as metadata_service
from app.services.facebook_photo_metadata import assess_photo_metadata
from app.services.facebook_photo_selection import photo_rank
from app.services.facebook_source import to_image_metadata
from app.services.photo_quality import photo_quality


def _detailed(seed=7, size=(1200, 800)) -> Image.Image:
    """A frame with real texture: smooth gradients plus fine noise and edges."""
    rng = np.random.default_rng(seed)
    w, h = size
    y, x = np.mgrid[0:h, 0:w]
    base = (np.sin(x / 37.0) + np.cos(y / 23.0)) * 40 + 128
    noise = rng.normal(0, 25, (h, w))
    stripes = ((x // 15) % 2) * 30
    grey = np.clip(base + noise + stripes, 0, 255)
    rgb = np.stack([grey, grey * 0.9, grey * 0.8], axis=-1).astype("uint8")
    return Image.fromarray(rgb)


def _bytes(image: Image.Image) -> bytes:
    out = BytesIO()
    image.save(out, format="JPEG", quality=90)
    return out.getvalue()


def test_a_sharp_photo_outranks_the_same_photo_blurred():
    image = _detailed()
    sharp = photo_quality(_bytes(image))
    blurred = photo_quality(_bytes(image.filter(ImageFilter.GaussianBlur(6))))
    assert sharp is not None and blurred is not None
    assert sharp > 0.8
    assert blurred < sharp - 0.4


def test_a_dark_or_blown_out_photo_scores_below_a_well_exposed_one():
    image = _detailed()
    good = photo_quality(_bytes(image))
    dark = photo_quality(_bytes(ImageEnhance.Brightness(image).enhance(0.15)))
    blown = photo_quality(_bytes(ImageEnhance.Brightness(image).enhance(3.5)))
    assert dark < good and blown < good


def test_undecodable_bytes_are_unmeasured_not_bad():
    assert photo_quality(b"not an image") is None


def _meta(url, *, caption="", quality=None, width=1600, height=1000):
    return ImageMetadata(url=url, caption=caption, quality=quality, width=width, height=height,
                         selection_context="facebook")


def test_a_captioned_photo_ranks_first_then_quality_then_size():
    big_blurry = _meta("https://scontent.fbcdn.net/v/a_n.jpg", quality=0.2, width=4000, height=3000)
    small_sharp = _meta("https://scontent.fbcdn.net/v/b_n.jpg", quality=0.9, width=1200, height=800)
    captioned = _meta("https://scontent.fbcdn.net/v/c_n.jpg", caption="Planting seeds today",
                      quality=0.4, width=800, height=600)
    ranked = sorted([big_blurry, small_sharp, captioned], key=photo_rank, reverse=True)
    # Area alone used to put the big blurry photo first.
    assert [m.url for m in ranked] == [captioned.url, small_sharp.url, big_blurry.url]


def test_every_photo_carries_its_post_and_origin():
    post = FacebookPost(message="Our reading corner", created_time="2026-10-01T09:00:00+0000",
                        post_id="1_2", permalink="https://www.facebook.com/x/posts/2",
                        photos=[FacebookPhoto(url="https://scontent.fbcdn.net/v/p1_n.jpg")])
    tile = FacebookPost(photos=[FacebookPhoto(url="https://scontent.fbcdn.net/v/t1_n.jpg")],
                        permalink="https://www.facebook.com/photo/?fbid=9")
    page = FacebookPage(name="Kiddo", canonical_url="https://www.facebook.com/kiddo",
                        cover_photo_url="https://scontent.fbcdn.net/v/cover_n.jpg",
                        posts=[post, tile], fetched_via="render")
    by_url = {meta.url: meta for meta in to_image_metadata(page)}
    cover = by_url["https://scontent.fbcdn.net/v/cover_n.jpg"]
    photo = by_url["https://scontent.fbcdn.net/v/p1_n.jpg"]
    tiled = by_url["https://scontent.fbcdn.net/v/t1_n.jpg"]
    assert cover.origin == "cover"
    assert (photo.origin, photo.source_post_id, photo.published_at) == (
        "post", "1_2", "2026-10-01T09:00:00+0000")
    assert photo.source_post_url == "https://www.facebook.com/x/posts/2"
    assert tiled.origin == "photos_tab" and tiled.source_post_id is None


@pytest.fixture(autouse=True)
def _clear_pixel_cache():
    metadata_service._PIXEL_CACHE.clear()
    yield
    metadata_service._PIXEL_CACHE.clear()


async def _ocr(pool, *, prefetched, max_images):
    for meta in pool:
        meta.ocr_has_text = False
    return {}


async def _graphics(pool, *, prefetched, max_images):
    return {meta.url: False for meta in pool}


_PHOTO = "https://scontent.fbcdn.net/v/t39.30808-6/461234567_1234567890_n.jpg"


def test_a_re_signed_url_reuses_the_measurement_of_the_same_picture():
    raw = _bytes(_detailed())
    first = ImageMetadata(url=f"{_PHOTO}?oe=AAA&_nc_sig=1", selection_context="facebook")
    again = ImageMetadata(url=f"{_PHOTO}?oe=BBB&_nc_sig=2", selection_context="facebook")
    fetch = AsyncMock(return_value=raw)
    with patch.object(metadata_service, "_fetch_image_bytes", fetch), \
         patch.object(metadata_service, "prefetch_text_flags", _ocr), \
         patch.object(metadata_service, "screen_source_images_for_graphics", _graphics):
        asyncio.run(assess_photo_metadata([first]))
        asyncio.run(assess_photo_metadata([again]))
    assert fetch.await_count == 1
    assert again.quality == first.quality and first.quality is not None


def test_a_crop_is_measured_apart_from_its_original():
    """A crop shares its original's filename; its size is not the original's."""
    portrait = BytesIO()
    _detailed(size=(600, 900)).save(portrait, format="JPEG")
    square = BytesIO()
    _detailed(size=(600, 600)).save(square, format="JPEG")
    original = ImageMetadata(url=f"{_PHOTO}?oe=AAA", selection_context="facebook")
    crop = ImageMetadata(url=f"{_PHOTO}?stp=c0.150.600.600a_dst-jpg&oe=AAA",
                         selection_context="facebook")
    fetch = AsyncMock(side_effect=[portrait.getvalue(), square.getvalue()])
    with patch.object(metadata_service, "_fetch_image_bytes", fetch), \
         patch.object(metadata_service, "prefetch_text_flags", _ocr), \
         patch.object(metadata_service, "screen_source_images_for_graphics", _graphics):
        asyncio.run(assess_photo_metadata([original]))
        asyncio.run(assess_photo_metadata([crop]))
    assert (original.width, original.height) == (600, 900)
    assert (crop.width, crop.height) == (600, 600)
