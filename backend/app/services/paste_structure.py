"""
Ask the LLM where a pasted document's pages begin and end.

Every other reader has ground truth for structure: a crawl has URLs, a PDF has
font sizes, a DOCX has heading styles, a Facebook Page has fields. A paste has
nothing — which is why ``paste_source``'s line-shape heuristic reads a content
brief backwards, promoting layout labels ("Hero", "Services grid (four cards)")
to headings while the author's own numbered page list ("1. HOME" … "7. CONTACT")
is swallowed by the list-item rule. No refinement of line shape fixes that,
because the two are shaped identically; only meaning separates them.

**The model returns line numbers, never text.** It says which lines open a page,
which lines belong to it, and which lines are instructions to a copywriter
rather than copy; this module then slices the pasted lines accordingly. So a
structure call cannot invent a sentence, its reply stays small however large the
paste is, and the copy that reaches the planner is verbatim what the user typed.
Same division of labour as the rest of the pipeline: the model supplies small
semantic judgements, deterministic code does the mapping.

The output is an ordinary ``ParsedDocument`` plus the page topic per title, so
``doc_structure.split_into_pages`` still owns bucketing, image placement, slugs
and the page cap. There is no second splitter.

Failure is always a fallback, never an error: no LLM configured, an
``LlmError``, a paste past ``paste_structure_max_chars``, or an outline that
doesn't line up with the text all return ``None``, and the caller reads the
paste the heuristic way.
"""

from __future__ import annotations

import logging
from typing import get_args

from pydantic import BaseModel, Field, field_validator

from app.config import settings
from app.models.industry import PageType
from app.services.llm import LlmClient, LlmError, chat_json_cached, get_llm
from app.services.paste_source import strip_markers
from app.services.prompts import PASTE_STRUCTURE_PROMPT
from app.services.source_outline import OutlineBlock, ParsedDocument

logger = logging.getLogger(__name__)

_PAGE_TYPES = frozenset(get_args(PageType))
# What an unrecognised page_type becomes. Mirrors page_inference._infer_page_type,
# whose fallback for an unknown TOP-LEVEL page is also "services".
_PAGE_TYPE_FALLBACK: PageType = "services"

# A page's title line is a title, not a paragraph. Past this the model has
# pointed at a body line and we let the range speak for itself instead.
_TITLE_MAX_CHARS = 120


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
    pages: list[PasteOutlinePage] = Field(default_factory=list)
    # Lines a visitor must never see: layout labels, notes to the writer,
    # placeholder rows. Dropped from the copy, never from the line numbering.
    skip_lines: list[int] = Field(default_factory=list)


class StructuredPaste(BaseModel):
    """A parsed paste plus the page topic for each of its title lines.

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
) -> StructuredPaste | None:
    """Read a paste's structure with the LLM, or ``None`` to fall back.

    ``llm`` is injectable so tests never reach a server.
    """
    if not settings.paste_llm_structure_enabled:
        return None
    body = (text or "").strip()
    if not body:
        return None
    if len(body) > settings.paste_structure_max_chars:
        # Truncating would silently lose every page in the tail, which is worse
        # than a consistent heuristic read of the whole thing.
        logger.info(
            "Paste is %d chars (cap %d) — structuring heuristically",
            len(body),
            settings.paste_structure_max_chars,
        )
        return None

    lines = body.splitlines()
    try:
        outline = await chat_json_cached(
            llm or get_llm(),
            system_prompt=PASTE_STRUCTURE_PROMPT,
            user_prompt=_numbered(lines),
            schema=PasteOutline,
            temperature=settings.plan_temperature,
        )
    except LlmError as exc:
        logger.warning("Paste structuring failed (%s) — falling back", exc)
        return None
    except Exception as exc:  # noqa: BLE001 - a read of a paste must never 500
        logger.warning("Paste structuring errored (%s) — falling back", exc)
        return None

    return _assemble(lines, outline, given_title=title)


def _numbered(lines: list[str]) -> str:
    """The paste as ``1: text`` rows — the only thing the model is shown."""
    return "\n".join(f"{index}: {line}" for index, line in enumerate(lines, start=1))


def _assemble(
    lines: list[str], outline: PasteOutline, *, given_title: str | None
) -> StructuredPaste | None:
    """Turn the model's line numbers back into blocks of the user's own text."""
    pages = _usable_pages(outline, len(lines))
    if not pages:
        return None

    skip = {n for n in outline.skip_lines if 1 <= n <= len(lines)}
    blocks: list[OutlineBlock] = []
    page_topics: dict[str, PageType] = {}
    headings: list[str] = []

    for position, page in enumerate(pages):
        title_line = page.title_line
        title_text = ""
        if title_line is not None and page.first_line <= title_line <= page.last_line:
            title_text = _clean(lines[title_line - 1])
            if len(title_text) > _TITLE_MAX_CHARS:
                title_text, title_line = "", None
        # The title TEXT is the user's line, never the model's restatement — a
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
            blocks.append(OutlineBlock(level=1, text=title_text))
            page_topics[title_text] = page.page_type
            headings.append(title_text)

        for number in range(page.first_line, page.last_line + 1):
            if number == title_line or number in skip:
                continue
            body = _clean(lines[number - 1])
            if body:
                blocks.append(OutlineBlock(level=0, text=body))

    if not blocks:
        return None

    resolved_title = (given_title or "").strip() or _clean(outline.site_title or "") or None
    document = ParsedDocument(
        source_kind="paste",
        source_ref=resolved_title or "Pasted content",
        title=resolved_title,
        raw_text="\n".join(block.text for block in blocks),
        headings=headings,
        outline=blocks,
    )
    return StructuredPaste(document=document, page_topics=page_topics)


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
