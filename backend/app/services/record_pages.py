"""
Look-alike detail pages: laid out once per template, filled verbatim per page.

A catalogue site gives every product its own page, built from the same template
— feruni.com has 170 of them under /product/*. Planning each one through the
content LLM costs a batch per few pages (50+ calls, hours on local hardware) and
has the model rewrite product specifications, the kind of fact that must never
be paraphrased. Yet the pages share their structure exactly; only the words and
photos differ. So a set of them is treated like a set of translated mirrors
(services/translations.py): one decision, reused.

**The model answers in section numbers, never text** — the contract
``paste_structure`` and ``bio_condense`` already run on. It is shown ONE
exemplar page's section outline and says which sections become which block
(hero, gallery, …). That template is then applied to every page in the set,
each reading ITS OWN section at the same position, so:

1. every word and photo on a record page is the page's own source content —
   nothing can be invented or moved from one product to another;
2. a set of any size costs one small call, cached like any other;
3. every page in the set shares one layout by construction.

Positions line up because a set is defined by its template signature (the
shape of each section, in order) — see ``mark_record_sets``. A page whose
section is missing simply skips that block.

Failure is always a fallback, never an error: the flag off, no client, an
``LlmError``, or a reply with nothing usable all yield
``default_record_template``, a deterministic layout of the same kind.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from pydantic import BaseModel, Field, ValidationError

from app.config import settings
from app.models.content_blocks import (
    AboutBlock,
    ContentBlock,
    FeatureItem,
    FeaturesBlock,
    HeroBlock,
    PagePlan,
    SectionCandidate,
    SourceContent,
)
from app.models.industry import PageScaffold
from app.services.llm import LlmClient, chat_json_cached, get_llm
from app.services.prompts import RECORD_TEMPLATE_PROMPT
from app.services.source_injection import gallery_block_from_section, max_items
from app.services.source_router import promptable_images

logger = logging.getLogger(__name__)

# A section showing at least this many pictures reads as a gallery in the
# deterministic default template — mirrors generate._MIN_ALBUM_PHOTOS.
_DEFAULT_GALLERY_MIN_IMAGES = 3
# How much of a section's text the outline shows the model: enough to tell a
# disclaimer from a description, never enough to tempt a rewrite.
_OUTLINE_EXCERPT_CHARS = 200
# A hero shows its section's text only when that text is a tagline ("colour
# design"). Anything longer is the page's content and belongs to the section's
# content block — so no words appear twice and nothing overflows a hero.
_TAGLINE_MAX_CHARS = 160
# Words every page of a set must end a section with before that shared tail is
# the template's (a heading-less footer, share buttons) rather than coincidence.
_SHARED_TAIL_MIN_WORDS = 8
_WORD_RE = re.compile(r"\S+")

TemplateSignature = tuple[tuple[int, bool, bool], ...]


# --- which pages form a set -------------------------------------------------------


def template_signature(page: SourceContent) -> TemplateSignature:
    """The shape of a page's sections, in order: heading level and whether each
    holds images and cards. Two pages built from one template share it even
    though every heading and sentence differs (Relievo's "SHADOW PLAY" and
    Eterna's "HERITAGE ETERNITY" sit in the same slot)."""
    return tuple(
        (section.level, bool(section.image_urls), bool(section.cards))
        for section in page.section_candidates or ()
    )


def mark_record_sets(
    scaffolds: list[PageScaffold], sources: Mapping[str, SourceContent]
) -> None:
    """Stamp ``record_set`` on sub-pages that share a parent and a template.

    A set needs ``settings.record_set_min_pages`` members. Pages already routed
    elsewhere are left alone: a roster-linked profile page (``menu_hidden``
    before this pass) has its own verbatim profile pipeline, and translations,
    legal and top-level pages are never records. A page with no section
    structure has no signature to share.

    Members become ``menu_hidden``: a reader reaches a product from its listing,
    and 170 products are not a dropdown.
    """
    groups: dict[tuple[str, TemplateSignature], list[PageScaffold]] = {}
    for scaffold in scaffolds:
        source = sources.get(scaffold.slug)
        if (
            source is None
            or not scaffold.parent_slug
            or not scaffold.from_source
            or scaffold.menu_hidden
            or scaffold.locale
            or scaffold.is_legal
        ):
            continue
        signature = template_signature(source)
        if signature:
            groups.setdefault((scaffold.parent_slug, signature), []).append(scaffold)

    for (parent_slug, _signature), members in groups.items():
        if len(members) < settings.record_set_min_pages:
            continue
        # The richest page gives the model the most to judge the layout by.
        exemplar = min(members, key=lambda s: (-len(sources[s.slug].raw_text or ""), s.slug))
        for member in members:
            member.record_set = exemplar.slug
            member.menu_hidden = True
            member.rationale = (
                f"One of {len(members)} /{parent_slug} pages built from the same "
                f"template — laid out once from /{exemplar.slug}, filled with its own content."
            )
        logger.info(
            "Record set /%s/*: %d pages share one template (exemplar /%s)",
            parent_slug, len(members), exemplar.slug,
        )


def without_shared_tails(pages: list[SourceContent]) -> list[SourceContent]:
    """The set's pages with the text they ALL end a section with removed.

    A page builder's footer carries no heading, so the section extractor hands
    its text — and the share buttons above it — to whichever section comes
    last. On feruni.com every product's last section ended with the same ~400
    characters ("Share on facebook … COPYRIGHT 2023"). The LLM path paraphrases
    around that; verbatim filling would publish it on every page. Text a whole
    set shares word for word says nothing about any one item, so a shared tail
    of at least ``_SHARED_TAIL_MIN_WORDS`` words is cut, leaving each page's own
    words exactly as written. Copies — never mutates what the caller owns.
    """
    section_counts = {len(page.section_candidates) for page in pages}
    if len(pages) < 2 or len(section_counts) != 1:
        return pages
    tails = [
        _shared_tail_words([page.section_candidates[index].prose for page in pages])
        for index in range(section_counts.pop())
    ]
    if not any(tails):
        return pages
    return [
        page.model_copy(
            update={
                "section_candidates": [
                    section.model_copy(update={"prose": _without_last_words(section.prose, words)})
                    if words
                    else section
                    for section, words in zip(page.section_candidates, tails)
                ]
            }
        )
        for page in pages
    ]


def _shared_tail_words(texts: list[str]) -> int:
    """How many trailing words every non-empty text ends with (0 below the minimum)."""
    words = [text.split() for text in texts if text.strip()]
    if len(words) < 2:
        return 0
    shared = 0
    for column in zip(*(reversed(w) for w in words)):
        if len(set(column)) != 1:
            break
        shared += 1
    return shared if shared >= _SHARED_TAIL_MIN_WORDS else 0


def _without_last_words(text: str, count: int) -> str:
    matches = list(_WORD_RE.finditer(text))
    return text[: matches[-count].start()].rstrip() if len(matches) > count else ""


# --- the template -----------------------------------------------------------------


class RecordSlot(BaseModel):
    kind: str
    section: int = Field(description="1-based number of a section in the outline.")


class RecordTemplate(BaseModel):
    """Which outline sections a set's pages show, and as what block."""

    slots: list[RecordSlot] = Field(default_factory=list)


def _first_content_photo(page: SourceContent) -> str | None:
    return next((image.url for image in promptable_images(page)), None)


def _is_tagline(prose: str) -> bool:
    return 0 < len(prose.strip()) <= _TAGLINE_MAX_CHARS


def _hero_block(section: SectionCandidate, page: SourceContent) -> HeroBlock | None:
    if not section.heading.strip():
        return None
    return HeroBlock(
        headline=section.heading.strip(),
        subheadline=section.prose.strip() if _is_tagline(section.prose) else None,
        image_url=next(iter(section.image_urls), None) or _first_content_photo(page),
    )


def _about_block(section: SectionCandidate, page: SourceContent) -> AboutBlock | None:
    if not section.prose.strip():
        return None
    return AboutBlock(
        heading=section.heading,
        body=section.prose.strip(),
        image_url=next(iter(section.image_urls), None),
    )


def _gallery_block(section: SectionCandidate, page: SourceContent) -> ContentBlock | None:
    return gallery_block_from_section(section) if section.image_urls else None


def _features_block(section: SectionCandidate, page: SourceContent) -> FeaturesBlock | None:
    items = [
        FeatureItem(
            title=card.title,
            description=card.body,
            image_url=card.image_url,
            image_alt=card.image_alt or None,
        )
        for card in section.cards
        if card.title and card.body
    ][: max_items(FeaturesBlock)]
    if not items:
        return None
    return FeaturesBlock(
        heading=section.heading, subheading=section.prose.strip() or None, items=items
    )


@dataclass(frozen=True)
class _SlotKind:
    use_for: str
    build: Callable[[SectionCandidate, SourceContent], ContentBlock | None]


# The one registry of record-page blocks: the prompt lists these kinds, replies
# are validated against them, and filling dispatches through them. A new kind is
# one entry here.
_SLOT_KINDS: dict[str, _SlotKind] = {
    "hero": _SlotKind("the section that names the page — exactly one", _hero_block),
    "about": _SlotKind("a heading with a description in prose", _about_block),
    "gallery": _SlotKind(
        "a section whose pictures ARE its content — photos, colours, swatches", _gallery_block
    ),
    "features": _SlotKind(
        "a section of repeated cards, each with its own title and text", _features_block
    ),
}


def _content_kind(section: SectionCandidate, *, text_shown: bool) -> str | None:
    """The content block a section's holdings call for; ``text_shown`` when the
    hero already shows its text as a tagline."""
    if len(section.image_urls) >= _DEFAULT_GALLERY_MIN_IMAGES:
        return "gallery"
    if section.cards:
        return "features"
    if section.prose.strip() and not text_shown:
        return "about"
    return None


def default_record_template(exemplar: SourceContent) -> RecordTemplate:
    """A deterministic layout: the first section is the hero (plus a content
    block when it holds more than a tagline), then each section by what it
    holds. A heading already shown is a responsive duplicate (page builders
    print sections twice, for desktop and mobile) and is skipped."""
    slots: list[RecordSlot] = []
    shown: set[str] = set()
    for number, section in enumerate(exemplar.section_candidates or (), start=1):
        heading = section.heading.strip().lower()
        if heading in shown:
            continue
        shown.add(heading)
        is_hero = not slots
        if is_hero:
            slots.append(RecordSlot(kind="hero", section=number))
        kind = _content_kind(section, text_shown=is_hero and _is_tagline(section.prose))
        if kind is not None:
            slots.append(RecordSlot(kind=kind, section=number))
    return RecordTemplate(slots=slots)


def _usable(template: RecordTemplate, section_count: int) -> RecordTemplate:
    """Known kinds and real section numbers; one hero, and at most one content
    block per section — the hero first, then source order. Whatever else a reply
    said is dropped."""
    hero: RecordSlot | None = None
    content: dict[int, RecordSlot] = {}
    for slot in template.slots:
        if slot.kind not in _SLOT_KINDS or not 1 <= slot.section <= section_count:
            continue
        if slot.kind == "hero":
            hero = hero or slot
        else:
            content.setdefault(slot.section, slot)
    ordered = sorted(content.values(), key=lambda s: s.section)
    return RecordTemplate(slots=[hero, *ordered] if hero else ordered)


def _outline(exemplar: SourceContent) -> str:
    """The exemplar's sections as numbered rows — the only thing the model sees."""
    rows = []
    for number, section in enumerate(exemplar.section_candidates or (), start=1):
        excerpt = " ".join(section.prose.split())[:_OUTLINE_EXCERPT_CHARS]
        rows.append(
            f"{number}. [h{section.level}] {section.heading} — \"{excerpt}\" — "
            f"{len(section.image_urls)} images, {len(section.cards)} cards"
        )
    return "\n".join(rows)


async def plan_record_template(
    exemplar: SourceContent, *, client: LlmClient | None = None
) -> RecordTemplate:
    """The layout for a record set, chosen from its exemplar. Never raises."""
    fallback = default_record_template(exemplar)
    section_count = len(exemplar.section_candidates or ())
    if not settings.record_template_llm_enabled or not section_count:
        return fallback
    kinds = "\n".join(f'- "{name}": {kind.use_for}' for name, kind in _SLOT_KINDS.items())
    try:
        reply = await chat_json_cached(
            client or get_llm(),
            system_prompt=RECORD_TEMPLATE_PROMPT.format(kinds=kinds),
            user_prompt=_outline(exemplar),
            schema=RecordTemplate,
            temperature=settings.plan_temperature,
        )
    except Exception as exc:  # noqa: BLE001 — LlmError included; a layout is never worth a failed build
        logger.warning(
            "Record template for %s failed (%s) — using the default layout",
            exemplar.source_ref, exc,
        )
        return fallback
    usable = _usable(reply, section_count)
    if not any(slot.kind == "hero" for slot in usable.slots):
        return fallback
    return usable


# --- filling ----------------------------------------------------------------------


def fill_record_page(
    template: RecordTemplate, scaffold: PageScaffold, source: SourceContent
) -> PagePlan:
    """One record page from the set's template and the page's own source.

    Pure and deterministic. Photos are set as ``image_url`` directly (the
    picture-wall idiom), so no stock search or ref binding runs for them. Button
    labels are the block models' own defaults, as on every generated page.
    """
    sections = source.section_candidates or []
    hero_sections = {slot.section for slot in template.slots if slot.kind == "hero"}
    blocks: list[ContentBlock] = []
    for slot in template.slots:
        if slot.section > len(sections):
            continue
        section = sections[slot.section - 1]
        if slot.kind != "hero" and slot.section in hero_sections and _is_tagline(section.prose):
            section = section.model_copy(update={"prose": ""})  # the hero shows it already
        block = _SLOT_KINDS[slot.kind].build(section, source)
        if block is None and slot.kind != "hero":
            # This page's section can't fill the set's block (cards without text,
            # a rack without photos) — its words are still the page's content.
            block = _about_block(section, source)
        if block is not None:
            blocks.append(block)
    if not any(block.kind == "hero" for block in blocks):
        # A page must open with something: its own title and first photo.
        blocks.insert(
            0, HeroBlock(headline=scaffold.title, image_url=_first_content_photo(source))
        )
    return PagePlan(
        page_type=scaffold.page_type,
        slug=scaffold.slug,
        title=scaffold.title,
        description=scaffold.description,
        blocks=blocks,
        seo_title=source.title or scaffold.title,
        seo_description=source.description or "",
        parent_slug=scaffold.parent_slug,
        from_source=True,
        menu_hidden=scaffold.menu_hidden,
    )


async def build_record_pages(
    scaffolds: list[PageScaffold],
    sources: Mapping[str, SourceContent],
    *,
    client: LlmClient | None = None,
) -> list[PagePlan]:
    """Every selected record page, one template per set.

    ``sources`` maps a slug to its crawled page. The exemplar is looked up there
    rather than among ``scaffolds``, so a set still gets its judged layout (and a
    page to compare shared tails against) when the user deselected the exemplar
    itself. Pages with no source are skipped.
    """
    members_by_set: dict[str, list[PageScaffold]] = {}
    for scaffold in scaffolds:
        if scaffold.record_set and scaffold.slug in sources:
            members_by_set.setdefault(scaffold.record_set, []).append(scaffold)
    if not members_by_set:
        return []

    # slug → page with the set's template chrome removed, one dict per set;
    # the exemplar's key comes first.
    set_pages: list[dict[str, SourceContent]] = []
    for set_slug, members in members_by_set.items():
        exemplar_slug = set_slug if set_slug in sources else members[0].slug
        slugs = list(dict.fromkeys([exemplar_slug, *(member.slug for member in members)]))
        set_pages.append(
            dict(zip(slugs, without_shared_tails([sources[slug] for slug in slugs])))
        )

    templates = await asyncio.gather(
        *(plan_record_template(next(iter(pages.values())), client=client) for pages in set_pages)
    )
    built: list[PagePlan] = []
    for template, members, pages in zip(templates, members_by_set.values(), set_pages):
        for scaffold in members:
            try:
                built.append(fill_record_page(template, scaffold, pages[scaffold.slug]))
            except ValidationError as exc:
                # One malformed crawled page must not cost the other 169.
                logger.warning("Record page /%s skipped: %s", scaffold.slug, exc)
    logger.info("Built %d record page(s) from %d template(s)", len(built), len(templates))
    return built
