"""The photo filter is stated in three repos; this keeps them one rule.

The generator lifts its baked copy scrim into the photo filter's settings
(`image_styling.lift_photo_scrim`), and every renderer paints a shaped filter
back into the photo's layer stack (`with_photo_filter`): the builder
(src/lib/photo-filter.ts, which also lifts pages generated before the lift)
and webtree-public (lib/photoFilter.ts). A drift would paint a lifted hero
differently from how it was generated — or leave a scrim the builder cannot
read, so the owner could not change it.

The fixture is computed here and checked into both sibling repos, whose own
tests assert their mirror reproduces it case for case. Regenerate with:

    WRITE_PHOTO_FILTER_FIXTURES=1 .venv/bin/python -m pytest tests/test_photo_filter_mirror.py

Skipped when a sibling repo is absent, the `test_self_ink.py` idiom.
"""

from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

from app.models.builder_schema import BuilderElement
from app.services import image_styling as m
from app.services.schema_builder import lift_photo_scrims

_WEBTREE = Path(__file__).resolve().parents[3]
_FIXTURES = (
    _WEBTREE / "builder" / "src" / "lib" / "photo-filter.fixtures.json",
    _WEBTREE / "webtree-public" / "lib" / "photoFilter.fixtures.json",
)
_PHOTO = "https://cdn.example.com/hero.jpg"
_SCRIM_HEX = "#221d2b"
_SCRIM_TOKEN = f"var(--builder-color-photo-scrim, {_SCRIM_HEX})"
_ANCHORS = ("center", "left", "bottom-left", "bottom")
_LIST_KEYS = ("backgroundImage", "backgroundSize", "backgroundRepeat", "backgroundPosition")


