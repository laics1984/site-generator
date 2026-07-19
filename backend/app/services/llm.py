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

import hashlib
import json
import logging
import time
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


class EmptyLlmResponse(LlmError):
    """The backend streamed zero content tokens. Distinct from LlmError so the
    caller can retry it: an empty stream is often transient (a cold model load,
    or a hybrid-thinking model that spent a turn's budget on reasoning tokens
    without emitting JSON `content`), so one retry usually recovers instead of
    failing the whole generation with a 502."""

    pass


class TruncatedLlmResponse(LlmError):
    """The backend stopped because it hit its output/context budget mid-generation
    (Ollama `done_reason='length'`, OpenAI-compatible `finish_reason='length'`),
    not because the model chose to stop. Distinct from a generic ValidationError
    so `_validated` can retry with a LARGER budget instead of asking the model to
    "fix" JSON it was structurally never able to finish — a truncated response
    that gets the usual repair prompt just produces a bigger prompt against the
    same budget, and truncates again."""

    pass


class LlmClient(Protocol):
    """The surface every backend exposes (Ollama, MLX). Call sites depend only on
    this, so `get_llm()` can return either backend transparently."""

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        temperature: float | None = None,
        num_ctx: int | None = None,
        images: list[str] | None = None,
        think: bool | None = None,
    ) -> T: ...

    async def list_models(self) -> list[str]: ...


_REPAIR_INSTRUCTION = (
    "Fix every error: replace null values with real strings, add any missing "
    "required fields (especially: every page object MUST have a 'blocks' array "
    "containing the section objects), and correct any wrong 'kind' values. "
    "Reply ONLY with valid JSON matching the schema. No markdown, no commentary."
)


async def _post_nonempty(post: PostChat, payload: dict[str, Any], attempts: int = 2) -> str:
    """POST, retrying up to `attempts` times when the backend streams an empty
    response. An empty stream is usually transient (cold model load, or a turn
    spent on reasoning tokens with no JSON `content`), so one extra try normally
    recovers instead of failing the whole generation. Re-raises the last
    EmptyLlmResponse when every attempt comes back empty."""
    last: EmptyLlmResponse | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await post(payload)
        except EmptyLlmResponse as exc:
            last = exc
            if attempt < attempts:
                logger.warning(
                    "LLM streamed empty content (attempt %d/%d), retrying: %s",
                    attempt, attempts, exc,
                )
    raise last  # type: ignore[misc]


def _boost_budget(payload: dict[str, Any]) -> dict[str, Any]:
    """Double whichever output/context budget field this transport's payload
    carries (Ollama's `options.num_ctx` caps prompt+completion together; MLX/
    OpenAI's `max_tokens` caps the completion alone), capped so a retry can't
    request an unbounded generation. Used only after a genuine truncation
    (see TruncatedLlmResponse) — the same prompt just needed more room.

    The cap is 131 072 because a scaffold batch can legitimately bundle several
    pages' worth of sections into one call (max_pages_per_batch /
    max_sections_per_batch in config.py) — real generations for content-rich
    sites can run past 32K output tokens. Models in use here have context
    windows well beyond that (e.g. Qwen3.5's 262K native), so the cap is a
    circuit breaker against runaway generation, not a model limitation.
    Returns `payload` unchanged (same dict) once the cap is reached, so callers
    can detect "budget can't grow any further" via equality."""
    boosted = dict(payload)
    if isinstance(payload.get("options"), dict) and "num_ctx" in payload["options"]:
        new_num_ctx = min(payload["options"]["num_ctx"] * 2, 131072)
        if new_num_ctx == payload["options"]["num_ctx"]:
            return payload
        boosted["options"] = dict(payload["options"])
        boosted["options"]["num_ctx"] = new_num_ctx
    if "max_tokens" in payload:
        new_max_tokens = min(payload["max_tokens"] * 2, 131072)
        if new_max_tokens == payload["max_tokens"]:
            return payload
        boosted["max_tokens"] = new_max_tokens
    return boosted


