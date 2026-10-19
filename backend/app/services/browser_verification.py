"""One ordinary-browser attempt for supported HTTP verification responses.

Retrieval only: no extraction, fingerprint changes, CAPTCHA solving, saved
sessions or retries. fast_fetch is the only production entry point. Existing
JS-shell and navigation rendering remain independent of this opt-in path.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from weakref import WeakKeyDictionary

from bs4 import BeautifulSoup
from playwright.async_api import Error as PlaywrightError, TimeoutError as PlaywrightTimeout

from app.config import settings
from app.services.browser import browser_context
from app.services.polite import is_bot_challenge, is_challenge_page
from app.services.url_guard import UnsafeUrlError, assert_public_url

logger = logging.getLogger(__name__)
_limits: WeakKeyDictionary = WeakKeyDictionary()
_MAX_NAVIGATIONS = 10
_POLL_SECONDS = 0.2


@dataclass(frozen=True)
class VerificationResult:
    final_url: str = ""
    html: str | None = None
    http_status: int = 0
    failure: str | None = None


@dataclass
class _Navigation:
    failure: str | None = None
    status: int = 0
    headers: dict | None = None
    count: int = 0
    opened: bool = False
    waiting: str = "verification_timeout"
    response_count: int = 0


def _limiter() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    if loop not in _limits:
        _limits[loop] = asyncio.Semaphore(settings.playwright_fallback_max_concurrency)
    return _limits[loop]


def _content_state(html: str) -> tuple[str | None, str]:
    """Reject interstitials; leave all content extraction to the existing parser."""
    challenge = is_challenge_page(html)
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True).lower() if soup.title else ""
    for tag in soup.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = soup.body.get_text(" ", strip=True) if soup.body else ""
    # An embedded contact-form CAPTCHA on a normal page is not an interstitial.
    thin = len(text) < 500
    captcha_widget = soup.select_one(
        'form[action*="captcha"], #captcha, .g-recaptcha, .h-captcha, iframe[src*="captcha"]'
    )
    if thin and ("captcha" in title or (captcha_widget and any(
        phrase in text.lower() for phrase in ("verify you are human", "confirm you are human", "complete the captcha")
    ))):
        return "captcha", text
    if thin and title in {"just a moment...", "one moment, please...", "browser verification", "security verification", "checking your browser"}:
        return "verification_failed", text
    if title in {"access denied", "403 forbidden", "forbidden", "request blocked"} or (
        thin and text.lower().strip(". !") in {"access denied", "forbidden", "request blocked"}
    ):
        return "access_denied", text
    if thin and soup.select_one('input[type="password"]'):
        return "authentication_required", text
    if challenge:
        return "verification_failed", text
    if not text:
        return "empty_page", ""
    return None, text


async def _retrieve(url: str, state: _Navigation) -> VerificationResult:
    await assert_public_url(url)
    async with browser_context(ordinary=True) as context:
        state.opened = True
        page = await context.new_page()

        session = await context.new_cdp_session(page)
        tree = await session.send("Page.getFrameTree")
        main_frame = tree["frameTree"]["frame"]["id"]

        async def stop_request(request_id):
            try:
                await session.send("Fetch.failRequest", {"requestId": request_id, "errorReason": "BlockedByClient"})
            except PlaywrightError:
                pass  # context teardown may already have cancelled the request

        async def guarded_request(event):
            request_id = event["requestId"]
            try:
                if "responseStatusCode" in event:
                    if event["resourceType"] not in {"Document", "Script"} or 300 <= event["responseStatusCode"] < 400:
                        await session.send("Fetch.continueResponse", {"requestId": request_id})
                        return
                    # Chromium 128's Network.setBlockedURLs does not cover
                    # WebSocket handshakes. Add a response policy that permits
                    # ordinary HTTP(S) verification fetches but no ws/wss
                    # connections outside the request guard. Keep any existing
                    # policy as an additional, independently enforced policy.
                    body = await session.send("Fetch.getResponseBody", {"requestId": request_id})
                    # Replay decoded text with a matching charset so adding the
                    # policy cannot corrupt compressed or non-ASCII content.
                    discarded = {"content-encoding", "content-length", "transfer-encoding"}
                    headers = [
                        header for header in event.get("responseHeaders", [])
                        if header["name"].lower() not in discarded
                    ]
                    if not body["base64Encoded"]:
                        body["body"] = base64.b64encode(body["body"].encode("utf-8")).decode("ascii")
                        content_type = next(
                            (header["value"].split(";", 1)[0] for header in headers
                             if header["name"].lower() == "content-type"),
                            "text/html" if event["resourceType"] == "Document" else "application/javascript",
                        )
                        headers = [header for header in headers if header["name"].lower() != "content-type"] + [{
                            "name": "Content-Type", "value": content_type + "; charset=utf-8",
                        }]
                    headers = [*headers, {
                        "name": "Content-Security-Policy", "value": "connect-src http: https:",
                    }]
                    await session.send("Fetch.fulfillRequest", {
                        "requestId": request_id, "responseCode": event["responseStatusCode"],
                        "responseHeaders": headers, "body": body["body"],
                    })
                    return
                await assert_public_url(event["request"]["url"])
                main = event["resourceType"] == "Document" and event["frameId"] == main_frame
                if main:
                    state.count += 1
                    if state.count > _MAX_NAVIGATIONS:
                        state.failure = "navigation_limit"
                if state.failure:
                    await stop_request(request_id)
                    return
                if event["resourceType"] in {"Media", "Font", "WebSocket"}:
                    await stop_request(request_id)
                    return
                if event["resourceType"] == "Document" and not main:
                    # Source HTML retains iframe attributes, but do not create
                    # out-of-process frame targets outside this page's guard.
                    # Verification depending on an iframe remains unsupported.
                    await stop_request(request_id)
                    return
                # Chromium pauses each redirect hop before dispatch. Playwright
                # route.continue_ only intercepts the first request in a chain.
                await session.send("Fetch.continueRequest", {"requestId": request_id, "interceptResponse": True})
            except UnsafeUrlError:
                state.failure = "blocked_url"
                await stop_request(request_id)
            except Exception:
                if not page.is_closed():
                    state.failure = state.failure or "network_failure"
                    await stop_request(request_id)

        async def record_response(response):
            try:
                request = response.request
                if request.is_navigation_request() and request.frame == page.main_frame:
                    count = state.count
                    headers = await response.all_headers()
                    if count == state.count:
                        state.status = response.status
                        state.headers = headers
                        state.response_count = count
            except PlaywrightError:
                pass

        session.on("Fetch.requestPaused", guarded_request)
        await session.send("Network.enable")
        await session.send("Fetch.enable", {"patterns": [{"urlPattern": "*", "requestStage": "Request"}]})
        page.on("response", record_response)
        response = await page.goto(url, wait_until="domcontentloaded", timeout=settings.playwright_goto_timeout_ms)
        if response is None:
            return VerificationResult(failure="html_unavailable")
        previous = None
        while True:
            if state.failure:
                return VerificationResult(failure=state.failure)
            if page.is_closed():
                return VerificationResult(failure="browser_context_closed")
            if state.response_count != state.count:
                await asyncio.sleep(_POLL_SECONDS)
                continue
            if state.status in {401, 429}:
                return VerificationResult(failure="authentication_required" if state.status == 401 else "rate_limited")
            try:
                html = await page.content()
            except PlaywrightError:
                if page.is_closed():
                    return VerificationResult(failure="browser_context_closed")
                await asyncio.sleep(_POLL_SECONDS)
                continue  # a normal verification reload may destroy the DOM
            if not isinstance(html, str):
                return VerificationResult(failure="html_unavailable")
            failure, text = await asyncio.to_thread(_content_state, html)
            state.waiting = failure or "content_not_ready"
            if is_bot_challenge(state.headers or {}):
                return VerificationResult(failure="unsupported_verification")
            if failure in {"captcha", "access_denied", "authentication_required"}:
                return VerificationResult(failure=failure)
            if state.status >= 400 and failure != "verification_failed":
                return VerificationResult(failure="access_denied" if state.status == 403 else "http_error")
            if failure is None and 200 <= state.status < 300:
                await assert_public_url(page.url)
                snapshot = (page.url, text, state.count)
                if snapshot == previous:
                    return VerificationResult(final_url=page.url, html=html, http_status=state.status)
                previous = snapshot
            else:
                previous = None
            await asyncio.sleep(_POLL_SECONDS)


async def fetch_verified_html(url: str) -> VerificationResult:
    """Return a structured outcome; cancellation propagates after resource cleanup."""
    if not settings.playwright_fallback_enabled:
        return VerificationResult(failure="disabled")
    started = time.monotonic()
    state = _Navigation()
    # Never log raw URLs, query strings, exception messages, or page contents.
    try:
        host = urlsplit(url).hostname or "invalid"
    except ValueError:
        host = "invalid"
    logger.info("browser fallback host=%s trigger=verification type=imunify360 status=started", host)
    try:
        async with asyncio.timeout(settings.playwright_fallback_timeout_seconds):
            async with _limiter():
                result = await _retrieve(url, state)
    except UnsafeUrlError:
        result = VerificationResult(failure="blocked_url")
    except asyncio.CancelledError:
        logger.info("browser fallback host=%s type=imunify360 status=cancelled duration_ms=%d", host, (time.monotonic() - started) * 1000)
        raise
    except PlaywrightTimeout:
        result = VerificationResult(failure=state.failure or "navigation_timeout")
    except TimeoutError:
        result = VerificationResult(failure=state.failure or (state.waiting if state.opened else "overall_timeout"))
    except PlaywrightError as exc:
        if state.failure:
            reason = state.failure
        elif "Executable doesn't exist" in str(exc):
            reason = "chromium_unavailable"
        elif "closed" in str(exc).lower():
            reason = "browser_context_closed"
        else:
            reason = "network_failure" if state.opened else "browser_launch_failed"
        result = VerificationResult(failure=reason)
    except Exception:
        result = VerificationResult(failure=state.failure or "retrieval_failed")
    logger.info(
        "browser fallback host=%s type=imunify360 status=%s reason=%s duration_ms=%d",
        host, "success" if result.html is not None else "failed",
        result.failure or "verified", (time.monotonic() - started) * 1000,
    )
    return result
