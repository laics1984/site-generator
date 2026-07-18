"""
httpx-first fetch path. Tries a plain HTTP GET before launching Playwright.

Why: ~80% of real marketing sites are static HTML or server-rendered (SSR'd
Next.js, Astro, Hugo, Jekyll, Wordpress, etc.) — for those, the rendered HTML
is sitting in the first response body. We don't need a browser to read it.

When the page IS a JS shell (SPA without SSR), httpx returns a near-empty
document. We detect that with a content-substance heuristic and fall back to
Playwright.

Speed: typical httpx fetch + parse is ~250-600ms vs Playwright's 3-5s.
On a 20-page crawl that's the difference between ~12s and ~70s of fetching.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx
import trafilatura
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


# Reused from scraper.py. Imported there too — keep in sync if any change.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

_HTTPX_HEADERS = {
    "User-Agent": _BROWSER_USER_AGENT,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    # Deliberately omit `br` — httpx ships without a Brotli decoder by default,
    # and advertising support yields raw-br bytes we can't parse. gzip+deflate
    # is enough for ~99% of sites.
    "Accept-Encoding": "gzip, deflate",
    "Upgrade-Insecure-Requests": "1",
}


# Minimum text length (chars after trafilatura) that signals a substantive page.
_SUBSTANTIVE_TEXT_THRESHOLD = 500


@dataclass
class FastFetchResult:
    """Output of a successful httpx fetch."""

    final_url: str
    html: str
    http_status: int


class FastFetchSkipReason:
    """Sentinel reasons httpx fetch wasn't usable — caller falls through to Playwright."""

    HTTP_ERROR = "http_error"
    NON_HTML = "non_html"
    JS_SHELL = "js_shell"            # HTML returned but no real content (SPA)
    TOO_SHORT = "too_short"           # HTML returned but text < threshold
    EXCEPTION = "exception"


@dataclass
class FastFetchSkipped:
    """Returned when httpx can't satisfy the request. Caller falls back."""

    reason: str
    http_status: int | None = None
    detail: str | None = None


