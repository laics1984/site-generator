"""The single OpenAI-compatible client, model auto-discovery, and role routing.

There is no backend switch any more: every engine the ai-server can run speaks
the OpenAI wire, so the backend has one client and no notion of which engine is
behind the URL. The model id is discovered from /v1/models rather than
configured, which is what lets a model swap in ai-server/.env take effect here
without an edit or a restart.
"""

import json
import os
import unittest
from unittest import mock

from pydantic import BaseModel

from app.config import Settings
from app.services import llm as llm_mod
from app.services.llm import (
    EmptyLlmResponse,
    LlmError,
    OpenAIClient,
    TruncatedLlmResponse,
    get_llm,
    get_reasoning_llm,
)


class _Out(BaseModel):
    x: int


def _sse(body: str) -> list[str]:
    """Frame an assistant message `body` as an OpenAI SSE stream (one delta + DONE)."""
    return [
        "",  # blank keep-alive line, must be tolerated
        f'data: {json.dumps({"choices": [{"delta": {"content": body}}]})}',
        "data: [DONE]",
    ]


def _sse_truncated(body: str) -> list[str]:
    """OpenAI-style SSE stream that stopped because max_tokens ran out."""
    return [
        f'data: {json.dumps({"choices": [{"delta": {"content": body}, "finish_reason": "length"}]})}',
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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient. `.stream()` pops the next prepared body
    and frames it as SSE, recording url + payload; `.get()` answers the
    /v1/models probe that model discovery makes. Probes go in their own list so
    they don't shift the indices tests assert against."""

    def __init__(self, bodies, recorder, framer=_sse, models=("test-model",), probes=None):
        self._bodies = bodies
        self._recorder = recorder
        self._framer = framer
        self._models = models
        self._probes = probes if probes is not None else []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, headers=None, **kwargs):
        self._probes.append({"url": url, "headers": headers})
        return _FakeResponse({"data": [{"id": m} for m in self._models]})

    def stream(self, method, url, json=None, **kwargs):
        self._recorder.append(
            {"url": url, "payload": json, "headers": kwargs.get("headers")}
        )
        body = self._bodies.pop(0)
        framer = self._framer
        if isinstance(body, tuple):  # (body, framer) overrides the default for this call
            body, framer = body
        return _FakeStreamCtx(framer(body))


def _patch_httpx(testcase, bodies, recorder, framer=_sse, models=("test-model",), probes=None):
    original = llm_mod.httpx.AsyncClient
    llm_mod.httpx.AsyncClient = lambda *a, **k: _FakeAsyncClient(
        bodies, recorder, framer, models, probes
    )
    testcase.addCleanup(setattr, llm_mod.httpx, "AsyncClient", original)
    # Discovered ids are cached process-wide; without this a test would inherit
    # whatever model a previous one advertised.
    llm_mod.clear_model_cache()
    testcase.addCleanup(llm_mod.clear_model_cache)


class ClientFactoryTest(unittest.TestCase):
    """One client for every engine — nothing to select."""

    def test_get_llm_returns_the_openai_client(self):
        self.assertIsInstance(get_llm(), OpenAIClient)

    def test_base_url_comes_from_settings(self):
        self.assertEqual(
            get_llm().base_url, llm_mod.settings.llm_base_url.rstrip("/")
        )


