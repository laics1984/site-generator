"""`apply_section_rhythm` and its mirror in the builder.

The generator alternates a page's plain sections between the page background
and the surface tint once, at generation time. The builder paints a section the
same way as it is inserted from the Templates tab
(`builder/src/lib/section-rhythm.ts`), so a section added by hand continues the
page's rhythm instead of always opening on the page background. A token that
drifts on one side only is a band the other side cannot recognise.
"""

import pathlib
import re
import unittest

from app.models.builder_schema import BuilderElement
from app.services.section_content import _PAGE_BG, _SURFACE_BG, apply_section_rhythm

_DARK_BAND = "var(--builder-color-secondary, #0f172a)"


def _section(**styles) -> BuilderElement:
    return BuilderElement(name="Section", type="container", styles=styles, content=[])


def _plain() -> BuilderElement:
    return _section(backgroundColor=_PAGE_BG)


class SectionRhythmTest(unittest.TestCase):
    # The same page the builder's section-rhythm.test.ts paints from a Starter.
    def test_plain_sections_alternate_from_the_top_around_filled_ones(self):
        page = [_plain(), _plain(), _section(backgroundColor=_DARK_BAND), _plain(), _plain()]
        apply_section_rhythm(page)
        self.assertEqual(
            [s.styles["backgroundColor"] for s in page],
            [_PAGE_BG, _SURFACE_BG, _DARK_BAND, _PAGE_BG, _SURFACE_BG],
        )

    def test_a_photo_or_gradient_section_keeps_its_fill_and_does_not_advance(self):
        photo = _section(backgroundColor=_PAGE_BG, backgroundImage="url(https://x/p.jpg)")
        page = [photo, _plain(), _plain()]
        apply_section_rhythm(page)
        self.assertEqual(
            [s.styles["backgroundColor"] for s in page], [_PAGE_BG, _PAGE_BG, _SURFACE_BG]
        )


class MirrorTest(unittest.TestCase):
    """Skipped rather than failed when the sibling repo is absent, the same idiom
    as `test_seo.HeadingMirrorTest`."""

    @property
    def _source(self) -> str:
        here = pathlib.Path(__file__).resolve()
        if len(here.parents) <= 3:  # running from a container copy, no siblings
            self.skipTest("builder/ not checked out beside this repo")
        mirror = here.parents[3] / "builder" / "src" / "lib" / "section-rhythm.ts"
        if not mirror.exists():
            self.skipTest(f"{mirror} not checked out beside this repo")
        return mirror.read_text()

    def _constant(self, name: str) -> str:
        match = re.search(rf"const {name} = '([^']+)'", self._source)
        self.assertIsNotNone(match, f"{name} not found in the mirror")
        return match.group(1)

    def test_the_page_band_token_matches(self):
        self.assertEqual(self._constant("PAGE_BACKGROUND"), _PAGE_BG)

    def test_the_surface_band_token_matches(self):
        self.assertEqual(self._constant("SURFACE_BACKGROUND"), _SURFACE_BG)


if __name__ == "__main__":
    unittest.main()
