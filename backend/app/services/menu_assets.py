"""Classify and route authentic restaurant menu images; no I/O or model calls.

OCR is evidence of purpose only. It never becomes a structured menu or an
assertion that a photographed price is current. Unknown images stay unknown.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Literal

from app.models.content_blocks import ImageMetadata, PagePlan, PosterBlock, SourceContent
from app.models.industry import PageScaffold
from app.services.image_urls import image_identity
from app.services.source_injection import companion_insert_index, hero_insert_index, max_items, source_pages
from app.services.source_path import normalize_source_slug

logger = logging.getLogger(__name__)

_MENU = re.compile(r"\b(?:menus?|menu board|price list|senarai harga|餐单|菜单)\b", re.I)
_PRICE = re.compile(r"(?:\b(?:RM|MYR|USD|SGD|IDR|THB|EUR|GBP)\s*\d+(?:[.,]\d+)?|[$€£]\s*\d+(?:[.,]\d+)?)", re.I)
_PROMOTION = re.compile(r"\b(?:promotion|promo|limited time|valid until|offer ends|expired|special offer|ticket|concert|event|voucher|coupon)\b", re.I)
_VERSION = re.compile(r"\b(?:old menu|previous menu|updated menu|new menu|revised menu|version\s*\d+)\b", re.I)
_NON_MENU = re.compile(r"\b(?:ticket|concert|invoice|receipt|subtotal|total|parking|admission)\b", re.I)
# Only source labels become category headings; OCR never manufactures one.
_CATEGORY = re.compile(r"^(?:(?:food|drinks?|beverages?|desserts?|breakfast|lunch|dinner|set meals?)\s*(?:menu)?|menu\s*[-:]?\s*(?:food|drinks?|beverages?|desserts?|breakfast|lunch|dinner|set meals?))$", re.I)


# The industries whose menus are replayed as menu sheets. One answer, asked by
# every generation step that handles menus and by the page gate that offers a
# Menu page — a Menu page this pipeline would never fill is a hollow page.
MENU_INDUSTRIES = frozenset({"restaurant"})


def serves_menus(industry: str | None) -> bool:
    return industry in MENU_INDUSTRIES


@dataclass(frozen=True)
class MenuReading:
    purpose: Literal["menu", "menu_review"]
    evidence: str
    heading: str


def classify_menu_image(meta: ImageMetadata) -> None:
    """Stamp strong menu evidence, reserving questionable offers for review."""
    reading = read_menu_image(meta)
    meta.content_purpose = reading.purpose if reading else None
    meta.menu_evidence = reading.evidence if reading else ""
    meta.menu_heading = reading.heading if reading else ""


def read_menu_image(meta: ImageMetadata) -> MenuReading | None:
    """What an image's labels and pixels say about it being a menu. Pure."""
    if meta.intent == "logo" or meta.role in {"portrait", "decoration"}:
        return None
    labels = " ".join((meta.alt, meta.context_heading, meta.caption, meta.vision_caption or ""))[:1200]
    pixels = meta.ocr_text[:2000]
    # A menu label is direct source evidence; a plain price isn't a menu.
    explicit = bool(_MENU.search(labels))
    pixel_menu = bool(_MENU.search(pixels)) and bool(_PRICE.search(pixels))
    # A named dish/price row also supports a single priced food image. Exclude
    # event/offer language before accepting, and never export these OCR words.
    priced_lines = sum(bool(_PRICE.search(line)) and bool(re.search(r"[A-Za-z\u3400-\u9fff]{3,}", _PRICE.sub("", line)))
                       for line in pixels.splitlines())
    if not explicit and not pixel_menu and (priced_lines < 1 or _NON_MENU.search(pixels)):
        return None
    # A measured text-free food photo merely captioned "from our menu" isn't
    # a menu sheet. Explicitly labelled menu scans still work with OCR disabled.
    if (meta.ocr_has_text is False or meta.vision_has_text is False) and not pixels:
        return None
    evidence = "source menu label" if explicit else "OCR menu and price evidence"
    questionable = bool(_PROMOTION.search(labels + " " + pixels) or _VERSION.search(labels))
    return MenuReading(
        purpose="menu_review" if questionable else "menu",
        evidence="promotion or version requires review" if questionable else evidence,
        heading=next((label.strip() for label in (meta.context_heading, meta.alt)
                      if _CATEGORY.fullmatch(label.strip())), "Menu"),
    )


