"""
Shared machinery for re-attaching content the source stated *exactly*.

Some sections state their content in a form the LLM cannot be faithful to. A
picture rack carries no prose. A download card's href and a video's embed id are
opaque strings with external referents — asked to write them, a model can only
invent, and an invented href is a dead link while an invented video id is a
*different video* on a client's site. So these sections are not narrated: they
are placed, verbatim, from what the crawler read.

``routers.generate`` has four such passes — ``_inject_downloads``,
``_inject_image_walls``, ``_inject_videos`` and ``_inject_maps``. They differ in
what they collect and how they gate it, but they share the same spine, and each
step encodes a bug that was paid for once already:

    accumulate_by_slug   — several source records routinely share one slug
    repeated_across_slugs — template furniture looks exactly like content
    group_by_heading     — one page's embeds are not one undifferentiated wall
    insert_after_hero    — appending lands the section after the closing CTA

Keeping the spine here means the next deterministic section costs a mapper and a
block builder, not a fourth copy of the bookkeeping. See
``models.content_blocks.DETERMINISTIC_SECTION_KINDS`` for the other half of the
contract: these kinds are withheld from the LLM and discarded if it volunteers one.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Container, Hashable, Iterable
from math import ceil
from typing import TypeVar

from app.models.content_blocks import ContentBlock, PagePlan, SourceContent
from app.services.source_path import normalize_source_slug

T = TypeVar("T")
K = TypeVar("K", bound=Hashable)

# Slugs a key must appear on before it reads as template chrome rather than
# content. Two is the floor at which "repeated" means anything at all.
CHROME_MIN_SLUGS = 2


def source_pages(source: SourceContent) -> list[SourceContent]:
    """The entry page plus every page the crawl discovered, in one list."""
    return [source, *source.discovered_pages]


def accumulate_by_slug(
    source: SourceContent, extract: Callable[[SourceContent], Iterable[T]]
) -> dict[str, list[T]]:
    """Group each source page's items under its normalized slug.

    Accumulating before placing is the point. Several crawled records routinely
    share ONE slug: a PHP viewer serves every album from ``gallery-photo.php?id=NNN``
    and ``url_path`` carries no query, so 28 albums normalize to ``gallery-photo``.
    Placing each record as it was read overwrote the same slot 28 times and
    shipped one album.

    Order within a slug follows crawl order; callers that need stable output
    should dedupe on their own identity key afterwards.
    """
    out: dict[str, list[T]] = {}
    for page in source_pages(source):
        items = list(extract(page))
        if not items:
            continue
        out.setdefault(normalize_source_slug(page.url_path), []).extend(items)
    return out


def repeated_across_slugs(
    source: SourceContent,
    extract: Callable[[SourceContent], Iterable[K]],
    *,
    min_slugs: int = CHROME_MIN_SLUGS,
    min_share: float = 0.0,
) -> set[K]:
    """Keys repeated across enough distinct SLUGS to be template chrome.

    Same reasoning as ``nav_extraction.strip_chrome_sections``, applied to media:
    a footer photo strip, a row of social icons, a sidebar promo video are the
    template's, and they are indistinguishable from content until the crawl
    finishes. On brightkids the footer strip is the ONLY thing the section tree
    offers the gallery page, so without this the "photo gallery" is six copies of
    the site's footer furniture.

    Counted per SLUG, not per crawled record. Several records routinely share one
    slug — a paginated album serves ``?id=107`` and ``?id=107&gspg=2`` from the
    same path — and counting those as two pages would let an album's own photos
    look repeated, which is precisely backwards: they are one page's content
    appearing on one page.

    ``min_share`` raises the bar PROPORTIONALLY to the size of the crawl, for
    media where appearing on a second page is ordinary rather than suspicious.
    Two pages is the right floor for a photo — the same picture on two pages of a
    20-page site really is furniture. It is the wrong floor for a video, twice
    over, and brightkids shipped both failures at once:

    - An index page legitimately re-shows content that also lives on its topic
      page. All 15 of that site's videos sat on ``/gallery-video.php`` AND on
      their own subject page (``/testimony.php``, ``/Super_Brain.php``, …), so a
      flat two-slug rule condemned **every video on the site**.
    - The entry page is crawled under two slugs — ``/`` and ``/index.php``
      normalize to ``""`` and ``index`` — so even a video that exists on exactly
      one real page counts twice, which is what silently cost the homepage its
      own.

    A genuine sidebar or footer reel is on nearly EVERY page, so a share-based
    threshold still catches it while leaving twice-used content alone.
    """
    by_slug: dict[str, set[K]] = {}
    for page in source_pages(source):
        keys = by_slug.setdefault(normalize_source_slug(page.url_path), set())
        keys.update(extract(page))
    threshold = max(min_slugs, ceil(len(by_slug) * min_share)) if min_share else min_slugs
    if len(by_slug) < threshold:
        return set()
    seen: Counter[K] = Counter()
    for keys in by_slug.values():
        seen.update(keys)
    return {key for key, n in seen.items() if n >= threshold}


def hero_insert_index(page: PagePlan) -> int:
    """Where a deterministic section goes: right after the hero, else the top.

    Not appended. A page's closing CTA is the last block, and appending lands a
    resources rack or a video wall *after* the reader has been asked to act —
    these are top-of-page content, and on a gallery or video page they ARE the page.
    """
    hero_index = next((i for i, b in enumerate(page.blocks) if b.kind == "hero"), None)
    return hero_index + 1 if hero_index is not None else 0


def insert_after_hero(page: PagePlan, block: ContentBlock) -> None:
    """Place one deterministic block at ``hero_insert_index``, in place."""
    page.blocks.insert(hero_insert_index(page), block)


def companion_insert_index(page: PagePlan, kinds: Iterable[str]) -> int:
    """Just after the last block of a companion kind, else ``hero_insert_index``.

    Some deterministic sections are not top-of-page content in their own right —
    they ANNOTATE a section the model wrote. A map belongs beside the address
    that names the place, not stranded above it under the hero. When the page
    has no such companion the hero rule applies unchanged.
    """
    wanted = set(kinds)
    last = next(
        (i for i in range(len(page.blocks) - 1, -1, -1) if page.blocks[i].kind in wanted),
        None,
    )
    return last + 1 if last is not None else hero_insert_index(page)


def group_by_heading(
    items: Iterable[T],
    *,
    key_of: Callable[[T], K],
    heading_of: Callable[[T], str],
    exclude: Container[K] = frozenset(),
    max_groups: int,
    max_items: int,
) -> list[tuple[str, list[T]]]:
    """Dedupe, drop excluded keys, then bucket by each item's own group heading.

    The source's headings are what keep one page's embeds from merging into a
    single undifferentiated wall: a page showing "Concerts" and "Open Days"
    ships two sections rather than one. Items with no heading fall together
    under ``""``, which the block models heal to their own default.

    Insertion-ordered throughout, so output is a pure function of crawl order
    rather than of dict iteration luck — the caps below have to bite
    deterministically or two runs of the same site differ.
    """
    groups: dict[str, list[T]] = {}
    seen: set[K] = set()
    for item in items:
        key = key_of(item)
        if key in exclude or key in seen:
            continue
        seen.add(key)
        groups.setdefault(heading_of(item).strip(), []).append(item)
    return [
        (heading, group[:max_items])
        for heading, group in list(groups.items())[:max_groups]
    ]
