"""Which model each LLM role uses, chosen per request in the UI.

Two roles, two choices: CONTENT (page copy, paste structure, bios, record pages,
translations) and REASONING (brand detection, design brain, image tie-break).
Each is either `LOCAL` — the ai-server, whatever model it serves — or the id of
a Claude model in `CLAUDE_MODELS`.

**The wire carries a NAME, never a URL or a key** — the same rule
`cms_targets` follows. The API key stays in the root .env; a caller can only
pick from this catalogue.

The choice rides on two request headers (`CONTENT_HEADER`, `REASONING_HEADER`)
that the frontend's one request helper attaches to every call, and
`LlmChoiceMiddleware` puts it in a ContextVar for the life of the request. So
`llm.get_llm()` / `get_reasoning_llm()` read it without a parameter threaded
through nine services, and every LLM-backed endpoint — paste preview, page
recipe, generation — honours it with no per-endpoint edit. Tasks spawned
inside a request (`asyncio.create_task` copies the context) inherit it,
including a crawl job started from /api/scrape/start.

A request with no headers — tests, scripts, curl — gets `LlmChoice()`: both
roles local, exactly what the backend did before the choice existed.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from dataclasses import dataclass

LOCAL = "local"

CONTENT_HEADER = "x-webtree-llm-content"
REASONING_HEADER = "x-webtree-llm-reasoning"


@dataclass(frozen=True, slots=True)
class ClaudeModel:
    id: str
    label: str
    note: str
    # Server-side refusal fallbacks (`fallbacks="default"`) are documented for
    # these models only; elsewhere the parameter is left off rather than risked.
    fallbacks: bool


CLAUDE_MODELS: tuple[ClaudeModel, ...] = (
    ClaudeModel(
        id="claude-opus-5",
        label="Claude Opus 5",
        note="Strong default for page copy. $5 / $25 per million tokens.",
        fallbacks=True,
    ),
    ClaudeModel(
        id="claude-fable-5-1",
        label="Claude Fable 5.1",
        note=(
            "Most capable, for judgment-heavy calls. $10 / $50 per million tokens. "
            "Needs 30-day data retention on the Anthropic organisation."
        ),
        fallbacks=True,
    ),
    ClaudeModel(
        id="claude-sonnet-5",
        label="Claude Sonnet 5",
        note="Faster and cheaper. $2 / $10 per million tokens.",
        fallbacks=False,
    ),
)

_CLAUDE_BY_ID = {model.id: model for model in CLAUDE_MODELS}


def claude_model(choice: str) -> ClaudeModel | None:
    """The catalogue entry for `choice`, or None when it is `LOCAL`."""
    return _CLAUDE_BY_ID.get(choice)


def is_known(choice: str) -> bool:
    return choice == LOCAL or choice in _CLAUDE_BY_ID


@dataclass(frozen=True, slots=True)
class LlmChoice:
    content: str = LOCAL
    reasoning: str = LOCAL


_CURRENT: ContextVar[LlmChoice] = ContextVar("llm_choice", default=LlmChoice())


def current() -> LlmChoice:
    """The choice for the request being served (both roles local outside one)."""
    return _CURRENT.get()


def use(choice: LlmChoice):
    """Make `choice` current; returns the token `reset` takes. Tests use it too."""
    return _CURRENT.set(choice)


def reset(token) -> None:
    _CURRENT.reset(token)


def uses_claude(choice: LlmChoice | None = None) -> bool:
    choice = choice or current()
    return choice.content != LOCAL or choice.reasoning != LOCAL


class LlmChoiceMiddleware:
    """Reads the two headers into the ContextVar for one request.

    Pure ASGI rather than `@app.middleware("http")`, so the endpoint runs in
    this coroutine's own context — nothing to propagate across a task boundary.
    An unknown id is a 400, never a silent fallback: generating with a model
    nobody picked either spends money nobody agreed to, or quietly isn't the
    model that was asked for.
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        content = headers.get(CONTENT_HEADER, "").strip() or LOCAL
        reasoning = headers.get(REASONING_HEADER, "").strip() or LOCAL
        unknown = [c for c in (content, reasoning) if not is_known(c)]
        if unknown:
            body = json.dumps(
                {
                    "detail": (
                        f"Unknown model choice {unknown[0]!r}. Choose one of: "
                        + ", ".join([LOCAL, *_CLAUDE_BY_ID])
                        + "."
                    )
                }
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [(b"content-type", b"application/json")],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        token = use(LlmChoice(content=content, reasoning=reasoning))
        try:
            await self.app(scope, receive, send)
        finally:
            reset(token)
