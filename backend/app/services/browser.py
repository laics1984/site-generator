"""Headless Chromium: launching it, and getting one URL rendered in it.

Extracted because three separate modules need a rendered page and only one of
them is the crawler. The browser lifecycle (launch args, stealth patch, header
set, resource blocking) was duplicated three times inside ``scraper.py``, and
``content_collections.py`` and the Facebook reader both reached in and imported
``scraper._fetch_rendered_html`` — a private function, lazily, so a rename would
have failed at runtime rather than at import.

The split of responsibility:

* **here** — launch a browser, open a URL in it, map HTTP failures to a clear
  error, hand back the live page.
* **the caller** — decide what to do with that page. ``scraper.py`` autoscrolls
  and stamps render evidence onto the DOM because its image pipeline reads that
  back; nothing else wants either, so neither belongs in the shared path.

``RenderedPage`` is a named result on purpose. The old private helper returned a
bare ``(final_url, html)`` tuple, and ``content_collections`` unpacked it
backwards — every render-fallback listing page parsed a URL string as markup and
silently found nothing.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, NamedTuple

from playwright.async_api import async_playwright

from app.config import settings
from app.services.url_guard import UnsafeUrlError, assert_public_url

logger = logging.getLogger(__name__)


class RenderError(Exception):
    """A page that could not be rendered. Carries a user-facing status code."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class RenderedPage(NamedTuple):
    final_url: str
    html: str


# Chrome on macOS. Some WAFs reject the default Playwright UA outright on the
# first request; the UA just stops naive blocklist matching at the door. Single
# source of truth in config, shared with the httpx fast-fetch path.
BROWSER_USER_AGENT = settings.http_user_agent

# Headers a real Chrome sends. Many WAFs flag requests missing these.
BROWSER_HEADERS = {
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

# Patched into every page on context creation to mask the most-obvious
# Playwright tell. Doesn't beat sophisticated stealth detection but clears most.
STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'plugins', {
  get: () => [1, 2, 3, 4, 5].map(() => ({}))
});
Object.defineProperty(navigator, 'languages', {
  get: () => ['en-US', 'en']
});
window.chrome = window.chrome || { runtime: {} };
"""

_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-blink-features=AutomationControlled",
    "--disable-features=IsolateOrigins,site-per-process",
]

_VIEWPORT = {"width": 1366, "height": 900}


async def block_heavy_resources(route, request) -> None:
    """Block media/font/websocket resources to speed up renders."""
    if request.resource_type in {"media", "font", "websocket"}:
        await route.abort()
    else:
        await route.continue_()


@asynccontextmanager
async def browser_context() -> AsyncIterator:
    """A configured Chromium context, torn down on exit.

    Every caller wants the same launch args, stealth patch, headers and resource
    blocking — differences between them were drift, not intent.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True, args=_LAUNCH_ARGS)
        context = await browser.new_context(
            user_agent=BROWSER_USER_AGENT,
            viewport=_VIEWPORT,
            ignore_https_errors=True,
            locale="en-US",
            extra_http_headers=BROWSER_HEADERS,
        )
        await context.add_init_script(STEALTH_INIT_SCRIPT)
        await context.route("**/*", block_heavy_resources)
        try:
            yield context
        finally:
            await context.close()
            await browser.close()


@asynccontextmanager
async def rendered_page(context, url: str, *, timeout_ms: int) -> AsyncIterator:
    """Open `url` in `context` and yield the live page, closing it on exit.

    Raises `RenderError` for a URL that fails the SSRF guard or for a 4xx/5xx
    response. Yielding the page rather than its HTML is what lets the crawler do
    its own scroll-and-stamp work while other callers just read the content.
    """
    try:
        await assert_public_url(url)
    except UnsafeUrlError as exc:
        raise RenderError(str(exc), status=400) from exc

    page = await context.new_page()
    try:
        response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        if response is None:
            raise RenderError(f"No response from {url}", status=502)
        if response.status == 403:
            raise RenderError(
                f"{url} blocked our request (403). The site has bot-detection "
                "active and won't render in a headless browser. Try pasting the "
                "page content into the document tab instead, or pick a different "
                "URL on the same site that's less protected (e.g. a blog post).",
                status=403,
            )
        if response.status == 401:
            raise RenderError(
                f"{url} requires authentication (401). Paste the content "
                "directly into the document tab instead.",
                status=401,
            )
        if response.status == 429:
            raise RenderError(
                f"{url} is rate-limiting us (429). Wait a minute and try again.",
                status=429,
            )
        if response.status >= 400:
            raise RenderError(f"Page returned {response.status} for {url}", status=502)
        try:
            await page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        yield page
    finally:
        await page.close()


async def render_url(url: str, *, timeout_ms: int | None = None) -> RenderedPage:
    """Single-shot render in its own browser.

    For one-off pages outside a crawl. A crawl should open one context via
    `browser_context()` and reuse it across pages rather than paying the launch
    cost per URL.
    """
    if timeout_ms is None:
        timeout_ms = settings.playwright_goto_timeout_ms
    async with browser_context() as context:
        async with rendered_page(context, url, timeout_ms=timeout_ms) as page:
            return RenderedPage(final_url=page.url, html=await page.content())
