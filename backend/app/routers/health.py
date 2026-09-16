import httpx
from fastapi import APIRouter

from app.config import settings
from app.services import llm_choice, text_detection
from app.services.llm import (
    ANTHROPIC_API_ROOT,
    AnthropicClient,
    LlmError,
    endpoint_failure_hint,
)

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


# Kept as aliases so existing bookmarks/scripts don't 404. There is only one
# LLM endpoint now, whichever engine the ai-server happens to be running.
@router.get("/health/ollama")
@router.get("/health/mlx")
@router.get("/health/llm")
async def health_llm() -> dict[str, object]:
    """What the AI server is actually serving right now.

    Reports the LIVE model list from /v1/models rather than a configured name —
    the backend holds no model config, so this is the honest answer and it
    reflects a model swap on the ai-server immediately.

    It answers for THIS request's model choice (the picker's headers, see
    services/llm_choice.py): top-level keys describe the content role, and
    `reasoning` the reasoning role. A Claude choice is confirmed by a model
    lookup that spends no tokens; one lookup per distinct model.
    """
    choice = llm_choice.current()
    probes: dict[tuple[str, ...], dict[str, object]] = {}

    async def claude(model: str) -> dict[str, object]:
        key = ("anthropic", model)
        if key not in probes:
            probes[key] = {"provider": "anthropic", **await _claude_status(model)}
        return probes[key]

    async def local(base_url: str, pinned: str | None) -> dict[str, object]:
        key = ("local", base_url, pinned or "")
        if key not in probes:
            probes[key] = {"provider": "local", **await _local_status(base_url, pinned)}
        return probes[key]

    if choice.content != llm_choice.LOCAL:
        content = await claude(choice.content)
    else:
        content = await local(settings.llm_base_url, settings.llm_model)
    result: dict[str, object] = {"choice": choice.content, **content}

    # Always present, so the picker can show each role's own status. One probe
    # per distinct endpoint: an all-local or single-model choice probes once.
    if choice.reasoning != llm_choice.LOCAL:
        # api_key deliberately excluded — this endpoint is frontend-visible.
        reasoning = {
            **await claude(choice.reasoning),
            "effort": settings.anthropic_reasoning_effort,
        }
    elif settings.reasoning_base_url or settings.reasoning_model:
        reasoning = {
            **await local(
                settings.reasoning_base_url or settings.llm_base_url, settings.reasoning_model
            ),
            "think": settings.reasoning_think,
        }
    else:
        reasoning = await local(settings.llm_base_url, settings.llm_model)
    result["reasoning"] = {"choice": choice.reasoning, **reasoning}
    return result


async def _claude_status(model: str) -> dict[str, object]:
    status: dict[str, object] = {
        "base_url": ANTHROPIC_API_ROOT,
        "model": model,
        "models": [model],
        "pinned": True,
    }
    try:
        await AnthropicClient(model=model).probe()
    except LlmError as exc:  # missing package or key — nothing was sent
        return status | {"status": "unconfigured", "hint": str(exc)}
    except Exception as exc:  # noqa: BLE001 - the SDK's own error types
        return status | {
            "status": "unreachable",
            "error": str(exc),
            "hint": endpoint_failure_hint(exc, ANTHROPIC_API_ROOT),
        }
    return status | {"status": "ok"}


async def _local_status(base_url: str, pinned: str | None) -> dict[str, object]:
    result: dict[str, object] = {"base_url": base_url}
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{base_url}/v1/models")
            response.raise_for_status()
            # `or []` not `.get("data", [])`: Ollama returns "data": null (not an
            # empty list) when no model is loaded yet.
            models = [m.get("id") for m in (response.json().get("data") or [])]
            result |= {
                "status": "ok",
                "models": models,
                # What a generation would actually use — matches the pinning and
                # tie-break rules in services/llm._discover_model.
                "model": pinned or (sorted(m for m in models if m) or [None])[0],
                "pinned": bool(pinned),
            }
        except httpx.HTTPError as exc:
            # `hint` carries the remedy, the same way /health/pexels and
            # /health/ocr do — str(exc) alone names the syscall, not the cause.
            result |= {
                "status": "unreachable",
                "error": str(exc),
                "hint": endpoint_failure_hint(exc, base_url),
            }
    return result


@router.get("/health/pexels")
async def health_pexels() -> dict[str, object]:
    """Reports whether the Pexels API key is set. Does not make a real API call."""
    if settings.pexels_api_key:
        return {"status": "configured", "provider": "pexels"}
    return {
        "status": "missing_key",
        "provider": "placeholder_fallback",
        "hint": "Set PEXELS_API_KEY in .env for topical photos. Get a free key at https://www.pexels.com/api/.",
    }


@router.get("/health/ocr")
async def health_ocr() -> dict[str, object]:
    """Reports whether the hero text-detection veto can actually run.

    Unlike /health/pexels this does real work on first hit — it lazily loads
    the rapidocr-onnxruntime model (~350ms, no network) to find out, since a
    missing/broken wheel is otherwise silent (see text_detection._engine).
    Cached process-lifetime after the first call either way.
    """
    if not settings.ocr_text_detection_enabled:
        return {"status": "disabled"}
    if text_detection.ocr_engine_available():
        return {"status": "ok"}
    return {
        "status": "unavailable",
        "hint": "rapidocr-onnxruntime is not importable — hero backgrounds fall "
        "back to vision/naming signals only. Rebuild the image after a "
        "requirements.txt change, or check the container logs for the import error.",
    }
