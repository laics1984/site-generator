"""A bot-protection challenge is recognized by the header its vendor documents."""

from __future__ import annotations

import unittest
from unittest import mock

import httpx

from app.services import browser, fast_fetch
from app.services.fast_fetch import FastFetchSkipReason
from app.services.polite import is_bot_challenge


class IsBotChallengeTest(unittest.TestCase):
    def test_cloudflare_challenge_header(self):
        self.assertTrue(is_bot_challenge(httpx.Headers({"CF-Mitigated": "challenge"})))

    def test_ordinary_responses_are_not_challenges(self):
        self.assertFalse(is_bot_challenge(httpx.Headers({"server": "cloudflare"})))
        self.assertFalse(is_bot_challenge({}))


class FastFetchChallengeTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_challenge_page_is_reported_as_challenged_not_as_an_error(self):
        # Cloudflare's managed challenge: a 403 "Just a moment..." interstitial.
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                403,
                headers={"cf-mitigated": "challenge", "content-type": "text/html"},
                text="<html><title>Just a moment...</title></html>",
            )
        )
        real_client = httpx.AsyncClient

        with mock.patch.object(fast_fetch, "assert_public_url", mock.AsyncMock()), mock.patch.object(
            fast_fetch.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
        ):
            result = await fast_fetch.try_fast_fetch("https://site.test/about-us/")

        self.assertEqual(result.reason, FastFetchSkipReason.CHALLENGED)
        self.assertEqual(result.http_status, 403)


class RenderedPageChallengeTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_challenge_to_the_browser_is_flagged(self):
        response = mock.Mock(status=403)
        response.all_headers = mock.AsyncMock(return_value={"cf-mitigated": "challenge"})
        page = mock.Mock(goto=mock.AsyncMock(return_value=response), close=mock.AsyncMock())
        context = mock.Mock(new_page=mock.AsyncMock(return_value=page))

        with mock.patch.object(browser, "assert_public_url", mock.AsyncMock()):
            with self.assertRaises(browser.RenderError) as caught:
                async with browser.rendered_page(context, "https://site.test/", timeout_ms=1000):
                    pass

        self.assertTrue(caught.exception.challenged)
        self.assertEqual(caught.exception.status, 403)
        page.close.assert_awaited()


if __name__ == "__main__":
    unittest.main()


# --- a challenge served as 200 with no header ------------------------------------


def _fixture(name: str) -> str:
    from pathlib import Path

    return (Path(__file__).parent / "fixtures" / name).read_text()


class ChallengePageTest(unittest.TestCase):
    """Imunify360 answers 200 with no header, so the page itself is the evidence
    — read by the field names its script posts, never by its visible wording."""

    def test_the_imunify_challenge_is_recognised(self):
        from app.services.polite import is_challenge_page

        self.assertTrue(is_challenge_page(_fixture("challenge_imunify.html")))

    def test_its_wording_alone_is_not_evidence(self):
        from app.services.polite import is_challenge_page

        html = "<html><title>One moment, please...</title><body><p>Please wait while your request is being verified...</p></body></html>"
        self.assertFalse(is_challenge_page(html))

    def test_a_real_page_that_mentions_the_token_is_not_a_challenge(self):
        from app.services.polite import is_challenge_page

        prose = "Our kindergarten welcomes children aged two to six. " * 20
        html = (
            f"<html><body><p>{prose}</p>"
            "<script>var wsidchk = 1, failedChecks = [];</script></body></html>"
        )
        self.assertFalse(is_challenge_page(html))
        # …and a token in visible copy, not a script, never counts at all.
        self.assertFalse(is_challenge_page("<p>wsidchk failedChecks</p>"))

    def test_the_parser_reports_a_challenge_not_missing_text(self):
        from app.services.scraper import ScrapeError, _parse_rendered_html

        with self.assertRaises(ScrapeError) as caught:
            _parse_rendered_html(_fixture("challenge_imunify.html"), "https://mykiddyland.com/")
        self.assertTrue(caught.exception.challenged)
        self.assertEqual(caught.exception.status, 403)
        self.assertIn("mykiddyland.com", str(caught.exception))
        self.assertIn("allowlist", str(caught.exception))

    def test_a_thin_ordinary_page_still_says_not_enough_text(self):
        from app.services.scraper import ScrapeError, _parse_rendered_html

        with self.assertRaises(ScrapeError) as caught:
            _parse_rendered_html("<html><body><p>Hi.</p></body></html>", "https://x.test/")
        self.assertFalse(caught.exception.challenged)
        self.assertIn("Could not extract enough text", str(caught.exception))


class FastFetchChallengePageTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_headerless_challenge_is_reported_as_challenged(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"server": "openresty", "content-type": "text/html"},
                text=_fixture("challenge_imunify.html"),
            )
        )
        real_client = httpx.AsyncClient

        with mock.patch.object(fast_fetch, "assert_public_url", mock.AsyncMock()), mock.patch.object(
            fast_fetch.httpx, "AsyncClient", lambda **kwargs: real_client(transport=transport, **kwargs)
        ):
            result = await fast_fetch.try_fast_fetch("https://mykiddyland.com/")

        self.assertEqual(result.reason, FastFetchSkipReason.CHALLENGED)
        self.assertEqual(result.http_status, 200)


class RenderedChallengePageTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_rendered_challenge_page_is_flagged(self):
        response = mock.Mock(status=200)
        response.all_headers = mock.AsyncMock(return_value={"server": "openresty"})
        page = mock.Mock(
            goto=mock.AsyncMock(return_value=response),
            wait_for_load_state=mock.AsyncMock(),
            content=mock.AsyncMock(return_value=_fixture("challenge_imunify.html")),
            close=mock.AsyncMock(),
        )
        context = mock.Mock(new_page=mock.AsyncMock(return_value=page))

        with mock.patch.object(browser, "assert_public_url", mock.AsyncMock()):
            with self.assertRaises(browser.RenderError) as caught:
                async with browser.rendered_page(context, "https://mykiddyland.com/", timeout_ms=1000):
                    pass

        self.assertTrue(caught.exception.challenged)
        self.assertEqual(caught.exception.status, 403)
        page.close.assert_awaited()
