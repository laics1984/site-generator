"""Slot-usage gating in the image ranker (services/image_match.py).

An image the SOURCE site used as a CSS background must never be cropped into
an inline slot (split-hero side image, feature card), and must win a
background slot over inline photos — that's how "a background stays a
background" survives the pipeline.
"""

import unittest

from app.models.content_blocks import ImageMetadata
from app.services.image_match import rank_candidates


def _img(url, *, intent="generic", source_usage="unknown", alt="", w=1200, h=800, role="unknown"):
    return ImageMetadata(
        url=url, alt=alt, intent=intent, source_usage=source_usage,
        width=w, height=h, role=role,
    )


class InlineSlotTest(unittest.TestCase):
    def test_css_background_never_wins_an_inline_slot_even_intent_pinned(self):
        bg = _img(
            "https://x/bg.jpg", intent="hero", source_usage="css_background",
            w=2400, h=1400,
        )
        result = rank_candidates("community volunteers", "hero", [bg], slot_usage="inline")
        self.assertIsNone(result.chosen)

    def test_inline_slot_falls_through_to_the_inline_photo(self):
        bg = _img(
            "https://x/bg.jpg", intent="hero", source_usage="css_background",
            w=2400, h=1400,
        )
        photo = _img(
            "https://x/team.jpg", intent="hero", source_usage="inline",
            alt="volunteers", w=1200, h=800,
        )
        result = rank_candidates("volunteers", "hero", [bg, photo], slot_usage="inline")
        self.assertIsNotNone(result.chosen)
        self.assertEqual(result.chosen.url, "https://x/team.jpg")


class BackgroundSlotTest(unittest.TestCase):
    def test_css_background_pinned_ahead_of_a_larger_inline_photo(self):
        bg = _img(
            "https://x/bg.jpg", intent="hero", source_usage="css_background",
            w=1400, h=800,
        )
        bigger_inline = _img(
            "https://x/big.jpg", intent="hero", source_usage="inline",
            alt="volunteers", w=3000, h=2000,
        )
        result = rank_candidates(
            "volunteers", "hero", [bigger_inline, bg], slot_usage="background"
        )
        self.assertEqual(result.chosen.url, "https://x/bg.jpg")
        self.assertEqual(result.decision, "intent-pinned")

    def test_background_slot_without_css_background_keeps_size_order(self):
        # Sizes span _size_bonus tiers (<800 vs >=800) so the ordinary
        # size-led pin ordering is observable.
        small = _img("https://x/small.jpg", intent="hero", source_usage="inline", w=500, h=350)
        big = _img("https://x/big.jpg", intent="hero", source_usage="inline", w=2400, h=1600)
        result = rank_candidates("anything", "hero", [small, big], slot_usage="background")
        self.assertEqual(result.chosen.url, "https://x/big.jpg")


class PortraitVetoTest(unittest.TestCase):
    """A grid headshot (role=portrait) must never fill a hero/about slot or any
    background — a face blown up behind hero text is the classic directory-page
    scrape failure — while staying rankable for ordinary content slots."""

    def test_portrait_never_wins_a_background_slot_whatever_its_size(self):
        headshot = _img(
            "https://x/face.jpg", intent="hero", role="portrait",
            alt="music therapist", w=1600, h=1600,
        )
        result = rank_candidates(
            "music therapist", "hero", [headshot], slot_usage="background"
        )
        self.assertIsNone(result.chosen)

    def test_portrait_excluded_from_primary_inline_slots(self):
        headshot = _img(
            "https://x/face.jpg", intent="hero", role="portrait",
            alt="music therapist", w=1600, h=1600, source_usage="inline",
        )
        for slot_intent in ("hero", "about"):
            result = rank_candidates(
                "music therapist", slot_intent, [headshot], slot_usage="inline"
            )
            self.assertIsNone(result.chosen, f"portrait won the {slot_intent} slot")

    def test_portrait_still_ranks_for_generic_feature_slots(self):
        headshot = _img(
            "https://x/face.jpg", role="portrait",
            alt="smiling therapist", w=1600, h=1600,
        )
        result = rank_candidates("smiling therapist", "feature", [headshot])
        self.assertIsNotNone(result.chosen)
        self.assertEqual(result.chosen.url, "https://x/face.jpg")

    def test_gallery_role_excluded_from_hero_background(self):
        cell = _img(
            "https://x/grid-cell.jpg", intent="hero", role="gallery",
            alt="clinic photo", w=2000, h=1400,
        )
        result = rank_candidates("clinic photo", "hero", [cell], slot_usage="background")
        self.assertIsNone(result.chosen)

    def test_background_slot_falls_through_to_the_real_banner(self):
        headshot = _img(
            "https://x/face.jpg", intent="hero", role="portrait",
            alt="therapist", w=1600, h=1600,
        )
        banner = _img(
            "https://x/banner.jpg", intent="hero", source_usage="css_background",
            alt="hands on piano", w=1920, h=800,
        )
        result = rank_candidates("therapist", "hero", [headshot, banner], slot_usage="background")
        self.assertIsNotNone(result.chosen)
        self.assertEqual(result.chosen.url, "https://x/banner.jpg")


class LegacyAnyTest(unittest.TestCase):
    def test_any_slot_usage_matches_the_default_ranking(self):
        # Regression guard: "any" must behave exactly like the pre-slot_usage
        # ranker — css_background candidates stay ordinary contenders.
        candidates = [
            _img("https://x/bg.jpg", intent="hero", source_usage="css_background", w=2400, h=1400),
            _img("https://x/team.jpg", intent="hero", source_usage="inline", alt="volunteers"),
        ]
        default = rank_candidates("volunteers", "hero", candidates)
        explicit = rank_candidates("volunteers", "hero", candidates, slot_usage="any")
        self.assertEqual(default.chosen.url, explicit.chosen.url)
        self.assertEqual(default.decision, explicit.decision)
        self.assertEqual(
            [s.candidate.url for s in default.scores],
            [s.candidate.url for s in explicit.scores],
        )


if __name__ == "__main__":
    unittest.main()
