"""Tests for locale-mirror ordering in the crawl frontier.

A multilingual site duplicates every page under a language directory
(/bm/committee, /zh/about). Those copies are real pages the owner maintains,
but they carry no structure the source language doesn't already have — and
they're same-host, page-shaped links, so the BFS used to queue them level with
everything else. On mmta.org.my the /bm/* and /zh/* mirrors ate 14 of the
default 20 slots and pushed the nine committee-member profile pages out of the
crawl entirely.

Mirrors now go to a deferred queue: crawled after every untranslated page,
never dropped. Locale-*looking* paths that aren't mirrors (/it/support on a
site with no /support) keep full priority.
"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from app.services import scraper
from app.services.fast_fetch import FastFetchResult
from app.services.locale import locale_segment


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


def _patch_crawl(test, links_by_url: dict[str, list[str]] | None = None):
    """Serve every URL instantly; each page's outbound links come from the map."""
    links_by_url = links_by_url or {}

    async def fetch(url):
        await asyncio.sleep(0)
        return FastFetchResult(html="<html></html>", final_url=url, http_status=200)

    def parse(html, final_url, require_text=False):
        return SimpleNamespace(
            final_url=final_url,
            source_content=SimpleNamespace(links=links_by_url.get(final_url, [])),
        )

    for patcher in (
        mock.patch.object(scraper, "try_fast_fetch", fetch),
        mock.patch.object(scraper, "_parse_rendered_html", parse),
        mock.patch.object(
            scraper, "get_politeness", mock.AsyncMock(return_value=_FakePoliteness())
        ),
    ):
        patcher.start()
        test.addCleanup(patcher.stop)


async def _crawl(entry, seeds, *, max_pages=20, max_depth=3):
    pages, unvisited = await scraper._crawl_extra_pages(
        None,
        entry,
        seeds,
        max_pages=max_pages,
        max_depth=max_depth,
        timeout_ms=1000,
        respect_robots=False,
    )
    return [p.final_url for p in pages], unvisited


class LocaleSegmentTest(unittest.TestCase):
    def test_language_directories_are_recognized(self):
        self.assertEqual(locale_segment("/zh/about"), "zh")
        self.assertEqual(locale_segment("/BM/committee"), "bm")
        self.assertEqual(locale_segment("/fr-FR/produits"), "fr-fr")
        self.assertEqual(locale_segment("/zh-hans"), "zh-hans")

    def test_ordinary_paths_are_not_locales(self):
        for path in ("/", "/about", "/committee", "/services/web-design", "/team-nl"):
            self.assertIsNone(locale_segment(path), path)


class LocaleMirrorOrderingTest(unittest.IsolatedAsyncioTestCase):
    async def test_language_roots_are_crawled_after_the_source_language(self):
        _patch_crawl(self)
        seeds = [
            "https://site.test/bm",
            "https://site.test/zh",
            "https://site.test/about",
        ]
        crawled, unvisited = await _crawl("https://site.test/", seeds)
        # Listed first in the header, crawled last — but still crawled.
        self.assertEqual(crawled[0], "https://site.test/about")
        self.assertEqual(sorted(crawled[1:]), ["https://site.test/bm", "https://site.test/zh"])
        self.assertEqual(unvisited, [])

    async def test_translated_copy_of_a_known_page_is_deferred(self):
        _patch_crawl(self)
        seeds = ["https://site.test/zh/about", "https://site.test/about"]
        crawled, _ = await _crawl("https://site.test/", seeds)
        # /zh/about mirrors /about even though the entry page lists it first.
        self.assertEqual(
            crawled, ["https://site.test/about", "https://site.test/zh/about"]
        )

    async def test_a_deferred_mirror_surfaces_as_unvisited_not_lost(self):
        _patch_crawl(self)
        seeds = ["https://site.test/zh/about", "https://site.test/about"]
        crawled, unvisited = await _crawl("https://site.test/", seeds, max_pages=1)
        self.assertEqual(crawled, ["https://site.test/about"])
        # Budget ran out — the translation is resumable via /api/scrape/extend.
        self.assertEqual(unvisited, ["https://site.test/zh/about"])

    async def test_locale_looking_path_without_a_counterpart_keeps_priority(self):
        _patch_crawl(self)
        # No /support anywhere, so /it/support is an IT section, not Italian.
        seeds = ["https://site.test/it/support", "https://site.test/about"]
        crawled, _ = await _crawl("https://site.test/", seeds)
        self.assertEqual(
            crawled,
            ["https://site.test/it/support", "https://site.test/about"],
        )

    async def test_entry_language_subtree_is_crawled(self):
        _patch_crawl(self)
        seeds = [
            "https://site.test/bm/about",
            "https://site.test/bm/jawatankuasa",
            "https://site.test/about",
        ]
        crawled, _ = await _crawl("https://site.test/bm", seeds)
        # Scraping the Malay site makes Malay the source language.
        self.assertIn("https://site.test/bm/about", crawled)
        self.assertIn("https://site.test/bm/jawatankuasa", crawled)

    async def test_mirrors_no_longer_spend_the_page_budget(self):
        """The MMTA regression: depth-2 detail pages beat depth-1 mirrors."""
        entry = "https://site.test/"
        committee = "https://site.test/committee"
        members = [f"https://site.test/profile/m{i}" for i in range(4)]
        mirrors = [
            "https://site.test/bm/about",
            "https://site.test/bm/committee",
            "https://site.test/bm/contact",
        ]
        seeds = [
            "https://site.test/bm",
            "https://site.test/zh",
            "https://site.test/about",
            committee,
            "https://site.test/contact",
        ]
        # The language roots fan out into a full mirror of the site.
        _patch_crawl(self, {committee: members, "https://site.test/bm": mirrors})

        crawled, unvisited = await _crawl(entry, seeds, max_pages=7)

        # The budget goes to pages that exist in no other language first.
        for member in members:
            self.assertIn(member, crawled)
        self.assertFalse([u for u in crawled if u in mirrors])
        # And the translations are queued behind them, not discarded.
        self.assertTrue(unvisited)
