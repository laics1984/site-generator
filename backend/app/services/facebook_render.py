"""Read a Facebook Page by rendering it, when there's no access token.

Navigation only. Every byte of HTML is turned into facts by the pure readers in
`facebook_dom`, so the tests drive parsing with inline strings and drive this
module with a fake browser. Best-effort by construction: Facebook changes its
markup without notice, so the result is always flagged `partial`.

Optionally signed in. `fetch_page` takes a `storage_state` captured by
`scripts/facebook_login.py` (see `services/browser_session.py`). That is a
rescue for a Page that refuses a logged-out visitor, not a richer read —
measured, a signed-in render reads LESS of a Page that loads logged out (see
`facebook_source.default_fetchers`).

A read is two stages:

1. **The ladder** — the About tab, then the Page itself — until one yields an
   identity. That page is the read; a wall or a page with no identity is an
   error, never a thin result.
2. **The views** — the timeline, the Photos tab, the recommendations — in the
   same browser context under one budget (`facebook_views_timeout_seconds`),
   each folded in by `facebook_dom.merge_view`, which only fills gaps. A view
   that fails costs only itself. The stage stops at the first wall: repeated
   anonymous views are what make Facebook start refusing, and nothing a later
   view could add is worth risking that.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass

from app.config import settings
from app.models.facebook import FacebookPage, FacebookPhoto
from app.services.browser import browser_context, rendered_page
from app.services.facebook_dom import (
    FacebookRenderError,
    RenderedView,
    ViewRead,
    finish_views,
    fold_view,
    parse_public_html,
    read_photos,
    read_reviews,
    read_timeline,
)
from app.services.facebook_urls import FacebookRef, tab_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _View:
    tab: str  # "" is the timeline itself
    read: Callable[[RenderedView], ViewRead]
    scroll: bool  # the tab lazy-loads more as a visitor scrolls


# Ordered by what each adds to a site: the timeline and the Photos tab carry
# the photographs and the captions; the recommendations are one section.
_VIEWS = (
    _View("", read_timeline, scroll=True),
    _View("photos", read_photos, scroll=True),
    _View("reviews", read_reviews, scroll=False),
)


def _candidate_urls(ref: FacebookRef) -> list[str]:
    """Richest first: the About tab, then the Page itself.

    **No mbasic.** The mobile-basic hosts served plain server-rendered HTML and
    were the right first rung for years, but Facebook retired them — they now
    302 straight to `/login`, so every read spent a full render discovering a
    wall before it got to the URLs that work.
    """
    return [url for url in (tab_url(ref, "about"), tab_url(ref)) if url]


async def _scroll(rendered) -> None:
    """One viewport down, then wait — at most 1.2s — for new images only."""
    count = await rendered.locator("img").count()
    await rendered.evaluate("window.scrollBy(0, window.innerHeight)")
    try:
        await rendered.wait_for_function(
            "count => document.images.length > count", arg=count, timeout=1200
        )
    except Exception:
        pass


async def _collect_views(
    context, ref: FacebookRef, page: FacebookPage, *, already_read: str
) -> None:
    """Fold every view into `page`. A failure or the budget keeps what's read.

    `already_read` is the ladder's URL: when the ladder had to fall back to the
    Page itself, that render already WAS the timeline, and opening it again
    would cost a page load to learn nothing.
    """
    originals: dict[str, FacebookPhoto] = {}
    timeout_ms = int(settings.facebook_timeout_seconds * 1000)
    try:
        async with asyncio.timeout(settings.facebook_views_timeout_seconds):
            for view in _VIEWS:
                url = tab_url(ref, view.tab)
                if not url or url == already_read:
                    continue
                try:
                    async with rendered_page(context, url, timeout_ms=timeout_ms) as rendered:
                        steps = settings.facebook_view_scroll_steps if view.scroll else 0
                        for step in range(steps + 1):
                            if step:
                                await _scroll(rendered)
                            html = await rendered.content()
                            if not fold_view(page, originals, view.read, html, rendered.url):
                                logger.info("Facebook views: %s is walled; stopping", url)
                                return
                            if len(page.post_photo_urls) >= settings.facebook_photo_max_images:
                                break
                except Exception as exc:  # RenderError, Playwright timeouts, transport
                    logger.info("Facebook view unavailable: %s (%s)", url, exc)
    except TimeoutError:
        logger.info("Facebook views reached their time budget")
    finally:
        finish_views(page, originals)
        logger.info(
            "Facebook views: %d photos, %d captioned posts, %d recommendations for '%s'",
            len(page.post_photo_urls), len(page.posts_with_text), len(page.reviews), page.name,
        )


async def fetch_page(
    ref: FacebookRef, *, storage_state: dict | None = None
) -> FacebookPage:
    """Render the Page and parse it. Raises `FacebookRenderError`."""
    if not settings.facebook_render_fallback_enabled:
        raise FacebookRenderError(
            "Reading public Facebook Pages is turned off. Connect a Page access "
            "token to continue.",
            status=422,
        )

    timeout_ms = int(settings.facebook_timeout_seconds * 1000)
    last_error: FacebookRenderError | None = None
    signed_in = storage_state is not None

    # One browser for the whole read — each ladder URL is a fallback for the
    # last, and every view after it reuses the same context.
    async with browser_context(storage_state=storage_state) as context:
        for url in _candidate_urls(ref):
            try:
                async with rendered_page(context, url, timeout_ms=timeout_ms) as page:
                    html = await page.content()
                    # Where we LANDED, not where we asked to go: a redirect to
                    # /login is the wall, and the requested URL can't show it.
                    final_url = page.url
            except Exception as exc:  # RenderError, Playwright timeouts, transport
                logger.info("Facebook render: %s failed (%s)", url, exc)
                last_error = FacebookRenderError(
                    f"Couldn't open that Facebook Page: {exc}"
                )
                continue
            try:
                fb_page = parse_public_html(
                    html, ref, final_url=final_url, signed_in=signed_in
                )
            except FacebookRenderError as exc:
                logger.info("Facebook render: %s unusable (%s)", url, exc)
                last_error = exc
                continue
            logger.info(
                "Facebook render: read '%s' from %s (%s)",
                fb_page.name,
                url,
                "signed in" if signed_in else "anonymous",
            )
            await _collect_views(context, ref, fb_page, already_read=url)
            return fb_page

    raise last_error or FacebookRenderError(
        "Couldn't read that Facebook Page.", status=422
    )
