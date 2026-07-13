import httpx
from fastapi import APIRouter

from app.config import settings

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/health/ollama")
async def health_ollama() -> dict[str, object]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{settings.ollama_base_url}/api/tags")
            response.raise_for_status()
            payload = response.json()
            models = [m.get("name") for m in payload.get("models", [])]
            return {"status": "ok", "models": models}
        except httpx.HTTPError as exc:
            return {"status": "unreachable", "error": str(exc)}


@router.get("/health/mlx")
async def health_mlx() -> dict[str, object]:
    """Reports the MLX server's loaded models (mlx_lm.server, OpenAI-compatible)."""
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            response = await client.get(f"{settings.mlx_base_url}/v1/models")
            response.raise_for_status()
            payload = response.json()
            models = [m.get("id") for m in payload.get("data", [])]
            return {"status": "ok", "models": models}
        except httpx.HTTPError as exc:
            return {"status": "unreachable", "error": str(exc)}


@router.get("/health/llm")
async def health_llm() -> dict[str, object]:
    """The active LLM backend (mlx|ollama) and its default model — lets the
    frontend show which engine is serving generation."""
    from app.services.llm import resolve_llm_backend

    backend = resolve_llm_backend()
    model = settings.mlx_model if backend == "mlx" else settings.ollama_model
    result: dict[str, object] = {
        "backend": backend,
        "model": model,
        "configured": settings.llm_backend,
    }
    if settings.reasoning_model:
        # api_key deliberately excluded — this endpoint is frontend-visible.
        result["reasoning"] = {
            "backend": (settings.reasoning_backend or backend).lower(),
            "model": settings.reasoning_model,
            "base_url": settings.reasoning_base_url,
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
