"""
The linear shape a non-crawled source is read into: ordered, level-tagged text
blocks plus the images sitting between them.

``doc_structure.split_into_pages`` turns one of these into page-shaped
``SourceContent``, so every reader that wants the document → pages behaviour
builds a ``ParsedDocument``: PDF and DOCX in ``doc_parser``, pasted copy and
pasted markup in ``paste_source``. Those dataclasses lived in ``doc_parser``
until a second reader needed them — which would have made a paste depend on
PyMuPDF for three dataclasses. They describe a parsed source, not a PDF, so
they live here and ``doc_parser`` imports them.

``read_html`` is the other half: ONE DOM walk that yields those same ordered
blocks (heading level kept) plus the images anchored between them. The crawler
takes its recall spine from ``text_blocks`` here, so "which tags carry content"
and "which tags are chrome" have exactly one answer for every reader.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from bs4 import BeautifulSoup, Tag

from app.services.image_urls import (
    absolute_url,
    image_src_from_tag,
    looks_like_icon,
    upgrade_source_image_url,
)


# --- the parsed-source shape ----------------------------------------------------


ParsedSourceKind = Literal["pdf", "docx", "paste"]


@dataclass
class OutlineBlock:
    """One block of the source in reading order.

    ``level`` is the heading level: ``1``/``2``/``3`` for headings (1 = most
    prominent), ``0`` for body text. Preserving order + level is what lets
    ``doc_structure`` group body copy under the right title — the flat
    ``raw_text``/``headings`` fields lose that association.
    """

    level: int  # 0 = body, 1/2/3 = heading levels
    text: str


@dataclass
class DocImage:
    """An image extracted in document order, as bytes.

    ``anchor`` is ``len(outline)`` at the moment the image was encountered, i.e.
    it sits *after* ``outline[anchor - 1]``. That lets ``doc_structure`` tie each
    image to the page (and nearest heading) it appears under, instead of dumping
    every image on the homepage. ``width``/``height`` (pixels) drive both quality
    filtering and the matcher's size bonus.
    """

    data: bytes
    mime: str
    width: int | None = None
    height: int | None = None
    anchor: int = 0


@dataclass
class ParsedDocument:
    source_kind: ParsedSourceKind
    source_ref: str           # original filename, or a label for a paste
    title: str | None
    raw_text: str
    headings: list[str] = field(default_factory=list)
    images: list[DocImage] = field(default_factory=list)  # in document order, w/ dims + anchor
    outline: list[OutlineBlock] = field(default_factory=list)  # ordered blocks w/ heading level


# --- HTML → outline -------------------------------------------------------------


# Block-level tags whose text is content. Anything else is a wrapper.
HTML_BLOCK_TAGS = (
    "p", "li", "blockquote", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "dt", "dd", "td", "th", "summary",
)

# Containers stripped before the walk — chrome, not content.
HTML_CHROME_TAGS = ("script", "style", "noscript", "template", "svg", "nav", "footer")

# h4-h6 all collapse to 3: doc_structure only opens pages at level 1-2, and a
# deeply nested heading is in-page detail either way. Mirrors the PDF reader's
# `_pdf_size_to_level`, which caps its font-size tiers at three levels.
_HEADING_LEVELS = {"h1": 1, "h2": 2, "h3": 3, "h4": 3, "h5": 3, "h6": 3}

# Below this a "block" is a stray character, not a sentence.
_MIN_BLOCK_CHARS = 2


@dataclass
class OutlineImage:
    """An image found in the markup, resolved to a usable URL."""

    url: str
    alt: str = ""
    width: int | None = None
    height: int | None = None
    anchor: int = 0


@dataclass
class HtmlOutline:
    blocks: list[OutlineBlock] = field(default_factory=list)
    images: list[OutlineImage] = field(default_factory=list)
    title: str | None = None
    description: str | None = None
    # Images whose `src` was a site-relative path with no origin to resolve it
    # against. Reported so a paste can say "3 images skipped" rather than
    # silently dropping them.
    unresolved_images: int = 0


def read_html(html: str, *, base_url: str | None = None) -> HtmlOutline:
    """Parse markup into ordered blocks + anchored images.

    ``base_url`` resolves relative URLs; a ``<base href>`` in the markup wins
    over it, since that is the document's own statement of where it came from.
    With neither, a relative ``src`` has no origin and the image is counted in
    ``unresolved_images`` instead of being emitted as a broken URL.
    """
    soup = BeautifulSoup(html or "", "lxml")

    title = soup.title.get_text(" ", strip=True) if soup.title else None
    description = _meta_content(soup, "description", "og:description")
    base = _declared_base(soup) or base_url

    for tag in soup.find_all(HTML_CHROME_TAGS):
        tag.decompose()

    outline = HtmlOutline(title=title or None, description=description)
    seen_text: set[str] = set()
    seen_images: set[str] = set()

    # One traversal in document order over text blocks AND images, so each
    # image's anchor is its true position among the blocks.
    for el in soup.find_all([*HTML_BLOCK_TAGS, "img"]):
        if not isinstance(el, Tag):
            continue
        if el.name == "img":
            src = (image_src_from_tag(el) or "").strip()
            if not src:
                continue
            if _needs_origin(src, base):
                outline.unresolved_images += 1
                continue
            url = _image_url(src, base, _attr(el, "alt"))
            if url is None:
                continue
            if url in seen_images:
                continue
            seen_images.add(url)
            outline.images.append(
                OutlineImage(
                    url=url,
                    alt=_attr(el, "alt"),
                    width=_int(_attr(el, "width")),
                    height=_int(_attr(el, "height")),
                    anchor=len(outline.blocks),
                )
            )
            continue

        text = el.get_text(" ", strip=True)
        if len(text) < _MIN_BLOCK_CHARS:
            continue
        # Nested block tags (a <p> inside an <li>) surface the same words twice.
        key = text.lower()
        if key in seen_text:
            continue
        seen_text.add(key)
        outline.blocks.append(OutlineBlock(level=_HEADING_LEVELS.get(el.name, 0), text=text))

    return outline


def text_blocks(html: str) -> list[str]:
    """Content text block-by-block, in document order.

    The crawler's recall spine: trafilatura optimises for precision and drops
    whole <section>/<div> blocks on marketing pages, so ``_extract_body_text``
    merges this with it rather than picking a winner.
    """
    return [block.text for block in read_html(html).blocks]


def _needs_origin(src: str, base: str | None) -> bool:
    """True when the src is a site-relative path and we have no base for it."""
    return not base and not src.startswith(("http://", "https://", "data:"))


def _image_url(src: str, base: str | None, alt: str) -> str | None:
    """The image's usable URL, or None when it isn't worth carrying.

    Data URIs are kept as-is (a pasted inline image is real content) and
    absolute URLs are upgraded through the shared CDN rewriter. Icons and
    tracking pixels are dropped the same way the crawler drops them — those are
    filtered, not unresolved, and must not be counted as skipped.
    """
    if src.startswith("data:"):
        return src if src.startswith("data:image/") else None
    url = (
        upgrade_source_image_url(src)
        if src.startswith(("http://", "https://"))
        else absolute_url(base or "", src)
    )
    if not url or looks_like_icon(url, alt):
        return None
    return url


def _declared_base(soup: BeautifulSoup) -> str | None:
    """An absolute ``<base href>`` declared by the markup itself."""
    tag = soup.find("base", href=True)
    if not isinstance(tag, Tag):
        return None
    href = _attr(tag, "href")
    return href if href.startswith(("http://", "https://")) else None


def _meta_content(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"name": name}) or soup.find(
            "meta", attrs={"property": name}
        )
        if isinstance(tag, Tag):
            content = _attr(tag, "content")
            if content:
                return content
    return None


def _attr(tag: Tag, name: str) -> str:
    value = tag.get(name)
    if isinstance(value, list):
        value = value[0] if value else ""
    return value.strip() if isinstance(value, str) else ""


def _int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
