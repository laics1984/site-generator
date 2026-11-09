"""Render generated pages in the existing private preview and collect evidence."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
from contextlib import asynccontextmanager
from typing import NotRequired, TypedDict
from urllib.parse import urlsplit

from playwright.async_api import BrowserContext, Error, Route, async_playwright

from app.models.builder_schema import GeneratedPage, GeneratedSite
from app.services.url_guard import assert_public_url

log = logging.getLogger(__name__)
WIDTHS = (375, 1280)
_RENDER_TIMEOUT = 12000
_RENDER_CONCURRENCY = 3
_INLINE_SCRIPT = re.compile(r"<script\b[^>]*>([^<]*)</script>", re.IGNORECASE)


class NodeMetrics(TypedDict):
    id: str
    overflow: bool
    clippedHeading: bool
    fontSize: float
    foreground: str | None
    background: str | None
    paddingTop: float
    paddingBottom: float
    inFrame: bool


class ViewMetrics(TypedDict):
    nodes: list[NodeMetrics]
    headings: list[str]
    sections: NotRequired[list[str]]
    horizontalOverflow: bool
    brokenImages: int
    image: NotRequired[str]


@asynccontextmanager
async def review_browser(url: str):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(service_workers="block")
            renderer = PreviewRenderer(url, context)
            await context.route("**/*", renderer.route)
            yield renderer
        finally:
            await browser.close()


class PreviewRenderer:
    def __init__(self, url: str, context: BrowserContext):
        self.url = url
        self.origin = urlsplit(url)[:2]
        self.context = context
        self.slots = asyncio.Semaphore(_RENDER_CONCURRENCY)

    async def route(self, route: Route) -> None:
        request = route.request
        url = urlsplit(request.url)
        same_origin = url[:2] == self.origin
        try:
            if request.method != "GET":
                await route.abort()
            elif request.url == self.url and request.resource_type == "document":
                response = await route.fetch(max_redirects=0, timeout=_RENDER_TIMEOUT)
                html = await response.text()
                # Vite dev injects an inline React bootstrap. Allow its exact
                # hash, and keep event handlers from input HTML inert.
                hashes = [
                    "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"
                    for script in _INLINE_SCRIPT.findall(html) if script.strip()
                ]
                csp = (
                    f"default-src 'none'; script-src 'self' {' '.join(hashes)}; "
                    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
                    "img-src http: https: data:; font-src https://fonts.gstatic.com; "
                    "frame-src about:; form-action 'none'; base-uri 'none'; object-src 'none'"
                )
                await route.fulfill(response=response, headers={
                    **response.headers, "content-security-policy": csp,
                })
            elif same_origin and request.resource_type == "script" and url.path.startswith(
                ("/assets/", "/src/", "/@vite/", "/@react-refresh", "/node_modules/.vite/", "/node_modules/vite/dist/client/")
            ):
                response = await route.fetch(max_redirects=0, timeout=_RENDER_TIMEOUT)
                if response.ok:
                    await route.fulfill(response=response)
                else:
                    await route.abort()
            elif not same_origin and (
                request.resource_type == "image"
                or (request.resource_type, url.hostname) in {
                    ("stylesheet", "fonts.googleapis.com"), ("font", "fonts.gstatic.com"),
                }
            ):
                if url.username or url.password:
                    await route.abort()
                    return
                await assert_public_url(request.url, allow_private=False)
                response = await route.fetch(max_redirects=0, timeout=3500, headers={
                    **request.headers, "cookie": "", "authorization": "", "referer": "",
                })
                # Do not delegate redirects to Chromium. An asset redirect
                # could otherwise escape the destination guard.
                if response.ok:
                    await route.fulfill(response=response)
                else:
                    await route.abort()
            else:
                await route.abort()
        except (Error, ValueError):
            await route.abort()

    async def measure(self, site: GeneratedSite, page: GeneratedPage, layout: dict, width: int, *,
                      screenshot: bool = False) -> ViewMetrics | None:
        async with self.slots:
            tab = await self.context.new_page()
            try:
                await tab.set_viewport_size({"width": width, "height": 900})
                # The shell and this page are sufficient. Collections, other
                # page bodies and CMS connection data stay out of the browser.
                payload = {
                    "slug": page.slug,
                    "layout": layout,
                    "site": {
                        **site.model_dump(mode="json", include={"site_name", "builder_styles", "google_fonts", "multilingual"}),
                        "pages": [
                            page.model_dump(mode="json", include={
                                "slug", "title", "is_homepage", "locale", "translation_of", "body_schema",
                            }) if item.slug == page.slug else item.model_dump(
                                mode="json", include={"slug", "locale", "translation_of"},
                            )
                            for item in site.pages
                        ],
                    },
                }
                await tab.add_init_script("window.__BENCH__ = " + json.dumps(payload))
                response = await tab.goto(self.url, wait_until="domcontentloaded", timeout=_RENDER_TIMEOUT)
                if not response or not response.ok:
                    return None
                await tab.locator('html[data-bench-ready="true"]').wait_for(state="attached", timeout=_RENDER_TIMEOUT)
                if screenshot:
                    await tab.evaluate("""async () => {
                      const doc = document.querySelector('iframe').contentDocument
                      const images = [...doc.images].filter(img => img.getBoundingClientRect().top < innerHeight)
                      await Promise.race([
                        Promise.all(images.map(img => img.decode().catch(() => {}))),
                        new Promise(resolve => setTimeout(resolve, 1400)),
                      ])
                    }""")
                metrics = await tab.evaluate("window.__BENCH_MEASURE__()")
                if screenshot:
                    jpeg = await tab.locator("iframe").screenshot(type="jpeg", quality=65, timeout=_RENDER_TIMEOUT)
                    metrics["image"] = base64.b64encode(jpeg).decode()
                return metrics
            except Error:
                log.debug("Design review could not render page %s at %s", page.slug, width, exc_info=True)
                return None
            finally:
                await tab.close()
