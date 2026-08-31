"""
Combine two already-read sources (e.g. a URL crawl and a document upload) into
one before an optional paste is layered on top.

The paste endpoint already folds a paste into a `base` it's handed
(`routers/paste.py`), but nothing joins two *rich* reader outputs — both
independently-built `SourceContent` trees — until now. This is the other half
of the same operation: `services/source_merge.merge_sources` doesn't care
which reader produced either side.

Deliberately returns only the merged `source_content`. Brand candidate, image
candidates, crawl frontier and Facebook facts are all things only a reader
itself measured, so precedence between two readers' worth of those is a
frontend concern (frontend/src/lib/sourceCombine.ts) — the same split
`sourcePaste.withPastedContent` already draws for a paste's `base`.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter
from pydantic import BaseModel

from app.models.content_blocks import SourceContent
from app.services.source_merge import merge_sources
from app.services.source_preview import source_preview_payload

router = APIRouter(prefix="/api/source", tags=["source"])


class SourceMergeRequest(BaseModel):
    """Two sources already read, to be joined into one before generation."""

    base: SourceContent
    addition: SourceContent


@router.post("/merge")
async def merge_preview(payload: SourceMergeRequest) -> dict:
    """Join two SourceContent trees by page topic/slug (see merge_sources)."""
    merged = await asyncio.to_thread(merge_sources, payload.base, payload.addition)
    label = merged.source_ref or payload.base.source_ref
    return source_preview_payload(
        url=label,
        final_url=label,
        source_content=merged,
        brand_candidate=None,
        image_candidates=[],
    )
