"""
PDF + DOCX → SourceContent.

PDF path (PyMuPDF / fitz):
- Iterate text blocks per page, capture span font sizes.
- Body median = the most common rounded font size across the document.
- Headings = text spans whose size > body median * 1.18 AND length 2-200 chars.
- Images extracted via page.get_images() and reassembled with their pixmap data.

DOCX path (python-docx + raw ZIP):
- python-docx walks paragraphs, reading paragraph.style.name. "Heading 1/2/3" →
  heading, anything else → body text.
- Inline images live in ``word/media/*`` inside the .docx ZIP. We surface every
  image-bytes blob plus the first one as the brand-logo candidate (common
  enough that the logo is on the cover page / first inline image).

Both paths emit a ParsedDocument carrying:
  - raw_text (concatenated body)
  - headings (deduped, length-capped)
  - images (raw bytes, ordered as found)
  - title (document title metadata, falls back to first heading)
"""

from __future__ import annotations

import base64
import hashlib
import io
import logging
import re
from collections import Counter

import docx as python_docx  # python-docx
import fitz  # PyMuPDF — `import fitz` is correct for the pymupdf package
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.config import settings
from app.services.price_lists import PRICE

# The parsed-source shape is shared with the paste reader — see source_outline.
from app.services.source_outline import DocImage, OutlineBlock, ParsedDocument
from app.services.theme import has_brand_hue

logger = logging.getLogger(__name__)

# Image extension → MIME, shared by the PDF and DOCX paths.
_IMG_EXT_TO_MIME = {
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
    "tif": "image/tiff",
}


# --- PDF ------------------------------------------------------------------------


_HEADING_MIN_SCALE = 1.18  # font-size > body_median * this counts as a heading
_HEADING_MIN_LEN = 2
_HEADING_MAX_LEN = 200


def _ocr_pages(doc: fitz.Document) -> str:
    """The text of a scanned PDF's first `ocr_document_max_pages` pages, read
    with the OCR engine the image screen already loads. Pages render at 2x
    (≈144 dpi): enough for body type, and the reader caps the long side anyway.
    No headings come back — a scan states no font sizes — so the document
    lands as one page of prose, which is honest about what was read."""
    from app.services.text_detection import read_document_page

    pages: list[str] = []
    for index, page in enumerate(doc):
        if index >= settings.ocr_document_max_pages:
            break
        text = read_document_page(page.get_pixmap(matrix=fitz.Matrix(2, 2)).tobytes("png"))
        if text is None:
            return ""  # OCR unavailable: nothing to gain from the other pages
        if text.strip():
            pages.append(text.strip())
    return "\n\n".join(pages)


