"""
Pexels photo search client.

Free API key required: https://www.pexels.com/api/
The key has a generous free tier (200 req/hr, 20k req/month) but we still
cache results so multi-page generations don't hammer the API.

Attribution: Pexels TOS requires photographer credit. Always surface the
PhotoResult.attribution when displaying a photo to end users.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


PhotoOrientation = Literal["landscape", "portrait", "square"]
PhotoSize = Literal["medium", "large", "large2x", "original"]


@dataclass(frozen=True)
class PhotoResult:
    """A single photo selected from Pexels (or a fallback provider)."""

    url: str
    alt: str
    photographer: str | None
    photographer_url: str | None
    source: Literal["pexels", "picsum", "scraped"]
    # Average colour hex (Pexels returns this for free). Drives the adaptive
    # dark-overlay intensity for photo backgrounds. None when unknown.
    avg_color: str | None = None

    @property
    def attribution(self) -> str | None:
        """Plain-text attribution string suitable for footer credit."""
        if self.source == "pexels" and self.photographer:
            return f"Photo by {self.photographer} on Pexels"
        return None


class PexelsClient:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key or settings.pexels_api_key
        self.base_url = settings.pexels_base_url.rstrip("/")
        self.timeout = settings.pexels_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    async def search(
        self,
        query: str,
        *,
        orientation: PhotoOrientation = "landscape",
        size: PhotoSize = "large",
        per_page: int = 5,
    ) -> PhotoResult | None:
        """
        Returns the first matching photo for `query`, or None if Pexels is
        unconfigured / errors / returns no results.
        """
        if not self.configured:
            return None

        cache_key = _cache_key(query, orientation, size)
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        params = {
            "query": query,
            "orientation": orientation,
            "per_page": str(per_page),
            "size": "medium",  # quality tier; we pick the URL below
        }
        headers = {"Authorization": self.api_key or ""}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.get(
                    f"{self.base_url}/search", params=params, headers=headers
                )
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError as exc:
            logger.warning("Pexels search failed for %r: %s", query, exc)
            return None

        photos = payload.get("photos") or []
        if not photos:
            return None

        photo = photos[0]
        src = photo.get("src") or {}
        url = src.get(size) or src.get("large") or src.get("medium")
        if not url:
            return None

        result = PhotoResult(
            url=url,
            alt=photo.get("alt") or query,
            photographer=photo.get("photographer"),
            photographer_url=photo.get("photographer_url"),
            source="pexels",
            avg_color=photo.get("avg_color"),
        )
        _cache_put(cache_key, result)
        return result


# Simple LRU cache. We deliberately don't use functools.lru_cache on an async
# method because httpx clients are not safely shared across event loops; instead
# we cache just the PhotoResult by key.

_CACHE: dict[str, PhotoResult] = {}
_CACHE_ORDER: list[str] = []
_CACHE_LOCK = asyncio.Lock()


def _cache_key(query: str, orientation: str, size: str) -> str:
    return f"{orientation}|{size}|{query.strip().lower()}"


def _cache_get(key: str) -> PhotoResult | None:
    return _CACHE.get(key)


def _cache_put(key: str, value: PhotoResult) -> None:
    if key in _CACHE:
        return
    _CACHE[key] = value
    _CACHE_ORDER.append(key)
    while len(_CACHE_ORDER) > max(1, settings.pexels_cache_size):
        oldest = _CACHE_ORDER.pop(0)
        _CACHE.pop(oldest, None)


@lru_cache(maxsize=1)
def get_pexels_client() -> PexelsClient:
    return PexelsClient()
