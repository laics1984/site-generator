"""Generation speed, with nothing about the site or its requests made worse.

Three contracts, each pinned here:

* **Design unchanged** — the planner sends the same prompts whatever order its
  calls finish in (the depth-barrier equivalence lives in
  `test_scaffold_concurrency`); a joined LLM call hands out copies, exactly as
  a cache hit would.
* **Request footprint only shrinks** — one image is downloaded once however many
  pixel passes read it; the business's own server is capped even when no crawl
  registered it; and that cap never leaks into the crawler's pacing.
* **Throttling never leaks into design** — a deadline covers the transfer, not
  the wait for a host's slot, so a queue cannot turn into an "unreadable" photo.
"""

from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

import httpx
from pydantic import BaseModel

from app.config import settings
from app.models.industry import PageScaffold
from app.services import asset_fetch, image_vision, polite
from app.services.asset_fetch import gate_source_hosts, get_asset
from app.services.llm import OpenAIClient, chat_json_cached, clear_response_cache
from app.services.planner import (
    ScaffoldedSitePlan,
    _generate_page_section_chunks,
    _split_page_sections,
)


def _counting_transport(seen: list[str], *, delay: float = 0.0, status: int = 200):
    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if delay:
            await asyncio.sleep(delay)
        return httpx.Response(status, content=b"\x89PNG bytes")

    return httpx.MockTransport(handler)


class OneDownloadPerImageTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self._public = patch.object(image_vision, "is_public_url", return_value=True)
        self._public.start()

    async def asyncTearDown(self) -> None:
        self._public.stop()

    async def test_every_pass_after_the_first_reads_the_kept_copy(self):
        seen: list[str] = []
        async with httpx.AsyncClient(transport=_counting_transport(seen)) as client:
            for _pass in range(5):  # vision, OCR, graphics, sampling, Facebook
                body = await image_vision._fetch_image_bytes(
                    "https://shop.example/a.jpg", client=client
                )
                self.assertEqual(body, b"\x89PNG bytes")
        self.assertEqual(seen, ["https://shop.example/a.jpg"])

    async def test_passes_running_side_by_side_share_one_download(self):
        seen: list[str] = []
        async with httpx.AsyncClient(transport=_counting_transport(seen, delay=0.05)) as client:
            bodies = await asyncio.gather(*(
                image_vision._fetch_image_bytes("https://shop.example/b.jpg", client=client)
                for _ in range(3)
            ))
        self.assertEqual(bodies, [b"\x89PNG bytes"] * 3)
        self.assertEqual(len(seen), 1)

    async def test_a_failed_download_is_not_remembered(self):
        seen: list[str] = []
        async with httpx.AsyncClient(transport=_counting_transport(seen, status=503)) as client:
            self.assertIsNone(
                await image_vision._fetch_image_bytes("https://shop.example/c.jpg", client=client)
            )
        async with httpx.AsyncClient(transport=_counting_transport(seen)) as client:
            self.assertEqual(
                await image_vision._fetch_image_bytes("https://shop.example/c.jpg", client=client),
                b"\x89PNG bytes",
            )
        self.assertEqual(len(seen), 2)

    async def test_a_caller_that_gives_up_does_not_cancel_the_shared_download(self):
        seen: list[str] = []
        async with httpx.AsyncClient(transport=_counting_transport(seen, delay=0.1)) as client:
            patient = asyncio.create_task(
                image_vision._fetch_image_bytes("https://shop.example/d.jpg", client=client)
            )
            await asyncio.sleep(0)
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(
                    image_vision._fetch_image_bytes("https://shop.example/d.jpg", client=client),
                    0.01,
                )
            self.assertEqual(await patient, b"\x89PNG bytes")
        self.assertEqual(len(seen), 1)

    async def test_the_cache_is_bounded_by_total_size(self):
        original = settings.image_bytes_cache_mb
        settings.image_bytes_cache_mb = 0  # nothing fits: every pass downloads
        try:
            seen: list[str] = []
            async with httpx.AsyncClient(transport=_counting_transport(seen)) as client:
                for _ in range(2):
                    await image_vision._fetch_image_bytes("https://shop.example/e.jpg", client=client)
            self.assertEqual(len(seen), 2)
        finally:
            settings.image_bytes_cache_mb = original


