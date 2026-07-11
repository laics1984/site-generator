"""LLM backend selection (resolve_llm_backend / get_llm) and the MLX OpenAI client."""

import json
import unittest

from pydantic import BaseModel

from app.services import llm as llm_mod
from app.services.llm import (
    EmptyLlmResponse,
    LlmError,
    MlxClient,
    OllamaClient,
    get_llm,
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
        self._recorder.append({"url": url, "payload": json})
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


if __name__ == "__main__":
    unittest.main()
