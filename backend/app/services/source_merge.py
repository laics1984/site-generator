"""
Combining two independently-read ``SourceContent`` trees into one.

A URL crawl, a document parse and a pasted-text read each build their own
``SourceContent`` tree in isolation — the crawler, and
``doc_structure.split_into_pages`` for both a document and a paste. This
module is the one place that folds a second tree into a first, so "a paste
added to a crawl", "a document added to a crawl" and "a paste added to a
document" are the same operation, not three. It used to live in
``paste_source.py`` under the assumption ``addition`` was always a thin paste;
nothing about it actually was paste-specific, and now that a Document upload
and a URL crawl can both be "the addition" too, it lives here instead.

Pages join by topic: an added page merges into an existing one when they
share a slug, or when both name the same page topic and the existing page is
top-level (pasted Contact copy belongs on ``/contact``, not on
``/services/emergency-contact``). Anything unmatched is appended as a new
page — how "add a page the base source never had" works, regardless of which
reader is on either side.
"""

from __future__ import annotations

from typing import Callable, TypeVar

from app.models.content_blocks import ImageMetadata, SourceContent
from app.services.doc_contract import classify_page_title, slug_for_title
from app.services.doc_structure import MAX_DISCOVERED_PAGES

T = TypeVar("T")


def merge_sources(base: SourceContent, addition: SourceContent) -> SourceContent:
    """Fold ``addition``'s content into ``base``, keeping base's identity.

    ``base`` stays the source: its kind, ref, title and everything only a
    reader measures (nav links, section candidates, profile cards, embeds) are
    carried through untouched via ``model_copy`` — unioned with whatever
    ``addition`` also found, per page, by ``_merge_content`` — so a field
    added to ``SourceContent`` later needs no edit here.

    Pages join by topic. An added page merges into an existing one when they
    share a slug, or when both name the same page topic and the existing page
    is top-level; anything unmatched is appended as a new page.
    """
    pages = [page.model_copy(deep=True) for page in base.discovered_pages]
    index_by_key: dict[str, int] = {}
    for position, page in enumerate(pages):
        for key in _page_keys(page):
            index_by_key.setdefault(key, position)

    used_paths = {page.url_path for page in pages if page.url_path}
    for extra in addition.discovered_pages:
        match = next(
            (index_by_key[key] for key in _page_keys(extra) if key in index_by_key), None
        )
        if match is not None:
            pages[match] = _merge_content(pages[match], extra)
            continue
        if len(pages) >= MAX_DISCOVERED_PAGES:
            continue
        new_page = extra.model_copy(
            update={"url_path": _unique_path(extra.url_path, used_paths)}
        )
        used_paths.add(new_page.url_path)
        for key in _page_keys(new_page):
            index_by_key.setdefault(key, len(pages))
        pages.append(new_page)

    # Merge the addition's OWN homepage content only — see _unclaimed_headings.
    merged = _merge_content(
        base, addition.model_copy(update={"headings": _unclaimed_headings(addition)})
    )
    return merged.model_copy(
        update={
            "title": base.title or addition.title,
            "description": base.description or addition.description,
            "discovered_pages": pages,
        }
    )


def _unclaimed_headings(addition: SourceContent) -> list[str]:
    """``addition``'s headings, minus the ones its own pages took with them.

    ``split_into_pages`` falls back to the whole source's headings when the
    leading (home) bucket has none of its own — right for a standalone read,
    where something has to describe the source, and wrong when merging. An
    addition that is nothing but "## Contact" and "## Meet the Team" would
    otherwise hand the site's HOMEPAGE both headings while the copy under them
    lives on the pages those headings opened: a heading with no copy behind
    it, which is the shape that produces a hollow section.
    """
    claimed = {
        heading.strip().lower()
        for page in addition.discovered_pages
        for heading in page.headings
    }
    return [h for h in addition.headings if h.strip().lower() not in claimed]


