"""
Match each PageScaffold to its best-matching SourceContent.

The crawler captures content per discovered page in
``source.discovered_pages``. Today the planner sends one big blob (the entry
page's raw_text) to the LLM for every page, so the LLM is writing /services
copy from the homepage's content. This module fixes that — for each scaffold,
we find the specific scraped page whose content matches its slug / page_type.

Match priority:
  1. Exact path match            (slug "" → entry, slug "services" → /services)
  2. Trailing-segment match      (slug "services/web-design" → /web-design)
  3. Page-type keyword match     (page_type="services" → any page whose path or
                                  title contains "service", "solution", "offering")
  4. Entry source as final fallback

The returned map keys are the scaffold's slug (so the planner can look it up
the same way it iterates scaffolds). The values are the actual SourceContent
objects so the prompt gets raw_text + headings + everything.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from app.models.content_blocks import SourceContent
from app.models.industry import PageScaffold

logger = logging.getLogger(__name__)


# Keyword hints used when slug/path matching fails. Aligned with page_inference's
# _TYPE_HINTS but kept here as a local copy so this module can be used standalone.
_PAGE_TYPE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "home": ("home",),
    "about": ("about", "company", "story", "who-we-are", "mission"),
    "contact": ("contact", "get-in-touch", "reach-us"),
    "services": ("service", "solution", "offering", "expertise", "what-we-do", "product"),
    "pricing": ("pricing", "plans", "subscription", "cost"),
    "team": ("team", "people", "leadership", "staff", "board"),
    "work": ("work", "portfolio", "case-stud", "project", "client"),
    "menu": ("menu", "food", "drink", "wine-list"),
    "gallery": ("gallery", "photo", "image"),
    "testimonials": ("testimonial", "review", "story", "praise"),
    "faq": ("faq", "question", "help"),
    "blog": ("blog", "news", "insight", "article", "press"),
    "process": ("process", "approach", "method", "how-we-work"),
    "landing": (),  # generic — only matches by direct path
}


def _normalize_slug(value: str | None) -> str:
    """Strip slashes; lowercase. Empty string ⇒ homepage."""
    if not value:
        return ""
    return value.strip("/").lower()


def _path_to_slug(url_path: str | None) -> str:
    if not url_path or url_path == "/":
        return ""
    return _normalize_slug(url_path)


def _trailing_segment(slug: str) -> str:
    """``"services/web-design"`` → ``"web-design"``."""
    if "/" not in slug:
        return slug
    return slug.rsplit("/", 1)[-1]


def _source_haystack(page: SourceContent) -> str:
    """Lowercase string used for keyword matching: path + title + first heading."""
    parts: list[str] = []
    if page.url_path:
        try:
            parts.append(urlparse(page.url_path).path)
        except ValueError:
            parts.append(page.url_path)
    if page.title:
        parts.append(page.title)
    if page.headings:
        parts.extend(page.headings[:3])
    return " ".join(parts).lower()


def match_scaffolds_to_pages(
    scaffolds: list[PageScaffold],
    primary_source: SourceContent,
) -> dict[str, SourceContent]:
    """
    For each scaffold, find the SourceContent whose content should drive its
    generation. Returns a map keyed by scaffold.slug.

    Pages without a discovered match still get an entry — the primary source —
    so callers can always assume a value exists.
    """
    discovered = primary_source.discovered_pages or []

    # Index discovered pages by normalized slug derived from url_path.
    by_slug: dict[str, SourceContent] = {}
    by_trailing: dict[str, SourceContent] = {}
    for page in discovered:
        slug = _path_to_slug(page.url_path)
        if slug and slug not in by_slug:
            by_slug[slug] = page
            trailing = _trailing_segment(slug)
            if trailing and trailing not in by_trailing:
                by_trailing[trailing] = page

    out: dict[str, SourceContent] = {}
    for scaffold in scaffolds:
        if scaffold.is_legal:
            # Legal pages don't use scraped content — boilerplate template wins.
            continue

        target_slug = _normalize_slug(scaffold.slug)

        # 1. Homepage → entry source itself
        if target_slug == "" and scaffold.is_homepage:
            out[scaffold.slug] = primary_source
            continue

        # 2. Exact slug match against discovered pages
        if target_slug in by_slug:
            out[scaffold.slug] = by_slug[target_slug]
            logger.debug("Routed scaffold %r to discovered /%s (exact)", scaffold.slug, target_slug)
            continue

        # 3. Trailing-segment match — handles "services/web-design" → /web-design
        trailing = _trailing_segment(target_slug)
        if trailing and trailing != target_slug and trailing in by_trailing:
            out[scaffold.slug] = by_trailing[trailing]
            logger.debug("Routed scaffold %r to discovered /%s (trailing)", scaffold.slug, trailing)
            continue

        # 4. Page-type keyword match against discovered pages' path+title+headings
        matched = _match_by_keywords(scaffold.page_type, discovered)
        if matched is not None:
            out[scaffold.slug] = matched
            logger.debug(
                "Routed scaffold %r (type=%s) to discovered %r (keyword)",
                scaffold.slug, scaffold.page_type, matched.url_path or matched.title,
            )
            continue

        # 5. Final fallback — entry source, so the LLM always has *something*.
        out[scaffold.slug] = primary_source
        logger.debug("Routed scaffold %r to entry source (fallback)", scaffold.slug)

    return out


def _match_by_keywords(
    page_type: str,
    discovered: list[SourceContent],
) -> SourceContent | None:
    """Find a discovered page whose path/title/headings mention this page_type."""
    keywords = _PAGE_TYPE_KEYWORDS.get(page_type, ())
    if not keywords:
        return None
    for page in discovered:
        haystack = _source_haystack(page)
        for kw in keywords:
            if kw in haystack:
                return page
    return None


def split_raw_text(raw_text: str, max_chars: int) -> list[str]:
    """Split a page's text into chunks of at most ``max_chars`` characters.

    The extractor emits one block (paragraph / heading / list item) per line, so
    we split on newlines and greedily pack whole blocks into each chunk — block
    boundaries are never crossed unless a single block is itself larger than
    ``max_chars``, in which case it's hard-wrapped so a giant block can't stall
    the loop. Returns ``[raw_text]`` unchanged when the whole text already fits,
    so small pages stay a single pass.
    """
    text = raw_text or ""
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for block in text.split("\n"):
        # Hard-wrap a single block that can't fit in one chunk on its own.
        if len(block) > max_chars:
            if current:
                chunks.append("\n".join(current))
                current, current_len = [], 0
            for i in range(0, len(block), max_chars):
                chunks.append(block[i : i + max_chars])
            continue
        # +1 for the newline that will rejoin this block to the previous one.
        added = len(block) + (1 if current else 0)
        if current_len + added > max_chars:
            chunks.append("\n".join(current))
            current, current_len = [block], len(block)
        else:
            current.append(block)
            current_len += added

    if current:
        chunks.append("\n".join(current))
    return chunks


def excerpt_for_prompt(
    source: SourceContent,
    *,
    max_chars: int = 4000,
    max_headings: int = 20,
    text_override: str | None = None,
) -> dict[str, object]:
    """
    Compact per-page payload for inclusion in the LLM prompt. Caps the raw
    text and headings so a single page's content fits inside the batch budget.

    ``text_override`` lets a multi-pass caller feed one pre-sized chunk of the
    page (from :func:`split_raw_text`) verbatim, bypassing the ``max_chars`` cut
    — the chunk is already budgeted, so re-truncating it would drop content.

    Returns a JSON-serialisable dict.
    """
    raw_text = source.raw_text or ""
    body = text_override if text_override is not None else raw_text[:max_chars]
    return {
        "url_path": source.url_path or "/",
        "title": source.title,
        "headings": (source.headings or [])[:max_headings],
        "raw_text": body,
        "raw_text_char_count": len(raw_text),
    }
