"""
Rendered-image evidence: parse the geometry stamps left by the Playwright
render pass and classify each image into a visual role.

The render pass (scraper._stamp_render_evidence) stamps every <img> with a
`data-webtree-evidence` attribute — and every CSS-background element with
`data-webtree-bg-evidence` — containing measured geometry as compact JSON:

    {"nw": naturalWidth, "nh": naturalHeight,   # intrinsic bitmap size
     "x": left, "y": top,                        # document coordinates
     "w": renderedWidth, "h": renderedHeight,    # layout box
     "vw": viewportWidth, "vh": viewportHeight,
     "grid": similarSiblingCount,                # >=3 ⇒ card/portrait grid
     "text": overlaidTextLength}                 # bg elements only

`classify_role` turns that into a role the matcher can trust:

    background  — CSS background with real text rendered over it
    hero        — large and above the fold; the page's lead visual
    portrait    — small square-ish cell in a repeating grid (team/committee)
    gallery     — repeating-grid cell that isn't a portrait
    content     — substantive in-flow image (a featured/supporting visual)
    decoration  — tiny or an extreme strip; excluded from the pool entirely

Evidence only exists when the page went through Playwright. The httpx fast
path leaves no stamps — parse_evidence returns None and the scraper falls
back to its legacy DOM-order heuristics, so behaviour there is unchanged.

Deterministic + dependency-light on purpose — no LLM call, unit-testable alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal

ImageRole = Literal[
    "hero", "background", "content", "gallery", "portrait", "logo", "decoration", "unknown"
]


# --- thresholds -------------------------------------------------------------------

# Below this rendered box the image can't be content — icons, badges, pixels.
_MIN_RENDER_W = 96
_MIN_RENDER_H = 72

# Wide-and-flat strips (dividers, award banners, marquee logos) are decoration.
_STRIP_ASPECT = 4.5
_STRIP_MAX_H = 140

# Hero: element starts within the first ~60% of the viewport (tolerates sticky
# navs) AND either covers a meaningful share of it or spans near-full width.
_HERO_TOP_FRACTION = 0.6
_HERO_MIN_COVERAGE = 0.18
_HERO_MIN_WIDTH_FRACTION = 0.8
_HERO_MIN_HEIGHT_FRACTION = 0.35

# Text rendered inside a CSS-background element ⇒ the image is a backdrop.
_BG_MIN_TEXT_CHARS = 24

# Repeating-grid membership (computed by the stamper: >=3 sibling cells with
# one similar-area image each). Square-ish small cells are portrait grids.
_GRID_MIN = 3
_PORTRAIT_ASPECT_RANGE = (0.6, 1.6)
_PORTRAIT_MAX_W = 420

# Minimum rendered area for a standalone in-flow image to count as content.
_CONTENT_MIN_AREA = 160 * 120


@dataclass(frozen=True)
class ImageEvidence:
    """Measured geometry for one rendered image element."""

    natural_width: int | None  # intrinsic bitmap size; None when not loaded
    natural_height: int | None
    x: int  # document coordinates of the layout box
    y: int
    width: int  # rendered size
    height: int
    viewport_width: int
    viewport_height: int
    grid_count: int = 0  # similar-size sibling images (>=3 ⇒ grid cell)
    text_length: int = 0  # chars of text rendered inside (bg elements only)

    @property
    def coverage(self) -> float:
        """Rendered area as a fraction of the viewport area."""
        return (self.width * self.height) / (self.viewport_width * self.viewport_height)

    @property
    def above_fold(self) -> bool:
        return self.y < self.viewport_height * _HERO_TOP_FRACTION

    @property
    def aspect(self) -> float | None:
        """Rendered width/height ratio. None when height is zero."""
        if self.height <= 0:
            return None
        return self.width / self.height


def parse_evidence(raw: object) -> ImageEvidence | None:
    """Parse a stamped evidence attribute. None on anything malformed —
    callers treat that exactly like the fast-fetch path (no evidence)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    def _int(key: str) -> int | None:
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return int(value)

    viewport_w = _int("vw")
    viewport_h = _int("vh")
    width = _int("w")
    height = _int("h")
    x = _int("x")
    y = _int("y")
    if not viewport_w or not viewport_h or viewport_w <= 0 or viewport_h <= 0:
        return None
    if width is None or height is None or x is None or y is None:
        return None

    natural_w = _int("nw") or None  # 0 ⇒ image never loaded ⇒ unknown
    natural_h = _int("nh") or None
    return ImageEvidence(
        natural_width=natural_w,
        natural_height=natural_h,
        x=x,
        y=y,
        width=max(0, width),
        height=max(0, height),
        viewport_width=viewport_w,
        viewport_height=viewport_h,
        grid_count=max(0, _int("grid") or 0),
        text_length=max(0, _int("text") or 0),
    )


def classify_role(evidence: ImageEvidence, *, is_background: bool = False) -> ImageRole:
    """Map measured geometry to a visual role. Rules are ordered: cheap
    disqualifiers first, then the most specific positive signals."""
    width, height = evidence.width, evidence.height

    if width < _MIN_RENDER_W or height < _MIN_RENDER_H:
        return "decoration"
    aspect = evidence.aspect
    if aspect is not None and aspect >= _STRIP_ASPECT and height < _STRIP_MAX_H:
        return "decoration"

    if is_background and evidence.text_length >= _BG_MIN_TEXT_CHARS:
        return "background"

    if evidence.above_fold and (
        evidence.coverage >= _HERO_MIN_COVERAGE
        or (
            width >= evidence.viewport_width * _HERO_MIN_WIDTH_FRACTION
            and height >= evidence.viewport_height * _HERO_MIN_HEIGHT_FRACTION
        )
    ):
        return "hero"

    if evidence.grid_count >= _GRID_MIN:
        lo, hi = _PORTRAIT_ASPECT_RANGE
        if aspect is not None and lo <= aspect <= hi and width <= _PORTRAIT_MAX_W:
            return "portrait"
        return "gallery"

    if width * height >= _CONTENT_MIN_AREA:
        return "content"
    return "decoration"
