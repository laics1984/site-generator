"""The Claude API provider: AnthropicClient, the per-request model choice, the
middleware that carries it, /api/llm/models and /health/llm.

Every test injects a fake SDK client, so nothing here reaches the network or
needs a key (conftest._offline_claude drops the real one). The fake records the
exact keyword arguments each `messages.stream` call received, which is what the
payload assertions read — the request shape is the contract with the API.
"""

from __future__ import annotations

import asyncio
import socket
import unittest
from types import SimpleNamespace
from unittest import mock

import anthropic
import httpx2
from pydantic import BaseModel

from fastapi.testclient import TestClient

from app.config import settings
from app.routers import health, llm_models
from app.services import llm as llm_mod
from app.services import llm_choice
from app.services.llm_choice import CONTENT_HEADER, REASONING_HEADER, LlmChoice
from app.services.llm import (
    AnthropicClient,
    EmptyLlmResponse,
    LlmError,
    OpenAIClient,
    chat_json_cached,
    clear_response_cache,
    endpoint_failure_hint,
    get_llm,
    get_reasoning_llm,
)


class _Out(BaseModel):
    x: int


_REQUEST = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")


def _status_error(cls, status: int, message: str = "rejected"):
    return cls(message, response=httpx2.Response(status, request=_REQUEST), body=None)


def _reply(text: str, stop_reason: str = "end_turn", stop_details=None):
    return SimpleNamespace(
        model="claude-opus-5",
        stop_reason=stop_reason,
        stop_details=stop_details,
        # A thinking block first, as adaptive thinking returns: only text counts.
        content=[
            SimpleNamespace(type="thinking", thinking=""),
            SimpleNamespace(type="text", text=text),
        ],
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        ),
    )


class _FakeStream:
    def __init__(self, message):
        self._message = message

    async def get_final_message(self):
        return self._message


