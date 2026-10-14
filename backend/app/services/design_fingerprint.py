"""
Design fingerprint — what a finished site LOOKS like, as a few comparable tokens.

The diversity history used to record each design area on its own (header,
footer, palette, scheme, hero), so it could say "this header was used last
time" but never "this site looks like that one". Two kindergartens sharing a
palette, a font, a hero, a section sequence and a band rhythm were five
unrelated coincidences to it. A fingerprint is the whole design reduced to the
tokens a visitor would notice, so sameness is one number.

Read from the BUILT site, never from the decisions that were meant to produce
it: a hero directive can degrade to another template when its image doesn't
resolve, and a scheme's tint can be overruled by a contrast pass. The tree is
what ships, so the tree is what is compared. That also makes it work on a site
that came back from the frontend, where ``theme`` and ``design_manifest`` are
plain dicts rather than models.

Nothing here stores content — no copy, no URLs, no names beyond the brand key
the history already uses. Only design tokens.

Pure and deterministic: no I/O, no settings.
"""

from __future__ import annotations

import colorsys
import json
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass, fields, replace
from functools import lru_cache
from typing import Any

from app.models.builder_schema import BuilderElement, GeneratedSite
from app.services.template_filler import load_catalog
from app.services.theme import has_brand_hue

# A hue bucket is 30 degrees wide: coarse enough that two "blues" match, fine
# enough that sky and navy-violet don't. -1 means no usable hue (grey/black).
_HUE_BUCKETS = 12
_NEUTRAL_HUE = -1
# Whether a light band is TINTED is the opposite case: a pastel has little
# chroma by construction, and HLS saturation is what still sees its colour.
_MIN_TINT_SATURATION = 0.18

# How much each token counts toward "looks the same". The hero, the colour and
# the type are what a visitor registers first; the section sequence is the
# page's structure; chrome and finish details matter less individually.
_WEIGHTS: dict[str, float] = {
    "hero": 0.14,
    "header": 0.06,
    "footer": 0.05,
    "scheme": 0.08,
    "hue": 0.13,
    "canvas": 0.05,
    "heading_font": 0.13,
    "radius": 0.04,
    "sections": 0.15,
    "bands": 0.09,
    "divider": 0.04,
    "title_inks": 0.04,
}


@dataclass(frozen=True)
class DesignFingerprint:
    """The look of one generated site. Every field is a design token."""

    hero: str = ""  # homepage hero template id
    header: str = ""
    footer: str = ""
    scheme: str = ""  # design scheme slug, "" when schemes are off
    hue: int = _NEUTRAL_HUE  # primary colour's hue bucket
    canvas: str = "white"  # page background: white | tinted | dark
    heading_font: str = ""
    radius: str = ""  # square | soft | round | pill
    sections: tuple[str, ...] = ()  # homepage body template ids, hero excluded
    bands: tuple[str, ...] = ()  # homepage band per section: photo/dark/light/tint
    divider: str = "none"  # first shaped edge on the homepage
    title_inks: int = 0  # distinct coloured title hues on light bands (0 = plain, capped 3)

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"), sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> DesignFingerprint | None:
        """Parse a stored fingerprint; None for anything unreadable. Unknown keys
        are ignored and missing ones default, so the format can grow."""
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        known = {f.name for f in fields(cls)}
        kept = {k: v for k, v in data.items() if k in known}
        for key in ("sections", "bands"):
            if key in kept:
                kept[key] = tuple(kept[key] or ())
        try:
            return cls(**kept)
        except TypeError:
            return None


# --- similarity ----------------------------------------------------------------


def _sequence_similarity(a: Sequence[str], b: Sequence[str]) -> float:
    """1 − normalised edit distance. Order matters: the same sections in a
    different order is a different page rhythm, though a related one."""
    if not a and not b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        current = [i]
        for j, right in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return 1.0 - previous[-1] / max(len(a), len(b))


def _hue_similarity(a: int, b: int) -> float:
    if a == b:
        return 1.0
    if _NEUTRAL_HUE in (a, b):
        return 0.0
    distance = min((a - b) % _HUE_BUCKETS, (b - a) % _HUE_BUCKETS)
    return 0.5 if distance == 1 else 0.0


# How many recent sites a new design is compared against — by the design
# direction while choosing, and by the manifest's report afterwards.
FINGERPRINT_WINDOW = 12