def parse_pdf(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Extract text + headings + images from a PDF. Requires a text layer —
    image-only / scanned PDFs raise. Callers should check raw_text length and
    surface a helpful message."""
    try:
        doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not open PDF: {exc}") from exc

    # Pass 1: collect font sizes to determine the body median
    sizes: list[int] = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:  # 0 = text block
                continue
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    size = span.get("size")
                    if isinstance(size, (int, float)) and span.get("text", "").strip():
                        sizes.append(round(size))

    if not sizes:
        # No text layer: a scan. Read its pages with OCR rather than refuse the
        # upload; empty when OCR is off or unavailable, and the caller then
        # says so (parse_document's minimum-text check).
        title = _pdf_title(doc)
        raw_text = _ocr_pages(doc)
        doc.close()
        return ParsedDocument(
            source_kind="pdf",
            source_ref=filename,
            title=title,
            raw_text=raw_text,
            headings=[],
            images=[],
        )

    body_median = Counter(sizes).most_common(1)[0][0]
    heading_threshold = body_median * _HEADING_MIN_SCALE

    # PDFs carry no heading *levels* — only font sizes. Rank the distinct heading
    # sizes (largest → H1, next → H2, rest → H3) so the outline can reconstruct a
    # hierarchy the way DOCX styles give us for free.
    heading_sizes = sorted(
        {round(s) for s in sizes if s >= heading_threshold}, reverse=True
    )

    # Pass 2: pull text + headings + ordered outline + interleaved images, in
    # document order. TEXT_PRESERVE_IMAGES surfaces image blocks (type 1) in the
    # same block stream as text — carrying their bytes + pixel dimensions — so
    # each image's position relative to the headings is preserved for per-page
    # association downstream.
    text_chunks: list[str] = []
    headings: list[str] = []
    outline: list[OutlineBlock] = []
    images: list[DocImage] = []
    seen_headings: set[str] = set()
    seen_image_hashes: set[bytes] = set()
    dict_flags = fitz.TEXTFLAGS_DICT | fitz.TEXT_PRESERVE_IMAGES
    for page in doc:
        xrefs = _image_xrefs(page)
        for block in page.get_text("dict", flags=dict_flags)["blocks"]:
            btype = block.get("type")
            if btype == 1:  # image block
                _collect_pdf_image(
                    _with_alpha(doc, block, xrefs.get(block.get("number"))),
                    images,
                    seen_image_hashes,
                    anchor=len(outline),
                )
                continue
            if btype != 0:
                continue
            for line_text, line_max_size in _paragraphs(block):
                text_chunks.append(line_text)
                is_heading = (
                    line_max_size >= heading_threshold
                    and _HEADING_MIN_LEN <= len(line_text) <= _HEADING_MAX_LEN
                )
                if is_heading:
                    outline.append(
                        OutlineBlock(
                            level=_pdf_size_to_level(line_max_size, heading_sizes),
                            text=line_text,
                        )
                    )
                    if line_text.lower() not in seen_headings:
                        seen_headings.add(line_text.lower())
                        headings.append(line_text)
                else:
                    outline.append(OutlineBlock(level=0, text=line_text))

    raw_text = "\n".join(text_chunks)

    title = _pdf_title(doc) or (headings[0] if headings else None)
    reference_colors = _pdf_brand_colors(doc)
    doc.close()

    return ParsedDocument(
        source_kind="pdf",
        source_ref=filename,
        title=title,
        raw_text=raw_text,
        headings=headings[:50],
        images=images,
        outline=outline,
        reference_colors=reference_colors,
    )


# A designed brief's brand lives in what it paints, not always in its logo: a
# white wordmark on a green cover carries no hue of its own, so without this the
# palette call heard "no colour" and the site came out in a curated neutral.
_MAX_REFERENCE_COLORS = 8


def _pdf_brand_colors(doc) -> list[str]:
    """The document's chromatic opaque fills, largest painted area first.

    Neutrals (page, cards, shadows, rules) say nothing about the brand and are
    left out — `theme.has_brand_hue` is the one hue test. Any failure is no
    evidence, never an error."""
    area: Counter[str] = Counter()
    try:
        for page in doc:
            for drawing in page.get_drawings():
                fill, rect = drawing.get("fill"), drawing.get("rect")
                if not fill or rect is None or (drawing.get("fill_opacity") or 1) < 1:
                    continue
                hex_color = "#%02x%02x%02x" % tuple(round(c * 255) for c in fill[:3])
                if has_brand_hue(hex_color):
                    area[hex_color] += abs(rect.width * rect.height)
    except Exception as exc:  # noqa: BLE001
        logger.debug("PDF colour read failed: %s", exc)
        return []
    return [color for color, _ in area.most_common(_MAX_REFERENCE_COLORS)]


def _paragraphs(block: dict) -> list[tuple[str, float]]:
    """A text block's paragraphs, each with its font size.

    A PDF stores VISUAL lines: "Helping your kindergarten / grow, one parent
    connection / at a time." is three lines, and read one by one it was three
    H1s, while every wrapped sentence reached the planner in fragments. A run
    of lines in one text block at one size is one paragraph; a size change
    inside a block (an eyebrow over its headline, a label over its body) starts
    the next."""
    paragraphs: list[tuple[str, float]] = []
    run: list[str] = []
    run_size: float | None = None
    for line in block.get("lines", []):
        parts = [
            (span.get("text") or "").strip()
            for span in line.get("spans", [])
            if (span.get("text") or "").strip()
        ]
        if not parts:
            continue
        size = max(
            float(span.get("size") or 0)
            for span in line.get("spans", [])
            if (span.get("text") or "").strip()
        )
        text = _collapse_tracking(" ".join(parts))
        if run and round(size, 1) != round(run_size or 0, 1):
            paragraphs.append((_join_lines(run), run_size or 0.0))
            run = []
        run.append(text)
        run_size = size
    if run:
        paragraphs.append((_join_lines(run), run_size or 0.0))
    return paragraphs


def _join_lines(lines: list[str]) -> str:
    """Rejoin wrapped lines; a line broken after a hyphen keeps the hyphen and
    no space ("mobile-" + "friendly" is "mobile-friendly")."""
    text = lines[0]
    for line in lines[1:]:
        text += line if text.endswith("-") else " " + line
    return text


# CSS letter-spacing printed to PDF puts a space between every glyph and two
# between words: "F O R  K I N D E R GA R T E N S , C H I L D". Kerned pairs
# survive as two letters ("GA"), so a line is tracked when most of its pieces
# are single glyphs — "A to Z  of it" has two in five and is left alone.
_TRACKED_WORD_GAP = re.compile(r"\s{2,}")
_TRACKED_MIN_GLYPH_SHARE = 0.6
_PUNCTUATION_GAP = re.compile(r"([,;:])(?=\S)")


def _collapse_tracking(text: str) -> str:
    pieces = text.split()
    if len(pieces) < 4 or "  " not in text:
        return text
    if sum(len(piece) == 1 for piece in pieces) / len(pieces) < _TRACKED_MIN_GLYPH_SHARE:
        return text
    words = (word.replace(" ", "") for word in _TRACKED_WORD_GAP.split(text.strip()))
    return _PUNCTUATION_GAP.sub(r"\1 ", " ".join(words))


def _image_xrefs(page) -> dict[int, int]:
    """Image block number → its XObject xref (0/absent for inline images)."""
    try:
        return {
            info["number"]: info["xref"]
            for info in page.get_image_info(xrefs=True)
            if info.get("xref")
        }
    except Exception:  # noqa: BLE001
        return {}


def _with_alpha(doc, block: dict, xref: int | None) -> dict:
    """The block with its soft mask applied, so a transparent image stays one.

    A dict image block carries the BASE image only. A logo drawn as white
    letters through a soft mask then arrives as white letters on a black box:
    the header shows the box, and the palette is read from black and white
    (Fowable's cover mark came out as six greys). Any failure keeps the block."""
    if not xref:
        return block
    try:
        smask = doc.extract_image(xref).get("smask")
        if not smask:
            return block
        base = fitz.Pixmap(doc, xref)
        if base.alpha:
            base = fitz.Pixmap(base, 0)
        if base.colorspace and base.colorspace.n not in (1, 3):
            base = fitz.Pixmap(fitz.csRGB, base)
        masked = fitz.Pixmap(base, fitz.Pixmap(doc, smask))
        return {**block, "image": masked.tobytes("png"), "ext": "png"}
    except Exception as exc:  # noqa: BLE001
        logger.debug("Soft mask for image xref %s not applied: %s", xref, exc)
        return block


def _collect_pdf_image(
    block: dict,
    images: list[DocImage],
    seen_hashes: set[bytes],
    *,
    anchor: int,
) -> None:
    """Append a dict image block as a :class:`DocImage`, de-duped by content.

    The same logo/photo repeated on every page shares identical bytes, so we
    hash to keep only the first occurrence (and its document position)."""
    data = block.get("image")
    if not data:
        return
    digest = hashlib.sha1(data).digest()
    if digest in seen_hashes:
        return
    seen_hashes.add(digest)
    ext = (block.get("ext") or "png").lower()
    images.append(
        DocImage(
            data=data,
            mime=_IMG_EXT_TO_MIME.get(ext, "image/png"),
            width=block.get("width"),
            height=block.get("height"),
            anchor=anchor,
        )
    )


# Heading levels a PDF's sizes are ranked into: markdown's six. Sizes past the
# sixth tier share it.
_PDF_HEADING_TIERS = 6


def _pdf_size_to_level(size: float, heading_sizes: list[int]) -> int:
    """Map a heading's font size to a 1-6 level using the document's size tiers.

    ``heading_sizes`` is sorted largest-first: the largest distinct size is H1,
    the next H2, and so on to H6, which every smaller size shares. It used to
    stop at H3, and a designed brief spends three tiers on its cover alone —
    Fowable's 20pt section headlines and 9pt card titles both became H3, so no
    reader could tell a section from a card inside it. Falls back to level 1
    if the size isn't a known tier (shouldn't happen, but keeps a heading from
    becoming body).
    """
    rounded = round(size)
    for idx, tier in enumerate(heading_sizes[:_PDF_HEADING_TIERS]):
        if rounded >= tier:
            return idx + 1
    return min(len(heading_sizes), _PDF_HEADING_TIERS) or 1


def _pdf_title(doc) -> str | None:
    """Read /Title metadata; fall back to None."""
    try:
        meta = doc.metadata or {}
        title = (meta.get("title") or "").strip()
        return title or None
    except Exception:  # noqa: BLE001
        return None


# --- DOCX -----------------------------------------------------------------------


_HEADING_STYLE_PREFIXES = ("heading", "title", "subtitle")
_DOCX_BLIP = qn("a:blip")
_DOCX_EMBED = qn("r:embed")


def parse_docx(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Extract text + headings + images from a DOCX file."""
    try:
        document = python_docx.Document(io.BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not open DOCX: {exc}") from exc

    text_parts: list[str] = []
    headings: list[str] = []
    outline: list[OutlineBlock] = []
    images: list[DocImage] = []
    seen_headings: set[str] = set()
    seen_image_hashes: set[bytes] = set()
    title_from_style: str | None = None

    # Paragraphs and tables in document order. Walking `document.paragraphs`
    # and then `document.tables` put every table after the last paragraph:
    # Fowable's plan table landed past the brief's appendix, outside its copy,
    # and the homepage had no plans to show.
    content = list(document.iter_inner_content())
    level_of = _docx_levels([item for item in content if isinstance(item, Paragraph)])
    for item in content:
        if isinstance(item, Table):
            for block in _docx_table_blocks(item):
                outline.append(block)
                text_parts.append(block.text)
            continue
        paragraph = item
        # Inline images (<w:drawing><a:blip>) are collected at their
        # paragraph's position so each one stays associated with the heading it
        # sits under — an empty paragraph that holds only an image still
        # contributes the image at the right anchor.
        text = (paragraph.text or "").strip()
        blips = paragraph._p.findall(".//" + _DOCX_BLIP)
        if not text and not blips:
            continue
        if text:
            style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
            level = level_of.get(id(paragraph), 0)
            if level > 0:
                outline.append(OutlineBlock(level=level, text=text))
                if _HEADING_MIN_LEN <= len(text) <= _HEADING_MAX_LEN:
                    key = text.lower()
                    if key not in seen_headings:
                        seen_headings.add(key)
                        headings.append(text)
                if title_from_style is None and style_name.startswith("title"):
                    title_from_style = text
            else:
                outline.append(OutlineBlock(level=0, text=text))
            text_parts.append(text)
        for blip in blips:
            _collect_docx_image(
                blip, document, images, seen_image_hashes, anchor=len(outline)
            )

    raw_text = "\n".join(text_parts)

    title = title_from_style or _docx_core_title(document) or (headings[0] if headings else None)

    return ParsedDocument(
        source_kind="docx",
        source_ref=filename,
        title=title,
        raw_text=raw_text,
        headings=headings[:50],
        images=images,
        outline=outline,
    )


def _collect_docx_image(
    blip,
    document,
    images: list[DocImage],
    seen_hashes: set[bytes],
    *,
    anchor: int,
) -> None:
    """Resolve a ``<a:blip>`` to its image part and append it as a DocImage."""
    rid = blip.get(_DOCX_EMBED)
    if not rid:
        return
    part = document.part.related_parts.get(rid)
    if part is None:
        return
    try:
        data = part.blob
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed reading DOCX image %s: %s", rid, exc)
        return
    if not data:
        return
    digest = hashlib.sha1(data).digest()
    if digest in seen_hashes:
        return
    seen_hashes.add(digest)
    img = getattr(part, "image", None)
    images.append(
        DocImage(
            data=data,
            mime=getattr(part, "content_type", None) or "image/png",
            width=getattr(img, "px_width", None),
            height=getattr(img, "px_height", None),
            anchor=anchor,
        )
    )


# A DOCX heading level is its style's; past the styles, markdown's six.
_DOCX_MAX_LEVEL = 6
# What a table's cards are titled at: the deepest level, below any heading the
# document sets itself (`outline_sections` reads a section's cards at its most
# common deeper level).
_TABLE_CARD_LEVEL = _DOCX_MAX_LEVEL
# A cell's or column's first line this long is a sentence, not a card title.
_TABLE_TITLE_MAX_CHARS = 90


def _docx_levels(paragraphs: list[Paragraph]) -> dict[int, int]:
    """Each heading paragraph's level, keyed by ``id(paragraph)``.

    A heading style states its level. A designed brief often states none: its
    section headlines are 16pt bold Normal paragraphs and its card titles and
    FAQ questions 11.5pt bold ones, so read by style alone every section was
    one block of prose. A short paragraph that is wholly bold and larger than
    body text, or set well above it, is a heading too — ranked by size like a
    PDF's (``_pdf_size_to_level``), below the deepest style level in use. A bold
    line at body size is a button or an emphasised sentence ("Talk to us about
    the right plan"), and a smaller one a label ("SECTION HEADLINE").
    """
    styled: dict[int, int] = {}
    sized: list[tuple[Paragraph, float, bool]] = []
    for paragraph in paragraphs:
        text = (paragraph.text or "").strip()
        if not text:
            continue
        style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
        level = _docx_style_to_level(style_name)
        if level:
            styled[id(paragraph)] = level
        else:
            size, bold = _docx_look(paragraph)
            if size is not None:
                sized.append((paragraph, size, bold))
    if not sized:
        return styled
    body = Counter(size for _, size, _ in sized).most_common(1)[0][0]
    visual = [
        (paragraph, size)
        for paragraph, size, bold in sized
        if _HEADING_MIN_LEN <= len(paragraph.text.strip()) <= _HEADING_MAX_LEN
        and ((bold and size > body) or size >= body * _HEADING_MIN_SCALE)
    ]
    # Ranked from the smallest up: a card title and the section headline above
    # it are the distinction a page needs; the cover's display sizes are not,
    # and ranked from the top they used up every level before the cards.
    tiers = sorted({size for _, size in visual})
    first = max(styled.values(), default=0) + 1
    levels = dict(styled)
    for paragraph, size in visual:
        levels[id(paragraph)] = max(first, _DOCX_MAX_LEVEL - tiers.index(size))
    return levels


def _docx_look(paragraph: Paragraph) -> tuple[float | None, bool]:
    """The paragraph's type size in points (its largest run's) and whether
    every run that carries text is bold, each resolved through run, character
    style and paragraph style chains."""
    sizes: list[float] = []
    bold = True
    for run in paragraph.runs:
        if not run.text.strip():
            continue
        size = run.font.size or _docx_style_value(run.style, "size") or _docx_style_value(
            paragraph.style, "size"
        )
        if size is not None:
            sizes.append(size.pt)
        weight = run.bold
        if weight is None:
            weight = _docx_style_value(run.style, "bold")
        if weight is None:
            weight = _docx_style_value(paragraph.style, "bold")
        bold = bold and bool(weight)
    return (max(sizes) if sizes else None), (bold and bool(sizes))


def _docx_style_value(style, attribute: str):
    """A font attribute from a style, or from the first base style that sets it."""
    while style is not None:
        value = getattr(style.font, attribute, None)
        if value is not None:
            return value
        style = style.base_style
    return None


def _docx_table_blocks(table: Table) -> list[OutlineBlock]:
    """A table as outline blocks, its cards as headings over their copy.

    Flattened row by row, a plan table read "Starter | Growth | Business", then
    each row's three plans run together — every plan's features interleaved,
    and nothing saying which price was whose. Three shapes are read as cards:

    - **one plan per column**: a header row and a row that prices every column;
    - **one card per cell**: every cell a short title line over its copy;
    - **one card per row**: two columns under a header row ("Step | What
      happens"), the first a short title.

    Any other table stays one line per row, cells joined by " | ".
    """
    rows = [_docx_row_cells(row) for row in table.rows]
    rows = [row for row in rows if any(row)]
    if not rows:
        return []
    width = max(len(row) for row in rows)

    def card(title: str, lines: list[str]) -> list[OutlineBlock]:
        return [
            OutlineBlock(level=_TABLE_CARD_LEVEL, text=title),
            *(OutlineBlock(level=0, text=line) for line in lines),
        ]

    def lines_of(cell: str) -> list[str]:
        return [line.strip() for line in cell.split("\n") if line.strip()]

    header, rest = rows[0], rows[1:]
    if (
        width >= 2 and rest and len(header) == width and all(header)
        and not any(PRICE.search(cell) for cell in header)
        and any(len(row) == width and all(row) and all(PRICE.search(cell) for cell in row) for row in rest)
    ):
        blocks: list[OutlineBlock] = []
        for column, title in enumerate(header):
            body = [line for row in rest if column < len(row) for line in lines_of(row[column])]
            blocks += card(" ".join(title.split()), body)
        return blocks

    cells = [lines_of(cell) for row in rows for cell in row if cell]
    if len(cells) >= 2 and all(
        len(lines) >= 2 and len(lines[0]) <= _TABLE_TITLE_MAX_CHARS for lines in cells
    ):
        return [block for lines in cells for block in card(lines[0], lines[1:])]

    if width == 2 and len(rest) >= 2 and all(
        len(row) == 2 and row[0] and len(row[0]) <= _TABLE_TITLE_MAX_CHARS for row in rest
    ):
        blocks = [OutlineBlock(level=0, text=" | ".join(header))]
        for title, body in rest:
            blocks += card(" ".join(title.split()), lines_of(body))
        return blocks

    return [OutlineBlock(level=0, text=" | ".join(cell for cell in row if cell)) for row in rows]


def _docx_row_cells(row) -> list[str]:
    """The row's cell texts, a horizontally merged cell once (python-docx
    returns it once per grid column it spans)."""
    seen: set[int] = set()
    cells: list[str] = []
    for cell in row.cells:
        if id(cell._tc) in seen:
            continue
        seen.add(id(cell._tc))
        cells.append(cell.text.strip())
    return cells


def _docx_style_to_level(style_name: str) -> int:
    """Map a (lowercased) DOCX paragraph style to an outline level.

    ``Title`` → 1 and ``Subtitle`` → 2 so a cover-page title/tagline anchors the
    hierarchy; ``Heading N`` maps to ``min(N, 3)``; everything else is body (0).
    """
    if style_name.startswith("title"):
        return 1
    if style_name.startswith("subtitle"):
        return 2
    if style_name.startswith("heading"):
        digits = "".join(ch for ch in style_name if ch.isdigit())
        if digits:
            return min(int(digits), 3)
        return 1
    return 0


def _docx_core_title(document) -> str | None:
    try:
        props = document.core_properties
        title = (getattr(props, "title", "") or "").strip()
        return title or None
    except Exception:  # noqa: BLE001
        return None


# --- dispatcher -----------------------------------------------------------------


class DocParseError(Exception):
    """Raised when a document is unparseable. Carries a friendly status code."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def parse_document(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Dispatch by extension. Caller is responsible for catching DocParseError."""
    if not filename or not file_bytes:
        raise DocParseError("Empty file or missing filename", status=400)

    lower = filename.lower()
    try:
        if lower.endswith(".pdf"):
            parsed = parse_pdf(file_bytes, filename)
        elif lower.endswith(".docx"):
            parsed = parse_docx(file_bytes, filename)
        elif lower.endswith(".doc"):
            raise DocParseError(
                "Legacy .doc files aren't supported — please save as .docx.",
                status=415,
            )
        else:
            raise DocParseError(
                f"Unsupported file type: {filename}. Upload a PDF or DOCX.",
                status=415,
            )
    except ValueError as exc:
        raise DocParseError(str(exc), status=400) from exc

    if not parsed.raw_text.strip() or len(parsed.raw_text.strip()) < 80:
        raise DocParseError(
            "Could not extract enough text content from that file. "
            "A scanned PDF is read with OCR, but this one gave too little "
            "readable text. Paste its content into the paste box instead.",
            status=422,
        )

    return parsed


# --- helpers for the router -----------------------------------------------------


def image_to_data_url(image_bytes: bytes, mime: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"
