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
