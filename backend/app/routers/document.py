"""
Document endpoints.

- ``/preview`` accepts a PDF/DOCX upload and returns the same response shape as
  /api/scrape/preview so the frontend's ScrapePreview component can render either
  source kind without branching.
- ``/export`` renders a scraped/inferred site into an editable .docx content
  brief that re-imports through ``/preview`` (the scrape → document bridge).
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.models.brand import BrandIdentity
from app.models.content_blocks import (
    ImageMetadata,
    IndustryCategoryLiteral,
    SourceContent,
)
from app.services.doc_export import build_site_document
from app.services.doc_parser import (
    DocParseError,
    image_to_data_url,
    parse_document,
)
from app.services.doc_structure import split_into_pages
from app.services.logo import extract_palette_from_image_bytes
from app.services.page_inference import infer_page_scaffolds

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/document", tags=["document"])

_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


@dataclass
class _ImageCandidate:
    """Mirror of scraper.ImageCandidate so the frontend ImageCandidate type
    works for both sources without changes."""

    url: str
    alt: str
    width: int | None
    height: int | None
    intent: str


_FILE_SIZE_LIMIT = 20 * 1024 * 1024  # 20 MB


@router.post("/preview")
async def document_preview(file: UploadFile = File(...)) -> dict:
    """Parse an uploaded PDF/DOCX and return SourceContent + brand candidate.

    Response shape matches /api/scrape/preview so the frontend can reuse the
    ScrapePreview → PagePicker → Generate flow with no source-kind branching.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided.")

    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Empty file.")
    if len(contents) > _FILE_SIZE_LIMIT:
        raise HTTPException(
            status_code=413,
            detail=f"File too large ({len(contents) // 1024} KB). Max 20 MB.",
        )

    try:
        parsed = parse_document(contents, file.filename)
    except DocParseError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    # Build image candidates from extracted images. First image is the strongest
    # logo candidate (cover page in PDFs, first inline image in DOCX).
    image_candidates: list[_ImageCandidate] = []
    for i, (img_bytes, mime) in enumerate(zip(parsed.images, parsed.image_mimes)):
        data_url = image_to_data_url(img_bytes, mime)
        # First image becomes the hero candidate; rest are generic.
        intent = "hero" if i == 0 else "generic"
        image_candidates.append(
            _ImageCandidate(url=data_url, alt=parsed.title or "", width=None, height=None, intent=intent)
        )

    # Brand candidate: try the first image as a logo seed.
    brand_candidate: BrandIdentity | None = None
    if parsed.images:
        try:
            extraction = extract_palette_from_image_bytes(parsed.images[0])
            brand_candidate = BrandIdentity(
                name=parsed.title or "Untitled",
                logo_data_url=extraction.logo_data_url,
                extracted_palette=extraction.palette,
                logo_is_light=extraction.logo_is_light,
                mood=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Brand palette extraction failed: %s", exc)

    # Examine the document's titles to split it into page-shaped content. The
    # planner (not this module) still owns section composition + distinctiveness;
    # we only hand it discovered_pages the same way the crawler would.
    image_metadata = [
        ImageMetadata(
            url=c.url,
            alt=c.alt,
            intent=c.intent,  # type: ignore[arg-type]
            width=c.width,
            height=c.height,
        )
        for c in image_candidates
    ]
    source_content = split_into_pages(
        parsed,
        images=[c.url for c in image_candidates],
        image_metadata=image_metadata,
    )

    return {
        "url": file.filename,
        "final_url": file.filename,
        "source_content": source_content.model_dump(mode="json"),
        "brand_candidate": (
            brand_candidate.model_dump(mode="json") if brand_candidate else None
        ),
        "image_candidates": [asdict(c) for c in image_candidates],
        "fetched_at": 0.0,
        "discovered_count": len(source_content.discovered_pages),
    }


class ExportRequest(BaseModel):
    """A scraped/uploaded source to render as an editable .docx content brief."""

    source: SourceContent
    site_name: str | None = None
    # Optional industry so the inferred page set matches the picker; defaults to
    # a neutral template (no LLM call) since the brief's titles come from the
    # source's own pages, not the industry.
    industry: IndustryCategoryLiteral | None = None


@router.post("/export")
async def document_export(payload: ExportRequest) -> StreamingResponse:
    """Render the source's inferred pages + copy into a downloadable .docx.

    The titles come from ``infer_page_scaffolds`` (the same inference the page
    picker uses), so the brief lists the canonical page set. Editing it and
    re-uploading via ``/preview`` regenerates the site from the document.
    """
    industry = payload.industry or "other"
    scaffolds = infer_page_scaffolds(
        payload.source, industry=industry, site_name=payload.site_name
    )
    if not scaffolds:
        raise HTTPException(
            status_code=422, detail="No pages could be inferred from this source."
        )

    docx_bytes = build_site_document(
        payload.source, scaffolds, site_name=payload.site_name
    )
    filename = _safe_filename(payload.site_name or payload.source.title or "website")

    return StreamingResponse(
        iter([docx_bytes]),
        media_type=_DOCX_MEDIA_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}.docx"'},
    )


def _safe_filename(label: str) -> str:
    """ASCII, dash-separated stem safe for a Content-Disposition header."""
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", label.strip().lower()).strip("-")
    return (stem[:60] or "website") + "-content"