class _FakeManager:
    def __init__(self, outcome):
        self._outcome = outcome

    async def __aenter__(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return _FakeStream(self._outcome)

    async def __aexit__(self, *exc):
        return False


class _FakeMessages:
    def __init__(self, sdk: "_FakeSdk", beta: bool):
        self._sdk = sdk
        self._beta = beta

    def stream(self, **kwargs):
        self._sdk.calls.append({"beta": self._beta, **kwargs})
        return _FakeManager(self._sdk.outcomes.pop(0))


class _FakeModels:
    def __init__(self, sdk: "_FakeSdk"):
        self._sdk = sdk

    async def retrieve(self, model_id, **kwargs):
        if self._sdk.probe_error is not None:
            raise self._sdk.probe_error
        return SimpleNamespace(id=model_id, display_name=model_id)


class _FakeSdk:
    """Stands in for anthropic.AsyncAnthropic."""

    def __init__(self, outcomes=(), probe_error=None):
        self.calls: list[dict] = []
        self.outcomes = list(outcomes)
        self.probe_error = probe_error
        self.messages = _FakeMessages(self, beta=False)
        self.beta = SimpleNamespace(messages=_FakeMessages(self, beta=True))
        self.models = _FakeModels(self)


def _client(outcomes, **kwargs) -> tuple[AnthropicClient, _FakeSdk]:
    sdk = _FakeSdk(outcomes)
    return AnthropicClient(sdk_client=sdk, **kwargs), sdk


class RequestShapeTest(unittest.IsolatedAsyncioTestCase):
    async def test_structured_output_effort_and_cached_system_prompt(self):
        client, sdk = _client([_reply('{"x": 1}')])
        out = await client.chat_json("sys prompt", "user prompt", _Out, temperature=0.25)
        self.assertEqual(out.x, 1)
        call = sdk.calls[0]
        self.assertEqual(call["model"], "claude-opus-5")
        self.assertEqual(
            call["system"],
            [{"type": "text", "text": "sys prompt", "cache_control": {"type": "ephemeral"}}],
        )
        self.assertEqual(call["messages"], [{"role": "user", "content": "user prompt"}])
        self.assertEqual(call["output_config"]["effort"], settings.anthropic_effort)
        self.assertEqual(call["output_config"]["format"]["type"], "json_schema")
        self.assertEqual(
            call["output_config"]["format"]["schema"], anthropic.transform_schema(_Out)
        )

    async def test_no_sampling_or_thinking_parameters_are_sent(self):
        # Current Claude models reject sampling knobs and think adaptively.
        client, sdk = _client([_reply('{"x": 1}')])
        await client.chat_json("s", "u", _Out, temperature=0.0, think=False)
        for forbidden in ("temperature", "top_p", "top_k", "thinking"):
            self.assertNotIn(forbidden, sdk.calls[0])

    async def test_think_selects_the_reasoning_effort(self):
        client, sdk = _client([_reply('{"x": 1}')], effort="low", think_effort="xhigh")
        await client.chat_json("s", "u", _Out, think=True)
        self.assertEqual(sdk.calls[0]["output_config"]["effort"], "xhigh")

    async def test_prompt_cache_can_be_switched_off(self):
        client, sdk = _client([_reply('{"x": 1}')])
        with mock.patch.object(settings, "anthropic_prompt_cache", False):
            await client.chat_json("s", "u", _Out)
        self.assertEqual(sdk.calls[0]["system"], [{"type": "text", "text": "s"}])

    async def test_refusal_fallbacks_ride_the_beta_endpoint_by_default(self):
        client, sdk = _client([_reply('{"x": 1}'), _reply('{"x": 2}')])
        await client.chat_json("s", "u", _Out)
        self.assertTrue(sdk.calls[0]["beta"])
        self.assertEqual(sdk.calls[0]["betas"], ["server-side-fallback-2026-07-01"])
        self.assertEqual(sdk.calls[0]["fallbacks"], "default")

        with mock.patch.object(settings, "anthropic_fallbacks_enabled", False):
            await client.chat_json("s", "u", _Out)
        self.assertFalse(sdk.calls[1]["beta"])
        self.assertNotIn("fallbacks", sdk.calls[1])
        self.assertNotIn("betas", sdk.calls[1])

    async def test_images_are_base64_jpeg_blocks_before_the_text(self):
        client, sdk = _client([_reply('{"x": 1}')])
        await client.chat_json("s", "describe", _Out, images=["AAAA", "BBBB"])
        content = sdk.calls[0]["messages"][0]["content"]
        self.assertEqual(
            content,
            [
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "AAAA"}},
                {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "BBBB"}},
                {"type": "text", "text": "describe"},
            ],
        )

    async def test_max_tokens_never_exceeds_the_api_output_ceiling(self):
        client, sdk = _client([_reply('{"x": 1}')], max_tokens=500_000)
        await client.chat_json("s", "u", _Out)
        self.assertEqual(sdk.calls[0]["max_tokens"], 128_000)

    async def test_a_stray_code_fence_is_stripped(self):
        client, _ = _client([_reply('```json\n{"x": 7}\n```')])
        self.assertEqual((await client.chat_json("s", "u", _Out)).x, 7)


class StopReasonTest(unittest.IsolatedAsyncioTestCase):
    async def test_max_tokens_doubles_the_budget_and_retries(self):
        client, sdk = _client(
            [_reply('{"x": ', stop_reason="max_tokens"), _reply('{"x": 4}')],
            max_tokens=16_000,
        )
        self.assertEqual((await client.chat_json("s", "u", _Out)).x, 4)
        self.assertEqual([c["max_tokens"] for c in sdk.calls], [16_000, 32_000])

    async def test_thinking_that_spends_the_whole_budget_is_a_truncation_not_empty(self):
        client, sdk = _client(
            [_reply("", stop_reason="max_tokens"), _reply('{"x": 5}')], max_tokens=8_000
        )
        self.assertEqual((await client.chat_json("s", "u", _Out)).x, 5)
        self.assertEqual(sdk.calls[1]["max_tokens"], 16_000)

    async def test_budget_growth_stops_at_the_api_ceiling(self):
        outcomes = [_reply("{", stop_reason="max_tokens") for _ in range(4)]
        client, sdk = _client(outcomes, max_tokens=64_000)
        with self.assertRaises(llm_mod.TruncatedLlmResponse):
            await client.chat_json("s", "u", _Out)
        self.assertEqual([c["max_tokens"] for c in sdk.calls], [64_000, 128_000])

    async def test_refusal_is_an_llm_error_carrying_the_category(self):
        details = SimpleNamespace(category="cyber", explanation="declined")
        client, sdk = _client([_reply("", stop_reason="refusal", stop_details=details)])
        with self.assertRaisesRegex(LlmError, "cyber"):
            await client.chat_json("s", "u", _Out)
        self.assertEqual(len(sdk.calls), 1)  # a refusal is not retried

    async def test_empty_text_is_retried_once(self):
        client, sdk = _client([_reply(""), _reply('{"x": 6}')])
        self.assertEqual((await client.chat_json("s", "u", _Out)).x, 6)
        self.assertEqual(len(sdk.calls), 2)

    async def test_empty_twice_raises(self):
        client, _ = _client([_reply(""), _reply("")])
        with self.assertRaises(EmptyLlmResponse):
            await client.chat_json("s", "u", _Out)


