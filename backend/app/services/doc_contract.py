"""
Examine free-text document titles to decide which read like *page topics*.

Scraped sites carry URL slugs that ``page_inference`` classifies; uploaded
documents carry only headings. This module examines a heading's wording and
returns a ``PageType`` when it reads like a page topic (About, Services,
Contact…), or ``None`` when it's just an in-page heading ("Why Choose Us", "Our
Promise") — the latter must NOT spawn its own page.

Per the design refinement: the website's section composition and visual
distinctiveness stay owned by the planner. Titles only shape *which pages exist*
and *where content is routed*. We deliberately reuse ``page_inference._TYPE_HINTS``
so the doc path and the scrape path share one notion of what each page topic is.
"""

from __future__ import annotations

import re

from app.models.industry import PageType
from app.services.page_inference import _TYPE_HINTS

# Titles meaning "this is the landing/home page" — kept as the primary page,
# never emitted as a discovered sub-page even though "home"/"overview" could
# otherwise read as a topic.
_HOME_TITLE_HINTS = (
    "home",
    "welcome",
    "homepage",
    "overview",
    "introduction",
    "landing page",
)

# Page types we never spin a standalone content page out of from a doc heading:
# legal + utility pages are generated from boilerplate, not document copy.
_NON_PAGE_TYPES = {"privacy", "terms", "thank-you"}

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def classify_page_title(title: str) -> PageType | None:
    """Return a ``PageType`` if the title reads like a page topic, else ``None``.

    Matching mirrors ``page_inference._infer_page_type`` but, unlike it, returns
    ``None`` on no match instead of falling back to ``services`` — a generic
    in-page heading must stay content, not become a page.
    """
    text = (title or "").strip().lower()
    if not text:
        return None
    for page_type, hints in _TYPE_HINTS:
        if page_type in _NON_PAGE_TYPES:
            continue
        for hint in hints:
            # Match the hint as a word/phrase: "our-story" → "our story".
            if hint in text or hint.replace("-", " ") in text:
                return page_type
    return None


def is_home_title(title: str) -> bool:
    """True when a heading names the homepage itself (so it stays primary)."""
    text = (title or "").strip().lower()
    return any(hint in text for hint in _HOME_TITLE_HINTS)


def slug_for_title(title: str) -> str:
    """Synthesize a URL-ish slug from a document title.

    The slug is what ``page_inference`` re-classifies downstream, so keeping the
    title's words ("About Us" → ``about-us``) lets it infer the right page_type
    without the doc path having to assert one.
    """
    base = _SLUG_STRIP.sub("-", (title or "").strip().lower()).strip("-")
    return base[:60] or "page"
