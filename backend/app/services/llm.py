"""
LLM client for structured (JSON) generation.

ONE client, `OpenAIClient`, speaking the OpenAI wire (/v1/chat/completions and
/v1/models). That is deliberate: every engine the ai-server can run — Ollama,
llama.cpp's llama-server, mlx_lm.server on Apple Silicon, LM Studio, vLLM —
serves that same API, so the backend needs no notion of which one is behind the
URL. Engine, model, quantization, context length, keep-alive and GPU offload all
live in ai-server/.env; this module only knows `settings.llm_base_url`.

The model id is not configured either — `resolve_model()` reads it from
/v1/models, so swapping the model in ai-server/.env takes effect here without an
edit or a restart. `settings.llm_model` exists only to pin one when a server
advertises several.

Responses are streamed so httpx's read timeout applies to the gap BETWEEN
tokens rather than the whole generation, and every call goes through one
validate-or-repair retry (`_validated`) since a local model in JSON mode is
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
    """The surface call sites depend on. There is one real implementation
    (`OpenAIClient`); the Protocol stays so tests can substitute fakes.

    Note there is no `num_ctx`: the OpenAI wire has no per-request context
    parameter — context size is a server setting (LLM_CTX in ai-server/.env).
    Callers that need to *size* their prompts read `settings.llm_context_tokens`
    instead (see planner._plan_batches)."""

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        temperature: float | None = None,
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
    """Double the payload's `max_tokens` (the OpenAI completion budget), capped
    so a retry can't request an unbounded generation. Used only after a genuine
    truncation (see TruncatedLlmResponse) — the same prompt just needed more room.

    Only the OUTPUT budget can grow from here: the server's context window is
    fixed at startup (LLM_CTX in ai-server/.env), so a prompt that overflows it
    needs a smaller batch (planner._plan_batches) or a bigger LLM_CTX, not a
    retry.

    The cap is 131 072 because a scaffold batch can legitimately bundle several
    pages' worth of sections into one call (max_pages_per_batch /
    max_sections_per_batch in config.py) — real generations for content-rich
    sites can run past 32K output tokens. Models in use here have context
    windows well beyond that (e.g. Qwen3.5's 262K native), so the cap is a
    circuit breaker against runaway generation, not a model limitation.
    Returns `payload` unchanged (same dict) once the cap is reached, so callers
    can detect "budget can't grow any further" via equality."""
    boosted = dict(payload)
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
    retried separately (see _post_nonempty) before validation. Raises LlmError if
    the second attempt still fails.

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
    model: str,
    system_prompt: str,
    user_prompt: str,
    schema: type[BaseModel],
    temperature: float | None,
    think: bool | None,
) -> str:
    """Hash every input that shapes the response — any change is a miss.

    `model` is the RESOLVED id (not settings.llm_model, which is normally None),
    so swapping the model on the ai-server invalidates these entries instead of
    serving content the previous model wrote."""
    ident = "\x1f".join(
        (
            type(client).__name__,
            str(getattr(client, "base_url", "")),
            model,
            f"{schema.__module__}.{schema.__qualname__}",
            repr(temperature),
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
    think: bool | None = None,
) -> T:
    """`client.chat_json` behind the opt-in response cache.

    Identical inputs (same endpoint/model + prompts + sampling knobs) within
    `llm_cache_ttl_seconds` return the previously validated result instead of
    re-hitting the LLM. Kill switch: LLM_CACHE_ENABLED=false. Deliberately has
    no `images` parameter — multimodal payloads are never cached here (the
    vision pass keeps its own URL-keyed cache). Only the kwargs the caller
    actually provided are forwarded, and only the REAL client participates in
    caching — test fakes pass through untouched, so fixtures that count calls
    or vary responses keep working.
    """
    key: str | None = None
    if settings.llm_cache_enabled and isinstance(client, OpenAIClient):
        key = _response_cache_key(
            client,
            await client.resolve_model(),
            system_prompt,
            user_prompt,
            schema,
            temperature,
            think,
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


# --- model discovery ----------------------------------------------------------
# The backend stores no model name — it asks the server what it serves. Cached
# with a SHORT ttl rather than forever: long enough that a generation's burst of
# calls doesn't re-probe /v1/models each time, short enough that swapping the
# model in ai-server/.env takes effect without restarting the backend.
_MODEL_DISCOVERY_TTL_SECONDS = 60.0
_MODEL_CACHE: dict[str, tuple[float, str]] = {}


def clear_model_cache() -> None:
    """Forget discovered model ids (tests, or to pick a swap up immediately)."""
    _MODEL_CACHE.clear()


async def _discover_model(base_url: str, headers: dict[str, str] | None) -> str:
    """The id this server advertises on /v1/models."""
    entry = _MODEL_CACHE.get(base_url)
    if entry is not None and time.monotonic() - entry[0] < _MODEL_DISCOVERY_TTL_SECONDS:
        return entry[1]
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{base_url}/v1/models", headers=headers)
            response.raise_for_status()
            ids = sorted(
                m.get("id", "") for m in (response.json().get("data") or [])
            )
    except httpx.HTTPError as exc:
        raise LlmError(
            f"Could not reach the AI server at {base_url} to discover a model "
            f"[{type(exc).__name__}]: {exc}. Check it is running — "
            "ai-server/: ./ai.sh status"
        ) from exc
    ids = [i for i in ids if i]
    if not ids:
        raise LlmError(
            f"The AI server at {base_url} advertises no models on /v1/models. "
            "Load one (ai-server/: ./ai.sh pull) or set LLM_MODEL to pin an id."
        )
    if len(ids) > 1:
        # Sorted above so this stays deterministic rather than depending on the
        # server's listing order.
        logger.warning(
            "AI server at %s advertises %d models %s — using %r. Set LLM_MODEL "
            "(or REASONING_MODEL) to pin one.",
            base_url, len(ids), ids, ids[0],
        )
    _MODEL_CACHE[base_url] = (time.monotonic(), ids[0])
    logger.info("Using LLM model %r discovered at %s", ids[0], base_url)
    return ids[0]


class OpenAIClient:
    """Client for any OpenAI-compatible server — which is every engine the
    ai-server can run (Ollama, llama-server, mlx_lm.server, LM Studio, vLLM).

    `model` is normally None: the id is discovered from /v1/models on first use
    (see resolve_model), so the backend carries no model configuration at all."""

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
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        # None ⇒ resolve_model() discovers it from the server.
        self.model = model or settings.llm_model
        self.timeout = timeout or settings.llm_timeout_seconds
        key = api_key or settings.llm_api_key
        self._headers = {"Authorization": f"Bearer {key}"} if key else None
        # Client-level defaults for calls that pass None. None here ⇒ fall back
        # to the global settings at call time (preserves env-driven behavior for
        # the default client); the reasoning role sets its own defaults.
        self._max_tokens = max_tokens
        self._think_default = think_default
        self._repetition_penalty = repetition_penalty
        # A vision request routes to the vision endpoint when one is configured,
        # else falls back to the text one (a multimodal model may serve both).
        self.vision_base_url = (
            vision_base_url or settings.llm_vision_base_url or self.base_url
        ).rstrip("/")
        self.vision_model = vision_model or settings.llm_vision_model

    async def resolve_model(self) -> str:
        """The model id to send. Configured value if pinned, else whatever the
        server advertises on /v1/models — cached briefly so a model swap on the
        ai-server is picked up without restarting the backend."""
        if self.model:
            return self.model
        return await _discover_model(self.base_url, self._headers)

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        temperature: float | None = None,
        images: list[str] | None = None,
        think: bool | None = None,
    ) -> T:
        """OpenAI Chat Completions in JSON mode. There is no `num_ctx`: the
        server owns its context window (LLM_CTX in ai-server/.env). Thinking
        output is handled defensively by stripping a `<think>` preamble before
        validation."""
        temperature = settings.llm_default_temperature if temperature is None else temperature
        if think is None:
            think = (
                self._think_default
                if self._think_default is not None
                else settings.llm_think
            )
        if images:
            # Only fall back to discovery when no vision model is pinned — a
            # vision endpoint may not be the one that answers /v1/models.
            base_url = self.vision_base_url
            model = self.vision_model or await self.resolve_model()
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
            base_url = self.base_url
            model = await self.resolve_model()
            user_message = {"role": "user", "content": user_prompt}

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
            "temperature": temperature,
            "max_tokens": self._max_tokens or settings.llm_max_tokens,
            "response_format": {"type": "json_object"},
            # Hybrid-thinking off by default (settings.llm_think): the server
            # otherwise streams a long `reasoning` preamble in a separate channel
            # that burns the token budget before any JSON `content` is produced.
            # Harmlessly ignored by model templates that don't define
            # `enable_thinking`.
            #
            # TWO knobs because no single one works everywhere: llama.cpp
            # (--jinja) and mlx_lm.server honour chat_template_kwargs, while
            # Ollama's OpenAI endpoint IGNORES it and honours reasoning_effort
            # instead — measured on qwen3, which emitted ~1000 reasoning tokens
            # and an empty `content` with only chat_template_kwargs set. Servers
            # ignore whichever field they don't implement.
            "chat_template_kwargs": {"enable_thinking": think},
            # Stream so httpx's read timeout applies to the gap BETWEEN tokens
            # rather than the whole generation: a long multi-section generation
            # can't ReadTimeout while tokens keep flowing, only a genuine stall
            # trips it. NB this does NOT cover time-to-first-token (model load +
            # prompt prefill) — keep prompts inside the server's context window.
            "stream": True,
        }
        if not think:
            # The half of the thinking kill switch that Ollama actually honours
            # (see chat_template_kwargs above). Only sent when disabling, so a
            # server that maps it to a real effort level isn't told "none" when
            # the reasoning role genuinely wants to think.
            payload["reasoning_effort"] = "none"
        repetition_penalty = (
            self._repetition_penalty
            if self._repetition_penalty is not None
            else settings.llm_repetition_penalty
        )
        if repetition_penalty:
            # mlx_lm.server extension (not part of the OpenAI schema, ignored by
            # servers that don't implement it); 0.0 means "disabled", so only
            # send it when actually set — see config.llm_repetition_penalty.
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
                        raise LlmError(f"LLM stream error: {event['error']}")
                    choices = event.get("choices") or []
                    if choices:
                        piece = (choices[0].get("delta") or {}).get("content")
                        if isinstance(piece, str):
                            chunks.append(piece)
                        reason = choices[0].get("finish_reason")
                        if reason:
                            finish_reason = reason
        except httpx.HTTPError as exc:
            raise LlmError(f"LLM request failed [{type(exc).__name__}]: {exc}") from exc

        content = _strip_think("".join(chunks))
        if not content.strip():
            raise EmptyLlmResponse("LLM returned empty content (stream produced no tokens)")
        if finish_reason == "length":
            # max_tokens was exhausted mid-generation — the JSON is cut off
            # mid-token, not merely malformed.
            snippet = content[-1000:] if len(content) > 1000 else content
            raise TruncatedLlmResponse(
                f"LLM response hit max_tokens (finish_reason='length') "
                f"after {len(content)} chars. End snippet: {snippet!r}"
            )
        return content

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{self.base_url}/v1/models", headers=self._headers)
            response.raise_for_status()
            data = response.json()
            return [m.get("id", "") for m in data.get("data", [])]


def get_llm(model: str | None = None) -> LlmClient:
    """The default client. `model` pins a specific model id (used by the opt-in
    vision pass); left None the id is discovered from the server."""
    return OpenAIClient(model=model)


def get_reasoning_llm() -> LlmClient:
    """Client for the reasoning/design role: brand detection, the design-brain
    passes, and the image tie-break judge — optionally a different (bigger)
    endpoint with thinking enabled, while the default client keeps bulk content
    generation. Falls back to the default client when neither REASONING_BASE_URL
    nor REASONING_MODEL is set, so calling this is always safe.

    This is the one piece of model routing that stays in the backend: *which
    role talks to which endpoint* is an application decision, not a serving one.
    """
    if not settings.reasoning_base_url and not settings.reasoning_model:
        return get_llm()
    return OpenAIClient(
        base_url=settings.reasoning_base_url,
        model=settings.reasoning_model,
        timeout=settings.reasoning_timeout_seconds,
        api_key=settings.reasoning_api_key,
        max_tokens=settings.reasoning_max_tokens,
        think_default=settings.reasoning_think,
    )


def active_vision_model() -> str | None:
    """The multimodal model for the opt-in vision pass. None ⇒ the pass is
    skipped entirely (the default)."""
    return settings.llm_vision_model


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
