"""What a page's own markup declares it to be: one dated entry — a post or an
event — or a standing page.

A CMS publishes posts beside its pages, and nothing in a URL has to tell them
apart: WordPress serves a post at ``/kindergarten-selangor/`` as readily as a
page at ``/about/``. The markup does, in three declarations read in order of
authority:

1. **WordPress core's body classes.** ``page-id-N`` is a page and
   ``single-post`` a post, whatever an SEO plugin states beside them. Yoast and
   Rank Math write ``og:type=article`` (and Rank Math an ``Article`` schema) on
   every page, so on WordPress the plugin's claim is a default, not a
   declaration — which is also why ``og:type=article`` alone counts for nothing.
2. **JSON-LD naming ONE entry**: an Event, or an Article / BlogPosting /
   NewsArticle. A page that names several different entries lists them — an
   events calendar emits one Event per row — so it is a listing, not an entry.
3. ``article:published_time``, which the plugins and platforms that write it
   at all write for posts, not pages.

``content_collections`` reads the same JSON-LD vocabulary when it extracts an
entry, so the crawl's verdict and the migration's cannot disagree about what a
post is.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Literal

from bs4 import BeautifulSoup, Tag

EntryKind = Literal["article", "event"]

ARTICLE_LD_TYPES = frozenset({"article", "blogposting", "newsarticle"})
EVENT_LD_TYPES = frozenset({
    "event", "socialevent", "businessevent", "educationevent", "festival",
    "exhibitionevent", "musicevent", "sportsevent", "theaterevent",
})

# WordPress core stamps these on <body> itself (body_class()); no theme or
# plugin setting changes them. `postid-N` is not enough for a post: every
# singular post TYPE carries it, a WooCommerce product included.
_WP_PAGE_CLASS = re.compile(r"page-id-\d+")
_WP_POST_CLASS = "single-post"


def iter_ld_nodes(soup: BeautifulSoup) -> Iterator[dict]:
    """Every JSON-LD object on the page, ``@graph`` members and arrays opened."""
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if "@graph" in node and isinstance(node["@graph"], list):
                    stack.extend(node["@graph"])
                yield node


def ld_types(node: dict) -> set[str]:
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw.lower()}
    if isinstance(raw, list):
        return {t.lower() for t in raw if isinstance(t, str)}
    return set()


def declared_entry(soup: BeautifulSoup) -> EntryKind | None:
    """The entry the page declares itself, or None for a page (or a listing).

    Read before anything strips ``<script>`` from ``soup``: the JSON-LD is in one.
    """
    classes = _body_classes(soup)
    if any(_WP_PAGE_CLASS.fullmatch(name) for name in classes):
        return None
    named = _ld_entry(soup)
    if named is not None:
        return named
    if _WP_POST_CLASS in classes or _has_meta(soup, "article:published_time"):
        return "article"
    return None


def _body_classes(soup: BeautifulSoup) -> list[str]:
    body = soup.find("body")
    classes = body.get("class") if isinstance(body, Tag) else None
    return list(classes) if isinstance(classes, list) else []


def _ld_entry(soup: BeautifulSoup) -> EntryKind | None:
    """The one entry the JSON-LD names; None for none, or for a list of them.

    Entries are counted by what they name, so a post that two plugins each
    describe once is still one post.
    """
    events: set[str] = set()
    articles: set[str] = set()
    for node in iter_ld_nodes(soup):
        types = ld_types(node)
        if types & EVENT_LD_TYPES:
            events.add(_named(node))
        elif types & ARTICLE_LD_TYPES:
            articles.add(_named(node))
    if len(events) == 1:
        return "event"
    if not events and len(articles) == 1:
        return "article"
    return None


def _named(node: dict) -> str:
    for key in ("headline", "name", "url", "@id"):
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().casefold()
    return ""


def _has_meta(soup: BeautifulSoup, name: str) -> bool:
    tag = soup.find("meta", attrs={"property": name}) or soup.find(
        "meta", attrs={"name": name}
    )
    content = tag.get("content") if isinstance(tag, Tag) else None
    return isinstance(content, str) and bool(content.strip())
