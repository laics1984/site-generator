"""The shape every source hands the frontend before any LLM work runs.

Three readers produce it — the crawler, the document parser and the Facebook
reader — and the preview UI hydrates from it without knowing which one ran. That
only works if there is exactly one definition of the shape.

There wasn't. ``crawl_orchestrator._result_to_payload`` defined it,
``routers/document.py`` hand-mirrored it (down to a second copy of
``ImageCandidate`` named ``_ImageCandidate``, with a comment saying so), and the
Facebook reader added a third. A field added to one of them silently didn't
exist on the others.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

from app.models.content_blocks import SourceContent


@dataclass
class ImageCandidate:
    """One image a source offered, with an intent guess.

    Shared by every reader: the crawler fills the render-evidence fields from a
    real layout, while the document and Facebook readers leave `role` at
    "unknown" and let the matcher rely on `intent` alone.
    """

    url: str
    alt: str
    width: int | None = None
    height: int | None = None
    intent: str = "generic"  # 'hero' | 'about' | 'logo' | 'generic'
    # Visual role measured from render evidence (image_evidence.classify_role).
    # 'unknown' when the page came through the httpx fast path (no stamps), and
    # for document uploads and Facebook reads, where no layout was rendered.
    role: str = "unknown"
    evidence: Any | None = None
    # How the source used this image: 'css_background' when it came from a CSS
    # background-image (stamped attr, inline style or <style> block), 'inline'
    # for <img>/og:image. Downstream, css_background images are kept out of
    # side/featured slots and pinned to full-bleed background slots.
    source_usage: str = "inline"
    # Nearest preceding heading text — ties the image back to the source section
    # it illustrated. Feeds the planner prompt (image_ref binding) and the
    # matcher's lexical scoring.
    context_heading: str = ""
    # <figcaption> text when the image sits inside a <figure>.
    caption: str = ""


def candidates_from_source(source: SourceContent) -> list[ImageCandidate]:
    """Flatten a source's per-page image metadata into preview candidates.

    For readers whose only record of an image is the metadata they wrote onto
    the source itself — the document parser and the paste reader. The crawler
    measures a live layout and builds richer candidates as it goes, so it never
    needs this.
    """
    return [
        ImageCandidate(
            url=meta.url,
            alt=meta.alt,
            width=meta.width,
            height=meta.height,
            intent=meta.intent,
            role=meta.role,
            source_usage=meta.source_usage,
            context_heading=meta.context_heading,
            caption=meta.caption,
        )
        for page in [source, *source.discovered_pages]
        for meta in page.image_metadata
    ]


def source_preview_payload(
    *,
    url: str,
    final_url: str,
    source_content: SourceContent,
    brand_candidate: Any | None,
    image_candidates: Sequence[ImageCandidate],
    fetched_at: float | None = None,
    unvisited_urls: Sequence[str] = (),
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the canonical preview payload.

    `unvisited_urls` is crawl-only — a document or a Facebook Page has no
    frontier, so it defaults empty and the UI's "crawl N more" affordance stays
    hidden without either caller having to think about it. `extra` carries
    reader-specific keys (the Facebook facts) additively, so adding one can
    never change the shared shape.
    """
    unvisited = list(unvisited_urls)
    payload: dict[str, Any] = {
        "url": url,
        "final_url": final_url,
        "source_content": source_content.model_dump(mode="json"),
        "brand_candidate": (
            brand_candidate.model_dump(mode="json") if brand_candidate else None
        ),
        "image_candidates": [asdict(c) for c in image_candidates],
        "fetched_at": fetched_at,
        "discovered_count": len(source_content.discovered_pages),
        "unvisited_urls": unvisited,
        "unvisited_count": len(unvisited),
    }
    if extra:
        payload.update(extra)
    return payload
