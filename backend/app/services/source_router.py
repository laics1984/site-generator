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

from app.models.content_blocks import ImageMetadata, SectionCandidate, SourceContent
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
    "team": (
        "team",
        "people",
        "leadership",
        "staff",
        "board",
        "committee",
        "council",
        "governance",
        "trustee",
    ),
    "work": ("work", "portfolio", "case-stud", "project", "client"),
    "menu": ("menu", "food", "drink", "wine-list"),
    "gallery": ("gallery", "photo", "image"),
    "testimonials": ("testimonial", "review", "story", "praise"),
    "faq": ("faq", "question", "help"),
    "blog": ("blog", "news", "insight", "article", "press"),
    "events": ("events", "calendar", "whats-on", "upcoming-events"),
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

    # FAQ content is often spread across more than one URL (e.g. a dedicated
    # /faq page plus a /support or /help page) — combine every keyword-matching
    # page's text into the routed source so none of it is silently dropped.
    for scaffold in scaffolds:
        if scaffold.is_legal or scaffold.page_type != "faq":
            continue
        primary = out.get(scaffold.slug)
        if primary is None:
            continue
        extras = [
            page
            for page in _match_by_keywords_all("faq", discovered)
            if page is not primary
        ]
        if extras:
            out[scaffold.slug] = _combine_sources(primary, extras)
            logger.debug(
                "Combined %d extra FAQ source(s) into scaffold %r",
                len(extras), scaffold.slug,
            )

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


def _match_by_keywords_all(
    page_type: str,
    discovered: list[SourceContent],
) -> list[SourceContent]:
    """Every discovered page whose path/title/headings mention this page_type
    (not just the first) — used to aggregate content spread across multiple
    matching URLs, e.g. FAQ content on both /faq and /support."""
    keywords = _PAGE_TYPE_KEYWORDS.get(page_type, ())
    if not keywords:
        return []
    return [page for page in discovered if any(kw in _source_haystack(page) for kw in keywords)]


def _combine_sources(primary: SourceContent, extras: list[SourceContent]) -> SourceContent:
    """Merge extra pages' text into the primary source so downstream generation
    (chunking, item extraction) sees content from every matching URL as one
    page. Only raw_text/headings are combined — title/url_path/images/links
    stay the primary's, since those describe the routed page's own identity."""
    raw_text = "\n\n".join(
        text for text in (primary.raw_text, *(p.raw_text for p in extras)) if text
    )
    headings = list(primary.headings)
    seen = {h.strip().lower() for h in headings if h.strip()}
    for page in extras:
        for h in page.headings:
            key = h.strip().lower()
            if key and key not in seen:
                headings.append(h)
                seen.add(key)
    return primary.model_copy(update={"raw_text": raw_text, "headings": headings})


