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

import logging

from app.models.content_blocks import (
    AboutBlock,
    ContactBlock,
    ContentBlock,
    CtaBlock,
    HeroBlock,
    PagePlan,
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
# gallery, menu, process, …) makes specific factual claims, so if the LLM
# didn't produce it we drop it instead of inventing content.
_STRUCTURAL_FALLBACK_KINDS = frozenset({"hero", "about", "cta", "contact"})


def _block_kind(block: ContentBlock) -> str:
    """Pydantic discriminated union — pull `kind` off the discriminator."""
    return block.kind  # type: ignore[attr-defined]


def align_page_to_scaffold(
    page: PagePlan,
    scaffold: PageScaffold,
    *,
    brand_name: str = "Untitled",
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
    """
    required_kinds = list(scaffold.sections)
    by_kind: dict[str, list[ContentBlock]] = {}
    for blk in page.blocks:
        by_kind.setdefault(_block_kind(blk), []).append(blk)

    aligned_blocks: list[ContentBlock] = []
    structural_filled: list[str] = []
    omitted: list[str] = []
    for kind in required_kinds:
        bucket = by_kind.get(kind, [])
        if bucket:
            aligned_blocks.append(bucket.pop(0))
        elif kind in _STRUCTURAL_FALLBACK_KINDS:
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
