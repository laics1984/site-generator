"""
URL scraper. Headless Chromium (via Playwright) → rendered HTML →
- main text via trafilatura (boilerplate-stripped)
- headings, links, images via BeautifulSoup
- brand candidates: favicon, og:image, apple-touch-icon, logo-named images

Returns a `ScrapeResult` carrying a normalized `SourceContent` for the LLM
pipeline plus an optional `BrandIdentity` candidate so the frontend can
auto-populate the Brand panel.

Design notes:
- We do not OCR images, summarise, or call any LLM here. Pure extraction.
- Image candidates are filtered (no tracking pixels, no tiny icons) and
  *categorised by likely intent* so the schema_builder gets better matches.
- On the Playwright path, the render stamps measured geometry onto every
  image (_stamp_render_evidence); services/image_evidence.py turns that into
  visual roles (hero/background/content/gallery/portrait/decoration), which
  replace the DOM-order intent guesses and exclude decorations entirely.
- robots.txt is honoured by default; pass `respect_robots=False` to bypass.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import urllib.robotparser
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from urllib.parse import urljoin, urlparse

import httpx
import trafilatura
from bs4 import BeautifulSoup, Tag
from playwright.async_api import async_playwright

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import (
    DocumentCardCandidate,
    DocumentCardLink,
    ImageMetadata,
    NavLink,
    ProfileCandidate,
    SourceContent,
)
from app.services.url_guard import UnsafeUrlError, assert_public_url, is_public_url
from app.services.timing import stage
from app.services.fast_fetch import (
    FastFetchResult,
    FastFetchSkipped,
    try_fast_fetch,
)
from app.services.image_evidence import ImageEvidence, classify_role, parse_evidence
from app.services.locale import AMBIGUOUS_LOCALE_SEGMENTS, locale_segment
from app.services.profile_text import has_contact_token, is_boilerplate_line
from app.services.logo import extract_palette_from_image_bytes
from app.services.nav_extraction import (
    DOCUMENT_EXTENSIONS,
    extract_body_link_clusters,
    extract_nav_links,
    extract_social_links,
    is_document_href,
    social_links_from_anchors,
    strip_chrome_lines,
)
from app.services.polite import RETRIABLE_STATUS_CODES, get_politeness

logger = logging.getLogger(__name__)


# --- public types ---------------------------------------------------------------


@dataclass
class ImageCandidate:
    """One image candidate found on the page, with an intent guess."""

    url: str
    alt: str
    width: int | None
    height: int | None
    intent: str  # 'hero' | 'about' | 'logo' | 'generic'
    # Visual role measured from render evidence (image_evidence.classify_role).
    # 'unknown' when the page came through the httpx fast path (no stamps).
    role: str = "unknown"
    evidence: ImageEvidence | None = None
    # How the source site used this image: 'css_background' when it came from a
    # CSS background-image (stamped attr, inline style or <style> block),
    # 'inline' for <img>/og:image. Downstream, css_background images are kept
    # out of side/featured slots and pinned to full-bleed background slots.
    source_usage: str = "inline"
    # Nearest preceding heading text — ties the image back to the source
    # section it illustrated. Feeds the planner prompt (image_ref binding)
    # and the matcher's lexical scoring.
    context_heading: str = ""
    # <figcaption> text when the image sits inside a <figure>.
    caption: str = ""


@dataclass
class ScrapeResult:
    """Full scrape output, frontend-friendly."""

    url: str
    final_url: str  # after redirects
    source_content: SourceContent
    brand_candidate: BrandIdentity | None
    image_candidates: list[ImageCandidate]
    fetched_at: float = field(default_factory=time.time)
    # URLs the BFS frontier had ready but didn't fetch because max_pages was
    # reached. Frontend uses these to offer "Crawl N more" without restarting.
    unvisited_urls: list[str] = field(default_factory=list)


class ScrapeError(Exception):
    """User-facing scrape failure with a clear status code."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# --- robots.txt -----------------------------------------------------------------


_ROBOTS_CACHE: dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]] = {}
_ROBOTS_TTL = 600  # 10 minutes


