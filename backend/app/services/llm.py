"""
Ollama client. Uses the /api/chat endpoint with format='json' for
structured outputs — required because schema_builder consumes typed JSON.
"""

from __future__ import annotations

import json
import logging
from typing import Any, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.config import settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


class LlmError(Exception):
    pass


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
    ) -> T:
        """
        Send a chat request expecting JSON back, then validate against `schema`.

        Retries once with a stricter instruction if the first response is
        unparseable. Qwen 2.5 with format='json' is reliable but not perfect.

        num_ctx must be set explicitly — Ollama's default of 2048 is too small
        for most generation tasks. Callers can override for larger payloads.
        """
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "format": "json",
            # Stream the response so httpx's read timeout applies to the gap
            # BETWEEN tokens, not the whole generation. A long multi-section
            # generation can't ReadTimeout as long as tokens keep flowing — only
            # a genuine stall trips the timeout. _post_chat reassembles the chunks.
            "stream": True,
            "options": {"temperature": temperature, "num_ctx": num_ctx},
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response_text = await self._post_chat(client, payload)
            first_err: ValidationError | None = None
            try:
                return schema.model_validate_json(response_text)
            except ValidationError as exc:
                first_err = exc
                err_summary = "; ".join(
                    f"{'.'.join(str(l) for l in e['loc'])}: {e['msg']}"
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
                        "Fix every error: replace null values with real strings, add any missing "
                        "required fields (especially: every page object MUST have a 'blocks' array "
                        "containing the section objects), and correct any wrong 'kind' values. "
                        "Reply ONLY with valid JSON matching the schema. No markdown, no commentary."
                    ),
                },
            ]
            response_text = await self._post_chat(client, repair_payload)
            try:
                return schema.model_validate_json(response_text)
            except ValidationError as second_err:
                raise LlmError(
                    f"LLM produced invalid JSON after retry: {second_err}"
                ) from second_err

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
                "POST", f"{self.base_url}/api/chat", json=payload
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


def get_llm(model: str | None = None) -> OllamaClient:
    return OllamaClient(model=model)


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
