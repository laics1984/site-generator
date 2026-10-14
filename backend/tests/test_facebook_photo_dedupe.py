"""A re-encoded copy of a photo is one photo, whatever its caption says.

`facebook_photo_selection._similar_photo` used to compare thumbnail hashes only
when both photos shared a caption classification — which no uncaptioned photo
and no non-childcare Page ever has, so near-duplicate detection was off for
exactly the photos that needed it. The threshold is measured (see the constant's
comment): copies within 3 bits, distinct shots of one activity no closer than 18.
"""

from __future__ import annotations

import asyncio
import io
import random
from unittest.mock import AsyncMock, patch

from PIL import Image

from app.models.content_blocks import ImageMetadata, SourceContent
from app.models.facebook import FacebookPage
from app.services.facebook_photo_selection import (
    _NEAR_DUPLICATE_BITS,
    _similar_photo,
    review_photo_library,
)
from app.services.image_vision import _thumbnail_hash


def _photo(seed: int, size=(1600, 1000)) -> Image.Image:
    """A photo-like test image: smooth light falloff plus seeded blobs, so its
    9x8 luminance grid has real structure for the hash to read."""
    rng = random.Random(seed)
    image = Image.new("L", (32, 20))
    image.putdata([
        int(40 + 3 * x + 4 * y + rng.randint(-60, 60)) % 256
        for y in range(20) for x in range(32)
    ])
    return image.resize(size, Image.Resampling.BICUBIC).convert("RGB")


def _jpeg(image: Image.Image, quality: int = 85) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    return buffer.getvalue()


def _meta(url: str, raw: bytes, size: tuple[int, int]) -> ImageMetadata:
    return ImageMetadata(
        url=url, width=size[0], height=size[1], selection_context="facebook",
        facebook_photo_hash=_thumbnail_hash(raw),
    )


def _hash_with_bits_flipped(value: str, bits: int) -> str:
    return f"{int(value, 16) ^ ((1 << bits) - 1):016x}"


def test_a_reencoded_copy_is_the_same_photo_without_any_caption():
    original = _photo(1)
    copy = original.resize((960, 600))
    a = _meta("https://cdn/a.jpg", _jpeg(original), original.size)
    b = _meta("https://cdn/b.jpg", _jpeg(copy, quality=40), copy.size)
    assert a.facebook_photo_review is None and b.facebook_photo_review is None
    assert _similar_photo(a, b)


def test_different_photos_stay_different():
    a = _meta("https://cdn/a.jpg", _jpeg(_photo(1)), (1600, 1000))
    b = _meta("https://cdn/b.jpg", _jpeg(_photo(2)), (1600, 1000))
    assert not _similar_photo(a, b)


def test_the_threshold_is_a_hard_edge():
    base = _meta("https://cdn/a.jpg", _jpeg(_photo(1)), (1600, 1000))
    near = base.model_copy(update={
        "url": "https://cdn/near.jpg",
        "facebook_photo_hash": _hash_with_bits_flipped(base.facebook_photo_hash, _NEAR_DUPLICATE_BITS),
    })
    far = base.model_copy(update={
        "url": "https://cdn/far.jpg",
        "facebook_photo_hash": _hash_with_bits_flipped(base.facebook_photo_hash, _NEAR_DUPLICATE_BITS + 1),
    })
    assert _similar_photo(base, near)
    assert not _similar_photo(base, far)


def test_a_landscape_and_a_portrait_are_never_one_photo():
    base = _meta("https://cdn/a.jpg", _jpeg(_photo(1)), (1600, 1000))
    turned = base.model_copy(update={"url": "https://cdn/b.jpg", "width": 1000, "height": 1600})
    assert not _similar_photo(base, turned)


def test_a_generic_page_keeps_one_copy_and_the_larger_one():
    page = FacebookPage(name="Kopitiam", canonical_url="https://www.facebook.com/kopitiam",
                        category="Cafe")
    original = _photo(7)
    small = original.resize((800, 500))
    large_meta = _meta("https://cdn/large.jpg", _jpeg(original), original.size)
    small_meta = _meta("https://cdn/small.jpg", _jpeg(small, quality=50), small.size)
    other = _meta("https://cdn/other.jpg", _jpeg(_photo(8)), (1600, 1000))
    source = SourceContent(
        source_kind="facebook", source_ref=page.canonical_url, raw_text="Kopitiam",
        image_metadata=[small_meta, other, large_meta],
    )
    # The pixel pass already ran: the hashes above are what it stamps.
    with patch("app.services.facebook_photo_metadata.assess_photo_metadata", AsyncMock()):
        report = asyncio.run(review_photo_library(source, page))

    assert sorted(source.images) == ["https://cdn/large.jpg", "https://cdn/other.jpg"]
    assert report["rejected_summary"]["duplicate"] == 1
