"""
Paste endpoint. Copy or markup the user pasted, read into the same preview
payload a crawl or an upload returns, so the frontend's
ScrapePreview → PagePicker → Generate flow needs no fourth branch.

One endpoint covers both ways a paste is used, on the presence of ``base``:

* **Standalone** (no ``base``) — the paste *is* the source.
* **Added on** (``base`` = what the crawler or the document parser just
  produced) — the paste is merged into it, and the merged source comes back.

Merging happens here rather than in the crawl job because a paste is
orthogonal to the read: the job stays "read this URL", cacheable and reusable
on the URL alone, and the paste rides on a separate call the frontend makes
once the read lands. See ``services/paste_source.merge_sources`` for the
page-joining rule.

``image_candidates`` in the response are the PASTE's own. The reader that
produced ``base`` built its candidates from a live layout and carries richer
evidence than the source's metadata can reproduce, so the caller keeps those
and appends these (frontend/src/lib/sourcePaste.ts).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.models.content_blocks import SourceContent
from app.services.doc_structure import split_into_pages
from app.services.paste_source import PASTE_LABEL, read_paste, merge_sources
from app.services.source_preview import candidates_from_source, source_preview_payload

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/paste", tags=["paste"])

# Roughly 60k words — far past any hand-written brief, and the point where the
# planner is truncating the text anyway (settings.llm_context_tokens).
_TEXT_LIMIT_CHARS = 400_000


class PastePreviewRequest(BaseModel):
    """Pasted copy or markup, optionally merged into a source already read."""

    text: str
    title: str | None = None
    base: SourceContent | None = Field(
        default=None,
        description=(
            "The source this paste is added to (a crawl result or a parsed "
            "document). Omit for a standalone paste."
        ),
    )


@router.post("/preview")
async def paste_preview(payload: PastePreviewRequest) -> dict:
    """Read a paste into a preview payload, merging it into `base` when given."""
    text = payload.text or ""
    if not text.strip():
        raise HTTPException(status_code=400, detail="Nothing pasted.")
    if len(text) > _TEXT_LIMIT_CHARS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"That's {len(text):,} characters — the limit is "
                f"{_TEXT_LIMIT_CHARS:,}. Trim it, or upload it as a document."
            ),
        )

    # Parsing markup and splitting it into pages is pure blocking CPU, and a
    # 400k-character paste is a real page's worth of DOM. Off the loop for the
    # same reason the document parse is.
    return await asyncio.to_thread(
        _build_preview, text, payload.title, payload.base
    )


def _build_preview(text: str, title: str | None, base: SourceContent | None) -> dict:
    parsed = read_paste(text, title=title)
    pasted = split_into_pages(
        parsed.document, images=parsed.images, description=parsed.description
    )
    if not pasted.raw_text.strip() and not pasted.discovered_pages:
        raise HTTPException(
            status_code=422,
            detail=(
                "No readable text in that paste. If it's markup, check it "
                "carries content and not just scripts or styles."
            ),
        )

    source = merge_sources(base, pasted) if base is not None else pasted
    label = source.source_ref or PASTE_LABEL
    added_pages = len(source.discovered_pages) - (
        len(base.discovered_pages) if base is not None else 0
    )

    return source_preview_payload(
        url=label,
        final_url=label,
        source_content=source,
        brand_candidate=None,
        image_candidates=candidates_from_source(pasted),
        extra={
            "paste": {
                "is_html": parsed.is_html,
                "characters": len(text),
                "added_pages": max(added_pages, 0),
                "unresolved_images": parsed.unresolved_images,
                "merged": base is not None,
            }
        },
    )
