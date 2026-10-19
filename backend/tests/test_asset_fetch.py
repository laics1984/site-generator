"""A site's files are asked for the way its pages are (services/asset_fetch.py).

Every image pass used to send `python-httpx/<version>` and fetch outside the
crawl's per-host budget; Imunify360 hosts then started challenging this tool's
address everywhere at once.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

import httpx

from app.config import settings
from app.services import image_vision, polite
from app.services.asset_fetch import ASSET_HEADERS, get_asset


def _recording_client(seen: list[httpx.Request]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, content=b"\x89PNG fake")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class AssetHeadersTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_file_request_carries_the_crawlers_browser_identity(self):
        seen: list[httpx.Request] = []
        async with _recording_client(seen) as client:
            await get_asset(client, "https://example.com/photo.jpg")
        agent = seen[0].headers["user-agent"]
        self.assertEqual(agent, settings.http_user_agent)
        self.assertNotIn("python-httpx", agent)
        self.assertIn("image/", seen[0].headers["accept"])

    async def test_a_client_built_with_another_agent_cannot_leak_it(self):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler), headers={"User-Agent": "WebtreeSiteGen/0.1"}
        ) as client:
            await get_asset(client, "https://example.com/logo.png")
        self.assertEqual(seen[0].headers["user-agent"], ASSET_HEADERS["User-Agent"])

    async def test_the_shared_image_fetcher_uses_it(self):
        """`_fetch_image_bytes` is what OCR, the graphic screen, vision and photo
        sampling all download through — one seam covers every pass."""
        seen: list[httpx.Request] = []
        with patch.object(image_vision, "is_public_url", return_value=True):
            async with _recording_client(seen) as client:
                body = await image_vision._fetch_image_bytes(
                    "https://example.com/a.png", client=client
                )
        self.assertEqual(body, b"\x89PNG fake")
        self.assertEqual(seen[0].headers["user-agent"], settings.http_user_agent)


class AssetPacingTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        polite.reset_politeness()

    async def test_a_crawled_host_paces_its_files_with_the_crawl(self):
        await polite.get_politeness("crawled.example", concurrency=1, min_delay_ms=150)
        seen: list[httpx.Request] = []
        async with _recording_client(seen) as client:
            started = time.monotonic()
            await asyncio.gather(
                get_asset(client, "https://crawled.example/1.jpg"),
                get_asset(client, "https://crawled.example/2.jpg"),
            )
            elapsed = time.monotonic() - started
        self.assertEqual(len(seen), 2)
        self.assertGreaterEqual(elapsed, 0.14)

    async def test_a_host_the_crawl_never_visited_is_neither_paced_nor_registered(self):
        """A stock CDN must not queue behind a 200ms gap: photo sampling runs
        under a 4s budget per photo."""
        seen: list[httpx.Request] = []
        async with _recording_client(seen) as client:
            await asyncio.gather(
                *(get_asset(client, f"https://images.cdn.example/{i}.jpg") for i in range(5))
            )
        self.assertEqual(len(seen), 5)
        self.assertIsNone(polite.existing_politeness("images.cdn.example"))
