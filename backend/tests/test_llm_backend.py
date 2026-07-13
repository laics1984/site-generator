"""LLM backend selection (resolve_llm_backend / get_llm) and the MLX OpenAI client."""

import json
import unittest

from pydantic import BaseModel

from app.config import Settings
from app.services import llm as llm_mod
from app.services.llm import (
    EmptyLlmResponse,
    LlmError,
    MlxClient,
    OllamaClient,
    get_llm,
    get_reasoning_llm,
    resolve_llm_backend,
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


def _ndjson(body: str) -> list[str]:
    """Frame an assistant message `body` as an Ollama NDJSON stream."""
    return [
        json.dumps({"message": {"content": body}}),
        json.dumps({"done": True}),
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
    """Stands in for httpx.AsyncClient: each .stream() pops the next prepared body
    and frames it via `framer` (SSE for MLX, NDJSON for Ollama). Records the
    url + payload of every call."""

    def __init__(self, bodies, recorder, framer=_sse):
        self._bodies = bodies
        self._recorder = recorder
        self._framer = framer

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url, json=None, **kwargs):
        self._recorder.append(
            {"url": url, "payload": json, "headers": kwargs.get("headers")}
        )
        return _FakeStreamCtx(self._framer(self._bodies.pop(0)))


def _patch_httpx(testcase, bodies, recorder, framer=_sse):
    original = llm_mod.httpx.AsyncClient
    llm_mod.httpx.AsyncClient = lambda *a, **k: _FakeAsyncClient(
        bodies, recorder, framer
    )
    testcase.addCleanup(setattr, llm_mod.httpx, "AsyncClient", original)


class ResolveBackendTest(unittest.TestCase):
    """The backend is whatever LLM_BACKEND (settings.llm_backend) says — no
    auto-detection; get_llm returns the matching client."""

    def setUp(self):
        self._orig_choice = llm_mod.settings.llm_backend

    def tearDown(self):
        llm_mod.settings.llm_backend = self._orig_choice

    def test_ollama_setting_selects_ollama_client(self):
        llm_mod.settings.llm_backend = "ollama"
        self.assertEqual(resolve_llm_backend(), "ollama")
        self.assertIsInstance(get_llm(), OllamaClient)

    def test_mlx_setting_selects_mlx_client(self):
        llm_mod.settings.llm_backend = "mlx"
        self.assertEqual(resolve_llm_backend(), "mlx")
        self.assertIsInstance(get_llm(), MlxClient)

    def test_value_is_case_insensitive(self):
        llm_mod.settings.llm_backend = "MLX"
        self.assertEqual(resolve_llm_backend(), "mlx")


class MlxClientTest(unittest.IsolatedAsyncioTestCase):
    async def test_streamed_json_validates(self):
        recorder = []
        _patch_httpx(self, ['{"x": 7}'], recorder)
        out = await MlxClient().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 7)
        self.assertTrue(recorder[0]["url"].endswith("/v1/chat/completions"))
        self.assertTrue(recorder[0]["payload"]["stream"])

    async def test_thinking_disabled_by_default(self):
        # JSON calls must turn off Qwen3 thinking, else `reasoning` burns the token
        # budget and `content` comes back empty.
        recorder = []
        _patch_httpx(self, ['{"x": 1}', '{"x": 1}'], recorder)
        await MlxClient().chat_json("sys", "user", _Out)  # default think=False
        self.assertEqual(
            recorder[0]["payload"]["chat_template_kwargs"], {"enable_thinking": False}
        )
        await MlxClient().chat_json("sys", "user", _Out, think=True)
        self.assertEqual(
            recorder[1]["payload"]["chat_template_kwargs"], {"enable_thinking": True}
        )

    async def test_think_preamble_is_stripped(self):
        recorder = []
        _patch_httpx(self, ['<think>let me think</think>{"x": 3}'], recorder)
        out = await MlxClient().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 3)

    async def test_invalid_first_response_triggers_repair(self):
        recorder = []
        _patch_httpx(self, ['{"y": 1}', '{"x": 9}'], recorder)  # 1st invalid, 2nd fixed
        out = await MlxClient().chat_json("sys", "user", _Out)
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
            await MlxClient().chat_json("sys", "user", _Out)
        self.assertEqual(len(recorder), 2)  # retried the empty response

    async def test_empty_stream_recovers_on_retry(self):
        # A transient empty first response is retried and the second (valid)
        # response is used — the whole generation no longer dies on one empty.
        recorder = []
        _patch_httpx(self, ["", '{"x": 5}'], recorder)
        out = await MlxClient().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 5)
        self.assertEqual(len(recorder), 2)

    async def test_images_route_to_vision_server_as_data_urls(self):
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder)
        client = MlxClient(
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


class SettingsDrivenDefaultsTest(unittest.IsolatedAsyncioTestCase):
    """chat_json resolves temperature/num_ctx/think from settings when a call
    site passes nothing — retuning for a different model is a .env change, not
    a code change."""

    def setUp(self):
        s = llm_mod.settings
        self._orig = (s.llm_default_temperature, s.llm_default_num_ctx, s.llm_think)

    def tearDown(self):
        s = llm_mod.settings
        s.llm_default_temperature, s.llm_default_num_ctx, s.llm_think = self._orig

    async def test_ollama_payload_reflects_settings(self):
        llm_mod.settings.llm_default_temperature = 0.11
        llm_mod.settings.llm_default_num_ctx = 3333
        llm_mod.settings.llm_think = True
        recorder = []
        _patch_httpx(self, ['{"x": 2}'], recorder, framer=_ndjson)
        out = await OllamaClient().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 2)
        payload = recorder[0]["payload"]
        self.assertEqual(payload["options"], {"temperature": 0.11, "num_ctx": 3333})
        self.assertTrue(payload["think"])

    async def test_mlx_payload_reflects_settings(self):
        llm_mod.settings.llm_default_temperature = 0.22
        llm_mod.settings.llm_think = True
        recorder = []
        _patch_httpx(self, ['{"x": 4}'], recorder)
        out = await MlxClient().chat_json("sys", "user", _Out)
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
        await MlxClient().chat_json(
            "sys", "user", _Out, think=False, temperature=0.9
        )
        payload = recorder[0]["payload"]
        self.assertEqual(payload["temperature"], 0.9)
        self.assertEqual(
            payload["chat_template_kwargs"], {"enable_thinking": False}
        )