async def try_fast_fetch(
    url: str,
    *,
    timeout_seconds: float = 8.0,
) -> FastFetchResult | FastFetchSkipped:
    """
    Attempt to fetch + judge the URL via plain httpx. Returns either:
      - FastFetchResult — page has substantive content, parse downstream as usual
      - FastFetchSkipped — caller should fall back to Playwright

    Never raises. All exceptions become FastFetchSkipped(EXCEPTION).
    """
    try:
        async with httpx.AsyncClient(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers=_HTTPX_HEADERS,
        ) as client:
            response = await client.get(url)
    except httpx.HTTPError as exc:
        logger.debug("httpx fetch failed for %s: %s", url, exc)
        return FastFetchSkipped(reason=FastFetchSkipReason.EXCEPTION, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.debug("httpx fetch unexpected error %s: %s", url, exc)
        return FastFetchSkipped(reason=FastFetchSkipReason.EXCEPTION, detail=str(exc))

    if response.status_code >= 400:
        return FastFetchSkipped(
            reason=FastFetchSkipReason.HTTP_ERROR, http_status=response.status_code
        )

    content_type = (response.headers.get("content-type") or "").lower()
    if "html" not in content_type and not response.text.lstrip().startswith("<"):
        return FastFetchSkipped(
            reason=FastFetchSkipReason.NON_HTML,
            http_status=response.status_code,
            detail=content_type,
        )

    html = response.text
    if not html:
        return FastFetchSkipped(
            reason=FastFetchSkipReason.TOO_SHORT, http_status=response.status_code
        )

    # The substance judgment is pure CPU (trafilatura + several lxml parses) —
    # run it in a worker thread so the crawl pool's other fetches keep moving.
    skipped = await asyncio.to_thread(_judge_html, html, response.status_code)
    if skipped is not None:
        return skipped

    return FastFetchResult(
        final_url=str(response.url),
        html=html,
        http_status=response.status_code,
    )


def _judge_html(html: str, http_status: int) -> FastFetchSkipped | None:
    """CPU-heavy substance judgment. Returns the skip verdict, or None to accept.

    Accept if EITHER:
      - We have substantive text AND substantive markup (typical SSR site)
      - We have very strong markup (lots of headings + nav links) even if
        text is thin — small static pages like example.com or /contact

    Reject when markup is missing entirely: a React shell can produce 20k+
    chars of div-soup text yet still lack <title>, <h*>, and <a href> — we
    can't usefully crawl that without Playwright, so fall back.
    """
    if _looks_like_js_shell(html):
        return FastFetchSkipped(
            reason=FastFetchSkipReason.JS_SHELL, http_status=http_status
        )

    text = trafilatura.extract(html, favor_recall=True) or ""
    text_len = len(text.strip())
    markup_ok = _has_substantive_markup(html)

    if markup_ok and text_len >= _SUBSTANTIVE_TEXT_THRESHOLD:
        return None
    if _has_strong_markup(html):
        # Very small but well-formed page (e.g. example.com, /contact).
        return None

    return FastFetchSkipped(
        reason=FastFetchSkipReason.JS_SHELL if not markup_ok else FastFetchSkipReason.TOO_SHORT,
        http_status=http_status,
        detail=f"text={text_len} markup_ok={markup_ok}",
    )


_JS_SHELL_MARKERS = (
    "you need to enable javascript",
    "please enable javascript",
    "this site requires javascript",
    "javascript is disabled",
)


def _looks_like_js_shell(html: str) -> bool:
    """Quick sniff: pages that explicitly say "enable JavaScript" or have
    only a single empty <div id=root> are SPA shells. We could be more
    sophisticated (count nodes, etc.) but this catches the obvious cases."""
    lowered = html.lower()
    if any(marker in lowered for marker in _JS_SHELL_MARKERS):
        return True
    # Heuristic for empty root pattern: very small body + a single root div.
    if "<body" in lowered:
        body_start = lowered.find("<body")
        body_end = lowered.find("</body", body_start)
        if body_start != -1 and body_end != -1:
            body = html[body_start:body_end]
            # If body is < 800 chars AND contains <div id="root" / __next> with no children
            if len(body) < 800 and (
                'id="root"' in body or 'id="__next"' in body or 'id="app"' in body
            ):
                return True
    return False


def _has_real_body_content(html: str) -> bool:
    """Returns True if the body has any meaningful non-script element."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        return False
    body = soup.body
    if not body:
        return False
    # Drop scripts/styles/noscripts and count remaining text-ish tags.
    for tag in body.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    remaining_text = body.get_text(strip=True)
    if len(remaining_text) > 200:
        return True
    # Or: at least one paragraph/heading/list/article — proves it's a content
    # page, not just a navigation shell.
    content_tags = body.find_all(["p", "article", "h1", "h2", "h3", "li"])
    return len(content_tags) >= 3


def _has_substantive_markup(html: str) -> bool:
    """
    Stricter check than _has_real_body_content: returns True only when the HTML
    has the *semantic markup* we actually need downstream (title, headings, or
    navigable anchors). Defeats React-shell pages that render 20k+ chars of
    div-soup text but no real HTML structure.

    A page passes if it has at least ONE of:
      - <title> (or <meta property="og:title">) with non-empty text
      - any <h1>/<h2>/<h3>
      - at least 3 <a href> tags (real navigability)
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        return False

    # Title check (also tolerates SSR'd og:title in <head>).
    title_text = ""
    if soup.title and soup.title.get_text(strip=True):
        title_text = soup.title.get_text(strip=True)
    if not title_text:
        for prop in ("og:title", "twitter:title"):
            meta = soup.find("meta", attrs={"property": prop}) or soup.find(
                "meta", attrs={"name": prop}
            )
            if meta and isinstance(meta.get("content"), str) and meta["content"].strip():
                title_text = meta["content"].strip()
                break
    if title_text:
        return True

    # Headings — strong signal of SEO-conscious markup.
    if soup.find(["h1", "h2", "h3"]):
        return True

    # Navigable anchors — even without headings, a real nav has multiple links.
    anchors = [a for a in soup.find_all("a") if a.get("href")]
    return len(anchors) >= 3


def _has_strong_markup(html: str) -> bool:
    """
    Stricter signal than _has_substantive_markup: accepts even short pages
    when they're CLEARLY well-formed HTML (title + heading or title + links).
    Used to rescue legitimately small pages (example.com, /contact) from the
    text-length threshold.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:  # noqa: BLE001
        return False
    has_title = bool(soup.title and soup.title.get_text(strip=True))
    has_heading = bool(soup.find(["h1", "h2"]))
    anchors = len([a for a in soup.find_all("a") if a.get("href")])
    # Title + heading is a clear content page; title + 2 links is a clear nav.
    return has_title and (has_heading or anchors >= 2)
