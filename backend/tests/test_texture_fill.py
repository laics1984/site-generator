"""A see-through fill never takes the theme texture.

Every renderer recomputes a section's grain/mesh live, and paints the theme's
default on any container whose fill is "plain" — the page or surface colour.
That gate read the fill through a resolver whose fallback is white, and which
drops an `rgba()`'s alpha, so a `transparent` card, a translucent glass card or
a `color-mix()` matched the page background and got grain tiled across its
whole box (every renderer paints a texture with `backgroundRepeat: repeat`) — a
grey card behind the copy that the design never drew.

`resolveSolidFillHex` is the gate's reader in all three renderers now — the one
opaque colour a fill paints, or null. webtree-public pins its blocks and the
builder pins its canvas in their own suites; this pins what only this repo can
see, the preview port. It also catches the drift that left the preview's grain
at 0.55 opacity after upstream and `grain_data_uri()` moved to 0.2.

Drift tests over sibling repos (the `test_self_ink.py` idiom), skipped when one
is absent.
"""

import pathlib
import re
import unittest

_REPOS = pathlib.Path(__file__).resolve().parents[3]
_SITEGEN = pathlib.Path(__file__).resolve().parents[2]

_PUBLIC_LIB = _REPOS / "webtree-public" / "lib" / "backgroundTexture.ts"
_PREVIEW = _SITEGEN / "frontend" / "src" / "preview"
_PREVIEW_LIB = _PREVIEW / "lib" / "backgroundTexture.ts"
_PREVIEW_BLOCKS = (_PREVIEW / "blocks" / "ContainerBlock.tsx", _PREVIEW / "blocks" / "SectionBlock.tsx")

# The port's one code delta: a type suppression on the Node-only Buffer branch.
_BUFFER_NOTE = re.compile(r"^  // The Buffer branch.*?\n(?=  const encoded)", re.MULTILINE | re.DOTALL)


def _read(path: pathlib.Path, test: unittest.TestCase) -> str:
    if not path.exists():
        test.skipTest(f"{path} not checked out beside this repo")
    return path.read_text()


class PreviewTextureVendoringTest(unittest.TestCase):
    def test_the_texture_lib_is_vendored_verbatim(self):
        public = _read(_PUBLIC_LIB, self)
        preview = _read(_PREVIEW_LIB, self)
        body = preview.split("\n//\n", 1)[1]  # the provenance banner
        self.assertEqual(public.replace("~/types/public", "./public"), _BUFFER_NOTE.sub("", body, count=1))

    def test_the_preview_blocks_read_their_fill_as_a_solid_colour(self):
        for block in _PREVIEW_BLOCKS:
            source = _read(block, self)
            with self.subTest(block=block.name):
                self.assertIn("resolveSolidFillHex(bgColorValue, palette, pageBackgroundHex)", source)
                self.assertNotIn("resolveColorToHex(bgColorValue", source)


if __name__ == "__main__":
    unittest.main()
