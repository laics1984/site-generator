"""The profile picture as the brand mark, and the guard around it.

The profile picture is the conventional brand-mark slot on a business Page, so
it goes through the shared `brand_candidate` seam. But plenty of Pages
put a storefront shot or a face there, which renders badly as a header logo —
hence the vision guard, which demotes a photograph to palette-only rather than
letting it sit in the header.
"""

import asyncio
import io
import unittest
from unittest.mock import patch

from PIL import Image

from app.config import settings
from app.models.facebook import FacebookPage
from app.services import facebook_source
from app.services.logo_extraction import is_renderable


def _png(width: int, height: int, rgb=(20, 90, 200)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), rgb).save(buf, format="PNG")
    return buf.getvalue()


def _page(**overrides) -> FacebookPage:
    base = dict(
        name="Acme Coffee Roasters",
        canonical_url="https://www.facebook.com/acmecoffee",
        profile_picture_url="https://cdn.example/pic.png",
    )
    base.update(overrides)
    return FacebookPage(**base)


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content
        self.status_code = 200

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, content: bytes):
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, *args, **kwargs):
        return _FakeResponse(self._content)


def _async_const(value):
    async def _inner(*args, **kwargs):
        return value

    return _inner


def _build(page: FacebookPage, image: bytes, *, photo: bool = False):
    """Run build_brand with the network and the vision judge both stubbed.

    `is_public_url` is stubbed too: the SSRF guard resolves the host for real,
    and `cdn.example` doesn't exist, so the fetch would be refused before the
    bytes ever mattered.
    """
    with (
        patch("httpx.AsyncClient", lambda *a, **k: _FakeClient(image)),
        patch("app.services.brand_candidate.is_public_url", _async_const(True)),
        patch.object(facebook_source, "_looks_like_photograph", _async_const(photo)),
    ):
        return asyncio.run(facebook_source.build_brand(page))


class ProfilePictureAsMarkTest(unittest.TestCase):
    def test_a_square_profile_picture_becomes_the_brand_mark(self):
        brand = _build(_page(), _png(720, 720))
        self.assertEqual(brand.name, "Acme Coffee Roasters")
        self.assertEqual(brand.logo_source, "logo")
        self.assertTrue(brand.logo_render_ok)
        self.assertEqual(brand.logo_url, "https://cdn.example/pic.png")

    def test_a_palette_is_extracted_from_it(self):
        brand = _build(_page(), _png(720, 720, rgb=(20, 90, 200)))
        self.assertTrue(brand.extracted_palette)

    def test_a_page_with_no_picture_still_yields_a_named_brand(self):
        brand = asyncio.run(facebook_source.build_brand(_page(profile_picture_url=None)))
        self.assertEqual(brand.name, "Acme Coffee Roasters")
        self.assertIsNone(brand.logo_url)


class PhotoGuardTest(unittest.TestCase):
    def test_a_photograph_is_demoted_to_palette_only(self):
        """`og-image` is the source for which is_renderable always returns
        False, so the header falls back to the text wordmark while the palette
        still comes from the picture."""
        brand = _build(_page(), _png(720, 720), photo=True)
        self.assertEqual(brand.logo_source, "og-image")
        self.assertFalse(brand.logo_render_ok)
        self.assertTrue(brand.extracted_palette)

    def test_og_image_is_never_renderable_whatever_its_size(self):
        self.assertFalse(is_renderable("og-image", size=(1200, 1200), is_vector=False))

    def test_the_guard_is_skipped_when_disabled(self):
        original = settings.facebook_logo_vision_check
        settings.facebook_logo_vision_check = False
        try:
            # The judge would say "photo", but it must never be consulted.
            calls: list[str] = []

            async def _judge(url):
                calls.append(url)
                return True

            with (
                patch.object(facebook_source, "_looks_like_photograph", _judge),
                patch("httpx.AsyncClient", lambda *a, **k: _FakeClient(_png(720, 720))),
                patch("app.services.brand_candidate.is_public_url", _async_const(True)),
            ):
                brand = asyncio.run(facebook_source.build_brand(_page()))
            self.assertEqual(brand.logo_source, "logo")
            self.assertEqual(calls, [])
        finally:
            settings.facebook_logo_vision_check = original

    def test_the_guard_degrades_to_accepting_the_mark_when_vision_is_off(self):
        """Vision is opt-in; with no model configured the check must be a
        no-op, not a failure and not a blanket demotion."""
        self.assertIsNone(settings.llm_vision_model)
        self.assertFalse(
            asyncio.run(facebook_source._looks_like_photograph("https://cdn.example/pic.png"))
        )


class CoverPhotoTest(unittest.TestCase):
    def test_the_cover_photo_is_never_offered_as_a_logo(self):
        """It's a wide banner with baked-in text — the worst possible header
        mark, and the one an og:image-first ranking used to pick."""
        page = _page(
            profile_picture_url=None, cover_photo_url="https://cdn.example/cover.jpg"
        )
        brand = asyncio.run(facebook_source.build_brand(page))
        self.assertIsNone(brand.logo_url)

    def test_the_cover_photo_is_the_hero_image_instead(self):
        page = _page(cover_photo_url="https://cdn.example/cover.jpg")
        metas = facebook_source.to_image_metadata(page)
        self.assertEqual(metas[0].url, "https://cdn.example/cover.jpg")
        self.assertEqual(metas[0].intent, "hero")


if __name__ == "__main__":
    unittest.main()
