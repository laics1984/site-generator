"""Job dispatch and token handling.

The token assertions are the important ones: a Page access token belongs to the
request that supplied it and must not outlive it, must never be written to the
jobs table, and must never appear in an error the user sees.
"""

import time
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
    """The reuse key. Drives `scrape.job_options` itself — an inline copy of the
    dict here would pass while the router did something else."""

    def _options(self, **kwargs):
        from app.routers.scrape import StartCrawlRequest, job_options

        return job_options(
            "facebook",
            StartCrawlRequest(url="https://www.facebook.com/acmecoffee", **kwargs),
        )

    def test_the_options_row_records_only_that_a_token_existed(self):
        """crawl_jobs.options_json is persisted to SQLite — the token itself
        must never reach it, but reuse still has to distinguish a tokenless
        (thinner) result from a tokened one."""
        options = self._options(access_token="SECRET123")
        self.assertTrue(options["has_token"])
        self.assertNotIn("SECRET123", str(options))

    def test_no_token_is_recorded_as_such(self):
        self.assertFalse(self._options()["has_token"])

    def test_a_connected_session_changes_the_reuse_key(self):
        """Otherwise the first read after signing in is served the anonymous
        result cached before it, and signing in looks like a no-op."""
        from app.services import browser_session

        self.assertFalse(self._options()["has_session"])

        browser_session.save(
            "facebook",
            {
                "cookies": [
                    {"name": "c_user", "value": "1", "domain": ".facebook.com",
                     "path": "/", "expires": time.time() + 86400},
                    {"name": "xs", "value": "2", "domain": ".facebook.com",
                     "path": "/", "expires": time.time() + 86400},
                ],
                "origins": [],
            },
        )
        try:
            self.assertTrue(self._options()["has_session"])
            self.assertNotEqual(self._options(), {"source": "facebook", "has_token": False})
        finally:
            browser_session.clear("facebook")

    def test_the_session_itself_never_reaches_the_options_row(self):
        """Same rule as the token: options_json goes to SQLite."""
        options = self._options()
        self.assertNotIn("cookies", str(options))
        self.assertIsInstance(options["has_session"], bool)


if __name__ == "__main__":
    unittest.main()
