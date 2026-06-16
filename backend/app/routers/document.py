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
from app.models.content_blocks import IndustryCategoryLiteral, SourceContent
from app.services.doc_export import build_site_document
from app.services.doc_parser import (
    DocImage,
    DocParseError,
    image_to_data_url,
    parse_document,
)
from app.services.doc_structure import DocImageRef, split_into_pages
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
# Below this short side an image is an icon / rule / small logo, not a feature.
_MIN_FEATURE_SHORT_SIDE = 200
# An early image this small is treated as a logo seed rather than a hero photo.
_LOGO_MAX_SHORT_SIDE = 256


def _is_logo_like(img: DocImage) -> bool:
    """A small graphic near the top of the document — a plausible logo, not a hero."""
    if img.width is None or img.height is None:
        return False
    return min(img.width, img.height) <= _LOGO_MAX_SHORT_SIDE and max(img.width, img.height) <= 1024


def _select_logo(images: list[DocImage]) -> DocImage | None:
    """First logo-like image among the opening few (logos live on the cover)."""
    for img in images[:3]:
        if _is_logo_like(img):
            return img
    return None


def _is_feature_sized(img: DocImage) -> bool:
    """Keep images big enough to carry a section; unknown dims pass (can't judge)."""
    if img.width is None or img.height is None:
        return True
    return min(img.width, img.height) >= _MIN_FEATURE_SHORT_SIDE


def _candidates_from_source(source: SourceContent) -> list["_ImageCandidate"]:
    """Flatten placed per-page image metadata into the preview's candidate list."""
    out: list[_ImageCandidate] = []
    for page in [source, *source.discovered_pages]:
        for meta in page.image_metadata:
            out.append(
                _ImageCandidate(
                    url=meta.url,
                    alt=meta.alt,
                    width=meta.width,
                    height=meta.height,
                    intent=meta.intent,
                )
            )
    return out


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

    # Separate a logo seed from feature imagery: a small cover graphic becomes the
    # brand logo (and is kept out of the hero pool), while feature-sized images
    # are filtered and handed to the splitter to place per page.
    logo_image = _select_logo(parsed.images)
    feature_refs: list[DocImageRef] = [
        DocImageRef(
            url=image_to_data_url(img.data, img.mime),
            width=img.width,
            height=img.height,
            anchor=img.anchor,
        )
        for img in parsed.images
        if img is not logo_image and _is_feature_sized(img)
    ]

    # Brand candidate: only seed from a genuine logo-like image so a hero photo
    # never gets mistaken for the logo.
    brand_candidate: BrandIdentity | None = None
    if logo_image is not None:
        try:
            extraction = extract_palette_from_image_bytes(logo_image.data)
            brand_candidate = BrandIdentity(
                name=parsed.title or "Untitled",
                logo_data_url=extraction.logo_data_url,
                extracted_palette=extraction.palette,
                logo_is_light=extraction.logo_is_light,
                mood=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Brand palette extraction failed: %s", exc)

    # Examine the document's titles to split it into page-shaped content, placing
    # each image on the page + heading it appears under. The planner (not this
    # module) still owns section composition + distinctiveness.
    source_content = split_into_pages(parsed, images=feature_refs)
    image_candidates = _candidates_from_source(source_content)

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
