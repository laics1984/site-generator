"""
Fast pre-scrape probe to learn a site's true scope before paying for Playwright.

Reads:
  1. /robots.txt for `Sitemap:` directives (preferred — author-curated)
  2. Common sitemap locations as fallback: /sitemap.xml, /sitemap_index.xml,
     /sitemap.xml.gz

Returns counts + a small sample of URLs so the UI can show the user
"Found a sitemap: 142 pages on this site" before deciding how aggressively
to crawl.

All HTTP via httpx (no Playwright). Per-probe wall time ~1-3s.

We deliberately do NOT walk sitemap_index entries recursively beyond depth 2 —
some sites publish thousands of sub-sitemaps and the user only needs a rough
total. We sample the first N and extrapolate if needed.
"""

from __future__ import annotations

import gzip
import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse
from xml.etree import ElementTree as ET

import httpx

logger = logging.getLogger(__name__)


_USER_AGENT = "WebtreeSiteGenerator/0.2 (+sitemap-probe)"
_HTTP_TIMEOUT = 8.0
_SITEMAP_MAX_BYTES = 5 * 1024 * 1024  # 5 MB — biggest reasonable sitemap
_MAX_SUB_SITEMAPS = 8  # cap depth-2 sitemap_index recursion
_MAX_URLS_RETURNED = 500  # we don't need to ship 50k URLs to the frontend


@dataclass
class SitemapProbeResult:
    """What the probe found."""

    has_sitemap: bool
    total_urls: int
    urls: list[str] = field(default_factory=list)  # sample, capped at _MAX_URLS_RETURNED
    sources: list[str] = field(default_factory=list)  # which sitemap URLs we read


_COMMON_PATHS = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap.xml.gz",
)


# Sitemap XML namespace — most sitemaps use it; some don't.
_SM_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


async def probe_sitemap(entry_url: str) -> SitemapProbeResult:
    """
    Look up sitemaps for the entry's host and return a scope estimate.

    On any error or missing sitemaps, returns SitemapProbeResult(has_sitemap=False).
    Callers can then fall back to BFS-only crawling.
    """
    parsed = urlparse(entry_url)
    if parsed.scheme not in ("http", "https"):
        return SitemapProbeResult(has_sitemap=False, total_urls=0)
    base = f"{parsed.scheme}://{parsed.netloc}"

    async with httpx.AsyncClient(
        timeout=_HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": _USER_AGENT, "Accept": "application/xml, text/xml, */*"},
    ) as client:
        # 1. robots.txt → Sitemap directives (authoritative)
        candidate_urls = await _read_robots_sitemaps(client, base)

        # 2. fall back to common locations
        if not candidate_urls:
            candidate_urls = [urljoin(base, p) for p in _COMMON_PATHS]

        all_urls: list[str] = []
        sources_used: list[str] = []
        for sm_url in candidate_urls:
            urls, used = await _read_sitemap_recursive(client, sm_url, depth=0)
            if urls:
                all_urls.extend(urls)
                sources_used.extend(used)
            if len(all_urls) >= _MAX_URLS_RETURNED:
                break

        # De-dupe while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for u in all_urls:
            if u not in seen:
                seen.add(u)
                deduped.append(u)

        if not deduped:
            return SitemapProbeResult(has_sitemap=False, total_urls=0)

        return SitemapProbeResult(
            has_sitemap=True,
            total_urls=len(deduped),
            urls=deduped[:_MAX_URLS_RETURNED],
            sources=sources_used,
        )


async def _read_robots_sitemaps(client: httpx.AsyncClient, base: str) -> list[str]:
    """Parse robots.txt and return any `Sitemap:` URLs found."""
    try:
        resp = await client.get(f"{base}/robots.txt")
        if resp.status_code != 200 or not resp.text:
            return []
    except httpx.HTTPError as exc:
        logger.debug("robots.txt fetch failed for %s: %s", base, exc)
        return []

    sitemaps: list[str] = []
    for line in resp.text.splitlines():
        m = re.match(r"^\s*sitemap\s*:\s*(\S+)\s*$", line, flags=re.IGNORECASE)
        if m:
            sitemaps.append(m.group(1).strip())
    return sitemaps


async def _read_sitemap_recursive(
    client: httpx.AsyncClient, sitemap_url: str, *, depth: int
) -> tuple[list[str], list[str]]:
    """
    Returns (urls, sources_used).

    Handles two sitemap shapes:
      - <urlset>: a leaf sitemap listing actual page URLs → return them
      - <sitemapindex>: an index pointing at sub-sitemaps → recurse (capped)
    """
    if depth > 2:
        return [], []

    try:
        resp = await client.get(sitemap_url)
        if resp.status_code != 200:
            return [], []
        content = resp.content[:_SITEMAP_MAX_BYTES]
    except httpx.HTTPError as exc:
        logger.debug("sitemap fetch failed %s: %s", sitemap_url, exc)
        return [], []

    # Handle gzipped sitemaps
    if sitemap_url.endswith(".gz") or resp.headers.get("content-type", "").startswith(
        "application/gzip"
    ):
        try:
            content = gzip.decompress(content)
        except (OSError, EOFError) as exc:
            logger.debug("gzip decompress failed for %s: %s", sitemap_url, exc)
            return [], []

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        logger.debug("sitemap parse failed for %s: %s", sitemap_url, exc)
        return [], []

    tag = _strip_ns(root.tag).lower()
    urls: list[str] = []
    sources: list[str] = [sitemap_url]

    if tag == "urlset":
        for url_el in root.iter():
            if _strip_ns(url_el.tag).lower() == "loc" and url_el.text:
                urls.append(url_el.text.strip())
    elif tag == "sitemapindex":
        sub_urls: list[str] = []
        for sm_el in root.iter():
            if _strip_ns(sm_el.tag).lower() == "loc" and sm_el.text:
                sub_urls.append(sm_el.text.strip())
        # Cap recursion to avoid massive index-of-indexes
        for sub in sub_urls[:_MAX_SUB_SITEMAPS]:
            sub_found, sub_sources = await _read_sitemap_recursive(
                client, sub, depth=depth + 1
            )
            urls.extend(sub_found)
            sources.extend(sub_sources)
            if len(urls) >= _MAX_URLS_RETURNED:
                break
    return urls, sources


def _strip_ns(tag: str) -> str:
    """``{http://...}urlset`` → ``urlset``."""
    if tag.startswith("{"):
        return tag.split("}", 1)[1]
    return tag
