"""
Recover a source page's own section tree from its markup.

Consumed by ``scraper._parse_rendered_html``, alongside the nav/profile/document
extractors. Where those hunt for one *kind* of thing across the whole page, this
one asks the prior question — how does this page divide itself up? — and hands
the answer to the planner so the LLM never has to re-derive it.

Why it exists
-------------
``SourceContent.headings`` is a flat, level-less, deduped ``list[str]`` and
``raw_text`` is newline-joined lines. Between them they delete every signal that
says which heading OPENS a section and which merely TITLES a card inside one. On
Glorykids' /school-life that left "School Life" (an h2 spanning four age-group
cards) and "Innovation Centre" (an h3 card title) at identical rank in the
prompt — so the model merged six sections into one and shuffled the cards
between them. The information was never missing from the DOM; it was discarded
on the way to the prompt.

How a section is found
----------------------
Heading rank, and nothing else: a heading owns everything until the next heading
of same-or-higher rank. That nests h3 card titles inside the h2 above them, and
it works on any site that uses headings for what they mean.

How its cards are found
-----------------------
**A section's cards are its direct child headings** — the source's own answer,
free of any assumption about class names or markup convention. Applied with one
guard: the children must be LEAVES. A heading whose own children carry cards is
a container (the page's h1 over its h2 sections), not a card rack, so it keeps
its prose and lets its children stand as sections in their own right.

Only when a section has no child headings does the repeated-sibling scan run —
the fallback for card racks built from bare divs, which is how a page-builder
site with no heading discipline expresses the same thing.

Classification is group-level on purpose
----------------------------------------
A card in isolation is unclassifiable: a photo, a two-word title and a paragraph
describe "Innovation Centre" and "Marcus Ong" identically. Only the group
disambiguates — a roster agrees with itself. ``people`` therefore has to be
EARNED (most titles person-shaped AND portrait or contact evidence present);
everything else is a cheaper fallback. This inverts the old model, where
whichever anchor-specific extractor ran first claimed the cards for its own
kind, and a facilities grid became a six-person team.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Callable
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

from app.models.content_blocks import SectionCandidate, SourceCard, SourceCardKind

_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# Never content: a nav or footer heading describes the site, not this page. The
# scope walk honours these too — without that, the LAST section on every page
# runs to the end of the document and adopts the footer's address and phone
# numbers as its cards.
_CHROME_TAGS = ("nav", "header", "footer", "aside", "form")

# Bounds. Below the floor a "group" is one card and a coincidence; above the
# ceiling it is a sitemap dump or a link farm, not a section's card rack.
_MIN_GROUP_CARDS = 2
_MAX_GROUP_CARDS = 24
_MAX_SECTIONS = 30

# A repeated sibling group must actually dominate its parent — a 2-of-9
# signature match is two similar divs in a pile of markup, not a card rack.
_MIN_GROUP_SHARE = 0.6

_MAX_HEADING_LEN = 200
_MAX_PROSE_CHARS = 1200
_MAX_CARD_BODY_CHARS = 600
# A card's short label/value lines ("8:30 am - 3:00 pm"). Longer than this and
# it is body copy, not a badge.
_MAX_META_LEN = 60
# A heading longer than this is a sentence a page-builder styled as a heading.
_MAX_CARD_TITLE_LEN = 90

# An explicit ordinal marker — "1.", "Step 2", "03)". Deliberately NOT any
# leading digit: "18 months to 3 years old" is an age group, not step 18.
_STEP_RE = re.compile(r"^(step\s+)?\d{1,2}\s*[.):]\s+|^step\s+\d{1,2}\b", re.I)

_DOC_EXTENSIONS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx")

PersonNameTest = Callable[[str], bool]


def _clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _text(tag: Tag) -> str:
    return _clean(tag.get_text(" ", strip=True))


def _in_chrome(tag: Tag) -> bool:
    return tag.find_parent(list(_CHROME_TAGS)) is not None


def _signature(tag: Tag) -> tuple[str, tuple[str, ...]]:
    """Structural fingerprint of a subtree: own tag + immediate child tags.

    Class names are deliberately ignored. Page builders stamp per-element
    generated classes (Wix's ``comp-xyz123``) that differ on every sibling, so
    matching on them would find no groups at all on exactly the sites that need
    this most.
    """
    kids = [c.name for c in tag.find_all(recursive=False) if isinstance(c, Tag)]
    return (tag.name, tuple(kids[:6]))


def _measured_portrait(img: Tag) -> bool:
    """True when an image is measurably square-ish or tall.

    Unknown dimensions return False — the opposite of the scraper's per-image
    benefit-of-the-doubt. This is a vote FOR calling a group people, so silence
    must not count as evidence.
    """
    raw_w, raw_h = img.get("width"), img.get("height")
    if not isinstance(raw_w, str) or not isinstance(raw_h, str):
        return False
    try:
        width, height = int(raw_w), int(raw_h)
    except ValueError:
        return False
    return width > 0 and height > 0 and 0.6 <= width / height <= 1.4


def _has_contact_link(card: Tag) -> bool:
    """A mailto:/tel: link is how a roster says 'reach this person'."""
    for anchor in card.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href")
        if isinstance(href, str) and href.strip().lower().startswith(("mailto:", "tel:")):
            return True
    return False


def _has_document_link(card: Tag) -> bool:
    for anchor in card.find_all("a", href=True):
        if not isinstance(anchor, Tag):
            continue
        href = anchor.get("href")
        if isinstance(href, str) and href.strip().lower().split("?")[0].endswith(
            _DOC_EXTENSIONS
        ):
            return True
    return False


# --- pass 1: segment ------------------------------------------------------------


class _Node:
    """One heading and the span of document it owns.

    ``card_scopes`` is parallel to ``cards``: the ELEMENTS each card was built
    from, which the classifier needs and the finished ``SourceCard`` no longer
    carries. It must be the card's whole span, not the element that titled it —
    a heading-derived card is titled by an ``<h3>``, which holds neither the
    portrait nor the mailto: link that prove the group is people.
    """

    __slots__ = ("tag", "level", "stop", "children", "cards", "card_scopes", "is_card")

    def __init__(self, tag: Tag, level: int, stop: Tag | None) -> None:
        self.tag = tag
        self.level = level
        self.stop = stop
        self.children: list[_Node] = []
        self.cards: list[SourceCard] = []
        self.card_scopes: list[list[Tag]] = []
        self.is_card = False


def _segment(soup: BeautifulSoup) -> list[_Node]:
    """Headings → nodes, nested by rank. Returns roots in document order."""
    body = soup.body or soup
    heads: list[Tag] = []
    for tag in body.find_all(_HEADING_TAGS):
        if not isinstance(tag, Tag) or _in_chrome(tag):
            continue
        text = _text(tag)
        if text and 2 <= len(text) <= _MAX_HEADING_LEN:
            heads.append(tag)

    nodes: list[_Node] = []
    for i, head in enumerate(heads):
        level = int(head.name[1])
        stop = next((n for n in heads[i + 1:] if int(n.name[1]) <= level), None)
        nodes.append(_Node(head, level, stop))

    roots: list[_Node] = []
    stack: list[_Node] = []
    for node in nodes:
        while stack and stack[-1].level >= node.level:
            stack.pop()
        if stack:
            stack[-1].children.append(node)
        else:
            roots.append(node)
        stack.append(node)
    return roots


def _scope(node: _Node) -> list[Tag]:
    """Elements between a heading and its stop heading, chrome excluded."""
    out: list[Tag] = []
    seen: set[int] = set()
    for el in node.tag.next_elements:
        if node.stop is not None and el is node.stop:
            break
        if not isinstance(el, Tag) or id(el) in seen:
            continue
        seen.add(id(el))
        if el.name in _CHROME_TAGS:
            break
        if _in_chrome(el):
            continue
        out.append(el)
    return out


# --- pass 2: cards --------------------------------------------------------------


def _card_from_heading(node: _Node, base_url: str) -> tuple[SourceCard, list[Tag]] | None:
    """A child heading, read as one card: title + its own span of content."""
    title = _text(node.tag)
    if not title or len(title) > _MAX_CARD_TITLE_LEN:
        return None
    scope = _scope(node)
    return _build_card(title, scope, base_url), scope


def _is_label(title: str) -> bool:
    """A trailing colon introduces a value — "Full Programme :" names a field,
    not a card. Left in the section's prose/meta rather than promoted to a card
    title, which is how a schedule table briefly became a two-card rack."""
    return title.rstrip().endswith(":")


def _card_from_container(tag: Tag, base_url: str) -> tuple[SourceCard, list[Tag]] | None:
    """A repeated sibling div, read as one card."""
    heading = next(
        (h for h in tag.find_all(_HEADING_TAGS) if isinstance(h, Tag) and _text(h)), None
    )
    lines = [_clean(str(s)) for s in tag.stripped_strings if _clean(str(s))]
    if not lines:
        return None
    title = _text(heading) if heading is not None else lines[0]
    if not title or len(title) > _MAX_CARD_TITLE_LEN or _is_label(title):
        return None
    card = _build_card(
        title, [tag], base_url, own_lines=[ln for ln in lines if ln != title]
    )
    return card, [tag]


def _build_card(
    title: str,
    scope: list[Tag],
    base_url: str,
    *,
    own_lines: list[str] | None = None,
) -> SourceCard:
    if own_lines is not None:
        lines = own_lines
    else:
        lines = []
        for el in scope:
            if el.name not in ("p", "li", "span", "div"):
                continue
            text = _text(el)
            if text and text != title and text not in lines:
                lines.append(text)
        # Keep only the outermost text of nested wrappers: a div and the p
        # inside it yield the same sentence twice.
        lines = [ln for ln in lines if not any(ln != o and ln in o for o in lines)]

    meta = [ln for ln in lines if len(ln) <= _MAX_META_LEN]
    body = " ".join(ln for ln in lines if len(ln) > _MAX_META_LEN)

    image_url = None
    image_alt = ""
    link = None
    for el in scope:
        for img in el.find_all("img") if el.name != "img" else [el]:
            if not isinstance(img, Tag) or image_url is not None:
                continue
            src = img.get("src")
            if isinstance(src, str) and src.strip():
                image_url = urljoin(base_url, src.strip())
                alt = img.get("alt")
                image_alt = alt.strip() if isinstance(alt, str) else ""
        for anchor in el.find_all("a", href=True):
            if not isinstance(anchor, Tag) or link is not None:
                continue
            href = anchor.get("href")
            if isinstance(href, str) and not href.strip().lower().startswith(
                ("mailto:", "tel:", "javascript:", "#")
            ):
                link = urljoin(base_url, href.strip())

    return SourceCard(
        title=title[:_MAX_CARD_TITLE_LEN],
        body=body[:_MAX_CARD_BODY_CHARS],
        image_url=image_url,
        image_alt=image_alt,
        link=link,
        meta=meta[:6],
    )


def _repeated_group(scope: list[Tag]) -> list[Tag]:
    """Largest repeated sibling group in a scope — the heading-less fallback."""
    best: list[Tag] = []
    scope_ids = {id(el) for el in scope}
    for parent in scope:
        kids = [c for c in parent.find_all(recursive=False) if isinstance(c, Tag)]
        if len(kids) < _MIN_GROUP_CARDS:
            continue
        signature, hits = Counter(_signature(k) for k in kids).most_common(1)[0]
        if hits < _MIN_GROUP_CARDS or hits < len(kids) * _MIN_GROUP_SHARE:
            continue
        group = [
            k
            for k in kids
            if _signature(k) == signature and _text(k) and id(k) in scope_ids
        ]
        if len(group) > len(best):
            best = group[:_MAX_GROUP_CARDS]
    return best


def _assign_cards(node: _Node, base_url: str) -> None:
    """Post-order: a node's cards are its child headings, if they are leaves."""
    for child in node.children:
        _assign_cards(child, base_url)

    leaf_children = [c for c in node.children if not c.cards]
    if len(node.children) >= _MIN_GROUP_CARDS and len(leaf_children) == len(node.children):
        built = [(c, _card_from_heading(c, base_url)) for c in node.children]
        kept = [(child, res) for child, res in built if res is not None]
        if len(kept) >= _MIN_GROUP_CARDS:
            node.cards = [card for _, (card, _) in kept]
            node.card_scopes = [scope for _, (_, scope) in kept]
            for child, _ in kept:
                child.is_card = True
            return

    if not node.children:
        group = _repeated_group(_scope(node))
        built_c = [_card_from_container(t, base_url) for t in group]
        kept_c = [res for res in built_c if res is not None]
        # A card says something. A repeated pair of bare label/value rows — a
        # schedule, an opening-hours table — matches the sibling signature just
        # as well as a card rack does, and promoting it costs the SECTION ABOVE
        # its own cards: a parent only claims child headings when they are all
        # leaves, so one spurious group two levels down silently flattens the
        # real one. Requiring body text is what separates the two.
        with_body = sum(1 for card, _ in kept_c if card.body)
        if len(kept_c) >= _MIN_GROUP_CARDS and with_body >= len(kept_c) * 0.6:
            node.cards = [card for card, _ in kept_c]
            node.card_scopes = [scope for _, scope in kept_c]


# --- pass 3: classify -----------------------------------------------------------


def _first_image(scope: list[Tag]) -> Tag | None:
    for el in scope:
        if el.name == "img":
            return el
        img = next((i for i in el.find_all("img") if isinstance(i, Tag)), None)
        if img is not None:
            return img
    return None


def _classify(
    cards: list[SourceCard],
    scopes: list[list[Tag]],
    person_name: PersonNameTest | None,
) -> SourceCardKind:
    """Decide what a card GROUP is, from positive evidence.

    ``scopes`` is each card's full span of elements, parallel to ``cards``.

    ``person_name`` is ``scraper._looks_like_person_name``, injected to avoid a
    circular import. Absent, the people branch cannot fire — the safe direction:
    a misfiled roster loses portraits, a misfiled facility grid invents staff.
    """
    if not cards:
        return "prose"
    total = len(cards)

    if any(_has_document_link(el) for scope in scopes for el in scope):
        return "documents"

    if person_name is not None:
        named = sum(1 for c in cards if person_name(c.title))
        portraits = sum(
            1
            for scope in scopes
            for img in [_first_image(scope)]
            if img is not None and _measured_portrait(img)
        )
        contacts = sum(
            1 for scope in scopes if any(_has_contact_link(el) for el in scope)
        )
        # A roster agrees with itself. One name-shaped label among six is a
        # coincidence; titles that all read as people, backed by portrait
        # geometry or a contact link, is a team.
        if named >= max(_MIN_GROUP_CARDS, total * 0.6) and (
            portraits >= total * 0.5 or contacts >= 1
        ):
            return "people"

    if sum(1 for c in cards if _STEP_RE.match(c.title)) >= total * 0.6:
        return "steps"

    # Almost entirely picture — a title and nothing else — is a gallery, not an
    # offering list. Beyond that no further split is attempted: whether a card
    # carries a photo is already in `image_urls`, and a "facilities" kind keyed
    # on that alone drew a line the markup does not actually draw.
    with_image = sum(1 for c in cards if c.image_url)
    if with_image >= total * 0.8 and all(len(c.body) < 40 for c in cards):
        return "gallery"
    return "offerings"


# --- public entry point ---------------------------------------------------------


def extract_section_candidates(
    soup: BeautifulSoup,
    base_url: str,
    *,
    person_name: PersonNameTest | None = None,
) -> list[SectionCandidate]:
    """The page's section tree: heading, prose, and its classified card group."""
    roots = _segment(soup)
    for root in roots:
        _assign_cards(root, base_url)

    out: list[SectionCandidate] = []

    def emit(node: _Node) -> None:
        if node.is_card:
            return  # claimed as a card by its parent — not a section too
        card_text = {c.title for c in node.cards}
        prose_parts: list[str] = []
        for el in _scope(node):
            if el.name not in ("p", "li", "h2", "h3", "h4"):
                continue
            text = _text(el)
            if not text or text in card_text:
                continue
            if any(text in c.body or text in " ".join(c.meta) for c in node.cards):
                continue
            if any(text in existing for existing in prose_parts):
                continue
            prose_parts.append(text)
        # Content owned by a CHILD section is that child's, not this one's.
        for child in node.children:
            if child.is_card:
                continue
            child_scope = {_text(el) for el in _scope(child)}
            child_scope.add(_text(child.tag))
            prose_parts = [p for p in prose_parts if p not in child_scope]

        section = SectionCandidate(
            heading=_text(node.tag)[:_MAX_HEADING_LEN],
            level=node.level,
            prose=" ".join(prose_parts)[:_MAX_PROSE_CHARS],
            cards=node.cards,
            card_kind=_classify(node.cards, node.card_scopes, person_name),
            image_urls=[c.image_url for c in node.cards if c.image_url][:_MAX_GROUP_CARDS],
        )
        if section.cards or section.prose:
            out.append(section)
        for child in node.children:
            emit(child)

    for root in roots:
        emit(root)
    return out[:_MAX_SECTIONS]