async def _retry_with_growing_budget(
    post: PostChat, payload: dict[str, Any], trunc: TruncatedLlmResponse
) -> tuple[str, dict[str, Any]]:
    """Keep doubling the output/context budget (_boost_budget) and retrying
    after a TruncatedLlmResponse, until a response fits or the budget hits its
    hard cap. Raises the last TruncatedLlmResponse once doubling stops changing
    the payload (cap reached) — at that point more retries can't help. Returns
    the response text AND the boosted payload it was won with, so a caller that
    goes on to a repair retry (schema validation failure) uses the same larger
    budget instead of falling back to the original, too-small one."""
    while True:
        boosted = _boost_budget(payload)
        if boosted == payload:
            raise trunc
        payload = boosted
        logger.warning(
            "LLM response was truncated by the token/context budget (%s) — "
            "retrying with a larger budget",
            trunc,
        )
        try:
            return await _post_nonempty(post, payload), payload
        except TruncatedLlmResponse as exc:
            trunc = exc


async def _validated(post: PostChat, payload: dict[str, Any], schema: type[T]) -> T:
    """POST `payload`, validate the reply against `schema`, and on a validation
    error retry ONCE with the errors fed back to the model. An empty stream is
    retried separately (see _post_nonempty) before validation. Transport-agnostic:
    both Ollama and OpenAI/MLX payloads carry a `messages` list, so the repair
    turn is appended the same way for either backend. Raises LlmError if the
    second attempt still fails.

    A response that was cut off by the token/context budget (TruncatedLlmResponse)
    is handled separately from malformed JSON: retrying with the SAME budget would
    just truncate again (and the repair prompt is even longer than the original),
    so that case retries with a doubled budget (see _retry_with_growing_budget)
    instead of a repair message.
    """
    try:
        response_text = await _post_nonempty(post, payload)
    except TruncatedLlmResponse as trunc:
        response_text, payload = await _retry_with_growing_budget(post, payload, trunc)
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
    response_text = await _post_nonempty(post, repair_payload)
    try:
        return schema.model_validate_json(response_text)
    except ValidationError as second_err:
        raise LlmError(
            f"LLM produced invalid JSON after retry: {second_err}"
        ) from second_err


# --- response cache -----------------------------------------------------------
# In-process TTL + LRU cache over VALIDATED chat_json results, used via
# chat_json_cached(). Opt-in per call site: only the deterministic-ish,
# expensive calls go through it (scaffolded content batches, the image
# tie-break judge), which is what makes re-generating an unchanged site
# near-instant. dict insertion order doubles as LRU recency; single event
# loop ⇒ no locking needed. Entries hold a pristine deep copy and hits hand
# out fresh deep copies, because callers mutate results in place (alignment,
# photo enrichment, parent_slug stitching).
_RESPONSE_CACHE: dict[str, tuple[float, BaseModel]] = {}


def _response_cache_key(
    client: LlmClient,
    system_prompt: str,
    user_prompt: str,
    schema: type[BaseModel],
    temperature: float | None,
    num_ctx: int | None,
    think: bool | None,
) -> str:
    """Hash every input that shapes the response — any change is a miss."""
    ident = "\x1f".join(
        (
            type(client).__name__,
            str(getattr(client, "base_url", "")),
            str(getattr(client, "model", "")),
            f"{schema.__module__}.{schema.__qualname__}",
            repr(temperature),
            repr(num_ctx),
            repr(think),
            system_prompt,
            user_prompt,
        )
    )
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()


def _response_cache_get(key: str) -> BaseModel | None:
    entry = _RESPONSE_CACHE.get(key)
    if entry is None:
        return None
    stored_at, cached = entry
    ttl = settings.llm_cache_ttl_seconds
    if ttl <= 0 or time.monotonic() - stored_at >= ttl:
        _RESPONSE_CACHE.pop(key, None)
        return None
    # Re-insert so dict order tracks recency (LRU eviction in _put).
    _RESPONSE_CACHE.pop(key, None)
    _RESPONSE_CACHE[key] = (stored_at, cached)
    return cached.model_copy(deep=True)


def _response_cache_put(key: str, result: BaseModel) -> None:
    _RESPONSE_CACHE.pop(key, None)
    _RESPONSE_CACHE[key] = (time.monotonic(), result.model_copy(deep=True))
    while len(_RESPONSE_CACHE) > settings.llm_cache_max_entries:
        _RESPONSE_CACHE.pop(next(iter(_RESPONSE_CACHE)))


def clear_response_cache() -> None:
    """Drop every cached LLM response (tests / ops)."""
    _RESPONSE_CACHE.clear()


