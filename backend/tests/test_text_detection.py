"""OCR text screening (services/text_detection.py).

Bars source images that already carry a headline from slots we draw ours over.
The engine itself is an optional wheel, so these tests pin the wiring, the
scope, and the degradation — the parts that must hold whether or not
rapidocr-onnxruntime is installed. Accuracy was validated separately against a
15-image corpus (10 real photographs, 5 text-bearing): 0.00-3.1% coverage vs
10.5-16.3%, all 15 classified correctly through this module.
"""

import asyncio
import unittest
from unittest import mock

from app.config import settings
from app.models.content_blocks import ImageMetadata
from app.services import text_detection
from app.services.image_match import bears_text
from app.services.text_detection import (
    _COVERAGE_THRESHOLD,
    _SKIP_ROLES,
    _candidates,
    ocr_enabled,
    prefetch_text_flags,
)


def _img(url, **kw):
    base = dict(
        url=url, alt="", intent="generic", role="unknown",
        width=1600, height=900, source_usage="inline",
    )
    base.update(kw)
    return ImageMetadata(**base)


class FlagWiringTest(unittest.TestCase):
    """The flag has to actually reach the veto — a screened image that nothing
    reads is worse than no screening, because it looks like it works."""

    def test_the_flag_bars_the_image(self):
        meta = _img("https://x/a.jpg")
        self.assertFalse(bears_text(meta))
        meta.ocr_has_text = True
        self.assertTrue(bears_text(meta))

    def test_ocr_and_vision_are_independent_yeses(self):
        """Different evidence, so either alone is enough. A missed banner costs
        a bad hero; a false positive costs one photo its background slot."""
        for field in ("ocr_has_text", "vision_has_text"):
            meta = _img("https://x/a.jpg")
            setattr(meta, field, True)
            self.assertTrue(bears_text(meta), msg=field)

    def test_a_measured_backdrop_does_not_override_ocr(self):
        """role="background" is an inference from the source's DOM; OCR read the
        pixels. When they disagree the pixels win."""
        meta = _img("https://x/a.jpg", role="background")
        self.assertFalse(bears_text(meta))
        meta.ocr_has_text = True
        self.assertTrue(bears_text(meta))


class DegradationTest(unittest.TestCase):
    """Never load-bearing: every failure path leaves the flag unset, which is
    exactly how the pipeline behaved before OCR existed."""

    def test_disabled_setting_is_a_no_op(self):
        self.assertFalse(ocr_enabled())  # conftest turns it off for the suite
        pool = [_img("https://x/a.jpg")]
        self.assertEqual(asyncio.run(prefetch_text_flags(pool)), {})
        self.assertIsNone(pool[0].ocr_has_text)

    def test_missing_wheel_degrades_without_raising(self):
        pool = [_img("https://x/a.jpg")]
        with mock.patch.object(settings, "ocr_text_detection_enabled", True), \
             mock.patch.object(text_detection, "_engine", return_value=None):
            self.assertEqual(asyncio.run(prefetch_text_flags(pool)), {})
        self.assertIsNone(pool[0].ocr_has_text)

    def test_an_undecodable_payload_is_skipped_not_raised(self):
        with mock.patch.object(text_detection, "_engine", return_value=object()):
            self.assertIsNone(text_detection.text_coverage(b"not an image"))

    def test_threshold_sits_between_the_measured_populations(self):
        # Clean photographs measured up to ~3.1%, text-bearing from ~10.5%.
        self.assertGreater(_COVERAGE_THRESHOLD, 0.031)
        self.assertLess(_COVERAGE_THRESHOLD, 0.105)


class ScopeTest(unittest.TestCase):
    """Source images only, and only the ones that could reach a background."""

    def test_roles_that_can_never_be_a_background_are_not_screened(self):
        pool = [_img(f"https://x/{role}.jpg", role=role) for role in _SKIP_ROLES]
        self.assertEqual(_candidates(pool, 12), [])

    def test_background_shaped_images_get_the_budget_first(self):
        content = _img("https://x/content.jpg", role="content")
        css_bg = _img("https://x/bg.jpg", source_usage="css_background")
        hero = _img("https://x/hero.jpg", role="hero", intent="hero")
        picked = _candidates([content, css_bg, hero], 2)
        self.assertNotIn(content, picked)
        self.assertEqual(len(picked), 2)

    def test_the_cap_is_honoured(self):
        pool = [_img(f"https://x/{i}.jpg", role="hero") for i in range(40)]
        self.assertEqual(len(_candidates(pool, 12)), 12)
        self.assertEqual(_candidates(pool, 0), [])

    def test_it_takes_scraped_metadata_not_stock_results(self):
        """Stock photos are PhotoResult and never become ImageMetadata, so they
        cannot be screened — Pexels ships photographs, not posters."""
        import inspect

        from app.services.pexels import PhotoResult

        sig = inspect.signature(prefetch_text_flags)
        self.assertIn("ImageMetadata", str(sig.parameters["metadata"].annotation))
        stock = PhotoResult(url="u", alt="a", photographer=None,
                            photographer_url=None, source="pexels")
        self.assertFalse(hasattr(stock, "ocr_has_text"))


