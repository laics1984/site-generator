"""Crawl-result re-use, retention sweep, and orphan reaping.

A crawl is the most expensive thing this service does. The synchronous
`/api/scrape/preview` endpoint had a 30-minute in-process cache for exactly
that reason — but the job model that replaced it never got one, so a
double-click, a browser Back, or a regeneration re-rendered every page through
Playwright. Worse, the frontend deleted each job row the moment polling
finished, so no result could outlive the request that produced it.

Also covers the two housekeeping paths that make re-use safe: expired rows are
dropped, and rows stranded `running` by a dead process are failed rather than
polled forever.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.services.crawl_jobs import CrawlJobManager


_OPTIONS = {
    "respect_robots": True,
    "crawl": True,
    "crawl_max_pages": 20,
    "crawl_max_depth": 3,
}


class _TempDb:
    """Point services.db at a throwaway file for one test."""

    def __enter__(self):
        from app.services import db as db_module

        self._db = db_module
        self._orig_path = db_module.DB_PATH
        self._orig_boot = db_module._BOOTSTRAPPED
        self._tmp = tempfile.TemporaryDirectory()
        db_module.DB_PATH = Path(self._tmp.name) / "jobs-test.db"
        db_module._BOOTSTRAPPED = False
        return self

    def __exit__(self, *exc):
        self._db.DB_PATH = self._orig_path
        self._db._BOOTSTRAPPED = self._orig_boot
        self._tmp.cleanup()
        return False


class CrawlJobReuseTest(unittest.IsolatedAsyncioTestCase):
    async def test_identical_finished_crawl_is_reused(self):
        with _TempDb():
            mgr = CrawlJobManager()
            job = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_done(job.id, {"final_url": "https://acme.example"})

            found = await mgr.find_reusable(
                "https://acme.example", _OPTIONS, max_age_seconds=1800
            )
            self.assertIsNotNone(found)
            self.assertEqual(found.id, job.id)

    async def test_different_options_are_not_reused(self):
        with _TempDb():
            mgr = CrawlJobManager()
            job = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_done(job.id, {"final_url": "https://acme.example"})

            deeper = {**_OPTIONS, "crawl_max_pages": 40}
            self.assertIsNone(
                await mgr.find_reusable(
                    "https://acme.example", deeper, max_age_seconds=1800
                )
            )

    async def test_option_key_order_does_not_matter(self):
        # Options are compared as parsed dicts, not as their stored JSON, so a
        # differently-ordered payload still hits.
        with _TempDb():
            mgr = CrawlJobManager()
            job = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_done(job.id, {"ok": True})

            reordered = dict(reversed(list(_OPTIONS.items())))
            found = await mgr.find_reusable(
                "https://acme.example", reordered, max_age_seconds=1800
            )
            self.assertIsNotNone(found)
            self.assertEqual(found.id, job.id)

    async def test_unfinished_or_failed_jobs_are_never_reused(self):
        with _TempDb():
            mgr = CrawlJobManager()
            running = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_running(running.id)
            self.assertIsNone(
                await mgr.find_reusable(
                    "https://acme.example", _OPTIONS, max_age_seconds=1800
                )
            )

            failed = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_failed(failed.id, "boom")
            self.assertIsNone(
                await mgr.find_reusable(
                    "https://acme.example", _OPTIONS, max_age_seconds=1800
                )
            )

    async def test_a_done_job_with_no_result_is_not_reused(self):
        with _TempDb():
            mgr = CrawlJobManager()
            job = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_done(job.id, {})
            self.assertIsNone(
                await mgr.find_reusable(
                    "https://acme.example", _OPTIONS, max_age_seconds=1800
                )
            )

    async def test_expired_results_are_not_reused(self):
        with _TempDb():
            mgr = CrawlJobManager()
            job = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_done(job.id, {"ok": True})
            # A zero-length window makes every row older than the cutoff.
            self.assertIsNone(
                await mgr.find_reusable(
                    "https://acme.example", _OPTIONS, max_age_seconds=0
                )
            )

    async def test_purge_drops_expired_terminal_rows_only(self):
        with _TempDb():
            mgr = CrawlJobManager()
            done = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_done(done.id, {"ok": True})
            running = await mgr.create("https://other.example", _OPTIONS)
            await mgr.mark_running(running.id)

            purged = await mgr.purge_expired(max_age_seconds=0)

            self.assertEqual(purged, 1)
            self.assertIsNone(await mgr.get(done.id))
            # A live crawl is never swept out from under itself.
            self.assertIsNotNone(await mgr.get(running.id))

    async def test_reap_fails_jobs_with_no_live_task(self):
        with _TempDb():
            mgr = CrawlJobManager()
            stranded = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_running(stranded.id)

            reaped = await mgr.reap_orphans()

            self.assertEqual(reaped, 1)
            job = await mgr.get(stranded.id)
            self.assertEqual(job.status, "failed")
            self.assertIn("restarted", job.error.lower())

    async def test_reap_leaves_jobs_this_process_is_running(self):
        import asyncio

        with _TempDb():
            mgr = CrawlJobManager()
            live = await mgr.create("https://acme.example", _OPTIONS)
            await mgr.mark_running(live.id)

            async def _forever():
                await asyncio.sleep(60)

            task = asyncio.create_task(_forever())
            mgr.register_task(live.id, task)
            try:
                self.assertEqual(await mgr.reap_orphans(), 0)
                self.assertEqual((await mgr.get(live.id)).status, "running")
            finally:
                task.cancel()


if __name__ == "__main__":
    unittest.main()
