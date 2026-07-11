"""
Resolve LLM-emitted ``image_ref`` indexes to real scraped photo URLs.

The planner prompt shows each page a numbered list of its own content-grade
photos (source_router.promptable_images). The LLM binds a section to a photo
by echoing the number in the block's ``image_ref``. This pass — run after
scaffold alignment, before schema building — recomputes the SAME numbered
list per page and turns each valid ref into ``image_url``/``image_alt`` on
the block, which schema_builder's slot resolution then treats as an already-
chosen source image.

Rules:
- A ref outside the list is dropped (the block falls back to image_query).
- A ref used twice on one page binds only its first use.
- Returns the set of bound URLs so the caller can ImageResolver.mark_used()
  them and keep slot resolution from re-picking the same photo.
"""

from __future__ import annotations

import logging

from app.models.content_blocks import ImageMetadata, PagePlan, SourceContent
from app.services.source_router import promptable_images

logger = logging.getLogger(__name__)


def _bind_one(obj: object, images: list[ImageMetadata], used: set[str]) -> str | None:
    """Bind obj.image_ref → obj.image_url/image_alt. Returns the URL if bound."""
    ref = getattr(obj, "image_ref", None)
    if ref is None:
        return None
    if not (0 <= ref < len(images)):
        obj.image_ref = None  # type: ignore[attr-defined]
        return None
    meta = images[ref]
    if meta.url in used:
        # Second use of the same photo on this page — fall back to image_query.
        obj.image_ref = None  # type: ignore[attr-defined]
        return None
    obj.image_url = meta.url  # type: ignore[attr-defined]
    if hasattr(obj, "image_alt") and not getattr(obj, "image_alt", None):
        obj.image_alt = meta.alt or meta.caption or meta.context_heading or None  # type: ignore[attr-defined]
    used.add(meta.url)
    return meta.url


def bind_image_refs(
    pages: list[PagePlan],
    source_map: dict[str, SourceContent],
) -> set[str]:
    """Resolve every block/item image_ref across ``pages``. Mutates in place.

    ``source_map`` is the scaffold-slug → SourceContent routing the planner
    prompt used (source_router.match_scaffolds_to_pages), so refs are resolved
    against exactly the image list the LLM saw.
    """
    bound: set[str] = set()
    for page in pages:
        source = source_map.get(page.slug)
        if source is None:
            continue
        images = promptable_images(source)
        if not images:
            continue
        page_used: set[str] = set()
        for block in page.blocks:
            url = _bind_one(block, images, page_used)
            if url:
                bound.add(url)
            for item in getattr(block, "items", None) or []:
                url = _bind_one(item, images, page_used)
                if url:
                    bound.add(url)
    if bound:
        logger.info("Bound %d scraped photos via image_ref", len(bound))
    return bound
