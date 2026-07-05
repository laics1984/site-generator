"""Tests for the crawl sliding-window worker pool (_crawl_extra_pages).

The old crawl ran lockstep batches of 3 — one slow page stalled two finished
slots every round. The pool keeps workers busy while preserving the strict
max_pages cap, cancellation, and the deterministic (depth, discovery) result
order downstream ranking relies on.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from app.services import scraper
from app.services.fast_fetch import FastFetchResult


class _FakeSlot:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakePoliteness:
    circuit_open = False

    def slot(self):
        return _FakeSlot()

    def record_success(self):
        pass

    def record_failure(self, retriable=False):
        pass


def _fake_parse(html, final_url, require_text=False):
    return SimpleNamespace(final_url=final_url, source_content=SimpleNamespace(links=[]))


def _patch_crawl(test, fetch):
    for patcher in (
        mock.patch.object(scraper, "try_fast_fetch", fetch),
        mock.patch.object(scraper, "_parse_rendered_html", _fake_parse),
        mock.patch.object(scraper, "get_politeness", mock.AsyncMock(return_value=_FakePoliteness())),
    ):
        patcher.start()
        test.addCleanup(patcher.stop)


def _seeds(n):
    return [f"https://site.test/page-{i}" for i in range(n)]


class CrawlWorkerPoolTest(unittest.IsolatedAsyncioTestCase):
    async def test_fast_pages_do_not_wait_on_a_slow_one(self):
        async def fetch(url):
            await asyncio.sleep(0.25 if "page-0" in url else 0.01)
            return FastFetchResult(html="<html></html>", final_url=url, http_status=200)

        _patch_crawl(self, fetch)
        started = time.monotonic()
        pages, unvisited = await scraper._crawl_extra_pages(
            None,
            "https://site.test/",
            _seeds(6),
            max_pages=10,
            max_depth=1,
            timeout_ms=1000,
            respect_robots=False,
        )
        elapsed = time.monotonic() - started

        self.assertEqual(len(pages), 6)
        self.assertEqual(unvisited, [])
        # Lockstep batches of 3 would serialize: 2 rounds of >=0.25s ≈ 0.5s+.
        # The pool overlaps everything behind the single slow fetch.
        self.assertLess(elapsed, 0.45)
        # Deterministic (depth, discovery) order regardless of completion order.
        self.assertEqual(
            [p.final_url for p in pages],
            [f"https://site.test/page-{i}" for i in range(6)],
        )

    async def test_max_pages_cap_is_strict_and_leftover_surfaces(self):
        async def fetch(url):
            await asyncio.sleep(0.005)
            return FastFetchResult(html="<html></html>", final_url=url, http_status=200)

        _patch_crawl(self, fetch)
        pages, unvisited = await scraper._crawl_extra_pages(
            None,
            "https://site.test/",
            _seeds(10),
            max_pages=4,
            max_depth=1,
            timeout_ms=1000,
            respect_robots=False,
        )

        self.assertEqual(len(pages), 4)
        # Whatever wasn't dequeued when the cap hit is resumable.
        self.assertGreaterEqual(len(unvisited), 1)

    async def test_cancellation_stops_the_pool(self):
        fetched: list[str] = []

        async def fetch(url):
            fetched.append(url)
            await asyncio.sleep(0.01)
            return FastFetchResult(html="<html></html>", final_url=url, http_status=200)

        _patch_crawl(self, fetch)
        cancelled = False

        def is_cancelled():
            return cancelled

        async def cancel_soon():
            nonlocal cancelled
            await asyncio.sleep(0.02)
            cancelled = True

        task = asyncio.create_task(cancel_soon())
        pages, _ = await scraper._crawl_extra_pages(
            None,
            "https://site.test/",
            _seeds(30),
            max_pages=30,
            max_depth=1,
            timeout_ms=1000,
            respect_robots=False,
            is_cancelled=is_cancelled,
        )
        await task

        self.assertLess(len(fetched), 30)
        self.assertLess(len(pages), 30)
