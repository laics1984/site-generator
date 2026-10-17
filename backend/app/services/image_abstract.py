"""
Is this picture OF something, or is it a surface? The one answer.

A featured slot — a split hero's picture, an About image, a card, a gallery
tile — shows the reader something the business is or does. An abstract image
(a gradient, a texture, a wallpaper, bokeh, a 3D render, the generator's own
gradient placeholder) shows nothing, so it may only sit BEHIND copy: a
full-bleed hero or CTA background, or the split hero's wash. Every gate that
fills a featured slot asks `is_abstract`; nothing else decides it.

Three kinds of evidence, none of them pixels:

* **Words** — what the source, the vision pass or Pexels said the image is
  (alt, caption, vision caption, platform alt, Pexels page slug, filename).
  A head term must survive removing a trailing "on/against/with … background"
  clause: "child painting on textured paper" is a child painting; "Textured
  Blue Surface" is a surface. Bare "background" is never a cue — "product on a
  white background" is a product photo.
* **Declared kind** — a vision `graphic` is an illustration, icon OR pattern, so
  it counts only when its words agree; for a graphic weaker words do (a "blue
  background design" is a backdrop once we know it is not a photograph).
  Illustrations and diagrams stay content.
* **Provenance** — a theme-matched texture from `resolve_abstract_bg`
  (`PhotoResult.is_abstract`) or the gradient placeholder.

There is deliberately no pixel vote: separating a texture from a photograph by
pixel statistics needs thresholds calibrated on real stock results, which have
not been measured. So an unlabelled CSS background the vision pass never saw is
not recognised as abstract.

`theme_fits` answers the second question a SOURCE background raises: an
abstract the theme did not choose must at least wear the theme's colours.
Photographs are never colour-judged — the caller only asks for abstracts.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

if TYPE_CHECKING:
    from app.models.content_blocks import ImageMetadata
    from app.services.pexels import PhotoResult


# Head terms that name a surface rather than a subject.
_ABSTRACT_TERM_RE = re.compile(
    r"\b(?:abstract|gradients?|bokeh|wallpapers?|textures?|textured|backdrops?"
    r"|patterns?|geometric|marble|swirls?|iridescent|holographic|defocused"
    r"|3d render(?:s|ed|ing)?|3d shapes?|(?:fluid|liquid) art)\b"
)
# Extra cues that count only once the vision pass has said "graphic" — on a
# photograph each of these is ordinary ("a blue background", "waves").
_GRAPHIC_TERM_RE = re.compile(
    r"\b(?:backgrounds?|shapes?|lines|waves?|design|curves?|stripes?|dots|blobs?)\b"
)
# "… on / against / over / in front of / with … background": the image is OF
# whatever precedes the clause, and the clause names what it stands on.
_TRAILING_SETTING_RE = re.compile(
    r"\s(?:on|against|over|in front of|with)\s.*?"
    r"\b(?:background|backdrop|surface|wall|paper|texture)s?$"
)
_SEPARATORS_RE = re.compile(r"[-_/+.,;:()\[\]]+")
_TRAILING_ID_RE = re.compile(r"(?:\s\d+)+$")
_SPACES_RE = re.compile(r"\s+")


def _normalise(text: str | None) -> str:
    """Lower-cased words, separators as spaces, a trailing photo id dropped
    (a Pexels slug ends in one: "abstract-blue-texture-1910236")."""
    if not text:
        return ""
    words = _SPACES_RE.sub(" ", _SEPARATORS_RE.sub(" ", text.lower())).strip()
    return _TRAILING_ID_RE.sub("", words)


def _subject_of(text: str | None) -> str:
    """What the description is OF, with a trailing setting clause removed."""
    return _TRAILING_SETTING_RE.sub("", _normalise(text))


def abstract_text(text: str | None) -> bool:
    """Whether a description names a surface, not a subject. Also how an
    image_query is judged before the resolver ever sees it."""
    return bool(_ABSTRACT_TERM_RE.search(_subject_of(text)))


def _graphic_text(text: str | None) -> bool:
    subject = _subject_of(text)
    return bool(_ABSTRACT_TERM_RE.search(subject) or _GRAPHIC_TERM_RE.search(subject))


def _filename_words(url: str | None) -> str:
    """The filename stem as words — "abstract-blue-bg.jpg" says what it is.
    Never a data: URL (its "path" is the payload)."""
    if not url or url.startswith("data:"):
        return ""
    try:
        path = urlparse(url).path
    except ValueError:
        return ""
    stem = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    return unquote(stem)


def _meta_texts(meta: ImageMetadata) -> Iterable[str]:
    yield meta.alt or ""
    yield meta.caption or ""
    yield meta.vision_caption or ""
    yield meta.platform_alt or ""
    yield _filename_words(meta.url)


def is_abstract(
    meta: ImageMetadata | None = None, *, photo: PhotoResult | None = None
) -> bool:
    """Whether the image is a surface (gradient, texture, wallpaper, bokeh…)
    rather than a picture OF something. A featured slot never shows one."""
    if photo is not None:
        if photo.is_abstract or photo.source == "placeholder":
            return True
        if abstract_text(photo.alt) or abstract_text(photo.description):
            return True
    if meta is not None:
        judge = _graphic_text if meta.vision_kind == "graphic" else abstract_text
        if any(judge(text) for text in _meta_texts(meta)):
            return True
    return False


# --- colour fit -------------------------------------------------------------------

# Under this share of chromatic pixels an image reads as neutral (grey, white,
# black, beige) and sits with any theme.
NEUTRAL_SHARE = 0.15
# How far a hue may sit from a palette hue and still read as that colour family.
HUE_TOLERANCE = 40.0
# How much of an image's colour may sit off every palette hue before it clashes.
OFF_THEME_SHARE = 0.25


def hue_of(hex_color: str) -> float:
    from app.services.image_styling import _hue_sat

    return _hue_sat(hex_color)[0]


def _hue_delta(a: float, b: float) -> float:
    return abs(((a - b + 180.0) % 360.0) - 180.0)


def theme_fits(
    hues: Sequence[float],
    palette_hexes: Iterable[str],
    *,
    chromatic_share: float = 1.0,
) -> bool:
    """Whether an abstract image's colours belong to the theme.

    `hues` are the image's chromatic pixel hues (`PhotoSample.hues`) and
    `chromatic_share` the share of its pixels that carry a hue at all. A
    near-neutral image fits any theme; otherwise at most `OFF_THEME_SHARE` of
    its colour may sit further than `HUE_TOLERANCE` from every chromatic palette
    colour. Which palette colours carry a hue is `theme.has_brand_hue` — chroma,
    not HLS saturation, which would read a near-black ink as a hue.
    """
    from app.services.theme import has_brand_hue

    if not hues or chromatic_share < NEUTRAL_SHARE:
        return True
    anchors = [hue_of(h) for h in palette_hexes if has_brand_hue(h)]
    if not anchors:
        return False  # a coloured surface on a palette with no colour of its own
    off = sum(1 for h in hues if min(_hue_delta(h, a) for a in anchors) > HUE_TOLERANCE)
    return off / len(hues) <= OFF_THEME_SHARE


# --- the generator's own placeholder ----------------------------------------------


def is_placeholder_src(src: str | None) -> bool:
    """Whether `src` is `media._placeholder_photo`'s gradient — an aurora of
    radial hotspots under a grain filter, percent-encoded. The theme grain
    (`style_tokens.grain_data_uri`) shares the filter but is base64 with no
    hotspots; monograms and icons have neither."""
    if not src or not src.startswith("data:image/svg+xml;utf8,"):
        return False
    body = unquote(src)
    return "feTurbulence" in body and "radialGradient" in body
