"""The article/event list lays out by its own width, and inks its headings to read.

Two defects met on the listing pages, both renderer-side, both invisible from
the schema:

- **Layout by the window.** The list's columns, card direction and type steps
  were media queries (640/768/1024px). The builder canvas simulates a device by
  width, not by viewport, so its Mobile and Tablet previews drew the desktop
  grid; and one breakpoint table stranded a 1024px tablet in a four-up grid of
  201px cards. Every rule now answers to the list's OWN width — container
  queries and a self-sizing grid — so the canvas and the published page agree by
  construction. Only the section's gutter stays on the viewport, where every
  other section's is.
- **Headings inked `secondary`.** On a dark palette `secondary` is the darkest
  band colour: Feruni's titles measured 1.04:1. `--builder-color-heading` is
  `secondary` where it reads (4.5:1) and `text` where it doesn't — the
  generator's own `text if dark else secondary`, answered from the colours
  because the scheme never reaches the wire.

The three renderers cannot import one another, so these are drift tests over
sibling repos (the `test_self_ink.py` idiom), skipped when one is absent.
"""

import pathlib
import re
import unittest

_REPOS = pathlib.Path(__file__).resolve().parents[3]
_SITEGEN = pathlib.Path(__file__).resolve().parents[2]

_PUBLIC_LIST = _REPOS / "webtree-public" / "components" / "blocks" / "CmsListBlock.vue"
_PUBLIC_ARCHIVE = _REPOS / "webtree-public" / "components" / "blocks" / "CmsArchiveHeaderBlock.vue"
_PUBLIC_STYLES = _REPOS / "webtree-public" / "lib" / "styles.ts"
_BUILDER_LIST = _REPOS / "builder" / "src" / "components" / "tabs" / "editor-components" / "cms-list.tsx"
_BUILDER_ARCHIVE = (
    _REPOS / "builder" / "src" / "components" / "tabs" / "editor-components" / "cms-archive-header.tsx"
)
_BUILDER_LAYOUT = _REPOS / "builder" / "src" / "lib" / "cms-list-layout.ts"
_BUILDER_STYLES = _REPOS / "builder" / "src" / "lib" / "builder-styles.ts"
_PREVIEW_CSS = _SITEGEN / "frontend" / "src" / "preview" / "preview.css"
_PREVIEW_STYLES = _SITEGEN / "frontend" / "src" / "preview" / "lib" / "styles.ts"


def _read(path: pathlib.Path, test: unittest.TestCase) -> str:
    if not path.exists():
        test.skipTest(f"{path} not checked out beside this repo")
    return path.read_text()


def _squash(css: str) -> str:
    """One spelling of a CSS value: no line breaks, no padding inside parens."""
    css = re.sub(r"\s+", " ", css).strip()
    return re.sub(r"\(\s+", "(", re.sub(r"\s+\)", ")", css))


def _public_container_queries(css: str) -> set[tuple[str, str, str]]:
    return set(re.findall(r"@container (wt-cms-[\w-]+) \(width (>=|<) ([\d.]+rem)\)", css))


