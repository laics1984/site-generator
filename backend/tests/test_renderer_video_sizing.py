"""An embed's frame defers to the height its node authored.

`VideoBlock`'s inner `.wt-video-block__frame` carried an unconditional
`aspect-ratio: 16 / 9`. A node's own styles land on the OUTER box and never
reach that frame, so an authored `height` was silently ignored and the frame
sized itself from its width alone. Wherever `width x 9/16` exceeded the
authored height the frame overflowed downward and — being positioned — painted
*over* the next sibling.

`locations-map-cards` was the only video node in the catalog authored with a
fixed height and no ratio, so it was the only one that could diverge: at 1280px
its map wanted ~307px against an authored 230px and covered 77px of the branch
address. Every other embed template states `aspectRatio` and no height, which
happened to agree with the frame — which is why this went unseen.

The builder canvas is where it was invisible: it renders one box and honours the
authored height, so it had the opposite bug (it ignored `aspectRatio` and fell
back to a hard-coded 315px). Three renderers, three different answers to one
question.

Nothing here is generated — this is renderer chrome the schema cannot see — so
these are drift tests over sibling repos, the same idiom as `test_self_ink` and
`test_design_schemes.test_pinned_name_mirror_matches_the_renderer`. Skipped
rather than failed when a sibling repo is absent.
"""

import json
import pathlib
import re
import unittest

_REPOS = pathlib.Path(__file__).resolve().parents[3]
_SITEGEN = pathlib.Path(__file__).resolve().parents[2]

_PUBLIC_VIDEO = _REPOS / "webtree-public" / "components" / "blocks" / "VideoBlock.vue"
_BUILDER_VIDEO = (
    _REPOS / "builder" / "src" / "components" / "tabs" / "editor-components" / "video.tsx"
)
_PREVIEW_CSS = _SITEGEN / "frontend" / "src" / "preview" / "preview.css"

_CATALOG = _SITEGEN / "backend" / "app" / "templates" / "section_catalog.json"


def _read(path: pathlib.Path, test: unittest.TestCase) -> str:
    if not path.exists():
        test.skipTest(f"{path} not checked out beside this repo")
    return path.read_text()


def _rule_body(css: str, selector: str) -> str:
    """The declarations of the first rule with this exact selector."""
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return match.group(1) if match else ""


class VideoFrameSizingTest(unittest.TestCase):
    def test_public_frame_defers_to_an_authored_height(self):
        body = _rule_body(_read(_PUBLIC_VIDEO, self), ".wt-video-block__frame")
        self.assertIn("aspect-ratio", body, "the ratio is still the fallback")
        self.assertRegex(
            body,
            r"height:\s*100%",
            "without `height: 100%` the frame ignores the node's authored height "
            "and overflows onto the next sibling",
        )

    def test_preview_mirrors_the_public_frame(self):
        # preview.css is GENERATED — `node scripts/vendor-preview-css.mjs
        # ../../webtree-public` from frontend/. A hand-edit here is the drift
        # this test exists to catch.
        body = _rule_body(_read(_PREVIEW_CSS, self), ".wt-video-block__frame")
        self.assertRegex(body, r"height:\s*100%")
        self.assertIn("aspect-ratio", body)

    def test_builder_canvas_reads_aspect_ratio(self):
        source = _read(_BUILDER_VIDEO, self)
        self.assertIn(
            "styles.aspectRatio",
            source,
            "the canvas hard-defaulted 315px and never read the ratio, so every "
            "ratio-sized embed rendered at a different height there",
        )


class EmbedNodesStateOneSizingAxisTest(unittest.TestCase):
    """The catalog's own half of the contract.

    A video node states a ratio OR a height, never both and never neither. Both
    is ambiguous across the three renderers; neither leaves the size to whichever
    default each renderer happens to carry.
    """

    def test_every_catalog_video_node_states_exactly_one(self):
        catalog = json.loads(_CATALOG.read_text())
        offenders: list[tuple[str, str, str]] = []

        def walk(node: dict, section_id: str) -> None:
            if node.get("type") == "video":
                styles = node.get("styles") or {}
                has_ratio = bool(styles.get("aspectRatio"))
                has_height = bool(styles.get("height"))
                if has_ratio == has_height:
                    offenders.append(
                        (section_id, node.get("name", "?"), "both" if has_ratio else "neither")
                    )
            content = node.get("content")
            if isinstance(content, list):
                for child in content:
                    walk(child, section_id)

        for section in catalog["sections"]:
            walk(section["tree"], section["id"])

        self.assertEqual(offenders, [], f"video nodes with ambiguous sizing: {offenders}")


if __name__ == "__main__":
    unittest.main()