class RepairTurnTest(unittest.IsolatedAsyncioTestCase):
    async def test_repair_quotes_the_reply_in_a_user_turn_never_an_assistant_turn(self):
        client, sdk = _client([_reply('{"x": "not a number"}'), _reply('{"x": 9}')])
        self.assertEqual((await client.chat_json("s", "u", _Out)).x, 9)
        repair_messages = sdk.calls[1]["messages"]
        self.assertEqual([m["role"] for m in repair_messages], ["user", "user"])
        self.assertIn('{"x": "not a number"}', repair_messages[1]["content"])
        self.assertIn("failed validation", repair_messages[1]["content"])

    async def test_still_invalid_after_repair_raises(self):
        client, _ = _client([_reply('{"x": "a"}'), _reply('{"x": "b"}')])
        with self.assertRaisesRegex(LlmError, "invalid JSON after retry"):
            await client.chat_json("s", "u", _Out)


class SchemaRejectionTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_rejected_schema_is_retried_as_an_instruction_and_remembered(self):
        rejection = _status_error(anthropic.BadRequestError, 400, "schema too complex")
        client, sdk = _client([rejection, _reply('{"x": 1}'), _reply('{"x": 2}')])

        self.assertEqual((await client.chat_json("s", "u", _Out)).x, 1)
        self.assertIn("format", sdk.calls[0]["output_config"])
        self.assertNotIn("format", sdk.calls[1]["output_config"])
        self.assertIn("JSON Schema", sdk.calls[1]["system"][-1]["text"])

        # The next call skips the doomed structured-output attempt entirely.
        self.assertEqual((await client.chat_json("s", "u", _Out)).x, 2)
        self.assertNotIn("format", sdk.calls[2]["output_config"])
        self.assertEqual(len(sdk.calls), 3)

    async def test_a_400_that_survives_dropping_the_format_is_not_blamed_on_the_schema(self):
        first = _status_error(anthropic.BadRequestError, 400, "retention")
        second = _status_error(anthropic.BadRequestError, 400, "retention")
        client, _ = _client([first, second])
        with self.assertRaisesRegex(LlmError, "BadRequestError"):
            await client.chat_json("s", "u", _Out)
        self.assertEqual(llm_mod._UNCONSTRAINED_SCHEMAS, {})

    async def test_other_api_errors_are_not_retried_without_the_format(self):
        client, sdk = _client([_status_error(anthropic.AuthenticationError, 401)])
        with self.assertRaisesRegex(LlmError, "ANTHROPIC_API_KEY"):
            await client.chat_json("s", "u", _Out)
        self.assertEqual(len(sdk.calls), 1)


class MissingSetupTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_key_is_a_clear_llm_error_before_any_request(self):
        # conftest has dropped the key; no fake SDK is injected here.
        with self.assertRaisesRegex(LlmError, "ANTHROPIC_API_KEY is not set"):
            await AnthropicClient().chat_json("s", "u", _Out)

    async def test_a_stale_image_without_the_package_says_how_to_fix_it(self):
        with mock.patch.object(llm_mod, "anthropic", None):
            with self.assertRaisesRegex(LlmError, "docker compose build backend"):
                await AnthropicClient().chat_json("s", "u", _Out)


