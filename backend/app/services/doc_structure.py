"""
Turn a parsed document's outline into page-shaped ``SourceContent``.

The document's titles are a *content signal*, not a layout blueprint. A heading
that reads like a page topic (About, Services, Contact…) opens a new page
bucket; every other heading is in-page content. Body paragraphs accumulate into
whichever bucket is open. The leading bucket becomes the primary page
(homepage); the rest become ``discovered_pages``, each carrying a synthesized
``url_path`` so the planner walks them exactly like crawled pages.

Crucially, section composition and visual distinctiveness are **not** derived
here — ``infer_page_scaffolds`` + the section catalog own that, identically to
the scrape path. This module only decides *which pages exist* and *which content
belongs to each*.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.content_blocks import ImageMetadata, SourceContent
from app.services.doc_contract import classify_page_title, is_home_title, slug_for_title
from app.services.doc_parser import ParsedDocument

# A page-topic heading must be reasonably prominent. Examining only H1/H2 keeps a
# deep "Heading 3" detail inside a section from fracturing the page into many.
_MAX_PAGE_HEADING_LEVEL = 2
# Defensive cap so a pathological document can't explode into hundreds of pages.
_MAX_DISCOVERED_PAGES = 30
# Keep a page's image set bounded so a gallery-heavy doc can't flood one page.
_MAX_IMAGES_PER_PAGE = 8


@dataclass
class DocImageRef:
    """A document image resolved to a data URL, ready for per-page placement.

    ``anchor`` is the ``DocImage.anchor`` from the parser (its position in the
    outline); ``split_into_pages`` uses it to attach the image to the page and
    nearest heading it appears under.
    """

    url: str
    width: int | None = None
    height: int | None = None
    anchor: int = 0


@dataclass
class _PlacedImage:
    url: str
    alt: str
    width: int | None
    height: int | None


class _Bucket:
    """Accumulates one page's title + headings + body lines in reading order."""

    __slots__ = ("title", "slug", "page_type", "headings", "lines", "images")

    def __init__(self, title: str, slug: str, page_type: str) -> None:
        self.title = title
        self.slug = slug  # "" marks the primary (home) bucket
        self.page_type = page_type
        self.headings: list[str] = []
        self.lines: list[str] = []
        self.images: list[_PlacedImage] = []

    @property
    def raw_text(self) -> str:
        return "\n".join(self.lines)

    @property
    def is_empty(self) -> bool:
        return not self.lines and not self.headings


def split_into_pages(
    parsed: ParsedDocument,
    *,
    images: list[str] | None = None,
    image_metadata: list[ImageMetadata] | None = None,
    description: str | None = None,
) -> SourceContent:
    """Build the primary ``SourceContent`` (homepage) with ``discovered_pages``.

    Falls back to a single page (today's behaviour) when the document has no
    page-topic headings — the planner then composes a full homepage from the
    industry template.
    """
    images = images or []
    image_metadata = image_metadata or []

    # Only headings at the document's *shallowest* topic level open pages. A doc
    # that uses H1 for page titles keeps its H2s as in-page sections; a doc that
    # only uses H2s lets those open pages. This stops a sub-heading like "Our
    # Story" inside About from fracturing off into its own page.
    page_level = _page_heading_level(parsed)

    home = _Bucket(title=parsed.title or "Home", slug="", page_type="home")
    pages: list[_Bucket] = [home]
    current = home
    used_slugs: set[str] = set()
    seen_types: set[str] = {"home"}

    for block in parsed.outline:
        text = block.text.strip()
        if not text:
            continue

        page_type = (
            classify_page_title(text)
            if page_level is not None and block.level == page_level
            else None
        )
        opens_page = (
            page_type is not None
            and not is_home_title(text)
            and page_type != current.page_type  # same topic ⇒ stays in this page
            and page_type not in seen_types      # one page per topic
            and len(pages) - 1 < _MAX_DISCOVERED_PAGES
        )

        if opens_page:
            slug = _unique_slug(slug_for_title(text), used_slugs)
            used_slugs.add(slug)
            seen_types.add(page_type)
            current = _Bucket(title=text, slug=slug, page_type=page_type)
            current.headings.append(text)  # the page title is also its first heading
            pages.append(current)
            continue

        # In-page heading or body. Headings feed both the headings list (section
        # cues) and raw_text (so the copy isn't dropped); body feeds raw_text.
        if block.level > 0:
            current.headings.append(text)
        current.lines.append(text)

    # Drop an empty leading home bucket only if real pages followed it — otherwise
    # the homepage would have no content while sub-pages do.
    discovered = [b for b in pages[1:] if not b.is_empty]

    primary = SourceContent(
        source_kind=parsed.source_kind,
        source_ref=parsed.source_ref,
        title=parsed.title,
        description=description,
        raw_text=home.raw_text or parsed.raw_text,
        headings=home.headings or parsed.headings,
        images=images,
        links=[],
        url_path=None,
        discovered_pages=[
            SourceContent(
                source_kind=parsed.source_kind,
                source_ref=parsed.source_ref,
                title=b.title,
                raw_text=b.raw_text,
                headings=_dedupe(b.headings),
                url_path=f"/{b.slug}",
            )
            for b in discovered
        ],
        image_metadata=image_metadata,
    )
    return primary


def _page_heading_level(parsed: ParsedDocument) -> int | None:
    """The shallowest heading level (1-``_MAX_PAGE_HEADING_LEVEL``) at which a
    page-topic title appears, or ``None`` if the doc has none (→ single page)."""
    levels = [
        block.level
        for block in parsed.outline
        if 0 < block.level <= _MAX_PAGE_HEADING_LEVEL
        and not is_home_title(block.text)
        and classify_page_title(block.text) is not None
    ]
    return min(levels) if levels else None


def _unique_slug(slug: str, used: set[str]) -> str:
    """Disambiguate repeated titles so no page is silently dropped downstream."""
    if slug not in used:
        return slug
    n = 2
    while f"{slug}-{n}" in used:
        n += 1
    return f"{slug}-{n}"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
