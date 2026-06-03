"""
Document preview endpoint. Accepts a PDF or DOCX upload and returns the same
response shape as /api/scrape/preview so the frontend's ScrapePreview component
can render either source kind without branching.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.models.brand import BrandIdentity
from app.models.content_blocks import ImageMetadata, SourceContent
from app.services.doc_parser import (
    DocParseError,
    image_to_data_url,
    parse_document,
)
from app.services.logo import extract_palette_from_image_bytes

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/document", tags=["document"])


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
                mood=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Brand palette extraction failed: %s", exc)

    source_content = SourceContent(
        source_kind=parsed.source_kind,
        source_ref=parsed.source_ref,
        title=parsed.title,
        description=None,
        raw_text=parsed.raw_text,
        headings=parsed.headings,
        images=[c.url for c in image_candidates],
        links=[],
        url_path=None,
        discovered_pages=[],
        image_metadata=[
            ImageMetadata(
                url=c.url,
                alt=c.alt,
                intent=c.intent,  # type: ignore[arg-type]
                width=c.width,
                height=c.height,
            )
            for c in image_candidates
        ],
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
        "discovered_count": 0,
    }
