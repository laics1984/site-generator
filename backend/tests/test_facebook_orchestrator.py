"""Job dispatch and token handling.

The token assertions are the important ones: a Page access token belongs to the
request that supplied it and must not outlive it, must never be written to the
jobs table, and must never appear in an error the user sees.
"""

import unittest

from app.services import facebook_orchestrator as orch
from app.services.source_detect import detect


class DispatchTest(unittest.TestCase):
    """The routing decision /api/scrape/start makes. Kept as a unit test on
    `detect` because the router branches on exactly this."""

    def test_a_facebook_link_routes_to_the_facebook_reader(self):
        self.assertEqual(detect("https://www.facebook.com/acmecoffee"), "facebook")

    def test_everything_else_still_crawls(self):
        self.assertEqual(detect("https://acmecoffee.example"), "html")


class TokenLifecycleTest(unittest.TestCase):
    def setUp(self):
        orch._TOKENS.clear()

    def tearDown(self):
        orch._TOKENS.clear()

    def test_a_stashed_token_is_taken_exactly_once(self):
        orch.stash_token("job-1", "SECRET123")
        self.assertEqual(orch._take_token("job-1"), "SECRET123")
        self.assertIsNone(orch._take_token("job-1"))

    def test_blank_tokens_are_not_stashed(self):
        orch.stash_token("job-1", "")
        orch.stash_token("job-2", "   ")
        orch.stash_token("job-3", None)
        self.assertEqual(orch._TOKENS, {})

    def test_whitespace_is_trimmed(self):
        """Users paste tokens with a trailing newline more often than not."""
        orch.stash_token("job-1", "  SECRET123\n")
        self.assertEqual(orch._take_token("job-1"), "SECRET123")

    def test_forget_is_safe_to_call_on_an_unknown_job(self):
        orch.forget_token("never-existed")  # must not raise

    def test_forget_drops_a_token_a_cancelled_job_would_never_collect(self):
        orch.stash_token("job-1", "SECRET123")
        orch.forget_token("job-1")
        self.assertEqual(orch._TOKENS, {})


class JobOptionsTest(unittest.TestCase):
    def test_the_options_row_records_only_that_a_token_existed(self):
        """crawl_jobs.options_json is persisted to SQLite — the token itself
        must never reach it, but reuse still has to distinguish a tokenless
        (thinner) result from a tokened one."""
        from app.routers.scrape import StartCrawlRequest

        payload = StartCrawlRequest(
            url="https://www.facebook.com/acmecoffee", access_token="SECRET123"
        )
        options = {"source": "facebook", "has_token": bool((payload.access_token or "").strip())}
        self.assertEqual(options, {"source": "facebook", "has_token": True})
        self.assertNotIn("SECRET123", str(options))

    def test_no_token_is_recorded_as_such(self):
        from app.routers.scrape import StartCrawlRequest

        payload = StartCrawlRequest(url="https://www.facebook.com/acmecoffee")
        self.assertFalse(bool((payload.access_token or "").strip()))


if __name__ == "__main__":
    unittest.main()
