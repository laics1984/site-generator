"""
Document endpoints.

- ``/preview`` accepts a PDF/DOCX upload and returns the same response shape as
  a crawl job's result so the frontend's ScrapePreview component can render either
  source kind without branching.
- ``/export`` renders a scraped/inferred site into an editable .docx content
  brief that re-imports through ``/preview`` (the scrape → document bridge).
"""

from __future__ import annotations

import asyncio
import logging
import re

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.models.brand import BrandIdentity
from app.models.content_blocks import IndustryCategoryLiteral, SourceContent
from app.services.doc_export import build_site_document
from app.services.doc_parser import (
    DocImage,
    DocParseError,
    ParsedDocument,
    image_to_data_url,
    parse_document,
)
from app.services.doc_structure import DocImageRef, outline_plan, split_into_pages
from app.services.logo import extract_palette_from_image_bytes
from app.services.outline_pages import OutlinePlan, SiteShape
from app.services.page_inference import infer_page_scaffolds
from app.services.paste_structure import StructuredDocument, structure_document
from app.services.photo_quality import depicts_nothing
from app.services.source_preview import candidates_from_source, source_preview_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/document", tags=["document"])

_DOCX_MEDIA_TYPE = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)


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


@router.post("/preview")
async def document_preview(
    file: UploadFile = File(...),
    site_shape: SiteShape | None = Form(None),
) -> dict:
    """Parse an uploaded PDF/DOCX and return SourceContent + brand candidate.

    Response shape matches a crawl job's `result` so the frontend can reuse the
    ScrapePreview → PagePicker → Generate flow with no source-kind branching.

    ``site_shape`` is the page picker's "Site type": a one-page landing site
    or a multi-page website, over what the document's own outline says
    (``outline_pages``). Omitted, the document decides. The pages exist in the
    source itself, so the choice re-reads the document rather than reshaping a
    recipe; the structure read is cached, so the second read costs no call.
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

    # Parsing is blocking CPU — PyMuPDF/python-docx, a base64 pass over each
    # image, a per-pixel palette walk — so it runs off the event loop; on it a
    # 20 MB PDF stalls every other request on this worker, /health included.
    # Same move (and same reason) as the crawler's
    # `asyncio.to_thread(_parse_rendered_html, ...)` in services/scraper.py.
    try:
        parsed = await asyncio.to_thread(parse_document, contents, file.filename)
    except DocParseError as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc)) from exc

    # Which parts are pages and which lines are copy is read by the LLM
    # (services/paste_structure.py), in line numbers, never text. A designed
    # brief's typography only says what is prominent. Unavailable, disabled or
    # an answer that doesn't line up ⇒ `None`, and the headings split it.
    structured = await structure_document(parsed, shape=site_shape)
    return await asyncio.to_thread(_build_preview, parsed, structured, site_shape)


def _build_preview(
    parsed: ParsedDocument,
    structured: StructuredDocument | None,
    site_shape: SiteShape | None = None,
) -> dict:
    """The synchronous split → payload pipeline, run off the loop."""
    document = structured.document if structured else parsed

    # Separate a logo seed from feature imagery: a small cover graphic becomes the
    # brand logo (and is kept out of the hero pool), while feature-sized images
    # are filtered and handed to the splitter to place per page. The logo is
    # read from the document as printed — the cover is often a part the
    # structure read leaves out. A browser-printed PDF carries its box-shadows
    # and placeholder boxes as images; those show nothing and would become
    # blank section photos (`depicts_nothing`).
    logo_image = _select_logo(parsed.images)
    feature_refs: list[DocImageRef] = [
        DocImageRef(
            url=image_to_data_url(img.data, img.mime),
            width=img.width,
            height=img.height,
            anchor=img.anchor,
        )
        for img in document.images
        if (logo_image is None or img.data != logo_image.data)
        and _is_feature_sized(img)
        and not depicts_nothing(img.data)
    ]

    # Brand candidate: only seed from a genuine logo-like image so a hero photo
    # never gets mistaken for the logo.
    brand_candidate: BrandIdentity | None = None
    if logo_image is not None:
        try:
            extraction = extract_palette_from_image_bytes(logo_image.data)
            brand_candidate = BrandIdentity(
                name=document.title or "Untitled",
                logo_data_url=extraction.logo_data_url,
                extracted_palette=extraction.palette,
                logo_is_light=extraction.logo_is_light,
                mood=None,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Brand palette extraction failed: %s", exc)

    # Split the document into page-shaped content, placing each image on the
    # page + heading it appears under. The planner (not this module) still owns
    # section composition + distinctiveness.
    source_content = split_into_pages(
        document,
        images=feature_refs,
        page_topics=structured.page_topics if structured else None,
        shape=site_shape,
    )
    image_candidates = candidates_from_source(source_content)

    return source_preview_payload(
        url=parsed.source_ref,
        final_url=parsed.source_ref,
        source_content=source_content,
        brand_candidate=brand_candidate,
        image_candidates=image_candidates,
        fetched_at=0.0,
        # Which reader worked out the pages, so a silent fallback (LLM down)
        # is visible rather than a mysteriously flat site.
        extra={"document": {
            "structured_by": "llm" if structured else "headings",
            **_site_shape_report(
                structured.plan if structured else outline_plan(parsed, site_shape),
                structured,
                source_content,
            ),
        }},
    )


def _site_shape_report(
    plan: OutlinePlan | None,
    structured: StructuredDocument | None,
    source: SourceContent,
) -> dict:
    """What the picker's "Site type" shows: the shape built, the shape the
    document itself suggests, and why. With no plan (no titles to group by,
    or a model that grouped a source with no ranks) the pages it built are
    the evidence."""
    if plan is not None:
        built, suggested, reason = not plan.one_page, not plan.suggests_one_page, plan.reason
    else:
        named = structured is not None and any(
            page_type != "home" for page_type in structured.page_topics.values()
        )
        built = bool(source.discovered_pages)
        suggested, reason = named or built, "it has no titles to group its parts by"
    return {
        "site_shape": "multi_page" if built else "one_page",
        "suggested_site_shape": "multi_page" if suggested else "one_page",
        "site_shape_reason": reason,
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
    # Both are pure-CPU tree walks (page inference, then a python-docx write) —
    # off the loop for the same reason /preview's parse is.
    scaffolds = await asyncio.to_thread(
        infer_page_scaffolds,
        payload.source,
        industry=industry,
        site_name=payload.site_name,
    )
    if not scaffolds:
        raise HTTPException(
            status_code=422, detail="No pages could be inferred from this source."
        )

    docx_bytes = await asyncio.to_thread(
        build_site_document, payload.source, scaffolds, site_name=payload.site_name
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