class OnDemandVerificationTest(unittest.TestCase):
    """The prefetch screens a CAPPED SAMPLE. On a 12-page scrape the pool runs
    to 100+ images, so the sample is a small fraction of it and a newsletter
    scan on page nine sails through — the exact bug this backstop exists for.
    Whatever wins a background slot gets screened, sample or no sample.
    """

    def _resolver(self, pool):
        from app.services.media import ImageResolver

        return ImageResolver(scraped_metadata=pool)

    def test_an_unscreened_winner_is_verified_and_re_ranked(self):
        wordy = _img("https://x/newsletter.jpg", role="hero", intent="hero",
                     source_usage="css_background", alt="music therapy")
        clean = _img("https://x/room.jpg", role="hero", intent="hero",
                     source_usage="css_background", alt="music therapy")
        self.assertIsNone(wordy.ocr_has_text)  # prefetch never reached it

        async def fake_verify(meta):
            meta.ocr_has_text = meta.url == wordy.url
            return meta.ocr_has_text

        with mock.patch("app.services.media.verify_one", side_effect=fake_verify):
            got = asyncio.run(
                self._resolver([wordy, clean]).resolve(
                    "music therapy", intent="hero", slot_usage="background"
                )
            )
        self.assertEqual(got.url, clean.url)
        self.assertTrue(wordy.ocr_has_text)

    def test_a_pool_of_only_text_images_falls_through_to_stock(self):
        pool = [
            _img(f"https://x/promo{i}.jpg", role="hero", intent="hero",
                 source_usage="css_background", alt="music therapy")
            for i in range(3)
        ]

        async def always_text(meta):
            meta.ocr_has_text = True
            return True

        with mock.patch("app.services.media.verify_one", side_effect=always_text):
            got = asyncio.run(
                self._resolver(pool).resolve(
                    "music therapy", intent="hero", slot_usage="background"
                )
            )
        self.assertNotEqual(got.source, "scraped")

    def test_a_pinned_background_is_verified_too(self):
        """An image_ref pin bypasses ranking, and with it every filter that
        reads the flag — so the pin path screens on demand as well."""
        pinned = _img("https://x/pinned-banner.jpg", role="hero", intent="hero",
                      source_usage="css_background", alt="music therapy")

        async def always_text(meta):
            meta.ocr_has_text = True
            return True

        with mock.patch("app.services.media.verify_one", side_effect=always_text):
            got = asyncio.run(
                self._resolver([pinned]).resolve(
                    "music therapy", intent="hero", slot_usage="background",
                    pinned_url=pinned.url,
                )
            )
        self.assertNotEqual(got.source, "scraped")

    def test_inline_slots_are_never_verified(self):
        """Nothing is drawn over a featured image, so screening it would only
        spend an inference to reject a usable photo."""
        wordy = _img("https://x/promo.jpg", role="content", alt="summer promotion")
        calls = []

        async def spy(meta):
            calls.append(meta.url)
            return False

        with mock.patch("app.services.media.verify_one", side_effect=spy):
            asyncio.run(
                self._resolver([wordy]).resolve(
                    "summer promotion", intent="generic", slot_usage="inline"
                )
            )
        self.assertEqual(calls, [])

    def test_a_prescreened_image_costs_no_inference(self):
        from app.services.text_detection import verify_one

        meta = _img("https://x/known.jpg")
        meta.ocr_has_text = True
        with mock.patch.object(text_detection, "_engine") as engine:
            self.assertTrue(asyncio.run(verify_one(meta)))
            engine.assert_not_called()

    def test_the_reject_budget_is_bounded(self):
        self.assertGreaterEqual(settings.ocr_verify_budget, 1)
        self.assertLessEqual(settings.ocr_verify_budget, 8)


class BudgetTest(unittest.TestCase):
    """OCR is CPU-bound at ~630ms/image with no parallel speedup (onnxruntime
    already uses every core; thread pools measured slower). The cap is what
    keeps it inside the prefetch window, so it must stay modest."""

    def test_default_cap_stays_within_the_prefetch_window(self):
        self.assertLessEqual(settings.ocr_max_images, 16)

    def test_input_size_matches_the_vision_thumbnail(self):
        """Same size ⇒ prefetched downloads are reused verbatim, so with vision
        on the OCR pass costs no extra network."""
        from app.services.image_vision import _THUMBNAIL_PX

        self.assertEqual(settings.ocr_input_px, _THUMBNAIL_PX)


if __name__ == "__main__":
    unittest.main()
