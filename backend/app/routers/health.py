import httpx
from fastapi import APIRouter

from app.config import settings
from app.services import text_detection

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
    """
    result: dict[str, object] = {"base_url": settings.llm_base_url}
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{settings.llm_base_url}/v1/models")
            response.raise_for_status()
            # `or []` not `.get("data", [])`: Ollama returns "data": null (not an
            # empty list) when no model is loaded yet.
            models = [m.get("id") for m in (response.json().get("data") or [])]
            result |= {
                "status": "ok",
                "models": models,
                # What a generation would actually use — matches the pinning and
                # tie-break rules in services/llm._discover_model.
                "model": settings.llm_model or (sorted(m for m in models if m) or [None])[0],
                "pinned": bool(settings.llm_model),
            }
        except httpx.HTTPError as exc:
            result |= {"status": "unreachable", "error": str(exc)}
    if settings.reasoning_base_url or settings.reasoning_model:
        # api_key deliberately excluded — this endpoint is frontend-visible.
        result["reasoning"] = {
            "model": settings.reasoning_model,
            "base_url": settings.reasoning_base_url or settings.llm_base_url,
            "think": settings.reasoning_think,
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
