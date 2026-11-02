"""How we ask a website for one of its files — a photo, a logo, a PDF.

A page request goes out with the crawler's browser headers and waits its turn
in `polite`. The passes that download a source's FILES did neither: each opened
a bare `httpx.AsyncClient`, so it announced itself as `python-httpx/<version>`
and fetched outside the per-host budget, three at a time per pass (OCR, the
graphic screen, the vision pass, photo sampling, the brand mark, the push's
re-hosting). One generation sends dozens of those to the site it just crawled.

Imunify360 (and WAFs like it) judge the client ADDRESS and share that verdict
across every server that runs them. Measured Oct 2026: four sites on three
unrelated hosts (mykiddyland, mmta, watr, brightkids) started answering this
tool's IP with a WebShield challenge within two days of each other, while the
crawler's own request was byte-identical to August's. So every file request
states what the crawler states, and a file on a host the crawl is visiting
shares the crawl's slots instead of running beside them.

Pacing is LOOKUP-only (`polite.existing_politeness`): a host the crawl never
registered — a stock CDN, Facebook's CDN — is not throttled, because photo
sampling runs under a 4s budget per photo and queueing 40 stock photos behind a
200ms gap would time half of them out. Not a way past a challenge: the
answer to a host that challenges stays the owner's allowlist.
"""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager, nullcontext
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.services.polite import DEFAULT_CONCURRENCY, existing_politeness
from app.services.site_url import site_host

ASSET_HEADERS: dict[str, str] = {
    "User-Agent": settings.http_user_agent,
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


# The source's own hosts, capped even when no crawl registered them. A
# generation from a cached scrape, after a restart, or from a paste/PDF/Page
# that links the business's site has no crawl politeness to share, and the
# image passes then ran side by side — vision prefetch, OCR and the graphic
# screen at three each — nine requests at once to a site behind Imunify360.
#
# Deliberately NOT polite's registry: `get_politeness` is get-or-create and
# ignores its arguments, so a no-delay entry registered here would later pace a
# real crawl of that host at zero delay. This cap is concurrency only (no gap),
# because every timed pass reads files through it, and a queue must never turn
# into a timeout (see image_vision._fetch_image_bytes). Keyed by `site_host`, so
# `www.` and the bare domain share one cap, holding the loop the semaphore
# belongs to — a semaphore is bound to the event loop it first waits on.
_SOURCE_GATES: dict[str, tuple[asyncio.AbstractEventLoop, asyncio.Semaphore]] = {}


def gate_source_hosts(urls: list[str | None]) -> None:
    """Cap concurrent file requests to each host in `urls` at the crawl's own
    per-host concurrency, for the rest of this process (idempotent per loop).

    CDNs are never passed here: a stock or Facebook CDN is not the business's
    server, and its photos are sampled under a per-photo budget.
    """
    loop = asyncio.get_running_loop()
    for url in urls:
        if not (url or "").startswith(("http://", "https://")):
            continue
        host = site_host(urlparse(url or "").netloc)
        if not host:
            continue
        current = _SOURCE_GATES.get(host)
        if current is None or current[0] is not loop:
            _SOURCE_GATES[host] = (loop, asyncio.Semaphore(DEFAULT_CONCURRENCY))


def _slot_for(host: str) -> AbstractAsyncContextManager[object]:
    politeness = existing_politeness(host)
    if politeness is not None:
        return politeness.slot()
    gate = _SOURCE_GATES.get(site_host(host))
    if gate is not None and gate[0] is asyncio.get_running_loop():
        return gate[1]
    return nullcontext()


async def get_asset(
    client: httpx.AsyncClient, url: str, *, budget: float | None = None
) -> httpx.Response:
    """GET one file a site published, as the crawler would ask for it.

    Request-level headers, so a caller's pooled client needs no configuring and
    a client built for something else cannot leak its own User-Agent here.
    A host a crawl is visiting shares the crawl's slots; a source host with no
    crawl takes its concurrency cap (`gate_source_hosts`); any other host is
    asked directly.

    `budget` bounds the request itself and starts once the slot is held, so
    waiting for a slot is never what makes a timed caller give up (raises
    `TimeoutError` past it).
    """
    async with _slot_for(urlparse(url).netloc):
        request = client.get(url, headers=ASSET_HEADERS)
        if budget is None:
            return await request
        return await asyncio.wait_for(request, budget)


# One keep-alive client for every file download that brings none of its own.
# Each pass used to open a client per call, so every photo sampled during the
# render cost a fresh TCP + TLS handshake with the site — more connections, not
# fewer, which is the opposite of how a browser asks. Rebuilt for a new event
# loop (tests run one per case) and after `aclose_asset_client`.
# A monkeypatched `httpx.AsyncClient` (tests) also forces a rebuild, the
# `llm._shared_client` rule.
_shared_client: tuple[asyncio.AbstractEventLoop, object, httpx.AsyncClient] | None = None


def asset_client() -> httpx.AsyncClient:
    global _shared_client
    loop = asyncio.get_running_loop()
    factory = httpx.AsyncClient
    if (
        _shared_client is None
        or _shared_client[0] is not loop
        or _shared_client[1] is not factory
        or _shared_client[2].is_closed is True
    ):
        _shared_client = (
            loop,
            factory,
            factory(timeout=settings.vision_fetch_timeout_seconds, follow_redirects=True),
        )
    return _shared_client[2]


async def aclose_asset_client() -> None:
    """Close the shared file client (app shutdown)."""
    global _shared_client
    if _shared_client is not None and _shared_client[2].is_closed is False:
        await _shared_client[2].aclose()
    _shared_client = None
