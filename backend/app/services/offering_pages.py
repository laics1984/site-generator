"""The Programmes / Services page a single-page source earns from its offerings.

`facebook_knowledge` finds what a Page's business offers, each offering
pointing at the posts that state it (`SourceContent.page_knowledge`). This
module answers the three questions that turns into a page, in one place so
the gate and the filler can never disagree:

- **Is a page earned?** At least `MIN_OFFERINGS` validated offerings
  (`earned_offerings`). One offering is a sentence on the homepage; two are a
  list worth a page. An offering stays a card on that page unless it has a
  page's worth of evidence of its own — `CHILD_MIN_OWN_CHARS` characters no
  other offering shares, and a photo of its own (`child_worthy`).
- **Which scaffolds?** `offering_scaffolds`: "Programmes" for childcare,
  "Services" otherwise, plus the child pages; asked by
  `page_inference.evidence_pages`, so they arrive pre-ticked like the earned
  Gallery and Menu pages.
- **Planned from what?** `offering_source`: a private planning view — the
  `landing_source.homepage_source` precedent — restricted to the page's own
  offerings: their verbatim bodies as the text, the offerings as one
  `card_kind="offerings"` section tree, and only their own photos. Routed by
  `source_router.match_scaffolds_to_pages` where it would otherwise hand the
  page the whole digest. `discovered_pages` stays empty, so nothing that
  assumes a Facebook source has none changes.

A card the model wrote for an offering shows that offering's own photo
(`pin_offering_photos`): the pass writes the card's `image_ref` before the
binder runs, so the photo is screened and bound like any the model chose.
"""

from __future__ import annotations

import re

from app.models.content_blocks import (
    ImageMetadata, PagePlan, SectionCandidate, SourceCard, SourceContent,
)
from app.models.industry import IndustryCategory, PageScaffold
from app.models.page_knowledge import Offering
from app.services.image_urls import photo_identity

MIN_OFFERINGS = 2
CHILD_MIN_OWN_CHARS = 300


def picture_id(url: str) -> str:
    """The identity a picture is stored under (`PageKnowledge.photo_ids`)."""
    return photo_identity(url) or url


def _slugify(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-") or "offering"


def offering_photos(source: SourceContent, offering: Offering) -> list[ImageMetadata]:
    """The offering's own photos still in the source's live pool, best first."""
    from app.services.source_router import is_content_photo

    pool = {picture_id(meta.url): meta for meta in source.image_metadata if is_content_photo(meta)}
    return [pool[pid] for pid in offering.photo_ids if pid in pool]


def earned_offerings(source: SourceContent) -> list[Offering]:
    """The offerings that earn a page, or [] when fewer than MIN_OFFERINGS."""
    knowledge = source.page_knowledge
    offerings = list(knowledge.offerings) if knowledge else []
    return offerings if len(offerings) >= MIN_OFFERINGS else []


def child_worthy(source: SourceContent, offering: Offering) -> bool:
    return offering.own_chars >= CHILD_MIN_OWN_CHARS and bool(offering_photos(source, offering))


def _parent_slug(industry: IndustryCategory) -> tuple[str, str]:
    return ("programmes", "Programmes") if industry == "childcare" else ("services", "Services")


def offering_scaffolds(source: SourceContent, industry: IndustryCategory) -> list[PageScaffold]:
    offerings = earned_offerings(source)
    if not offerings:
        return []
    slug, title = _parent_slug(industry)
    parent = PageScaffold(
        page_type="services", slug=slug, title=title,
        sections=["hero", "services", "cta"],
        rationale="What the business offers, as its own posts state it.",
        from_source=True,
    )
    children: list[PageScaffold] = []
    taken: set[str] = {slug}
    for offering in offerings:
        if not child_worthy(source, offering):
            continue
        child = f"{slug}/{_slugify(offering.name)}"
        if child in taken:
            continue
        taken.add(child)
        children.append(PageScaffold(
            page_type="services", slug=child, title=offering.name,
            sections=["hero", "about", "cta"], parent_slug=slug,
            rationale="An offering with a page's worth of its own posts.",
            from_source=True,
        ))
    return [parent, *children]


def _card(source: SourceContent, offering: Offering) -> SourceCard:
    photos = offering_photos(source, offering)
    return SourceCard(
        title=offering.name, body=offering.body, link=offering.link,
        image_url=photos[0].url if photos else None,
        meta=[offering.published_at[:10]] if offering.published_at else [],
    )


def _view(source: SourceContent, title: str, offerings: list[Offering],
          sections: list[SectionCandidate]) -> SourceContent:
    photos: dict[str, ImageMetadata] = {}
    for offering in offerings:
        for meta in offering_photos(source, offering):
            photos.setdefault(meta.url, meta)
    return source.model_copy(update={
        "title": title,
        "raw_text": "\n\n".join(f"{o.name}:\n{o.body}" for o in offerings),
        "headings": [o.name for o in offerings],
        "section_candidates": sections,
        "image_metadata": list(photos.values()),
        "images": list(photos),
    })


def offering_source(source: SourceContent, scaffold: PageScaffold) -> SourceContent | None:
    """The planning view for an earned offerings page, or None for any other
    scaffold — the caller then keeps whatever it would have routed."""
    offerings = earned_offerings(source)
    if not offerings or scaffold.page_type != "services":
        return None
    parent_slug = scaffold.parent_slug or scaffold.slug
    if scaffold.parent_slug is None:
        if scaffold.slug not in {"programmes", "services"}:
            return None
        cards = [_card(source, o) for o in offerings]
        tree = [SectionCandidate(heading=scaffold.title, level=2, card_kind="offerings", cards=cards)]
        return _view(source, scaffold.title, offerings, tree)
    match = next((o for o in offerings if f"{parent_slug}/{_slugify(o.name)}" == scaffold.slug), None)
    if match is None:
        return None
    tree = [SectionCandidate(heading=match.name, level=2, prose=match.body)]
    return _view(source, match.name, [match], tree)


def _normal(text: str) -> str:
    return " ".join(re.findall(r"\w+", text.casefold()))


def pin_offering_photos(pages: list[PagePlan], source_map: dict[str, SourceContent]) -> int:
    """Point each offering card at the best photo from its own posts.

    A services/features card whose title names an offering takes the first of
    that offering's photos the page's prompt numbered, ahead of whatever the
    model picked — a card about outdoor learning shows the outdoor post, not
    the photo the model happened to bind. One photo per card on a page; a card
    whose offering has no numbered photo keeps the model's choice. Returns the
    number of cards pinned."""
    from app.services.source_router import promptable_images

    pinned = 0
    for page in pages:
        source = source_map.get(page.slug)
        knowledge = source.page_knowledge if source is not None else None
        if not knowledge or not knowledge.offerings:
            continue
        numbered = {picture_id(meta.url): i for i, meta in enumerate(promptable_images(source))}
        by_name = {_normal(o.name): o for o in knowledge.offerings}
        taken: set[int] = set()
        for block in page.blocks:
            if getattr(block, "kind", None) not in {"services", "features"}:
                continue
            for item in block.items:
                offering = by_name.get(_normal(item.title))
                if offering is None:
                    continue
                ref = next((numbered[pid] for pid in offering.photo_ids
                            if pid in numbered and numbered[pid] not in taken), None)
                if ref is None:
                    continue
                item.image_ref, item.image_url = ref, None
                taken.add(ref)
                pinned += 1
    return pinned
