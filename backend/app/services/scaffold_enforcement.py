"""
Post-LLM enforcement: align an LLM-produced PagePlan to a PageScaffold.

The scaffold owns the ORDER and the allowed set of section kinds; the LLM owns
the words AND which content sections it can honestly fill. This module bridges
them — for each scaffold-required kind it finds the LLM's matching block,
reorders to match, and drops extras.

Fidelity policy (per product decision: improve scraped content, never invent):
when the LLM omits a requested section, that means the source had nothing to
ground it — so we OMIT it too, rather than padding with fabricated testimonials,
prices, team members, FAQs, etc. The only exception is a small set of
"structural" kinds (hero / about / cta / contact) that carry no invented facts;
those we keep with a minimal, placeholder-only default so the page isn't blank.
"""

from __future__ import annotations

import difflib
import logging
import re

from app.models.content_blocks import (
    AboutBlock,
    AwardsBlock,
    ClientsBlock,
    ContactBlock,
    ContentBlock,
    CtaBlock,
    HeroBlock,
    PagePlan,
    StatsBlock,
    TeamBlock,
    TestimonialItem,
    TestimonialsBlock,
    TimelineBlock,
)
from app.models.industry import PageScaffold

logger = logging.getLogger(__name__)


# Kinds we may synthesize when the LLM omits them, because their default copy
# asserts NO facts the source has to back up:
#   - hero    → restates the page's own real title/topic
#   - about   → empty placeholder body the user fills in the builder
#   - cta     → a generic call-to-action (not a factual claim)
#   - contact → a contact form with no fabricated details
# Every OTHER kind (features, services, testimonials, faq, pricing, team,
# gallery, menu, process, timeline, awards, clients, stats, …) makes specific
# factual claims, so if the LLM didn't produce it we drop it instead of
# inventing content.
_STRUCTURAL_FALLBACK_KINDS = frozenset({"hero", "about", "cta", "contact"})

_NON_PERSON_EXACT_NAMES = {
    "board",
    "committee",
    "leadership",
    "management",
    "management team",
    "marketing team",
    "meet the team",
    "our team",
    "specialists",
    "team",
    "web design",
    "getting involved",
    "good food",
    "latest events",
    "latest news",
    "upcoming events",
}
_NON_PERSON_NAME_TOKENS = {
    "achievement",
    "achievements",
    "award",
    "awards",
    "card",
    "cards",
    "certification",
    "certifications",
    "department",
    "departments",
    "design",
    "engineering",
    "events",
    "faq",
    "faqs",
    "info",
    "leadership",
    "management",
    "marketing",
    "membership",
    "news",
    "portfolio",
    "product",
    "products",
    "service",
    "services",
    "solution",
    "solutions",
    "specialist",
    "specialists",
    "stack",
    "support",
    "team",
    "technology",
    "values",
    "web",
}
_NON_NAME_LEAD_TOKENS = {
    "a",
    "about",
    "an",
    "how",
    "meet",
    "our",
    "the",
    "these",
    "this",
    "what",
    "when",
    "where",
    "who",
    "why",
    "your",
    # Gerunds / present participles — section headings, not person names.
    "getting", "giving", "making", "building", "going", "coming",
    "taking", "bringing", "keeping", "sharing", "starting", "creating",
    "becoming", "growing", "supporting", "helping", "working", "leading",
    "serving", "living", "doing",
    # CTA verbs not already covered.
    "welcome", "contact", "discover", "explore", "join", "visit", "view",
    "see", "find", "get", "learn", "read", "book", "call",
    # Adjectives that open section headings.
    "good", "great", "best", "new", "fresh", "clean", "latest",
    "upcoming", "featured", "popular", "top", "free",
}
_NAME_PARTICLES = {
    "al",
    "bin",
    "binte",
    "binti",
    "da",
    "de",
    "del",
    "della",
    "den",
    "der",
    "di",
    "el",
    "la",
    "le",
    "ter",
    "ten",
    "van",
    "von",
}


def _block_kind(block: ContentBlock) -> str:
    """Pydantic discriminated union — pull `kind` off the discriminator."""
    return block.kind  # type: ignore[attr-defined]