# The tokens a THEME already fixes, before a page exists — what a design concept
# can be compared on while it is still a candidate (services/design_direction).
THEME_FIELDS: tuple[str, ...] = ("scheme", "hue", "canvas", "heading_font", "radius")


def field_similarity(
    a: DesignFingerprint, b: DesignFingerprint, fields: tuple[str, ...] | None = None
) -> dict[str, float]:
    """Per-token similarity in [0, 1] — what a report shows to say WHY two
    sites look alike. ``fields`` restricts the comparison (default: all)."""
    scores: dict[str, float] = {}
    for name in fields or tuple(_WEIGHTS):
        left, right = getattr(a, name), getattr(b, name)
        if name in ("sections", "bands"):
            scores[name] = _sequence_similarity(left, right)
        elif name == "hue":
            scores[name] = _hue_similarity(left, right)
        else:
            scores[name] = 1.0 if left == right else 0.0
    return scores


def similarity(
    a: DesignFingerprint, b: DesignFingerprint, fields: tuple[str, ...] | None = None
) -> float:
    """Weighted look-alike score in [0, 1]; 1 is the same design. With
    ``fields``, the score over those tokens only (re-normalised)."""
    scores = field_similarity(a, b, fields)
    total = sum(_WEIGHTS[name] for name in scores)
    return sum(_WEIGHTS[name] * score for name, score in scores.items()) / total


def nearest(
    fingerprint: DesignFingerprint,
    others: Sequence[DesignFingerprint],
    fields: tuple[str, ...] | None = None,
) -> tuple[float, DesignFingerprint | None]:
    """The highest similarity to any of ``others`` and the one it belongs to;
    (0.0, None) when there is nothing to compare against."""
    best, match = 0.0, None
    for other in others:
        score = similarity(fingerprint, other, fields)
        if score > best:
            best, match = score, other
    return best, match


def parse_all(raws: Sequence[str]) -> list[DesignFingerprint]:
    """Stored fingerprints, unreadable ones skipped."""
    return [fp for raw in raws if (fp := DesignFingerprint.from_json(raw)) is not None]


# --- extraction ----------------------------------------------------------------


@lru_cache(maxsize=1)
def _template_id_by_root_name() -> dict[str, str]:
    """Catalog root name → template id. Body sections' root names are unique in
    the catalog (only the chrome shares "Site Header"/"Site Footer"), which is
    what lets a built tree name the template each section came from."""
    names: dict[str, str] = {}
    for entry in load_catalog()["sections"]:
        name = (entry.get("tree") or {}).get("name")
        if name and not entry["id"].startswith("chrome-"):
            names.setdefault(name, entry["id"])
    return names


def template_id_of(section: BuilderElement) -> str:
    """The catalog template a built body section came from ("" when it did not
    come from one) — the one answer for every reader of a finished tree."""
    return _template_id_by_root_name().get(section.name or "", "")


