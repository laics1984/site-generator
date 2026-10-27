"""A menu heading is one rule in three renderers (webtree-public
lib/menuDisclosure.ts, vendored by the preview, mirrored by the builder).

The generator writes a navigation category that is no page with an empty href
(menu_builder.plan_nav_groups); every renderer must draw it as a button that
opens its list. A renderer whose predicate drifted would draw it as a link to
nowhere on one surface only — the preview, the editor or the published site.
The three cannot import one another, so these are drift tests over sibling
repos (the `test_self_ink.py` idiom), skipped when one is absent.
"""

import pathlib
import re
import unittest

_REPOS = pathlib.Path(__file__).resolve().parents[3]
_SITEGEN = pathlib.Path(__file__).resolve().parents[2]

_PUBLIC = _REPOS / "webtree-public" / "lib" / "menuDisclosure.ts"
_PREVIEW = _SITEGEN / "frontend" / "src" / "preview" / "lib" / "menuDisclosure.ts"
_BUILDER = _REPOS / "builder" / "src" / "lib" / "menu-disclosure.ts"


def _read(path: pathlib.Path, test: unittest.TestCase) -> str:
    if not path.exists():
        test.skipTest(f"{path} not checked out beside this repo")
    return path.read_text()


def _heading_rule(source: str) -> str:
    match = re.search(r"export function isMenuHeading\([^)]*\)[^{]*\{(.*?)\n\}", source, re.S)
    assert match, "isMenuHeading not found"
    return re.sub(r"\s+", " ", match.group(1)).strip()


class MenuHeadingMirrorTest(unittest.TestCase):
    def test_the_preview_vendors_the_renderer_verbatim(self):
        public = _read(_PUBLIC, self)
        preview = _read(_PREVIEW, self)
        # Everything after the two-line vendoring banner.
        self.assertEqual(preview.split("//\n", 1)[1], public)

    def test_the_builder_draws_the_same_headings(self):
        self.assertEqual(
            _heading_rule(_read(_BUILDER, self)), _heading_rule(_read(_PUBLIC, self))
        )

    def test_the_generator_writes_what_the_renderers_read_as_a_heading(self):
        # menu_builder's heading href; the predicate's own first case.
        self.assertIn("href === ''", _heading_rule(_read(_PUBLIC, self)))