def _bound(styles: dict) -> dict:
    """The stack as `bind_theme_styles` stores it: the scrim's rgba stops as
    mixes of the photo-scrim token."""
    r, g, b = m._hex_to_rgb(_SCRIM_HEX)

    def mix(match: re.Match) -> str:
        if (int(match[1]), int(match[2]), int(match[3])) != (r, g, b):
            return match[0]
        return f"color-mix(in srgb, {_SCRIM_TOKEN} {float(match[4]) * 100:g}%, transparent)"

    out = dict(styles)
    out["backgroundImage"] = re.sub(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", mix, styles["backgroundImage"])
    return out


def _generated_stacks() -> list[tuple[str, dict]]:
    stacks = []
    for anchor in _ANCHORS:
        for avg in ("#808080", "#f2efe9", "#1d1d1d"):
            for page_bg in (None, "#ffffff"):
                styles = m.photo_background(avg, _PHOTO, _SCRIM_HEX, "#2563eb", anchor=anchor, page_bg_hex=page_bg)
                name = f"{anchor} over {avg}{' with edge fade' if page_bg else ''}"
                stacks.append((name, styles))
                stacks.append((f"{name}, bound to the palette", _bound(styles)))
    return stacks


def _not_lifted() -> list[tuple[str, dict]]:
    base = m.photo_background("#808080", _PHOTO, _SCRIM_HEX, "#2563eb", anchor="left")
    sheet = "linear-gradient(rgba(0,0,0,0.45), rgba(0,0,0,0.45))"
    return [
        ("the owner's filter already set", {**base, m.PHOTO_FILTER_OPACITY: 30}),
        ("a catalog sheet, not a scrim", {"backgroundImage": f"{sheet}, url('{_PHOTO}')", "backgroundSize": "cover"}),
        ("a gradient band with no photo", {"backgroundImage": "linear-gradient(to right, rgba(34,29,43,0.5) 54%, rgba(34,29,43,0.28) 76%, rgba(34,29,43,0) 98%)"}),
        ("a hand-tuned scrim (stops moved)", {**base, "backgroundImage": base["backgroundImage"].replace(" 76%", " 70%")}),
    ]


# Heroes generated before the plateau (until 2026-10-15), as stored — the
# center case is a real site's hero, read from the CMS.
_GRAIN = 'url("data:image/svg+xml;base64,PHN2Zz4=")'
_FADE = "linear-gradient(to bottom, rgba(255,255,255,0) 72%, rgba(255,255,255,0.55) 90%, rgba(255,255,255,1) 100%)"
_LEGACY_SCRIMS = {
    "center": "radial-gradient(115% 88% at 50% 50%, rgba(5,8,15,0.2), rgba(5,8,15,0.11) 44%, rgba(5,8,15,0) 78%)",
    "left": "linear-gradient(to right, rgba(5,8,15,0.4), rgba(5,8,15,0.22) 44%, rgba(5,8,15,0) 78%)",
    "bottom-left": (
        "linear-gradient(to top, rgba(5,8,15,0.23), rgba(5,8,15,0.13) 46%, rgba(5,8,15,0) 82%), "
        "linear-gradient(to right, rgba(5,8,15,0.23), rgba(5,8,15,0.13) 46%, rgba(5,8,15,0) 82%)"
    ),
}


def _legacy_stacks() -> list[tuple[str, str, dict]]:
    tail = [
        "radial-gradient(125% 105% at 50% 42%, rgba(5,8,15,0) 45%, rgba(5,8,15,0.08) 100%)",
        "linear-gradient(135deg, rgba(5,8,15,0.16), rgba(59,30,115,0.13))",
        f"url('{_PHOTO}')",
    ]
    stacks = []
    for anchor, scrim in _LEGACY_SCRIMS.items():
        for head in ([_GRAIN], [_GRAIN, _FADE]):
            layers = [*head, *m._split_layers(scrim), *tail]
            n = len(layers)
            stacks.append((f"pre-plateau {anchor}{' with edge fade' if len(head) > 1 else ''}", anchor, {
                "backgroundImage": ", ".join(layers),
                "backgroundSize": ", ".join(["140px 140px", *["cover"] * (n - 1)]),
                "backgroundRepeat": ", ".join(["repeat", *["no-repeat"] * (n - 1)]),
                "backgroundPosition": ", ".join(["0 0", *["center"] * (n - 1)]),
            }))
    return stacks


def build_fixtures() -> dict:
    layers = [
        {"color": color, "opacity": opacity, "shape": shape, "layers": m.photo_filter_layers(color, opacity, shape)}
        for color in (_SCRIM_HEX, _SCRIM_TOKEN)
        for shape in m.PHOTO_FILTER_SHAPES
        for opacity in (0, 1, 24, 47, 62, 100)
    ]
    lift = []
    for name, styles in [*_generated_stacks(), *_not_lifted()]:
        lifted = m.lift_photo_scrim(styles)
        lift.append({
            "name": name,
            "styles": styles,
            "lifted": lifted,
            # What a renderer paints for the lifted styles: the stack as baked.
            "painted": {k: m.with_photo_filter(lifted).get(k) for k in _LIST_KEYS} if lifted else None,
        })
    legacy = []
    for name, _anchor, styles in _legacy_stacks():
        lifted = m.lift_photo_scrim(styles)
        legacy.append({
            "name": name,
            "styles": styles,
            "lifted": lifted,
            # Repainted in today's geometry — NOT the stack as baked.
            "painted": {k: m.with_photo_filter(lifted).get(k) for k in _LIST_KEYS} if lifted else None,
        })
    return {
        "_comment": "Generated by site-generator tests/test_photo_filter_mirror.py — do not edit by hand.",
        "layers": layers,
        "lift": lift,
        "legacy": legacy,
    }


def _dump(fixtures: dict) -> str:
    return json.dumps(fixtures, indent=2, sort_keys=True) + "\n"


class PhotoFilterRoundTripTest(unittest.TestCase):
    """The generator's own half: a lifted scrim paints back as it was baked."""

    def test_every_generated_scrim_is_lifted_and_paints_back_identically(self):
        for name, styles in _generated_stacks():
            with self.subTest(name):
                lifted = m.lift_photo_scrim(styles)
                self.assertIsNotNone(lifted, "a scrim the generator baked was not lifted")
                painted = m.with_photo_filter(lifted)
                for key in _LIST_KEYS:
                    self.assertEqual(painted.get(key), styles.get(key), key)

    def test_the_filter_keeps_the_scrims_colour_and_strength(self):
        styles = _bound(m.photo_background("#808080", _PHOTO, _SCRIM_HEX, "#2563eb", anchor="left"))
        lifted = m.lift_photo_scrim(styles)
        self.assertEqual(lifted[m.PHOTO_FILTER_COLOR], _SCRIM_TOKEN)
        self.assertEqual(lifted[m.PHOTO_FILTER_SHAPE], "left")
        peak = float(re.search(r"to right, color-mix\(in srgb, .+?\) ([\d.]+)%", styles["backgroundImage"])[1])
        self.assertEqual(lifted[m.PHOTO_FILTER_OPACITY], round(peak))

    def test_a_pre_plateau_scrim_is_lifted_at_its_strength_and_anchor(self):
        for name, anchor, styles in _legacy_stacks():
            with self.subTest(name):
                lifted = m.lift_photo_scrim(styles)
                self.assertIsNotNone(lifted)
                self.assertEqual(lifted[m.PHOTO_FILTER_SHAPE], anchor)
                self.assertEqual(lifted[m.PHOTO_FILTER_COLOR], "#05080f")
                kept = m._split_layers(lifted["backgroundImage"])
                self.assertFalse(any(" 44%" in layer or " 82%" in layer for layer in kept), "scrim left behind")
                self.assertEqual(len(m._split_layers(lifted["backgroundSize"])), len(kept))
                # Painted back in today's geometry, in the scrim's slot.
                painted = m._split_layers(m.with_photo_filter(lifted)["backgroundImage"])
                at = 2 if _FADE in styles["backgroundImage"] else 1
                self.assertEqual(painted[at:at + (2 if anchor == "bottom-left" else 1)],
                                 m.photo_filter_layers("#05080f", lifted[m.PHOTO_FILTER_OPACITY], anchor))
        center = m.lift_photo_scrim(_legacy_stacks()[0][2])
        self.assertEqual(center[m.PHOTO_FILTER_OPACITY], 20)

    def test_what_is_not_a_generated_scrim_stays_baked(self):
        for name, styles in _not_lifted():
            with self.subTest(name):
                self.assertIsNone(m.lift_photo_scrim(styles))

    def test_a_flat_or_clear_filter_paints_nothing_into_the_stack(self):
        styles = {"backgroundImage": f"url('{_PHOTO}')", m.PHOTO_FILTER_OPACITY: 40}
        self.assertIs(m.with_photo_filter(styles), styles)
        self.assertIs(m.with_photo_filter({**styles, m.PHOTO_FILTER_SHAPE: "left", m.PHOTO_FILTER_OPACITY: 0}).get("backgroundImage"), styles["backgroundImage"])

    def test_the_pass_lifts_nested_heroes_in_place(self):
        hero = BuilderElement(
            id="hero", name="Hero", type="section",
            styles=m.photo_background("#808080", _PHOTO, _SCRIM_HEX, "#2563eb", anchor="bottom-left"),
            content=[],
        )
        body = BuilderElement(id="__body", name="Body", type="__body", styles={}, content=[hero])
        self.assertEqual(lift_photo_scrims([body]), 1)
        self.assertEqual(hero.styles[m.PHOTO_FILTER_SHAPE], "bottom-left")
        self.assertNotIn("to top", hero.styles["backgroundImage"])


class PhotoFilterMirrorTest(unittest.TestCase):
    """The renderers' half: each sibling repo carries this exact fixture."""

    def test_sibling_fixtures_are_current(self):
        expected = _dump(build_fixtures())
        for path in _FIXTURES:
            with self.subTest(path.name):
                if not path.parent.exists():
                    self.skipTest(f"sibling repo absent: {path.parent}")
                if os.environ.get("WRITE_PHOTO_FILTER_FIXTURES"):
                    path.write_text(expected)
                self.assertTrue(path.exists(), f"{path} missing — regenerate (see module docstring)")
                self.assertEqual(path.read_text(), expected, f"{path.name} is stale — regenerate (see module docstring)")


if __name__ == "__main__":
    unittest.main()
