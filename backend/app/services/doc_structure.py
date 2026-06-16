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

from dataclasses import dataclass

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
    images: list[DocImageRef] | None = None,
    description: str | None = None,
) -> SourceContent:
    """Build the primary ``SourceContent`` (homepage) with ``discovered_pages``.

    Falls back to a single page (today's behaviour) when the document has no
    page-topic headings — the planner then composes a full homepage from the
    industry template. Images are placed on the page (and given alt text from the
    heading) they appear under, so the resolver can match them to the right slot.
    """
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

    # Parallel to parsed.outline: the bucket + nearest heading at each position,
    # so an image's anchor can be resolved to the page it belongs under.
    bucket_at: list[_Bucket] = []
    heading_at: list[str | None] = []
    last_heading: str | None = None

    for block in parsed.outline:
        text = block.text.strip()
        if not text:
            bucket_at.append(current)
            heading_at.append(last_heading)
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
            last_heading = text
        else:
            # In-page heading or body. Headings feed both the headings list
            # (section cues) and raw_text (so the copy isn't dropped); body feeds
            # raw_text.
            if block.level > 0:
                current.headings.append(text)
                last_heading = text
            current.lines.append(text)

        bucket_at.append(current)
        heading_at.append(last_heading)

    _place_images(images or [], bucket_at, heading_at, home)

    # Drop an empty leading home bucket only if real pages followed it — otherwise
    # the homepage would have no content while sub-pages do. Images keep a page
    # alive: a page that is only a photo still belongs in the site.
    discovered = [b for b in pages[1:] if not b.is_empty or b.images]

    home_urls, home_meta = _image_payload(home)
    primary = SourceContent(
        source_kind=parsed.source_kind,
        source_ref=parsed.source_ref,
        title=parsed.title,
        description=description,
        raw_text=home.raw_text or parsed.raw_text,
        headings=home.headings or parsed.headings,
        images=home_urls,
        links=[],
        url_path=None,
        discovered_pages=[_page_source(parsed, b) for b in discovered],
        image_metadata=home_meta,
    )
    return primary


def _page_source(parsed: ParsedDocument, bucket: _Bucket) -> SourceContent:
    urls, meta = _image_payload(bucket)
    return SourceContent(
        source_kind=parsed.source_kind,
        source_ref=parsed.source_ref,
        title=bucket.title,
        raw_text=bucket.raw_text,
        headings=_dedupe(bucket.headings),
        url_path=f"/{bucket.slug}",
        images=urls,
        image_metadata=meta,
    )


def _place_images(
    images: list[DocImageRef],
    bucket_at: list[_Bucket],
    heading_at: list[str | None],
    home: _Bucket,
) -> None:
    """Attach each image to the page + nearest heading it appears under."""
    for ref in images:
        pos = ref.anchor - 1
        if not bucket_at or pos < 0:
            bucket, heading = home, None
        elif pos >= len(bucket_at):
            bucket, heading = bucket_at[-1], heading_at[-1]
        else:
            bucket, heading = bucket_at[pos], heading_at[pos]
        if len(bucket.images) >= _MAX_IMAGES_PER_PAGE:
            continue
        alt = (heading or bucket.title or "").strip()
        bucket.images.append(
            _PlacedImage(url=ref.url, alt=alt, width=ref.width, height=ref.height)
        )


def _image_payload(bucket: _Bucket) -> tuple[list[str], list[ImageMetadata]]:
    """The first image on a page is its hero candidate; the rest are generic.

    Alt text comes from the heading the image sat under, which is what lets the
    global resolver place a photo found below "Our Team" into the team slot.
    """
    urls: list[str] = []
    meta: list[ImageMetadata] = []
    for index, image in enumerate(bucket.images):
        urls.append(image.url)
        meta.append(
            ImageMetadata(
                url=image.url,
                alt=image.alt,
                intent="hero" if index == 0 else "generic",
                width=image.width,
                height=image.height,
            )
        )
    return urls, meta


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
