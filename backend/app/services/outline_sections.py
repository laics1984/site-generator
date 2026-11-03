"""The section tree of an authored source, read from its outline.

A crawled page gets its tree from its markup (``section_extraction``); a brief
— a PDF, a DOCX, a structured paste — never got one, so it reached the planner
as one flat block of text. Fowable's brief is one landing page in fourteen
sections, each with its own headline and cards. It was planned as a fixed
seven-section SaaS homepage, and its 54 titled cards competed for one six-card
features block filled first-come from whichever section came first.

The tree is the outline's own heading ranks, read the way ``section_extraction``
reads markup: a heading owns everything until the next heading of the same or
higher rank. Which rank is the SECTION rank is the one question the outline
does not answer on its own — a designed brief's cover sets a headline or two
larger than any section headline — so it is the shallowest rank that recurs
(``_MIN_SECTIONS`` times). Anything above it opens the page and belongs to the
hero. Below it, a section's cards are titled at its most common heading rank
(``_card_rank``); a heading set above them is the section's own subheading.

Every word stays the source's: a card's title is its heading line and its body
the lines under it. Kinds are read from what the cards themselves state —
a price makes a plan, a question makes a FAQ item — never from vocabulary.
"""

from __future__ import annotations

from collections import Counter

from app.models.content_blocks import SectionCandidate, SourceCard, SourceCardKind
from app.services.price_lists import PRICE
from app.services.source_outline import OutlineBlock

# A rank that opens fewer sections than this is a cover headline, not the
# page's section rank. Mirrors page_inference._TREE_MIN_SECTIONS.
_MIN_SECTIONS = 3
# A deeper heading this long is a paragraph set large, not a card's title:
# Fowable's subheadings run 90-250 characters at heading size.
_CARD_TITLE_MAX_CHARS = 90
# A price line set as a heading belongs to the card above it.
_PRICE_LINE_MAX_CHARS = 60
# Share of a group's cards that must state a price / ask a question.
_GROUP_SHARE = 0.6
_MIN_GROUP_CARDS = 2


def outline_sections(blocks: list[OutlineBlock]) -> list[SectionCandidate]:
    """The page's sections, in order; [] when its outline states no section rank."""
    rank = _section_rank(blocks)
    if rank is None:
        return []
    groups: list[tuple[str, list[OutlineBlock]]] = []
    current: list[OutlineBlock] | None = None
    for block in blocks:
        text = " ".join(block.text.split())
        if not text:
            continue
        if block.level and block.level <= rank:
            if block.level == rank:
                current = []
                groups.append((text, current))
            else:
                current = None  # a cover headline: the hero's, not a section's
            continue
        if current is not None:
            current.append(OutlineBlock(level=block.level, text=text))
    sections = []
    for heading, body in groups:
        section = _Section(heading, rank, _card_rank(body))
        for block in body:
            section.take(block.level, block.text)
        sections.append(section.candidate())
    return sections


def _section_rank(blocks: list[OutlineBlock]) -> int | None:
    counts = Counter(block.level for block in blocks if block.level)
    recurring = [level for level, count in counts.items() if count >= _MIN_SECTIONS]
    return min(recurring) if recurring else None


def _card_rank(body: list[OutlineBlock]) -> int:
    """The rank a section's cards are titled at: its most common heading rank
    (the deeper one on a tie). A headline set above them is the section's own
    — "A plan for every size of centre" over Starter, Growth and Business."""
    counts = Counter(block.level for block in body if block.level)
    if not counts:
        return 0
    return max(counts, key=lambda level: (counts[level], level))


class _Section:
    """One section while its blocks are read in."""

    def __init__(self, heading: str, level: int, card_rank: int) -> None:
        self.heading = heading
        self.level = level
        self.card_rank = card_rank
        self.subheading = ""
        self.prose: list[str] = []
        self.cards: list[_Card] = []
        self.current: _Card | None = None
        # Whether body lines still belong to `current`. A plan's copy ends at
        # its price: what follows the last plan of a table ("Talk to us about
        # the right plan", a billing note) is the section's, not a feature.
        self.open = False

    def take(self, level: int, text: str) -> None:
        if level and level < self.card_rank:
            # A heading above the cards is the section's own: its subheading,
            # or a line of its prose, and the card before it has ended.
            self.current, self.open = None, False
            if not self.subheading and not self.cards:
                self.subheading = text
            else:
                self.prose.append(text)
        elif self.current is not None and _is_price_line(text):
            self.current.meta.append(text)
            self.open = False
        elif level and len(text) <= _CARD_TITLE_MAX_CHARS:
            # A title the section already used is that card again: a plan
            # table followed by "which plan suits you" names each plan twice.
            same = next((c for c in self.cards if c.title.casefold() == text.casefold()), None)
            if same is None:
                same = _Card(text)
                self.cards.append(same)
            self.current, self.open = same, True
        elif self.current is not None and self.open:
            self.current.body.append(text)
        elif self.current is not None:
            self.prose.append(text)
        elif level and not self.subheading and not self.prose:
            self.subheading = text
        else:
            self.prose.append(text)

    def candidate(self) -> SectionCandidate:
        prose = list(self.prose)
        cards = self.cards
        if any(card.body or card.meta for card in cards):
            # Bare titles among cards with copy are a list in the section's
            # prose ("What parents can find: who you are, programmes, fees…").
            prose += [card.title for card in cards if not (card.body or card.meta)]
            cards = [card for card in cards if card.body or card.meta]
        if len(cards) < _MIN_GROUP_CARDS:
            # One card is not a group: it is the section's own copy.
            prose += [line for card in cards for line in card.lines()]
            cards = []
        cards = [card.card() for card in cards]
        kind = _card_kind(cards)
        if kind == "plans":
            # A plan states its price. An unpriced card in a plan table is the
            # table's own note or ask ("Talk to us about the right plan"),
            # which the model would otherwise write up as a fourth plan.
            prose += [
                line for card in cards if not _priced(card)
                for line in [card.title, *card.body.split("\n")] if line
            ]
            cards = [card for card in cards if _priced(card)]
        return SectionCandidate(
            heading=self.heading,
            level=min(self.level, 6),
            subheading=self.subheading,
            prose="\n".join(prose),
            cards=cards,
            card_kind=kind,
        )


class _Card:
    def __init__(self, title: str) -> None:
        self.title = title
        self.body: list[str] = []
        self.meta: list[str] = []

    def card(self) -> SourceCard:
        return SourceCard(title=self.title, body="\n".join(self.body), meta=self.meta)

    def lines(self) -> list[str]:
        return [self.title, *self.meta, *self.body]


def _is_price_line(text: str) -> bool:
    return len(text) <= _PRICE_LINE_MAX_CHARS and bool(PRICE.search(text))


def _priced(card: SourceCard) -> bool:
    return any(PRICE.search(line) for line in card.meta)


def _card_kind(cards: list[SourceCard]) -> SourceCardKind:
    if not cards:
        return "prose"
    need = len(cards) * _GROUP_SHARE
    if sum(_priced(card) for card in cards) >= need:
        return "plans"
    if sum(card.title.rstrip().endswith("?") for card in cards) >= need:
        return "questions"
    return "offerings"