def _merge_content(base: SourceContent, addition: SourceContent) -> SourceContent:
    """One page's worth of merge: every field a second reader can contribute,
    base first.

    Union, not overwrite — a page that TWO readers each described (e.g. a
    "Contact" page from both a URL crawl and an uploaded document) must keep
    both sides' profile cards, embeds and nav evidence, not just base's. Each
    list field is deduped on a cheap identity key rather than blindly
    concatenated, so re-merging the same addition twice (or merging a paste
    that repeats what the base already said) doesn't double every card.
    """
    images = _dedupe_urls([*base.images, *addition.images])
    metadata = _merge_metadata(base.image_metadata, addition.image_metadata)
    return base.model_copy(
        update={
            "raw_text": "\n\n".join(
                part for part in (base.raw_text.strip(), addition.raw_text.strip()) if part
            ),
            "headings": _dedupe([*base.headings, *addition.headings]),
            "images": images,
            "image_metadata": metadata,
            "profile_candidates": _dedupe_by(
                [*base.profile_candidates, *addition.profile_candidates],
                lambda p: p.name.strip().lower(),
            ),
            "section_candidates": _dedupe_by(
                [*base.section_candidates, *addition.section_candidates],
                lambda s: s.heading.strip().lower(),
            ),
            "document_cards": _dedupe_by(
                [*base.document_cards, *addition.document_cards],
                lambda d: (d.title or "").strip().lower() or d.links[0].href,
            ),
            "video_embeds": _dedupe_by(
                [*base.video_embeds, *addition.video_embeds],
                lambda v: (v.provider, v.video_id),
            ),
            "map_embeds": _dedupe_by(
                [*base.map_embeds, *addition.map_embeds],
                lambda m: m.embed_url,
            ),
            "nav_links": _dedupe_by(
                [*base.nav_links, *addition.nav_links],
                lambda n: n.href,
            ),
            "body_link_clusters": _dedupe_by(
                [*base.body_link_clusters, *addition.body_link_clusters],
                lambda c: c.href_key,
            ),
            "social_links": _dedupe_by(
                [*base.social_links, *addition.social_links],
                lambda n: n.href,
            ),
            "subject_name": base.subject_name or addition.subject_name,
        }
    )


def _dedupe_by(items: list[T], key: Callable[[T], object]) -> list[T]:
    """First-wins dedupe by an arbitrary key.

    Callers always pass ``[*base.field, *addition.field]``, so base's own
    entries rank first and survive a collision.
    """
    seen: set[object] = set()
    out: list[T] = []
    for item in items:
        k = key(item)
        if k in seen:
            continue
        seen.add(k)
        out.append(item)
    return out


def _merge_metadata(
    base: list[ImageMetadata], addition: list[ImageMetadata]
) -> list[ImageMetadata]:
    seen = {meta.url for meta in base}
    return [*base, *(meta for meta in addition if meta.url not in seen)]


def _page_keys(page: SourceContent) -> list[str]:
    """Identities a page can be matched on, most specific first."""
    keys: list[str] = []
    path = (page.url_path or "").strip("/")
    title = (page.title or "").strip()
    slug = path.rsplit("/", 1)[-1] if path else (slug_for_title(title) if title else "")
    if slug:
        keys.append(f"slug:{slug}")
    # Topic is a broad match, so only a top-level page may answer to it.
    if "/" not in path:
        topic = classify_page_title(title) or classify_page_title(slug.replace("-", " "))
        if topic:
            keys.append(f"topic:{topic}")
    return keys


def _unique_path(path: str | None, used: set[str | None]) -> str:
    candidate = path or f"/{slug_for_title('page')}"
    if candidate not in used:
        return candidate
    suffix = 2
    while f"{candidate}-{suffix}" in used:
        suffix += 1
    return f"{candidate}-{suffix}"


def _dedupe(values) -> list[str]:
    """Case-insensitive dedupe for prose (headings), first spelling wins."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _dedupe_urls(values) -> list[str]:
    """Exact dedupe — a URL path is case-sensitive, unlike a heading."""
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
