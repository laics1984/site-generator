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
import io
import logging
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

import docx as python_docx  # python-docx
import fitz  # PyMuPDF — `import fitz` is correct for the pymupdf package

logger = logging.getLogger(__name__)


SourceKindDoc = Literal["pdf", "docx"]


@dataclass
class ParsedDocument:
    source_kind: SourceKindDoc
    source_ref: str           # original filename
    title: str | None
    raw_text: str
    headings: list[str] = field(default_factory=list)
    images: list[bytes] = field(default_factory=list)  # raw bytes, in encounter order
    image_mimes: list[str] = field(default_factory=list)  # parallel to images


# --- PDF ------------------------------------------------------------------------


_HEADING_MIN_SCALE = 1.18  # font-size > body_median * this counts as a heading
_HEADING_MIN_LEN = 2
_HEADING_MAX_LEN = 200


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
        # PDF has no text layer (image-only / scanned). Caller decides how to surface.
        doc.close()
        return ParsedDocument(
            source_kind="pdf",
            source_ref=filename,
            title=_pdf_title(doc),
            raw_text="",
            headings=[],
            images=[],
            image_mimes=[],
        )

    body_median = Counter(sizes).most_common(1)[0][0]
    heading_threshold = body_median * _HEADING_MIN_SCALE

    # Pass 2: pull text + headings, in document order
    text_chunks: list[str] = []
    headings: list[str] = []
    seen_headings: set[str] = set()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                line_text_parts: list[str] = []
                line_max_size = 0.0
                for span in line.get("spans", []):
                    span_text = (span.get("text") or "").strip()
                    if not span_text:
                        continue
                    line_text_parts.append(span_text)
                    line_max_size = max(line_max_size, float(span.get("size") or 0))
                if not line_text_parts:
                    continue
                line_text = " ".join(line_text_parts)
                text_chunks.append(line_text)
                if (
                    line_max_size >= heading_threshold
                    and _HEADING_MIN_LEN <= len(line_text) <= _HEADING_MAX_LEN
                    and line_text.lower() not in seen_headings
                ):
                    seen_headings.add(line_text.lower())
                    headings.append(line_text)

    raw_text = "\n".join(text_chunks)

    # Pass 3: extract images.
    # page.get_images() returns tuples; xref is index 0.
    images: list[bytes] = []
    mimes: list[str] = []
    seen_xrefs: set[int] = set()
    for page in doc:
        for img_info in page.get_images(full=True):
            xref = img_info[0]
            if xref in seen_xrefs:
                continue
            seen_xrefs.add(xref)
            try:
                pix = fitz.Pixmap(doc, xref)
                # Convert CMYK / alpha to RGB PNG for downstream consistency.
                if pix.n - pix.alpha > 3:  # CMYK
                    pix = fitz.Pixmap(fitz.csRGB, pix)
                png_bytes = pix.tobytes("png")
                images.append(png_bytes)
                mimes.append("image/png")
                pix = None
            except Exception as exc:  # noqa: BLE001
                logger.debug("Skipped PDF image xref=%s: %s", xref, exc)
                continue

    title = _pdf_title(doc) or (headings[0] if headings else None)
    doc.close()

    return ParsedDocument(
        source_kind="pdf",
        source_ref=filename,
        title=title,
        raw_text=raw_text,
        headings=headings[:50],
        images=images,
        image_mimes=mimes,
    )


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
_DOCX_IMAGE_DIR = "word/media/"
_DOCX_IMAGE_EXTS_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
}


def parse_docx(file_bytes: bytes, filename: str) -> ParsedDocument:
    """Extract text + headings + images from a DOCX file."""
    try:
        document = python_docx.Document(io.BytesIO(file_bytes))
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"Could not open DOCX: {exc}") from exc

    text_parts: list[str] = []
    headings: list[str] = []
    seen_headings: set[str] = set()
    title_from_style: str | None = None

    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        if not text:
            continue
        style_name = (paragraph.style.name or "").lower() if paragraph.style else ""
        is_heading = any(style_name.startswith(p) for p in _HEADING_STYLE_PREFIXES)
        if is_heading:
            if _HEADING_MIN_LEN <= len(text) <= _HEADING_MAX_LEN:
                key = text.lower()
                if key not in seen_headings:
                    seen_headings.add(key)
                    headings.append(text)
            if title_from_style is None and style_name.startswith("title"):
                title_from_style = text
        text_parts.append(text)

    # Tables — flatten cell text so the LLM sees pricing tables, hours, etc.
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
            if cells:
                text_parts.append(" | ".join(cells))

    raw_text = "\n".join(text_parts)

    # Images: DOCX is a ZIP; images live in word/media/*. Sorting by filename
    # roughly matches encounter order in most Word exports (image1.png < image2.png).
    images: list[bytes] = []
    mimes: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            media_entries = sorted(
                [n for n in zf.namelist() if n.startswith(_DOCX_IMAGE_DIR)]
            )
            for entry in media_entries:
                ext = "." + entry.rsplit(".", 1)[-1].lower() if "." in entry else ""
                mime = _DOCX_IMAGE_EXTS_TO_MIME.get(ext)
                if not mime:
                    continue
                try:
                    raw = zf.read(entry)
                    if raw:
                        images.append(raw)
                        mimes.append(mime)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed reading DOCX image %s: %s", entry, exc)
    except zipfile.BadZipFile:
        logger.warning("DOCX file is not a valid ZIP — no images extracted")

    title = title_from_style or _docx_core_title(document) or (headings[0] if headings else None)

    return ParsedDocument(
        source_kind="docx",
        source_ref=filename,
        title=title,
        raw_text=raw_text,
        headings=headings[:50],
        images=images,
        image_mimes=mimes,
    )


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
            "If it's a scanned PDF, the generator needs a text layer — "
            "OCR isn't supported. Paste the content into the URL tab's textarea instead.",
            status=422,
        )

    return parsed


# --- helpers for the router -----------------------------------------------------


def image_to_data_url(image_bytes: bytes, mime: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime};base64,{encoded}"
