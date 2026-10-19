"""A card that names one of the business's own things shows that thing.

`facebook_knowledge` finds what a Page's business offers and the facilities it
has (`SourceContent.page_knowledge`), each with the photos its own posts show,
by identity. A features/services card or an about section whose title NAMES
one of them is a statement about that specific thing, so its picture is
decided here, in two passes either side of `image_refs.bind_image_refs`:

- **`pin_knowledge_photos`** (before the binder): the card's `image_ref` points
  at the first of the thing's own photos the page's prompt numbered, ahead of
  whatever the model picked. Written as a ref rather than a URL, so the photo
  is screened (`verify_many`) and bound (`_unfit_for_kind`) like any the model
  chose — and a photo the binder refuses is refused here too.
- **`withhold_stock_photos`** (after the binder): a card naming a FACILITY that
  still has no source photo bound loses its stock query, and a card item is
  marked `source_photo_only`, which `section_content._item_image` reads as "no
  image here" instead of searching stock on the card's title. A stock playground under "Our playground" is a false claim about the
  premises — the `prompts.py` rule, "stock is illustrative, never evidence of
  … facilities", enforced where the model can't be trusted to apply it. An
  offering is different: it is an activity or a service, which a stock photo
  can illustrate honestly, so it keeps the title fallback.

The same rule `_drop_unbound_gallery_items` applies to gallery tiles, and
suspended in the same mode: under `stock_images_only` there is no source
photography anywhere on the site, so a stock picture claims nothing.

**A title names a thing when the thing's name appears in it as whole words**
(`named_in`). Knowledge names are verbatim spans of the source ("outdoor
playground"); card titles are the model's ("Our Outdoor Playground"), so an
exact match would miss most of them. The longest name wins, so a card titled
"Infant care room" is the room, not the care.
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from app.models.content_blocks import AboutBlock, PagePlan, SourceContent
from app.models.page_knowledge import PageKnowledge
from app.services.offering_pages import picture_id

# The block kinds whose cards (or, for about, the block itself) present one
# thing of the business under its own title.
_CARD_KINDS = frozenset({"features", "services"})


def _words(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r"\w+", text.casefold()))


def named_in(title: str, names: list[str]) -> str | None:
    """The longest of `names` whose words appear, in order and adjacent, in
    `title` — or None when the title names none of them."""
    words = _words(title or "")
    best: str | None = None
    best_len = 0
    for name in names:
        needle = _words(name)
        n = len(needle)
        if not n or n <= best_len:
            continue
        if any(words[i:i + n] == needle for i in range(len(words) - n + 1)):
            best, best_len = name, n
    return best


def _cards(page: PagePlan) -> Iterator[tuple[object, str]]:
    """(holder, title) for every card on a page that can present one thing:
    each features/services item, and an about section by its heading."""
    for block in page.blocks:
        if isinstance(block, AboutBlock):
            yield block, block.heading
        elif getattr(block, "kind", None) in _CARD_KINDS:
            for item in block.items:
                yield item, item.title


def _photos_by_name(knowledge: PageKnowledge) -> dict[str, list[str]]:
    """Every named thing's photo ids. An offering and a facility of one name
    are one thing; the offering's posts are listed first."""
    named: dict[str, list[str]] = {}
    for thing in (*knowledge.offerings, *knowledge.facilities):
        named.setdefault(thing.name, []).extend(thing.photo_ids)
    return named


def pin_knowledge_photos(pages: list[PagePlan], source_map: dict[str, SourceContent]) -> int:
    """Point each card that names an offering or a facility at the best photo
    from that thing's own posts.

    The card takes the first of those photos the page's prompt numbered, ahead
    of whatever the model picked — a card about outdoor learning shows the
    outdoor post, not the photo the model happened to bind. One photo per card
    on a page; a card whose thing has no numbered photo keeps the model's
    choice. Returns the number of cards pinned."""
    from app.services.source_router import promptable_images

    pinned = 0
    for page in pages:
        source = source_map.get(page.slug)
        knowledge = source.page_knowledge if source is not None else None
        if not knowledge:
            continue
        photos = _photos_by_name(knowledge)
        if not photos:
            continue
        numbered = {picture_id(meta.url): i for i, meta in enumerate(promptable_images(source))}
        taken: set[int] = set()
        for card, title in _cards(page):
            name = named_in(title, list(photos))
            if name is None:
                continue
            ref = next((numbered[pid] for pid in photos[name]
                        if pid in numbered and numbered[pid] not in taken), None)
            if ref is None:
                continue
            card.image_ref, card.image_url = ref, None  # type: ignore[attr-defined]
            taken.add(ref)
            pinned += 1
    return pinned


def withhold_stock_photos(pages: list[PagePlan], source_map: dict[str, SourceContent]) -> int:
    """Leave every card that names a facility and holds no source photo
    imageless. Runs after `bind_image_refs`, the first point at which "no
    source photo is bound" is true rather than a guess. Returns the number of
    cards withheld."""
    withheld = 0
    for page in pages:
        source = source_map.get(page.slug)
        knowledge = source.page_knowledge if source is not None else None
        facilities = [f.name for f in knowledge.facilities] if knowledge else []
        if not facilities:
            continue
        for card, title in _cards(page):
            if getattr(card, "image_url", None) or named_in(title, facilities) is None:
                continue
            card.image_query = None  # type: ignore[attr-defined]
            # An about section with neither a query nor a URL already has no
            # image value (`section_content._featured_image`); only a card's
            # title fallback needs telling.
            if not isinstance(card, AboutBlock):
                card.source_photo_only = True  # type: ignore[attr-defined]
            withheld += 1
    return withheld
