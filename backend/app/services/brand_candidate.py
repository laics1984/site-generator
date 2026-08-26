"""A detected mark → a `BrandIdentity` with its palette and render verdict.

Nothing here is scrape-specific: it takes a `LogoCandidate` (a URL or an inline
SVG), fetches it, reads the palette off the pixels, and decides whether the mark
may be rendered as the site's logo. It lived inside ``scraper.py`` as a private
function, which meant the Facebook reader had to import a private name from a
2.6k-line module it otherwise shares nothing with.

The render verdict is the load-bearing part (see CLAUDE.md contract #3):
`logo_render_ok` comes from the **decoded** pixel size, never from a `sizes`
attribute or a URL, and every consumer gates on it rather than on `logo_url`.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import httpx

from app.config import settings
from app.models.brand import BrandIdentity
from app.services.logo import extract_palette_from_image_bytes
from app.services.logo_extraction import LogoCandidate, is_renderable
from app.services.url_guard import is_public_url

logger = logging.getLogger(__name__)

# Plain identifying UA for asset fetches — these endpoints aren't WAF-gated the
# way page HTML is, so there's no reason to present as Chrome.
_ASSET_USER_AGENT = "WebtreeSiteGenerator/0.2 (+contact: hello@example.com)"


async def build_brand_candidate(
    site_name: str | None,
    logo: LogoCandidate | None,
    *,
    favicon_url: str | None = None,
) -> BrandIdentity | None:
    """Fetch the detected mark, read its palette, and decide whether it may be
    rendered as the brand logo.

    Every failure past this point degrades to a name-only brand rather than
    None: the site name is worth keeping even when the logo 404s, and losing it
    used to force the generator back onto the LLM's guess.

    `favicon_url` is the site icon the reader found declared on the page. It is
    carried through every degraded path: a logo that fails to fetch says nothing
    about whether the page declared an icon. Falling back to the mark when it is
    absent is `BrandIdentity`'s own rule, not restated here.
    """
    name_only = (
        BrandIdentity(name=site_name, favicon_url=favicon_url, mood=None)
        if site_name
        else None
    )
    if logo is None:
        return name_only

    if logo.data_url and not logo.url:
        # Inline <svg> — already in hand, nothing to fetch.
        image_bytes = base64.b64decode(logo.data_url.split(",", 1)[1])
    else:
        logo_url = logo.url or ""
        if not await is_public_url(logo_url):
            logger.warning("Refusing to fetch logo from non-public URL %s", logo_url)
            return name_only
        try:
            async with httpx.AsyncClient(
                timeout=settings.robots_fetch_timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": _ASSET_USER_AGENT},
            ) as client:
                resp = await client.get(logo_url)
                resp.raise_for_status()
                image_bytes = resp.content
        except httpx.HTTPError as exc:
            logger.warning("Failed to fetch logo %s: %s", logo_url, exc)
            return name_only

    try:
        # PIL decode + quantize is CPU-bound — keep it off the event loop.
        extraction = await asyncio.to_thread(extract_palette_from_image_bytes, image_bytes)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to extract palette from %s: %s", logo.ref, exc)
        return name_only

    render_ok = is_renderable(
        logo.source, size=extraction.size, is_vector=extraction.is_vector
    )
    logger.info(
        "Brand mark: source=%s size=%s render_ok=%s ref=%s",
        logo.source,
        extraction.size or ("vector" if extraction.is_vector else "?"),
        render_ok,
        (logo.url or "inline-svg"),
    )

    return BrandIdentity(
        name=site_name or "Untitled",
        logo_url=logo.url,
        logo_data_url=extraction.logo_data_url,
        extracted_palette=extraction.palette,
        logo_is_light=extraction.logo_is_light,
        favicon_url=favicon_url,
        logo_source=logo.source,
        logo_render_ok=render_ok,
        mood=None,
    )
