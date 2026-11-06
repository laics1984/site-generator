"""
Examine free-text document titles: what a title names, and what it declares.

Scraped sites carry URL slugs that ``page_inference`` classifies; uploaded
documents carry only headings. This module answers the questions a heading's
WORDS can answer — which page topic it names (About, Services, Contact…),
whether it names the home page, whether it labels itself as a page or as a
section of one. Which titles actually open pages is a question about the whole
outline, and ``outline_pages`` answers it with these predicates.

Per the design refinement: the website's section composition and visual
distinctiveness stay owned by the planner. Titles only shape *which pages exist*
and *where content is routed*. Topics are read by ``page_inference``'s own
whole-token matcher, so the doc path and the scrape path share one notion of
what each page topic is.
"""

from __future__ import annotations

import re

from app.models.industry import PageType
from app.services.page_inference import _matched_page_type
from app.services.source_outline import strip_list_marker

# Page types we never spin a standalone content page out of from a doc heading:
# legal + utility pages are generated from boilerplate, not document copy.
_NON_PAGE_TYPES = {"privacy", "terms", "thank-you"}

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")

# A page's name is a few words, never a sentence: "About Us", "Our Services",
# "Meet the Team". A one-page brief's section headlines run longer or
# punctuate ("Helping your kindergarten grow, one family at a time.").
_PAGE_NAME_MAX_WORDS = 4
_SENTENCE_MARK = re.compile(r"[.!?,;:]")
# The whole title names the home page: "HOME", "Home page", "Homepage".
# "Welcome to Sunny Steps" is a hero headline, not a page's name.
_HOME_PAGE = re.compile(r"^(?:home|home\s*page|landing\s+page|main\s+page)$", re.I)
# A title labelled as a page: "About Us page", "Page 2: Services",
# "PAGE – CONTACT". Never paper numbering ("Page 3 of 29").
_PAGE_SUFFIX = re.compile(r"^(?P<name>.+?)\s+page$", re.I)
_PAGE_PREFIX = re.compile(r"^page\s*\d{0,3}\s*[:\-–—.|]\s*(?P<name>\S.*)$", re.I)
# A line labelling one section of a page: "Section 01", "WEBSITE SECTION
# 02 / 14 · ABOUT" — the prefix — and the hero: "Hero", "Hero section",
# "Hero (above the fold)", "Section 01 — Hero".
_SECTION_PREFIX = re.compile(
    r"^(?:website\s+)?section\s*\d{1,3}(?:\s*/\s*\d{1,3})?\b\s*[·:\-–—|.]?\s*", re.I
)
_HERO = re.compile(r"^hero(?:\s+section|\s+banner)?(?:\s*\(.*\))?$", re.I)


def classify_page_title(title: str) -> PageType | None:
    """Return a ``PageType`` if the title's words name a page topic, else ``None``.

    Whole-token, by ``page_inference._matched_page_type``: a substring test
    read "We help families" as an FAQ ("help") and "Helping people grow" as a
    team ("people"). Unlike ``_infer_page_type`` it returns ``None`` on no
    match instead of falling back to ``services`` — a generic in-page heading
    must stay content, not become a page.
    """
    text = (title or "").strip()
    if not text:
        return None
    page_type = _matched_page_type("", text)
    return None if page_type in _NON_PAGE_TYPES else page_type


def names_page(title: str) -> PageType | None:
    """The page a title NAMES — a short page name, never a headline that
    happens to contain a topic word. "Our Services" names one; "Services that
    grow with you." does not, though it classifies."""
    text = _bare(title)
    name = _page_label_name(text) or text
    if len(name.split()) > _PAGE_NAME_MAX_WORDS or _SENTENCE_MARK.search(name):
        return None
    return classify_page_title(name)


def names_home_page(title: str) -> bool:
    """True when the whole title names the home page ("HOME", "Homepage")."""
    return bool(_HOME_PAGE.match(_bare(title)))


def declares_page(title: str) -> bool:
    """True when a title labels itself a page: the home page by name, or
    "About Us page" / "Page 2: Services"."""
    text = _bare(title)
    return names_home_page(text) or _page_label_name(text) is not None


def declares_section(text: str) -> bool:
    """True when a line labels a section of one page ("Section 01", "Hero")."""
    return bool(_SECTION_PREFIX.match(_bare(text))) or declares_hero(text)


def declares_hero(text: str) -> bool:
    """True when a line labels the page's hero ("Hero", "Section 01 — Hero")."""
    return bool(_HERO.match(_SECTION_PREFIX.sub("", _bare(text), count=1).strip()))


def slug_for_title(title: str) -> str:
    """Synthesize a URL-ish slug from a document title.

    The slug is what ``page_inference`` re-classifies downstream, so keeping the
    title's words ("About Us" → ``about-us``) lets it infer the right page_type
    without the doc path having to assert one.
    """
    base = _SLUG_STRIP.sub("-", (title or "").strip().lower()).strip("-")
    return base[:60] or "page"


def _bare(title: str) -> str:
    """A title as its words: list number off ("1. HOME"), whitespace collapsed."""
    return " ".join(strip_list_marker(" ".join((title or "").split())).split())


def _page_label_name(text: str) -> str | None:
    """The name inside a page label ("About Us page" → "About Us"), or None."""
    if names_home_page(text):
        return None
    for pattern in (_PAGE_PREFIX, _PAGE_SUFFIX):
        match = pattern.match(text)
        if match:
            return match.group("name").strip()
    return None
