"""
Per-host politeness: concurrency cap + min-delay between requests + circuit
breaker on repeated 4xx/5xx.

This sits between the crawl loop and the actual fetch calls. We use it as an
async context manager — the crawl loop acquires a "slot" before each fetch,
the slot enforces:

  1. At most N concurrent in-flight requests to the same host (default 4)
  2. A minimum gap between consecutive requests to the same host (default
     200ms, or robots.txt `Crawl-delay` if larger)
  3. Tracks consecutive failures — after 5 in a row, opens a circuit and the
     crawl loop should skip the remaining frontier for that host

This keeps us out of WAF rate-limit jail on sites that don't explicitly
publish a Crawl-delay. Without it, a 40-page parallel crawl will get
hit with 429s on most production sites within seconds.

Built for the single-host case (every crawl walks one origin). The host
registry is process-wide so future multi-host work can layer on cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


DEFAULT_CONCURRENCY = 4
DEFAULT_MIN_DELAY_MS = 200
DEFAULT_BACKOFF_BASE_SEC = 1.0
DEFAULT_BACKOFF_MAX_SEC = 30.0
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5
# How long a tripped circuit stays open before it half-opens and lets the next
# crawl try again. Long enough to ride out a WAF cool-off or a brief outage,
# short enough that a user retrying by hand isn't told "no" for the rest of the
# process's life.
DEFAULT_CIRCUIT_COOLDOWN_SEC = 120.0


@dataclass
class HostPoliteness:
    """Per-host concurrency + delay enforcement."""

    host: str
    concurrency: int = DEFAULT_CONCURRENCY
    min_delay_sec: float = DEFAULT_MIN_DELAY_MS / 1000
    circuit_cooldown_sec: float = DEFAULT_CIRCUIT_COOLDOWN_SEC

    _semaphore: asyncio.Semaphore = field(init=False)
    _last_request_at: float = field(init=False, default=0.0)
    _consecutive_failures: int = field(init=False, default=0)
    _circuit_opened_at: float | None = field(init=False, default=None)
    _lock: asyncio.Lock = field(init=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.concurrency)
        self._lock = asyncio.Lock()

    @property
    def circuit_open(self) -> bool:
        """When True, the crawl loop should stop sending requests to this host.

        The circuit is HALF-OPEN rather than latching: once
        ``circuit_cooldown_sec`` has passed since it tripped, it closes itself
        and the failure counter resets, so the next crawl gets a fresh set of
        attempts.

        This registry is process-wide and keyed by host, so a latching circuit
        meant one bad crawl (a site down for a minute, a transient WAF block)
        blocked that host for the lifetime of the backend — every later crawl
        stopping instantly at zero pages with no way back short of a restart.
        """
        if self._circuit_opened_at is None:
            return False
        if time.monotonic() - self._circuit_opened_at >= self.circuit_cooldown_sec:
            logger.info(
                "Politeness circuit for host=%s cooled down after %.0fs — closing",
                self.host,
                self.circuit_cooldown_sec,
            )
            self._circuit_opened_at = None
            self._consecutive_failures = 0
            return False
        return True

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        """
        Acquire a politeness slot. Blocks until under concurrency cap AND the
        min-delay since the last request to this host has elapsed.

        Always releases the slot on exit, even if the wrapped fetch raises.
        """
        async with self._semaphore:
            # Wait for the min-delay since the last request to settle. Lock
            # protects the last_request_at write so two parallel slots don't
            # both think they were "the most recent" request.
            async with self._lock:
                now = time.monotonic()
                gap = self.min_delay_sec - (now - self._last_request_at)
                if gap > 0:
                    await asyncio.sleep(gap)
                self._last_request_at = time.monotonic()
            yield

    def record_success(self) -> None:
        self._consecutive_failures = 0

    def record_failure(self, *, retriable: bool = True) -> None:
        """Increment failure counter. Opens the circuit after the configured
        threshold of consecutive failures. ``retriable`` is informational."""
        self._consecutive_failures += 1
        if (
            self._consecutive_failures >= DEFAULT_MAX_CONSECUTIVE_FAILURES
            and self._circuit_opened_at is None
        ):
            self._circuit_opened_at = time.monotonic()
            logger.warning(
                "Politeness circuit opened for host=%s after %d consecutive "
                "failures — reopening in %.0fs",
                self.host,
                self._consecutive_failures,
                self.circuit_cooldown_sec,
            )

    async def back_off(self, attempt: int) -> None:
        """Sleep with exponential backoff. attempt=1 → 1s, 2→2s, 3→4s, capped."""
        delay = min(
            DEFAULT_BACKOFF_BASE_SEC * (2 ** max(0, attempt - 1)),
            DEFAULT_BACKOFF_MAX_SEC,
        )
        await asyncio.sleep(delay)


# Process-wide registry. Keyed by host so multi-host scenarios share the
# same per-host state across crawl jobs.
_registry: dict[str, HostPoliteness] = {}
_registry_lock = asyncio.Lock()


async def get_politeness(
    host: str,
    *,
    concurrency: int | None = None,
    min_delay_ms: int | None = None,
) -> HostPoliteness:
    """Get-or-create a per-host politeness instance. Thread-safe via asyncio lock."""
    async with _registry_lock:
        existing = _registry.get(host)
        if existing is not None:
            return existing
        instance = HostPoliteness(
            host=host,
            concurrency=concurrency if concurrency is not None else DEFAULT_CONCURRENCY,
            min_delay_sec=(
                (min_delay_ms if min_delay_ms is not None else DEFAULT_MIN_DELAY_MS) / 1000
            ),
        )
        _registry[host] = instance
        return instance


def existing_politeness(host: str) -> HostPoliteness | None:
    """The host's politeness if a crawl registered it, else None — never creates
    one. Lets a file download share the crawl's budget for the site it is
    crawling without throttling every CDN it happens to touch."""
    return _registry.get(host)


def reset_politeness(host: str | None = None) -> None:
    """Clear a host's state (or all hosts). Mainly used by tests and on
    explicit user request via UI ('Reset crawl politeness')."""
    if host is None:
        _registry.clear()
    else:
        _registry.pop(host, None)


# Status codes that signal we should back off and retry rather than fail.
RETRIABLE_STATUS_CODES: frozenset[int] = frozenset({429, 503, 502, 504})


# (response header, value) pairs a bot-protection layer sets when it answers
# with a challenge page instead of the site. Cloudflare documents
# `cf-mitigated: challenge` for exactly this. Matched on the header, never on
# the page text: a challenge interstitial is a "Just a moment..." page that
# would otherwise pass as thin content. Add a vendor's documented signal here.
_CHALLENGE_SIGNATURES: tuple[tuple[str, str], ...] = (
    ("cf-mitigated", "challenge"),
)


# Bot-protection layers that answer 200 with a challenge page and NO header to
# say so. Matched on STRUCTURAL tokens the vendor's challenge script uses —
# form field names, never the visible copy ("One moment, please...", which any
# page could say) — and only on a page with almost no visible text, so a real
# page that happens to mention a token is never mistaken for one. Every token in
# an entry must appear inside a <script>. Add a vendor as one tuple.
_CHALLENGE_PAGE_MARKERS: tuple[tuple[str, ...], ...] = (
    # Imunify360 WebShield (cPanel hosts): its JS check posts `wsidchk` and
    # reports `failedChecks` (webdriver / headless / spoofing probes).
    # mykiddyland.com, Oct 2026.
    ("wsidchk", "failedChecks"),
)
# A challenge interstitial says one sentence; a real page says more than this.
_CHALLENGE_PAGE_MAX_TEXT = 200

_SCRIPT_RE = re.compile(r"<script\b[^>]*>(.*?)</script\s*>", re.IGNORECASE | re.DOTALL)
_NON_TEXT_RE = re.compile(
    r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


def is_challenge_page(html: str | None) -> bool:
    """True when `html` is a bot-protection challenge served without a header.

    The body twin of `is_bot_challenge`, for vendors that document no header.
    Same contract: detection only — the remedy is the site owner allowlisting
    the crawler, never getting past the check.
    """
    if not html:
        return False
    scripts = "\n".join(_SCRIPT_RE.findall(html))
    if not scripts or not any(
        all(token in scripts for token in marker) for marker in _CHALLENGE_PAGE_MARKERS
    ):
        return False
    text = " ".join(_TAG_RE.sub(" ", _NON_TEXT_RE.sub(" ", html)).split())
    return len(text) < _CHALLENGE_PAGE_MAX_TEXT


def challenge_message(url: str) -> str:
    """What the operator is told when a host answers with a challenge page."""
    host = urlparse(url).hostname or url
    return (
        f"{host} answered with a bot-verification page instead of the site (its "
        "firewall blocks automated browsers). Ask the site owner to allowlist the "
        "crawler, try again later, or paste the content into the paste box."
    )


def is_bot_challenge(headers: Mapping[str, str]) -> bool:
    """True when a response is a bot-protection challenge, not the page.

    A challenge is about the HOST's view of this client, not about the page
    that was asked for: the next page gets the same answer, and a headless
    browser is challenged too (measured on feruni.com — the render never
    cleared it). Callers stop spending requests on it instead of retrying
    through a slower path. Deliberately detection only: getting past a
    challenge is the site owner's call (allowlist the crawler), not ours.
    """
    return any(
        (headers.get(name) or "").strip().lower() == value
        for name, value in _CHALLENGE_SIGNATURES
    )
