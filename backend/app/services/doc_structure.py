"""
Turn a parsed document's outline into page-shaped ``SourceContent``.

The document's titles are a *content signal*, not a layout blueprint. Which of
them open pages is ``outline_pages``' decision (or, for a paste read by the
model, the model's); every other heading is in-page content. Body paragraphs
accumulate into whichever bucket is open. The leading bucket becomes the
primary page (homepage); the rest become ``discovered_pages``, each carrying a
synthesized ``url_path`` so the planner walks them exactly like crawled pages.

Crucially, section composition and visual distinctiveness are **not** derived
here — ``infer_page_scaffolds`` + the section catalog own that, identically to
the scrape path. This module only decides *which pages exist* and *which content
belongs to each*, and hands each page its section tree
(``outline_sections``) — the document's own structure, the way the crawler
hands a page the tree its markup declares.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Mapping

from app.models.content_blocks import ImageMetadata, SourceContent
from app.services.doc_contract import names_home_page, slug_for_title
from app.services.outline_pages import OutlinePlan, SiteShape, plan_pages
from app.services.outline_sections import outline_sections
from app.services.source_outline import OutlineBlock, ParsedDocument, strip_numbered_titles

logger = logging.getLogger(__name__)

# Defensive cap so a pathological document can't explode into hundreds of pages.
# Public because paste_source.merge_sources holds the merged page set to it too.
MAX_DISCOVERED_PAGES = 30
# Keep a page's image set bounded so a gallery-heavy doc can't flood one page.
_MAX_IMAGES_PER_PAGE = 8


@dataclass
class DocImageRef:
    """A source image resolved to a URL, ready for per-page placement.

    ``anchor`` is the ``DocImage.anchor`` from the parser (its position in the
    outline); ``split_into_pages`` uses it to attach the image to the page and
    nearest heading it appears under. ``alt`` is the markup's own alt text when
    the source carried any — a PDF/DOCX image has none, so it falls back to the
    heading the image sat under.
    """

    url: str
    width: int | None = None
    height: int | None = None
    anchor: int = 0
    alt: str = ""


@dataclass
class _PlacedImage:
    url: str
    alt: str
    width: int | None
    height: int | None


class _Bucket:
    """Accumulates one page's title + headings + body lines in reading order."""

    __slots__ = ("title", "slug", "page_type", "headings", "lines", "blocks", "images")

    def __init__(self, title: str, slug: str, page_type: str) -> None:
        self.title = title
        self.slug = slug  # "" marks the primary (home) bucket
        self.page_type = page_type
        self.headings: list[str] = []
        self.lines: list[str] = []
        self.blocks: list[OutlineBlock] = []  # the lines, with their heading levels
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
    page_topics: Mapping[str, str] | None = None,
    shape: SiteShape | None = None,
) -> SourceContent:
    """Build the primary ``SourceContent`` (homepage) with ``discovered_pages``.

    ``page_topics`` maps a heading's exact text to its page type, and is the
    whole answer to which headings open pages: a heading it does not name is
    in-page content, and an empty mapping means the document is one page. A
    structure read supplies it (``services/paste_structure.py``: from the
    document's outline when the source states heading ranks, from the model
    for a paste that states none). Absent, ``outline_pages`` reads it from
    this outline — so a document read with or without the model is grouped by
    one rule. With no page the planner composes a full homepage from the
    industry template. A title typed ``home`` files its part back on the
    homepage after a page. ``shape`` is the site the person chose
    ("one_page" folds every page onto the homepage, whoever named them).

    The author's section numbers ("01 Hero", "02 Sound familiar") come off the
    titles first (``strip_numbered_titles``), so every reader of the outline —
    the plan, the section tree, the planner — sees one spelling of each title.

    A page title opens a page once: a second heading of the same slug stays in
    the page before it. Several pages may share a TYPE — "AI Agents" and
    "Custom Software" are both ``services``, and collapsing them would
    silently drop one. A heading that only names the home page ("HOME") is a
    label for the bucket it already is, not copy.

    Images are placed on the page (and given alt text from the heading) they
    appear under, so the resolver can match them to the right slot.
    """
    outline = strip_numbered_titles(parsed.outline)
    opens = _page_titles(outline, page_topics, shape)

    home = _Bucket(title=parsed.title or "Home", slug="", page_type="home")
    pages: list[_Bucket] = [home]
    current = home
    used_slugs: set[str] = set()

    # Parallel to parsed.outline: the bucket + nearest heading at each position,
    # so an image's anchor can be resolved to the page it belongs under.
    bucket_at: list[_Bucket] = []
    heading_at: list[str | None] = []
    last_heading: str | None = None

    for index, block in enumerate(outline):
        text = block.text.strip()
        if not text:
            bucket_at.append(current)
            heading_at.append(last_heading)
            continue

        page_type = opens.get(index)
        if page_type == "home":
            current, page_type = home, None  # this part is the homepage's again
        slug = slug_for_title(text)
        if (
            page_type is not None
            and len(pages) - 1 < MAX_DISCOVERED_PAGES
            and slug not in used_slugs
        ):
            used_slugs.add(slug)
            current = _Bucket(title=text, slug=slug, page_type=page_type)
            current.headings.append(text)  # the page title is also its first heading
            pages.append(current)
            last_heading = text
        elif block.level > 0 and names_home_page(text):
            pass  # "HOME" labels the homepage; it is not the page's copy
        else:
            # In-page heading or body. Headings feed both the headings list
            # (section cues) and raw_text (so the copy isn't dropped); body feeds
            # raw_text.
            if block.level > 0:
                current.headings.append(text)
                last_heading = text
            current.lines.append(text)
            current.blocks.append(block)

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
        section_candidates=outline_sections(home.blocks),
        reference_colors=parsed.reference_colors[:8],
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
        section_candidates=outline_sections(bucket.blocks),
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
        alt = (ref.alt or heading or bucket.title or "").strip()
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


def outline_plan(parsed: ParsedDocument, shape: SiteShape | None = None) -> OutlinePlan | None:
    """The page plan this document's own outline states (``outline_pages``)."""
    return plan_pages(strip_numbered_titles(parsed.outline), shape=shape)


def _page_titles(
    outline: list[OutlineBlock],
    page_topics: Mapping[str, str] | None,
    shape: SiteShape | None,
) -> dict[int, str]:
    """The blocks that open pages, by position, with their page types.

    Supplied topics name a title by its text; the outline's own plan names the
    exact block, so a card titled "Our Programmes" on the homepage can never
    open the "Our Programmes" page that comes after it.
    """
    if shape == "one_page":
        return {}
    if page_topics is not None:
        return {
            index: page_topics[block.text.strip()]
            for index, block in enumerate(outline)
            if block.level > 0 and block.text.strip() in page_topics
        }
    plan = plan_pages(outline, shape=shape)
    if plan is None:
        return {}
    opened = sum(page_type != "home" for _, page_type in plan.pages)
    logger.info(
        "Document grouped as %s: %s",
        "one page" if plan.one_page else f"the homepage and {opened} page(s)",
        plan.reason,
    )
    return dict(plan.pages)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out