class ModelDiscoveryTest(unittest.IsolatedAsyncioTestCase):
    """The backend stores no model name: it asks the server what it serves."""

    def setUp(self):
        self._orig_model = llm_mod.settings.llm_model
        llm_mod.settings.llm_model = None
        self.addCleanup(setattr, llm_mod.settings, "llm_model", self._orig_model)

    async def test_model_is_read_from_v1_models(self):
        recorder, probes = [], []
        _patch_httpx(self, ['{"x": 1}'], recorder, models=("qwen3.6:35b-a3b",), probes=probes)
        await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertTrue(probes[0]["url"].endswith("/v1/models"))
        self.assertEqual(recorder[0]["payload"]["model"], "qwen3.6:35b-a3b")

    async def test_discovery_is_cached_across_calls(self):
        recorder, probes = [], []
        _patch_httpx(self, ['{"x": 1}', '{"x": 2}'], recorder, probes=probes)
        client = OpenAIClient()
        await client.chat_json("sys", "user", _Out)
        await client.chat_json("sys", "user", _Out)
        self.assertEqual(len(probes), 1)  # second call reused the cached id

    async def test_configured_model_pins_and_skips_discovery(self):
        recorder, probes = [], []
        _patch_httpx(self, ['{"x": 1}'], recorder, models=("ignored",), probes=probes)
        llm_mod.settings.llm_model = "pinned-model"
        await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertEqual(probes, [])
        self.assertEqual(recorder[0]["payload"]["model"], "pinned-model")

    async def test_multiple_models_picks_deterministically(self):
        # Sorted, so the choice doesn't depend on the server's listing order.
        recorder, probes = [], []
        _patch_httpx(self, ['{"x": 1}'], recorder, models=("zeta", "alpha"), probes=probes)
        await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertEqual(recorder[0]["payload"]["model"], "alpha")

    async def test_server_with_no_models_raises_llm_error(self):
        # Ollama answers "data": null (not []) before anything is pulled.
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder, models=())
        with self.assertRaises(LlmError):
            await OpenAIClient().chat_json("sys", "user", _Out)


class OpenAIClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_json_validates(self):
        recorder = []
        _patch_httpx(self, ['{"x": 7}'], recorder)
        out = await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 7)
        self.assertTrue(recorder[0]["url"].endswith("/v1/chat/completions"))
        self.assertTrue(recorder[0]["payload"]["stream"])

    async def test_no_num_ctx_is_sent(self):
        # Context size is a SERVER setting (LLM_CTX in ai-server/.env); the
        # OpenAI wire has no per-request equivalent.
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder)
        await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertNotIn("num_ctx", recorder[0]["payload"])
        self.assertNotIn("options", recorder[0]["payload"])

    async def test_thinking_disabled_by_default(self):
        # JSON calls must turn off Qwen3 thinking, else `reasoning` burns the token
        # budget and `content` comes back empty.
        recorder = []
        _patch_httpx(self, ['{"x": 1}', '{"x": 1}'], recorder)
        await OpenAIClient().chat_json("sys", "user", _Out)  # default think=False
        self.assertEqual(
            recorder[0]["payload"]["chat_template_kwargs"], {"enable_thinking": False}
        )
        await OpenAIClient().chat_json("sys", "user", _Out, think=True)
        self.assertEqual(
            recorder[1]["payload"]["chat_template_kwargs"], {"enable_thinking": True}
        )

    async def test_thinking_off_sends_both_kill_switches(self):
        # No single field works everywhere: llama.cpp/mlx honour
        # chat_template_kwargs, Ollama's OpenAI endpoint ignores it and honours
        # reasoning_effort. Measured against Ollama: with only
        # chat_template_kwargs, qwen3 emitted ~1000 reasoning tokens and an
        # EMPTY content, which the backend would see as an empty stream.
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder)
        await OpenAIClient().chat_json("sys", "user", _Out)
        payload = recorder[0]["payload"]
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(payload["reasoning_effort"], "none")

    async def test_thinking_on_does_not_force_effort_none(self):
        # The reasoning role genuinely wants to think — don't tell a server that
        # implements reasoning_effort to skip it.
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder)
        await OpenAIClient().chat_json("sys", "user", _Out, think=True)
        payload = recorder[0]["payload"]
        self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": True})
        self.assertNotIn("reasoning_effort", payload)

    async def test_repetition_penalty_sent_by_default(self):
        # mlx_lm.server defaults repetition_penalty to 0.0 (off), unlike Ollama —
        # send a non-zero default so a small model can't loop re-emitting the same
        # JSON fragment until it exhausts max_tokens (config.llm_repetition_penalty).
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder)
        await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertEqual(recorder[0]["payload"]["repetition_penalty"], 1.1)

    async def test_repetition_penalty_override_and_disable(self):
        recorder = []
        _patch_httpx(self, ['{"x": 1}', '{"x": 1}'], recorder)
        await OpenAIClient(repetition_penalty=1.3).chat_json("sys", "user", _Out)
        self.assertEqual(recorder[0]["payload"]["repetition_penalty"], 1.3)
        await OpenAIClient(repetition_penalty=0.0).chat_json("sys", "user", _Out)
        self.assertNotIn("repetition_penalty", recorder[1]["payload"])

    async def test_think_preamble_is_stripped(self):
        recorder = []
        _patch_httpx(self, ['<think>let me think</think>{"x": 3}'], recorder)
        out = await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 3)

    async def test_invalid_first_response_triggers_repair(self):
        recorder = []
        _patch_httpx(self, ['{"y": 1}', '{"x": 9}'], recorder)  # 1st invalid, 2nd fixed
        out = await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 9)
        self.assertEqual(len(recorder), 2)  # repair round happened
        # Repair payload carries the original turns + assistant + corrective user.
        self.assertEqual(len(recorder[1]["payload"]["messages"]), 4)

    async def test_empty_stream_raises_after_retry(self):
        # An empty stream is retried once; when BOTH attempts come back empty the
        # call fails (EmptyLlmResponse is an LlmError subclass → still a 502).
        recorder = []
        _patch_httpx(self, ["", ""], recorder)
        with self.assertRaises(EmptyLlmResponse):
            await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertEqual(len(recorder), 2)  # retried the empty response

    async def test_empty_stream_recovers_on_retry(self):
        # A transient empty first response is retried and the second (valid)
        # response is used — the whole generation no longer dies on one empty.
        recorder = []
        _patch_httpx(self, ["", '{"x": 5}'], recorder)
        out = await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 5)
        self.assertEqual(len(recorder), 2)

    async def test_images_route_to_vision_server_as_data_urls(self):
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder)
        client = OpenAIClient(
            vision_base_url="http://host:8081", vision_model="vlm-model"
        )
        await client.chat_json("sys", "describe", _Out, images=["QUJD"])

        self.assertEqual(recorder[0]["url"], "http://host:8081/v1/chat/completions")
        payload = recorder[0]["payload"]
        self.assertEqual(payload["model"], "vlm-model")
        parts = payload["messages"][1]["content"]
        kinds = {p["type"] for p in parts}
        self.assertIn("image_url", kinds)
        image_part = next(p for p in parts if p["type"] == "image_url")
        self.assertEqual(
            image_part["image_url"]["url"], "data:image/jpeg;base64,QUJD"
        )


class TruncatedResponseTest(unittest.IsolatedAsyncioTestCase):
    """A response cut off by the output budget (finish_reason='length') is a
    different failure than malformed JSON: retrying with the same budget would
    just truncate again, so _validated keeps DOUBLING max_tokens and retrying
    (see _retry_with_growing_budget) instead of the usual repair-prompt retry,
    until either a response fits or the budget hits its hard cap (131072)."""

    async def test_truncation_retries_with_doubled_max_tokens(self):
        recorder = []
        _patch_httpx(
            self,
            [('{"x": 1', _sse_truncated), '{"x": 9}'],
            recorder,
        )
        out = await OpenAIClient(max_tokens=1000).chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 9)
        self.assertEqual(len(recorder), 2)
        self.assertEqual(recorder[0]["payload"]["max_tokens"], 1000)
        self.assertEqual(recorder[1]["payload"]["max_tokens"], 2000)

    async def test_truncation_keeps_retrying_across_multiple_boosts(self):
        # Two truncations in a row, then success — the budget must keep doubling
        # (not give up after one retry) until it fits.
        recorder = []
        _patch_httpx(
            self,
            [
                ('{"x": 1', _sse_truncated),
                ('{"x": 1', _sse_truncated),
                '{"x": 9}',
            ],
            recorder,
        )
        out = await OpenAIClient(max_tokens=1000).chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 9)
        self.assertEqual(len(recorder), 3)
        self.assertEqual(recorder[1]["payload"]["max_tokens"], 2000)
        self.assertEqual(recorder[2]["payload"]["max_tokens"], 4000)

    async def test_still_truncated_at_budget_cap_raises(self):
        # Starting already at the hard cap: the first failure can't be boosted
        # any further, so it raises immediately with no extra HTTP call.
        recorder = []
        _patch_httpx(self, ['{"x": 1'], recorder, framer=_sse_truncated)
        with self.assertRaises(TruncatedLlmResponse):
            await OpenAIClient(max_tokens=131072).chat_json("sys", "user", _Out)
        self.assertEqual(len(recorder), 1)


