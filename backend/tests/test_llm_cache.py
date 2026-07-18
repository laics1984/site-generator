"""chat_json_cached — the opt-in LLM response cache behind fast regeneration.

Identical inputs within the TTL must return the previously validated result
with exactly one HTTP round-trip; any input change is a miss; entries are
deep-copied both ways so callers can mutate results freely; and non-backend
clients (test fakes) bypass the cache entirely.
"""

from __future__ import annotations

import json
import unittest

from pydantic import BaseModel

from app.services import llm as llm_mod
from app.services.llm import MlxClient, chat_json_cached, clear_response_cache


class _Plan(BaseModel):
    name: str
    items: list[str] = []


def _sse(body: str) -> list[str]:
    """Frame an assistant message `body` as an OpenAI SSE stream (one delta + DONE)."""
    return [
        f'data: {json.dumps({"choices": [{"delta": {"content": body}}]})}',
        "data: [DONE]",
    ]


class _FakeStreamCtx:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; records every POST payload."""

    def __init__(self, bodies, recorder):
        self._bodies = bodies
        self._recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None, **kwargs):
        self._recorder.append({"url": url, "payload": json})
        return _FakeStreamCtx(_sse(self._bodies.pop(0)))


def _patch_httpx(testcase, bodies, recorder):
    original = llm_mod.httpx.AsyncClient
    llm_mod.httpx.AsyncClient = lambda *a, **k: _FakeAsyncClient(bodies, recorder)
    testcase.addCleanup(setattr, llm_mod.httpx, "AsyncClient", original)


class _FakeLlm:
    """A non-backend client (like every test double in this suite) — must
    bypass the cache so fixtures that count calls keep working."""

    def __init__(self):
        self.calls = 0

    async def chat_json(self, system_prompt, user_prompt, schema, temperature=None,
                        num_ctx=None, images=None, think=None):
        self.calls += 1
        return _Plan(name=f"call-{self.calls}")


class ChatJsonCachedTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        s = llm_mod.settings
        self._orig = (
            s.llm_cache_enabled, s.llm_cache_ttl_seconds, s.llm_cache_max_entries
        )
        s.llm_cache_enabled = True
        s.llm_cache_ttl_seconds = 1800
        s.llm_cache_max_entries = 64
        clear_response_cache()

    def tearDown(self):
        s = llm_mod.settings
        (
            s.llm_cache_enabled, s.llm_cache_ttl_seconds, s.llm_cache_max_entries
        ) = self._orig
        clear_response_cache()

    async def _call(self, user_prompt="u", temperature=0.25, **kwargs):
        return await chat_json_cached(
            MlxClient(),
            system_prompt="sys",
            user_prompt=user_prompt,
            schema=_Plan,
            temperature=temperature,
            **kwargs,
        )

    async def test_identical_call_hits_cache_with_one_http_round_trip(self):
        recorder = []
        _patch_httpx(self, ['{"name": "a", "items": ["x"]}'], recorder)
        first = await self._call()
        # New client instance, same backend/model/prompts → still a hit.
        second = await self._call()
        self.assertEqual(len(recorder), 1)
        self.assertEqual(second, first)

    async def test_hits_return_isolated_deep_copies(self):
        recorder = []
        _patch_httpx(self, ['{"name": "a", "items": ["x"]}'], recorder)
        first = await self._call()
        first.items.append("mutated-by-first-caller")

        second = await self._call()
        self.assertEqual(second.items, ["x"])  # pristine despite first's mutation
        second.items.append("mutated-by-second-caller")

        third = await self._call()
        self.assertEqual(third.items, ["x"])
        self.assertEqual(len(recorder), 1)

    async def test_zero_ttl_disables_reuse(self):
        llm_mod.settings.llm_cache_ttl_seconds = 0
        recorder = []
        _patch_httpx(self, ['{"name": "a"}', '{"name": "b"}'], recorder)
        await self._call()
        await self._call()
        self.assertEqual(len(recorder), 2)

    async def test_key_is_sensitive_to_prompt_and_sampling(self):
        recorder = []
        _patch_httpx(
            self, ['{"name": "a"}', '{"name": "b"}', '{"name": "c"}'], recorder
        )
        await self._call(user_prompt="u1", temperature=0.2)
        await self._call(user_prompt="u2", temperature=0.2)  # prompt differs → miss
        await self._call(user_prompt="u1", temperature=0.3)  # temperature differs → miss
        self.assertEqual(len(recorder), 3)

    async def test_kill_switch_bypasses_cache(self):
        llm_mod.settings.llm_cache_enabled = False
        recorder = []
        _patch_httpx(self, ['{"name": "a"}', '{"name": "b"}'], recorder)
        await self._call()
        await self._call()
        self.assertEqual(len(recorder), 2)

    async def test_lru_eviction_keeps_recently_used_entries(self):
        llm_mod.settings.llm_cache_max_entries = 2
        recorder = []
        _patch_httpx(
            self,
            ['{"name": "1"}', '{"name": "2"}', '{"name": "3"}', '{"name": "2b"}'],
            recorder,
        )
        await self._call(user_prompt="u1")  # miss → cached
        await self._call(user_prompt="u2")  # miss → cached
        await self._call(user_prompt="u1")  # hit → bumps u1's recency
        await self._call(user_prompt="u3")  # miss → evicts u2 (LRU), not u1
        self.assertEqual(len(recorder), 3)

        result = await self._call(user_prompt="u1")  # still cached
        self.assertEqual(result.name, "1")
        self.assertEqual(len(recorder), 3)

        await self._call(user_prompt="u2")  # was evicted → fresh HTTP call
        self.assertEqual(len(recorder), 4)

    async def test_fake_clients_never_cache(self):
        fake = _FakeLlm()
        first = await chat_json_cached(
            fake, system_prompt="s", user_prompt="u", schema=_Plan, temperature=0.0
        )
        second = await chat_json_cached(
            fake, system_prompt="s", user_prompt="u", schema=_Plan, temperature=0.0
        )
        self.assertEqual(fake.calls, 2)
        self.assertNotEqual(first.name, second.name)


if __name__ == "__main__":
    unittest.main()
