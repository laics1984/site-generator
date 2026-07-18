"""
Extract the source site's blog posts and events into CMS-ready entries.

The main crawl (scraper.py) captures listing pages; this module goes one level
deeper: it finds the post/event detail URLs behind a blog or events listing,
fetches each one, and pulls out the fields the CMS needs to create real
article/event entries (title, date, excerpt, body HTML, cover image).

Extraction is deterministic — no LLM. Entry content is copied verbatim from
the source, so the fidelity rules that govern generated sections don't apply
here. Priority per page:
  1. JSON-LD (schema.org Article / BlogPosting / NewsArticle / Event)
  2. OpenGraph / meta tags + <time datetime> + URL date patterns
  3. Heuristics (main content container, first content image)
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from app.models.content_blocks import (
    ArticleEntry,
    ContentCollections,
    EventEntry,
    PageType,
    SourceContent,
)
from app.services.fast_fetch import FastFetchResult, try_fast_fetch
from app.services.page_inference import _infer_page_type

logger = logging.getLogger(__name__)

__all__ = [
    "ArticleEntry",
    "ContentCollections",
    "EventEntry",
    "extract_collections",
]


# --- candidate URL discovery -------------------------------------------------------


# Listing sub-paths that are navigation, not content: pagination, taxonomy
# archives, feeds. Never treated as detail pages.
_NON_DETAIL_SEGMENTS = (
    "page", "category", "categories", "tag", "tags", "author", "authors",
    "archive", "archives", "feed", "rss", "search",
)

_URL_DATE_RE = re.compile(r"/(19|20)\d{2}/\d{1,2}(/|$)")

# Per-site bound on Playwright fallbacks (each launches its own browser).
_MAX_RENDER_FALLBACKS = 4

_FETCH_CONCURRENCY = 4


def _path_of(url: str) -> str:
    return (urlparse(url).path or "/").rstrip("/") or "/"


def _is_detail_path(path: str, listing_path: str) -> bool:
    """True when `path` looks like a content page nested under `listing_path`."""
    if path == listing_path or listing_path == "/":
        under = path.count("/") >= 1 and path != "/"
        prefix_ok = _URL_DATE_RE.search(path) is not None
        if not (under and prefix_ok):
            return False
    elif not path.startswith(listing_path + "/"):
        # WordPress-style dated permalinks often live off the site root even
        # when the listing is /blog — accept those too.
        if not _URL_DATE_RE.search(path):
            return False
    rel = path[len(listing_path):].strip("/") if path.startswith(listing_path) else path.strip("/")
    segments = [s for s in rel.split("/") if s]
    if not segments:
        return False
    if any(seg.lower() in _NON_DETAIL_SEGMENTS for seg in segments):
        return False
    return True


def _listing_pages(source: SourceContent) -> dict[PageType, list[SourceContent]]:
    """Discovered pages classified as blog / events listings."""
    out: dict[PageType, list[SourceContent]] = {"blog": [], "events": []}
    for page in source.discovered_pages or []:
        slug = (page.url_path or "").strip("/").lower()
        if not slug or "/" in slug:
            continue
        page_type = _infer_page_type(slug, page.title or "")
        if page_type in ("blog", "events"):
            out[page_type].append(page)
    return out


def _candidate_urls(
    listing: SourceContent,
    source: SourceContent,
    extra_urls: list[str],
) -> list[str]:
    """Detail-page URLs behind one listing page, in first-seen order."""
    listing_host = urlparse(listing.source_ref).netloc
    listing_path = _path_of(listing.source_ref)

    seen: set[str] = set()
    ordered: list[str] = []

    def consider(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return
        if parsed.netloc and parsed.netloc != listing_host:
            return
        path = _path_of(url)
        if not _is_detail_path(path, listing_path):
            return
        key = f"{parsed.netloc}{path}"
        if key in seen:
            return
        seen.add(key)
        ordered.append(url.split("#", 1)[0])

    for url in listing.links or []:
        consider(url)
    for page in source.discovered_pages or []:
        if page.source_ref:
            consider(page.source_ref)
    for url in extra_urls:
        consider(url)
    return ordered


# --- per-page extraction -------------------------------------------------------------


_ARTICLE_LD_TYPES = {"article", "blogposting", "newsarticle"}
_EVENT_LD_TYPES = {
    "event", "socialevent", "businessevent", "educationevent", "festival",
    "exhibitionevent", "musicevent", "sportsevent", "theaterevent",
}


def _parse_dt(value: object) -> datetime | None:
    """Parse an ISO-ish date/datetime string → aware UTC datetime."""
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip()
    candidates = [raw, raw.replace("Z", "+00:00")]
    for cand in candidates:
        try:
            dt = datetime.fromisoformat(cand)
            break
        except ValueError:
            dt = None
    if dt is None:
        for fmt in ("%Y-%m-%d %H:%M:%S", "%d %B %Y", "%B %d, %Y", "%d %b %Y", "%b %d, %Y"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _date_from_url(url: str) -> datetime | None:
    m = re.search(r"/((?:19|20)\d{2})/(\d{1,2})(?:/(\d{1,2}))?(?:/|$)", urlparse(url).path)
    if not m:
        return None
    year, month = int(m.group(1)), int(m.group(2))
    day = int(m.group(3)) if m.group(3) else 1
    try:
        return datetime(year, month, min(day, 28) if day > 28 else day, tzinfo=timezone.utc)
    except ValueError:
        return None


def _iter_ld_nodes(soup: BeautifulSoup):
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


def _ld_types(node: dict) -> set[str]:
    raw = node.get("@type")
    if isinstance(raw, str):
        return {raw.lower()}
    if isinstance(raw, list):
        return {t.lower() for t in raw if isinstance(t, str)}
    return set()


def _ld_image(node: dict) -> str | None:
    img = node.get("image")
    if isinstance(img, list) and img:
        img = img[0]
    if isinstance(img, dict):
        img = img.get("url") or img.get("contentUrl")
    return img if isinstance(img, str) and img.strip() else None


def _ld_location(node: dict) -> str | None:
    loc = node.get("location")
    if isinstance(loc, list) and loc:
        loc = loc[0]
    if isinstance(loc, str):
        return loc.strip() or None
    if isinstance(loc, dict):
        name = loc.get("name") if isinstance(loc.get("name"), str) else None
        addr = loc.get("address")
        if isinstance(addr, dict):
            parts = [
                addr.get(k)
                for k in ("streetAddress", "addressLocality", "addressRegion")
                if isinstance(addr.get(k), str) and addr.get(k).strip()
            ]
            addr = ", ".join(parts) if parts else None
        if not isinstance(addr, str):
            addr = None
        if name and addr:
            return f"{name}, {addr}"
        return name or addr
    return None


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if tag and isinstance(tag.get("content"), str) and tag["content"].strip():
            return tag["content"].strip()
    return None


_BODY_KEEP_TAGS = {
    "p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "li",
    "strong", "em", "b", "i", "u", "a", "img", "blockquote", "br",
    "figure", "figcaption", "table", "thead", "tbody", "tr", "th", "td",
}
_BODY_DROP_TAGS = {"script", "style", "nav", "footer", "header", "form", "aside", "iframe", "noscript", "button", "svg"}
_KEEP_ATTRS = {"a": {"href"}, "img": {"src", "alt"}}


def _main_container(soup: BeautifulSoup) -> Tag | None:
    for selector in ("article", "main", "[role=main]"):
        node = soup.select_one(selector)
        if node and len(node.get_text(strip=True)) > 80:
            return node
    # Largest text-bearing block as a last resort.
    best: tuple[int, Tag] | None = None
    for div in soup.find_all("div"):
        length = len(div.get_text(strip=True))
        if best is None or length > best[0]:
            best = (length, div)
    return best[1] if best else None


def _sanitize_body(container: Tag, base_url: str) -> str:
    for tag in container.find_all(list(_BODY_DROP_TAGS)):
        tag.decompose()
    for tag in list(container.find_all(True)):
        if tag.name not in _BODY_KEEP_TAGS:
            tag.unwrap()
            continue
        keep = _KEEP_ATTRS.get(tag.name, set())
        for attr in list(tag.attrs):
            if attr not in keep:
                del tag.attrs[attr]
        if tag.name == "img":
            src = tag.get("src") or ""
            if not src or src.startswith("data:"):
                tag.decompose()
                continue
            if src.startswith("/"):
                parsed = urlparse(base_url)
                tag["src"] = f"{parsed.scheme}://{parsed.netloc}{src}"
        if tag.name == "a":
            href = tag.get("href") or ""
            if href.startswith("/"):
                parsed = urlparse(base_url)
                tag["href"] = f"{parsed.scheme}://{parsed.netloc}{href}"
    html = container.decode_contents().strip()
    # Collapse runs of blank text the unwrapping leaves behind.
    return re.sub(r"\n{3,}", "\n\n", html)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "entry"


def _slug_from_url(url: str) -> str:
    tail = (urlparse(url).path or "").rstrip("/").rsplit("/", 1)[-1]
    return _slugify(tail) if tail else "entry"


def _clean_title(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if h1 and h1.get_text(strip=True):
        return h1.get_text(" ", strip=True)
    title = soup.find("title")
    if title and title.get_text(strip=True):
        raw = title.get_text(strip=True)
        for sep in (" | ", " — ", " – ", " - "):
            if sep in raw:
                raw = raw.split(sep, 1)[0].strip()
                break
        return raw or None
    return None


def _first_content_image(container: Tag | None, base_url: str) -> str | None:
    if container is None:
        return None
    for img in container.find_all("img"):
        src = img.get("src") or ""
        if not src or src.startswith("data:"):
            continue
        if src.startswith("/"):
            parsed = urlparse(base_url)
            src = f"{parsed.scheme}://{parsed.netloc}{src}"
        return src
    return None


def _excerpt_from(soup: BeautifulSoup, body_text: str) -> str:
    meta = _meta(soup, "og:description", "description")
    if meta:
        return meta[:300]
    text = re.sub(r"\s+", " ", body_text).strip()
    return text[:200] + ("…" if len(text) > 200 else "") if text else ""


def _extract_entry(
    html: str, url: str, kind: PageType
) -> ArticleEntry | EventEntry | None:
    """Parse one detail page. Returns None when no usable title was found."""
    soup = BeautifulSoup(html, "html.parser")

    ld_article: dict | None = None
    ld_event: dict | None = None
    for node in _iter_ld_nodes(soup):
        types = _ld_types(node)
        if ld_event is None and types & _EVENT_LD_TYPES:
            ld_event = node
        elif ld_article is None and types & _ARTICLE_LD_TYPES:
            ld_article = node

    # JSON-LD type beats the listing's classification — a "news" page can list
    # schema.org Events and vice versa.
    if ld_event is not None:
        kind = "events"
    elif ld_article is not None:
        kind = "blog"

    ld = ld_event if kind == "events" else ld_article

    title = None
    if ld:
        for key in ("headline", "name"):
            if isinstance(ld.get(key), str) and ld[key].strip():
                title = ld[key].strip()
                break
    title = title or _meta(soup, "og:title") or _clean_title(soup)
    if not title:
        return None

    container = _main_container(soup)
    body_html = _sanitize_body(container, url) if container else ""
    body_text = BeautifulSoup(body_html, "html.parser").get_text(" ", strip=True)
    if len(body_text) < 40 and not ld:
        return None  # navigation stub / empty shell — not a real detail page

    image_url = (
        (_ld_image(ld) if ld else None)
        or _meta(soup, "og:image")
        or _first_content_image(container, url)
    )
    excerpt = ""
    if ld and isinstance(ld.get("description"), str):
        excerpt = ld["description"].strip()[:300]
    excerpt = excerpt or _excerpt_from(soup, body_text)
    slug = _slug_from_url(url)
    if slug == "entry":
        slug = _slugify(title)

    if kind == "events":
        start = _parse_dt(ld.get("startDate")) if ld else None
        end = _parse_dt(ld.get("endDate")) if ld else None
        if start is None:
            time_tag = soup.find("time", attrs={"datetime": True})
            start = _parse_dt(time_tag["datetime"]) if time_tag else None
        return EventEntry(
            title=title,
            slug=slug,
            excerpt=excerpt or title,
            body_html=body_html or f"<p>{excerpt or title}</p>",
            start=start,
            end=end,
            location=_ld_location(ld) if ld else None,
            image_url=image_url,
            source_url=url,
        )

    published = _parse_dt(ld.get("datePublished")) if ld else None
    if published is None:
        published = _parse_dt(_meta(soup, "article:published_time", "date"))
    if published is None:
        time_tag = soup.find("time", attrs={"datetime": True})
        published = _parse_dt(time_tag["datetime"]) if time_tag else None
    if published is None:
        published = _date_from_url(url)
    return ArticleEntry(
        title=title,
        slug=slug,
        excerpt=excerpt or title,
        body_html=body_html or f"<p>{excerpt or title}</p>",
        published_at=published,
        image_url=image_url,
        source_url=url,
    )


# --- fetching ---------------------------------------------------------------------


async def _fetch_html(url: str, render_budget: list[int]) -> str | None:
    fast = await try_fast_fetch(url)
    if isinstance(fast, FastFetchResult):
        return fast.html
    if render_budget[0] <= 0:
        logger.debug("collections: render budget exhausted, skipping %s", url)
        return None
    render_budget[0] -= 1
    try:
        from app.services.scraper import _fetch_rendered_html

        html, _final = await _fetch_rendered_html(url)
        return html
    except Exception as exc:  # noqa: BLE001 — one bad page never kills the batch
        logger.warning("collections: render fallback failed for %s: %s", url, exc)
        return None


# --- main entry -------------------------------------------------------------------


async def extract_collections(
    source: SourceContent,
    *,
    max_entries: int = 12,
    extra_urls: list[str] | None = None,
) -> ContentCollections:
    """Find and extract article/event entries behind the source's listings.

    ``extra_urls`` — unvisited URLs from the crawl frontier (ScrapeResult
    .unvisited_urls); listing links usually cover everything, this catches
    posts only reachable from e.g. a sitemap.
    """
    listings = _listing_pages(source)
    collections = ContentCollections()
    if not listings["blog"] and not listings["events"]:
        return collections

    sem = asyncio.Semaphore(_FETCH_CONCURRENCY)
    render_budget = [_MAX_RENDER_FALLBACKS]

    async def process(url: str, kind: PageType) -> ArticleEntry | EventEntry | None:
        async with sem:
            html = await _fetch_html(url, render_budget)
        if not html:
            return None
        try:
            # BS4 parse is CPU-bound and this task runs concurrently with the
            # render loop — thread it off instead of blocking the event loop.
            return await asyncio.to_thread(_extract_entry, html, url, kind)
        except Exception as exc:  # noqa: BLE001
            logger.warning("collections: extraction failed for %s: %s", url, exc)
            return None

    tasks: list[tuple[PageType, asyncio.Task]] = []
    seen_urls: set[str] = set()
    # Over-fetch a little so entries rejected as stubs don't shrink the final
    # set below the cap.
    fetch_cap = max_entries + max(4, max_entries // 2)
    for kind in ("blog", "events"):
        budget = fetch_cap
        for listing in listings[kind]:
            for url in _candidate_urls(listing, source, list(extra_urls or [])):
                if url in seen_urls or budget <= 0:
                    continue
                seen_urls.add(url)
                budget -= 1
                tasks.append((kind, asyncio.ensure_future(process(url, kind))))

    for kind, task in tasks:
        entry = await task
        if entry is None:
            continue
        if isinstance(entry, EventEntry):
            collections.events.append(entry)
        else:
            collections.articles.append(entry)

    _dedupe_slugs(collections.articles)
    _dedupe_slugs(collections.events)
    _EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
    collections.articles.sort(
        key=lambda a: a.published_at or _EPOCH, reverse=True
    )
    # Events: soonest upcoming first (undated last) — matches the builder's
    # "Upcoming Events" framing better than reverse-chronological.
    _FAR_FUTURE = datetime(9999, 1, 1, tzinfo=timezone.utc)
    collections.events.sort(key=lambda e: e.start or _FAR_FUTURE)
    collections.articles = collections.articles[:max_entries]
    collections.events = collections.events[:max_entries]

    logger.info(
        "collections: extracted %d article(s), %d event(s) from source",
        len(collections.articles),
        len(collections.events),
    )
    return collections


def _dedupe_slugs(entries: list) -> None:
    seen: dict[str, int] = {}
    for entry in entries:
        base = entry.slug
        if base in seen:
            seen[base] += 1
            entry.slug = f"{base}-{seen[base]}"
        else:
            seen[base] = 1