class SettingsDrivenDefaultsTest(unittest.IsolatedAsyncioTestCase):
    """chat_json resolves temperature/think from settings when a call site
    passes nothing — retuning is a .env change, not a code change."""

    def setUp(self):
        s = llm_mod.settings
        self._orig = (s.llm_default_temperature, s.llm_think)

    def tearDown(self):
        s = llm_mod.settings
        s.llm_default_temperature, s.llm_think = self._orig

    async def test_payload_reflects_settings(self):
        llm_mod.settings.llm_default_temperature = 0.22
        llm_mod.settings.llm_think = True
        recorder = []
        _patch_httpx(self, ['{"x": 4}'], recorder)
        out = await OpenAIClient().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 4)
        payload = recorder[0]["payload"]
        self.assertEqual(payload["temperature"], 0.22)
        self.assertEqual(
            payload["chat_template_kwargs"], {"enable_thinking": True}
        )

    async def test_explicit_arguments_beat_settings(self):
        llm_mod.settings.llm_default_temperature = 0.22
        llm_mod.settings.llm_think = True
        recorder = []
        _patch_httpx(self, ['{"x": 6}'], recorder)
        await OpenAIClient().chat_json(
            "sys", "user", _Out, think=False, temperature=0.9
        )
        payload = recorder[0]["payload"]
        self.assertEqual(payload["temperature"], 0.9)
        self.assertEqual(
            payload["chat_template_kwargs"], {"enable_thinking": False}
        )


class ReasoningRoleTest(unittest.IsolatedAsyncioTestCase):
    """get_reasoning_llm() routes the judgment-heavy calls to a different
    ENDPOINT when configured, and is a transparent alias for get_llm() when not.
    Which role talks to which endpoint is application routing, so it survives
    here even though engine selection does not."""

    _FIELDS = (
        "reasoning_base_url",
        "reasoning_model",
        "reasoning_api_key",
        "reasoning_timeout_seconds",
        "reasoning_max_tokens",
        "reasoning_think",
    )

    def setUp(self):
        s = llm_mod.settings
        self._orig = {f: getattr(s, f) for f in self._FIELDS}
        for f in ("reasoning_base_url", "reasoning_model", "reasoning_api_key"):
            setattr(s, f, None)

    def tearDown(self):
        for f, v in self._orig.items():
            setattr(llm_mod.settings, f, v)

    def test_unset_role_falls_back_to_default_client(self):
        s = llm_mod.settings
        s.reasoning_base_url = None
        s.reasoning_model = None
        client = get_reasoning_llm()
        self.assertIsInstance(client, OpenAIClient)
        self.assertEqual(client.base_url, s.llm_base_url.rstrip("/"))

    def test_base_url_alone_enables_the_role(self):
        # A second endpoint with its own single model needs no model name.
        s = llm_mod.settings
        s.reasoning_base_url = "http://ai-server:8000"
        s.reasoning_model = None
        client = get_reasoning_llm()
        self.assertEqual(client.base_url, "http://ai-server:8000")
        self.assertIsNone(client.model)

    async def test_payload_headers_and_thinking(self):
        s = llm_mod.settings
        s.reasoning_base_url = "http://ai-server:8000"
        s.reasoning_model = "glm-z1-9b"
        s.reasoning_api_key = "sekret"
        s.reasoning_max_tokens = 16384
        s.reasoning_think = True
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder)
        out = await get_reasoning_llm().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 1)
        call = recorder[0]
        self.assertEqual(call["url"], "http://ai-server:8000/v1/chat/completions")
        self.assertEqual(call["payload"]["model"], "glm-z1-9b")
        self.assertEqual(call["payload"]["max_tokens"], 16384)
        self.assertEqual(call["headers"], {"Authorization": "Bearer sekret"})
        self.assertEqual(
            call["payload"]["chat_template_kwargs"], {"enable_thinking": True}
        )

    async def test_explicit_think_beats_reasoning_default(self):
        s = llm_mod.settings
        s.reasoning_base_url = "http://ai-server:8000"
        s.reasoning_model = "glm-z1-9b"
        s.reasoning_think = True
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder)
        await get_reasoning_llm().chat_json("sys", "user", _Out, think=False)
        self.assertEqual(
            recorder[0]["payload"]["chat_template_kwargs"], {"enable_thinking": False}
        )

    async def test_default_client_is_isolated_from_reasoning_settings(self):
        # A plain client must not inherit the reasoning role's auth/budget.
        s = llm_mod.settings
        s.reasoning_api_key = "sekret"
        s.reasoning_max_tokens = 16384
        recorder = []
        _patch_httpx(self, ['{"x": 3}'], recorder)
        await OpenAIClient().chat_json("sys", "user", _Out)
        call = recorder[0]
        self.assertIsNone(call["headers"])
        self.assertEqual(call["payload"]["max_tokens"], s.llm_max_tokens)