class ReasoningRoleTest(unittest.IsolatedAsyncioTestCase):
    """get_reasoning_llm() routes the judgment-heavy calls to the REASONING_*
    model when configured, and is a transparent alias for get_llm() when not."""

    _FIELDS = (
        "llm_backend",
        "reasoning_backend",
        "reasoning_base_url",
        "reasoning_model",
        "reasoning_api_key",
        "reasoning_timeout_seconds",
        "reasoning_max_tokens",
        "reasoning_num_ctx",
        "reasoning_think",
    )

    def setUp(self):
        s = llm_mod.settings
        self._orig = {f: getattr(s, f) for f in self._FIELDS}

    def tearDown(self):
        for f, v in self._orig.items():
            setattr(llm_mod.settings, f, v)

    def test_unset_model_falls_back_to_default_client(self):
        s = llm_mod.settings
        s.llm_backend = "ollama"
        s.reasoning_model = None
        client = get_reasoning_llm()
        self.assertIsInstance(client, OllamaClient)
        self.assertEqual(client.model, s.ollama_model)

    def test_backend_inherits_llm_backend_when_unset(self):
        s = llm_mod.settings
        s.llm_backend = "mlx"
        s.reasoning_backend = None
        s.reasoning_model = "glm-z1-9b"
        s.reasoning_base_url = "http://ai-server:8000"
        client = get_reasoning_llm()
        self.assertIsInstance(client, MlxClient)
        self.assertEqual(client.model, "glm-z1-9b")
        self.assertEqual(client.base_url, "http://ai-server:8000")

    async def test_openai_path_payload_headers_and_thinking(self):
        s = llm_mod.settings
        s.llm_backend = "ollama"  # reasoning_backend must win over this
        s.reasoning_backend = "mlx"
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
        s.reasoning_backend = "mlx"
        s.reasoning_model = "glm-z1-9b"
        s.reasoning_think = True
        recorder = []
        _patch_httpx(self, ['{"x": 1}'], recorder)
        await get_reasoning_llm().chat_json("sys", "user", _Out, think=False)
        self.assertEqual(
            recorder[0]["payload"]["chat_template_kwargs"], {"enable_thinking": False}
        )

    async def test_ollama_path_think_num_ctx_and_headers(self):
        s = llm_mod.settings
        s.reasoning_backend = "ollama"
        s.reasoning_base_url = "http://ai-server:11434"
        s.reasoning_model = "glm-z1:9b"
        s.reasoning_api_key = "sekret"
        s.reasoning_num_ctx = 8192
        s.reasoning_think = True
        recorder = []
        _patch_httpx(self, ['{"x": 2}'], recorder, framer=_ndjson)
        out = await get_reasoning_llm().chat_json("sys", "user", _Out)
        self.assertEqual(out.x, 2)
        call = recorder[0]
        self.assertEqual(call["url"], "http://ai-server:11434/api/chat")
        self.assertEqual(call["payload"]["model"], "glm-z1:9b")
        self.assertTrue(call["payload"]["think"])
        self.assertEqual(call["payload"]["options"]["num_ctx"], 8192)
        self.assertEqual(call["headers"], {"Authorization": "Bearer sekret"})

    async def test_ollama_path_num_ctx_falls_back_to_global_default(self):
        s = llm_mod.settings
        s.reasoning_backend = "ollama"
        s.reasoning_model = "glm-z1:9b"
        s.reasoning_num_ctx = None
        recorder = []
        _patch_httpx(self, ['{"x": 2}'], recorder, framer=_ndjson)
        await get_reasoning_llm().chat_json("sys", "user", _Out)
        self.assertEqual(
            recorder[0]["payload"]["options"]["num_ctx"], s.llm_default_num_ctx
        )

    async def test_default_client_is_isolated_from_reasoning_settings(self):
        # A plain client must not inherit the reasoning role's auth/budget.
        s = llm_mod.settings
        s.reasoning_api_key = "sekret"
        s.reasoning_max_tokens = 16384
        recorder = []
        _patch_httpx(self, ['{"x": 3}'], recorder)
        await MlxClient().chat_json("sys", "user", _Out)
        call = recorder[0]
        self.assertIsNone(call["headers"])
        self.assertEqual(call["payload"]["max_tokens"], s.mlx_max_tokens)


class ReasoningConfigCoercionTest(unittest.TestCase):
    def test_empty_env_strings_mean_unset(self):
        # `REASONING_X=` (present but empty) in .env must read as None — an empty
        # REASONING_BACKEND would otherwise fail Literal validation.
        s = Settings(
            _env_file=None,
            reasoning_backend="",
            reasoning_base_url="",
            reasoning_model="",
            reasoning_api_key="",
        )
        self.assertIsNone(s.reasoning_backend)
        self.assertIsNone(s.reasoning_base_url)
        self.assertIsNone(s.reasoning_model)
        self.assertIsNone(s.reasoning_api_key)


if __name__ == "__main__":
    unittest.main()