def looks_like_team_member_name(value: str | None) -> bool:
    """Conservative final gate for generated/scraped team member names."""
    if not value:
        return False
    text = " ".join(value.replace("\xa0", " ").split()).strip(" :|-")
    if not text:
        return False
    low = text.lower()
    if low in _NON_PERSON_EXACT_NAMES:
        return False
    if "@" in text or "http" in low:
        return False
    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z'.-]*", text) if t]
    if len(tokens) < 2 or len(tokens) > 7:
        return False
    lowered = [t.lower() for t in tokens]
    if lowered[0] in _NON_NAME_LEAD_TOKENS:
        return False
    if any(t in _NON_PERSON_NAME_TOKENS for t in lowered):
        return False
    for token in tokens:
        if token[0].isupper() or token.lower() in _NAME_PARTICLES:
            continue
        return False
    return True


def _sanitize_team_block(block: TeamBlock) -> TeamBlock | None:
    members = [
        member for member in block.members
        if looks_like_team_member_name(member.name)
    ]
    if not members:
        return None
    return block.model_copy(update={"members": members})


# Generic placeholder names the LLM reaches for when it has no real reviewer
# to cite (despite the FIDELITY rule against fabricating testimonials). Exact,
# case-insensitive matches only — real people are occasionally named "John"
# or "Smith", so this only catches the textbook full-name placeholders.
_PLACEHOLDER_AUTHOR_NAMES = {
    "john doe",
    "jane doe",
    "john smith",
    "jane smith",
    "j. doe",
    "j. smith",
    "test user",
    "sample user",
    "anonymous",
    "anonymous customer",
    "satisfied customer",
    "happy customer",
    "valued customer",
    "customer name",
    "your name",
    "first last",
    "firstname lastname",
    "name surname",
}


def looks_like_placeholder_author(value: str | None) -> bool:
    """True when a testimonial author is a textbook fabricated placeholder."""
    if not value:
        return False
    normalized = " ".join(value.replace("\xa0", " ").split()).strip(" :|-").lower()
    return normalized in _PLACEHOLDER_AUTHOR_NAMES


def _normalize_for_grounding(text: str) -> str:
    return " ".join(text.lower().split())


def _longest_match_len(needle: str, haystack: str) -> int:
    matcher = difflib.SequenceMatcher(None, needle, haystack, autojunk=False)
    match = matcher.find_longest_match(0, len(needle), 0, len(haystack))
    return match.size


def is_grounded_in_source(text: str | None, source_text: str | None) -> bool:
    """True when `text` plausibly came FROM `source_text`.

    Catches fabrication the placeholder denylist misses (a fluent, plausible
    quote/name the LLM invented despite the FIDELITY rule). Tolerant of minor
    whitespace/punctuation cleanup — NOT of paraphrase, since the prompt tells
    the LLM testimonial quotes must be preserved verbatim, not rewritten.
    Short strings (e.g. a first-name-only author) require an exact substring;
    longer strings accept a long contiguous fuzzy match so trivial scraping
    noise (an extra space, a smart quote) doesn't trip a false negative.
    """
    if not text or not source_text:
        return False
    needle = _normalize_for_grounding(text)
    haystack = _normalize_for_grounding(source_text)
    if not needle:
        return False
    if needle in haystack:
        return True
    if len(needle) < 15:
        return False  # too short for fuzzy matching to be meaningful
    threshold = max(15, int(len(needle) * 0.6))
    return _longest_match_len(needle, haystack) >= threshold


def _sanitize_testimonials_block(
    block: TestimonialsBlock, source_text: str | None
) -> TestimonialsBlock | None:
    items: list[TestimonialItem] = []
    dropped_fabricated = 0
    for item in block.items:
        if looks_like_placeholder_author(item.author):
            continue
        # No source text to check against (e.g. legacy callers / unit tests) →
        # fall back to the placeholder-name check only.
        if source_text and not (
            is_grounded_in_source(item.quote, source_text)
            or is_grounded_in_source(item.author, source_text)
        ):
            dropped_fabricated += 1
            continue
        items.append(item)
    if dropped_fabricated:
        logger.info(
            "Dropped %d testimonial(s) with no match in the page source — "
            "likely fabricated despite the fidelity rule.",
            dropped_fabricated,
        )
    if not items:
        return None
    return block.model_copy(update={"items": items})


_YEAR_RE = re.compile(r"(1[89]\d{2}|20\d{2})")