async def _robots_allows(url: str, user_agent: str) -> bool:
    """Fetch+parse robots.txt for the host and check if `url` is allowed."""
    parsed = urlparse(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    cached = _ROBOTS_CACHE.get(base)
    now = time.time()
    if cached and (now - cached[0]) < _ROBOTS_TTL:
        rp = cached[1]
        return rp.can_fetch(user_agent, url) if rp else True

    rp: urllib.robotparser.RobotFileParser | None = urllib.robotparser.RobotFileParser()
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.get(f"{base}/robots.txt")
            if resp.status_code == 200 and resp.text:
                assert rp is not None
                rp.parse(resp.text.splitlines())
            else:
                rp = None
    except httpx.HTTPError:
        rp = None  # treat as permissive

    _ROBOTS_CACHE[base] = (now, rp)
    return rp.can_fetch(user_agent, url) if rp else True


# --- HTML fetch -----------------------------------------------------------------


# Realistic Chrome-on-macOS UA. Many enterprise sites (Cloudflare/Akamai-fronted)
# 403 anything that looks like a bot — `WebtreeSiteGenerator/x.y` would fail on
# the first request. We still respect robots.txt and rate limits; the UA just
# stops naive blocklist matching from rejecting us at the door.
# Single source of truth in config (shared with the httpx fast-fetch path).
BROWSER_USER_AGENT = settings.http_user_agent

# Used for the robots.txt check only — that endpoint isn't gated by WAFs.
USER_AGENT = "WebtreeSiteGenerator/0.2 (+contact: hello@example.com)"

# Headers a real Chrome on macOS sends. Many WAFs flag requests missing these.
_BROWSER_HEADERS = {
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Ch-Ua": '"Chromium";v="128", "Not;A=Brand";v="24", "Google Chrome";v="128"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"macOS"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

# Patched into every page on context creation to mask the most-obvious Playwright
# tell. Doesn't beat sophisticated stealth detection but clears most checks.
_STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5].map(() => ({}))
});
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-US', 'en']
});
window.chrome = window.chrome || { runtime: {} };
"""


async def _route_block_heavy(route, request) -> None:
    """Block media/font/websocket resources to speed up renders."""
    if request.resource_type in {"media", "font", "websocket"}:
        await route.abort()
    else:
        await route.continue_()


async def _autoscroll(page, *, max_steps: int = 12, step_px: int = 1200) -> None:
    """Scroll to the bottom in increments to trigger lazy-loaded content.

    Bails out early once the scroll height stops growing, and is wrapped so a
    flaky page never aborts the render — a non-scrolled snapshot still beats no
    snapshot.
    """
    try:
        prev_height = 0
        for _ in range(max_steps):
            height = await page.evaluate("document.body.scrollHeight")
            await page.evaluate(f"window.scrollBy(0, {step_px})")
            await page.wait_for_timeout(150)
            if height <= prev_height:
                break
            prev_height = height
        await page.evaluate("window.scrollTo(0, 0)")
    except Exception:
        pass


async def _stamp_render_evidence(page) -> None:
    """Expose render-time visual evidence to the static BeautifulSoup parser.

    Two kinds of stamps (parsed by services/image_evidence.py):
    - every <img> gets `data-webtree-evidence`: natural size, layout box,
      viewport size, and how many similar-size sibling images share its grid;
    - every large CSS-background element gets `data-webtree-bg-image` (the
      resolved URL, as before) plus `data-webtree-bg-evidence` with the layout
      box and the length of text rendered inside it (text over a background
      marks the image as a backdrop, not content).

    Runs after _autoscroll scrolled back to the top, so scrollX/scrollY are ~0
    and document coordinates equal viewport-relative ones.
    """
    try:
        await page.evaluate(
            """
            () => {
              const vw = Math.max(1, window.innerWidth);
              const vh = Math.max(1, window.innerHeight);
              const extractUrl = (value) => {
                if (!value || value === 'none') return null;
                const match = value.match(/url\\(["']?([^"')]+)["']?\\)/);
                return match ? match[1] : null;
              };
              const boxOf = (el) => {
                const r = el.getBoundingClientRect();
                return {
                  x: Math.round(r.left + window.scrollX),
                  y: Math.round(r.top + window.scrollY),
                  w: Math.round(r.width),
                  h: Math.round(r.height),
                };
              };
              // Count sibling cells holding exactly one similar-area image —
              // >=3 means this image is one tile of a card/portrait grid.
              const gridCount = (img) => {
                const mine = img.getBoundingClientRect();
                const myArea = mine.width * mine.height;
                if (myArea <= 0) return 0;
                let node = img.parentElement;
                for (let depth = 0; node && depth < 4; depth += 1, node = node.parentElement) {
                  const cells = Array.from(node.children);
                  if (cells.length < 3) continue;
                  let similar = 0;
                  for (const cell of cells) {
                    const imgs = cell.tagName === 'IMG'
                      ? [cell]
                      : Array.from(cell.querySelectorAll('img'));
                    if (imgs.length !== 1) continue;
                    const r = imgs[0].getBoundingClientRect();
                    const area = r.width * r.height;
                    if (area >= myArea * 0.4 && area <= myArea * 2.5) similar += 1;
                  }
                  if (similar >= 3) return similar;
                }
                return 0;
              };
              for (const img of Array.from(document.images)) {
                img.setAttribute('data-webtree-evidence', JSON.stringify({
                  nw: img.naturalWidth || 0,
                  nh: img.naturalHeight || 0,
                  ...boxOf(img),
                  vw, vh,
                  grid: gridCount(img),
                }));
              }
              for (const el of Array.from(document.querySelectorAll('body *'))) {
                const rect = el.getBoundingClientRect();
                if (rect.width < 200 || rect.height < 120) continue;
                const url = extractUrl(getComputedStyle(el).backgroundImage);
                if (!url) continue;
                el.setAttribute('data-webtree-bg-image', url);
                el.setAttribute('data-webtree-bg-evidence', JSON.stringify({
                  ...boxOf(el),
                  vw, vh,
                  text: ((el.innerText || '').trim()).length,
                }));
              }
            }
            """
        )
    except Exception:
        pass


async def _goto_and_render(
    context, url: str, *, timeout_ms: int
) -> tuple[str, str]:
    """Render a single URL inside an existing browser context.

    Returns (final_url, html). Raises ScrapeError for 4xx/5xx responses, or for
    a URL that fails the SSRF guard (non-public host).
    """
    try:
        await assert_public_url(url)
    except UnsafeUrlError as exc:
        raise ScrapeError(str(exc), status=400) from exc
    page = await context.new_page()
    try:
        response = await page.goto(
            url, wait_until="domcontentloaded", timeout=timeout_ms
        )
        if response is None:
            raise ScrapeError(f"No response from {url}", status=502)
        if response.status == 403:
            raise ScrapeError(
                f"{url} blocked our request (403). The site has bot-detection "
                "active and won't render in a headless browser. Try pasting the "
                "page content into the document tab instead, or pick a different "
                "URL on the same site that's less protected (e.g. a blog post).",
                status=403,
            )
        if response.status == 401:
            raise ScrapeError(
                f"{url} requires authentication (401). Paste the content "
                "directly into the document tab instead.",
                status=401,
            )
        if response.status == 429:
            raise ScrapeError(
                f"{url} is rate-limiting us (429). Wait a minute and try again.",
                status=429,
            )
        if response.status >= 400:
            raise ScrapeError(
                f"Page returned {response.status} for {url}", status=502
            )
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        # Scroll the page in steps so IntersectionObserver / lazy-load reveals
        # below-the-fold copy before we snapshot. Without this, paragraphs that
        # only mount on scroll never make it into page.content().
        await _autoscroll(page)
        await _stamp_render_evidence(page)
        html = await page.content()
        final_url = page.url
    finally:
        await page.close()
    return final_url, html


async def _fetch_rendered_html(
    url: str, *, timeout_ms: int | None = None
) -> tuple[str, str]:
    """Single-shot render — launches its own browser. Use _fetch_many for crawls."""
    if timeout_ms is None:
        timeout_ms = settings.playwright_goto_timeout_ms
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            user_agent=BROWSER_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            ignore_https_errors=True,
            locale="en-US",
            extra_http_headers=_BROWSER_HEADERS,
        )
        await context.add_init_script(_STEALTH_INIT_SCRIPT)
        await context.route("**/*", _route_block_heavy)
        try:
            return await _goto_and_render(context, url, timeout_ms=timeout_ms)
        finally:
            await context.close()
            await browser.close()


# --- HTML parsing ---------------------------------------------------------------


_IMG_EXT_OK = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")
_LOGO_HINTS = ("logo", "brandmark", "wordmark", "header-logo")
_BAD_IMG_HINTS = (
    "tracking",
    "pixel",
    "spacer",
    "blank",
    "sprite",
    "1x1",
    "loader",
    "loading",
)
_PROFILE_CONTAINER_HINTS = (
    "team",
    "member",
    "profile",
    "person",
    "people",
    "staff",
    "leadership",
    "committee",
    "council",
    "board",
    "trustee",
    "governance",
    "director",
)
_PROFILE_NAME_HINTS = ("name", "person-name", "member-name", "profile-name")
# A column holding the portrait and nothing else. See `_is_media_only`.
_PROFILE_MEDIA_COLUMN_MAX_CHARS = 24
_PROFILE_ROLE_HINTS = (
    "role",
    "title",
    "position",
    "designation",
    "job",
    "office",
)
# Job titles are short. "Deputy Director of Community Partnerships" is 5.
_ROLE_MAX_WORDS = 8
# Words that open a sentence about a person, never a job title.
_PROSE_LEAD_TOKENS = frozenset(
    {
        "he", "she", "they", "him", "her", "his", "their", "them",
        "we", "our", "us", "i", "my", "you", "your",
        "it", "its", "this", "that", "these", "those",
    }
)
# Chrome tags a profile card never lives inside. The container walk stops here
# rather than paying for a second parse of the document just to decompose them
# (_structural_text already re-parses once; twice per page is not worth it).
_PROFILE_CHROME_TAGS = {"nav", "footer", "header", "aside", "form"}
# A page builder emits its footer as a plain <div> (Divi: `et-l--footer`,
# `et_pb_column_1_tb_footer`), which the tag set above cannot catch. Only
# "footer" is safe to match on class: "header"/"nav" would also hit the
# legitimate `section-header` / `card-header` wrappers real cards sit in.
_PROFILE_CHROME_HINTS = ("footer",)

# A CTA/nav link is short; a card wrapped entirely in an <a> is not, and its
# text is real content that must not be discarded.
_PROFILE_LINK_TEXT_MAX = 60
_PROFILE_CARD_MAX_LINES = 12
# A card holds a name, a title and a bio, and the bio itself is capped at 480
# chars. A container carrying materially more prose than that is a section.
_PROFILE_CARD_MAX_CHARS = 600
# Headshots are square-ish or tall. Generous on both ends so a loosely cropped
# card photo still passes; only measurably banner-shaped images are rejected.
_PORTRAIT_MIN_ASPECT = 0.5
_PORTRAIT_MAX_ASPECT = 1.6

_GENERIC_PROFILE_NAMES = {
    "team",
    "our team",
    "meet the team",
    "committee",
    "our committee",
    "board",
    "our board",
    "leadership",
    "staff",
    # Name-shaped section headings (2+ capitalised tokens) that are never a
    # person. An inferred (unhinted) card no longer reads h2 at all, but a
    # HINTED container still does, so these stay as the second line of defence.
    "our story",
    "our mission",
    "our values",
    "our vision",
    "our services",
    "our history",
    "our approach",
    "our work",
    "our people",
    "our partners",
    "about us",
    "contact us",
    "who we are",
    "what we do",
    "why choose us",
    "get in touch",
    "join us",
    "getting involved",
    "good food",
    "latest events",
    "latest news",
    "upcoming events",
    "upcoming programs",
    "upcoming programmes",
}

# A real name never starts with a determiner/possessive/CTA verb. Catches the
# long tail of headings the exact-match set above can't enumerate
# ("Our Community Programmes", "Meet Your Dentists", "Why Families Trust Us").
_NON_NAME_LEAD_TOKENS = {
    "our", "the", "your", "my", "their", "this", "these", "a", "an",
    "meet", "about", "why", "what", "how", "who", "where", "when",
    "welcome", "contact", "discover", "explore", "join", "visit", "view",
    "see", "find", "get", "learn", "read", "book", "call",
    # Gerunds / present participles of CTA verbs — "Getting Involved",
    # "Building Futures", etc. are section headings, not person names.
    "getting", "giving", "making", "building", "going", "coming",
    "taking", "bringing", "keeping", "sharing", "starting", "creating",
    "becoming", "growing", "supporting", "helping", "working", "leading",
    "serving", "living", "doing",
    # Adjectives that open section headings ("Good Food", "Best Practice").
    "good", "great", "best", "new", "fresh", "clean", "latest",
    "upcoming", "featured", "popular", "top", "free",
}

# Lowercase tokens allowed inside a capitalised name ("Siti binti Rahman",
# "Jan van der Berg"). Everything else lowercase marks a sentence fragment,
# not a name.
_NAME_PARTICLES = {
    "bin", "binti", "binte", "van", "der", "de", "den", "da", "di",
    "del", "della", "von", "al", "el", "le", "la", "ter", "ten",
}

# Matches background-image / background shorthand containing a url().
# Handles quoted and unquoted URLs, with optional whitespace.
# Examples matched:
#   background-image: url("https://example.com/hero.jpg")
#   background-image: url('https://example.com/hero.jpg')
#   background: #333 url(https://example.com/banner.webp) no-repeat center
_BG_URL_RE = re.compile(
    r'background(?:-image)?\s*:[^;{]*url\(\s*["\']?([^"\')\s]+)["\']?\s*\)',
    re.IGNORECASE,
)


# Wix bakes the image transform into the URL PATH, e.g.
#   …/media/{id}/v1/fill/w_119,h_79,al_c,q_80,…,blur_2,enc_avif,quality_auto/{file}
# so a scraped <img src> (or srcset entry) frequently points at a tiny, blurred
# blur-up placeholder rather than the real photo. We rewrite it to a crisp,
# high-res variant with the blur removed. The media id encodes the original
# dimensions (…_d_{W}_{H}…), so we can cap the long edge while preserving aspect
# — the untransformed original can be 20 MB+, which would blow the CMS upload cap.
_WIX_MEDIA_RE = re.compile(
    r"^(https?://static\.wixstatic\.com/media/([^/?#]+))(?:/v1/[^?#]*)?",
    re.IGNORECASE,
)
_WIX_DIMS_RE = re.compile(r"_d_(\d+)_(\d+)")
_WIX_MAX_EDGE = 2560


def _upgrade_source_image_url(url: str) -> str:
    """Rewrite known image-CDN transform URLs to a crisp, full-size variant.

    Only matches unambiguous image-CDN transform URLs, so it is a no-op for page
    links — safe to run on every absolutized URL.
    """
    m = _WIX_MEDIA_RE.match(url)
    if not m:
        return url
    base, media_id = m.group(1), m.group(2)
    dims = _WIX_DIMS_RE.search(media_id)
    if dims:
        ow, oh = int(dims.group(1)), int(dims.group(2))
        if ow > 0 and oh > 0:
            # Cap the long edge and derive the short edge by FLOOR division —
            # Wix validates the requested dims against the original aspect and
            # 403s if the short edge doesn't match its own floor(…) computation.
            if ow >= oh:
                tw = min(ow, _WIX_MAX_EDGE)
                th = max(1, oh * tw // ow)
            else:
                th = min(oh, _WIX_MAX_EDGE)
                tw = max(1, ow * th // oh)
            return f"{base}/v1/fill/w_{tw},h_{th},al_c,q_90/{media_id}"
    # Original dimensions unknown → bare original (Wix serves it; usually small).
    return base


def _absolute_url(base: str, src: str) -> str | None:
    if not src or src.startswith("data:"):
        return None
    return _upgrade_source_image_url(urljoin(base, src))


def _looks_like_icon(url: str, alt: str) -> bool:
    low = url.lower()
    if any(h in low for h in _BAD_IMG_HINTS):
        return True
    if "favicon" in low:
        return True
    if alt and len(alt) > 0 and alt.lower() in {"icon", "logo icon"}:
        return True
    return False


def _looks_like_logo_url(url: str) -> bool:
    """True when the file NAME says logo (assets/logo.png, site-logo.svg).

    Filename only — a path segment like /logos/ marks a partner-logo gallery,
    and the query string could be anything.
    """
    basename = urlparse(url).path.rsplit("/", 1)[-1].lower()
    return "logo" in basename


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value)
    return int(m.group(0)) if m else None


def _iter_srcset_candidates(srcset: str):
    """Yield (url, descriptor) pairs from a srcset string.

    A srcset URL may itself contain commas — Wix bakes its transform into the
    path (``…/v1/fill/w_461,h_161,al_c,q_85,…/file.png``) and data URIs are
    comma-heavy — so a naive ``split(",")`` shatters them and yields a garbage
    trailing fragment. Per the HTML grammar, a candidate URL is a run of
    non-whitespace and the (optional) descriptor follows after whitespace, with
    candidates separated by commas; tokenize accordingly.
    """
    i, n = 0, len(srcset)
    while i < n:
        # Skip separators (whitespace and the commas between candidates).
        while i < n and (srcset[i].isspace() or srcset[i] == ","):
            i += 1
        if i >= n:
            break
        # URL: everything up to the next whitespace (internal commas kept).
        start = i
        while i < n and not srcset[i].isspace():
            i += 1
        url = srcset[start:i]
        descriptor = ""
        if url.endswith(","):
            # No descriptor — the comma directly separates candidates.
            url = url.rstrip(",")
        else:
            while i < n and srcset[i].isspace():
                i += 1
            dstart = i
            while i < n and srcset[i] != ",":
                i += 1
            descriptor = srcset[dstart:i].strip()
            if i < n and srcset[i] == ",":
                i += 1
        if url:
            yield url, descriptor


def _best_srcset_candidate(srcset: str | None) -> tuple[str | None, float]:
    """Return the highest-density/width URL from a srcset string and its score.

    The score is the winning ``w``/``x`` descriptor (``x`` scaled by 1000 so any
    density beats any raw width). A score of ``1.0`` means the winning candidate
    carried no real descriptor, so callers can treat it as "no measured size
    advantage" and keep a good base ``src``.
    """
    if not srcset:
        return None, -1.0
    best_url: str | None = None
    best_score = -1.0
    for url, descriptor in _iter_srcset_candidates(srcset):
        descriptor = descriptor.lower()
        score = 1.0
        try:
            if descriptor.endswith("w"):
                score = float(descriptor[:-1])
            elif descriptor.endswith("x"):
                score = float(descriptor[:-1]) * 1000.0
        except ValueError:
            score = 1.0
        if score > best_score:
            best_score = score
            best_url = url
    return best_url, best_score


def _image_src_from_tag(img: Tag) -> str | None:
    """Prefer real responsive image URLs over placeholders."""
    src = img.get("src") or img.get("data-src") or img.get("data-lazy-src")
    if isinstance(src, list):
        src = src[0] if src else None
    src = src if isinstance(src, str) else None

    srcset = (
        img.get("srcset")
        or img.get("data-srcset")
        or img.get("data-lazy-srcset")
    )
    if isinstance(srcset, list):
        srcset = srcset[0] if srcset else None
    srcset = srcset if isinstance(srcset, str) else None
    srcset_candidate, srcset_score = _best_srcset_candidate(srcset)

    source = img.find_previous_sibling("source")
    if source is None and isinstance(img.parent, Tag) and img.parent.name == "picture":
        sources = [s for s in img.parent.find_all("source") if isinstance(s, Tag)]
        source = sources[-1] if sources else None
    if isinstance(source, Tag):
        source_srcset = source.get("srcset") or source.get("data-srcset")
        if isinstance(source_srcset, list):
            source_srcset = source_srcset[0] if source_srcset else None
        picture_candidate, picture_score = _best_srcset_candidate(
            source_srcset if isinstance(source_srcset, str) else None
        )
        if picture_candidate:
            srcset_candidate, srcset_score = picture_candidate, picture_score

    src_low = (src or "").lower().split("?", 1)[0]
    if srcset_candidate and (
        not src
        or _looks_like_icon(src, "")
        or src_low.endswith(".svg")
        or "placeholder" in src_low
    ):
        return srcset_candidate
    # A responsive <img> usually keeps a small/medium fallback in `src` while the
    # full-resolution variants live only in `srcset`. Prefer the largest srcset
    # candidate so figure images are captured at full size — but only when it
    # carries a real width/density descriptor (score > 1); a descriptor-less
    # 1-URL srcset is no better than `src`, so keep the base then.
    if srcset_candidate and srcset_score > 1.0:
        return srcset_candidate
    return src or srcset_candidate


_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")


def _image_context(tag: Tag) -> tuple[str, str]:
    """(context_heading, caption) for an image-bearing tag.

    context_heading = nearest heading before the tag in document order;
    caption = <figcaption> text when the tag sits inside a <figure>.
    """
    heading = ""
    found = tag.find_previous(_HEADING_TAGS)
    if isinstance(found, Tag):
        heading = found.get_text(" ", strip=True)[:120]

    caption = ""
    figure = tag.find_parent("figure")
    if isinstance(figure, Tag):
        figcaption = figure.find("figcaption")
        if isinstance(figcaption, Tag):
            caption = figcaption.get_text(" ", strip=True)[:160]
    return heading, caption


def _tag_classes(tag: Tag) -> str:
    return " ".join(
        tag.get("class") if isinstance(tag.get("class"), list) else []
    ).lower()


def _bg_about_hint(tag: Tag) -> bool:
    return any(k in _tag_classes(tag) for k in ("about", "story", "team", "who-we-are"))


def _guess_bg_intent(tag: Tag, prior: list[ImageCandidate]) -> str:
    """Intent heuristic for a CSS background-image element without render
    evidence.

    Checks tag name and class names for hero/about signals.  Falls back to
    'generic'.  Never assigns 'hero' if one is already in ``prior`` so the
    first big background wins the slot.
    """
    hero_taken = any(c.intent == "hero" for c in prior)
    tag_name = (tag.name or "").lower()
    classes = _tag_classes(tag)

    if not hero_taken:
        if tag_name in ("header", "section"):
            return "hero"
        if any(k in classes for k in ("hero", "banner", "jumbotron", "cover", "splash", "masthead")):
            return "hero"

    if _bg_about_hint(tag):
        return "about"

    return "generic"


def _bg_candidate_from_tag(
    tag: Tag, abs_url: str, prior: list[ImageCandidate]
) -> ImageCandidate | None:
    """Build a candidate for a CSS-background element, evidence-aware.

    With render evidence: classify the measured role (None for decorations);
    hero intent is left to _promote_hero_by_evidence. Without evidence: legacy
    class/tag-name intent guess.
    """
    context_heading, caption = _image_context(tag)
    evidence = parse_evidence(tag.get("data-webtree-bg-evidence"))
    if evidence is not None:
        role = classify_role(evidence, is_background=True)
        if role == "decoration":
            return None
        intent = "about" if _bg_about_hint(tag) else "generic"
        return ImageCandidate(
            url=abs_url, alt="", width=evidence.width or None,
            height=evidence.height or None, intent=intent, role=role,
            evidence=evidence, source_usage="css_background",
            context_heading=context_heading, caption=caption,
        )
    return ImageCandidate(
        url=abs_url, alt="", width=None, height=None,
        intent=_guess_bg_intent(tag, prior), source_usage="css_background",
        context_heading=context_heading, caption=caption,
    )


def _extract_bg_images(
    soup: BeautifulSoup,
    base_url: str,
    seen: set[str],
    prior: list[ImageCandidate],
) -> list[ImageCandidate]:
    """Extract background-image URLs missed by the <img> pass.

    Level 1 — inline style attributes
        Any element with style="... background(-image): url(...) ..."
        Intent is inferred from the element's tag name and class list.

    Level 2 — <style> block content
        Regex over the raw CSS text; all matches get 'generic' intent since
        we can't map a selector back to a DOM position without a full CSS
        engine.  Data URIs and obvious icon paths are filtered.
    """
    candidates: list[ImageCandidate] = []
    # Render stamps anywhere on the page ⇒ legacy first-seen hero promotion is
    # disabled; _promote_hero_by_evidence picks the measured hero instead.
    page_has_evidence = (
        soup.find(attrs={"data-webtree-evidence": True}) is not None
        or soup.find(attrs={"data-webtree-bg-evidence": True}) is not None
    )

    # --- Level 1: inline style="background-image: url(...)" --------------------
    for tag in soup.find_all(attrs={"data-webtree-bg-image": True}):
        if not isinstance(tag, Tag):
            continue
        raw = tag.get("data-webtree-bg-image")
        if not isinstance(raw, str):
            continue
        abs_url = _absolute_url(base_url, raw.strip())
        if not abs_url or abs_url in seen:
            continue
        if _looks_like_icon(abs_url, ""):
            continue
        candidate = _bg_candidate_from_tag(tag, abs_url, prior + candidates)
        seen.add(abs_url)
        if candidate is not None:
            candidates.append(candidate)

    for tag in soup.find_all(style=True):
        if not isinstance(tag, Tag):
            continue
        style_val = tag.get("style")
        if not isinstance(style_val, str):
            continue
        for m in _BG_URL_RE.finditer(style_val):
            raw = m.group(1).strip()
            abs_url = _absolute_url(base_url, raw)
            if not abs_url or abs_url in seen:
                continue
            if _looks_like_icon(abs_url, ""):
                continue
            candidate = _bg_candidate_from_tag(tag, abs_url, prior + candidates)
            seen.add(abs_url)
            if candidate is not None:
                candidates.append(candidate)

    # --- Level 2: <style> tag CSS text ----------------------------------------
    for style_tag in soup.find_all("style"):
        if not isinstance(style_tag, Tag):
            continue
        css_text = style_tag.get_text()
        for m in _BG_URL_RE.finditer(css_text):
            raw = m.group(1).strip()
            if raw.startswith("data:"):
                continue
            abs_url = _absolute_url(base_url, raw)
            if not abs_url or abs_url in seen:
                continue
            if _looks_like_icon(abs_url, ""):
                continue
            seen.add(abs_url)
            # Can't infer intent from a CSS selector → generic, but on
            # evidence-less pages promote the first one to hero if nothing
            # better has been found yet.
            intent = (
                "hero"
                if not page_has_evidence
                and not any(c.intent == "hero" for c in prior + candidates)
                else "generic"
            )
            candidates.append(
                ImageCandidate(
                    url=abs_url, alt="", width=None, height=None, intent=intent,
                    source_usage="css_background",
                )
            )

    return candidates


def _extract_images(
    soup: BeautifulSoup, base_url: str
) -> list[ImageCandidate]:
    """
    Collect all <img> + og:image + apple-touch-icon, filter and rank.
    Returns candidates ordered: hero → about → generic. Logos are surfaced
    separately by _extract_logo_candidate.

    When the page carries render-evidence stamps (Playwright path), roles come
    from measured geometry, decorations are dropped, and the hero is the
    measured lead visual (_promote_hero_by_evidence) instead of the first
    image in DOM order.
    """
    seen: set[str] = set()
    candidates: list[ImageCandidate] = []

    # Inline <img> tags
    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue
        src = _image_src_from_tag(img)
        abs_url = _absolute_url(base_url, src or "")
        if not abs_url or abs_url in seen:
            continue
        alt = (img.get("alt") or "").strip() if isinstance(img.get("alt"), str) else ""
        if _looks_like_icon(abs_url, alt):
            continue
        # Skip obvious non-content paths
        low = abs_url.lower().split("?", 1)[0]
        if not (low.endswith(_IMG_EXT_OK) or "/wp-content/" in low or "cdn" in low):
            # Still allow if size hints big enough
            pass

        evidence = parse_evidence(img.get("data-webtree-evidence"))
        width = _parse_int(img.get("width") if isinstance(img.get("width"), str) else None)
        height = _parse_int(img.get("height") if isinstance(img.get("height"), str) else None)
        if evidence is not None:
            # Measured sizes beat declared attributes: natural is the true
            # bitmap size; the rendered box is a usable proxy when the bitmap
            # never finished loading.
            width = evidence.natural_width or width or evidence.width or None
            height = evidence.natural_height or height or evidence.height or None
        # Drop tiny declared sizes (decoration / icons)
        if (width and width < 200) or (height and height < 120):
            continue

        if evidence is not None:
            role = classify_role(evidence)
            if role == "decoration":
                continue
            # Hero is assigned by _promote_hero_by_evidence after all
            # candidates (incl. CSS backgrounds) are measured.
            intent = "about" if _about_hint(img) else "generic"
        else:
            role = "unknown"
            intent = _guess_intent(img, candidates)
        context_heading, caption = _image_context(img)
        candidates.append(
            ImageCandidate(
                url=abs_url, alt=alt, width=width, height=height,
                intent=intent, role=role, evidence=evidence,
                context_heading=context_heading, caption=caption,
            )
        )
        seen.add(abs_url)

    # og:image / twitter:image — usually high-quality and curated
    for prop in ("og:image", "twitter:image", "og:image:secure_url"):
        meta = soup.find("meta", attrs={"property": prop}) or soup.find(
            "meta", attrs={"name": prop}
        )
        if isinstance(meta, Tag):
            content = meta.get("content")
            if isinstance(content, str):
                abs_url = _absolute_url(base_url, content)
                if abs_url and abs_url not in seen:
                    candidates.insert(
                        0,
                        ImageCandidate(
                            url=abs_url,
                            alt="Open Graph image",
                            width=None,
                            height=None,
                            intent="hero",
                        ),
                    )
                    seen.add(abs_url)

    # CSS background-image URLs (inline styles + <style> blocks)
    bg_candidates = _extract_bg_images(soup, base_url, seen, candidates)
    candidates.extend(bg_candidates)

    # Rendered pages: hero = the measured lead visual, not DOM order.
    _promote_hero_by_evidence(candidates)

    return candidates[:30]  # cap


def _attr_haystack(tag: Tag) -> str:
    parts: list[str] = []
    for attr in ("class", "id", "itemprop"):
        value = tag.get(attr)
        if isinstance(value, list):
            parts.extend(str(v) for v in value)
        elif isinstance(value, str):
            parts.append(value)
    return " ".join(parts).lower()


def _has_any_hint(tag: Tag, hints: tuple[str, ...]) -> bool:
    haystack = _attr_haystack(tag)
    return any(hint in haystack for hint in hints)


def _clean_line(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _text_lines(tag: Tag) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for raw in tag.stripped_strings:
        line = _clean_line(str(raw))
        key = line.lower()
        if line and key not in seen:
            seen.add(key)
            lines.append(line)
    return lines


def _looks_like_person_name(value: str) -> bool:
    text = _clean_line(value).strip(" :|-")
    if not text:
        return False
    low = text.lower()
    if low in _GENERIC_PROFILE_NAMES:
        return False
    if "@" in text or "http" in low:
        return False
    tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z'.-]*", text) if t]
    if len(tokens) < 2 or len(tokens) > 7:
        return False
    if tokens[0].lower() in _NON_NAME_LEAD_TOKENS:
        return False
    # Every token must be capitalised (or a known name particle): rejects
    # sentence fragments like "Serving Penang since 1998" while keeping
    # "Dr Aisha Rahman", "Siti binti Rahman", and all-caps name plaques.
    for token in tokens:
        if token[0].isupper() or token.lower() in _NAME_PARTICLES:
            continue
        return False
    return True


def _cta_link_texts(container: Tag) -> set[str]:
    """Text of the container's short <a>/<button> descendants, lowercased.

    Short link text is a CTA or a nav label ("Read More", "View Profile"). A
    card wrapped entirely in an <a> has *long* text, and that text is the real
    content — hence the length bound rather than excluding all link text.
    """
    texts: set[str] = set()
    for el in container.find_all(["a", "button"]):
        if not isinstance(el, Tag):
            continue
        text = _clean_line(el.get_text(" ", strip=True))
        if text and len(text) <= _PROFILE_LINK_TEXT_MAX:
            texts.add(text.lower())
    return texts


def _looks_like_profile_card(tag: Tag) -> bool:
    """True when a container is plausibly ONE person's card.

    Replaces the old "any ancestor holding an h2-h5" fallback, which on a site
    without profile class names resolved to the whole section — every line in it
    then became that person's bio.
    """
    if len([i for i in tag.find_all("img") if isinstance(i, Tag)]) != 1:
        return False
    if tag.find("form") is not None:
        return False
    lines = _text_lines(tag)
    # A card names its person. A bare image wrapper (Divi's
    # `span.et_pb_image_wrap`, and every builder's equivalent) carries no text
    # at all, and would otherwise clear the size ceilings *trivially* — zero
    # lines is under any maximum. Claiming it as the card is worse than
    # claiming nothing: it is the innermost ancestor, so it wins the fallback
    # immediately, and returning it non-None suppresses the sibling-column
    # fallback that layouts like these actually need.
    if not lines:
        return False
    if len(lines) > _PROFILE_CARD_MAX_LINES:
        return False
    return sum(len(line) for line in lines) <= _PROFILE_CARD_MAX_CHARS


def _nearest_profile_container(img: Tag) -> tuple[Tag | None, bool]:
    """Return (container, hinted) for a portrait.

    ``hinted`` is True only when the container declared itself a profile card
    via class/id (``_PROFILE_CONTAINER_HINTS``). An inferred card gets less
    trust — see ``_extract_profile_name``.
    """
    current = img.parent
    fallback: Tag | None = None
    depth = 0
    while isinstance(current, Tag) and current.name not in {"body", "html"} and depth < 7:
        # Chrome is never a profile card. Walking past it would pull nav labels
        # and footer copy into the bio.
        if current.name in _PROFILE_CHROME_TAGS:
            return None, False
        if _has_any_hint(current, _PROFILE_CONTAINER_HINTS):
            img_count = len([i for i in current.find_all("img") if isinstance(i, Tag)])
            if img_count > 1:
                # A hinted container holding several portraits is the section,
                # not the card. Without a card-shaped descendant we cannot say
                # which text belongs to this person — emit nothing rather than
                # attributing the whole section to them.
                return fallback, False
            return current, True
        if fallback is None and _looks_like_profile_card(current):
            fallback = current
        current = current.parent
        depth += 1
    return fallback, False


def _in_profile_chrome(img: Tag) -> bool:
    """True when a portrait sits in site chrome rather than page content.

    ``_nearest_profile_container`` stops at ``_PROFILE_CHROME_TAGS`` on the way
    up; the sibling walk below has no such stop, and a footer's link column
    reads exactly like a card's text column to it — an image in a theme-builder
    footer plus the nav labels beside it would become a "person" named after a
    menu item.
    """
    if img.find_parent(list(_PROFILE_CHROME_TAGS)) is not None:
        return True
    # Stop below <body>: WordPress stamps page-level state onto the body class
    # ("et-tb-has-footer" says the theme HAS a footer template, not that this
    # element is in it), and matching there condemns every image on the page.
    for parent in img.parents:
        if not isinstance(parent, Tag) or parent.name in {"body", "html"}:
            break
        if _has_any_hint(parent, _PROFILE_CHROME_HINTS):
            return True
    return False


def _is_media_only(tag: Tag) -> bool:
    """True when this subtree holds the portrait and essentially nothing else.

    Not *zero* text — a caption, a photo credit or a stray nbsp shouldn't
    disqualify a column — but far below a name plus a line of copy.
    """
    return (
        len(_clean_line(tag.get_text(" ", strip=True))) <= _PROFILE_MEDIA_COLUMN_MAX_CHARS
    )


def _profile_text_sibling(children: list[Tag], owner_index: int) -> Tag | None:
    """The closest sibling that names a person — nearest first, never one with
    an image of its own.

    Both rules exist for flat grids. Laid out as
    ``[photoA][textA][photoB][textB]``, a document-order scan from photoB would
    walk back to textA and caption one person's portrait with another's name;
    nearest-first pairs each photo with its own copy. Equidistant neighbours
    break towards the FOLLOWING one, because a caption follows its photo — in
    that same grid photoB sits one step from both textA and textB, and only
    reading forwards gets it right. A two-column split that puts the copy
    first is unaffected: there the text column is the sole candidate.

    A sibling carrying its own <img> is another person's cell, never this
    one's text column.
    """
    ranked = sorted(
        (i for i in range(len(children)) if i != owner_index),
        key=lambda i: (abs(i - owner_index), 0 if i > owner_index else 1),
    )
    for index in ranked:
        sibling = children[index]
        if sibling.find("img") is not None:
            continue
        # A card names one person; a *section* names itself in an h1/h2 and
        # then talks about something else. Without this, an about split —
        # image one side, "Our Story" and prose the other — reads as a person,
        # because the loose any-text-line scan below accepts any two
        # capitalised words. The scan has to stay available: page builders
        # routinely put the name in an unheaded text module.
        if sibling.find(["h1", "h2"]) is not None:
            continue
        if not _extract_profile_name(sibling):
            continue
        if len(_clean_line(sibling.get_text(" ", strip=True))) < 24:
            continue
        return sibling
    return None


def _row_text_sibling_for_profile(img: Tag) -> Tag | None:
    """Find a portrait's copy when it lives in a SIBLING subtree.

    The split-column profile: portrait on one side, name and bio on the other,
    with no ancestor holding both and only them. ``_nearest_profile_container``
    walks *up* and meets nothing but text-less wrappers, so the copy has to be
    found by walking *across*.

    Structural, deliberately not name-based. Requiring ``row``/``col``/``grid``
    class names read the layout through one family of page builders (Divi,
    Bootstrap, WPBakery) and gave up silently on every site built with flex
    utilities, semantic element names, hashed CSS-module classes, or a table.
    The signal that holds everywhere is the one that sends us sideways to begin
    with: the portrait's own subtree carries no text, and a sibling's does.
    """
    if _in_profile_chrome(img):
        return None
    current = img.parent
    depth = 0
    while isinstance(current, Tag) and current.name not in {"body", "html"} and depth < 6:
        children = [c for c in current.find_all(recursive=False) if isinstance(c, Tag)]
        owner_index = next(
            (
                i
                for i, child in enumerate(children)
                if child is img or child.find(lambda t: t is img) is not None
            ),
            None,
        )
        if owner_index is None:
            break
        # Climb only while the portrait's subtree stays a media column. Once an
        # ancestor picks up the copy, THAT ancestor is the card, and scanning
        # its siblings would reach into the next person's.
        if not _is_media_only(children[owner_index]):
            break
        match = _profile_text_sibling(children, owner_index)
        if match is not None:
            return match
        current = current.parent
        depth += 1
    return None


def _find_text_by_hints(container: Tag, hints: tuple[str, ...]) -> str | None:
    for el in container.find_all(True):
        if not isinstance(el, Tag):
            continue
        if not _has_any_hint(el, hints):
            continue
        text = _clean_line(el.get_text(" ", strip=True))
        if text:
            return text
    return None


def _extract_profile_name(container: Tag, *, allow_h2: bool = True) -> str | None:
    """Find the person's name in a profile container.

    ``allow_h2=False`` for a container we merely *inferred* is a card (no
    class/id hint). A card names its person in an h3-h5 or a hinted element; an
    h2 is a SECTION heading, and accepting one is how an ordinary content
    section whose heading is two capitalised words ("Rahman Wellness") used to
    become a team member. The loose any-text-line scan is likewise hint-only.
    """
    hinted = _find_text_by_hints(container, _PROFILE_NAME_HINTS)
    if hinted and _looks_like_person_name(hinted):
        return hinted

    levels = ["h2", "h3", "h4", "h5"] if allow_h2 else ["h3", "h4", "h5"]
    for heading in container.find_all(levels):
        if not isinstance(heading, Tag):
            continue
        text = _clean_line(heading.get_text(" ", strip=True))
        if _looks_like_person_name(text):
            return text

    if not allow_h2:
        return None
    for line in _text_lines(container)[:5]:
        if _looks_like_person_name(line):
            return line
    return None


def _looks_like_role_line(line: str) -> bool:
    """True when a line reads as a job title rather than prose.

    A role is a LABEL — "Chairperson", "Founder & Speaker", "Head of Clinical
    Services". The positional scan below takes the lines just after the name,
    and on the very common card that carries NO role those lines are the first
    sentence of the bio. "Her interests include music, reading and travelling."
    is short, capitalised and free of contact tokens, so every other filter
    waves it through and it lands in the role slot under the person's name.
    """
    words = line.split()
    if len(words) > _ROLE_MAX_WORDS:
        return False
    # Trailing full stops mark a sentence, but not on "Ph.D." or "Jr." — so
    # only once the line is long enough to BE a sentence.
    if line.endswith((".", "!", "?")) and len(words) >= 4:
        return False
    lead = words[0].lower().strip(",.:;") if words else ""
    return lead not in _PROSE_LEAD_TOKENS


def _extract_profile_role(container: Tag, name: str) -> str | None:
    hinted = _find_text_by_hints(container, _PROFILE_ROLE_HINTS)
    if hinted and hinted != name and len(hinted) <= 90:
        return hinted

    lines = _text_lines(container)
    cta_texts = _cta_link_texts(container)
    try:
        name_index = next(i for i, line in enumerate(lines) if line == name)
    except StopIteration:
        name_index = -1
    for line in lines[name_index + 1 : name_index + 4]:
        if line == name or _looks_like_person_name(line):
            continue
        if len(line) > 90:
            continue
        # The positional fallback used to accept the next short line outright,
        # which made "Read More" and phone numbers look like job titles.
        if line.lower() in cta_texts or is_boilerplate_line(line):
            continue
        if has_contact_token(line):
            continue
        if not _looks_like_role_line(line):
            continue
        return line
    return None


def _extract_profile_bio(container: Tag, name: str, role: str | None) -> str | None:
    """Keep the card's own factual lines; drop chrome.

    Filters by line *kind*, not length: a directory card packs credentials,
    served populations and an address as short separate lines, and those are
    the bio. What must not survive is CTA/nav text, contact details, and copy
    belonging to the rest of the page.
    """
    cta_texts = _cta_link_texts(container)
    kept: list[str] = []
    for line in _text_lines(container):
        if line == name or (role and line == role):
            continue
        if len(line) <= 3:
            continue
        if line.lower() in cta_texts:
            continue
        if is_boilerplate_line(line):
            continue
        if has_contact_token(line):
            continue
        kept.append(line)
    if not kept:
        return None
    # Newline-joined so a directory card's distinct facts (credentials,
    # serving populations, clinic address) stay separate lines — the team
    # builder renders bios white-space: pre-line.
    bio = "\n".join(kept)
    return bio[:480]


def _has_portrait_aspect(
    width: int | None, height: int | None, evidence: ImageEvidence | None
) -> bool:
    """True unless the image is measurably too wide to be a headshot.

    Portraits are square-ish or tall. Banners, logo lockups and hero strips are
    wide. Unknown dimensions keep the benefit of the doubt — most real cards
    declare no width/height and carry no render evidence.
    """
    if evidence is not None and evidence.height:
        ratio = evidence.width / evidence.height
    elif width and height:
        ratio = width / height
    else:
        return True
    return _PORTRAIT_MIN_ASPECT <= ratio <= _PORTRAIT_MAX_ASPECT


# Elements a page uses to NAME something, in the two ways markup expresses it:
# by tag rank, or by a class/id that says "this is the name".
_NAME_ELEMENT_TAGS = ("h1", "h2", "h3", "h4", "h5")


def _leading_person_name(soup: BeautifulSoup) -> str | None:
    """The person the page's BODY leads with, read off the DOM hierarchy.

    Walks the body in document order and stops at the first *designated* name
    element — one ranked as a heading, or one a class/id marks as a name — whose
    text reads as a person's. Chrome is skipped: a nav or footer names people on
    every page of the site, so a match there says nothing about this page.

    This is the third way a page can say whose page it is, beside its <title>
    and its URL, and the only one that survives MMTA's committee pages: their
    <title> is the template "About MMTA", their h1 is the section banner "The
    Committee", and the person is named a level down in <div class="name"> —
    invisible to any title-or-heading reading.

    Elements are examined outermost-first, so a wrapper holding the whole card
    is seen before the name inside it; it fails the person-name test on length
    and the walk continues inward.
    """
    body = soup.body or soup
    chrome = list(_PROFILE_CHROME_TAGS)
    for el in body.find_all(True):
        if not isinstance(el, Tag):
            continue
        if el.name not in _NAME_ELEMENT_TAGS and not _has_any_hint(el, _PROFILE_NAME_HINTS):
            continue
        text = _clean_line(el.get_text(" ", strip=True))
        if not _looks_like_person_name(text):
            continue
        if el.find_parent(chrome) is not None:
            continue
        return text
    return None


def _page_subject_profile(
    soup: BeautifulSoup, base_url: str, portraits: list[tuple[str, str]]
) -> ProfileCandidate | None:
    """One candidate for a page that IS a person's profile, not a card grid.

    A committee member's own page doesn't card its person: the name is the
    page's h1 and the portrait sits loose in the content column, so the card
    walk finds no container — and would not read the name anyway, since a card
    names its person in an h3-h5. Named by the h1, photographed by the image
    whose alt echoes that name, or by the page's only portrait-shaped photo (a
    member page carries exactly one). Consulted only when no card matched, so a
    directory page is unaffected.

    The h1 is the whole claim: this page is ABOUT that person. A name-shaped h2
    is a section heading inside a page about something else ("Rahman Wellness"
    over a clinic's story), which is why the card walk distrusts h2 as well.
    """
    name = None
    for heading in soup.find_all("h1"):
        if not isinstance(heading, Tag):
            continue
        text = _clean_line(heading.get_text(" ", strip=True))
        if _looks_like_person_name(text):
            name = text
            break
    if name is None:
        return None

    matched = next((p for p in portraits if name.lower() in p[1].lower()), None)
    if matched is None and len(portraits) == 1:
        matched = portraits[0]
    if matched is None:
        return None

    # The page IS this person — any mailto:/tel:/social link in its body
    # (outside chrome shared by every page) is fair to attribute to them, the
    # same way a card's own anchors are attributed to it above.
    body = soup.body or soup
    chrome = list(_PROFILE_CHROME_TAGS)
    anchors = [
        a
        for a in body.find_all("a", href=True)
        if isinstance(a, Tag) and a.find_parent(chrome) is None
    ]
    email, phone = _contacts_from_anchors(anchors)
    social = social_links_from_anchors(anchors, base_url)

    photo_url, alt = matched
    return ProfileCandidate(
        name=name,
        role=None,
        # The page's prose is its own about section's material, not a card bio.
        bio=None,
        photo_url=photo_url,
        photo_alt=alt or f"{name} portrait",
        source_url=base_url,
        email=email,
        phone=phone,
        social_links=[(link.label, link.href) for link in social],
        # Below a real card's 0.8: the pairing is positional, not structural.
        confidence=0.75,
    )


def _card_anchors(img: Tag, container: Tag) -> list[Tag]:
    """Links belonging to THIS card, portrait-first.

    The portrait's own ancestor link can only be this person's; the container's
    links are next. Nothing wider — on a grid the row above a card holds its
    neighbours' links too.
    """
    anchors: list[Tag] = []
    ancestor = img.find_parent("a")
    if isinstance(ancestor, Tag):
        anchors.append(ancestor)
    anchors.extend(a for a in container.find_all("a") if isinstance(a, Tag))
    return anchors


def _contacts_from_anchors(anchors: list[Tag]) -> tuple[str | None, str | None]:
    """(email, phone) from mailto:/tel: hrefs among the given anchors.

    Shared by the card-scoped and whole-page extractors below — a directory
    card's and a solo profile page's own contact links are read the same way,
    just over a different anchor set.
    """
    email: str | None = None
    phone: str | None = None
    for anchor in anchors:
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        value = href.strip()
        low = value.lower()
        if email is None and low.startswith("mailto:"):
            # "mailto: a@b.my" — the space after the scheme is common enough.
            address = value.split(":", 1)[1].strip()
            if "@" in address:
                email = address
        elif phone is None and low.startswith("tel:"):
            number = value.split(":", 1)[1].strip()
            if number:
                phone = number
    return email, phone


def _profile_card_contacts(
    img: Tag, container: Tag, base_url: str
) -> tuple[str | None, str | None, list[NavLink]]:
    """(email, phone, social_links) the card offers for this person.

    A directory card's mailto:/tel:/social links are how the source says to
    reach that person — the profile block renders them beside the portrait
    rather than leaving them buried in a bio.
    """
    anchors = _card_anchors(img, container)
    email, phone = _contacts_from_anchors(anchors)
    social = social_links_from_anchors(anchors, base_url)
    return email, phone, social


def _profile_card_link(img: Tag, container: Tag, base_url: str) -> str | None:
    """The detail page this roster card points at, if it has one.

    A directory that gives its people their own pages says so in the card: the
    portrait is wrapped in the link, or a "view profile" control carries it
    (MMTA's committee grid puts it on a badge icon beside the email). That is
    the source's own statement of where the person's page lives — worth more
    than anything inferable from the URL or the name, and the only evidence
    that survives a template whose slugs are hand-spelled.

    The portrait's own ancestor link is preferred: it can only belong to this
    card. The container is searched second, and nothing wider — on a grid the
    row above a card holds its neighbours' links too.

    Only real pages qualify. ``_is_crawlable_link`` drops off-site links (a
    member's own practice) and asset URLs; mail/phone/anchor hrefs are not
    pages; and a link back to the page the card is ON is chrome — the "Back"
    arrow on a detail page's own card, not a link to a detail page.
    """
    here = _normalize_crawl_url(base_url)
    entry_host = urlparse(base_url).netloc

    for anchor in _card_anchors(img, container):
        href = anchor.get("href")
        if not isinstance(href, str) or not href.strip():
            continue
        if href.strip().lower().startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = _absolute_url(base_url, href.strip())
        if not absolute:
            continue
        normalized = _normalize_crawl_url(absolute)
        if not normalized or normalized == here:
            continue
        if not _is_crawlable_link(normalized, entry_host):
            continue
        return normalized
    return None


def _extract_profile_candidates(
    soup: BeautifulSoup, base_url: str
) -> list[ProfileCandidate]:
    """Extract likely profile cards where a portrait and nearby person text agree."""
    profiles: list[ProfileCandidate] = []
    seen: set[tuple[str, str | None]] = set()
    # Photos that cleared every image-level gate, for the page-subject fallback.
    portraits: list[tuple[str, str]] = []

    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue
        src = _image_src_from_tag(img)
        photo_url = _absolute_url(base_url, src or "")
        if not photo_url:
            continue
        alt = (img.get("alt") or "").strip() if isinstance(img.get("alt"), str) else ""
        if _looks_like_icon(photo_url, alt):
            continue
        width = _parse_int(img.get("width") if isinstance(img.get("width"), str) else None)
        height = _parse_int(img.get("height") if isinstance(img.get("height"), str) else None)
        if (width and width < 80) or (height and height < 80):
            continue
        # Render evidence: icon-size boxes next to a name are social/link
        # icons, not the portrait (declared width/height is usually absent).
        evidence = parse_evidence(img.get("data-webtree-evidence"))
        if evidence is not None and (evidence.width < 80 or evidence.height < 80):
            continue
        # A logo or a wide banner sitting in a section whose heading happens to
        # be name-shaped would otherwise be cropped into a circle and captioned
        # with that heading.
        if _looks_like_logo_url(photo_url):
            continue
        if not _has_portrait_aspect(width, height, evidence):
            continue
        # For the page-subject fallback only, the shape has to be MEASURED —
        # `_has_portrait_aspect` passes unknown dimensions on benefit of the
        # doubt, which a card's structure earns and a loose photo does not (a
        # dimensionless banner under a name-shaped h1 would become a portrait).
        if (evidence is not None and evidence.height) or (width and height):
            portraits.append((photo_url, alt))

        container, hinted = _nearest_profile_container(img)
        name = (
            _extract_profile_name(container, allow_h2=hinted)
            if container is not None
            else None
        )
        if not name:
            # The ancestor walk found no container, or found one that names
            # nobody — a page-builder layout that puts the portrait and the
            # copy in SIBLING columns looks like both. Either way the walk has
            # nothing to offer, so try across rather than up. Gating this on
            # `container is None` alone is how a text-less wrapper silently
            # cost a whole team grid its photos.
            container = _row_text_sibling_for_profile(img)
            hinted = False
            name = _extract_profile_name(container) if container is not None else None
        if container is None or not name:
            continue
        role = _extract_profile_role(container, name)
        card_email, card_phone, card_social = _profile_card_contacts(
            img, container, base_url
        )
        key = (name.lower(), photo_url)
        if key in seen:
            continue
        seen.add(key)
        profiles.append(
            ProfileCandidate(
                name=name,
                role=role,
                bio=_extract_profile_bio(container, name, role),
                photo_url=photo_url,
                photo_alt=alt or f"{name} portrait",
                source_url=base_url,
                profile_url=_profile_card_link(img, container, base_url),
                email=card_email,
                phone=card_phone,
                social_links=[(link.label, link.href) for link in card_social],
                confidence=0.9 if role else 0.8,
            )
        )

    if not profiles:
        subject = _page_subject_profile(soup, base_url, portraits)
        if subject is not None:
            return [subject]
    return profiles[:24]


# --- document cards (downloadable PDFs/DOCs presented with a title/thumbnail) ---
#
# Same structural idea as _extract_profile_candidates, applied to a different
# distinctive anchor: instead of a portrait, a document-extension <a href>.
# Deliberately makes NO assumption about class names or markup conventions —
# it must work on any site's resource/brochure listing, not just one that
# happens to use a particular convention.

# A document card holds a title and a short "Download" line, not prose — much
# tighter than a profile card's bio allowance.
_DOCUMENT_CARD_MAX_LINES = 8
_DOCUMENT_CARD_MAX_CHARS = 400

# Boilerplate lead words that must never be mistaken for a card's title (a
# card with no separate title — just "Download <a>PDF</a>" — gets no title
# rather than a misleading one).
_DOCUMENT_CARD_TITLE_STOPWORDS = frozenset(
    {"download", "downloads", "download now", "get file", "get the file"}
)


def _looks_like_document_card(tag: Tag) -> bool:
    """True when a container is plausibly ONE document's card: at most one
    thumbnail, no form, and text short enough to be a title plus a download
    line — not a whole grid of several cards."""
    if len([i for i in tag.find_all("img") if isinstance(i, Tag)]) > 1:
        return False
    if tag.find("form") is not None:
        return False
    lines = _text_lines(tag)
    if len(lines) > _DOCUMENT_CARD_MAX_LINES:
        return False
    return sum(len(line) for line in lines) <= _DOCUMENT_CARD_MAX_CHARS


def _document_anchors(tag: Tag, base_url: str) -> list[Tag] | None:
    """Every ``<a href>`` inside ``tag``, or None when any of them is NOT a
    document link — a card must not straddle a mix of document and nav/other
    links, so one stray link disqualifies the whole container."""
    anchors = [a for a in tag.find_all("a", href=True) if isinstance(a, Tag)]
    if not anchors:
        return None
    for a in anchors:
        href = _absolute_url(base_url, str(a.get("href")))
        if not href or not is_document_href(href):
            return None
    return anchors


def _nearest_document_card(a: Tag, base_url: str) -> Tag | None:
    """Walk up from a document anchor to the LARGEST ancestor that still (a)
    contains only document links and (b) looks like a single card. Growing
    stops the moment either condition would break — e.g. a grid wrapper
    holding several cards fails (b) via its multiple thumbnails, the same way
    _nearest_profile_container stops at a container holding >1 portrait.
    """
    current = a.parent
    best: Tag | None = None
    depth = 0
    while isinstance(current, Tag) and current.name not in {"body", "html"} and depth < 6:
        if current.name in _PROFILE_CHROME_TAGS:
            break
        if _document_anchors(current, base_url) is None:
            break
        if not _looks_like_document_card(current):
            break
        best = current
        current = current.parent
        depth += 1
    return best


# A title is a heading, not a sentence — bounds it away from a prose fragment
# ("...a report you can download here in passing") that happens to precede a
# document link inline within the same paragraph.
_DOCUMENT_CARD_TITLE_MAX_CHARS = 100


def _document_card_title(container: Tag, anchors: list[Tag]) -> str | None:
    """The card's title: text from a DIRECT CHILD of ``container`` that does
    not itself hold any of the card's document anchors.

    This is the key structural signal: a real card title lives in its own
    sibling element ("<p>Title</p><div>Download <a>...</a></div>"), while a
    PDF mentioned inline in running prose shares the SAME element as the
    anchor ("<p>...you can <a>download here</a>...</p>") — that container's
    only element child IS the anchor, so no sibling title text exists and
    None is returned correctly.
    """
    anchor_ids = {id(a) for a in anchors}
    for child in container.find_all(recursive=False):
        if not isinstance(child, Tag):
            continue
        # find_all searches descendants only, so a bare <a> CHILD must also be
        # checked against itself, not just its (nonexistent) sub-anchors.
        if id(child) in anchor_ids or any(
            id(a) in anchor_ids for a in child.find_all("a")
        ):
            continue
        text = _clean_line(child.get_text(" ", strip=True))
        if not text or len(text) > _DOCUMENT_CARD_TITLE_MAX_CHARS:
            continue
        if text.strip().rstrip(":").lower() in _DOCUMENT_CARD_TITLE_STOPWORDS:
            continue
        return text
    return None


def _extract_document_cards(
    soup: BeautifulSoup, base_url: str
) -> list["DocumentCardCandidate"]:
    """Extract likely downloadable-document cards: a title, an optional
    thumbnail, and one-or-more document-file links, grouped the way the
    source page visually grouped them (one enclosing card), not flattened
    into a single list of buttons."""
    work = BeautifulSoup(str(soup), "lxml")
    for tag in work.find_all(("header", "nav", "footer", "script", "style", "noscript")):
        tag.decompose()

    cards: list[DocumentCardCandidate] = []
    seen_containers: set[int] = set()
    seen_link_sets: set[frozenset[str]] = set()

    for a in work.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        href = _absolute_url(base_url, str(a.get("href")))
        if not href or not is_document_href(href):
            continue
        container = _nearest_document_card(a, base_url)
        if container is None or id(container) in seen_containers:
            continue
        seen_containers.add(id(container))

        anchors = _document_anchors(container, base_url) or []
        links: list[DocumentCardLink] = []
        for link_a in anchors:
            label = _clean_line(link_a.get_text(" ", strip=True))
            link_href = _absolute_url(base_url, str(link_a.get("href")))
            if not label or not link_href:
                continue
            links.append(DocumentCardLink(label=label, href=link_href))
        if not links:
            continue
        key = frozenset(link.href for link in links)
        if key in seen_link_sets:
            continue
        seen_link_sets.add(key)

        img = next((i for i in container.find_all("img") if isinstance(i, Tag)), None)
        image_url = None
        if img is not None:
            src = _image_src_from_tag(img)
            resolved = _absolute_url(base_url, src or "")
            alt = (img.get("alt") or "").strip() if isinstance(img.get("alt"), str) else ""
            if resolved and not _looks_like_icon(resolved, alt):
                image_url = resolved

        title = _document_card_title(container, anchors)
        # Qualifying bar: a bare single link with no title and no thumbnail
        # isn't a "card" — it's an ordinary link (a nav item that happens to
        # point at a PDF, an inline mention in prose). Requiring at least one
        # of {title, image, >1 link} keeps those out without relying on any
        # site-specific markup convention.
        if title is None and image_url is None and len(links) <= 1:
            continue

        cards.append(
            DocumentCardCandidate(title=title, image_url=image_url, links=links)
        )

    return cards[:20]


def _strip_document_card_lines(text: str, cards: list[DocumentCardCandidate]) -> str:
    """Remove each document card's title + link labels from raw_text.

    Without this, the LLM sees the same titles ("Music Therapy for Mental
    Health") as ordinary page text and invents its OWN services/about section
    narrating them — duplicating, in a second disconnected section, content
    the deterministic downloads block (routers.generate._inject_downloads)
    already renders with real buttons. Same reasoning as nav_extraction.
    strip_linkbar_lines: claim the text before planning, not after.
    """
    exact_lines = {
        _clean_line(card.title).strip().lower() for card in cards if card.title
    }
    label_groups = [
        [link.label.strip().lower() for link in card.links if link.label]
        for card in cards
    ]
    label_groups = [g for g in label_groups if g]
    if not exact_lines and not label_groups:
        return text

    def _is_link_label_line(line: str) -> bool:
        low = _clean_line(line).strip().lower()
        for labels in label_groups:
            remainder = low
            matched = False
            for label in labels:
                replaced = re.sub(re.escape(label), " ", remainder, count=1)
                if replaced != remainder:
                    matched = True
                    remainder = replaced
            if not matched:
                continue
            # A leading "Download " boilerplate word is not one of the card's
            # OWN link labels, but must not keep the remainder non-empty
            # either — strip it too before judging what's left over.
            for stopword in _DOCUMENT_CARD_TITLE_STOPWORDS:
                remainder = re.sub(re.escape(stopword), " ", remainder)
            # Only a line that's essentially *made of* this card's link labels
            # (nothing meaningful left over) is dropped — a short unrelated
            # line that merely contains a label as a substring must not match.
            if len(re.sub(r"[^a-z0-9]", "", remainder)) < 5:
                return True
        return False

    kept = [
        line
        for line in text.split("\n")
        if _clean_line(line).strip().lower() not in exact_lines
        and not _is_link_label_line(line)
    ]
    return "\n".join(kept)


def _about_hint(img: Tag) -> bool:
    """True when the nearest section heading reads like an about/team section."""
    section = img.find_parent(["section", "article", "div"])
    if isinstance(section, Tag):
        heading = section.find(["h1", "h2", "h3"])
        if isinstance(heading, Tag):
            text = heading.get_text(strip=True).lower()
            return any(t in text for t in ("about", "story", "team", "who we are"))
    return False


def _guess_intent(img: Tag, prior: list[ImageCandidate]) -> str:
    """
    Cheap heuristic for pages without render evidence: first big image we see
    is "hero"; subsequent are "about" or "generic" based on nearby text.
    """
    if not any(c.intent == "hero" for c in prior):
        return "hero"
    return "about" if _about_hint(img) else "generic"


def _promote_hero_by_evidence(candidates: list[ImageCandidate]) -> None:
    """Give the 'hero' intent to the strongest evidence-backed lead visual.

    Replaces the legacy "first big image wins" guess on rendered pages: the
    hero is the measured-hero (or an above-the-fold backdrop) with the largest
    viewport coverage. Evidence-bearing candidates never receive 'hero' during
    extraction, so this is the only place rendered pages assign it. No-op on
    fast-path pages (no evidence ⇒ legacy heuristics already picked a hero).
    """
    contenders = [
        c for c in candidates
        if c.evidence is not None
        and (c.role == "hero" or (c.role == "background" and c.evidence.above_fold))
    ]
    if not contenders:
        return
    best = max(contenders, key=lambda c: c.evidence.coverage)  # type: ignore[union-attr]
    best.intent = "hero"


def _extract_logo_candidate(soup: BeautifulSoup, base_url: str) -> str | None:
    """
    Try in this order:
    1. <link rel="apple-touch-icon"> (usually 180x180+)
    2. <link rel="icon"> with sizes >= 96
    3. <meta property="og:image">
    4. <img> with class/alt/src containing "logo"
    """
    # apple-touch-icon
    apple = soup.find("link", rel=lambda v: v and "apple-touch-icon" in v)
    if isinstance(apple, Tag):
        href = apple.get("href")
        if isinstance(href, str):
            return _absolute_url(base_url, href)

    # link rel="icon" with biggest sizes
    icon_tags = soup.find_all("link", rel=lambda v: v and "icon" in v)
    best_icon: tuple[int, str] | None = None
    for tag in icon_tags:
        if not isinstance(tag, Tag):
            continue
        sizes = tag.get("sizes")
        href = tag.get("href")
        if not isinstance(href, str):
            continue
        size_n = 0
        if isinstance(sizes, str) and "x" in sizes:
            try:
                size_n = int(sizes.split("x")[0])
            except ValueError:
                size_n = 0
        if best_icon is None or size_n > best_icon[0]:
            best_icon = (size_n, href)
    if best_icon and best_icon[0] >= 96:
        return _absolute_url(base_url, best_icon[1])

    # og:image (carries brand colour even if not strictly a logo)
    og = soup.find("meta", attrs={"property": "og:image"})
    if isinstance(og, Tag):
        content = og.get("content")
        if isinstance(content, str):
            return _absolute_url(base_url, content)

    # <img> tags containing "logo"
    for img in soup.find_all("img"):
        if not isinstance(img, Tag):
            continue
        haystack = " ".join(
            v
            for v in (
                str(img.get("src") or ""),
                str(img.get("alt") or ""),
                str(img.get("class") or ""),
            )
        ).lower()
        if any(h in haystack for h in _LOGO_HINTS):
            src = img.get("src") or img.get("data-src")
            if isinstance(src, str):
                return _absolute_url(base_url, src)

    # Final fallback — favicon
    if best_icon:
        return _absolute_url(base_url, best_icon[1])
    return None


# Block-level tags whose text we keep in the structural fallback pass. These
# carry real copy on marketing pages that trafilatura often discards as
# "boilerplate" because it isn't wrapped in a clean <article>/<main>.
_BLOCK_TEXT_TAGS = (
    "p", "li", "blockquote", "figcaption",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "dt", "dd", "td", "th", "summary",
)

# Containers we strip before the structural pass — chrome, not content.
_NON_CONTENT_TAGS = ("script", "style", "noscript", "template", "svg", "nav", "footer")


def _structural_text(soup: BeautifulSoup) -> str:
    """Block-by-block text harvest as a recall-oriented complement to trafilatura.

    trafilatura optimises for *precision* on article pages: it returns the one
    main column and drops everything else. On marketing/landing pages that means
    hero copy, feature grids, testimonials, and CTA blocks — all of which live in
    <section>/<div> soup rather than an <article> — get thrown away.

    This walks every block-level text tag, dedupes, and joins. It will include
    some nav/footer noise, so callers should keep it only when it's *materially*
    richer than trafilatura's output rather than always preferring it.
    """
    work = BeautifulSoup(str(soup), "lxml")
    for tag in work.find_all(_NON_CONTENT_TAGS):
        tag.decompose()

    chunks: list[str] = []
    seen: set[str] = set()
    for el in work.find_all(_BLOCK_TEXT_TAGS):
        if not isinstance(el, Tag):
            continue
        text = el.get_text(" ", strip=True)
        if not text or len(text) < 2:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        chunks.append(text)
    return "\n".join(chunks)


def _norm_block(text: str) -> str:
    """Normalise a text block for dedupe: collapse whitespace, lowercase."""
    return " ".join(text.split()).lower()


def _extract_body_text(html: str, soup: BeautifulSoup) -> str:
    """Best-effort body text, resilient to trafilatura under-extraction.

    trafilatura optimises for precision and reliably drops two things on
    marketing pages: (a) whole <section>/<div> blocks it deems boilerplate, and
    (b) heading text. The structural pass catches both but carries some nav/menu
    noise. So we *merge* rather than pick a winner: take the structural blocks as
    the recall spine (document order, headings included) and append any
    trafilatura block not already present. Neither side's content is lost.

    include_tables is on so table-laid-out copy survives. Falls back to a flat
    get_text only if both passes come back empty.
    """
    trafi = trafilatura.extract(
        html,
        include_comments=False,
        include_tables=True,
        include_links=False,
        favor_recall=True,
    ) or ""
    structural = _structural_text(soup)

    blocks: list[str] = []
    seen: set[str] = set()
    for block in structural.split("\n"):
        key = _norm_block(block)
        if not key or key in seen:
            continue
        seen.add(key)
        blocks.append(block.strip())
    for block in trafi.split("\n"):
        key = _norm_block(block)
        if not key or key in seen:
            continue
        seen.add(key)
        blocks.append(block.strip())

    if blocks:
        return "\n".join(blocks)

    # Both passes empty — flat text as a last resort.
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def _extract_headings(soup: BeautifulSoup) -> list[str]:
    out: list[str] = []
    for h in soup.find_all(["h1", "h2", "h3"]):
        if not isinstance(h, Tag):
            continue
        text = h.get_text(" ", strip=True)
        if text and 2 <= len(text) <= 200:
            out.append(text)
    # De-dupe, preserve order
    seen = set()
    deduped = []
    for t in out:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(t)
    return deduped[:50]


# How many links one page contributes. The cap exists so a sitemap-style page
# can't balloon SourceContent; it is applied AFTER crawlable links are sorted to
# the front (see below), so the crawl frontier is never the thing that loses out.
_MAX_LINKS_PER_PAGE = 200


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Absolute links on the page, crawlable same-host ones first.

    The order is load-bearing: this list is the crawl's seed frontier
    (``scrape_url`` passes it as ``seed_links``), and the cap used to be applied
    to raw document order. On a page carrying a mega-menu and a footer sitemap,
    the first 50 links are all chrome, external and asset URLs — so genuine
    content links were cut before ``_is_crawlable_link`` ever saw them, and the
    crawl silently explored a fraction of the site.

    Sorting is stable, so within each group document order is preserved.
    """
    out: list[str] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        if not isinstance(a, Tag):
            continue
        href = a.get("href")
        if not isinstance(href, str):
            continue
        abs_url = _absolute_url(base_url, href)
        if abs_url and abs_url not in seen and abs_url.startswith(("http://", "https://")):
            seen.add(abs_url)
            out.append(abs_url)

    entry_host = urlparse(base_url).netloc
    out.sort(key=lambda url: 0 if _is_crawlable_link(url, entry_host) else 1)
    return out[:_MAX_LINKS_PER_PAGE]


def _extract_meta_string(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        meta = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if isinstance(meta, Tag):
            content = meta.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
    return None


# --- brand candidate -----------------------------------------------------------


async def _build_brand_candidate(
    site_name: str | None,
    logo_url: str | None,
) -> BrandIdentity | None:
    if not logo_url:
        return None
    if not await is_public_url(logo_url):
        logger.warning("Refusing to fetch logo from non-public URL %s", logo_url)
        return None
    try:
        async with httpx.AsyncClient(
            timeout=settings.robots_fetch_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        ) as client:
            resp = await client.get(logo_url)
            resp.raise_for_status()
            image_bytes = resp.content
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch logo %s: %s", logo_url, exc)
        return None

    try:
        # PIL decode + quantize is CPU-bound — keep it off the event loop.
        extraction = await asyncio.to_thread(extract_palette_from_image_bytes, image_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to extract palette from %s: %s", logo_url, exc)
        return None

    return BrandIdentity(
        name=site_name or "Untitled",
        logo_url=logo_url,
        logo_data_url=extraction.logo_data_url,
        extracted_palette=extraction.palette,
        logo_is_light=extraction.logo_is_light,
        mood=None,
    )


# --- top-level orchestration ----------------------------------------------------


@dataclass
class _ParsedPage:
    """Output of parsing one rendered HTML document."""

    final_url: str
    site_name: str | None
    source_content: SourceContent
    image_candidates: list[ImageCandidate]
    logo_url: str | None


def _parse_rendered_html(html: str, final_url: str, *, require_text: bool = True) -> _ParsedPage:
    """HTML → SourceContent + image candidates + logo URL.

    ``require_text=False`` is used for crawled sub-pages: it's OK if a sub-page
    has thin content (e.g. a card directory), we just record what was found.
    """
    soup = BeautifulSoup(html, "lxml")

    title = (
        _extract_meta_string(soup, "og:title", "twitter:title")
        or (soup.title.get_text(strip=True) if soup.title else None)
    )
    description = _extract_meta_string(
        soup, "og:description", "twitter:description", "description"
    )
    site_name = _extract_meta_string(soup, "og:site_name") or title

    extracted_text = _extract_body_text(html, soup)

    if require_text and len(extracted_text.strip()) < 80:
        raise ScrapeError(
            "Could not extract enough text content from that page. "
            "It might be a single-page-app loading state, behind a paywall, "
            "or require auth. Try a different page or paste content manually.",
            status=422,
        )

    headings = _extract_headings(soup)
    image_candidates = _extract_images(soup, final_url)
    profile_candidates = _extract_profile_candidates(soup, final_url)
    document_cards = _extract_document_cards(soup, final_url)
    if document_cards:
        # Claim the cards' text before the LLM ever sees it — otherwise it
        # narrates the same titles into an invented, disconnected section.
        extracted_text = _strip_document_card_lines(extracted_text, document_cards)
    # Fast-path role stamping: without render evidence every candidate is
    # role="unknown", which lets a nav logo or a grid headshot win the hero
    # background. The filename and the profile-card structure are evidence we
    # DO have — use them. (No-ops on the render path, where roles are already
    # measured.)
    for candidate in image_candidates:
        if candidate.role == "unknown" and _looks_like_logo_url(candidate.url):
            candidate.role = "logo"
    # A roster page's card photos ARE headshots (3+ cards mirrors the render
    # evidence grid threshold). Keeps a directory's faces out of hero/section
    # backgrounds and out of the LLM's pinnable image pool.
    if len(profile_candidates) >= 3:
        profile_photo_urls = {p.photo_url for p in profile_candidates if p.photo_url}
        for candidate in image_candidates:
            if candidate.url in profile_photo_urls and candidate.role == "unknown":
                candidate.role = "portrait"
    links = _extract_links(soup, final_url)
    logo_url = _extract_logo_candidate(soup, final_url)
    nav_links = extract_nav_links(soup, final_url)
    body_link_clusters = extract_body_link_clusters(soup, final_url)
    social_links = extract_social_links(soup, final_url)

    source_content = SourceContent(
        source_kind="url",
        source_ref=final_url,
        title=title,
        description=description,
        raw_text=extracted_text,
        headings=headings,
        images=[c.url for c in image_candidates],
        links=links,
        nav_links=nav_links,
        body_link_clusters=body_link_clusters,
        social_links=social_links,
        url_path=urlparse(final_url).path or "/",
        subject_name=_leading_person_name(soup),
        image_metadata=[
            ImageMetadata(
                url=c.url,
                alt=c.alt,
                intent=c.intent,  # type: ignore[arg-type]
                role=c.role,  # type: ignore[arg-type]
                width=c.width,
                height=c.height,
                source_usage=c.source_usage,  # type: ignore[arg-type]
                context_heading=c.context_heading,
                caption=c.caption,
            )
            for c in image_candidates
        ],
        profile_candidates=profile_candidates,
        document_cards=document_cards,
    )
    return _ParsedPage(
        final_url=final_url,
        site_name=site_name,
        source_content=source_content,
        image_candidates=image_candidates,
        logo_url=logo_url,
    )


# --- bounded crawl --------------------------------------------------------------


# Asset extensions to never crawl — these aren't pages. Document extensions
# are sourced from nav_extraction.DOCUMENT_EXTENSIONS so the "is this a
# document" test stays in lockstep with find_document_link_clusters.
_NON_PAGE_EXTENSIONS = (
    *DOCUMENT_EXTENSIONS,
    ".zip", ".gz", ".tar", ".7z", ".rar",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".avif", ".ico",
    ".mp3", ".mp4", ".mov", ".webm", ".wav", ".m4a",
    ".css", ".js", ".json", ".xml", ".rss", ".atom",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
)

# Path segments that obviously aren't useful for site-generation context.
_SKIP_PATH_HINTS = (
    "/wp-admin", "/wp-login", "/cart", "/checkout", "/login", "/signin",
    "/signup", "/register", "/account", "/cdn-cgi", "/feed", "/api/",
    "/search", "/tag/", "/author/", "/page/", "/?", "/print",
)


def _normalize_crawl_url(url: str) -> str | None:
    """Strip fragments, normalize trailing slash, lowercase host. None ⇒ skip."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    host = parsed.netloc.lower()
    path = parsed.path or "/"
    # Drop trailing slash except for root
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    # Drop fragment; keep query — some sites use ?lang=en etc.
    return f"{parsed.scheme}://{host}{path}{('?' + parsed.query) if parsed.query else ''}"


# A translated mirror (/bm/committee, /zh/about, /fr-fr/produits) duplicates the
# whole site under one language segment. The translations are real content the
# owner maintains, but they carry no NEW structure, so on a bounded frontier
# they must not outrank pages we haven't seen in any language — MMTA's nine
# committee-member pages lost all 20 slots to /bm/* and /zh/* copies of pages
# already queued. Mirrors are crawled last (the `deferred` queue in
# _crawl_extra_pages), never dropped; page_inference then pairs each one with
# the page it translates.


def _path_key(url: str) -> str:
    """Lowercased path of a URL, without its trailing slash — the identity we
    compare translated paths against."""
    path = (urlparse(url).path or "/").lower()
    return path[:-1] if len(path) > 1 and path.endswith("/") else path


def _is_locale_mirror(path: str, *, entry_locale: str | None, known_paths: set[str]) -> bool:
    """True when ``path`` is a translated copy of the site we're already crawling.

    ``entry_locale`` is the entry URL's own locale segment, so scraping
    https://site.com/bm keeps /bm/* and treats it as the source language.
    """
    segments = [s for s in path.split("/") if s]
    if not segments:
        return False
    segment = locale_segment(path)
    if segment is None or segment == entry_locale:
        return False
    if len(segments) == 1:
        # A bare /zh, /de — the language switcher's landing page.
        return segment not in AMBIGUOUS_LOCALE_SEGMENTS
    # Deeper paths need evidence: /zh/about mirrors /about. Without a known
    # counterpart, /it/support may well be a real IT section.
    return "/" + "/".join(segments[1:]) in known_paths


def _is_crawlable_link(url: str, entry_host: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc.lower() != entry_host.lower():
        return False
    path_low = (parsed.path or "/").lower()
    if path_low.endswith(_NON_PAGE_EXTENSIONS):
        return False
    if any(h in path_low for h in _SKIP_PATH_HINTS):
        return False
    return True


# Crawl worker-pool sizing. Six workers keep the pipeline full while per-host
# politeness (4 slots + min-delay, services/polite.py) bounds pressure on any
# single host; the Playwright semaphore holds concurrent rendered tabs at the
# level the old batch-of-3 crawl exercised.
_CRAWL_WORKERS = 6
_CRAWL_PLAYWRIGHT_CONCURRENCY = 3


async def _crawl_extra_pages(
    context,
    entry_final_url: str,
    seed_links: list[str],
    *,
    max_pages: int,
    max_depth: int,
    timeout_ms: int,
    respect_robots: bool,
    extra_seed_urls: list[str] | None = None,
    fallback_seed_urls: list[str] | None = None,
    already_seen: set[str] | None = None,
    priority_seed_urls: set[str] | None = None,
    on_progress: "Callable[[int, str], Awaitable[None]] | None" = None,
    is_cancelled: "Callable[[], bool] | None" = None,
) -> tuple[list[_ParsedPage], list[str]]:
    """BFS-crawl same-domain pages starting from ``seed_links`` (already extracted
    from the entry page). Returns (parsed pages, leftover frontier URLs).

    Caps total at ``max_pages``, depth at ``max_depth``. Pages are fetched by a
    sliding-window pool of ``_CRAWL_WORKERS`` workers (per-host politeness still
    gates the real request rate), so one slow page no longer stalls the rest.

    Translated mirrors of the entry language (/zh/about beside /about) are
    crawled only after every other queued page — see ``_is_locale_mirror``.
    Roster/profile-card links (a committee page's links to its own members)
    jump to the *front* instead: they're what a Team block on the generated
    site actually needs, and a plain FIFO frontier lets them get crowded out
    by nav/footer links when a site has more pages than the crawl budget —
    see ``priority_seed_urls`` and the ``profile_candidates`` check below.

    The leftover frontier is what the BFS had queued but didn't process when
    the cap was hit. The router surfaces this so the frontend can offer
    "Crawl N more" without restarting from scratch.

    ``extra_seed_urls`` lets a resume call (POST /api/scrape/extend) seed the
    BFS with the prior crawl's leftover frontier.  ``already_seen`` lets the
    resume call avoid re-fetching URLs from the prior pass. ``priority_seed_urls``
    lets the caller mark some of ``seed_links``/``extra_seed_urls`` (e.g. the
    entry page's own profile-card links, when the entry page is itself a
    roster) as high-priority up front.

    ``fallback_seed_urls`` (the site's own sitemap) is queued LAST, behind every
    link the entry page actually shows. A sitemap is a complete inventory rather
    than a statement of importance, so it must not outrank the owner's own
    navigation — its job is to reach pages the link graph hides, not to reorder
    the ones it doesn't.
    """
    entry_parsed = urlparse(entry_final_url)
    entry_host = entry_parsed.netloc
    entry_norm = _normalize_crawl_url(entry_final_url)
    # `seen` starts with whatever caller already crawled (extend path) plus
    # the entry itself.
    seen: set[str] = set(already_seen) if already_seen else set()
    if entry_norm:
        seen.add(entry_norm)

    # Per-host politeness gates every fetch. Without this, parallel crawls
    # against a single host trigger 429s within seconds on real WAF'd sites.
    politeness = await get_politeness(entry_host)

    # The entry's own locale segment (None for an unprefixed site) — whatever
    # language the user pointed us at is the source language; every *other*
    # language's mirror is recognized against the paths we already know.
    entry_locale = locale_segment(urlparse(entry_final_url).path or "/")
    known_paths: set[str] = set()

    def _register_paths(urls) -> None:
        """Record same-host paths as pages this site is known to have."""
        for url in urls:
            if urlparse(url).netloc.lower() == entry_host.lower():
                known_paths.add(_path_key(url))

    _register_paths([entry_final_url, *seen])

    # depth 1 frontier seeded from the entry's links + any explicit extra seeds.
    # Each entry carries a discovery index so results can be re-sorted into the
    # deterministic (depth, discovery) order the old lockstep batches produced.
    #
    # `deferred` holds translated mirrors of pages we're already crawling. They
    # are real pages the owner maintains, so they stay in the queue — but they
    # only get fetched once nothing untranslated is left, otherwise a mirrored
    # site spends its whole budget saying the same things twice.
    # Queue items carry a tier (0 = untranslated, 1 = mirror) so the tier drives
    # the result order too, not just fetch order: downstream ranking reads
    # earlier pages as closer to the entry, and a translation of /about must
    # never outrank /about because the header happened to list it first.
    #
    # `priority` holds roster/profile-card links — a committee page's links to
    # its own members. They're drained before `frontier` so they win the page
    # budget over nav/footer/unrelated links when the site has more pages than
    # the crawl can afford, instead of losing out just because they happened
    # to be discovered later or appear lower in the page's HTML.
    discovery_count = 0
    priority: deque[tuple[str, int, int, int]] = deque()
    frontier: deque[tuple[str, int, int, int]] = deque()
    deferred: deque[tuple[str, int, int, int]] = deque()

    def _enqueue(norm: str, depth: int, *, priority_link: bool = False) -> None:
        nonlocal discovery_count
        is_mirror = _is_locale_mirror(
            _path_key(norm), entry_locale=entry_locale, known_paths=known_paths
        )
        if is_mirror:
            queue = deferred
        elif priority_link:
            queue = priority
        else:
            queue = frontier
        queue.append((norm, depth, discovery_count, 1 if is_mirror else 0))
        discovery_count += 1

    # Register every candidate path before queueing: /about must be known when
    # /bm/about is classified, whatever order the entry page lists them in.
    _all_seeds = [
        *(extra_seed_urls or []),
        *seed_links,
        *(fallback_seed_urls or []),
    ]
    _register_paths(_all_seeds)
    for link in _all_seeds:
        norm = _normalize_crawl_url(link)
        if not norm or norm in seen:
            continue
        if not _is_crawlable_link(norm, entry_host):
            continue
        seen.add(norm)
        _enqueue(norm, 1, priority_link=norm in (priority_seed_urls or ()))
        if len(priority) + len(frontier) + len(deferred) >= max_pages * 3:  # cap how many we even queue
            break

    # Sliding-window worker pool instead of lockstep batches: with batches of 3,
    # one slow Playwright fallback stalled two finished slots per round. Workers
    # pull from the shared frontier as they free up. Per-host politeness (slots
    # + min-delay) still bounds effective concurrency against a single host, and
    # a dedicated semaphore keeps Playwright tab pressure at the old level.
    # (tier, depth, discovery, page)
    collected: list[tuple[int, int, int, _ParsedPage]] = []
    in_flight = 0
    # Flips the first time nothing untranslated is queued *or* in flight. Until
    # then workers idle rather than start a mirror, so a translation can never
    # take a slot from a page no other language covers.
    mirrors_unlocked = False
    new_work = asyncio.Event()
    pw_sem = asyncio.Semaphore(_CRAWL_PLAYWRIGHT_CONCURRENCY)
    stop_logged = False

    def _should_stop() -> bool:
        nonlocal stop_logged
        if len(collected) >= max_pages:
            return True
        if is_cancelled and is_cancelled():
            if not stop_logged:
                logger.info("crawl cancelled by caller at %d pages", len(collected))
                stop_logged = True
            return True
        # Politeness circuit: too many consecutive failures on this host →
        # give up gracefully rather than keep hammering.
        if politeness.circuit_open:
            if not stop_logged:
                logger.warning(
                    "politeness circuit open for %s — stopping crawl at %d pages",
                    entry_host, len(collected),
                )
                stop_logged = True
            return True
        return False

    async def _fetch_one(item: tuple[str, int]) -> tuple[int, _ParsedPage | None]:
        url, depth = item
        if respect_robots and not await _robots_allows(url, USER_AGENT):
            return depth, None

        # Politeness slot gates concurrency + min-delay per host.
        async with politeness.slot():
            # 1. Try httpx-first fast path.
            fast = await try_fast_fetch(url)
            if isinstance(fast, FastFetchResult):
                politeness.record_success()
                try:
                    # CPU-heavy parse (trafilatura + lxml) off the event loop so
                    # the other crawl workers keep fetching while this one parses.
                    parsed = await asyncio.to_thread(
                        _parse_rendered_html,
                        fast.html,
                        fast.final_url,
                        require_text=False,
                    )
                    logger.debug("crawl httpx-fast %s", fast.final_url)
                    return depth, parsed
                except Exception as exc:  # noqa: BLE001
                    logger.info("crawl httpx-parse failed %s: %s", url, exc)
                    # Fall through to Playwright

            # If httpx hit a retriable HTTP status, record + back off but
            # don't fall through to Playwright — same host, same problem.
            if isinstance(fast, FastFetchSkipped) and fast.http_status in RETRIABLE_STATUS_CODES:
                politeness.record_failure(retriable=True)
                logger.info(
                    "crawl rate-limited %s status=%s — backing off",
                    url, fast.http_status,
                )
                return depth, None

            # 2. Fall back to Playwright (JS shell, thin content, or non-retriable error).
            if isinstance(fast, FastFetchSkipped):
                logger.debug(
                    "crawl httpx skipped %s (reason=%s) — using Playwright",
                    url, fast.reason,
                )
            try:
                async with pw_sem:
                    final_url, html = await _goto_and_render(
                        context, url, timeout_ms=timeout_ms
                    )
            except ScrapeError as exc:
                politeness.record_failure(retriable=exc.status in RETRIABLE_STATUS_CODES)
                logger.info("crawl skipped %s: %s", url, exc)
                return depth, None
            except Exception as exc:  # noqa: BLE001
                politeness.record_failure(retriable=False)
                logger.info("crawl failed %s: %s", url, exc)
                return depth, None
            try:
                parsed = await asyncio.to_thread(
                    _parse_rendered_html, html, final_url, require_text=False
                )
            except Exception as exc:  # noqa: BLE001
                politeness.record_failure(retriable=False)
                logger.info("crawl parse failed %s: %s", url, exc)
                return depth, None
            politeness.record_success()
            return depth, parsed

    async def _wait_for_work() -> None:
        """Park until another worker's fetch reports in (short timeout guards
        the clear/set race without busy-spinning)."""
        new_work.clear()
        try:
            await asyncio.wait_for(new_work.wait(), timeout=0.1)
        except asyncio.TimeoutError:
            # asyncio.TimeoutError is the builtin TimeoutError on the 3.11
            # runtime here, but a distinct class on ≤3.10 — catch the asyncio
            # one so this stays portable across both.
            pass

    async def _worker() -> None:
        nonlocal in_flight, mirrors_unlocked
        while True:
            if _should_stop():
                return
            if not priority and not frontier:
                if not mirrors_unlocked:
                    if in_flight:
                        # An in-flight fetch may still expand the frontier with
                        # untranslated pages (a roster page's member links).
                        # Idling here is what keeps a mirror from taking their
                        # slot — an empty frontier is not an exhausted one.
                        await _wait_for_work()
                        continue
                    mirrors_unlocked = True
                if not deferred:
                    if in_flight == 0:
                        return  # no queued work and nobody can produce more
                    await _wait_for_work()
                    continue
            url, depth, _discovered, _tier = (priority or frontier or deferred).popleft()
            in_flight += 1
            try:
                depth, parsed = await _fetch_one((url, depth))
            finally:
                in_flight -= 1
                new_work.set()
            if parsed is None or len(collected) >= max_pages:
                continue
            collected.append((_tier, depth, _discovered, parsed))
            if on_progress is not None:
                try:
                    await on_progress(len(collected), parsed.final_url)
                except Exception as exc:  # noqa: BLE001
                    # Progress reporting must never abort the crawl.
                    logger.debug("on_progress raised: %s", exc)
            # Expand frontier with this page's same-host links — but only if we
            # haven't hit the depth cap.
            if depth >= max_depth:
                continue
            _register_paths(parsed.source_content.links)
            # A page with a real roster (>= page_inference.ROSTER_MIN_PROFILES
            # cards) names its own members' pages via each card's profile_url —
            # the same signal page_inference.roster_detail_links reads after
            # the crawl. Those links jump the queue (see `priority` above) so
            # a Team block's member pages don't lose the page budget to nav
            # or footer links just because this roster wasn't crawled first.
            candidates = getattr(parsed.source_content, "profile_candidates", None) or []
            priority_urls: set[str] = set()
            if len(candidates) >= 2:
                for candidate in candidates:
                    if not candidate.profile_url:
                        continue
                    norm_p = _normalize_crawl_url(candidate.profile_url)
                    if norm_p:
                        priority_urls.add(norm_p)
            for child in parsed.source_content.links:
                norm = _normalize_crawl_url(child)
                if not norm or norm in seen:
                    continue
                if not _is_crawlable_link(norm, entry_host):
                    continue
                seen.add(norm)
                _enqueue(norm, depth + 1, priority_link=norm in priority_urls)
            new_work.set()

    if priority or frontier or deferred:
        queued = len(priority) + len(frontier) + len(deferred)
        worker_count = min(_CRAWL_WORKERS, max(1, queued))
        await asyncio.gather(*(_worker() for _ in range(min(worker_count, max_pages))))

    # Restore the deterministic (depth, discovery) order the old lockstep
    # batches produced — downstream ranking treats earlier pages as closer to
    # the entry page — with translated mirrors sorted behind their tier.
    collected.sort(key=lambda t: (t[0], t[1], t[2]))
    parsed_pages = [p for _t, _d, _i, p in collected]

    # Whatever the queues still hold when we stop is "unvisited" — surface it so
    # callers can resume via /api/scrape/extend. Untranslated pages lead, so a
    # "crawl N more" pass keeps picking up new content before translations.
    unvisited = [url for url, _depth, _i, _tier in (*priority, *frontier, *deferred)]
    return parsed_pages, unvisited


# --- top-level orchestration ----------------------------------------------------


async def _sitemap_seed_urls(entry_final_url: str) -> list[str]:
    """The site's sitemap URLs, for use as a last-resort crawl frontier.

    Wholly advisory — ``probe_sitemap`` already swallows every error and returns
    an empty result, and this adds a belt-and-braces guard so a surprise here can
    never take down a crawl that would otherwise have succeeded on links alone.
    Cost is one or two plain HTTP round-trips (1-3s) against a crawl measured in
    tens of seconds.
    """
    try:
        from app.services.sitemap import probe_sitemap

        result = await probe_sitemap(entry_final_url)
    except Exception as exc:  # noqa: BLE001 — advisory seed, never load-bearing
        logger.debug("sitemap seed unavailable for %s: %s", entry_final_url, exc)
        return []
    return list(result.urls)


async def scrape_url(
    url: str,
    *,
    respect_robots: bool = True,
    crawl: bool = True,
    crawl_max_pages: int = 20,
    crawl_max_depth: int = 3,
    on_progress: Callable[[int, str], Awaitable[None]] | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> ScrapeResult:
    """
    Main entry. Returns a ScrapeResult or raises ScrapeError with a
    user-friendly message + appropriate status.

    When ``crawl=True``, additionally walks same-domain links from the entry
    page (up to ``crawl_max_pages`` extra pages, depth ``crawl_max_depth``)
    and attaches them as ``source_content.discovered_pages``. The crawl shares
    one browser context with the primary render for efficiency.
    """
    try:
        await assert_public_url(url)
    except UnsafeUrlError as exc:
        raise ScrapeError(str(exc), status=400) from exc

    if respect_robots and not await _robots_allows(url, USER_AGENT):
        raise ScrapeError(
            f"This site's robots.txt disallows scraping {url}. "
            "Use a different page or paste the content manually.",
            status=403,
        )

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            user_agent=BROWSER_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            ignore_https_errors=True,
            locale="en-US",
            extra_http_headers=_BROWSER_HEADERS,
        )
        await context.add_init_script(_STEALTH_INIT_SCRIPT)
        await context.route("**/*", _route_block_heavy)

        try:
            # Try httpx-first for the entry too — same speed-win as for crawl
            # pages. Only spin up the Chromium tab when we actually need it.
            final_url: str
            html: str
            fast_entry = await try_fast_fetch(url, timeout_seconds=10.0)
            if isinstance(fast_entry, FastFetchResult):
                final_url, html = fast_entry.final_url, fast_entry.html
                logger.info("entry httpx-fast for %s", url)
            else:
                try:
                    final_url, html = await _goto_and_render(
                        context, url, timeout_ms=15000
                    )
                except ScrapeError:
                    raise
                except asyncio.TimeoutError as exc:
                    raise ScrapeError(f"Timeout fetching {url}", status=408) from exc
                except Exception as exc:  # noqa: BLE001
                    raise ScrapeError(f"Failed to fetch {url}: {exc}", status=502) from exc

            entry = await asyncio.to_thread(
                _parse_rendered_html, html, final_url, require_text=True
            )
            entry.source_content.url_path = None  # primary page has no path tag

            unvisited_urls: list[str] = []
            if crawl:
                # If the entry page IS the roster (the user pasted the committee
                # page directly), its member links deserve the same front-of-queue
                # treatment a roster discovered mid-crawl gets — see `priority`
                # in _crawl_extra_pages.
                entry_candidates = getattr(entry.source_content, "profile_candidates", None) or []
                priority_seed_urls: set[str] | None = None
                if len(entry_candidates) >= 2:
                    priority_seed_urls = {
                        norm
                        for c in entry_candidates
                        if c.profile_url
                        for norm in (_normalize_crawl_url(c.profile_url),)
                        if norm
                    }
                # The site's own inventory, as a LAST-resort seed set. The BFS
                # only ever sees pages some crawled page links to, so anything
                # reachable solely from a page beyond the budget — or from no
                # page at all — was previously invisible. Advisory: any failure
                # yields no URLs and the crawl proceeds on links alone.
                sitemap_seeds: list[str] = []
                if settings.crawl_seed_from_sitemap:
                    with stage("crawl_sitemap_seed"):
                        sitemap_seeds = await _sitemap_seed_urls(final_url)
                logger.info(
                    "crawling up to %d extra pages from %s (%d sitemap seed(s))",
                    crawl_max_pages, final_url, len(sitemap_seeds),
                )
                with stage("crawl_extra_pages"):
                    discovered, unvisited_urls = await _crawl_extra_pages(
                    context,
                    entry_final_url=final_url,
                    seed_links=entry.source_content.links,
                    fallback_seed_urls=sitemap_seeds,
                    max_pages=crawl_max_pages,
                    max_depth=crawl_max_depth,
                    timeout_ms=12000,
                    respect_robots=respect_robots,
                    priority_seed_urls=priority_seed_urls,
                    on_progress=on_progress,
                    is_cancelled=is_cancelled,
                )
                entry.source_content.discovered_pages = [
                    p.source_content for p in discovered
                ]
                # With the full page set known, body link clusters repeated
                # across pages are template chrome — purge their labels from
                # every page's raw_text so they don't read as content.
                strip_chrome_lines(entry.source_content)
                logger.info(
                    "crawl found %d additional pages, %d more in unvisited frontier",
                    len(discovered),
                    len(unvisited_urls),
                )
        finally:
            await context.close()
            await browser.close()

    brand_candidate = await _build_brand_candidate(
        entry.site_name, entry.logo_url
    )

    return ScrapeResult(
        url=url,
        final_url=entry.final_url,
        source_content=entry.source_content,
        brand_candidate=brand_candidate,
        image_candidates=entry.image_candidates,
        unvisited_urls=unvisited_urls,
    )


@dataclass
class ExtendCrawlResult:
    """Output of an extend pass — no entry page render, just additional pages."""

    additional_pages: list[SourceContent]
    unvisited_urls: list[str]


async def extend_crawl(
    entry_url: str,
    seed_urls: list[str],
    *,
    already_seen: list[str],
    max_more: int = 20,
    crawl_max_depth: int = 3,
    respect_robots: bool = True,
) -> ExtendCrawlResult:
    """
    Resume a crawl from a saved frontier without re-fetching the entry page.

    ``seed_urls`` is the prior call's ``unvisited_urls``.
    ``already_seen`` is the set of URLs the prior crawl already visited
        (so this pass doesn't duplicate them).
    """
    if not seed_urls:
        return ExtendCrawlResult(additional_pages=[], unvisited_urls=[])

    # Reject non-public entry/seed targets up front (each fetch is also guarded).
    try:
        await assert_public_url(entry_url)
        for seed in seed_urls:
            await assert_public_url(seed)
    except UnsafeUrlError as exc:
        raise ScrapeError(str(exc), status=400) from exc

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-features=IsolateOrigins,site-per-process",
            ],
        )
        context = await browser.new_context(
            user_agent=BROWSER_USER_AGENT,
            viewport={"width": 1366, "height": 900},
            ignore_https_errors=True,
            locale="en-US",
            extra_http_headers=_BROWSER_HEADERS,
        )
        await context.add_init_script(_STEALTH_INIT_SCRIPT)
        await context.route("**/*", _route_block_heavy)
        try:
            discovered, unvisited = await _crawl_extra_pages(
                context,
                entry_final_url=entry_url,
                seed_links=[],  # primary entry not re-rendered
                max_pages=max_more,
                max_depth=crawl_max_depth,
                timeout_ms=12000,
                respect_robots=respect_robots,
                extra_seed_urls=seed_urls,
                already_seen=set(already_seen),
            )
        finally:
            await context.close()
            await browser.close()

    return ExtendCrawlResult(
        additional_pages=[p.source_content for p in discovered],
        unvisited_urls=unvisited,
    )
