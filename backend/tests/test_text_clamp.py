"""The line-clamp style is the whole clamp declaration.

A clamped text node (a team-member bio) is truncated inline and offered a
"Show more" toggle by every renderer. That behaviour used to be declared
TWICE — a `wt-clamp` marker class beside the inline `WebkitLineClamp` — so one
behaviour had two sources of truth, and the half an editor could reach (the
style) was not the half the renderers read (the class). The line count could
never be changed from the builder, which had no clamp handling at all.

The style is the signal now. Sites published before that change ship the class
AND the style, so they keep their toggle with no regeneration — which is why
the class check could be dropped rather than kept alongside.

Three renderers answer the question and none can import the others': Vue in
webtree-public, its React port in the generator's preview, and a hand-written
mirror on the builder canvas. So this is a drift test over sibling repos, the
same idiom as `test_self_ink.py` and `test_grid_orphan.py` — skipped rather
than failed when a sibling repo is absent, since the backend must stay
testable on its own.
"""

import pathlib
import unittest

_REPOS = pathlib.Path(__file__).resolve().parents[3]
_SITEGEN = pathlib.Path(__file__).resolve().parents[2]

# The one style property that declares a clamp, in every repo that reads it.
_CLAMP_PROPERTY = "WebkitLineClamp"

# The shared predicate, one home per renderer.
_PREDICATE_HOMES = (
    _REPOS / "webtree-public" / "lib" / "blockRuntime.ts",
    _SITEGEN / "frontend" / "src" / "preview" / "lib" / "blockRuntime.ts",
    _REPOS / "builder" / "src" / "lib" / "text-clamp.ts",
)

# Every renderer that decides whether to offer the toggle.
_TOGGLE_MARKUP = (
    _REPOS / "webtree-public" / "components" / "blocks" / "TextBlock.vue",
    _SITEGEN / "frontend" / "src" / "preview" / "blocks" / "TextBlock.tsx",
    _REPOS / "builder" / "src" / "components" / "tabs" / "editor-components" / "text.tsx",
)


def _read(path: pathlib.Path, test: unittest.TestCase) -> str:
    if not path.exists():
        test.skipTest(f"{path} not checked out beside this repo")
    return path.read_text(encoding="utf-8")


class ClampPredicateMirrorTest(unittest.TestCase):
    def test_every_renderer_reads_the_same_style_property(self):
        for path in _PREDICATE_HOMES:
            with self.subTest(path.name):
                source = _read(path, self)
                self.assertIn(
                    _CLAMP_PROPERTY,
                    source,
                    f"{path} no longer derives the clamp from {_CLAMP_PROPERTY}",
                )
                self.assertIn(
                    "getClampLines",
                    source,
                    f"{path} lost the shared predicate name",
                )

    def test_no_renderer_still_greps_for_the_marker_class(self):
        # The marker is gone from the catalog, so a renderer still keyed on it
        # would silently stop offering the toggle on every newly generated site
        # — the exact silent-off-switch shape this repo keeps rediscovering.
        for path in _PREDICATE_HOMES + _TOGGLE_MARKUP:
            with self.subTest(path.name):
                source = _read(path, self)
                for line in source.splitlines():
                    stripped = line.strip()
                    # Comments explain the history; code must not test for it.
                    if stripped.startswith(("*", "//", "/*", "#")):
                        continue
                    self.assertNotIn(
                        "wt-clamp\\b",
                        line,
                        f"{path} still matches the retired marker class",
                    )
                    self.assertNotIn(
                        "'wt-clamp'",
                        line,
                        f"{path} still matches the retired marker class",
                    )

    def test_every_renderer_lifts_the_clamp_when_expanded(self):
        # Expanding writes `unset`, which the predicate reads back as "no
        # clamp". A renderer that only hid the button would leave the text cut.
        # The builder routes that through the shared `expandedClampStyles`
        # rather than restating the value, which is the better of the two.
        for path in _TOGGLE_MARKUP:
            with self.subTest(path.name):
                source = _read(path, self)
                self.assertTrue(
                    "unset" in source or "expandedClampStyles" in source,
                    f"{path} never lifts the clamp, so Show more cannot reveal anything",
                )


class CatalogClampTest(unittest.TestCase):
    def test_the_generated_catalog_declares_the_clamp_as_styles_only(self):
        from app.services.template_filler import get_template

        def walk(node):
            yield node
            content = node.get("content")
            if isinstance(content, list):
                for child in content:
                    yield from walk(child)

        for template_id, node_name in (
            ("team-grid", "Member Bio"),
            ("team-founders", "Founder Bio"),
        ):
            with self.subTest(template_id):
                tree = get_template(template_id)["tree"]
                bio = next(n for n in walk(tree) if n.get("name") == node_name)
                self.assertEqual(bio["styles"][_CLAMP_PROPERTY], "4")
                self.assertNotIn("wt-clamp", bio.get("classes") or "")