def _parsed_year(value: str | None) -> int | None:
    if not value:
        return None
    match = _YEAR_RE.search(value)
    return int(match.group(1)) if match else None


def _sort_timeline_items(items: list) -> list:
    """Chronological order by parsed year; undated items sink to the end.

    Stable on ties (same year, or no year at all) so items the LLM grouped
    together stay together rather than being shuffled.
    """
    indexed = list(enumerate(items))

    def sort_key(pair: tuple[int, object]) -> tuple[int, int, int]:
        idx, item = pair
        year = _parsed_year(getattr(item, "year", None))
        return (0, year, idx) if year is not None else (1, 0, idx)

    return [item for _, item in sorted(indexed, key=sort_key)]


def _sanitize_timeline_block(
    block: TimelineBlock, source_text: str | None
) -> TimelineBlock | None:
    """Drop ungrounded milestones, then put what survives in chronological order.

    A 2-page source chunked across multiple LLM calls (see
    `_generate_page_multipass` in planner.py) can come back with milestones in
    chunk-arrival order rather than year order — this is the single place that
    always re-sorts, regardless of whether the page was generated in one call
    or merged from several.
    """
    items = block.items
    if source_text:
        items = [
            item for item in items
            if is_grounded_in_source(item.year, source_text)
            or is_grounded_in_source(item.title, source_text)
        ]
        if not items:
            return None
    return block.model_copy(update={"items": _sort_timeline_items(items)})


def _sanitize_awards_block(
    block: AwardsBlock, source_text: str | None
) -> AwardsBlock | None:
    if not source_text:
        return block
    items = [
        item for item in block.items
        if is_grounded_in_source(item.title, source_text)
        or is_grounded_in_source(item.issuer, source_text)
    ]
    if not items:
        return None
    return block.model_copy(update={"items": items})


def _sanitize_clients_block(
    block: ClientsBlock, source_text: str | None
) -> ClientsBlock | None:
    if not source_text:
        return block
    items = [item for item in block.items if is_grounded_in_source(item.name, source_text)]
    if not items:
        return None
    return block.model_copy(update={"items": items})


def _sanitize_stats_block(
    block: StatsBlock, source_text: str | None
) -> StatsBlock | None:
    if not source_text:
        return block
    items = [
        item for item in block.items
        if is_grounded_in_source(item.value, source_text)
        or is_grounded_in_source(item.label, source_text)
    ]
    if not items:
        return None
    return block.model_copy(update={"items": items})


# Page-type-aware fallback stock phrase for a hero the LLM left blank. Keyed
# by the SCAFFOLD's page_type (deterministic — see industry_templates.py),
# not PagePlan.page_type, which the LLM merely echoes and can drift (see
# heal_page_type). Content-sparse interior pages (contact, faq) are where the
# LLM most often omits image_query — the source gives it nothing concrete to
# phrase a photo around — so those get a topical, Pexels-friendly phrase
# instead of falling through to a generic brand-name query.
_HERO_IMAGE_QUERY_BY_PAGE_TYPE: dict[str, str] = {
    "contact": "welcoming modern office reception",
    "about": "team collaborating in bright office",
    "team": "professional team portrait office",
    "faq": "friendly customer support team",
    "services": "professional providing service to client",
}


def _default_hero_image_query(page_type: str, brand_name: str) -> str:
    """Stock search phrase for a hero with no image_query, by page type.

    Falls back to a brand-name phrase (mirrors _default_block's
    f"{brand_name} brand photo") for any page type not in the map above.
    """
    return _HERO_IMAGE_QUERY_BY_PAGE_TYPE.get(page_type, f"{brand_name} team at work")


def _backfill_hero_image_query(
    block: HeroBlock, *, page_type: str, brand_name: str
) -> HeroBlock:
    """Fill a blank `image_query` on an LLM-produced hero block.

    The LLM sometimes leaves `image_query` blank on content-sparse interior
    pages despite the prompt's blanket "always fill visual query fields"
    instruction — HeroBlock has no `heal_*` validator for this field (unlike
    primary_cta_label/href, image_ref, layout on the same model), so nothing
    repairs it before this point. Left blank, schema_builder's resolver never
    queries Pexels for the slot and the hero silently degrades to a flat-
    colour gradient template instead of a photo.

    A no-op when the block already has a query, or already has a bound
    scraped photo (image_ref/image_url) that resolves independently.
    """
    if (block.image_query or "").strip():
        return block
    if block.image_ref is not None or (block.image_url or "").strip():
        return block
    return block.model_copy(
        update={"image_query": _default_hero_image_query(page_type, brand_name)}
    )