class ListLayoutMirrorTest(unittest.TestCase):
    def test_the_canvas_grid_is_the_published_declaration(self):
        vue = _read(_PUBLIC_LIST, self)
        layout = _read(_BUILDER_LAYOUT, self)

        published = re.search(r"grid-template-columns:\s*(repeat\(\s*auto-fill.*?\));", vue, re.S)
        mirrored = re.search(r"CMS_GRID_TEMPLATE_COLUMNS =\s*'([^']+)'", layout)
        self.assertIsNotNone(published, "the published list grid no longer self-sizes")
        self.assertIsNotNone(mirrored)
        self.assertEqual(_squash(published.group(1)), _squash(mirrored.group(1)))

    def test_the_grids_are_parameterised_alike(self):
        vue = _read(_PUBLIC_LIST, self)
        layout = _read(_BUILDER_LAYOUT, self)

        def block(selector: str) -> str:
            # A rule whose selector list starts with `selector` — not a later
            # line of a grouped selector.
            match = re.search(r"(?m)(?<!,\n)^" + re.escape(selector) + r"\s*\{([^}]*)\}", vue)
            self.assertIsNotNone(match, selector)
            return match.group(1)

        def var(body: str, name: str) -> str:
            return re.search(rf"--wt-cms-{name}:\s*([^;]+);", body).group(1).strip()

        grid = block(".wt-cms-list__grid")
        four = block(".wt-cms-list__grid[data-grid-density='four']")
        featured = block(".wt-cms-list__featured")
        shared = block(".wt-cms-list__grid,\n.wt-cms-list__featured")

        self.assertIn(f"'--wt-cms-gap' as string]: '{var(shared, 'gap')}'", layout)
        self.assertIn(
            f"gridStyle('{var(grid, 'column-min')}', itemCount >= 4 ? "
            f"{var(four, 'columns-max')} : {var(grid, 'columns-max')})",
            layout,
        )
        self.assertIn(
            f"gridStyle('{var(featured, 'column-min')}', {var(featured, 'columns-max')})", layout
        )

    def test_the_canvas_asks_the_same_container_questions(self):
        published = _public_container_queries(_read(_PUBLIC_LIST, self))
        canvas = {
            (name, ">=" if bound == "min" else "<", width)
            for bound, width, name in re.findall(
                r"@(min|max)-\[([\d.]+rem)\]/(wt-cms-[\w-]+):", _read(_BUILDER_LIST, self)
            )
        }
        self.assertEqual(published, canvas)
        self.assertTrue(published, "the published list asks no container questions")

    def test_nothing_in_the_list_answers_to_the_window(self):
        vue = _read(_PUBLIC_LIST, self)
        # The section's own gutter is the one viewport rule, like every section's.
        for media in re.finditer(r"@media[^{]*\{((?:[^{}]*\{[^}]*\})*)\s*\}", vue):
            selectors = set(re.findall(r"([^{}]+?)\s*\{", media.group(1)))
            self.assertEqual({".wt-cms-list"}, {s.strip() for s in selectors}, media.group(0))

        self.assertEqual(
            [],
            re.findall(r"(?<![\w@/-])(?:sm|md|lg|xl|2xl):[\w\[\]/%.-]+", _read(_BUILDER_LIST, self)),
            "a viewport breakpoint in the canvas list answers for the wrong device",
        )

    def test_the_preview_carries_the_published_rules(self):
        # preview.css is generated (vendor-preview-css.mjs); this catches a
        # renderer edit that was never re-vendored.
        self.assertEqual(
            _public_container_queries(_read(_PUBLIC_LIST, self)),
            _public_container_queries(_read(_PREVIEW_CSS, self)),
        )


class HeadingInkTest(unittest.TestCase):
    def test_no_list_or_archive_heading_inks_with_the_raw_secondary(self):
        for path in (_PUBLIC_LIST, _PUBLIC_ARCHIVE, _BUILDER_LIST, _BUILDER_ARCHIVE):
            source = _read(path, self)
            with self.subTest(path=path.name):
                self.assertNotIn("--builder-color-secondary", source)
                self.assertIn("--builder-color-heading", source)

    def test_every_token_builder_derives_the_heading_ink(self):
        public = _read(_PUBLIC_STYLES, self)
        builder = _read(_BUILDER_STYLES, self)

        self.assertIn(
            "'--builder-color-heading': headingInk(secondaryColor, textColor, backgroundColor)",
            public,
        )
        self.assertIn("['--builder-color-heading' as string]: headingInk(builderStyles.colors)", builder)
        for source in (public, builder):
            self.assertRegex(source, r"contrastRatio\(secondary, background\) < 4\.5")

    def test_the_preview_token_builder_is_vendored_verbatim(self):
        public = _read(_PUBLIC_STYLES, self)
        preview = _read(_PREVIEW_STYLES, self)
        body = preview.split("\n", 2)[2]  # the two-line provenance header
        self.assertEqual(public.replace("~/types/public", "./public"), body)


if __name__ == "__main__":
    unittest.main()