def assess_menu_assets(source: SourceContent, pool: list[ImageMetadata] | None = None) -> list[ImageMetadata]:
    """Assess once per URL, propagating pixel evidence to page-local copies."""
    metadata = [
        *(pool or []),
        *(meta for page in source_pages(source) for meta in page.image_metadata),
    ]
    # Pixel evidence can disprove an earlier caption-only assessment. Clear the
    # previous reservation so an ordinary food photo becomes eligible again.
    for meta in metadata:
        meta.content_purpose = None
        meta.menu_heading = ""
        meta.menu_evidence = ""
    measured = {meta.url: meta for meta in pool or []}
    assets: dict[str, ImageMetadata] = {}
    for page in source_pages(source):
        for original in page.image_metadata:
            meta = measured.get(original.url, original)
            # Keep page-local source labels when the shared pool deduped a URL.
            candidate = meta.model_copy(update={
                "alt": original.alt or meta.alt,
                "context_heading": original.context_heading or meta.context_heading,
                "caption": original.caption or meta.caption,
            })
            classify_menu_image(candidate)
            if candidate.content_purpose:
                for target in (original, meta):
                    target.content_purpose = candidate.content_purpose
                    target.menu_heading = candidate.menu_heading
                    target.menu_evidence = candidate.menu_evidence
                identity = image_identity(meta)
                previous = assets.get(identity)
                if previous is None or meta.content_purpose == "menu_review":
                    assets[identity] = meta
    # Copies with a different CDN URL but identical bytes share the reservation.
    for meta in metadata:
        asset = assets.get(image_identity(meta))
        if asset:
            meta.content_purpose = asset.content_purpose
            meta.menu_heading = asset.menu_heading
            meta.menu_evidence = asset.menu_evidence
    return list(assets.values())


def _destination(meta: ImageMetadata, pages: list[PagePlan], scaffolds: list[PageScaffold], owners: dict[str, str]) -> PagePlan | None:
    menu_slugs = {s.slug for s in scaffolds if s.page_type == "menu"}
    menus = [page for page in pages if page.slug in menu_slugs
             or (not scaffolds and (page.slug == "menu" or any(b.kind == "menu" for b in page.blocks)))]
    owner = owners.get(meta.url)
    # Respect the selected category page that originally owned the image.
    if menu_slugs or menus:
        return (next((page for page in menus if normalize_source_slug(page.slug) == owner), None)
                or next((page for page in menus if page.slug.strip("/").lower() == "menu"), None)
                or (menus[0] if len(menus) == 1 and len(menu_slugs) <= 1 else None))
    return next((page for page in pages if page.is_homepage), None)


def inject_menu_assets(pages: list[PagePlan], source: SourceContent, assets: list[ImageMetadata], scaffolds: list[PageScaffold]) -> None:
    """Reserve assets globally and reuse whole-image sections on their destination."""
    from app.services.legible_images import poster_item

    reserved = {meta.url for meta in assets}
    identities = {image_identity(meta) for meta in assets}
    # Remove generic placements before binding. Both exact URLs and content
    # hashes are used so a CDN alias cannot replay the same menu elsewhere.
    all_meta = {meta.url: meta for page in source_pages(source) for meta in page.image_metadata}
    def is_reserved(url: str | None) -> bool:
        return bool(url and (url in reserved or (url in all_meta and image_identity(all_meta[url]) in identities)))

    for page in pages:
        kept = []
        for block in page.blocks:
            if getattr(block, "kind", None) in {"gallery", "poster"}:
                block.items = [item for item in block.items if not is_reserved(getattr(item, "image_url", None))]
                if not block.items:
                    continue
            else:
                for holder in (block, *(getattr(block, "items", None) or [])):
                    if is_reserved(getattr(holder, "image_url", None)):
                        holder.image_url = None
                        if hasattr(holder, "image_ref"):
                            holder.image_ref = None
            kept.append(block)
        page.blocks = kept

    owners = {meta.url: normalize_source_slug(page.url_path)
              for page in source_pages(source) for meta in page.image_metadata}
    groups: dict[tuple[str, str], list[ImageMetadata]] = {}
    for meta in assets:
        if meta.content_purpose == "menu_review":
            logger.warning("Menu image withheld for review (%s)", meta.menu_evidence)
            continue
        destination = _destination(meta, pages, scaffolds, owners)
        if destination:
            groups.setdefault((destination.slug, meta.menu_heading or "Menu"), []).append(meta)
    by_slug = {page.slug: page for page in pages}
    for (slug, heading), metas in groups.items():
        page = by_slug[slug]
        at = companion_insert_index(page, ("menu",), fallback=lambda p: companion_insert_index(p, ("about",), fallback=hero_insert_index))
        cap = max_items(PosterBlock)
        blocks = [PosterBlock(heading=heading, items=[poster_item(meta, heading) for meta in metas[i:i + cap]])
                  for i in range(0, len(metas), cap)]
        page.blocks[at:at] = blocks


def menu_review_warnings(assets: list[ImageMetadata]) -> list[str]:
    """Owner-facing review notice; omit URLs and OCR implementation details."""
    count = sum(meta.content_purpose == "menu_review" for meta in assets)
    return ([f"{count} menu image(s) were withheld because they appear to be promotions or revised versions. "
             "Confirm the current menu in your source before regenerating."] if count else [])
