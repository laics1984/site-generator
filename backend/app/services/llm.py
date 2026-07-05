"""
LLM clients for structured (JSON) generation.

Two interchangeable backends behind a common `LlmClient` interface:

  * `OllamaClient` — Ollama's /api/chat with format='json' (the default everywhere
    Ollama runs, e.g. Windows, or a Mac host running `ollama serve`).
  * `MlxClient` — an OpenAI-compatible MLX server (`mlx_lm.server`) running natively
    on Apple Silicon; Docker can't run MLX, so the container reaches it over HTTP
    exactly like Ollama (see docker-compose.yml).

`resolve_llm_backend()` picks one from `LLM_BACKEND` (auto|mlx|ollama). `get_llm()`
returns the matching client. Both stream their response so httpx's read timeout
applies to the gap BETWEEN tokens, not the whole generation, and both share one
validate-or-repair retry (`_validated`) since a small model with JSON mode is
reliable but not perfect.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# A transport that POSTs a chat payload and returns the assembled response text.
PostChat = Callable[[dict[str, Any]], Awaitable[str]]


class LlmError(Exception):
    pass


class LlmClient(Protocol):
    """The surface every backend exposes (Ollama, MLX). Call sites depend only on
    this, so `get_llm()` can return either backend transparently."""

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        temperature: float = 0.4,
        num_ctx: int = 4096,
        images: list[str] | None = None,
        think: bool = False,
    ) -> T: ...

    async def list_models(self) -> list[str]: ...


_REPAIR_INSTRUCTION = (
    "Fix every error: replace null values with real strings, add any missing "
    "required fields (especially: every page object MUST have a 'blocks' array "
    "containing the section objects), and correct any wrong 'kind' values. "
    "Reply ONLY with valid JSON matching the schema. No markdown, no commentary."
)


async def _validated(post: PostChat, payload: dict[str, Any], schema: type[T]) -> T:
    """POST `payload`, validate the reply against `schema`, and on a validation
    error retry ONCE with the errors fed back to the model. Transport-agnostic:
    both Ollama and OpenAI/MLX payloads carry a `messages` list, so the repair
    turn is appended the same way for either backend. Raises LlmError if the
    second attempt still fails."""
    response_text = await post(payload)
    try:
        return schema.model_validate_json(response_text)
    except ValidationError as exc:
        first_err = exc
        err_summary = "; ".join(
            f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
            for e in exc.errors()[:5]
        )
        logger.warning(
            "LLM returned JSON that failed schema validation, retrying — %s",
            err_summary,
        )

    error_summary = "; ".join(
        f"{'.'.join(str(loc) for loc in e['loc'])}: {e['msg']}"
        for e in first_err.errors()[:10]
    )
    repair_payload = dict(payload)
    repair_payload["messages"] = [
        *payload["messages"],
        {"role": "assistant", "content": response_text},
        {
            "role": "user",
            "content": (
                f"That response failed validation with these errors: {error_summary}. "
                + _REPAIR_INSTRUCTION
            ),
        },
    ]
    response_text = await post(repair_payload)
    try:
        return schema.model_validate_json(response_text)
    except ValidationError as second_err:
        raise LlmError(
            f"LLM produced invalid JSON after retry: {second_err}"
        ) from second_err


def _strip_think(text: str) -> str:
    """Drop a leading `<think>…</think>` preamble (hybrid-thinking models like
    Qwen3 may emit one before the JSON body, which breaks JSON parsing). Only a
    leading block is removed; if the closing tag is missing we cut from the first
    `{`/`[` so the JSON body still parses."""
    stripped = text.lstrip()
    if not stripped.startswith("<think>"):
        return text
    end = stripped.find("</think>")
    if end != -1:
        return stripped[end + len("</think>"):].lstrip()
    for i, ch in enumerate(stripped):
        if ch in "{[":
            return stripped[i:]
    return stripped


# Long-lived AsyncClient shared by every LLM chat call so back-to-back calls
# (content batches, the 12-image vision pass) reuse keep-alive connections
# instead of opening a socket per call. Created with NO default timeout — each
# request passes its own, so the per-backend stream timeouts still apply.
# Rebuilt automatically when httpx.AsyncClient is monkeypatched (tests) or the
# client was closed (app shutdown).
_shared_http: httpx.AsyncClient | None = None
_shared_http_factory: Any = None


def _shared_client() -> httpx.AsyncClient:
    global _shared_http, _shared_http_factory
    factory = httpx.AsyncClient
    if (
        _shared_http is None
        or _shared_http_factory is not factory
        or getattr(_shared_http, "is_closed", False)
    ):
        _shared_http = factory(
            timeout=None, limits=httpx.Limits(max_keepalive_connections=10)
        )
        _shared_http_factory = factory
    return _shared_http


async def aclose_shared_client() -> None:
    """Close the shared LLM http client (called on app shutdown)."""
    global _shared_http
    if _shared_http is not None and not getattr(_shared_http, "is_closed", True):
        await _shared_http.aclose()
    _shared_http = None


class OllamaClient:
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout or settings.ollama_timeout_seconds

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        temperature: float = 0.4,
        num_ctx: int = 4096,
        images: list[str] | None = None,
        think: bool = False,
    ) -> T:
        """
        Send a chat request expecting JSON back, then validate against `schema`.

        num_ctx must be set explicitly — Ollama's default of 2048 is too small
        for most generation tasks. Callers can override for larger payloads.

        `images`: base64-encoded image payloads attached to the user message
        (Ollama's multimodal chat format). Requires a vision-capable model.

        `think`: hybrid-thinking models (Qwen3/3.5) can emit a `<think>...</think>`
        preamble before the JSON body, which breaks `format='json'` parsing.
        Defaults to False so every caller gets clean JSON; non-thinking models
        (e.g. Qwen 2.5) ignore the unknown field harmlessly.
        """
        user_message: dict[str, Any] = {"role": "user", "content": user_prompt}
        if images:
            user_message["images"] = images
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
            "format": "json",
            "think": think,
            # Stream the response so httpx's read timeout applies to the gap
            # BETWEEN tokens, not the whole generation. A long multi-section
            # generation can't ReadTimeout as long as tokens keep flowing — only
            # a genuine stall trips the timeout. _post_chat reassembles the chunks.
            # NB: streaming does NOT cover time-to-first-token (model load +
            # prompt prefill); keep prompts within num_ctx to bound that.
            "stream": True,
            # Keep the model resident between the recipe and generate calls so the
            # second request doesn't cold-load and trip the read timeout.
            "keep_alive": settings.ollama_keep_alive,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }

        client = _shared_client()

        async def post(p: dict[str, Any]) -> str:
            return await self._post_chat(client, p)

        return await _validated(post, payload, schema)

    async def _post_chat(
        self, client: httpx.AsyncClient, payload: dict[str, Any]
    ) -> str:
        """POST to /api/chat and reassemble the streamed response.

        Ollama streams newline-delimited JSON objects, each carrying a slice of
        ``message.content``, terminated by an object with ``done: true``. We
        concatenate the slices into the full JSON string the caller validates.
        Streaming keeps the read timeout per-chunk (see the stream=True note in
        chat_json), so long generations don't ReadTimeout while tokens flow.
        """
        chunks: list[str] = []
        try:
            async with client.stream(
                "POST", f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        # Tolerate any non-JSON keep-alive / blank framing line.
                        continue
                    if event.get("error"):
                        raise LlmError(f"Ollama stream error: {event['error']}")
                    piece = (event.get("message") or {}).get("content")
                    if isinstance(piece, str):
                        chunks.append(piece)
                    if event.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise LlmError(f"Ollama request failed [{type(exc).__name__}]: {exc}") from exc

        content = "".join(chunks)
        if not content.strip():
            raise LlmError("Ollama returned empty content (stream produced no tokens)")
        return content

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
            return [m.get("name", "") for m in data.get("models", [])]


class MlxClient:
    """OpenAI-compatible client for an MLX server (`mlx_lm.server`, and
    `mlx_vlm.server` for vision). Mirrors OllamaClient's surface so it drops into
    `get_llm()` and every existing call site unchanged."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        vision_base_url: str | None = None,
        vision_model: str | None = None,
    ) -> None:
        self.base_url = (base_url or settings.mlx_base_url).rstrip("/")
        self.model = model or settings.mlx_model
        self.timeout = timeout or settings.mlx_timeout_seconds
        # A vision request routes to the vision server when one is configured,
        # else falls back to the text server (a multimodal model may serve both).
        self.vision_base_url = (
            vision_base_url or settings.mlx_vision_base_url or self.base_url
        ).rstrip("/")
        self.vision_model = vision_model or settings.mlx_vision_model

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        temperature: float = 0.4,
        num_ctx: int = 4096,
        images: list[str] | None = None,
        think: bool = False,
    ) -> T:
        """OpenAI Chat Completions in JSON mode. `num_ctx`/`keep_alive`/`think`
        are accepted for signature parity with OllamaClient; thinking output is
        handled defensively by stripping a `<think>` preamble before validation."""
        if images:
            base_url, model = self.vision_base_url, (self.vision_model or self.model)
            content: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
            for b64 in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
                    }
                )
            user_message: dict[str, Any] = {"role": "user", "content": content}
        else:
            base_url, model = self.base_url, self.model
            user_message = {"role": "user", "content": user_prompt}

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
            "temperature": temperature,
            "max_tokens": settings.mlx_max_tokens,
            "response_format": {"type": "json_object"},
            # Disable hybrid-thinking by default (Qwen3): the server otherwise
            # streams a long `reasoning` preamble in a separate channel that burns
            # the whole token budget before any JSON `content` is produced. Off for
            # JSON calls (think=False) mirrors OllamaClient. Harmlessly ignored by
            # model templates that don't define `enable_thinking`.
            "chat_template_kwargs": {"enable_thinking": think},
            # Stream so the read timeout is per-chunk, not whole-generation (same
            # rationale as OllamaClient — see its chat_json note).
            "stream": True,
        }
        url = f"{base_url}/v1/chat/completions"

        client = _shared_client()

        async def post(p: dict[str, Any]) -> str:
            return await self._post_chat(client, url, p)

        return await _validated(post, payload, schema)

    async def _post_chat(
        self, client: httpx.AsyncClient, url: str, payload: dict[str, Any]
    ) -> str:
        """POST to /v1/chat/completions and reassemble the SSE stream.

        OpenAI servers stream `data: {json}` lines whose `choices[0].delta.content`
        carries each token slice, terminated by `data: [DONE]`. A `<think>` preamble
        (Qwen3) is stripped from the assembled text before the caller validates."""
        chunks: list[str] = []
        try:
            async with client.stream(
                "POST", url, json=payload, timeout=self.timeout
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if event.get("error"):
                        raise LlmError(f"MLX stream error: {event['error']}")
                    choices = event.get("choices") or []
                    if choices:
                        piece = (choices[0].get("delta") or {}).get("content")
                        if isinstance(piece, str):
                            chunks.append(piece)
        except httpx.HTTPError as exc:
            raise LlmError(f"MLX request failed [{type(exc).__name__}]: {exc}") from exc

        content = _strip_think("".join(chunks))
        if not content.strip():
            raise LlmError("MLX returned empty content (stream produced no tokens)")
        return content

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.base_url}/v1/models")
            response.raise_for_status()
            data = response.json()
            return [m.get("id", "") for m in data.get("data", [])]


def resolve_llm_backend() -> str:
    """The configured LLM backend ('mlx' | 'ollama'). Set via LLM_BACKEND in .env;
    there is no auto-detection — the choice is explicit so the same machine can run
    either backend without code changes."""
    return settings.llm_backend.lower()


def get_llm(model: str | None = None) -> LlmClient:
    """Return the active backend's client. `model` overrides the backend's default
    model (used by the opt-in vision pass)."""
    if resolve_llm_backend() == "mlx":
        return MlxClient(model=model)
    return OllamaClient(model=model)


def active_vision_model() -> str | None:
    """The multimodal model for the opt-in vision pass on the active backend
    (mlx_vision_model under MLX, ollama_vision_model under Ollama). None ⇒ the
    vision pass is skipped."""
    if resolve_llm_backend() == "mlx":
        return settings.mlx_vision_model
    return settings.ollama_vision_model


def extract_json_block(text: str) -> dict[str, Any]:
    """
    Defensive fallback for models that wrap JSON in markdown fences despite
    format='json'. Not used in the happy path but handy for debugging.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rsplit("```", 1)[0]
    return json.loads(stripped)