async def chat_json_cached(
    client: LlmClient,
    *,
    system_prompt: str,
    user_prompt: str,
    schema: type[T],
    temperature: float | None = None,
    num_ctx: int | None = None,
    think: bool | None = None,
) -> T:
    """`client.chat_json` behind the opt-in response cache.

    Identical inputs (same backend/model + prompts + sampling knobs) within
    `llm_cache_ttl_seconds` return the previously validated result instead of
    re-hitting the LLM. Kill switch: LLM_CACHE_ENABLED=false. Deliberately has
    no `images` parameter — multimodal payloads are never cached here (the
    vision pass keeps its own URL-keyed cache). Only the kwargs the caller
    actually provided are forwarded, and only REAL backend clients participate
    in caching — test fakes pass through untouched, so fixtures that count
    calls or vary responses keep working.
    """
    key: str | None = None
    if settings.llm_cache_enabled and isinstance(client, (OllamaClient, MlxClient)):
        key = _response_cache_key(
            client, system_prompt, user_prompt, schema, temperature, num_ctx, think
        )
        hit = _response_cache_get(key)
        if hit is not None:
            logger.info(
                "LLM response cache hit for %s — skipping generation", schema.__name__
            )
            return hit  # type: ignore[return-value]

    call_kwargs: dict[str, Any] = {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "schema": schema,
    }
    if temperature is not None:
        call_kwargs["temperature"] = temperature
    if num_ctx is not None:
        call_kwargs["num_ctx"] = num_ctx
    if think is not None:
        call_kwargs["think"] = think
    result = await client.chat_json(**call_kwargs)
    if key is not None:
        _response_cache_put(key, result)
    return result


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
        api_key: str | None = None,
        think_default: bool | None = None,
        num_ctx_default: int | None = None,
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout or settings.ollama_timeout_seconds
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        # Client-level defaults for calls that pass None. None here ⇒ fall back
        # to the global settings at call time (preserves env-driven behavior for
        # the default client); the reasoning role sets its own defaults.
        self._think_default = think_default
        self._num_ctx_default = num_ctx_default

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        temperature: float | None = None,
        num_ctx: int | None = None,
        images: list[str] | None = None,
        think: bool | None = None,
    ) -> T:
        """
        Send a chat request expecting JSON back, then validate against `schema`.

        `temperature`/`num_ctx`/`think` default to the model-variant settings
        (llm_default_temperature / llm_default_num_ctx / llm_think) when a call
        site passes None — resolved per call, so env config (not code) decides.

        `images`: base64-encoded image payloads attached to the user message
        (Ollama's multimodal chat format). Requires a vision-capable model.

        `think`: hybrid-thinking models (Qwen3/3.5) can emit a `<think>...</think>`
        preamble before the JSON body, which breaks `format='json'` parsing.
        Off by default (settings.llm_think) so every caller gets clean JSON;
        non-thinking models (e.g. Qwen 2.5) ignore the unknown field harmlessly.
        """
        temperature = settings.llm_default_temperature if temperature is None else temperature
        if num_ctx is None:
            num_ctx = (
                self._num_ctx_default
                if self._num_ctx_default is not None
                else settings.llm_default_num_ctx
            )
        if think is None:
            think = (
                self._think_default
                if self._think_default is not None
                else settings.llm_think
            )
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
        done_reason: str | None = None
        try:
            async with client.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
                headers=self._headers,
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
                        done_reason = event.get("done_reason")
                        break
        except httpx.HTTPError as exc:
            raise LlmError(f"Ollama request failed [{type(exc).__name__}]: {exc}") from exc

        content = "".join(chunks)
        if not content.strip():
            raise EmptyLlmResponse("Ollama returned empty content (stream produced no tokens)")
        if done_reason == "length":
            # num_ctx was exhausted mid-generation (Ollama has no separate
            # num_predict cap — see config.mlx_max_tokens comment) — the JSON is
            # cut off mid-token, not merely malformed.
            snippet = content[-1000:] if len(content) > 1000 else content
            raise TruncatedLlmResponse(
                f"Ollama response hit the context limit (done_reason='length') "
                f"after {len(content)} chars. End snippet: {snippet!r}"
            )
        return content

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.base_url}/api/tags", headers=self._headers)
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
        api_key: str | None = None,
        max_tokens: int | None = None,
        think_default: bool | None = None,
        repetition_penalty: float | None = None,
    ) -> None:
        self.base_url = (base_url or settings.mlx_base_url).rstrip("/")
        self.model = model or settings.mlx_model
        self.timeout = timeout or settings.mlx_timeout_seconds
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        # Client-level defaults for calls that pass None. None here ⇒ fall back
        # to the global settings at call time (preserves env-driven behavior for
        # the default client); the reasoning role sets its own defaults.
        self._max_tokens = max_tokens
        self._think_default = think_default
        self._repetition_penalty = repetition_penalty
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
        temperature: float | None = None,
        num_ctx: int | None = None,
        images: list[str] | None = None,
        think: bool | None = None,
    ) -> T:
        """OpenAI Chat Completions in JSON mode. Defaults resolve from the same
        model-variant settings as OllamaClient. `num_ctx` is accepted for
        signature parity (the server sizes its own context); thinking output is
        handled defensively by stripping a `<think>` preamble before validation."""
        temperature = settings.llm_default_temperature if temperature is None else temperature
        if think is None:
            think = (
                self._think_default
                if self._think_default is not None
                else settings.llm_think
            )
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
            "max_tokens": self._max_tokens or settings.mlx_max_tokens,
            "response_format": {"type": "json_object"},
            # Hybrid-thinking off by default (settings.llm_think): the server
            # otherwise streams a long `reasoning` preamble in a separate channel
            # that burns the whole token budget before any JSON `content` is
            # produced. Mirrors OllamaClient. Harmlessly ignored by model
            # templates that don't define `enable_thinking`.
            "chat_template_kwargs": {"enable_thinking": think},
            # Stream so the read timeout is per-chunk, not whole-generation (same
            # rationale as OllamaClient — see its chat_json note).
            "stream": True,
        }
        repetition_penalty = (
            self._repetition_penalty
            if self._repetition_penalty is not None
            else settings.mlx_repetition_penalty
        )
        if repetition_penalty:
            # mlx_lm.server-specific extension (not part of the OpenAI schema);
            # 0.0 means "disabled", so only send it when actually set — see
            # config.mlx_repetition_penalty for why this defaults on.
            payload["repetition_penalty"] = repetition_penalty
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
        finish_reason: str | None = None
        try:
            async with client.stream(
                "POST", url, json=payload, timeout=self.timeout, headers=self._headers
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
                        reason = choices[0].get("finish_reason")
                        if reason:
                            finish_reason = reason
        except httpx.HTTPError as exc:
            raise LlmError(f"MLX request failed [{type(exc).__name__}]: {exc}") from exc

        content = _strip_think("".join(chunks))
        if not content.strip():
            raise EmptyLlmResponse("MLX returned empty content (stream produced no tokens)")
        if finish_reason == "length":
            # max_tokens was exhausted mid-generation — the JSON is cut off
            # mid-token, not merely malformed.
            snippet = content[-1000:] if len(content) > 1000 else content
            raise TruncatedLlmResponse(
                f"MLX response hit max_tokens (finish_reason='length') "
                f"after {len(content)} chars. End snippet: {snippet!r}"
            )
        return content

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.base_url}/v1/models", headers=self._headers)
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


def get_reasoning_llm() -> LlmClient:
    """Client for the reasoning/design role: brand detection, the design-brain
    passes, and the image tie-break judge — typically a bigger remote model
    (GLM on the AI server) with thinking enabled. Falls back to the default
    client when REASONING_MODEL is unset, so calling this is always safe."""
    if not settings.reasoning_model:
        return get_llm()
    backend = (settings.reasoning_backend or settings.llm_backend).lower()
    if backend == "mlx":  # any OpenAI-compatible server (vLLM, sglang, mlx_lm.server)
        return MlxClient(
            base_url=settings.reasoning_base_url,
            model=settings.reasoning_model,
            timeout=settings.reasoning_timeout_seconds,
            api_key=settings.reasoning_api_key,
            max_tokens=settings.reasoning_max_tokens,
            think_default=settings.reasoning_think,
        )
    return OllamaClient(
        base_url=settings.reasoning_base_url,
        model=settings.reasoning_model,
        timeout=settings.reasoning_timeout_seconds,
        api_key=settings.reasoning_api_key,
        num_ctx_default=settings.reasoning_num_ctx,
        think_default=settings.reasoning_think,
    )


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