class SourceHostCapTest(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        polite.reset_politeness()

    async def test_the_business_server_is_capped_without_a_crawl(self):
        in_flight = peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return httpx.Response(200)

        gate_source_hosts(["https://www.shop.example/"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            # The bare domain shares the www cap (site_url.site_host).
            await asyncio.gather(*(
                get_asset(client, f"https://shop.example/{i}.jpg") for i in range(12)
            ))
        self.assertEqual(peak, polite.DEFAULT_CONCURRENCY)

    async def test_a_cdn_is_not_capped(self):
        in_flight = peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02)
            in_flight -= 1
            return httpx.Response(200)

        gate_source_hosts(["https://shop.example/"])
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await asyncio.gather(*(
                get_asset(client, f"https://images.pexels.com/{i}.jpg") for i in range(8)
            ))
        self.assertEqual(peak, 8)

    async def test_the_cap_never_becomes_the_crawls_pacing(self):
        """polite's registry is get-or-create: an entry put there would pace a
        later crawl of the host with no delay. The cap lives elsewhere."""
        gate_source_hosts(["https://shop.example/"])
        self.assertIsNone(polite.existing_politeness("shop.example"))
        crawl = await polite.get_politeness("shop.example")
        self.assertEqual(crawl.min_delay_sec, polite.DEFAULT_MIN_DELAY_MS / 1000)

    async def test_a_crawled_host_keeps_the_crawls_own_slots(self):
        await polite.get_politeness("shop.example", concurrency=1, min_delay_ms=0)
        gate_source_hosts(["https://shop.example/"])
        in_flight = peak = 0

        async def handler(request: httpx.Request) -> httpx.Response:
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.01)
            in_flight -= 1
            return httpx.Response(200)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await asyncio.gather(*(get_asset(client, f"https://shop.example/{i}") for i in range(4)))
        self.assertEqual(peak, 1)

    async def test_a_deadline_starts_when_the_slot_is_held(self):
        """Waiting behind other downloads must not make a timed caller (photo
        sampling, 4s) give up — that would assume the photo maximally bright
        and change its scrim."""
        asset_fetch._SOURCE_GATES["shop.example"] = (
            asyncio.get_running_loop(), asyncio.Semaphore(1)
        )

        async def handler(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.1)
            return httpx.Response(200, content=b"ok")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            started = time.monotonic()
            results = await asyncio.gather(*(
                get_asset(client, f"https://shop.example/{i}.jpg", budget=0.25) for i in range(5)
            ))
        self.assertTrue(all(r.status_code == 200 for r in results))
        # Five transfers through one slot: each took 100ms under a 250ms budget,
        # though the last one waited ~400ms — longer than its budget — for its turn.
        self.assertGreaterEqual(time.monotonic() - started, 0.45)


class _Answer(BaseModel):
    text: str
    items: list[str] = []


class _SlowClient(OpenAIClient):
    def __init__(self) -> None:
        super().__init__(model="test-model")
        self.calls = 0

    async def resolve_model(self) -> str:
        return "test-model"

    async def chat_json(self, **kwargs):
        self.calls += 1
        await asyncio.sleep(0.05)
        return _Answer(text="hello", items=["a"])


class JoinedLlmCallTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        clear_response_cache()

    def tearDown(self) -> None:
        clear_response_cache()

    async def test_identical_calls_in_flight_send_one_request(self):
        client = _SlowClient()
        answers = await asyncio.gather(*(
            chat_json_cached(client, system_prompt="s", user_prompt="u", schema=_Answer)
            for _ in range(3)
        ))
        self.assertEqual(client.calls, 1)
        self.assertEqual({a.text for a in answers}, {"hello"})
        # Each caller owns its copy — callers mutate results in place.
        answers[0].items.append("mutated")
        self.assertEqual(answers[1].items, ["a"])

    async def test_a_joined_answer_is_cached_for_the_next_caller(self):
        client = _SlowClient()
        await chat_json_cached(client, system_prompt="s", user_prompt="u", schema=_Answer)
        await chat_json_cached(client, system_prompt="s", user_prompt="u", schema=_Answer)
        self.assertEqual(client.calls, 1)

    async def test_different_calls_are_not_joined(self):
        client = _SlowClient()
        await asyncio.gather(
            chat_json_cached(client, system_prompt="s", user_prompt="one", schema=_Answer),
            chat_json_cached(client, system_prompt="s", user_prompt="two", schema=_Answer),
        )
        self.assertEqual(client.calls, 2)


class ChunkFanOutTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_large_pages_section_groups_go_out_together_in_order(self):
        sections = ["hero", "about", "features", "services", "process", "team",
                    "testimonials", "faq", "pricing", "gallery", "stats", "cta"]
        scaffold = PageScaffold(
            page_type="landing", slug="big", title="Big",
            sections=sections * 2,  # type: ignore[arg-type]
        )
        groups = _split_page_sections(scaffold.sections, settings.max_sections_per_batch)
        self.assertGreater(len(groups), 1)
        in_flight = peak = 0
        order: list[int] = []

        async def call(*, system_prompt: str, user_prompt: str) -> ScaffoldedSitePlan:
            nonlocal in_flight, peak
            index = len(order)
            order.append(index)
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.02 * (len(groups) - index))  # later groups finish first
            in_flight -= 1
            return ScaffoldedSitePlan(site_name="Acme", pages=[])

        from app.models.content_blocks import SourceContent

        source = SourceContent(source_kind="url", source_ref="https://acme.example", title="Acme", raw_text="x")
        await _generate_page_section_chunks(
            source, None, scaffold, {}, {"big": source}, object(), call=call,  # type: ignore[arg-type]
        )
        self.assertEqual(peak, len(groups))


if __name__ == "__main__":
    unittest.main()
