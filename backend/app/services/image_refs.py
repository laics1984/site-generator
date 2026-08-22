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
from app.services.image_match import _UNPINNABLE_VISION_KINDS
from app.services.source_router import promptable_images

logger = logging.getLogger(__name__)

# Sections that are ABOUT individual people: a head-and-shoulders portrait is
# the correct photo there. Gallery counts too — its whole job is replaying the
# source's own photos, and a source that shows faces should keep showing them.
_PORTRAIT_OK_KINDS = frozenset({"team", "testimonials", "gallery"})

# Sections whose image slots are decorative full-bleed backdrops rather than a
# photo OF the section's subject, so a source's own background art is fair game.
_BACKGROUND_OK_KINDS = frozenset({"cta"})


def _unfit_for_kind(meta: ImageMetadata, kind: str, *, layout: str | None) -> str | None:
    """Why `meta` must not be bound to a `kind` section, or None when it fits.

    The planner sees each photo's `role` and is told to respect it, but an LLM
    binding a plausible-sounding ref is exactly the failure this pass exists to
    contain — the classic one being a committee/team headshot pinned onto every
    services card, which renders four strangers' faces as if they were the
    services. The rules here are the same ones the resolver applies to its own
    pins (media._unfit_for_featured_pin); enforcing them at bind time covers the
    per-ITEM refs too, which never reach the resolver: a bound item URL goes
    straight into the card's image slot.
    """
    portrait = meta.role == "portrait" or meta.vision_portrait is True
    if portrait and kind not in _PORTRAIT_OK_KINDS:
        return "portrait"
    background = meta.role == "background" or meta.source_usage == "css_background"
    if background and not (
        kind in _BACKGROUND_OK_KINDS or (kind == "hero" and layout == "background")
    ):
        return "decorative background"
    if meta.vision_kind in _UNPINNABLE_VISION_KINDS:
        return f"vision_kind={meta.vision_kind}"
    return None


def _bind_one(
    obj: object,
    images: list[ImageMetadata],
    used: set[str],
    *,
    kind: str,
    layout: str | None = None,
) -> str | None:
    """Bind obj.image_ref → obj.image_url/image_alt. Returns the URL if bound.

    ``kind``/``layout`` come from the OWNING block (items inherit them), and
    gate which photos may fill that kind of slot — see `_unfit_for_kind`.
    """
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
    unfit = _unfit_for_kind(meta, kind, layout=layout)
    if unfit is not None:
        # Drop the ref so the slot resolves its image_query instead: a real
        # stock photo of the subject beats the wrong real photo.
        logger.debug("Dropped %s image_ref %d (%s): %s", kind, ref, unfit, meta.url)
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
        # Portraits already rendering as a person's photo on this page are
        # spoken for — a ref landing on one shows the same face twice (a
        # person's own profile page, where it's the only photo there is).
        page_used: set[str] = {
            person.photo_url
            for block in page.blocks
            if getattr(block, "kind", None) in ("team", "profile")
            # A profile block IS the person; a team block lists them.
            for person in (
                [block] if getattr(block, "kind", None) == "profile" else block.members
            )
            if person.photo_url
        }
        for block in page.blocks:
            kind = getattr(block, "kind", "") or ""
            layout = getattr(block, "layout", None)
            url = _bind_one(block, images, page_used, kind=kind, layout=layout)
            if url:
                bound.add(url)
            for item in getattr(block, "items", None) or []:
                url = _bind_one(item, images, page_used, kind=kind, layout=layout)
                if url:
                    bound.add(url)
    if bound:
        logger.info("Bound %d scraped photos via image_ref", len(bound))
    return bound
