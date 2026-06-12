import base64
import unittest
from io import BytesIO
from unittest import mock

from app.config import settings
from app.models.content_blocks import ImageMetadata
from app.services import image_vision
from app.services.image_match import rank_candidates
from app.services.image_vision import VisionAnnotation, annotate_image_pool


class FakeVisionLlm:
    """Returns queued annotations in order; records every call."""

    def __init__(self, annotations):
        self.queue = list(annotations)
        self.calls = []

    async def chat_json(self, *, system_prompt, user_prompt, schema, temperature, num_ctx, images):
        self.calls.append(images)
        return self.queue.pop(0)


def _enable_vision(test):
    patcher = mock.patch.object(settings, "ollama_vision_model", "fake-vl")
    patcher.start()
    test.addCleanup(patcher.stop)


def _fake_fetch(test, b64="ZmFrZQ==", missing=()):
    async def fetch(url):
        return None if url in missing else b64

    patcher = mock.patch.object(image_vision, "_fetch_image_b64", fetch)
    patcher.start()
    test.addCleanup(patcher.stop)


class AnnotateImagePoolTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        image_vision._ANNOTATION_CACHE.clear()

    async def test_disabled_without_vision_model(self):
        meta = [ImageMetadata(url="https://x.example/a.jpg")]

        result = await annotate_image_pool(meta, llm=FakeVisionLlm([]))

        self.assertEqual(result, {})
        self.assertIsNone(meta[0].vision_caption)

    async def test_annotates_and_enriches_metadata_in_place(self):
        _enable_vision(self)
        _fake_fetch(self)
        meta = [ImageMetadata(url="https://cdn.example/abc123.jpg", alt="")]
        llm = FakeVisionLlm(
            [
                VisionAnnotation(
                    caption="dentist treating a patient in a clinic",
                    kind="photo",
                    people_count=2,
                )
            ]
        )

        result = await annotate_image_pool(meta, llm=llm)

        self.assertIn("https://cdn.example/abc123.jpg", result)
        self.assertEqual(meta[0].vision_caption, "dentist treating a patient in a clinic")
        self.assertEqual(meta[0].vision_kind, "photo")
        self.assertEqual(meta[0].vision_people, 2)
        self.assertEqual(len(llm.calls), 1)

    async def test_respects_image_cap_and_annotates_extra_urls(self):
        _enable_vision(self)
        _fake_fetch(self)
        meta = [
            ImageMetadata(url=f"https://x.example/{i}.jpg") for i in range(3)
        ]
        llm = FakeVisionLlm([VisionAnnotation(caption=f"c{i}", kind="photo") for i in range(4)])

        result = await annotate_image_pool(
            meta, extra_urls=["https://x.example/portrait.jpg"], llm=llm, max_images=2
        )

        self.assertEqual(len(result), 2)  # cap wins over pool + extras

    async def test_fetch_failure_skips_image_gracefully(self):
        _enable_vision(self)
        _fake_fetch(self, missing={"https://x.example/broken.jpg"})
        meta = [
            ImageMetadata(url="https://x.example/broken.jpg"),
            ImageMetadata(url="https://x.example/ok.jpg"),
        ]
        llm = FakeVisionLlm([VisionAnnotation(caption="fine", kind="photo")])

        result = await annotate_image_pool(meta, llm=llm)

        self.assertEqual(list(result), ["https://x.example/ok.jpg"])
        self.assertIsNone(meta[0].vision_caption)

    async def test_cached_urls_skip_the_llm(self):
        _enable_vision(self)
        _fake_fetch(self)
        meta = [ImageMetadata(url="https://x.example/a.jpg")]
        llm = FakeVisionLlm([VisionAnnotation(caption="once", kind="photo")])

        await annotate_image_pool(meta, llm=llm)
        again = await annotate_image_pool(meta, llm=llm)

        self.assertEqual(len(llm.calls), 1)
        self.assertEqual(again["https://x.example/a.jpg"].caption, "once")


class FetchImageTest(unittest.IsolatedAsyncioTestCase):
    async def test_data_url_is_decoded_and_thumbnailed(self):
        from PIL import Image

        buf = BytesIO()
        Image.new("RGB", (900, 600), (10, 120, 200)).save(buf, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

        result = await image_vision._fetch_image_b64(data_url)

        self.assertIsNotNone(result)
        assert result is not None
        with Image.open(BytesIO(base64.b64decode(result))) as out:
            self.assertEqual(out.format, "JPEG")
            self.assertLessEqual(max(out.size), 512)

    async def test_undecodable_payload_returns_none(self):
        bad = "data:image/svg+xml;base64," + base64.b64encode(b"<svg/>").decode()
        self.assertIsNone(await image_vision._fetch_image_b64(bad))


class ImageMatchVisionTest(unittest.TestCase):
    def test_caption_rescues_hash_named_image_with_no_alt(self):
        # No alt, hashed filename — lexically invisible without the caption.
        candidate = ImageMetadata(
            url="https://cdn.example/9f8e7d6c.jpg",
            alt="",
            vision_caption="dentist treating a patient in a modern clinic",
        )

        result = rank_candidates("clinic patient care", "generic", [candidate])

        self.assertIs(result.chosen, candidate)

    def test_banner_vision_kind_is_not_intent_pinned_as_hero(self):
        banner = ImageMetadata(
            url="https://cdn.example/promo.jpg",
            alt="",
            intent="hero",
            vision_kind="banner",
        )

        result = rank_candidates("dental clinic team", "hero", [banner])

        self.assertNotEqual(result.decision, "intent-pinned")
        self.assertIsNone(result.chosen)

    def test_photo_vision_kind_still_pins_as_hero(self):
        photo = ImageMetadata(
            url="https://cdn.example/team.jpg",
            alt="",
            intent="hero",
            vision_kind="photo",
        )

        result = rank_candidates("dental clinic team", "hero", [photo])

        self.assertEqual(result.decision, "intent-pinned")
        self.assertIs(result.chosen, photo)


if __name__ == "__main__":
    unittest.main()
