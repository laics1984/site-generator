"""
One picture is one picture, whichever URL a page reaches it by.

A site this tool generated shows the same photo at two URLs: its og:image on
images.pexels.com and the copy the push re-hosted on the asset host. Crawled
back (the update flow), the pool held both, and webtree.my's regenerated home
page showed one photo as hero AND About image, while its About page showed one
laptop photo twice as two poster sections. Every "already shown" test now keys
on `image_urls.image_identity` — the photo id when the URL names one, the bytes
when the pixel pass read them, the URL otherwise.

The second half of that About page bug is pinned here too: the laptop photo
became a poster because OCR boxed the code on its screen. The recognizer's
confidence separates glyph noise from words (services/text_detection.py).
"""

from __future__ import annotations

import base64
import hashlib
import unittest
from unittest import mock

from app.models.content_blocks import (
    CtaBlock,
    HeroBlock,
    ImageMetadata,
    PagePlan,
    PosterBlock,
    SourceContent,
)
from app.services import text_detection
from app.services.image_urls import image_identity, photo_identity
from app.services.legible_images import inject_legible_images
from app.services.media import ImageResolver
from app.services.pexels import PhotoResult

PEXELS = "https://images.pexels.com/photos/7988114/pexels-photo-7988114.jpeg?auto=compress&h=650"
HOSTED = "https://asset.example/storage/obj/f7bf/2026/08/1787915650pexels-photo-7988114.jpg"
OTHER = "https://watr.org.my/wp-content/uploads/2020/02/Galleria_Pic1.jpg"


def _meta(url: str, **kw) -> ImageMetadata:
    base = dict(url=url, alt="", intent="generic", role="unknown", width=1080, height=1080,
                source_usage="inline")
    base.update(kw)
    return ImageMetadata(**base)


class IdentityTest(unittest.TestCase):
    def test_a_pexels_photo_is_its_id_under_either_host(self):
        self.assertEqual(photo_identity(PEXELS), "pexels:7988114")
        self.assertEqual(photo_identity(HOSTED), "pexels:7988114")
        self.assertEqual(image_identity(_meta(PEXELS)), image_identity(_meta(HOSTED)))

    def test_the_id_outranks_differing_bytes(self):
        """The regression. The CMS re-encodes a photo when the push re-hosts
        it, so the two copies hash differently — and with the hash ranked
        first, webtree.my's regenerated home page used one photo as hero AND
        About image all over again."""
        self.assertEqual(
            image_identity(_meta(PEXELS, content_hash="original-bytes")),
            image_identity(_meta(HOSTED, content_hash="re-encoded-bytes")),
        )

    def test_the_bytes_decide_when_no_id_is_in_the_url(self):
        self.assertEqual(image_identity(_meta(OTHER, content_hash="abc")), "abc")

    def test_anything_else_is_its_url(self):
        self.assertIsNone(photo_identity(OTHER))
        self.assertEqual(image_identity(_meta(OTHER)), OTHER)


class ReadingCarriesTheHashTest(unittest.TestCase):
    def _reading(self, payload):
        # A detector that sees no text: the reading is about the bytes, not the words.
        with mock.patch.object(text_detection, "_engine", return_value=lambda px: ([], 0)), \
             mock.patch.object(text_detection.qr_codes, "decode", return_value=None):
            return text_detection.read_pixels(payload)

    def test_raw_and_base64_payloads_hash_the_same(self):
        from io import BytesIO
        from PIL import Image
        buf = BytesIO()
        Image.new("RGB", (8, 8), (10, 20, 30)).save(buf, format="PNG")
        raw = buf.getvalue()

        by_bytes = self._reading(raw)
        by_b64 = self._reading(base64.b64encode(raw).decode())

        self.assertEqual(by_bytes.content_hash, hashlib.sha256(raw).hexdigest())
        self.assertEqual(by_b64.content_hash, by_bytes.content_hash)

    def test_the_stamp_writes_it_onto_the_metadata(self):
        meta = _meta(OTHER)
        text_detection._stamp(
            [meta], {OTHER: text_detection.PixelReading(has_text=False, qr_payload=None, content_hash="h")}
        )
        self.assertEqual(meta.content_hash, "h")


class GlyphNoiseIsNotWordingTest(unittest.TestCase):
    """The laptop photo: nine boxes the detector found on a screen of code,
    every one transcribed as garbage at 0.53–0.79 confidence. A poster's boxes
    score 0.93 and up."""

    def _box(self, x, y, w, h, text, confidence):
        return ([[x, y], [x + w, y], [x + w, y + h], [x, y + h]], text, confidence)

    def test_low_confidence_boxes_do_not_count_toward_coverage(self):
        noise = [self._box(0, i * 10, 100, 9, "prpladod,st", 0.7) for i in range(10)]
        self.assertEqual(text_detection._coverage(noise, 100, 100), 0.0)

    def test_readable_words_still_do(self):
        words = [self._box(0, 0, 100, 20, "Empowered Work Life", 0.95)]
        self.assertAlmostEqual(text_detection._coverage(words, 100, 100), 0.2)