class RoutingTest(unittest.TestCase):
    """The request's model choice picks each role's client; no choice is local."""

    def _route(self, content, reasoning):
        token = llm_choice.use(LlmChoice(content=content, reasoning=reasoning))
        try:
            return get_llm(), get_reasoning_llm()
        finally:
            llm_choice.reset(token)

    def test_local_outside_any_request(self):
        self.assertIsInstance(get_llm(), OpenAIClient)
        self.assertIsInstance(get_reasoning_llm(), OpenAIClient)

    def test_all_claude(self):
        content, reasoning = self._route("claude-opus-5", "claude-fable-5-1")
        self.assertIsInstance(content, AnthropicClient)
        self.assertEqual(content.model, "claude-opus-5")
        self.assertIsInstance(reasoning, AnthropicClient)
        self.assertEqual(reasoning.model, "claude-fable-5-1")

    def test_content_local_and_reasoning_on_claude(self):
        content, reasoning = self._route("local", "claude-fable-5-1")
        self.assertIsInstance(content, OpenAIClient)
        self.assertIsInstance(reasoning, AnthropicClient)

    def test_content_on_claude_keeps_local_reasoning_local(self):
        # A local reasoning choice must not follow the content role to Claude.
        content, reasoning = self._route("claude-opus-5", "local")
        self.assertIsInstance(content, AnthropicClient)
        self.assertIsInstance(reasoning, OpenAIClient)

    def test_reasoning_role_carries_its_own_effort(self):
        with mock.patch.object(settings, "anthropic_reasoning_effort", "max"):
            content, reasoning = self._route("claude-opus-5", "claude-opus-5")
        self.assertEqual(reasoning._effort, "max")
        self.assertEqual(content._effort, settings.anthropic_effort)

    def test_claude_budget_ignores_the_local_models_budgets(self):
        # The picker can switch roles between local and Claude per request, so
        # a budget sized for the local model must never size a Claude call.
        with mock.patch.multiple(
            settings, llm_max_tokens=4_096, reasoning_max_tokens=4_096, anthropic_max_tokens=48_000
        ):
            content, reasoning = self._route("claude-opus-5", "claude-fable-5-1")
        self.assertEqual((content._max_tokens, reasoning._max_tokens), (48_000, 48_000))

    def test_fallbacks_follow_the_catalogue(self):
        content, reasoning = self._route("claude-sonnet-5", "claude-fable-5-1")
        self.assertFalse(content._fallbacks)
        self.assertTrue(reasoning._fallbacks)

    def test_a_model_pin_is_the_local_vision_pass_whatever_the_choice(self):
        token = llm_choice.use(LlmChoice(content="claude-opus-5"))
        try:
            client = get_llm(model="qwen2.5vl:7b")
        finally:
            llm_choice.reset(token)
        self.assertIsInstance(client, OpenAIClient)
        self.assertEqual(client.model, "qwen2.5vl:7b")

    def test_tasks_spawned_inside_a_request_inherit_the_choice(self):
        async def client_type():
            return type(get_llm()).__name__

        async def request():
            llm_choice.use(LlmChoice(content="claude-opus-5"))
            return await asyncio.create_task(client_type())

        self.assertEqual(asyncio.run(request()), "AnthropicClient")


class SonnetFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_model_without_fallbacks_uses_the_plain_endpoint(self):
        client, sdk = _client([_reply('{"x": 1}')], model="claude-sonnet-5", fallbacks=False)
        await client.chat_json("s", "u", _Out)
        self.assertFalse(sdk.calls[0]["beta"])
        self.assertNotIn("fallbacks", sdk.calls[0])


class ResponseCacheTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        clear_response_cache()

    def tearDown(self):
        clear_response_cache()

    async def test_claude_responses_are_cached_like_local_ones(self):
        client, sdk = _client([_reply('{"x": 1}')])
        first = await chat_json_cached(client, system_prompt="s", user_prompt="u", schema=_Out)
        second = await chat_json_cached(client, system_prompt="s", user_prompt="u", schema=_Out)
        self.assertEqual((first.x, second.x), (1, 1))
        self.assertEqual(len(sdk.calls), 1)

    async def test_a_different_model_is_a_different_entry(self):
        opus, opus_sdk = _client([_reply('{"x": 1}')], model="claude-opus-5")
        fable, fable_sdk = _client([_reply('{"x": 2}')], model="claude-fable-5-1")
        await chat_json_cached(opus, system_prompt="s", user_prompt="u", schema=_Out)
        out = await chat_json_cached(fable, system_prompt="s", user_prompt="u", schema=_Out)
        self.assertEqual(out.x, 2)
        self.assertEqual((len(opus_sdk.calls), len(fable_sdk.calls)), (1, 1))


