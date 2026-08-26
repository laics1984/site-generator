"""
Pasted copy or pasted markup → the same parsed shape a document upload produces.

A paste is a fourth source, and deliberately the *thinnest* one: it has no
origin to crawl, no file to parse and no API to query, so all it has to do is
recover the structure the user pasted. Both flavours land on
``ParsedDocument`` — the ordered, level-tagged outline in
``services/source_outline.py`` — and are then handed to
``doc_structure.split_into_pages``, which is what makes a pasted "## Contact"
open a Contact page exactly like a Word heading does. There is no second
splitter and no paste-shaped ``SourceContent`` built anywhere by hand.

Two readers, one output:

* **Markup** (``looks_like_html``) goes through ``source_outline.read_html`` —
  the same DOM walk the crawler takes its recall spine from, so headings,
  list items, table cells and images are found the same way on both paths.
* **Plain text** is read here. Explicit markers win when the paste has any
  (``#`` headings, ``===``/``---`` underlines): someone who wrote structure
  gets exactly the structure they wrote. Only when a paste carries none does
  the bare-line heuristic run, because a short unpunctuated line surrounded by
  blanks is the only thing left to read a heading from.

Merging a paste into whatever a URL or a document read is
``services/source_merge.merge_sources`` — generic across any two sources, not
just a paste, so it lives there rather than here. It joins by page *topic*,
the same notion ``split_into_pages`` uses, so pasted Contact copy lands on the
site's own Contact page instead of creating a second one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.services.doc_structure import DocImageRef
from app.services.image_urls import upgrade_source_image_url
from app.services.source_outline import OutlineBlock, ParsedDocument, read_html

# Shown as the source label when the user gives the paste no title.
PASTE_LABEL = "Pasted content"

# --- markup detection -----------------------------------------------------------

# Any structural tag is enough. Deliberately not a parse: pasted markup is
# routinely a fragment with no <html> around it. The tag name must sit flush
# against the "<", as HTML requires — with whitespace allowed, "Pricing: a < b
# and c > d" reads as a <b> tag and the whole paste is parsed as markup, which
# throws away its line structure. Mirrored (cosmetically, for the inline badge)
# by frontend/src/lib/sourcePaste.ts.
_HTML_TAG_RE = re.compile(
    r"<(?:!doctype\s+html|/?(?:html|head|body|main|article|section|header|footer|"
    r"nav|aside|div|span|p|h[1-6]|ul|ol|li|dl|dt|dd|table|tr|td|th|thead|tbody|"
    r"figure|figcaption|blockquote|img|a|br|hr|strong|em|b|i|picture|source)\b)"
    r"[^>]*>",
    re.IGNORECASE,
)


def looks_like_html(text: str) -> bool:
    """True when the paste is markup rather than prose."""
    return bool(_HTML_TAG_RE.search(text or ""))


# --- plain-text structure -------------------------------------------------------

_ATX_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*#*$")
_SETEXT_H1_RE = re.compile(r"^=+$")
_SETEXT_H2_RE = re.compile(r"^-{2,}$")
# A dash is only ever a list marker; a NUMBER is also how people number their
# sections. Treating the two alike meant "1. HOME" — an author's own page list —
# was read as a list item and demoted to body text, while the layout labels
# around it became the document's only headings. So they are separated: a
# symbol bullet disqualifies a heading outright, a number prefix is stripped and
# the heading test applied to what is left ("1. You talk to the person building
# it." still ends in a full stop and stays a list item).
_SYMBOL_BULLET_RE = re.compile(r"^\s*[-*+•·]\s+")
_NUMBER_PREFIX_RE = re.compile(r"^\s*\d{1,3}[.)]\s+")
# The union, for stripping the marker off the text either way.
_BULLET_RE = re.compile(r"^\s*(?:[-*+•·]|\d+[.)])\s+")

# A brief labels each line with the slot it fills ("Heading: Most projects don't
# fail at launch"). The label is a marker on the line, exactly like a bullet or
# a `#`, and it prints on the live page if it survives — so it is stripped the
# same way. Deliberately a SHORT closed set of layout slots, never a vocabulary
# denylist: "Title:" is absent because a staff card legitimately reads
# "Title: Operations Manager", and "Contact:"/"Note:" are real copy.
_SLOT_LABEL_RE = re.compile(
    r"^(?:primary\s+|secondary\s+)?"
    r"(?:heading|headline|sub-?head(?:ing)?|cta|call\s+to\s+action)"
    r"\s*:\s*(?=\S)",
    re.IGNORECASE,
)

# Shallow markdown de-syntaxing — NOT a parser. Pasting from a chat assistant is
# the common case, and `**Our Team**` reaching the planner as literal asterisks
# ends up as literal asterisks on the page.
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(\s*(\S+?)(?:\s+\"[^\"]*\")?\s*\)")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(\s*\S+?(?:\s+\"[^\"]*\")?\s*\)")
_MD_EMPHASIS_RE = re.compile(r"(\*{1,3}|_{1,3}|`+)(?=\S)(.+?)(?<=\S)\1")
_MD_QUOTE_RE = re.compile(r"^\s*>\s?")

# A bare line reads as a heading only if it is short, unpunctuated and alone.
_HEADING_MAX_CHARS = 80
_HEADING_MAX_WORDS = 12
_SENTENCE_TAIL = ".,;!?"


@dataclass
class ParsedPaste:
    """What a paste yielded, ready for ``split_into_pages``."""

    document: ParsedDocument
    images: list[DocImageRef] = field(default_factory=list)
    description: str | None = None
    is_html: bool = False
    # Images the markup referenced with a site-relative path. There is no origin
    # to resolve those against, so they are counted and reported rather than
    # emitted as URLs that would 404 on every page they landed on.
    unresolved_images: int = 0


def read_paste(text: str, *, title: str | None = None) -> ParsedPaste:
    """Parse pasted copy or markup into the shared parsed-source shape."""
    body = (text or "").strip()
    given_title = (title or "").strip() or None

    if looks_like_html(body):
        return _read_markup(body, given_title)
    return _read_text(body, given_title)


def _read_markup(html: str, given_title: str | None) -> ParsedPaste:
    outline = read_html(html)
    blocks = outline.blocks
    resolved_title = given_title or outline.title or _first_heading(blocks)
    return ParsedPaste(
        document=_document(resolved_title, blocks),
        images=[
            DocImageRef(
                url=image.url,
                width=image.width,
                height=image.height,
                anchor=image.anchor,
                alt=image.alt,
            )
            for image in outline.images
        ],
        description=outline.description,
        is_html=True,
        unresolved_images=outline.unresolved_images,
    )


def _read_text(text: str, given_title: str | None) -> ParsedPaste:
    lines = text.splitlines()
    marked = _has_explicit_markers(lines)
    tiered = not marked and _has_heading_tiers(lines)

    blocks: list[OutlineBlock] = []
    images: list[DocImageRef] = []
    skip_next = False

    for index, raw in enumerate(lines):
        if skip_next:
            skip_next = False
            continue
        line = raw.strip()
        if not line:
            continue

        level, content = _text_heading(lines, index, marked=marked, tiered=tiered)
        if level and _is_setext_underline(lines, index + 1):
            skip_next = True

        cleaned, found = _demarkdown(content)
        for url, alt in found:
            images.append(DocImageRef(url=url, alt=alt, anchor=len(blocks)))
        if not cleaned:
            continue
        blocks.append(OutlineBlock(level=level, text=cleaned))

    resolved_title = given_title or _first_heading(blocks)
    return ParsedPaste(
        document=_document(resolved_title, blocks),
        images=images,
        is_html=False,
    )


def _document(title: str | None, blocks: list[OutlineBlock]) -> ParsedDocument:
    return ParsedDocument(
        source_kind="paste",
        source_ref=title or PASTE_LABEL,
        title=title,
        raw_text="\n".join(block.text for block in blocks),
        headings=_dedupe(block.text for block in blocks if block.level > 0),
        outline=blocks,
    )


def _dedupe(values) -> list[str]:
    """Case-insensitive dedupe for prose (headings), first spelling wins.

    Small and dependency-free enough to keep alongside its one caller here
    rather than reach into `services/source_merge`, which has its own copy for
    the same reason (union-merging headings from two sources).
    """
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _has_explicit_markers(lines: list[str]) -> bool:
    """True when the paste marks its own headings (``#`` or a setext rule)."""
    for index, raw in enumerate(lines):
        if _ATX_RE.match(raw.strip()):
            return True
        if _is_setext_underline(lines, index):
            return True
    return False


def _is_setext_underline(lines: list[str], index: int) -> bool:
    """``===``/``---`` directly under a non-blank line — a heading, not a rule."""
    if index <= 0 or index >= len(lines):
        return False
    candidate = lines[index].strip()
    if not (_SETEXT_H1_RE.match(candidate) or _SETEXT_H2_RE.match(candidate)):
        return False
    return bool(lines[index - 1].strip())


def _text_heading(
    lines: list[str], index: int, *, marked: bool, tiered: bool
) -> tuple[int, str]:
    """The line's heading level (0 = body) and its text with markers removed."""
    line = lines[index].strip()

    atx = _ATX_RE.match(line)
    if atx:
        return min(len(atx.group(1)), 3), atx.group(2).strip()

    if _is_setext_underline(lines, index + 1):
        underline = lines[index + 1].strip()
        return (1 if _SETEXT_H1_RE.match(underline) else 2), line

    # A paste that marks its own headings has already said everything it means
    # to; guessing at the rest would fracture pages the author didn't ask for.
    if marked or not _looks_like_bare_heading(lines, index):
        return 0, line
    level = 1 if tiered and _is_emphatic(line) else 2
    return level, line.rstrip(":")


def _looks_like_bare_heading(lines: list[str], index: int) -> bool:
    line = _NUMBER_PREFIX_RE.sub("", lines[index].strip())
    if not (2 <= len(line) <= _HEADING_MAX_CHARS):
        return False
    if line[-1] in _SENTENCE_TAIL or _SYMBOL_BULLET_RE.match(line):
        return False
    if len(line.split()) > _HEADING_MAX_WORDS:
        return False
    # Alone: blank (or nothing) above, and something below to be a heading OF.
    if index > 0 and lines[index - 1].strip():
        return False
    return any(later.strip() for later in lines[index + 1 :])


def _is_emphatic(line: str) -> bool:
    """True when the line SHOUTS its heading — numbered, or in capitals.

    The author's own way of marking a title as more important than the ones
    around it, and the only tier signal available in unmarked prose.
    """
    if _NUMBER_PREFIX_RE.match(line):
        return True
    letters = [char for char in line if char.isalpha()]
    return len(letters) >= 2 and all(char.isupper() for char in letters)


def _has_heading_tiers(lines: list[str]) -> bool:
    """True when the paste's bare headings come in two ranks.

    A document that titles its sections "1. HOME" / "2. CONTACT" and *also*
    labels their parts ("Hero", "Subhead") has stated a hierarchy, and the
    ranks must not compete: at one flat level the labels open pages alongside
    the titles. Mirrors ``doc_parser._pdf_size_to_level``, which ranks a PDF's
    distinct heading font sizes for the same reason.

    Both ranks must be present. A paste whose headings are all emphatic (or all
    plain) has said nothing about hierarchy, so it keeps the single level it
    has always had.
    """
    emphatic = plain = False
    for index in range(len(lines)):
        if not lines[index].strip() or not _looks_like_bare_heading(lines, index):
            continue
        if _is_emphatic(lines[index].strip()):
            emphatic = True
        else:
            plain = True
        if emphatic and plain:
            return True
    return False


def strip_markers(line: str) -> str:
    """One line's text with list markers and inline markdown removed.

    Public because the LLM structuring pass slices the SAME pasted lines and
    must clean them identically — otherwise a page title arrives as
    "3. CONTACT", slugs as `/3-contact`, and `**Our Team**` reaches the planner
    with its asterisks. Images are dropped here rather than returned: on that
    path they have already been harvested by `read_paste`.
    """
    text, _ = _demarkdown(line)
    return text


def _demarkdown(line: str) -> tuple[str, list[tuple[str, str]]]:
    """Strip inline markdown, returning the text and any images it referenced."""
    images: list[tuple[str, str]] = []

    def _take_image(match: re.Match[str]) -> str:
        url = match.group(2)
        if url.startswith(("http://", "https://")):
            images.append((upgrade_source_image_url(url), match.group(1).strip()))
        return ""

    text = _MD_IMAGE_RE.sub(_take_image, line)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_QUOTE_RE.sub("", text)
    text = _BULLET_RE.sub("", text)
    text = _SLOT_LABEL_RE.sub("", text)
    # Runs first-to-last so ***bold italic*** unwraps in one pass.
    text = _MD_EMPHASIS_RE.sub(r"\2", text)
    return text.strip(), images


def _first_heading(blocks: list[OutlineBlock]) -> str | None:
    """The paste's own name for itself: its top heading, shallowest first."""
    for level in (1, 2, 3):
        for block in blocks:
            if block.level == level:
                return block.text
    return None
