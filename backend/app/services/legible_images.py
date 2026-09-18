"""
Images whose message is in their pixels, placed whole.

watr.org.my shipped four of them wrong at once. Its footer DuitNow donation code
was stretched full-bleed behind an About headline and cropped 4:3 into another
page's About split; an Alpha course poster became a 3:4-cropped editorial hero
and the page's og:image; a volunteering slide carrying its own QR code became a
split hero. Each passed its slot's gate, because the gates only asked whether
OUR copy would collide with words in the picture — and every inline slot in the
catalog crops.

`image_match.hides_legible_content` now keeps such an image out of every slot.
This module is the other half of that rule: an image that may go nowhere must
still go somewhere, and the only faithful somewhere is a section that shows all
of it. Both sections are replayed, never narrated (DETERMINISTIC_SECTION_KINDS):

- ``qr`` — a code on its own is an ask. Placed with the page's other asks (after
  its contact section, else just before the closing CTA) and captioned with what
  scanning does, read off the payload by `qr_codes.purpose_of`.
- ``poster`` — words that are the content: posters, flyers, slides, a photo with
  its title burned in. Placed like the other replayed content, after the hero,
  under the heading the source showed it beneath. A code inside a poster belongs
  to the poster, and lends it a tap-through when it encodes a link.

Which page: the one the source showed it on. An image on most of the site is the
template's — watr's code sits in the footer of all six pages — so it is placed
once, on the homepage, the rule `routers.generate._inject_videos` already
follows for a sitewide promo reel.
"""

from __future__ import annotations

from app.models.content_blocks import (
    ImageMetadata,
    PagePlan,
    PosterBlock,
    PosterItem,
    QrBlock,
    QrItem,
    SourceContent,
)
from app.services.image_match import must_show_whole, shows_words
from app.services.qr_codes import QrPurpose, purpose_of
from app.services.image_urls import image_identity
from app.services.source_injection import (
    accumulate_by_slug,
    closing_insert_index,
    companion_insert_index,
    group_by_heading,
    hero_insert_index,
    max_items,
    pages_by_slug,
    sitewide_across_slugs,
)
from app.services.video_embed import is_player_thumbnail

# Roles that are never placed here. A portrait belongs to its person, a
# decoration is furniture, and a gallery cell is replayed by its own rack. A logo
# is the brand mark — except when it decodes as a code: the flat-graphic screen
# (image_graphics) rightly calls a transparent QR code a graphic, not a photo.
_UNPLACEABLE_ROLES = frozenset({"portrait", "decoration", "gallery"})

# A code sits after the section that states the same ways to reach the business.
_QR_COMPANION_KINDS = ("contact",)

_MAX_QR_ITEMS = max_items(QrBlock)
_MAX_POSTER_ITEMS = max_items(PosterBlock)
# Distinct poster groups on one page — past two headed groups it is a noticeboard
# archive, which a generated page does not replay wholesale.
_MAX_POSTER_BLOCKS = 2


def inject_legible_images(
    pages: list[PagePlan], source: SourceContent, pool: list[ImageMetadata]
) -> None:
    """Place every screened poster and QR code whole, on its source page. In place.

    ``pool`` is the resolver pool the pixel screens stamped. Each crawled page
    carries its OWN metadata objects, and the pool de-duplicates by URL, so a
    page's copy of a shared image is not the stamped one — every reading is
    looked up in the pool by URL.
    """
    legible = {meta.url: meta for meta in pool if _placeable(meta)}
    if not legible:
        return

    def on_page(page: SourceContent) -> list[ImageMetadata]:
        return [legible[m.url] for m in page.image_metadata if m.url in legible]

    sitewide = sitewide_across_slugs(source, lambda page: (m.url for m in on_page(page)))
    generated = pages_by_slug(pages)
    for slug, metas in accumulate_by_slug(source, on_page).items():
        page = generated.get(slug)
        if page is None:
            continue
        exclude = _placed_urls(page) | (set() if page.is_homepage else sitewide)
        _place(page, metas, _identities(metas, exclude))


def _placeable(meta: ImageMetadata) -> bool:
    if not must_show_whole(meta) or meta.role in _UNPLACEABLE_ROLES:
        return False
    if meta.role == "logo" and not meta.qr_payload:
        return False
    # A video's still carries its title burned in, but it is the video's poster
    # frame: shown alone, it is a player that doesn't play.
    return not is_player_thumbnail(meta.url)


def _is_code(meta: ImageMetadata) -> bool:
    """The code IS the picture. A code inside a poster is part of the poster."""
    return bool(meta.qr_payload) and not shows_words(meta)


def _placed_urls(page: PagePlan) -> set[str]:
    """Source images another replay already put on this page (a picture rack, a
    record page's own photos) — one image is shown once."""
    holders = [
        holder
        for block in page.blocks
        for holder in (block, *(getattr(block, "items", None) or []))
    ]
    return {url for holder in holders if (url := getattr(holder, "image_url", None))}


def _identities(metas: list[ImageMetadata], urls: set[str]) -> set[str]:
    """`urls` as grouping keys: a URL already on the page excludes the picture
    behind it under every URL it is served from, not just that spelling."""
    return urls | {image_identity(meta) for meta in metas if meta.url in urls}


def _place(page: PagePlan, metas: list[ImageMetadata], exclude: set[str]) -> None:
    posters = [
        PosterBlock(heading=heading, items=[_poster_item(meta, heading) for meta in group])
        for heading, group in group_by_heading(
            (meta for meta in metas if not _is_code(meta)),
            key_of=image_identity,
            heading_of=lambda meta: meta.context_heading,
            exclude=exclude,
            max_groups=_MAX_POSTER_BLOCKS,
            max_items=_MAX_POSTER_ITEMS,
        )
    ]
    at = hero_insert_index(page)
    page.blocks[at:at] = posters

    # One section for every code on the page. Its heading says what the codes
    # DO, so the heading each one happened to sit under is not consulted.
    codes = [
        _qr_item(meta)
        for _heading, group in group_by_heading(
            (meta for meta in metas if _is_code(meta)),
            key_of=image_identity,
            heading_of=lambda _meta: "",
            exclude=exclude,
            max_groups=1,
            max_items=_MAX_QR_ITEMS,
        )
        for meta in group
    ]
    if codes:
        titles = {item.title for item in codes}
        # Several codes doing different things fall back to the block's default.
        block = QrBlock(heading=titles.pop() if len(titles) == 1 else "", items=codes)
        at = companion_insert_index(page, _QR_COMPANION_KINDS, fallback=closing_insert_index)
        page.blocks.insert(at, block)


def _action(purpose: QrPurpose | None) -> dict[str, str | None]:
    if purpose is None:
        return {}
    return {"action_label": purpose.action_label, "action_href": purpose.action_href}


def _qr_item(meta: ImageMetadata) -> QrItem:
    purpose = purpose_of(meta.qr_payload or "")
    return QrItem(
        image_url=meta.url,
        title=purpose.title,
        description=purpose.description,
        **_action(purpose),
    )


def _poster_item(meta: ImageMetadata, heading: str) -> PosterItem:
    purpose = purpose_of(meta.qr_payload) if meta.qr_payload else None
    return PosterItem(
        image_url=meta.url,
        # A poster's content is its words, so an empty alt would hide the whole
        # section from a screen reader. The source's own labels come first.
        alt=meta.alt or meta.caption or heading,
        caption=meta.caption or (purpose.description if purpose else None),
        **_action(purpose),
    )