class FailureHintTest(unittest.TestCase):
    """Claude API failures get their own remedy, by SDK exception type."""

    def _hint(self, exc):
        return endpoint_failure_hint(exc, llm_mod.ANTHROPIC_API_ROOT)

    def test_each_status_names_its_remedy(self):
        cases = [
            (anthropic.AuthenticationError, 401, "ANTHROPIC_API_KEY"),
            (anthropic.PermissionDeniedError, 403, "HTTP 403"),
            (anthropic.NotFoundError, 404, "model menu"),
            (anthropic.RateLimitError, 429, "SCAFFOLD_BATCH_CONCURRENCY"),
            (anthropic.BadRequestError, 400, "30-day"),
            (anthropic.InternalServerError, 500, "HTTP 500"),
        ]
        for cls, status, needle in cases:
            with self.subTest(cls=cls.__name__):
                self.assertIn(needle, self._hint(_status_error(cls, status)))

    def test_timeout_and_connection_failures(self):
        self.assertIn("LLM_TIMEOUT_SECONDS", self._hint(anthropic.APITimeoutError(request=_REQUEST)))
        dns = anthropic.APIConnectionError(request=_REQUEST)
        dns.__cause__ = socket.gaierror(8, "nodename nor servname provided")
        self.assertIn("does not resolve", self._hint(dns))
        self.assertIn("outbound HTTPS", self._hint(anthropic.APIConnectionError(request=_REQUEST)))

    def test_local_failures_keep_their_own_hints(self):
        import httpx

        refused = httpx.ConnectError("boom")
        refused.__cause__ = ConnectionRefusedError()
        self.assertIn("./ai.sh up", endpoint_failure_hint(refused, "http://localhost:11434"))


class HealthTest(unittest.TestCase):
    def _health(self, sdk: _FakeSdk | None, choice: LlmChoice):
        real = AnthropicClient

        def fake_client(**kwargs):
            return real(sdk_client=sdk, **kwargs)

        async def run():
            token = llm_choice.use(choice)
            try:
                return await health.health_llm()
            finally:
                llm_choice.reset(token)

        # The local server is never probed from a test.
        with mock.patch.object(health, "_local_status", _fake_local_status):
            if sdk is None:
                return asyncio.run(run())
            with mock.patch.object(health, "AnthropicClient", fake_client):
                return asyncio.run(run())

    def test_claude_choices_report_ok_in_the_frontend_shape(self):
        sdk = _FakeSdk()
        result = self._health(sdk, LlmChoice("claude-opus-5", "claude-fable-5-1"))
        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(result["choice"], "claude-opus-5")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["model"], "claude-opus-5")
        self.assertEqual(result["models"], ["claude-opus-5"])
        self.assertEqual(result["reasoning"]["provider"], "anthropic")
        self.assertEqual(result["reasoning"]["model"], "claude-fable-5-1")
        self.assertNotIn("api_key", str(result))

    def test_one_model_for_both_roles_is_probed_once(self):
        calls = []
        sdk = _FakeSdk()
        original = sdk.models.retrieve

        async def counting(model_id, **kwargs):
            calls.append(model_id)
            return await original(model_id, **kwargs)

        sdk.models.retrieve = counting
        self._health(sdk, LlmChoice("claude-opus-5", "claude-opus-5"))
        self.assertEqual(calls, ["claude-opus-5"])

    def test_claude_content_with_local_reasoning_reports_the_local_server(self):
        result = self._health(_FakeSdk(), LlmChoice("claude-opus-5", "local"))
        self.assertEqual(result["provider"], "anthropic")
        self.assertEqual(result["reasoning"]["provider"], "local")
        self.assertEqual(result["reasoning"]["model"], "qwen3")

    def test_a_rejected_key_is_unreachable_with_its_hint(self):
        sdk = _FakeSdk(probe_error=_status_error(anthropic.AuthenticationError, 401))
        result = self._health(sdk, LlmChoice("claude-opus-5", "local"))
        self.assertEqual(result["status"], "unreachable")
        self.assertIn("ANTHROPIC_API_KEY", result["hint"])

    def test_missing_key_is_unconfigured_not_unreachable(self):
        result = self._health(None, LlmChoice("claude-opus-5", "local"))
        self.assertEqual(result["status"], "unconfigured")
        self.assertIn("ANTHROPIC_API_KEY is not set", result["hint"])


