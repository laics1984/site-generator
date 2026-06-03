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
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import AsyncIterator

logger = logging.getLogger(__name__)


DEFAULT_CONCURRENCY = 4
DEFAULT_MIN_DELAY_MS = 200
DEFAULT_BACKOFF_BASE_SEC = 1.0
DEFAULT_BACKOFF_MAX_SEC = 30.0
DEFAULT_MAX_CONSECUTIVE_FAILURES = 5


@dataclass
class HostPoliteness:
    """Per-host concurrency + delay enforcement."""

    host: str
    concurrency: int = DEFAULT_CONCURRENCY
    min_delay_sec: float = DEFAULT_MIN_DELAY_MS / 1000

    _semaphore: asyncio.Semaphore = field(init=False)
    _last_request_at: float = field(init=False, default=0.0)
    _consecutive_failures: int = field(init=False, default=0)
    _circuit_open: bool = field(init=False, default=False)
    _lock: asyncio.Lock = field(init=False)

    def __post_init__(self) -> None:
        self._semaphore = asyncio.Semaphore(self.concurrency)
        self._lock = asyncio.Lock()

    @property
    def circuit_open(self) -> bool:
        """When True, the crawl loop should stop sending requests to this host."""
        return self._circuit_open

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
        if self._consecutive_failures >= DEFAULT_MAX_CONSECUTIVE_FAILURES:
            self._circuit_open = True
            logger.warning(
                "Politeness circuit opened for host=%s after %d consecutive failures",
                self.host,
                self._consecutive_failures,
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


def reset_politeness(host: str | None = None) -> None:
    """Clear a host's state (or all hosts). Mainly used by tests and on
    explicit user request via UI ('Reset crawl politeness')."""
    if host is None:
        _registry.clear()
    else:
        _registry.pop(host, None)


# Status codes that signal we should back off and retry rather than fail.
RETRIABLE_STATUS_CODES: frozenset[int] = frozenset({429, 503, 502, 504})
