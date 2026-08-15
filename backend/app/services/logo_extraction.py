"""
Brand mark detection: find the *logo* on a scraped page, not the favicon.

The rule this module exists to enforce: a real logo outranks an icon, and an
og:image never renders as a brand mark at all. The previous implementation
(`scraper._extract_logo_candidate`) checked apple-touch-icon first, `og:image`
third and the actual `<img class="logo">` fourth, so any site shipping a touch
icon or a social card — i.e. most sites — never used its own logo.

Three tiers, first hit wins:

1. ``"logo"``     — a real mark: a header ``<img>``, an inline header ``<svg>``,
                    or a logo-named ``<img>`` anywhere on the page.
2. ``"icon"``     — the largest declared ``<link rel="…icon">``. Legitimate as a
                    last resort: plenty of sites draw their logo as an inline
                    ``<svg>`` we can't serialize or a CSS background we can't
                    see, and a 512px PWA icon is usually the logo mark itself.
3. ``"og-image"`` — the social card. Kept only because it carries brand colour;
                    it is a 1200x630 image with baked-in text, so it must never
                    be rendered as a logo (see `BrandIdentity.logo_render_ok`).

Provenance travels with the URL because the two consumers want different things:
the header needs a legible mark, the palette extractor just needs brand-coloured
pixels. Callers gate rendering on the source (and, for icons, on the decoded
size); they seed colour from whatever came back.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup, Tag

from app.models.brand import LogoSource
from app.services.image_evidence import parse_evidence
from app.services.image_urls import (
    _BADGE_DIR_HINTS,
    _LOGO_HINTS,
    absolute_url,
    image_src_from_tag,
    tag_classes,
)
from app.services.section_extraction import in_repeated_image_group

logger = logging.getLogger(__name__)

# Class/id tokens that mark a header brand slot on the common site builders
# (Webflow "brand", Squarespace "site-title", WordPress "custom-logo").
_BRAND_CLASS_HINTS = ("brand", "site-title", "site-logo", "custom-logo", "navbar-brand")

# A mark below this declared icon size is a browser-tab favicon, nothing more.
# Kept as the *ranking* input only — the render gate uses decoded pixels.
_ICON_USABLE_SIZE = 96

# Rendered y-coordinate under which an image counts as "in the header" on pages
# that carried Playwright evidence. Generous: sticky headers, announcement bars.
_HEADER_MAX_Y = 220

# >= this many similar-size siblings and the image is a grid cell — a partner /
# client logo wall, not the site's own mark.
_LOGO_WALL_GRID_MIN = 3

# An inline <svg> smaller than this is an interface glyph (hamburger, chevron,
# social icon), not a wordmark. Measured in serialized characters: real logo
# paths are long, icon paths are a handful of line segments.
_SVG_MIN_MARKUP_CHARS = 160

# Longer than this and an `alt` is a description, not a name. See `_label_alt`.
_ALT_LABEL_MAX_CHARS = 60


@dataclass(frozen=True)
class LogoCandidate:
    """A brand mark plus where it came from."""

    source: LogoSource
    url: str | None = None       # absolute; None when the mark is inline SVG
    data_url: str | None = None  # set for inline SVG only

    @property
    def ref(self) -> str | None:
        return self.url or self.data_url


def extract_logo(
    soup: BeautifulSoup, base_url: str, *, site_name: str | None = None
) -> LogoCandidate | None:
    """Best brand mark on the page, or None when there is nothing to use."""
    mark = _find_real_mark(soup, base_url, site_name=site_name)
    if mark is not None:
        return mark

    icon = _find_icon(soup, base_url)
    if icon is not None:
        return icon

    og = _find_og_image(soup, base_url)
    if og is not None:
        return og

    return None


# --- tier 1: a real mark --------------------------------------------------------


def _find_real_mark(
    soup: BeautifulSoup, base_url: str, *, site_name: str | None
) -> LogoCandidate | None:
    header_imgs = [img for img in soup.find_all("img") if _in_header(img)]

    # 1a. A header <img> that names itself, matches the site name, or is the
    # only image in the header (a header with exactly one image is a lockup).
    sole_header_img = header_imgs[0] if len(header_imgs) == 1 else None
    for img in header_imgs:
        if img is sole_header_img or _is_brand_img(img, site_name=site_name):
            url = _img_url(img, base_url)
            if url:
                return LogoCandidate(source="logo", url=url)

    # 1b. An inline <svg> brand mark in the header.
    for svg in soup.find_all("svg"):
        if not isinstance(svg, Tag) or not _in_header(svg):
            continue
        if not _is_brand_svg(svg):
            continue
        data_url = _svg_data_url(svg)
        if data_url:
            return LogoCandidate(source="logo", data_url=data_url)

    # 1c. A logo-named <img> anywhere — footer lockups, pages whose header is
    # built out of markup we can't identify as one.
    loose = [
        img
        for img in soup.find_all("img")
        if isinstance(img, Tag)
        and img not in header_imgs
        and _has_logo_hint(img)
        and not _in_logo_wall(img)
    ]
    for img in _drop_repeated_marks(loose):
        url = _img_url(img, base_url)
        if url:
            return LogoCandidate(source="logo", url=url)

    return None


def _drop_repeated_marks(images: list[Tag]) -> list[Tag]:
    """Remove logo-named images that repeat as a set — a wall, not the brand.

    `_in_logo_wall` needs either a `/logos/` URL or the renderer's grid stamp,
    and the fast-fetch path has neither. Styling still gives it away: a strip of
    sibling marks shares one class. BBC's homepage lists "BBC Scotland logo",
    "BBC ALBA logo", "BBC Cymru logo" and so on, all in one image class — none
    of which is the site's own mark.
    """
    groups: dict[str, list[Tag]] = {}
    for img in images:
        groups.setdefault(tag_classes(img), []).append(img)
    return [
        img
        for img in images
        if len(groups[tag_classes(img)]) < _LOGO_WALL_GRID_MIN
    ]


def _in_header(tag: Tag) -> bool:
    """True when the tag sits in page chrome — the only place a site puts its
    own logo. Structural first, then measured geometry on rendered pages."""
    for parent in tag.parents:
        if not isinstance(parent, Tag):
            continue
        if parent.name in ("header", "nav"):
            return True
        role = parent.get("role")
        if isinstance(role, str) and role.lower() in ("banner", "navigation"):
            return True
        if parent.name == "a" and _is_home_href(parent.get("href")):
            return True
        classes = tag_classes(parent)
        if any(h in classes for h in _BRAND_CLASS_HINTS):
            return True
        if parent.name == "body":
            break

    evidence = parse_evidence(tag.get("data-webtree-evidence"))
    return evidence is not None and evidence.y < _HEADER_MAX_Y


def _is_home_href(href: object) -> bool:
    if not isinstance(href, str):
        return False
    stripped = href.strip().rstrip("/")
    return stripped in ("", "#", "/", "index.html") or stripped.endswith("://")


def _is_brand_img(img: Tag, *, site_name: str | None) -> bool:
    if _has_logo_hint(img):
        return True
    classes = tag_classes(img)
    if any(h in classes for h in _BRAND_CLASS_HINTS):
        return True
    alt = _label_alt(img)
    if site_name and alt:
        return _norm(alt) == _norm(site_name) or _norm(site_name) in _norm(alt)
    return False


def _norm(text: str) -> str:
    """Fold to comparable words: 'Acme Dental — Home' vs 'acme dental'."""
    return " ".join(re.sub(r"[^\w\s]", " ", text).lower().split())


def _label_alt(img: Tag) -> str:
    """The alt text, but only when it reads as a *label* rather than prose.

    A logo's alt is a name — "Acme", "Acme Logo", "Acme — home". A photo's alt
    is a sentence, and a sentence can mention a logo without being one: BBC ran
    a news photo alt'd "…a black handbag with the Prada logo on it…", which a
    plain substring test happily crowned as the brand mark.
    """
    alt = img.get("alt")
    if not isinstance(alt, str):
        return ""
    alt = alt.strip()
    return alt if len(alt) <= _ALT_LABEL_MAX_CHARS else ""


def _has_logo_hint(img: Tag) -> bool:
    # src/class/id are machine-authored: a substring hit there is a real signal.
    structural = " ".join(
        str(img.get(attr) or "") for attr in ("src", "data-src", "class", "id")
    ).lower()
    haystack = f"{structural} {_label_alt(img).lower()}"
    return any(h in haystack for h in _LOGO_HINTS)


def _in_logo_wall(img: Tag) -> bool:
    """A tile in a partner / client / award strip, not the site's own mark.

    Three independent signals: the URL path convention (`/logos/acme.png` — a
    *directory* of other people's marks, distinct from a file named
    `logo.png`), measured grid membership stamped by the renderer, and — on the
    fast path, where no measurement exists — the DOM's own answer to the same
    question.

    The badge directories matter as much as `/logos/`: an award file is
    routinely NAMED for the award ("Logo of the 21st century the prestigious
    brand.png"), which trips every logo-name hint there is. Without this the
    medal outranked the real header mark and became the site's logo.
    """
    src = str(img.get("src") or img.get("data-src") or "").lower()
    directory = src.rsplit("/", 1)[0]
    if "/logos/" in src or any(hint in directory for hint in _BADGE_DIR_HINTS):
        return True
    evidence = parse_evidence(img.get("data-webtree-evidence"))
    if evidence is not None:
        return evidence.grid_count >= _LOGO_WALL_GRID_MIN
    return in_repeated_image_group(img, min_cells=_LOGO_WALL_GRID_MIN)


def _img_url(img: Tag, base_url: str) -> str | None:
    src = image_src_from_tag(img)
    return absolute_url(base_url, src) if src else None


def _is_brand_svg(svg: Tag) -> bool:
    """Inline SVGs in a header are mostly interface glyphs. Require an explicit
    brand marker or a home link wrapper — a hamburger inherits neither."""
    if len(str(svg)) < _SVG_MIN_MARKUP_CHARS:
        return False
    own = " ".join(
        [tag_classes(svg), str(svg.get("id") or ""), str(svg.get("aria-label") or "")]
    ).lower()
    if any(h in own for h in (*_LOGO_HINTS, *_BRAND_CLASS_HINTS)):
        return True
    parent = svg.parent
    if (
        isinstance(parent, Tag)
        and parent.name == "a"
        and _is_home_href(parent.get("href"))
        and len(parent.find_all("svg")) == 1
    ):
        return True
    return False


def _svg_data_url(svg: Tag) -> str | None:
    markup = str(svg)
    if "xmlns" not in markup:
        # A serialized inline <svg> loses the namespace the HTML parser implied;
        # without it the data URL renders blank in an <img>.
        markup = markup.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)
    encoded = base64.b64encode(markup.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# --- tier 2: declared icons -----------------------------------------------------


_SIZE_RE = re.compile(r"(\d+)\s*x\s*(\d+)", re.IGNORECASE)


def _find_icon(soup: BeautifulSoup, base_url: str) -> LogoCandidate | None:
    """Largest declared icon. `sizes` is parsed, not assumed: the old code took
    the first apple-touch-icon in DOM order, which on a legacy size list is the
    57x57 one."""
    best: tuple[int, str] | None = None
    for tag in soup.find_all("link"):
        if not isinstance(tag, Tag):
            continue
        rel = tag.get("rel")
        rel_text = " ".join(rel).lower() if isinstance(rel, list) else str(rel or "").lower()
        if "icon" not in rel_text:
            continue
        if "mask-icon" in rel_text:
            continue  # monochrome silhouette — never a usable logo
        href = tag.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        size = _declared_icon_size(tag)
        # apple-touch-icons are 180px by convention when undeclared; a bare
        # rel="icon" with no sizes is a 16/32px favicon.
        if size == 0 and "apple-touch-icon" in rel_text:
            size = 180
        if best is None or size > best[0]:
            best = (size, href)

    if best is None:
        return None
    url = absolute_url(base_url, best[1])
    return LogoCandidate(source="icon", url=url) if url else None


def _declared_icon_size(tag: Tag) -> int:
    """Largest edge declared in `sizes`. Handles multi-value lists ("16x16
    32x32") and returns 0 for "any" / missing / unparseable."""
    sizes = tag.get("sizes")
    if isinstance(sizes, list):
        sizes = " ".join(sizes)
    if not isinstance(sizes, str):
        return 0
    return max(
        (max(int(m.group(1)), int(m.group(2))) for m in _SIZE_RE.finditer(sizes)),
        default=0,
    )


# --- tier 3: og:image -----------------------------------------------------------


def _find_og_image(soup: BeautifulSoup, base_url: str) -> LogoCandidate | None:
    og = soup.find("meta", attrs={"property": "og:image"})
    if not isinstance(og, Tag):
        og = soup.find("meta", attrs={"name": "og:image"})
    if not isinstance(og, Tag):
        return None
    content = og.get("content")
    if not isinstance(content, str) or not content.strip():
        return None
    url = absolute_url(base_url, content)
    return LogoCandidate(source="og-image", url=url) if url else None


# --- render gate ----------------------------------------------------------------


# Below this decoded short side an icon is a browser-tab favicon; upscaled to
# the header's 52px lockup it reads as a blurred smudge.
_RENDER_MIN_SHORT_SIDE = 96
# A mark this much wider than it is tall is a banner or a social card, not a
# lockup the header can size by height.
_RENDER_MAX_ASPECT = 8.0


def is_renderable(
    source: LogoSource | None,
    *,
    size: tuple[int, int] | None,
    is_vector: bool = False,
) -> bool:
    """Whether a fetched mark may be rendered as the header/footer brand logo.

    `size` is the *decoded* pixel size — a `sizes` attribute is a claim, this is
    the truth. Vectors skip the size test: they have no meaningful raster size
    and scale to any lockup.
    """
    if source is None:
        return False
    if source == "og-image":
        return False
    if source == "logo" or is_vector:
        return True
    # source == "icon": only a big, roughly square icon is a usable mark.
    if size is None:
        return False
    width, height = size
    if min(width, height) < _RENDER_MIN_SHORT_SIDE:
        return False
    if height <= 0 or width / height > _RENDER_MAX_ASPECT:
        return False
    return True
