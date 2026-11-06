"""Which of a document's titles are pages, which are sections, which are subtitles.

A document states two things about its titles. Its typography — a PDF's font
sizes, a DOCX's styles, a paste's ``#`` markers — states RANK: which titles
stand over which. Its words state what some titles are FOR: a page's name
("About Us", "HOME"), a label ("Section 01", "Contact page"). Grouping a
document into a site needs both, and this module is the one place they are
read together.

**The main titles are the document's parts: the shallowest rank a part is
titled at.** A title is a part only when it owns copy (it is not stacked on
the next title of its rank or higher), so a cover set as three stacked lines,
or an eyebrow over its headline, is never a part. The document's opening title
is its cover, unless it names a page, and a lone headline set above a rank
that recurs is the cover's second line (``_main_rank``). Everything a main
title owns down to the next main title is its block.

**Whether the main titles are pages is decided on evidence, strongest first:**

1. *It names its pages* — a main title is the home page by name ("HOME",
   "Homepage") beside other main titles, or two carry a page label ("About Us
   page", "Page 2: Services"). Pages.
2. *It labels the sections of one page* — "Section 01", "WEBSITE SECTION
   02 / 14", "Hero", read on every line given, including the lines the
   structure read left out as notes. One page.
3. *Its main titles hold sections of their own* — two main titles each over
   two titles that carry their own titled cards: main > section > card is a
   page's shape. Pages.
4. *Its main titles are page names* — at least ``_PAGE_NAME_SHARE`` of them
   are short page names (``names_page``). Pages: one part titled "Contact"
   below an intro is the Contact page, which is what pasting a page's copy
   into the paste box means. A brief whose parts are mostly headlines ("Why
   parents choose us", "Our programmes", "Get in touch") is one page.
5. Otherwise its main titles head the sections of one page.

Nothing in the decision comes from a model. The model that reads a document
for its copy (``paste_structure``) says which lines are copy, and may name a
page's TYPE; it never changes which pages exist. The same outline is therefore
the same site, whichever reader ran and whatever the model answered.

**When it is pages**, the home page is everything before the first page: a
"HOME" title opens it explicitly, and without one, main titles before the
first that names a page, holds sections or is labelled a page are the home
page's own sections. Every main title after that opens a page, except a
second title naming a topic already given a page ("Services", then
"Solutions"), which stays in the page before it.

Inside a page, the titles one rank below its title are its sections and
anything deeper is a subtitle; ``outline_sections`` reads a page's own section
tree from there.

**The person making the site has the last word** (``shape``, the page picker's
"Site type"). One page folds every part onto the homepage. Multi-page on a
document that reads as one landing page files its parts by practice
(``_pages_by_practice``, ``OWN_PAGE_TYPES``); with nothing to file, the site
still gets the industry's pages around the whole homepage. The document's own
reading stays on the plan (``suggests_one_page``, ``reason``) so the picker can
say what it was.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Collection, Literal, Mapping, Sequence

from app.models.industry import PageType
from app.services.doc_contract import (
    declares_page,
    declares_section,
    names_home_page,
    names_page,
)
from app.services.source_outline import OutlineBlock

TitleRole = Literal["cover", "home", "page", "section", "subtitle"]
# What the person making the site chose it to be, over what the document says.
SiteShape = Literal["one_page", "multi_page"]

# What a multi-page site takes off its homepage when the document was written
# as one landing page: the pages a visitor goes to ON PURPOSE — to compare
# plans, look up an answer, read who you are, meet the team, see the photos,
# the menu or the work, read the news, or get in touch. The homepage keeps the
# journey that sells — the hero, the problem, the offer, how it works, the
# proof — and its closing ask, which now leads to those pages. Services stay:
# a homepage that does not say what the business does has nothing to sell.
OWN_PAGE_TYPES: frozenset[PageType] = frozenset({
    "about", "team", "pricing", "faq", "contact", "gallery", "menu", "work", "blog", "events",
})

# Titles a rank needs to recur before a lone headline above it is the cover's.
_MIN_MAIN_TITLES = 2
# Lines labelled as sections before the document is read as one page.
_MIN_SECTION_LABELS = 2
# Titles labelled as pages before they declare pages without a "HOME".
_MIN_PAGE_LABELS = 2
# Main titles that must hold sections of their own, and how many each.
_MIN_NESTED_MAINS = 2
_MIN_CHILD_SECTIONS = 2
# Titled cards a child title must carry to be a section rather than a card.
_MIN_CARDS = 2
# Share of main titles that must be page names for the names alone to make
# pages (the group share `outline_sections` and the card classifier use).
_PAGE_NAME_SHARE = 0.6
# A page whose title names no topic: the fallback `_infer_page_type` and the
# structure read already give an unknown top-level page.
UNNAMED_PAGE_TYPE: PageType = "services"


@dataclass(frozen=True)
class OutlinePlan:
    """How a document groups into pages, and every title's part in it.

    ``roles`` and ``pages`` key on the index of the title's block in the
    blocks that were planned. A ``pages`` entry typed ``home`` files its part
    back on the homepage after a page (``_pages_by_practice``). ``reason`` is
    the document's own evidence, and ``suggests_one_page`` what that evidence
    says; ``one_page`` is the site's shape, which a chosen shape overrides.
    """

    one_page: bool
    reason: str
    suggests_one_page: bool
    roles: Mapping[int, TitleRole] = field(default_factory=dict)
    pages: tuple[tuple[int, PageType], ...] = ()


def plan_pages(
    blocks: Sequence[OutlineBlock],
    *,
    copy: Collection[int] | None = None,
    named: Mapping[int, PageType] | None = None,
    shape: SiteShape | None = None,
) -> OutlinePlan | None:
    """Group ``blocks`` into pages, or ``None`` when no copy line is a title.

    ``copy`` holds the indices that are website copy (``None``: all of them);
    titles and ranks are read on copy alone, section labels on every block.
    ``named`` gives a page TYPE by title index (the structure read's answer),
    used to type a page this plan opens and for nothing else. ``shape`` is the
    site the person chose; ``None`` follows the document.
    """
    named = named or {}
    in_copy = [
        (copy is None or index in copy) and bool(block.text.strip())
        for index, block in enumerate(blocks)
    ]
    titles = [i for i, block in enumerate(blocks) if in_copy[i] and block.level > 0]
    if not titles:
        return None
    text = {i: blocks[i].text for i in titles}
    owns = {i: _owns_copy(blocks, in_copy, i) for i in titles}

    leads = titles[0] == in_copy.index(True)
    opening = titles[0] if leads and not _page_like(text[titles[0]]) else None
    parts = [i for i in titles if owns[i] and i != opening]
    if not parts:
        return OutlinePlan(
            one_page=shape != "multi_page",
            reason="it has no titled parts below its cover",
            suggests_one_page=True,
            roles={i: "cover" if i == opening else "subtitle" for i in titles},
        )

    rank = _main_rank(blocks, parts, text)
    first = next(i for i in parts if blocks[i].level == rank)
    mains = [i for i in titles if i >= first and blocks[i].level <= rank and owns[i]]
    ends = dict(zip(mains, [*mains[1:], len(blocks)]))
    roles: dict[int, TitleRole] = {
        i: "cover" if i < first and (i == opening or blocks[i].level <= rank) else "subtitle"
        for i in titles
    }
    nested = {
        m for m in mains
        if _child_sections(blocks, in_copy, m, ends[m]) >= _MIN_CHILD_SECTIONS
    }

    suggests_one_page, reason = _verdict(blocks, mains, nested, text)
    if (shape == "multi_page") if shape else not suggests_one_page:
        pages = (
            _pages_by_practice(mains, text, roles) if suggests_one_page
            else _assign_pages(mains, nested, text, named, roles)
        )
        opened = [page for page, page_type in pages if page_type != "home"]
        if opened or shape == "multi_page":
            for page in opened:
                _mark_page_sections(blocks, in_copy, page, ends[page], roles)
            return OutlinePlan(False, reason, suggests_one_page, roles, tuple(pages))
        reason, suggests_one_page = "no main title opens a page", True
    for main in mains:
        roles[main] = "section"
    return OutlinePlan(True, reason, suggests_one_page, roles)


def _main_rank(blocks: Sequence[OutlineBlock], parts: list[int], text: Mapping[int, str]) -> int:
    """The rank of the document's parts: the shallowest a part is titled at.

    One exception, the cover's second line: a LONE headline above a rank that
    recurs is set larger than the parts, not one of them ("Enrol now for 2027"
    over four sections). A lone title that names a page is a part — the
    "About Us" over "Our Story", "Our Team" and "Our Values" is that page.
    """
    counts = Counter(blocks[i].level for i in parts)
    levels = sorted(counts)
    lone = [i for i in parts if blocks[i].level == levels[0]]
    if len(lone) == 1 and not _page_like(text[lone[0]]):
        recurring = [level for level in levels[1:] if counts[level] >= _MIN_MAIN_TITLES]
        if recurring:
            return recurring[0]
    return levels[0]


def _verdict(
    blocks: Sequence[OutlineBlock],
    mains: list[int],
    nested: set[int],
    text: Mapping[int, str],
) -> tuple[bool, str]:
    """(one page?, why) — the evidence order in the module docstring."""
    homes = [m for m in mains if names_home_page(text[m])]
    labelled = [m for m in mains if declares_page(text[m])]
    if len(labelled) >= _MIN_PAGE_LABELS or (homes and len(mains) > len(homes)):
        return False, f"it names its pages ({_listed(text, labelled)})"
    if sum(declares_section(block.text) for block in blocks) >= _MIN_SECTION_LABELS:
        return True, "it labels the sections of one page"
    if len(nested) >= _MIN_NESTED_MAINS:
        return False, f"its main titles hold sections of their own ({_listed(text, sorted(nested))})"
    page_names = [m for m in mains if names_page(text[m]) is not None]
    if page_names and len(page_names) >= _PAGE_NAME_SHARE * len(mains):
        return False, f"its main titles are page names ({_listed(text, page_names)})"
    return True, f"its main titles head the sections of one page ({_listed(text, mains)})"


def _assign_pages(
    mains: list[int],
    nested: set[int],
    text: Mapping[int, str],
    named: Mapping[int, PageType],
    roles: dict[int, TitleRole],
) -> list[tuple[int, PageType]]:
    """The main titles that open pages, typed; every main title's role set."""
    pages: list[tuple[int, PageType]] = []
    topics: set[PageType] = set()
    started = False
    for main in mains:
        if names_home_page(text[main]):
            roles[main], started = "home", True
            continue
        topic = names_page(text[main])
        if not started and topic is None and main not in nested and not declares_page(text[main]):
            roles[main] = "section"  # the home page's own, before its first page
            continue
        if topic is not None and topic in topics:
            roles[main] = "section"  # one page per topic: a second About is more of the first
            continue
        started = True
        roles[main] = "page"
        if topic is not None:
            topics.add(topic)
        pages.append((main, named.get(main) or topic or UNNAMED_PAGE_TYPE))
    return pages


def _pages_by_practice(
    mains: list[int], text: Mapping[int, str], roles: dict[int, TitleRole]
) -> list[tuple[int, PageType]]:
    """A one-page document's parts, filed the way a multi-page site files them.

    A part that names an ``OWN_PAGE_TYPES`` page ("Pricing", "Common
    questions") becomes that page; every other part stays on the homepage, in
    the document's order — a part after a page goes BACK to the homepage
    (typed ``home``) rather than onto the page before it. One page per topic.
    """
    pages: list[tuple[int, PageType]] = []
    topics: set[PageType] = set()
    on_page = False
    for main in mains:
        topic = names_page(text[main])
        if topic in OWN_PAGE_TYPES and topic not in topics:
            topics.add(topic)
            roles[main] = "page"
            pages.append((main, topic))
            on_page = True
            continue
        roles[main] = "section"
        if on_page:
            pages.append((main, "home"))
            on_page = False
    return pages


def _mark_page_sections(
    blocks: Sequence[OutlineBlock],
    in_copy: list[bool],
    page: int,
    end: int,
    roles: dict[int, TitleRole],
) -> None:
    """A page's titles one rank below its own are its sections."""
    inner = [i for i in range(page + 1, end) if in_copy[i] and blocks[i].level > blocks[page].level]
    if not inner:
        return
    rank = min(blocks[i].level for i in inner)
    for i in inner:
        if blocks[i].level == rank and roles.get(i) == "subtitle":
            roles[i] = "section"


def _owns_copy(blocks: Sequence[OutlineBlock], in_copy: list[bool], index: int) -> bool:
    """False only for a STACKED title: the next copy line is a title of its
    rank or higher (a cover set in three lines, an eyebrow over its headline).
    A last title owns what the page will be given: "Contact", with the form."""
    level = blocks[index].level
    following = (j for j in range(index + 1, len(blocks)) if in_copy[j])
    nxt = next(following, None)
    return nxt is None or not 0 < blocks[nxt].level <= level


def _child_sections(
    blocks: Sequence[OutlineBlock], in_copy: list[bool], main: int, end: int
) -> int:
    """How many of a main title's children carry titled cards of their own."""
    inner = [i for i in range(main + 1, end) if in_copy[i] and blocks[i].level > blocks[main].level]
    if not inner:
        return 0
    rank = min(blocks[i].level for i in inner)
    children = [i for i in inner if blocks[i].level == rank]
    bounds = dict(zip(children, [*children[1:], end]))
    return sum(
        sum(1 for i in inner if child < i < bounds[child] and blocks[i].level > rank) >= _MIN_CARDS
        for child in children
    )


def _page_like(title: str) -> bool:
    return names_page(title) is not None or declares_page(title)


def _listed(text: Mapping[int, str], indices: list[int], limit: int = 3) -> str:
    names = [" ".join(text[i].split()) for i in indices[:limit]]
    more = len(indices) - limit
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")
