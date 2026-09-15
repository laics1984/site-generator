"""The model picker's menu: what each LLM role can be pointed at.

Static on purpose — no network call. Whether the local AI server is up, and
whether the Claude key actually works, is `/health/llm`'s job, which reports on
whatever the request's own choice is. This endpoint only says what may be
chosen, so the picker can render before either answers.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.config import settings
from app.services import llm_choice

router = APIRouter(prefix="/api/llm", tags=["llm"])

_NO_KEY_HINT = "Set ANTHROPIC_API_KEY in the root .env and restart the backend to use Claude."


@router.get("/models")
async def llm_models() -> dict[str, object]:
    claude_ready = bool(settings.anthropic_api_key)
    choices: list[dict[str, object]] = [
        {
            "id": llm_choice.LOCAL,
            "label": "Local AI server",
            "provider": "local",
            "note": "Whatever model the ai-server is serving. Nothing leaves this machine.",
            "available": True,
        }
    ]
    for model in llm_choice.CLAUDE_MODELS:
        entry: dict[str, object] = {
            "id": model.id,
            "label": model.label,
            "provider": "anthropic",
            "note": model.note,
            "available": claude_ready,
        }
        if not claude_ready:
            entry["hint"] = _NO_KEY_HINT
        choices.append(entry)
    default = llm_choice.LlmChoice()
    return {
        "choices": choices,
        "default": {"content": default.content, "reasoning": default.reasoning},
    }
