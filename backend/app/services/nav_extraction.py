"""
Extract navigation structure from scraped HTML.

Two extractors, both consumed by ``scraper._parse_rendered_html``:

1. ``extract_nav_links`` — the header navigation as an ordered NavLink tree
   (dropdown items nested as children). This is the site owner's own curation
   of what matters; page_inference uses membership, order, and nesting as
   priority + hierarchy evidence.

2. ``extract_body_link_clusters`` — link-dense blocks living *inside* the
   content container (template menu strips, section subnavs, quick-link rows).
   These pollute text extraction and carry hierarchy signal, but which of the
   two they are can only be decided across pages: a cluster repeated on
   several sibling pages is template chrome, a one-off is content. The
   cross-page pass lives in ``find_repeated_cluster_keys`` /
   ``strip_chrome_lines`` and runs once the crawl is complete.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from itertools import chain
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

from app.models.content_blocks import LinkCluster, NavLink, SectionCandidate, SourceContent
from app.services.site_url import is_same_site

logger = logging.getLogger(__name__)

# Sane bounds for nav-like link collections. Below the floor it's not a menu,
# above the ceiling it's a sitemap/footer dump we don't want as nav evidence.
_MIN_CLUSTER_LINKS = 3
_MAX_CLUSTER_LINKS = 15
_MAX_NAV_ITEMS = 12
_MAX_LABEL_LEN = 60

# How text-dominated by links a body block must be to count as a menu strip
# rather than prose-with-links. 0.6 (not higher) so labeled straps like
# "Current Releases: <a>…</a> <a>…</a>" still qualify — the label eats into
# the ratio but the block is clearly a link strip, not prose.
_MIN_LINK_TEXT_RATIO = 0.6

# Downloadable document extensions — a cluster made entirely of these hrefs is
# a download card (find_document_link_clusters), not a nav strip. Also
# consumed by scraper.py as the document subset of its non-page extensions,
# so the two stay in lockstep.
DOCUMENT_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)

_SKIP_HREF_PREFIXES = ("javascript:", "mailto:", "tel:", "data:")


def site_relative_href(href: str | None, base_url: str) -> str | None:
    """Same-site href → normalized relative path (``/services/web-design`` or
    ``/#pricing``). External, asset, and non-navigational hrefs ⇒ None.
    """
    if not href or not isinstance(href, str):
        return None
    href = href.strip()
    if not href or href.lower().startswith(_SKIP_HREF_PREFIXES):
        return None
    if href == "#":
        # Bare toggle href (dropdown parents) — not a navigable destination.
        # Without this, urljoin would resolve it to the page itself.
        return None
    try:
        absolute = urljoin(base_url, href)
        parsed = urlparse(absolute)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    if not is_same_site(absolute, base_url):
        return None
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    if parsed.fragment:
        return f"{path}#{parsed.fragment}"
    return path


def _anchor_label(a: Tag) -> str:
    text = a.get_text(" ", strip=True)
    if not text:
        # Icon-only anchors: fall back to aria-label / title.
        text = str(a.get("aria-label") or a.get("title") or "").strip()
    return re.sub(r"\s+", " ", text)[:_MAX_LABEL_LEN]


# --- header navigation ------------------------------------------------------------


# Classes a site's own menu walker stamps on every menu item. WordPress core's
# Walker_Nav_Menu and Drupal's menu template both emit "menu-item" — on <li> in
# most themes, but on plain <div>s in page-builder kits that emit no <nav>, no
# <ul> and no role at all (feruni.com's Elementor/LaStudio header: 14 menu links
# in the HTML, zero semantic markup, so the site used to read as having no
# navigation). A positive declaration the markup makes about itself, like
# scraper._PROFILE_CONTAINER_HINTS — never a guess from layout; a data edit
# extends it.
_DECLARED_MENU_ITEM_CLASSES = frozenset({"menu-item"})


def extract_nav_links(soup: BeautifulSoup, base_url: str) -> list[NavLink]:
    """Pull the primary header navigation as an ordered, nested NavLink list.

    Candidates are ``<nav>`` elements and ``role="navigation"`` containers;
    ones inside ``<header>`` win ties. Within the chosen container, top-level
    menu items become NavLinks and each item's nested item list becomes its
    children — the standard dropdown markup. Navs without list markup fall
    back to a flat anchor walk.

    Only when no semantic candidate yields a menu are declared menus read:
    containers of ``_DECLARED_MENU_ITEM_CLASSES`` items, scored the same way.
    Sites whose navigation is already marked up keep exactly what they had.
    """
    for find_candidates in (_semantic_nav_candidates, _declared_menu_roots):
        items = _best_menu(find_candidates(soup), base_url)
        if items:
            return items[:_MAX_NAV_ITEMS]
    return []


def _semantic_nav_candidates(soup: BeautifulSoup) -> list[Tag]:
    candidates: list[Tag] = [t for t in soup.find_all("nav") if isinstance(t, Tag)]
    for t in soup.find_all(attrs={"role": "navigation"}):
        if isinstance(t, Tag) and t.name != "nav" and t not in candidates:
            candidates.append(t)
    return candidates


def _is_declared_menu_item(tag: object) -> bool:
    return isinstance(tag, Tag) and not _DECLARED_MENU_ITEM_CLASSES.isdisjoint(
        tag.get("class") or ()
    )


def _declared_menu_roots(soup: BeautifulSoup) -> list[Tag]:
    """The containers holding each declared menu's TOP-level items, in document
    order — an item nested inside another item belongs to that item's submenu."""
    roots: dict[int, Tag] = {}  # by identity: bs4 Tag equality compares whole subtrees
    for item in soup.find_all(_is_declared_menu_item):
        if item.find_parent(_is_declared_menu_item) is not None:
            continue
        parent = item.parent
        if isinstance(parent, Tag):
            roots.setdefault(id(parent), parent)
    return list(roots.values())


def _best_menu(candidates: list[Tag], base_url: str) -> list[NavLink]:
    """The candidate with the most top-level items (bounded), a header bonus
    first; the earliest wins a tie. Each candidate is parsed once."""
    best: list[NavLink] = []
    best_score = (-1, -1)
    for container in candidates:
        items = _parse_nav_container(container, base_url)
        in_header = container.find_parent("header") is not None
        score = (1 if in_header else 0, min(len(items), _MAX_NAV_ITEMS))
        if score > best_score:
            best, best_score = items, score
    return best


def _is_menu_item(tag: object) -> bool:
    return isinstance(tag, Tag) and (tag.name == "li" or _is_declared_menu_item(tag))


def _menu_list_in(root: Tag, *, include_root: bool = True) -> Tag | None:
    """The first element in ``root`` that directly holds menu items.

    For ``<ul>/<li>`` markup that is the nested ``<ul>``; for declared markup it
    is whatever wrapper the theme puts around its item ``<div>``s. One rule for
    both, so there is one menu parser rather than one per markup style.

    An item looks for its submenu with ``include_root=False``: the item itself
    is the parent of its own anchor, so it must never be mistaken for the
    submenu that anchor is excluded from.
    """
    descendants = (node for node in root.descendants if isinstance(node, Tag))
    for tag in chain((root,), descendants) if include_root else descendants:
        if any(_is_menu_item(child) for child in tag.children):
            return tag
    return None


def _parse_nav_container(nav: Tag, base_url: str) -> list[NavLink]:
    menu_list = _menu_list_in(nav)
    if menu_list is not None:
        items = _parse_menu_list(menu_list, base_url, depth=0)
        if items:
            return items
    # No list markup — flat anchors directly under the nav.
    flat: list[NavLink] = []
    seen: set[str] = set()
    for a in nav.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        href = site_relative_href(str(a.get("href")), base_url)
        label = _anchor_label(a)
        if not href or not label or href in seen:
            continue
        seen.add(href)
        flat.append(NavLink(label=label, href=href))
    return flat


def _parse_menu_list(menu_list: Tag, base_url: str, *, depth: int) -> list[NavLink]:
    if depth > 2:  # guard against pathological nesting
        return []
    items: list[NavLink] = []
    seen: set[str] = set()
    for li in (child for child in menu_list.children if _is_menu_item(child)):
        submenu = _menu_list_in(li, include_root=False)
        # The item's own anchor is any <a> in the item that is NOT inside its
        # submenu.
        anchor: Tag | None = None
        for a in li.find_all("a", href=True):
            if not isinstance(a, Tag):
                continue
            if submenu is not None and any(parent is submenu for parent in a.parents):
                continue
            anchor = a
            break

        children = (
            _parse_menu_list(submenu, base_url, depth=depth + 1)
            if submenu is not None
            else []
        )

        if anchor is not None:
            href = site_relative_href(str(anchor.get("href")), base_url)
            label = _anchor_label(anchor)
        else:
            # Dropdown parents are often <button>/<span> toggles with no href.
            toggle = li.find(("button", "span"), recursive=False)
            label = (
                re.sub(r"\s+", " ", toggle.get_text(" ", strip=True))[:_MAX_LABEL_LEN]
                if isinstance(toggle, Tag)
                else ""
            )
            href = None

        if not label:
            continue
        if href is None and not children:
            continue  # unlabeled/unlinked leaf — noise
        effective_href = href or "#"
        key = f"{effective_href}|{label.lower()}"
        if key in seen:
            continue
        seen.add(key)
        items.append(NavLink(label=label, href=effective_href, children=children))
    return items


# --- social profile links ----------------------------------------------------------


# domain → display label. Order matters only for stable output.
_SOCIAL_DOMAINS: list[tuple[str, str]] = [
    ("facebook.com", "Facebook"),
    ("instagram.com", "Instagram"),
    ("twitter.com", "X"),
    ("x.com", "X"),
    ("linkedin.com", "LinkedIn"),
    ("youtube.com", "YouTube"),
    ("tiktok.com", "TikTok"),
    ("pinterest.com", "Pinterest"),
    ("github.com", "GitHub"),
    ("wa.me", "WhatsApp"),
    ("whatsapp.com", "WhatsApp"),
    ("t.me", "Telegram"),
    ("threads.net", "Threads"),
]

# Share/intent URLs are actions, not profiles — never social-menu material.
_SOCIAL_SHARE_HINTS = ("/sharer", "/share", "/intent", "/plugins/", "shareArticle")


def _social_link_for_href(href: str, base_url: str) -> NavLink | None:
    """The social profile an anchor's href points at, if any.

    Shared by the page-wide and card-scoped extractors below so the platform
    list and share-link exclusions live in one place.
    """
    raw = href.strip()
    if not raw or raw.lower().startswith(_SKIP_HREF_PREFIXES):
        return None
    try:
        absolute = urljoin(base_url, raw)
        parsed = urlparse(absolute)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path or "/"
    if any(hint in absolute for hint in _SOCIAL_SHARE_HINTS):
        return None
    for domain, label in _SOCIAL_DOMAINS:
        if host == domain or host.endswith(f".{domain}"):
            if path in ("", "/") and domain not in ("wa.me", "t.me"):
                return None  # bare platform homepage, not a profile
            return NavLink(label=label, href=absolute)
    return None


def extract_social_links(soup: BeautifulSoup, base_url: str) -> list[NavLink]:
    """Social profile links anywhere on the page — one per platform, max 6.

    First occurrence per platform wins (sites list their real profiles in the
    header/footer before any inline mentions). ``base_url`` resolves
    protocol-relative hrefs.
    """
    found: dict[str, NavLink] = {}
    for a in soup.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        link = _social_link_for_href(str(a.get("href") or ""), base_url)
        if link is not None and link.label not in found:
            found[link.label] = link
        if len(found) >= 6:
            break
    return list(found.values())


def social_links_from_anchors(anchors: list[Tag], base_url: str) -> list[NavLink]:
    """Social profile links among a specific set of anchors — one per platform, max 4.

    Used to scope social-link discovery to one person's card/page rather than
    the whole document, e.g. a team member's own LinkedIn/Instagram link.
    """
    found: dict[str, NavLink] = {}
    for a in anchors:
        href = a.get("href")
        if not isinstance(href, str):
            continue
        link = _social_link_for_href(href, base_url)
        if link is not None and link.label not in found:
            found[link.label] = link
        if len(found) >= 4:
            break
    return list(found.values())


# --- in-body link clusters --------------------------------------------------------


def extract_body_link_clusters(soup: BeautifulSoup, base_url: str) -> list[LinkCluster]:
    """Find nav-shaped link blocks inside the content body.

    Works on a copy with header/nav/footer removed, so anything found here is
    by definition part of the content container. A block qualifies when it
    holds 3–15 same-site links whose combined text dominates the block
    (ratio ≥ 0.7) — i.e. a menu strip, not prose with inline links.
    """
    work = BeautifulSoup(str(soup), "lxml")
    for tag in work.find_all(("header", "nav", "footer", "script", "style", "noscript")):
        tag.decompose()

    clusters: list[LinkCluster] = []
    consumed: set[int] = set()  # id() of anchors already claimed by a cluster
    seen_keys: set[str] = set()

    # Lists first (the common menu-strip markup), then paragraphs (labeled
    # straps like release banners), then generic containers for div-soup
    # templates. Smallest qualifying container wins via `consumed`.
    for container in [
        *work.find_all(("ul", "ol")),
        *work.find_all("p"),
        *work.find_all(("div", "section")),
    ]:
        if not isinstance(container, Tag):
            continue
        anchors = [
            a
            for a in container.find_all("a", href=True)
            if isinstance(a, Tag) and id(a) not in consumed
        ]
        if not (_MIN_CLUSTER_LINKS <= len(anchors) <= _MAX_CLUSTER_LINKS):
            continue

        links: list[NavLink] = []
        anchor_labels: list[str] = []
        link_text_len = 0
        for a in anchors:
            label = _anchor_label(a)
            if label:
                # ALL anchor text counts toward the "made of links" ratio —
                # including external links (e.g. a version number linking to
                # GitHub) that don't become cluster members themselves.
                anchor_labels.append(label)
                link_text_len += len(label)
            href = site_relative_href(str(a.get("href")), base_url)
            if not href or not label:
                continue
            links.append(NavLink(label=label, href=href))
        if len(links) < _MIN_CLUSTER_LINKS:
            continue
        if any(len(link.label) > _MAX_LABEL_LEN - 20 for link in links):
            continue  # long labels ⇒ article/card list, not a menu strip

        total_text = container.get_text(" ", strip=True)
        if not total_text or link_text_len / len(total_text) < _MIN_LINK_TEXT_RATIO:
            continue

        key = cluster_href_key([link.href for link in links])
        if key in seen_keys:
            continue
        seen_keys.add(key)
        for a in anchors:
            consumed.add(id(a))
        clusters.append(
            LinkCluster(
                links=links,
                href_key=key,
                context_label=_cluster_context_label(total_text, anchor_labels),
            )
        )

    return clusters


def _cluster_context_label(total_text: str, anchor_labels: list[str]) -> str:
    """The block's text with every anchor's label removed — e.g. the leading
    "Current Releases:" of a release strap. Empty for pure link lists.

    Tokens without a single alphanumeric character (emoji decorations,
    leftover separators) are dropped too.
    """
    remainder = total_text
    for label in anchor_labels:
        remainder = remainder.replace(label, " ", 1)
    tokens = [t for t in remainder.split() if re.search(r"[a-zA-Z0-9]", t)]
    return " ".join(tokens).strip(" \t|·•:–—-")[:60]


def cluster_href_key(hrefs: list[str]) -> str:
    """Stable identity for a link cluster: hash of the sorted href set."""
    joined = "\n".join(sorted({h.lower() for h in hrefs}))
    return hashlib.sha1(joined.encode("utf-8")).hexdigest()[:16]


# --- cross-page classification ----------------------------------------------------


def find_repeated_cluster_keys(source: SourceContent) -> set[str]:
    """Cluster keys that appear on two or more crawled pages ⇒ template chrome.

    The primary page and every discovered page each contribute their clusters
    once; anything seen twice is part of the template, not page content.
    """
    counts: dict[str, int] = {}
    for page in [source, *source.discovered_pages]:
        for cluster in page.body_link_clusters:
            counts[cluster.href_key] = counts.get(cluster.href_key, 0) + 1
    return {key for key, n in counts.items() if n >= 2}


def find_linkbar_cluster(source: SourceContent) -> LinkCluster | None:
    """A one-off announcement / quick-links strap on the entry page.

    Qualifies when a cluster on the *primary* page is not template chrome
    (not repeated across pages), holds 2–6 links, and either carries a
    context label ("Current Releases:") or is small enough (≤4 links) to be
    a deliberate strap rather than a content list. First match wins —
    extraction order approximates document order, so this favors straps near
    the top of the page.
    """
    chrome_keys = find_repeated_cluster_keys(source)
    for cluster in source.body_link_clusters:
        if cluster.href_key in chrome_keys:
            continue
        if not (2 <= len(cluster.links) <= 6):
            continue
        if not cluster.context_label and len(cluster.links) > 4:
            continue
        if _looks_like_breadcrumb(cluster):
            continue
        return cluster
    return None


def _looks_like_breadcrumb(cluster: LinkCluster) -> bool:
    """A breadcrumb trail masquerading as a strap ("Home › Programs").

    Announcement straps virtually never link back to the homepage; a cluster
    that starts with a homepage link (or whose label/first link is literally
    "Home") is the source page's breadcrumb — recreating it as a linkbar would
    render a redundant second menu under the hero.
    """
    first = cluster.links[0] if cluster.links else None
    if first is not None:
        href_path = first.href.split("#", 1)[0].strip("/").lower()
        if href_path == "" or (first.label or "").strip().lower() == "home":
            return True
    return (cluster.context_label or "").strip().rstrip(":").lower() == "home"


def is_document_href(href: str) -> bool:
    """True when href's path segment (pre-#/?) ends in DOCUMENT_EXTENSIONS.

    Shared by scraper._extract_document_cards (card detection) and
    push_orchestrator's document-rehosting pass — both need the same "is this
    a document link" test.
    """
    path = href.split("#", 1)[0].split("?", 1)[0].lower()
    return path.endswith(DOCUMENT_EXTENSIONS)


def strip_linkbar_lines(source: SourceContent, cluster: LinkCluster) -> None:
    """Remove the strap's text from the entry page's raw_text.

    The structural pass usually captures the strap as one line containing the
    label and every link label — so unlike ``strip_chrome_lines`` we can't
    whole-line-match a single label. Instead, drop any line that is left with
    nearly nothing once the cluster's labels are removed. Mutates in place.
    """
    labels = [link.label for link in cluster.links if link.label]
    if cluster.context_label:
        labels.append(cluster.context_label)
    if not labels:
        return

    def _is_strap_line(line: str) -> bool:
        remainder = line
        matched = 0
        for label in labels:
            replaced = re.sub(
                re.escape(label), " ", remainder, count=1, flags=re.IGNORECASE
            )
            if replaced != remainder:
                matched += 1
                remainder = replaced
        # Only lines that are essentially *made of* the strap's labels (at
        # least two of them, nothing else left) are dropped — a short
        # unrelated line must never match.
        return matched >= 2 and len(re.sub(r"[^a-z0-9]", "", remainder.lower())) < 5

    source.raw_text = "\n".join(
        line for line in source.raw_text.split("\n") if not _is_strap_line(line)
    )


# A section seen on this many crawled pages is template furniture — the same
# threshold, and the same reasoning, as find_repeated_cluster_keys.
_CHROME_SECTION_MIN_PAGES = 2


def _section_identity(section: SectionCandidate) -> tuple[str, str, tuple[str, ...], tuple[str, ...]] | None:
    """What a section IS for chrome purposes: its heading AND what it holds.

    A heading alone is a template's label, not its furniture. A catalogue's
    product pages all say "Product Specification" and "2 Collections" over
    different content — keyed on the heading, every product lost its content as
    "chrome" (feruni.com's modular pages kept only their title). A footer widget
    repeats its words and pictures too.
    """
    heading = section.heading.strip().lower()
    if not heading:
        return None
    return (
        heading,
        " ".join(section.prose.split()),
        tuple(section.image_urls),
        tuple(card.title for card in section.cards),
    )


def strip_chrome_sections(source: SourceContent) -> None:
    """Drop section candidates that repeat, heading and content, across pages.

    Same reasoning as ``strip_chrome_lines``, applied to the section tree: a
    section that appears on every page is the template's, not the page's. The
    tree is built per page during parsing, where the repetition is invisible —
    and ``_in_chrome`` only recognises chrome that says so in its tags, which a
    hand-built site whose footer is a plain ``<section>`` never does. Those
    footer widgets then read as ordinary sections and were handed to the
    planner as page content. Mutates in place; needs two pages to conclude
    anything, so a single-page crawl is left alone.

    Only ever removes a DUPLICATE: the same heading over the same content has to
    appear on more than one page (``_section_identity``), so a genuine section is
    safe even when it shares a name with one.
    """
    pages = [source, *source.discovered_pages]
    if len(pages) < _CHROME_SECTION_MIN_PAGES:
        return
    seen: Counter[tuple] = Counter()
    for page in pages:
        for identity in {_section_identity(section) for section in page.section_candidates} - {None}:
            seen[identity] += 1
    chrome = {key for key, n in seen.items() if n >= _CHROME_SECTION_MIN_PAGES}
    if not chrome:
        return
    for page in pages:
        kept = [
            section
            for section in page.section_candidates
            if _section_identity(section) not in chrome
        ]
        # Never strip a page down to nothing. If every section it has looks
        # repeated, the repetition is the page's own content being served at a
        # second URL — an alias, a print view — and the right answer is to keep
        # it rather than hand the planner an empty page.
        if kept or not page.section_candidates:
            page.section_candidates = kept


def strip_chrome_lines(source: SourceContent) -> None:
    """Remove repeated-cluster link labels from every page's raw_text.

    The structural text pass keeps menu-strip ``<li>`` text because it can't
    know the block is chrome until the crawl finishes. Here we drop raw_text
    lines that exactly match a chrome cluster's link label — only whole-line
    matches, so prose that merely mentions a label survives. Mutates in place.
    """
    chrome_keys = find_repeated_cluster_keys(source)
    if not chrome_keys:
        return
    chrome_labels: set[str] = set()
    for page in [source, *source.discovered_pages]:
        for cluster in page.body_link_clusters:
            if cluster.href_key in chrome_keys:
                chrome_labels.update(link.label.lower() for link in cluster.links)
    if not chrome_labels:
        return
    for page in [source, *source.discovered_pages]:
        kept = [
            line
            for line in page.raw_text.split("\n")
            if line.strip().lower() not in chrome_labels
        ]
        page.raw_text = "\n".join(kept)
