"""
Ask the LLM where a document's pages begin and end, and which lines are copy.

A paste has no ground truth for structure, which is why ``paste_source``'s
line-shape heuristic reads a content brief backwards, promoting layout labels
("Hero", "Services grid (four cards)") to headings while the author's own
numbered page list ("1. HOME" … "7. CONTACT") is swallowed by the list-item
rule. No refinement of line shape fixes that, because the two are shaped
identically; only meaning separates them.

An uploaded brief has the same problem with better typography. Fowable's PDF
is one landing page in fourteen sections, each followed by internal notes
("Never publish"), then eleven pages of internal appendices; its font sizes
made the cover headline the only page title, so the whole document — notes,
verification log and all — became one page and the homepage got the cover's
115 characters. Font size says what is prominent, not what is for the visitor.
So documents are read here too (``structure_document``), with their heading
levels shown to the model as evidence.

**The model returns line numbers, never text.** It says which lines open a page,
which lines belong to it, and which lines are not website copy; this module then
slices the source's own lines accordingly. So a structure call cannot invent a
sentence, its reply stays small however large the source is, and the copy that
reaches the planner is verbatim what the author wrote. Same division of labour
as the rest of the pipeline: the model supplies small semantic judgements,
deterministic code does the mapping.

**A source larger than one call is read in two steps**, never truncated — a
dropped tail silently loses its pages. A MAP call sees the whole document as an
outline (headings and short lines at their real numbers) and decides the pages
and the parts that are not copy at all; then windows of
``paste_structure_max_chars`` over the pages' own lines mark the notes. Pages
are one decision with a global view: decided part by part, Fowable's one page
came back as fourteen. A source that fits is the original single call, byte for
byte.

The output is an ordinary ``ParsedDocument`` plus the page topic per title, so
``doc_structure.split_into_pages`` still owns bucketing, image placement, slugs
and the page cap. There is no second splitter.

Failure is always a fallback, never an error: no LLM configured, an
``LlmError``, or an outline that doesn't line up with the text all return
``None``, and the caller reads the source the deterministic way.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import get_args

from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.models.industry import PageType
from app.services.llm import LlmClient, LlmError, chat_json_cached, get_llm
from app.services.paste_source import strip_markers
from app.services.prompts import (
    PASTE_STRUCTURE_PROMPT,
    STRUCTURE_MAP_RULES,
    STRUCTURE_WINDOW_RULES,
)
from app.services.source_outline import OutlineBlock, ParsedDocument

logger = logging.getLogger(__name__)

_PAGE_TYPES = frozenset(get_args(PageType))
# What an unrecognised page_type becomes. Mirrors page_inference._infer_page_type,
# whose fallback for an unknown TOP-LEVEL page is also "services".
_PAGE_TYPE_FALLBACK: PageType = "services"

# A page's title line is a title, not a paragraph. Past this the model has
# pointed at a body line and we let the range speak for itself instead.
_TITLE_MAX_CHARS = 120

# Deepest heading level a block can carry (markdown's six).
_MAX_HEADING_LEVEL = 6


class PasteOutlinePage(BaseModel):
    """One page the model found, addressed by line number."""

    title: str = ""
    page_type: PageType = _PAGE_TYPE_FALLBACK
    title_line: int | None = None
    first_line: int
    last_line: int

    @field_validator("page_type", mode="before")
    @classmethod
    def heal_page_type(cls, value: object) -> str:
        """An invented type degrades to "services" instead of failing the plan.

        Same policy as the content blocks' literal healing: a model that answers
        "portfolio" has still told us something useful about the page.
        """
        if isinstance(value, str) and value.strip().lower() in _PAGE_TYPES:
            return value.strip().lower()
        return _PAGE_TYPE_FALLBACK


class PasteOutline(BaseModel):
    site_title: str | None = None
    # Asked outright: does the document describe ONE web page laid out in
    # sections? A rule buried in the prompt was not enough — the model read
    # Fowable's "14 sections, one per page" (per PDF page) as fourteen pages.
    single_page: bool | None = None
    pages: list[PasteOutlinePage] = Field(default_factory=list)
    # Lines a visitor must never see: layout labels, notes to the writer,
    # placeholder rows. Dropped from the copy, never from the line numbering.
    skip_lines: list[int] = Field(default_factory=list)
    # The same, for a run: [first, last]. An internal appendix is hundreds of
    # lines, and listing each one would be most of the reply.
    skip_ranges: list[list[int]] = Field(default_factory=list)


class StructuredDocument(BaseModel):
    """A parsed source plus the page topic for each of its title lines.

    ``page_topics`` is what ``split_into_pages(page_topics=…)`` consumes: with
    it, a heading opens a page because the model said it names one, rather than
    because ``classify_page_title`` recognised the words. That is the whole
    reason "AI AGENTS" and "DIGITAL TRUST & BLOCKCHAIN" can become pages.
    """

    model_config = {"arbitrary_types_allowed": True}

    document: ParsedDocument
    page_topics: dict[str, PageType]


async def structure_paste(
    text: str, *, title: str | None = None, llm: LlmClient | None = None
) -> StructuredDocument | None:
    """Read a paste's structure with the LLM, or ``None`` to fall back.

    ``llm`` is injectable so tests never reach a server.
    """
    if not settings.paste_llm_structure_enabled:
        return None
    body = (text or "").strip()
    if not body:
        return None
    blocks = [OutlineBlock(level=0, text=line) for line in body.splitlines()]
    shell = ParsedDocument(source_kind="paste", source_ref="", title=None, raw_text="")
    structured = await _structure(blocks, shell, given_title=title, llm=llm)
    if structured is not None and not structured.document.source_ref:
        structured.document.source_ref = structured.document.title or "Pasted content"
    return structured


async def structure_document(
    parsed: ParsedDocument, *, llm: LlmClient | None = None
) -> StructuredDocument | None:
    """Read an uploaded document's structure, or ``None`` to keep its own.

    The document keeps everything a reader measured — its source, images
    (re-anchored to the new outline; one inside an excluded part goes with it)
    and painted colours. Its title is the site's name when the model states
    one: a PDF's metadata title is the DOCUMENT's ("Fowable · Kindergarten &
    Childcare Website Content"), and it became the brand in the footer.
    """
    if not settings.document_llm_structure_enabled or not parsed.outline:
        return None
    return await _structure(parsed.outline, parsed, given_title=None, llm=llm)


async def _structure(
    blocks: list[OutlineBlock],
    shell: ParsedDocument,
    *,
    given_title: str | None,
    llm: LlmClient | None,
) -> StructuredDocument | None:
    rows = _numbered_rows(blocks)
    budget = settings.paste_structure_max_chars
    client = llm or get_llm()
    try:
        if _size(rows) <= budget:
            outline = await _ask(client, PASTE_STRUCTURE_PROMPT, "\n".join(rows))
            pages = _one_page_if_said(outline, _usable_pages(outline, len(blocks)))
        else:
            mapped = await _ask_mapped(client, blocks, rows, budget)
            if mapped is None:
                return None
            outline, pages = mapped
    except LlmError as exc:
        logger.warning("Structure read failed (%s) — falling back", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - a read of a source must never 500
        logger.warning("Structure read errored (%s) — falling back", exc)
        return None
    return _assemble(blocks, outline, pages, shell, given_title=given_title)


async def _ask(client: LlmClient, system_prompt: str, user_prompt: str) -> PasteOutline:
    return await chat_json_cached(
        client,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=PasteOutline,
        temperature=settings.plan_temperature,
    )


# Line-length limits tried, longest first, for the outline the map call sees;
# 0 is headings only.
_MAP_LINE_LIMITS = (80, 60, 40, 24, 0)
_MAP_ROW_MAX_CHARS = 100


async def _ask_mapped(
    client: LlmClient, blocks: list[OutlineBlock], rows: list[str], budget: int
) -> tuple[PasteOutline, list[PasteOutlinePage]] | None:
    """Read a source too long for one call: decide its pages ONCE, from an
    outline of the whole thing, then mark its non-copy lines part by part.

    The pages must be one decision. Read part by part, each part met
    "WEBSITE SECTION 05 / 14" with no sight of the cover's "landing page …
    14 sections, one per page", and Fowable's one page came back as fourteen.
    The outline — headings and short lines at their real numbers — fits one
    call; which lines are notes is a local question, so it windows."""
    shown = _map_rows(blocks, budget)
    if shown is None:
        logger.info("Source outline too long for one structure call — falling back")
        return None
    header = (
        f"[Outline of a {len(rows)}-line document: headings and short lines only, "
        "at their real line numbers. The longer lines between them are body text.]\n"
    )
    plan = await _ask(
        client, PASTE_STRUCTURE_PROMPT + STRUCTURE_MAP_RULES, header + "\n".join(shown.values())
    )
    starts: list[PasteOutlinePage] = []
    for page in sorted(plan.pages, key=lambda p: p.title_line or p.first_line):
        begins = page.title_line if page.title_line is not None else page.first_line
        if begins in shown and all(begins > s.first_line for s in starts):
            starts.append(page.model_copy(update={"first_line": begins}))
    pages = _one_page_if_said(plan, _spans(starts, len(rows)))
    if not pages:
        return None

    # The map says where pages begin and where the copy ends — nothing finer.
    # It sees headings and short lines, not the copy between them, so a line
    # or a run it marks inside a page is a guess: Fowable's map marked 486
    # lines, then ~150 runs, and took sections' copy with them. What lies
    # inside the pages is the windows' to judge; outside them is left out.
    copy_rows = [
        number for page in pages for number in range(page.first_line, page.last_line + 1)
    ]
    skip_lines: list[int] = []
    skip_ranges: list[list[int]] = []
    windows = _windows([rows[n - 1] for n in copy_rows], budget)
    page_list = "; ".join(f'"{p.title}" ({p.page_type}) from line {p.first_line}' for p in pages)
    for index, (first, last) in enumerate(windows):
        numbers = copy_rows[first - 1 : last]
        header = (
            f"[Part {index + 1} of {len(windows)} of a {len(rows)}-line document. "
            f"The pages are decided: {page_list}.]\n"
        )
        reply = await _ask(
            client,
            PASTE_STRUCTURE_PROMPT + STRUCTURE_WINDOW_RULES,
            header + "\n".join(rows[n - 1] for n in numbers),
        )
        part = set(numbers)
        skip_lines += [n for n in reply.skip_lines if n in part]
        skip_ranges += [
            [max(pair[0], numbers[0]), min(pair[1], numbers[-1])]
            for pair in reply.skip_ranges
            if len(pair) == 2 and pair[0] <= pair[1]
            and pair[1] >= numbers[0] and pair[0] <= numbers[-1]
        ]
    outline = PasteOutline(
        site_title=plan.site_title, pages=pages, skip_lines=skip_lines, skip_ranges=skip_ranges
    )
    return outline, pages


def _spans(starts: list[PasteOutlinePage], line_count: int) -> list[PasteOutlinePage]:
    """Each page runs to the next one's start, so a careless ``last_line`` can
    never drop copy between pages; the LAST page ends where the model says the
    copy ends, which is what leaves a trailing appendix out."""
    pages = []
    for index, page in enumerate(starts):
        if index + 1 < len(starts):
            end = starts[index + 1].first_line - 1
        else:
            end = page.last_line if page.first_line < page.last_line <= line_count else line_count
        pages.append(page.model_copy(update={"last_line": end}))
    return pages


def _one_page_if_said(
    outline: PasteOutline, pages: list[PasteOutlinePage]
) -> list[PasteOutlinePage]:
    """The model's sections folded into one page when it said the document is
    one page: the first page's title, running to the last page's end."""
    if outline.single_page and len(pages) > 1:
        return [pages[0].model_copy(update={"last_line": pages[-1].last_line})]
    return pages


def _map_rows(blocks: list[OutlineBlock], budget: int) -> dict[int, str] | None:
    """The outline the map call sees — numbered row by real block number — at
    the longest line limit that fits ``budget``; None when even the headings
    alone do not."""
    for limit in _MAP_LINE_LIMITS:
        shown = {
            number: _row(number, block, max_chars=_MAP_ROW_MAX_CHARS)
            for number, block in enumerate(blocks, start=1)
            if block.level > 0 or (limit and len(block.text) <= limit)
        }
        if shown and _size(shown.values()) <= budget:
            return shown
    return None


def _size(rows) -> int:
    return sum(len(row) + 1 for row in rows)


def _numbered_rows(blocks: list[OutlineBlock]) -> list[str]:
    """The source as ``1: text`` rows — the only thing the model is shown.

    A heading carries its level as markdown hashes ("12: ## Our programmes"),
    the evidence a document's typography offers; a paste's lines are level 0,
    so its rows are exactly the lines it was given."""
    return [_row(number, block) for number, block in enumerate(blocks, start=1)]


def _row(number: int, block: OutlineBlock, *, max_chars: int | None = None) -> str:
    marker = "#" * min(block.level, _MAX_HEADING_LEVEL) + " " if block.level > 0 else ""
    text = block.text if max_chars is None else block.text[:max_chars]
    return f"{number}: {marker}{text}"


def _windows(rows: list[str], max_chars: int) -> list[tuple[int, int]]:
    """1-based inclusive row ranges of at most ``max_chars`` each (a single row
    longer than that is a window of its own)."""
    windows: list[tuple[int, int]] = []
    start, size = 1, 0
    for number, row in enumerate(rows, start=1):
        cost = len(row) + 1
        if size and size + cost > max_chars:
            windows.append((start, number - 1))
            start, size = number, 0
        size += cost
    if rows:
        windows.append((start, len(rows)))
    return windows


def _assemble(
    blocks: list[OutlineBlock],
    outline: PasteOutline,
    pages: list[PasteOutlinePage],
    shell: ParsedDocument,
    *,
    given_title: str | None,
) -> StructuredDocument | None:
    """Turn the model's line numbers back into blocks of the source's own text."""
    if not pages:
        return None

    skip = _titles_of_kept_copy(blocks, _skipped(outline, len(blocks)))
    assembled: list[OutlineBlock] = []
    page_topics: dict[str, PageType] = {}
    headings: list[str] = []
    # After source block n, how many assembled blocks exist (None: n was not
    # inside any page) — what re-anchors the source's images.
    after: dict[int, int | None] = {}

    for position, page in enumerate(pages):
        title_line = page.title_line
        title_text = ""
        if title_line is not None and page.first_line <= title_line <= page.last_line:
            title_text = _clean(blocks[title_line - 1].text)
            if len(title_text) > _TITLE_MAX_CHARS:
                title_text, title_line = "", None
        # The title TEXT is the source's line, never the model's restatement — a
        # model that tidies "1. HOME" to "Home" would otherwise hand us a key
        # that matches nothing in the blocks below.
        if not title_text:
            title_text = _clean(page.title)
            title_line = None
        if not title_text:
            continue

        # The homepage's own title is the site's name, not a page heading: the
        # splitter keeps the leading bucket as primary either way, and emitting
        # it would put "HOME" at the top of the page copy.
        if position > 0:
            assembled.append(OutlineBlock(level=1, text=title_text))
            page_topics[title_text] = page.page_type
            headings.append(title_text)

        for number in range(page.first_line, page.last_line + 1):
            source = blocks[number - 1]
            body = _clean(source.text)
            if number != title_line and number not in skip and body:
                # Only the model's pages open pages: an in-page heading keeps
                # its rank one step below them, or the splitter would read a
                # document's own H1 "About us" inside the homepage as a page.
                # Never capped: two ranks the source tells apart stay apart.
                # Capped at six, a DOCX's headlines (5) met its card titles (6)
                # and every section's headline came back as its first card.
                level = source.level + 1 if source.level else 0
                assembled.append(OutlineBlock(level=level, text=body))
            after[number] = len(assembled)

    if not assembled:
        return None

    resolved_title = (
        (given_title or "").strip()
        or _clean(outline.site_title or "")
        or shell.title
        or None
    )
    document = dataclasses.replace(
        shell,
        title=resolved_title,
        raw_text="\n".join(block.text for block in assembled),
        headings=headings,
        outline=assembled,
        images=_reanchored(shell.images, after),
    )
    return StructuredDocument(document=document, page_topics=page_topics)


def _skipped(outline: PasteOutline, line_count: int) -> set[int]:
    skip = {n for n in outline.skip_lines if 1 <= n <= line_count}
    for pair in outline.skip_ranges:
        if len(pair) == 2 and 1 <= pair[0] <= pair[1] <= line_count:
            skip.update(range(pair[0], pair[1] + 1))
    return skip


def _titles_of_kept_copy(blocks: list[OutlineBlock], skip: set[int]) -> set[int]:
    """``skip`` without the headings whose own copy was kept.

    A heading owns the body lines directly under it. When the model keeps that
    body and skips the heading, it has kept a card and thrown away what the card
    is about: Fowable's reads dropped 25 card titles ("One list of interested
    families", "An AI agent you control") together with the "ALL PLANS" tag
    under each, and every card reached the site as an untitled sentence. Which
    lines are notes is the model's judgement; that a kept paragraph's own
    heading is copy too is not a judgement at all. A paste's lines are level 0,
    so this never touches one.
    """
    restored: set[int] = set()
    for number in sorted(skip):
        if blocks[number - 1].level == 0:
            continue
        below = number + 1
        while below <= len(blocks) and blocks[below - 1].level == 0:
            if below not in skip and blocks[below - 1].text.strip():
                restored.add(number)
                break
            below += 1
    return skip - restored


def _reanchored(images, after: dict[int, int | None]):
    """Each image moved to its place in the assembled outline. An image's
    anchor is the number of source blocks before it; one that followed a block
    outside every page (an appendix, the notes the model left out) goes too."""
    kept = []
    for image in images:
        if image.anchor == 0:
            kept.append(image)
        elif after.get(image.anchor) is not None:
            kept.append(dataclasses.replace(image, anchor=after[image.anchor]))
    return kept


def _usable_pages(outline: PasteOutline, line_count: int) -> list[PasteOutlinePage]:
    """Pages whose ranges are real, in document order and non-overlapping.

    An outline that doesn't line up with the text is discarded rather than
    patched: a wrong range moves someone's copy onto the wrong page, which is
    both invisible in review and worse than the heuristic read it replaced.
    """
    pages = [
        page
        for page in outline.pages
        if 1 <= page.first_line <= page.last_line <= line_count
    ]
    if not pages:
        return []
    pages.sort(key=lambda page: page.first_line)
    kept = [pages[0]]
    for page in pages[1:]:
        if page.first_line > kept[-1].last_line:
            kept.append(page)
    return kept


def _clean(value: str) -> str:
    """Whitespace-collapsed, and stripped of the markers the reader strips.

    Shared with the heuristic path (`paste_source.strip_markers`), so "3.
    CONTACT" becomes "CONTACT" here exactly as it would there — a page title
    carrying its own numbering would otherwise slug to `/3-contact`.
    """
    return strip_markers(" ".join((value or "").split()))