def sanitize_blocks_against_source(
    blocks: list[ContentBlock], source_text: str | None
) -> list[ContentBlock]:
    """Scaffold-free equivalent of the per-kind sanitization inside
    ``align_page_to_scaffold``, for the legacy free-form ``/from-source`` flow.

    That flow has no PageScaffold to align against (the LLM picks pages and
    sections freely), so it never went through ``align_page_to_scaffold`` —
    which meant testimonials/awards/clients/stats fabrication checks never
    ran there at all. This applies the same kind-specific sanitizers directly
    to a page's block list, dropping any block that ends up with zero
    surviving items. Team and structural kinds are left untouched here (team
    keeps its existing placeholder-name check elsewhere; hero/about/cta/contact
    carry no invented facts).
    """
    sanitized: list[ContentBlock] = []
    for block in blocks:
        kind = _block_kind(block)
        result: ContentBlock | None = block
        if kind == "team" and isinstance(block, TeamBlock):
            result = _sanitize_team_block(block)
        elif kind == "testimonials" and isinstance(block, TestimonialsBlock):
            result = _sanitize_testimonials_block(block, source_text)
        elif kind == "timeline" and isinstance(block, TimelineBlock):
            result = _sanitize_timeline_block(block, source_text)
        elif kind == "awards" and isinstance(block, AwardsBlock):
            result = _sanitize_awards_block(block, source_text)
        elif kind == "clients" and isinstance(block, ClientsBlock):
            result = _sanitize_clients_block(block, source_text)
        elif kind == "stats" and isinstance(block, StatsBlock):
            result = _sanitize_stats_block(block, source_text)
        if result is not None:
            sanitized.append(result)
    return sanitized


