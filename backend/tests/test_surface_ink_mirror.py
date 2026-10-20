"""The surface-ink rule is stated in three repos; this keeps them one rule.

The copy inks are ONE derivation, the colour system's ink-light / ink-dark
(app/services/color_system.py, mirrored by colorSystem.ts in webtree-public, the
builder and the preview, pinned by shared fixtures): the generator writes them
(theme._neutral_inks) and every renderer resolves the tokens from it. The photo
sheet's cap (image_styling._COPY_MAX_COVER) is mirrored by the builder's
surface-ink.ts. A drift would make the builder re-ink copy to a colour the
generator never picks, or a renderer paint `--builder-color-ink-light` as
something else. Skipped when a
sibling repo is absent, the `test_self_ink.py` idiom.
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from app.services import image_styling, theme
from app.services.section_content import _LITERAL_WHITE
from app.services.template_filler import CATALOG_PATH

_WEBTREE = Path(__file__).resolve().parents[3]
_BUILDER_STYLES = _WEBTREE / "builder" / "src" / "lib" / "builder-styles.ts"
_BUILDER_SURFACE_INK = _WEBTREE / "builder" / "src" / "lib" / "surface-ink.ts"
_PUBLIC_STYLES = _WEBTREE / "webtree-public" / "lib" / "styles.ts"


def _read(path: Path, case: unittest.TestCase) -> str:
    if not path.exists():
        case.skipTest(f"sibling repo absent: {path}")
    return path.read_text()


def _number(source: str, name: str) -> float:
    match = re.search(rf"\b{name}\s*=\s*([\d.]+)", source)
    assert match, f"{name} not found"
    return float(match.group(1))


_PUBLIC_COLOR_SYSTEM = _WEBTREE / "webtree-public" / "lib" / "colorSystem.ts"


def assert_one_derivation(case: unittest.TestCase, *roles: str) -> None:
    """Both token builders take every colour role from the colour system
    (colorSystem.ts, byte-identical in the builder and the preview), always,
    and set none of them by hand; and that derivation defines each role the
    generator writes."""
    for path in (_BUILDER_STYLES, _PUBLIC_STYLES):
        source = _read(path, case)
        case.assertIn("semanticColorVars(", source, path.name)
        case.assertNotIn("colorSystemVersion === 2 ?", source, f"{path.name} still gates the colour system")
        for role in roles:
            case.assertNotRegex(source, rf"'--builder-color-{role}'[^\n]*?:", f"{path.name} sets {role} by hand")
    # The TypeScript derivation is pinned to Python's by the shared numerical
    # fixtures (colorSystem.fixtures.json), so its roles are derive()'s.
    _read(_PUBLIC_COLOR_SYSTEM, case)
    from app.services.color_system import derive
    derived = derive(theme.build_theme("#0284c7").palette.model_dump())
    for role in roles:
        case.assertIn(role, derived, f"the colour system does not derive {role}")


class InkConstantsMirrorTest(unittest.TestCase):
    def test_the_photo_sheet_is_the_generators_cap(self):
        source = _read(_BUILDER_SURFACE_INK, self)
        self.assertEqual(_number(source, "COPY_MAX_COVER"), image_styling._COPY_MAX_COVER)

    def test_both_renderers_derive_the_tokens_the_generator_writes(self):
        palette = theme.build_theme("#0284c7").palette
        light, dark = theme._neutral_inks(palette)
        written = {
            theme.ink_css(palette, light).split(",")[0].removeprefix("var("),
            theme.ink_css(palette, dark).split(",")[0].removeprefix("var("),
        }
        self.assertEqual(written, {"--builder-color-ink-light", "--builder-color-ink-dark"})
        assert_one_derivation(self, "ink-light", "ink-dark")


class CatalogCarriesNoHardCodedWhiteTest(unittest.TestCase):
    """Every line of copy the catalog ships is a palette token — a white is the
    palette's to choose (`--builder-color-ink-light`), never a literal."""

    def test_no_text_or_link_is_coloured_a_literal_white(self):
        catalog = json.loads(CATALOG_PATH.read_text())
        found: list[str] = []

        def walk(node, section_id):
            if isinstance(node, dict):
                styles = node.get("styles") or {}
                color = styles.get("color")
                if (
                    node.get("type") in ("text", "link")
                    and isinstance(color, str)
                    and _LITERAL_WHITE.match(color)
                ):
                    found.append(f"{section_id}/{node.get('name')}: {color}")
                for value in node.values():
                    walk(value, section_id)
            elif isinstance(node, list):
                for item in node:
                    walk(item, section_id)

        for section in catalog["sections"]:
            walk(section["tree"], section["id"])
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