def _page(slug: str) -> PagePlan:
    return PagePlan(
        page_type="about", slug=slug, title="About",
        blocks=[HeroBlock(headline="x"), CtaBlock(headline="y", cta_label="Go", cta_href="/")],
    )


def _source(metas: list[ImageMetadata]) -> SourceContent:
    return SourceContent(
        source_kind="url", source_ref="https://webtree.my/about", raw_text="",
        url_path="/about/",
        image_metadata=[_meta(m.url, context_heading=m.context_heading) for m in metas],
    )


class PosterPlacementTest(unittest.TestCase):
    def test_the_same_poster_at_two_urls_is_placed_once(self):
        pool = [
            _meta(PEXELS, ocr_has_text=True, content_hash="same"),
            _meta(HOSTED, ocr_has_text=True, content_hash="same"),
        ]
        page = _page("about")

        inject_legible_images([page], _source(pool), pool)

        posters = [b for b in page.blocks if isinstance(b, PosterBlock)]
        self.assertEqual(len(posters), 1)
        self.assertEqual(len(posters[0].items), 1)

    def test_a_poster_without_a_source_heading_has_none(self):
        pool = [_meta(OTHER, ocr_has_text=True)]
        page = _page("about")

        inject_legible_images([page], _source(pool), pool)

        poster = next(b for b in page.blocks if isinstance(b, PosterBlock))
        self.assertEqual(poster.heading, "")


class ResolverTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_used_picture_is_not_picked_again_under_another_url(self):
        pool = [_meta(PEXELS, content_hash="same"), _meta(HOSTED, content_hash="same"),
                _meta(OTHER, content_hash="other")]

        class NoStock:
            async def search(self, *a, **k):
                return []

        resolver = ImageResolver(scraped_metadata=pool, pexels=NoStock())
        resolver.mark_used([PEXELS])

        with mock.patch("app.services.media.verify_one", new=mock.AsyncMock()), \
             mock.patch.object(resolver, "_rank", new=mock.AsyncMock(side_effect=lambda q, i, cands, **k: _Chosen(cands[0]))):
            picked = await resolver._take_best_scraped("office", "about", slot_usage="inline")

        self.assertIsNotNone(picked)
        self.assertEqual(picked.url, OTHER)


class ReservedUnderAnyUrlTest(unittest.IsolatedAsyncioTestCase):
    """The ref-binding pass hands back the URL it wrote into the tree, which is
    not always the pool's spelling: Pexels serves one photo at `…&h=650` and
    `…&h=650&w=940`. The pool lookup missed, only the raw URL was reserved, and
    webtree.my's home page used that photo as hero background AND feature
    card."""

    VARIANT = PEXELS + "&w=940"

    async def test_a_variant_url_still_reserves_the_picture(self):
        pool = [_meta(PEXELS), _meta(OTHER)]

        class NoStock:
            async def search(self, *a, **k):
                return []

        resolver = ImageResolver(scraped_metadata=pool, pexels=NoStock())
        resolver.mark_used([self.VARIANT])  # a spelling the pool does not hold

        with mock.patch("app.services.media.verify_one", new=mock.AsyncMock()), \
             mock.patch.object(resolver, "_rank",
                               new=mock.AsyncMock(side_effect=lambda q, i, c, **k: _Chosen(c[0]))):
            picked = await resolver._take_best_scraped("office", "about", slot_usage="inline")

        self.assertEqual(picked.url, OTHER)

    async def test_an_unknown_url_with_no_id_reserves_only_itself(self):
        pool = [_meta(OTHER)]

        class NoStock:
            async def search(self, *a, **k):
                return []

        resolver = ImageResolver(scraped_metadata=pool, pexels=NoStock())
        resolver.mark_used(["https://elsewhere.example/a.jpg"])

        with mock.patch("app.services.media.verify_one", new=mock.AsyncMock()), \
             mock.patch.object(resolver, "_rank",
                               new=mock.AsyncMock(side_effect=lambda q, i, c, **k: _Chosen(c[0]))):
            picked = await resolver._take_best_scraped("office", "about", slot_usage="inline")

        self.assertEqual(picked.url, OTHER)


class _Chosen:
    def __init__(self, meta):
        self.chosen = meta
        self.chosen_score = 1.0
        self.decision = "test"
        self.scores = []


if __name__ == "__main__":
    unittest.main()
