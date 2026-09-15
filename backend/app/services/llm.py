"""
LLM client for structured (JSON) generation.

Two clients behind one `LlmClient` Protocol, chosen per ROLE and per REQUEST
by the model picker in the UI (services/llm_choice.py; see get_llm and
get_reasoning_llm):

- `OpenAIClient` (choice "local", the default) speaks the OpenAI wire
  (/v1/chat/completions and /v1/models). Every engine the ai-server can run —
  Ollama, llama.cpp's llama-server, mlx_lm.server on Apple Silicon, LM Studio,
  vLLM — serves that same API, so the backend needs no notion of which one is
  behind the URL. Engine, model, quantization, context length, keep-alive and
  GPU offload all live in ai-server/.env; this module only knows
  `settings.llm_base_url`. The model id is not configured either —
  `resolve_model()` reads it from /v1/models, so swapping the model in
  ai-server/.env takes effect here without an edit or a restart.
- `AnthropicClient` (a Claude model id) calls the Claude API through the
  official SDK, with the schema enforced as structured output.

Call sites never learn which one they hold: both return a validated Pydantic
model from `chat_json`, and both go through the same validate-or-repair retry
(`_validated`) and truncation-aware budget growth.
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
import time
from typing import Any, Awaitable, Callable, Iterator, Protocol, TypeVar
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings
from app.services import llm_choice

try:
    # Optional at import time so the local path keeps working in a container
    # built before the dependency was added (see CLAUDE.md "Docker dependency
    # skew"); AnthropicClient raises a clear LlmError instead.
    import anthropic
except ImportError:  # pragma: no cover - exercised only in a stale image
    anthropic = None  # type: ignore[assignment]

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


# --- Why the AI server could not be reached ------------------------------------
#
# One home for the remedy, because the two places that fail (model discovery and
# the completion stream) plus /health/llm would otherwise each carry their own
# guess. A raw transport error names the syscall, not the cause: "[Errno -2] Name
# or service not known" is a hostname that does not resolve, which is a different
# job from a port nothing is listening on, and neither is "check it is running".
#
# Classified by exception TYPE, never by message text. The identical DNS failure
# reads "Name or service not known" under glibc (the backend container) and
# "nodename nor servname provided" under macOS (the tests, and a bare uvicorn
# run), so a string match would be a silent off switch on one of the two.


def _cause_chain(exc: BaseException) -> Iterator[BaseException]:
    """`exc` and everything it was raised from. httpx wraps httpcore wraps the
    OSError that actually carries the verdict, so the cause is where the answer
    is."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _anthropic_failure_hint(exc: Exception) -> str | None:
    """The remedy for a failed Claude API call, or None when `exc` is not one.
    Classified by SDK exception type, the same rule as the local branch below —
    most specific first, since RateLimitError etc. all derive APIStatusError."""
    if anthropic is None or not isinstance(exc, anthropic.AnthropicError):
        return None
    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "the Claude API rejected the API key (HTTP 401). Set ANTHROPIC_API_KEY "
            "in the root .env and restart the backend."
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return (
            "the API key is not permitted to use this model or feature (HTTP 403). "
            "Check the key's workspace, or pick another model in the header's model menu."
        )
    if isinstance(exc, anthropic.NotFoundError):
        return (
            "the Claude API does not recognise the model id (HTTP 404) — this API key "
            "may not have access to it. Pick another model in the header's model menu."
        )
    if isinstance(exc, anthropic.RateLimitError):
        return (
            "the Claude API rate-limited this account (HTTP 429) after the SDK's own "
            "retries. Lower SCAFFOLD_BATCH_CONCURRENCY, or raise the account's "
            "rate-limit tier."
        )
    if isinstance(exc, anthropic.BadRequestError):
        return (
            "the Claude API rejected the request (HTTP 400). If the error mentions "
            "data retention: Claude Fable 5.1 needs the organisation on 30-day "
            "retention — pick Claude Opus 5 in the header's model menu instead."
        )
    if isinstance(exc, anthropic.APIStatusError):
        return (
            f"the Claude API answered HTTP {exc.status_code}, usually transient — the "
            "SDK already retried; try again shortly."
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return (
            "the Claude API did not answer in time. Raise LLM_TIMEOUT_SECONDS "
            "(or REASONING_TIMEOUT_SECONDS for the reasoning role)."
        )
    if isinstance(exc, anthropic.APIConnectionError):
        if socket.gaierror in {type(c) for c in _cause_chain(exc)}:
            return (
                "api.anthropic.com does not resolve from this process — check this "
                "machine's (or container's) DNS and internet access."
            )
        return (
            "could not connect to the Claude API — this process needs outbound "
            "HTTPS access to api.anthropic.com (check proxies and container egress)."
        )
    if isinstance(exc, anthropic.CredentialsError):
        return (
            "the Anthropic SDK found no usable credentials. Set ANTHROPIC_API_KEY in "
            "the root .env and restart the backend."
        )
    return f"the Anthropic SDK raised {type(exc).__name__} — see the error above."


def endpoint_failure_hint(exc: Exception, base_url: str) -> str:
    """A remedy for a failed call to `base_url`, addressed to the operator."""
    claude_hint = _anthropic_failure_hint(exc)
    if claude_hint is not None:
        return claude_hint
    where = urlsplit(base_url).netloc or base_url
    causes = {type(c) for c in _cause_chain(exc)}
    if socket.gaierror in causes:
        return (
            f"the hostname {where} does not resolve from this process. A VPN/Tailscale "
            "name resolves only while that tunnel is up, and a container does not inherit "
            "the host's VPN DNS — bring the tunnel up, or set LLM_BASE_URL (root .env) to "
            "an address this process can resolve."
        )
    if ConnectionRefusedError in causes:
        return (
            f"{where} resolves but refused the connection — nothing is serving there. "
            "Start the engine: ai-server/: ./ai.sh up (./ai.sh status shows which one is "
            "configured)."
        )
    if isinstance(exc, httpx.TimeoutException):
        return (
            f"{where} accepted the connection but did not answer in time. The model is "
            "probably still loading — ai-server/: ./ai.sh logs."
        )
    if isinstance(exc, httpx.HTTPStatusError):
        return (
            f"{where} answered HTTP {exc.response.status_code} for {exc.request.url.path}. "
            "LLM_BASE_URL must be the server ROOT (no /v1 suffix), and LLM_API_KEY must "
            "match what the server expects."
        )
    return f"could not reach {where} — ai-server/: ./ai.sh status."


class LlmClient(Protocol):
    """The surface call sites depend on. Two real implementations
    (`OpenAIClient`, `AnthropicClient`); tests substitute fakes.

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


_MAX_TOKENS_CAP = 131072


def _boost_budget(payload: dict[str, Any], cap: int = _MAX_TOKENS_CAP) -> dict[str, Any]:
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
    circuit breaker against runaway generation, not a model limitation. A client
    whose API has a hard output maximum passes it as `cap` (AnthropicClient:
    128K), since a request above it is a 400, not a longer generation.
    Returns `payload` unchanged (same dict) once the cap is reached, so callers
    can detect "budget can't grow any further" via equality."""
    boosted = dict(payload)
    if "max_tokens" in payload:
        new_max_tokens = min(payload["max_tokens"] * 2, cap)
        if new_max_tokens == payload["max_tokens"]:
            return payload
        boosted["max_tokens"] = new_max_tokens
    return boosted


async def _retry_with_growing_budget(
    post: PostChat,
    payload: dict[str, Any],
    trunc: TruncatedLlmResponse,
    cap: int = _MAX_TOKENS_CAP,
) -> tuple[str, dict[str, Any]]:
    """Keep doubling the output/context budget (_boost_budget) and retrying
    after a TruncatedLlmResponse, until a response fits or the budget hits its
    hard cap. Raises the last TruncatedLlmResponse once doubling stops changing
    the payload (cap reached) — at that point more retries can't help. Returns
    the response text AND the boosted payload it was won with, so a caller that
    goes on to a repair retry (schema validation failure) uses the same larger
    budget instead of falling back to the original, too-small one."""
    while True:
        boosted = _boost_budget(payload, cap)
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


def _repair_as_assistant_turn(
    payload: dict[str, Any], response_text: str, error_summary: str
) -> dict[str, Any]:
    """The rejected reply replayed as the model's own turn, then the errors."""
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
    return repair_payload


# Builds the repair request from (original payload, rejected reply, error summary).
RepairBuilder = Callable[[dict[str, Any], str, str], dict[str, Any]]


async def _validated(
    post: PostChat,
    payload: dict[str, Any],
    schema: type[T],
    repair: RepairBuilder = _repair_as_assistant_turn,
    max_tokens_cap: int = _MAX_TOKENS_CAP,
) -> T:
    """POST `payload`, validate the reply against `schema`, and on a validation
    error retry ONCE with the errors fed back to the model. An empty stream is
    retried separately (see _post_nonempty) before validation. Raises LlmError if
    the second attempt still fails.

    A response that was cut off by the token/context budget (TruncatedLlmResponse)
    is handled separately from malformed JSON: retrying with the SAME budget would
    just truncate again (and the repair prompt is even longer than the original),
    so that case retries with a doubled budget (see _retry_with_growing_budget)
    instead of a repair message.

    `repair` shapes the repair request. The default replays the rejected reply
    as an assistant turn; AnthropicClient quotes it inside a new user turn
    instead, because a replayed assistant turn stripped of its thinking blocks
    reads as edited history to models that preserve thinking.
    """
    try:
        response_text = await _post_nonempty(post, payload)
    except TruncatedLlmResponse as trunc:
        response_text, payload = await _retry_with_growing_budget(
            post, payload, trunc, max_tokens_cap
        )
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
    response_text = await _post_nonempty(post, repair(payload, response_text, error_summary))
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
    if settings.llm_cache_enabled and isinstance(client, (OpenAIClient, AnthropicClient)):
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
    """Close the shared LLM http clients (called on app shutdown)."""
    global _shared_http, _shared_anthropic
    if _shared_http is not None and not getattr(_shared_http, "is_closed", True):
        await _shared_http.aclose()
    _shared_http = None
    if _shared_anthropic is not None:
        await _shared_anthropic[1].close()
    _shared_anthropic = None


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
            f"[{type(exc).__name__}]: {exc}. Because {endpoint_failure_hint(exc, base_url)}"
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
            raise LlmError(
                # `url`, not self.base_url — a vision call routes to a
                # different server, and the hint must name the one that failed.
                f"LLM request failed [{type(exc).__name__}]: {exc}. Because "
                f"{endpoint_failure_hint(exc, url)}"
            ) from exc

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


# --- Claude API -----------------------------------------------------------------

ANTHROPIC_API_ROOT = "https://api.anthropic.com"
# The API's hard output ceiling for the models this client defaults to; a larger
# max_tokens is a 400, so budget growth stops here.
_ANTHROPIC_MAX_OUTPUT_TOKENS = 128_000
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

# One SDK client for the process, so back-to-back calls reuse its connection
# pool. Keyed by the API key it was built with, so a key change (tests, a
# settings reload) gets a fresh client instead of the stale one.
_shared_anthropic: tuple[str | None, Any] | None = None

# Schemas the API refused as structured output (a 400 that went away once the
# format was dropped), keyed by schema → the rejection it gave. Remembered so
# later calls skip the doomed first attempt.
_UNCONSTRAINED_SCHEMAS: dict[str, str] = {}


def _shared_anthropic_client() -> Any:
    global _shared_anthropic
    key = settings.anthropic_api_key
    if _shared_anthropic is None or _shared_anthropic[0] != key:
        # max_retries: the SDK already retries 408/409/429/5xx and connection
        # errors with backoff; 4 rides out a burst of concurrent scaffold batches.
        _shared_anthropic = (key, anthropic.AsyncAnthropic(api_key=key, max_retries=4))
    return _shared_anthropic[1]


def _strip_code_fence(text: str) -> str:
    """The JSON body of a reply that wrapped it in a markdown fence anyway."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        stripped = stripped.rsplit("```", 1)[0]
    return stripped.strip()


def _repair_as_user_turn(
    payload: dict[str, Any], response_text: str, error_summary: str
) -> dict[str, Any]:
    """The rejected reply QUOTED inside a new user turn — never replayed as an
    assistant turn. Claude models return thinking blocks alongside the text, and
    a replayed assistant turn without them is edited history to a model that
    preserves thinking (claude-fable-5-1 rejects it outright on newer accounts).
    Consecutive user turns are legal; the API joins them."""
    repair_payload = dict(payload)
    repair_payload["messages"] = [
        *payload["messages"],
        {
            "role": "user",
            "content": (
                f"Your previous reply was:\n{response_text}\n\n"
                f"It failed validation with these errors: {error_summary}. "
                + _REPAIR_INSTRUCTION
            ),
        },
    ]
    return repair_payload


def _without_output_format(payload: dict[str, Any], schema: type[BaseModel]) -> dict[str, Any]:
    """`payload` asking for the schema in words instead of as structured output —
    the path for a schema the API would not compile. Pydantic still validates
    the reply, and the repair retry still runs."""
    unconstrained = dict(payload)
    output_config = {k: v for k, v in payload["output_config"].items() if k != "format"}
    unconstrained["output_config"] = output_config
    instruction = (
        "\n\nReply with ONE JSON object that validates against this JSON Schema. "
        "No markdown, no commentary.\n"
        + json.dumps(schema.model_json_schema(), ensure_ascii=False)
    )
    unconstrained["system"] = [
        {**block, "text": block["text"] + instruction} if i == len(payload["system"]) - 1 else block
        for i, block in enumerate(payload["system"])
    ]
    return unconstrained


def _schema_key(schema: type[BaseModel]) -> str:
    return f"{schema.__module__}.{schema.__qualname__}"


class AnthropicClient:
    """Client for the Claude API — a role whose model choice is a Claude model
    (services/llm_choice.py).

    Same contract as OpenAIClient: `chat_json` returns a validated instance of
    `schema`, through the shared `_validated` retry. Differences, all forced by
    the API rather than chosen:

    - The schema is sent as structured output (`output_config.format`), so the
      reply is valid JSON by construction; Pydantic still validates, because the
      SDK strips constraints (min/max) the API cannot enforce.
    - No `temperature` and no `thinking`: current Claude models reject sampling
      knobs and think adaptively. `think=True` selects the reasoning effort
      instead (`output_config.effort`).
    - `model` is configured, not discovered — there is one API and many models.
    """

    base_url = ANTHROPIC_API_ROOT

    def __init__(
        self,
        model: str = "claude-opus-5",
        effort: str | None = None,
        think_effort: str | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        fallbacks: bool = True,
        sdk_client: Any = None,
    ) -> None:
        self.model = model
        # Off for a model the catalogue does not list fallbacks for; the
        # ANTHROPIC_FALLBACKS_ENABLED kill switch still wins when it is on.
        self._fallbacks = fallbacks
        self._effort = effort or settings.anthropic_effort
        self._think_effort = think_effort or settings.anthropic_reasoning_effort
        self._max_tokens = max_tokens
        self.timeout = timeout or settings.llm_timeout_seconds
        self._sdk_client = sdk_client

    @property
    def _sdk(self) -> Any:
        """The SDK client, or an LlmError naming what is missing. Raised here, at
        call time, so every call site's existing `except LlmError` handles it —
        a constructor that raised would escape the `llm or get_llm()` idiom."""
        if self._sdk_client is not None:
            return self._sdk_client
        if anthropic is None:
            raise LlmError(
                "A Claude model is selected but the anthropic "
                "package is not installed. Rebuild the backend image (docker compose "
                "build backend) or pip install -r backend/requirements.txt."
            )
        if not settings.anthropic_api_key:
            # Required rather than left to the SDK's own lookup: that reads the
            # environment and profile files behind config.py's back, and when it
            # finds nothing it raises a bare TypeError mid-generation.
            raise LlmError(
                "A Claude model is selected but ANTHROPIC_API_KEY is not set. Add it to "
                "the root .env and restart the backend, or pick the local AI server."
            )
        return _shared_anthropic_client()

    async def resolve_model(self) -> str:
        return self.model

    async def chat_json(
        self,
        system_prompt: str,
        user_prompt: str,
        schema: type[T],
        temperature: float | None = None,
        images: list[str] | None = None,
        think: bool | None = None,
    ) -> T:
        """`temperature` is accepted for the Protocol and deliberately not sent."""
        del temperature
        sdk = self._sdk  # fail before building anything when the SDK is unusable
        if images:
            content: str | list[dict[str, Any]] = [
                {
                    "type": "image",
                    # image_vision downscales every image to JPEG before encoding.
                    "source": {"type": "base64", "media_type": "image/jpeg", "data": b64},
                }
                for b64 in images
            ]
            content.append({"type": "text", "text": user_prompt})
        else:
            content = user_prompt

        system_block: dict[str, Any] = {"type": "text", "text": system_prompt}
        if settings.anthropic_prompt_cache:
            system_block["cache_control"] = {"type": "ephemeral"}

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": min(
                self._max_tokens or settings.llm_max_tokens, _ANTHROPIC_MAX_OUTPUT_TOKENS
            ),
            "system": [system_block],
            "messages": [{"role": "user", "content": content}],
            "output_config": {
                "effort": self._think_effort if think else self._effort,
                "format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)},
            },
        }

        key = _schema_key(schema)

        async def post(p: dict[str, Any]) -> str:
            if key in _UNCONSTRAINED_SCHEMAS:
                return await self._send(sdk, _without_output_format(p, schema))
            try:
                return await self._send(sdk, p)
            except LlmError as exc:
                if not isinstance(exc.__cause__, anthropic.BadRequestError):
                    raise
                # A 400 with the format attached may be the schema — or anything
                # else about the request. Only a success without the format
                # proves it was the schema; otherwise the original error stands.
                try:
                    text = await self._send(sdk, _without_output_format(p, schema))
                except LlmError as retry_exc:
                    if isinstance(retry_exc.__cause__, anthropic.BadRequestError):
                        raise exc from exc.__cause__
                    raise
                logger.warning(
                    "Claude API rejected %s as structured output (%s) — sending it "
                    "as a JSON instruction from now on",
                    schema.__name__, exc.__cause__,
                )
                _UNCONSTRAINED_SCHEMAS[key] = str(exc.__cause__)
                return text

        return await _validated(
            post,
            payload,
            schema,
            repair=_repair_as_user_turn,
            max_tokens_cap=_ANTHROPIC_MAX_OUTPUT_TOKENS,
        )

    async def _send(self, sdk: Any, payload: dict[str, Any]) -> str:
        """One streamed request, assembled into the reply text. Streaming keeps a
        long generation (large max_tokens, adaptive thinking) inside HTTP
        timeouts; the SDK assembles the final message."""
        try:
            if self._fallbacks and settings.anthropic_fallbacks_enabled:
                manager = sdk.beta.messages.stream(
                    **payload,
                    betas=[_FALLBACK_BETA],
                    fallbacks="default",
                    timeout=self.timeout,
                )
            else:
                manager = sdk.messages.stream(**payload, timeout=self.timeout)
            async with manager as stream:
                message = await stream.get_final_message()
        except anthropic.AnthropicError as exc:
            raise LlmError(
                f"Claude API request failed [{type(exc).__name__}]: {exc}. Because "
                f"{endpoint_failure_hint(exc, self.base_url)}"
            ) from exc

        usage = getattr(message, "usage", None)
        if usage is not None:
            logger.info(
                "Claude %s usage: input=%s output=%s cache_read=%s cache_write=%s",
                getattr(message, "model", self.model),
                getattr(usage, "input_tokens", None),
                getattr(usage, "output_tokens", None),
                getattr(usage, "cache_read_input_tokens", None),
                getattr(usage, "cache_creation_input_tokens", None),
            )

        if message.stop_reason == "refusal":
            details = getattr(message, "stop_details", None)
            raise LlmError(
                f"Claude declined the request (stop_reason='refusal', "
                f"category={getattr(details, 'category', None)!r}): "
                f"{getattr(details, 'explanation', None) or 'no explanation given'}"
            )
        text = _strip_code_fence(
            "".join(
                block.text for block in message.content if getattr(block, "type", None) == "text"
            )
        )
        if message.stop_reason == "max_tokens":
            # Checked before emptiness: thinking can spend the whole budget before
            # any text, and that needs a bigger budget, not the same one again.
            snippet = text[-1000:] if len(text) > 1000 else text
            raise TruncatedLlmResponse(
                f"Claude response hit max_tokens={payload.get('max_tokens')} "
                f"(stop_reason='max_tokens') after {len(text)} chars. End snippet: {snippet!r}"
            )
        if not text:
            raise EmptyLlmResponse("Claude returned no text content")
        return text

    async def probe(self) -> dict[str, Any]:
        """Confirm the key works and the model exists — spends no tokens."""
        info = await self._sdk.models.retrieve(self.model, timeout=10.0)
        return {"id": info.id, "display_name": getattr(info, "display_name", None)}

    async def list_models(self) -> list[str]:
        return [model.id async for model in self._sdk.models.list()]


# --- routing ------------------------------------------------------------------


def _claude_client(choice: str, *, reasoning: bool) -> AnthropicClient:
    entry = llm_choice.claude_model(choice)
    fallbacks = bool(entry and entry.fallbacks)
    # ANTHROPIC_MAX_TOKENS, never LLM_MAX_TOKENS / REASONING_MAX_TOKENS: those
    # are sized for the local model, and the picker can switch between the two
    # on the very next request.
    if reasoning:
        return AnthropicClient(
            model=choice,
            effort=settings.anthropic_reasoning_effort,
            max_tokens=settings.anthropic_max_tokens,
            timeout=settings.reasoning_timeout_seconds,
            fallbacks=fallbacks,
        )
    return AnthropicClient(
        model=choice, max_tokens=settings.anthropic_max_tokens, fallbacks=fallbacks
    )


def get_llm(model: str | None = None) -> LlmClient:
    """The content-role client for this request: the local AI server, or the
    Claude model picked in the UI (llm_choice.current().content).

    `model` pins a LOCAL model id and bypasses the choice — it is the opt-in
    vision pass (LLM_VISION_MODEL), which stays on the ai-server whatever the
    picker says."""
    if model is None:
        choice = llm_choice.current().content
        if choice != llm_choice.LOCAL:
            return _claude_client(choice, reasoning=False)
    return OpenAIClient(model=model)


def get_reasoning_llm() -> LlmClient:
    """Client for the reasoning/design role: brand detection, the design-brain
    passes, and the image tie-break judge.

    Picked in the UI independently of the content role
    (llm_choice.current().reasoning), so "all local", "all Claude" and "content
    local, judgment on Claude" are all one menu away. A LOCAL choice keeps the
    optional second endpoint: REASONING_BASE_URL / REASONING_MODEL route it to a
    different (bigger) model with thinking on; neither set, it uses the default
    local endpoint.

    This is the one piece of model routing that stays in the backend: *which
    role talks to which endpoint* is an application decision, not a serving one.
    """
    choice = llm_choice.current().reasoning
    if choice != llm_choice.LOCAL:
        return _claude_client(choice, reasoning=True)
    if not settings.reasoning_base_url and not settings.reasoning_model:
        return OpenAIClient()
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
    return json.loads(_strip_code_fence(text))