class ChoiceMiddlewareTest(unittest.TestCase):
    """The picker's headers become the request's choice; junk is refused."""

    def setUp(self):
        from app.main import app

        self.client = TestClient(app)

    def test_headers_reach_health_as_the_choice(self):
        with mock.patch.object(health, "_claude_status", _fake_claude_status):
            response = self.client.get(
                "/health/llm",
                headers={CONTENT_HEADER: "claude-opus-5", REASONING_HEADER: "claude-fable-5-1"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["choice"], "claude-opus-5")
        self.assertEqual(body["reasoning"]["choice"], "claude-fable-5-1")

    def test_no_headers_is_local_for_both_roles(self):
        with mock.patch.object(health, "_local_status", _fake_local_status):
            body = self.client.get("/health/llm").json()
        self.assertEqual(body["provider"], "local")
        self.assertEqual(body["reasoning"]["provider"], "local")
        self.assertEqual(body["reasoning"]["status"], "ok")

    def test_an_unknown_model_is_a_400_naming_the_choices(self):
        response = self.client.get("/health/llm", headers={CONTENT_HEADER: "gpt-5"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("gpt-5", response.json()["detail"])
        self.assertIn("claude-opus-5", response.json()["detail"])

    def test_the_choice_lives_exactly_as_long_as_the_request(self):
        # Driven in one event loop (TestClient runs the app on another thread,
        # where a leak could never be seen from here).
        seen = []

        async def app(scope, receive, send):
            seen.append(llm_choice.current())

        async def run():
            middleware = llm_choice.LlmChoiceMiddleware(app)
            scope = {
                "type": "http",
                "headers": [(CONTENT_HEADER.encode(), b"claude-opus-5")],
            }
            await middleware(scope, None, None)
            return llm_choice.current()

        after = asyncio.run(run())
        self.assertEqual(seen, [LlmChoice(content="claude-opus-5")])
        self.assertEqual(after, LlmChoice())

    def test_cors_allows_the_picker_headers(self):
        from app.main import app

        cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
        allowed = {h.lower() for h in cors.kwargs["allow_headers"]}
        self.assertTrue({CONTENT_HEADER, REASONING_HEADER} <= allowed)


async def _fake_claude_status(model):
    return {"model": model, "models": [model], "status": "ok"}


async def _fake_local_status(base_url, pinned):
    return {"model": "qwen3", "models": ["qwen3"], "status": "ok"}


class ModelsEndpointTest(unittest.TestCase):
    def _models(self):
        return asyncio.run(llm_models.llm_models())

    def test_local_is_always_available_and_the_default(self):
        body = self._models()
        local = body["choices"][0]
        self.assertEqual((local["id"], local["available"]), ("local", True))
        self.assertEqual(body["default"], {"content": "local", "reasoning": "local"})

    def test_claude_models_need_the_key(self):
        body = self._models()  # conftest dropped the key
        claude = [c for c in body["choices"] if c["provider"] == "anthropic"]
        self.assertEqual(
            [c["id"] for c in claude], [m.id for m in llm_choice.CLAUDE_MODELS]
        )
        self.assertTrue(all(not c["available"] and "ANTHROPIC_API_KEY" in c["hint"] for c in claude))

        with mock.patch.object(settings, "anthropic_api_key", "sk-test"):
            body = self._models()
        claude = [c for c in body["choices"] if c["provider"] == "anthropic"]
        self.assertTrue(all(c["available"] and "hint" not in c for c in claude))
        self.assertNotIn("sk-test", str(body))


class BrandCacheKeyTest(unittest.IsolatedAsyncioTestCase):
    async def test_a_different_reasoning_model_re_detects_the_brand(self):
        from app.models.content_blocks import SourceContent
        from app.services import planner

        seen = []

        async def fake_detect(source, llm=None):
            seen.append(llm_choice.current().reasoning)
            return mock.sentinel.brand

        source = SourceContent(source_kind="url", source_ref="https://brand-cache.example", raw_text="x")
        planner._DETECT_BRAND_CACHE.clear()
        try:
            with mock.patch.object(planner, "detect_brand", fake_detect):
                await planner.detect_brand_cached(source)
                await planner.detect_brand_cached(source)
                token = llm_choice.use(LlmChoice(reasoning="claude-fable-5-1"))
                try:
                    await planner.detect_brand_cached(source)
                finally:
                    llm_choice.reset(token)
        finally:
            planner._DETECT_BRAND_CACHE.clear()
        self.assertEqual(seen, ["local", "claude-fable-5-1"])


if __name__ == "__main__":
    unittest.main()
