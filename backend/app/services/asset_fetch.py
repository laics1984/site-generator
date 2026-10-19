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

from urllib.parse import urlparse

import httpx

from app.config import settings
from app.services.polite import existing_politeness

ASSET_HEADERS: dict[str, str] = {
    "User-Agent": settings.http_user_agent,
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


async def get_asset(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET one file a site published, as the crawler would ask for it.

    Request-level headers, so a caller's pooled client needs no configuring and
    a client built for something else cannot leak its own User-Agent here.
    """
    politeness = existing_politeness(urlparse(url).netloc)
    if politeness is None:
        return await client.get(url, headers=ASSET_HEADERS)
    async with politeness.slot():
        return await client.get(url, headers=ASSET_HEADERS)