class ConfigCoercionTest(unittest.TestCase):
    def test_empty_env_strings_mean_unset(self):
        # `FOO=` (present but empty) in .env must read as None. It matters most
        # for LLM_MODEL: empty has to fall through to /v1/models discovery
        # rather than being sent as a blank model id.
        s = Settings(
            _env_file=None,
            llm_model="",
            llm_api_key="",
            reasoning_base_url="",
            reasoning_model="",
            reasoning_api_key="",
        )
        self.assertIsNone(s.llm_model)
        self.assertIsNone(s.llm_api_key)
        self.assertIsNone(s.reasoning_base_url)
        self.assertIsNone(s.reasoning_model)
        self.assertIsNone(s.reasoning_api_key)

    @staticmethod
    def _env(**overrides):
        """Environment with the NEW names removed, so a legacy alias is actually
        what resolves. The test container has LLM_BASE_URL set by compose, and a
        real env var rightly beats an alias."""
        env = {
            k: v
            for k, v in os.environ.items()
            if k not in ("LLM_BASE_URL", "LLM_MODEL", "LLM_CONTEXT_TOKENS")
        }
        env.update(overrides)
        return mock.patch.dict(os.environ, env, clear=True)

    def test_legacy_env_names_still_resolve(self):
        # Old .env files keep working through the rename. Must go through the
        # ENVIRONMENT: init kwargs bypass alias resolution entirely.
        for legacy, value in (
            ("MLX_BASE_URL", "http://legacy-mlx:8080"),
            ("OLLAMA_BASE_URL", "http://legacy-ollama:11434"),
        ):
            with self.subTest(legacy), self._env(**{legacy: value}):
                self.assertEqual(Settings(_env_file=None).llm_base_url, value)

    def test_new_env_name_wins_over_legacy_alias(self):
        with self._env(
            MLX_BASE_URL="http://legacy:8080", LLM_BASE_URL="http://new:11434"
        ):
            self.assertEqual(Settings(_env_file=None).llm_base_url, "http://new:11434")

    def test_legacy_context_name_still_resolves(self):
        # SCAFFOLD_NUM_CTX was a request parameter; it is now the batcher's view
        # of the server's context window, but old .env files keep working.
        with self._env(SCAFFOLD_NUM_CTX="9999"):
            self.assertEqual(Settings(_env_file=None).llm_context_tokens, 9999)


if __name__ == "__main__":
    unittest.main()