def align_page_to_scaffold(
    page: PagePlan,
    scaffold: PageScaffold,
    *,
    brand_name: str = "Untitled",
    source_text: str | None = None,
) -> PagePlan:
    """
    Reorder + filter `page.blocks` to match `scaffold.sections`.

    - For each kind in scaffold.sections (in order):
        - if the LLM produced a matching block, use the first one
        - else if the kind is a structural fallback, inject a minimal default
        - else OMIT it (the source couldn't ground it — don't fabricate)
    - Drop any LLM blocks whose kind isn't in scaffold.sections
    - Guarantee the page isn't blank: if nothing survived, keep a hero.
    - Returns a new PagePlan; original is unmodified

    ``source_text`` is this page's own scraped/document text (the same text
    the LLM was grounded against). When given, the fact-bearing kinds most
    prone to plausible fabrication — testimonials, awards, clients, stats —
    are additionally checked item-by-item against it (see
    ``is_grounded_in_source``): an item with no match in the source is
    dropped, not just textbook placeholders like "John Doe". When omitted,
    only the placeholder-name check (testimonials only) applies.
    """
    required_kinds = list(scaffold.sections)
    by_kind: dict[str, list[ContentBlock]] = {}
    for blk in page.blocks:
        by_kind.setdefault(_block_kind(blk), []).append(blk)

    aligned_blocks: list[ContentBlock] = []
    structural_filled: list[str] = []
    omitted: list[str] = []
    occurrence: dict[str, int] = {}
    for kind in required_kinds:
        occurrence[kind] = occurrence.get(kind, 0) + 1
        bucket = by_kind.get(kind, [])
        if bucket:
            block = bucket.pop(0)
            if kind == "team" and isinstance(block, TeamBlock):
                sanitized = _sanitize_team_block(block)
                if sanitized is None:
                    omitted.append(kind)
                    continue
                block = sanitized
            elif kind == "testimonials" and isinstance(block, TestimonialsBlock):
                sanitized = _sanitize_testimonials_block(block, source_text)
                if sanitized is None:
                    omitted.append(kind)
                    continue
                block = sanitized
            elif kind == "timeline" and isinstance(block, TimelineBlock):
                sanitized = _sanitize_timeline_block(block, source_text)
                if sanitized is None:
                    omitted.append(kind)
                    continue
                block = sanitized
            elif kind == "awards" and isinstance(block, AwardsBlock):
                sanitized = _sanitize_awards_block(block, source_text)
                if sanitized is None:
                    omitted.append(kind)
                    continue
                block = sanitized
            elif kind == "clients" and isinstance(block, ClientsBlock):
                sanitized = _sanitize_clients_block(block, source_text)
                if sanitized is None:
                    omitted.append(kind)
                    continue
                block = sanitized
            elif kind == "stats" and isinstance(block, StatsBlock):
                sanitized = _sanitize_stats_block(block, source_text)
                if sanitized is None:
                    omitted.append(kind)
                    continue
                block = sanitized
            elif kind == "hero" and isinstance(block, HeroBlock):
                block = _backfill_hero_image_query(
                    block, page_type=scaffold.page_type, brand_name=brand_name
                )
            aligned_blocks.append(block)
        elif kind in _STRUCTURAL_FALLBACK_KINDS and occurrence[kind] == 1:
            # Only the FIRST occurrence of a structural kind gets a placeholder
            # default. Story pages request `about` several times (one per source
            # section) — a shortfall there means the LLM couldn't ground that
            # many sections, so the extras are omitted, not padded with blanks.
            aligned_blocks.append(_default_block(kind, page=page, brand_name=brand_name))
            structural_filled.append(kind)
        else:
            omitted.append(kind)

    if omitted:
        logger.info(
            "Page '%s' omitted ungrounded section(s) %s — source had no facts "
            "to fill them, so they were dropped rather than fabricated.",
            page.title,
            ", ".join(omitted),
        )
    if structural_filled:
        logger.info(
            "Page '%s' filled structural section(s) %s with placeholder defaults.",
            page.title,
            ", ".join(structural_filled),
        )

    dropped_kinds = {k: len(v) for k, v in by_kind.items() if v}
    if dropped_kinds:
        logger.info(
            "Page '%s' had extra LLM blocks not in scaffold — dropped: %s",
            page.title,
            dropped_kinds,
        )

    # Never ship a totally blank page — fall back to a hero from the real title.
    if not aligned_blocks:
        logger.info(
            "Page '%s' had no groundable sections — keeping a title-based hero.",
            page.title,
        )
        aligned_blocks.append(_default_block("hero", page=page, brand_name=brand_name))

    return page.model_copy(update={"blocks": aligned_blocks})


# --- minimal structural defaults (no fabricated facts) --------------------------


def _default_block(
    kind: str, *, page: PagePlan, brand_name: str
) -> ContentBlock:
    """
    Build a minimal, fact-free default for a structural section the LLM omitted.

    Only ``_STRUCTURAL_FALLBACK_KINDS`` are produced here — these carry no claims
    the source must support (a hero echoing the page title, a placeholder about,
    a generic CTA, an empty contact form). Fact-bearing sections are never
    defaulted; the caller omits them instead.
    """
    if kind == "hero":
        return HeroBlock(
            headline=page.title if page.title else f"Welcome to {brand_name}",
            subheadline=page.description or None,
            primary_cta_label="Get in touch",
            primary_cta_href="#contact",
            image_query=f"{brand_name} brand photo",
            layout="split",
        )
    if kind == "about":
        return AboutBlock(
            heading=f"About {brand_name}",
            # Intentionally empty — the builder prompts the user to add their
            # real story. We don't write a fictional one.
            body="",
            image_query=f"{brand_name} team or workplace",
        )
    if kind == "cta":
        return CtaBlock(
            headline=f"Get in touch with {brand_name}",
            subheadline="Let's start a conversation.",
            cta_label="Get in touch",
            cta_href="#contact",
            background_query="bright modern interior",
        )
    if kind == "contact":
        return ContactBlock(
            heading="Get in touch",
            subheading="We'd love to hear from you.",
        )

    # Should never happen: the caller only defaults structural kinds. Fall back
    # to a hero rather than fabricating a fact-bearing section.
    logger.warning(
        "No structural default for kind=%s; using a title-based hero instead.", kind
    )
    return HeroBlock(
        headline=page.title if page.title else f"Welcome to {brand_name}",
        subheadline=page.description or None,
        primary_cta_label="Get in touch",
        primary_cta_href="#contact",
        image_query=f"{brand_name} brand photo",
        layout="split",
    )
