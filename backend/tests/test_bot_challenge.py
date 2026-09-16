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