def split_raw_text(
    raw_text: str, max_chars: int, headings: list[str] | None = None
) -> list[str]:
    """Split a page's text into chunks of at most ``max_chars`` characters.

    The extractor emits one block (paragraph / heading / list item) per line, so
    we split on newlines and greedily pack whole blocks into each chunk — block
    boundaries are never crossed unless a single block is itself larger than
    ``max_chars``, in which case it's hard-wrapped so a giant block can't stall
    the loop. Returns ``[raw_text]`` unchanged when the whole text already fits,
    so small pages stay a single pass.

    ``headings`` (the page's extracted headings, in document order) makes the
    split heading-aware: when the current chunk is at least half full and the
    next block is a heading line, the chunk is sealed there, so a source
    section (heading + its paragraphs + its photo context) stays whole inside
    one chunk instead of being cut mid-section. Greedy packing is the fallback
    for pages without headings.
    """
    text = raw_text or ""
    if len(text) <= max_chars:
        return [text]

    heading_set = {h.strip().lower() for h in (headings or []) if h.strip()}

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
        # Prefer sealing at a section boundary: a heading line starting a new
        # source section closes the chunk once it's usefully full, so the
        # section's heading and body travel to the LLM together.
        is_heading = block.strip().lower() in heading_set
        # +1 for the newline that will rejoin this block to the previous one.
        added = len(block) + (1 if current else 0)
        if current and (
            current_len + added > max_chars
            or (is_heading and current_len >= max_chars // 2)
        ):
            chunks.append("\n".join(current))
            current, current_len = [block], len(block)
        else:
            current.append(block)
            current_len += added

    if current:
        chunks.append("\n".join(current))
    return chunks


# Max real photos surfaced per page in the LLM prompt. Keeps the per-page
# image payload bounded (~30 tokens each) while covering a photo-per-section
# page comfortably.
MAX_PROMPT_IMAGES = 12

# Roles that never belong in a content slot — mirrors image_match's veto set.
# Portraits (grid headshots) are also unpromptable: offering them as pinnable
# image_refs lets the LLM blow a face up as a hero/section image, and on a
# directory page they crowd every real banner out of the MAX_PROMPT_IMAGES
# budget. Team photos don't need refs — they flow through profile_candidates.
_UNPROMPTABLE_ROLES = frozenset({"logo", "decoration", "portrait"})


def promptable_images(source: SourceContent) -> list[ImageMetadata]:
    """The page's content-grade photos, in a DETERMINISTIC order.

    This same list is recomputed by the post-parse binding pass
    (services/image_refs.py) to resolve the LLM's ``image_ref`` indexes back
    to URLs, so the filter and ordering must stay pure functions of the
    source's ``image_metadata``.
    """
    out: list[ImageMetadata] = []
    for meta in source.image_metadata or []:
        if meta.role in _UNPROMPTABLE_ROLES or meta.intent == "logo":
            continue
        if (meta.width and meta.width < 200) or (meta.height and meta.height < 120):
            continue
        out.append(meta)
        if len(out) >= MAX_PROMPT_IMAGES:
            break
    return out


def _image_prompt_entry(ref: int, meta: ImageMetadata) -> dict[str, object]:
    entry: dict[str, object] = {"ref": ref}
    if meta.alt:
        entry["alt"] = meta.alt[:80]
    elif meta.caption:
        entry["alt"] = meta.caption[:80]
    if meta.role != "unknown":
        entry["role"] = meta.role
    if meta.context_heading:
        entry["near"] = meta.context_heading[:60]
    return entry


# Per-section caps for the prompt tree. A section is a summary of the source's
# shape, not a transcript — the full text is still in raw_text underneath.
_PROMPT_MAX_SECTIONS = 12
_PROMPT_MAX_CARDS = 12
_PROMPT_CARD_BODY_CHARS = 200
_PROMPT_SECTION_PROSE_CHARS = 400


def _section_prompt_entry(section: SectionCandidate) -> dict[str, object]:
    entry: dict[str, object] = {
        "heading": section.heading,
        "level": section.level,
    }
    if section.prose:
        entry["prose"] = section.prose[:_PROMPT_SECTION_PROSE_CHARS]
    if section.cards:
        entry["card_kind"] = section.card_kind
        entry["cards"] = [
            {
                k: v
                for k, v in (
                    ("title", card.title),
                    ("body", card.body[:_PROMPT_CARD_BODY_CHARS]),
                    ("meta", card.meta or None),
                    ("has_image", bool(card.image_url) or None),
                )
                if v
            }
            for card in section.cards[:_PROMPT_MAX_CARDS]
        ]
    return entry


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

    ``sections`` is the page's own structure (services/section_extraction.py):
    which heading opens a section, and which cards sit inside it. Without it the
    model sees only ``headings`` — a flat, level-less list in which an h2 that
    spans four cards and an h3 that titles one of them are indistinguishable —
    and it has to rebuild the tree by guesswork. That guesswork is what merged
    Glorykids' six sections into one. ``raw_text`` stays as the full-fidelity
    fallback for pages whose markup yields no tree.

    Returns a JSON-serialisable dict.
    """
    raw_text = source.raw_text or ""
    body = text_override if text_override is not None else raw_text[:max_chars]
    payload: dict[str, object] = {
        "url_path": source.url_path or "/",
        "title": source.title,
        "headings": (source.headings or [])[:max_headings],
        "raw_text": body,
        "raw_text_char_count": len(raw_text),
    }
    sections = source.section_candidates or []
    if sections:
        payload["sections"] = [
            _section_prompt_entry(s) for s in sections[:_PROMPT_MAX_SECTIONS]
        ]
    images = promptable_images(source)
    if images:
        payload["images"] = [
            _image_prompt_entry(i, meta) for i, meta in enumerate(images)
        ]
    return payload
