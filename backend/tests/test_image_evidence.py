import json
import unittest

from app.models.content_blocks import ImageMetadata
from app.services.image_evidence import ImageEvidence, classify_role, parse_evidence
from app.services.image_match import rank_candidates


def _stamp(**overrides) -> str:
    """A valid evidence JSON string with sensible defaults (1280x800 viewport)."""
    base = {"nw": 0, "nh": 0, "x": 0, "y": 0, "w": 0, "h": 0, "vw": 1280, "vh": 800}
    base.update(overrides)
    return json.dumps(base)


def _evidence(**overrides) -> ImageEvidence:
    parsed = parse_evidence(_stamp(**overrides))
    assert parsed is not None
    return parsed


class ParseEvidenceTest(unittest.TestCase):
    def test_parses_valid_stamp(self):
        ev = parse_evidence(_stamp(nw=1600, nh=900, y=40, w=1280, h=640, grid=2, text=12))

        self.assertIsNotNone(ev)
        assert ev is not None
        self.assertEqual(ev.natural_width, 1600)
        self.assertEqual(ev.natural_height, 900)
        self.assertEqual(ev.width, 1280)
        self.assertEqual(ev.grid_count, 2)
        self.assertEqual(ev.text_length, 12)

    def test_zero_natural_size_means_unknown(self):
        ev = parse_evidence(_stamp(nw=0, nh=0, w=300, h=200))

        assert ev is not None
        self.assertIsNone(ev.natural_width)
        self.assertIsNone(ev.natural_height)

    def test_returns_none_for_missing_or_malformed_input(self):
        self.assertIsNone(parse_evidence(None))
        self.assertIsNone(parse_evidence(""))
        self.assertIsNone(parse_evidence("not json"))
        self.assertIsNone(parse_evidence("[1, 2]"))
        self.assertIsNone(parse_evidence(["list", "attr"]))

    def test_returns_none_without_viewport(self):
        raw = json.dumps({"nw": 800, "nh": 600, "x": 0, "y": 0, "w": 800, "h": 600})
        self.assertIsNone(parse_evidence(raw))
        self.assertIsNone(parse_evidence(_stamp(vw=0)))

    def test_coverage_and_fold(self):
        ev = _evidence(w=1280, h=400, y=100)

        self.assertAlmostEqual(ev.coverage, 0.5)
        self.assertTrue(ev.above_fold)
        self.assertFalse(_evidence(w=1280, h=400, y=700).above_fold)


class ClassifyRoleTest(unittest.TestCase):
    def test_large_above_fold_image_is_hero(self):
        self.assertEqual(classify_role(_evidence(w=1280, h=600, y=0)), "hero")

    def test_wide_banner_above_fold_is_hero_by_width(self):
        # 1100x300 ≈ 32% coverage but spans the viewport — still the lead visual.
        self.assertEqual(classify_role(_evidence(w=1100, h=300, y=100)), "hero")

    def test_substantive_below_fold_image_is_content(self):
        self.assertEqual(classify_role(_evidence(w=600, h=400, y=2000)), "content")

    def test_background_with_text_over_it(self):
        ev = _evidence(w=1280, h=500, y=1200, text=85)
        self.assertEqual(classify_role(ev, is_background=True), "background")

    def test_background_without_text_above_fold_is_hero(self):
        ev = _evidence(w=1280, h=620, y=0, text=0)
        self.assertEqual(classify_role(ev, is_background=True), "hero")

    def test_tiny_image_is_decoration(self):
        self.assertEqual(classify_role(_evidence(w=64, h=64, y=300)), "decoration")

    def test_thin_strip_is_decoration(self):
        self.assertEqual(classify_role(_evidence(w=1200, h=100, y=900)), "decoration")

    def test_square_grid_cell_is_portrait(self):
        ev = _evidence(w=240, h=240, y=1800, grid=4)
        self.assertEqual(classify_role(ev), "portrait")

    def test_wide_grid_cell_is_gallery(self):
        ev = _evidence(w=500, h=300, y=1500, grid=6)
        self.assertEqual(classify_role(ev), "gallery")

    def test_above_fold_avatar_grid_is_portrait_not_hero(self):
        ev = _evidence(w=160, h=160, y=120, grid=4)
        self.assertEqual(classify_role(ev), "portrait")


class ImageMatchRoleGateTest(unittest.TestCase):
    """Measured decoration/logo roles never fill a slot, whatever their score."""

    def test_decoration_role_is_excluded_from_ranking(self):
        decoration = ImageMetadata(
            url="https://example.my/sprite-dental-clinic-team.jpg",
            alt="dental clinic team",
            role="decoration",
        )

        result = rank_candidates("dental clinic team", "generic", [decoration])

        self.assertIsNone(result.chosen)
        self.assertEqual(result.decision, "fallback")

    def test_content_role_with_same_score_is_chosen(self):
        content = ImageMetadata(
            url="https://example.my/photos/dental-clinic-team.jpg",
            alt="dental clinic team",
            role="content",
        )

        result = rank_candidates("dental clinic team", "generic", [content])

        self.assertIs(result.chosen, content)


if __name__ == "__main__":
    unittest.main()