def _get(obj: Any, name: str, default: Any = None) -> Any:
    """Read a field from a model or its serialized dict — a site posted back by
    the frontend carries theme and manifest as plain dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _rgb(hex_color: str | None) -> tuple[float, float, float] | None:
    raw = (hex_color or "").strip().lstrip("#")
    if len(raw) == 3:
        raw = "".join(ch * 2 for ch in raw)
    if len(raw) != 6:
        return None
    try:
        r, g, b = (int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except ValueError:
        return None
    return r, g, b


def _hls(hex_color: str | None) -> tuple[float, float, float] | None:
    rgb = _rgb(hex_color)
    return colorsys.rgb_to_hls(*rgb) if rgb else None


def hue_bucket(hex_color: str | None) -> int:
    """The colour's 30-degree hue bucket, or -1 when it has no usable hue
    (`theme.has_brand_hue` — the same test that decides whether a logo leads)."""
    rgb = _rgb(hex_color)
    if rgb is None or not has_brand_hue(hex_color):
        return _NEUTRAL_HUE
    return int(colorsys.rgb_to_hls(*rgb)[0] * _HUE_BUCKETS) % _HUE_BUCKETS


def _canvas(hex_color: str | None) -> str:
    hls = _hls(hex_color)
    if hls is None:
        return "white"
    if hls[1] < 0.35:
        return "dark"
    if hls[1] > 0.97 or hls[2] < 0.12:
        return "white"
    return "tinted"


def _radius_class(radius: Any) -> str:
    try:
        value = float(radius)
    except (TypeError, ValueError):
        return ""
    if value <= 6:
        return "square"
    if value <= 14:
        return "soft"
    if value <= 24:
        return "round"
    return "pill"


def _walk(element: BuilderElement) -> Iterator[BuilderElement]:
    yield element
    if isinstance(element.content, list):
        for child in element.content:
            yield from _walk(child)


def _band(section: BuilderElement, theme_lights: set[str]) -> str:
    """photo | dark | light | tint. "light" is the theme's own page or surface
    colour; "tint" is a coloured light fill the theme does not carry — what a
    multi-tint scheme paints, and what makes a page read multi-coloured."""
    styles = section.styles or {}
    if "url(" in str(styles.get("backgroundImage") or styles.get("background") or ""):
        return "photo"
    classes = section.classes or ""
    if "wt-band-dark" in classes:
        return "dark"
    fill = styles.get("backgroundColor")
    hls = _hls(fill) if isinstance(fill, str) else None
    if (
        hls is not None
        and hls[2] >= _MIN_TINT_SATURATION
        and (fill or "").lower() not in theme_lights
    ):
        return "tint"
    return "light"


def _css_colour(value: Any) -> str | None:
    """A literal hex, or the fallback inside ``var(--token, #hex)``."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text.startswith("var(") and "," in text:
        text = text.split(",", 1)[1].rstrip(") ").strip()
    return text if text.startswith("#") else None


def _title_inks(sections: Sequence[BuilderElement], bands: Sequence[str]) -> int:
    """How many distinct COLOURED hues the page's light-band section titles
    use — 0 when every title is the plain ink. A dark band's title is white by
    necessity and a neutral ink carries no hue, so neither says anything about
    whether a design colours its headings."""
    hues: set[int] = set()
    for section, band in zip(sections, bands):
        if band not in ("light", "tint"):
            continue
        for node in _walk(section):
            if node.type == "text" and node.name == "Heading":
                hue = hue_bucket(_css_colour((node.styles or {}).get("color")))
                if hue != _NEUTRAL_HUE:
                    hues.add(hue)
                break  # the section's own title only, never its cards
    return min(3, len(hues))


def _first_divider(sections: Sequence[BuilderElement]) -> str:
    for section in sections:
        divider = section.divider
        for edge in (divider.top, divider.bottom) if divider else ():
            if edge is not None:
                return str(edge.shape)
    return "none"


def fingerprint_of_theme(theme: Any) -> DesignFingerprint:
    """The THEME_FIELDS of a fingerprint, read off a theme (model or dict) —
    all a design concept has before its pages are built."""
    palette = _get(theme, "palette")
    typography = _get(theme, "typography")
    heading_font = str(_get(typography, "heading_font", "") or _get(typography, "headingFont", ""))
    return DesignFingerprint(
        scheme=str(_get(theme, "design_scheme") or ""),
        hue=hue_bucket(_get(palette, "primary")),
        canvas=_canvas(_get(palette, "background")),
        heading_font=heading_font.split(",")[0].strip("'\" "),
        radius=_radius_class(_get(_get(theme, "buttons"), "radius")),
    )


def fingerprint_of(site: GeneratedSite) -> DesignFingerprint:
    """The fingerprint of a built site, read from its homepage and theme."""
    home = next((p for p in site.pages if p.is_homepage), site.pages[0] if site.pages else None)
    sections = list(home.body_schema.elements) if home else []
    ids = [template_id_of(section) for section in sections]
    hero_index = next((i for i, tid in enumerate(ids) if tid.startswith("hero-")), None)
    body = [tid for i, tid in enumerate(ids) if i != hero_index and tid]

    theme = site.theme
    palette = _get(theme, "palette")
    page_background = _get(palette, "background")
    theme_lights = {
        str(c).lower() for c in (page_background, _get(palette, "surface")) if c
    }
    body_sections = [s for i, s in enumerate(sections) if i != hero_index]
    bands = tuple(_band(section, theme_lights) for section in body_sections)
    manifest = site.design_manifest or {}
    themed = fingerprint_of_theme(theme)

    return replace(
        themed,
        hero=ids[hero_index] if hero_index is not None else "",
        header=str(manifest.get("header_archetype") or ""),
        footer=str(manifest.get("footer_archetype") or ""),
        scheme=str(manifest.get("design_scheme") or themed.scheme),
        sections=tuple(body),
        bands=bands,
        divider=_first_divider(sections),
        title_inks=_title_inks(body_sections, bands),
    )
