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

from app.models.brand import BrandIdentity
from app.models.content_blocks import ImageMetadata, ProfileCandidate, SourceContent
from app.services.timing import stage
from app.services.fast_fetch import (
    FastFetchResult,
    FastFetchSkipped,
    try_fast_fetch,
)
from app.services.image_evidence import ImageEvidence, classify_role, parse_evidence
from app.services.logo import extract_palette_from_image_bytes
from app.services.nav_extraction import (
    extract_body_link_clusters,
    extract_nav_links,
    extract_social_links,
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
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/128.0.0.0 Safari/537.36"
)

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

    Returns (final_url, html). Raises ScrapeError for 4xx/5xx responses.
    """
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
    url: str, *, timeout_ms: int = 15000
) -> tuple[str, str]:
    """Single-shot render — launches its own browser. Use _fetch_many for crawls."""
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
_PROFILE_ROLE_HINTS = (
    "role",
    "title",
    "position",
    "designation",
    "job",
    "office",
)
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
    # person. _nearest_profile_container's fallback accepts any ancestor with
    # an h2-h5, so ordinary content sections reach the name check.
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
}

# A real name never starts with a determiner/possessive/CTA verb. Catches the
# long tail of headings the exact-match set above can't enumerate
# ("Our Community Programmes", "Meet Your Dentists", "Why Families Trust Us").
_NON_NAME_LEAD_TOKENS = {
    "our", "the", "your", "my", "their", "this", "these", "a", "an",
    "meet", "about", "why", "what", "how", "who", "where", "when",
    "welcome", "contact", "discover", "explore", "join", "visit", "view",
    "see", "find", "get", "learn", "read", "book", "call",
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


def _absolute_url(base: str, src: str) -> str | None:
    if not src or src.startswith("data:"):
        return None
    return urljoin(base, src)


def _looks_like_icon(url: str, alt: str) -> bool:
    low = url.lower()
    if any(h in low for h in _BAD_IMG_HINTS):
        return True
    if "favicon" in low:
        return True
    if alt and len(alt) > 0 and alt.lower() in {"icon", "logo icon"}:
        return True
    return False


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    m = re.search(r"\d+", value)
    return int(m.group(0)) if m else None


def _best_srcset_candidate(srcset: str | None) -> str | None:
    """Return the highest-density/width URL from a srcset string."""
    if not srcset:
        return None
    best_url: str | None = None
    best_score = -1.0
    for raw_part in srcset.split(","):
        part = raw_part.strip()
        if not part:
            continue
        pieces = part.split()
        url = pieces[0]
        score = 1.0
        if len(pieces) > 1:
            descriptor = pieces[1].lower()
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
    return best_url


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
    srcset_candidate = _best_srcset_candidate(srcset)

    source = img.find_previous_sibling("source")
    if source is None and isinstance(img.parent, Tag) and img.parent.name == "picture":
        sources = [s for s in img.parent.find_all("source") if isinstance(s, Tag)]
        source = sources[-1] if sources else None
    if isinstance(source, Tag):
        source_srcset = source.get("srcset") or source.get("data-srcset")
        if isinstance(source_srcset, list):
            source_srcset = source_srcset[0] if source_srcset else None
        picture_candidate = _best_srcset_candidate(
            source_srcset if isinstance(source_srcset, str) else None
        )
        if picture_candidate:
            srcset_candidate = picture_candidate

    src_low = (src or "").lower().split("?", 1)[0]
    if srcset_candidate and (
        not src
        or _looks_like_icon(src, "")
        or src_low.endswith(".svg")
        or "placeholder" in src_low
    ):
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


def _nearest_profile_container(img: Tag) -> Tag | None:
    current = img.parent
    fallback: Tag | None = None
    depth = 0
    while isinstance(current, Tag) and current.name not in {"body", "html"} and depth < 7:
        if _has_any_hint(current, _PROFILE_CONTAINER_HINTS):
            img_count = len([i for i in current.find_all("img") if isinstance(i, Tag)])
            if img_count > 1 and fallback is not None:
                return fallback
            return current
        if fallback is None and current.find(["h2", "h3", "h4", "h5"]):
            fallback = current
        current = current.parent
        depth += 1
    return fallback


def _row_text_sibling_for_profile(img: Tag) -> Tag | None:
    """Fallback for layouts where portrait and profile copy live in sibling columns."""
    current = img.parent
    depth = 0
    while isinstance(current, Tag) and current.name not in {"body", "html"} and depth < 6:
        children = [child for child in current.find_all(recursive=False) if isinstance(child, Tag)]
        if len(children) >= 2:
            row_hints = _has_any_hint(current, ("row", "columns", "grid")) or any(
                _has_any_hint(child, ("column", "col", "cell")) for child in children
            )
            if row_hints:
                owner = next(
                    (
                        child
                        for child in children
                        if child is img or child.find(lambda t: t is img) is not None
                    ),
                    None,
                )
                if owner is not None:
                    for sibling in children:
                        if sibling is owner:
                            continue
                        if not _extract_profile_name(sibling):
                            continue
                        text = _clean_line(sibling.get_text(" ", strip=True))
                        if len(text) < 24:
                            continue
                        return sibling
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


def _extract_profile_name(container: Tag) -> str | None:
    hinted = _find_text_by_hints(container, _PROFILE_NAME_HINTS)
    if hinted and _looks_like_person_name(hinted):
        return hinted

    for heading in container.find_all(["h2", "h3", "h4", "h5"]):
        if not isinstance(heading, Tag):
            continue
        text = _clean_line(heading.get_text(" ", strip=True))
        if _looks_like_person_name(text):
            return text

    for line in _text_lines(container)[:5]:
        if _looks_like_person_name(line):
            return line
    return None


def _extract_profile_role(container: Tag, name: str) -> str | None:
    hinted = _find_text_by_hints(container, _PROFILE_ROLE_HINTS)
    if hinted and hinted != name and len(hinted) <= 90:
        return hinted

    lines = _text_lines(container)
    try:
        name_index = next(i for i, line in enumerate(lines) if line == name)
    except StopIteration:
        name_index = -1
    for line in lines[name_index + 1 : name_index + 4]:
        if line == name or _looks_like_person_name(line):
            continue
        if len(line) <= 90:
            return line
    return None


def _extract_profile_bio(container: Tag, name: str, role: str | None) -> str | None:
    kept: list[str] = []
    for line in _text_lines(container):
        if line == name or (role and line == role):
            continue
        if len(line) <= 3:
            continue
        kept.append(line)
    if not kept:
        return None
    bio = " ".join(kept)
    return bio[:280]


def _extract_profile_candidates(
    soup: BeautifulSoup, base_url: str
) -> list[ProfileCandidate]:
    """Extract likely profile cards where a portrait and nearby person text agree."""
    profiles: list[ProfileCandidate] = []
    seen: set[tuple[str, str | None]] = set()

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

        container = _nearest_profile_container(img)
        if container is None:
            container = _row_text_sibling_for_profile(img)
        if container is None:
            continue
        name = _extract_profile_name(container)
        if not name:
            continue
        role = _extract_profile_role(container, name)
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
                confidence=0.9 if role else 0.8,
            )
        )

    return profiles[:24]


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


def _extract_links(soup: BeautifulSoup, base_url: str) -> list[str]:
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
    return out[:50]


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
    try:
        async with httpx.AsyncClient(
            timeout=10.0, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        ) as client:
            resp = await client.get(logo_url)
            resp.raise_for_status()
            image_bytes = resp.content
    except httpx.HTTPError as exc:
        logger.warning("Failed to fetch logo %s: %s", logo_url, exc)
        return None

    try:
        extraction = extract_palette_from_image_bytes(image_bytes)
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
    )
    return _ParsedPage(
        final_url=final_url,
        site_name=site_name,
        source_content=source_content,
        image_candidates=image_candidates,
        logo_url=logo_url,
    )


# --- bounded crawl --------------------------------------------------------------


# Asset extensions to never crawl — these aren't pages.
_NON_PAGE_EXTENSIONS = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
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
    already_seen: set[str] | None = None,
    on_progress: "Callable[[int, str], Awaitable[None]] | None" = None,
    is_cancelled: "Callable[[], bool] | None" = None,
) -> tuple[list[_ParsedPage], list[str]]:
    """BFS-crawl same-domain pages starting from ``seed_links`` (already extracted
    from the entry page). Returns (parsed pages, leftover frontier URLs).

    Caps total at ``max_pages``, depth at ``max_depth``. Pages are fetched by a
    sliding-window pool of ``_CRAWL_WORKERS`` workers (per-host politeness still
    gates the real request rate), so one slow page no longer stalls the rest.

    The leftover frontier is what the BFS had queued but didn't process when
    the cap was hit. The router surfaces this so the frontend can offer
    "Crawl N more" without restarting from scratch.

    ``extra_seed_urls`` lets a resume call (POST /api/scrape/extend) seed the
    BFS with the prior crawl's leftover frontier.  ``already_seen`` lets the
    resume call avoid re-fetching URLs from the prior pass.
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

    # depth 1 frontier seeded from the entry's links + any explicit extra seeds.
    # Each entry carries a discovery index so results can be re-sorted into the
    # deterministic (depth, discovery) order the old lockstep batches produced.
    discovery_count = 0
    frontier: deque[tuple[str, int, int]] = deque()
    for link in [*(extra_seed_urls or []), *seed_links]:
        norm = _normalize_crawl_url(link)
        if not norm or norm in seen:
            continue
        if not _is_crawlable_link(norm, entry_host):
            continue
        seen.add(norm)
        frontier.append((norm, 1, discovery_count))
        discovery_count += 1
        if len(frontier) >= max_pages * 3:  # cap how many we even queue
            break

    # Sliding-window worker pool instead of lockstep batches: with batches of 3,
    # one slow Playwright fallback stalled two finished slots per round. Workers
    # pull from the shared frontier as they free up. Per-host politeness (slots
    # + min-delay) still bounds effective concurrency against a single host, and
    # a dedicated semaphore keeps Playwright tab pressure at the old level.
    collected: list[tuple[int, int, _ParsedPage]] = []  # (depth, discovery, page)
    in_flight = 0
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
                    parsed = _parse_rendered_html(
                        fast.html, fast.final_url, require_text=False
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
                parsed = _parse_rendered_html(html, final_url, require_text=False)
            except Exception as exc:  # noqa: BLE001
                politeness.record_failure(retriable=False)
                logger.info("crawl parse failed %s: %s", url, exc)
                return depth, None
            politeness.record_success()
            return depth, parsed

    async def _worker() -> None:
        nonlocal in_flight, discovery_count
        while True:
            if _should_stop():
                return
            if not frontier:
                if in_flight == 0:
                    return  # no queued work and nobody can produce more
                # Another worker's in-flight fetch may expand the frontier —
                # wait for a completion signal (short timeout guards the
                # clear/set race without busy-spinning).
                new_work.clear()
                try:
                    await asyncio.wait_for(new_work.wait(), timeout=0.1)
                except asyncio.TimeoutError:
                    # asyncio.wait_for raises asyncio.TimeoutError, which is a
                    # DISTINCT class from the builtin TimeoutError on Python 3.10
                    # (the runtime here) — only aliased to it in 3.11+. Catch the
                    # asyncio one so this is portable across both.
                    pass
                continue
            url, depth, _discovered = frontier.popleft()
            in_flight += 1
            try:
                depth, parsed = await _fetch_one((url, depth))
            finally:
                in_flight -= 1
                new_work.set()
            if parsed is None or len(collected) >= max_pages:
                continue
            collected.append((depth, _discovered, parsed))
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
            for child in parsed.source_content.links:
                norm = _normalize_crawl_url(child)
                if not norm or norm in seen:
                    continue
                if not _is_crawlable_link(norm, entry_host):
                    continue
                seen.add(norm)
                frontier.append((norm, depth + 1, discovery_count))
                discovery_count += 1
            new_work.set()

    if frontier:
        worker_count = min(_CRAWL_WORKERS, max(1, len(frontier)))
        await asyncio.gather(*(_worker() for _ in range(min(worker_count, max_pages))))

    # Restore the deterministic (depth, discovery) order the old lockstep
    # batches produced — downstream ranking treats earlier pages as closer to
    # the entry page.
    collected.sort(key=lambda t: (t[0], t[1]))
    parsed_pages = [p for _d, _i, p in collected]

    # Whatever the frontier still holds when we stop is "unvisited" — surface
    # it so callers can resume via /api/scrape/extend.
    unvisited = [url for url, _depth, _i in frontier]
    return parsed_pages, unvisited


# --- top-level orchestration ----------------------------------------------------


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
    if not url.startswith(("http://", "https://")):
        raise ScrapeError("URL must start with http:// or https://", status=400)

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

            entry = _parse_rendered_html(html, final_url, require_text=True)
            entry.source_content.url_path = None  # primary page has no path tag

            unvisited_urls: list[str] = []
            if crawl:
                logger.info("crawling up to %d extra pages from %s", crawl_max_pages, final_url)
                with stage("crawl_extra_pages"):
                    discovered, unvisited_urls = await _crawl_extra_pages(
                    context,
                    entry_final_url=final_url,
                    seed_links=entry.source_content.links,
                    max_pages=crawl_max_pages,
                    max_depth=crawl_max_depth,
                    timeout_ms=12000,
                    respect_robots=respect_robots,
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
