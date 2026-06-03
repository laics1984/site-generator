"""
Image resolution layer used by schema_builder.

Picks the best available image for a given query+intent, in this order:
  1. Scraped images from the source page (URL scrape or doc upload), ranked
     against the slot's image_query via services/image_match.py. We use a
     scraped image ONLY when its match score clears the threshold — otherwise
     we let Pexels supply a more topical photo.
  2. Pexels API (fallback for everything that didn't pass the match threshold,
     plus avatars and CTA backgrounds which never use scraped images).
  3. Picsum deterministic placeholder (no key required, last resort).

The legacy `scraped_images: list[str]` constructor argument still works (existing
callers like the legacy /generate/from-source endpoint pass it). When it's used,
each URL is wrapped in a minimal ImageMetadata with intent='generic' so the
scorer can still rank them by URL-path tokens and size hints.

Attribution metadata is preserved on every result so the generator can surface
credit lines in the footer.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Literal

from app.models.content_blocks import ImageMetadata
from app.services.image_match import (
    rank_candidates,
    rank_candidates_with_llm_tiebreaker,
)
from app.services.pexels import PexelsClient, PhotoResult, get_pexels_client

logger = logging.getLogger(__name__)


ImageIntent = Literal["hero", "about", "cta_bg", "avatar", "feature", "generic"]


_INTENT_TO_ORIENTATION: dict[ImageIntent, Literal["landscape", "portrait", "square"]] = {
    "hero": "landscape",
    "about": "landscape",
    "cta_bg": "landscape",
    "avatar": "square",
    "feature": "square",
    "generic": "landscape",
}

# Intents where scraped imagery is plausible. Avatars / CTA backgrounds always
# skip the scraped pool (portraits and atmospheric backgrounds from Pexels work
# better than a brand brochure's random pages).
_SCRAPED_ELIGIBLE_INTENTS: frozenset[str] = frozenset({"hero", "about", "generic", "feature"})

# Intents likely to depict people, where a market/locale cue ("Southeast Asian …")
# keeps stock imagery on-audience. Atmospheric CTA backgrounds / logos are skipped.
_PEOPLE_INTENTS: frozenset[str] = frozenset({"hero", "about", "avatar", "feature", "generic"})


class ImageResolver:
    """
    Stateful resolver scoped to one site generation.

    Tracks which scraped images have been used so multiple sections get
    different photos. Keeps a list of attributions to surface later.

    `use_llm_tiebreaker`: when True (default), invoke a tiny LLM judge for
    scraped images whose heuristic match score is in the ambiguous band
    (0.30 – 0.55). Bounded: only fires when at least 2 candidates are within
    0.10 of the top score. Disable for deterministic / fast generations.
    """

    def __init__(
        self,
        scraped_images: list[str] | None = None,
        scraped_metadata: list[ImageMetadata] | None = None,
        pexels: PexelsClient | None = None,
        use_llm_tiebreaker: bool = True,
        market_cue: str | None = None,
    ) -> None:
        # Prefer rich metadata when provided; fall back to wrapping bare URLs.
        if scraped_metadata:
            self._pool: list[ImageMetadata] = list(scraped_metadata)
        elif scraped_images:
            self._pool = [
                ImageMetadata(url=u, alt="", intent="generic")
                for u in scraped_images
            ]
        else:
            self._pool = []

        self._used_urls: set[str] = set()
        self._pexels = pexels or get_pexels_client()
        self._attributions: list[str] = []
        self._seen_pexels_urls: set[str] = set()
        self._use_llm_tiebreaker = use_llm_tiebreaker
        # Regional demonym (e.g. "Southeast Asian") prepended to people-likely
        # stock queries so imagery reflects the business's actual market.
        self._market_cue = (market_cue or "").strip()

    @property
    def attributions(self) -> list[str]:
        # de-dupe but preserve order
        seen: set[str] = set()
        out: list[str] = []
        for a in self._attributions:
            if a not in seen:
                seen.add(a)
                out.append(a)
        return out

    async def resolve(
        self,
        query: str | None,
        *,
        intent: ImageIntent = "generic",
        alt_fallback: str | None = None,
    ) -> PhotoResult:
        """Returns a usable PhotoResult. Always succeeds — Picsum is the final fallback."""
        orientation = _INTENT_TO_ORIENTATION[intent]

        # 1. Scraped pool — rank against the slot's image_query
        if intent in _SCRAPED_ELIGIBLE_INTENTS:
            picked = await self._take_best_scraped(query, intent)
            if picked is not None:
                self._used_urls.add(picked.url)
                return PhotoResult(
                    url=picked.url,
                    alt=picked.alt or alt_fallback or (query or "Source image"),
                    photographer=None,
                    photographer_url=None,
                    source="scraped",
                )

        # 2. Pexels — locale-cued for people-likely slots, plain-query fallback.
        if query and self._pexels.configured:
            photo = await self._search_pexels(query, orientation, intent)
            if photo and photo.url not in self._seen_pexels_urls:
                self._seen_pexels_urls.add(photo.url)
                if photo.attribution:
                    self._attributions.append(photo.attribution)
                return photo

        # 3. Picsum deterministic fallback.
        return _picsum_photo(query or alt_fallback or "site", orientation, alt_fallback)

    async def _search_pexels(
        self, query: str, orientation: str, intent: str
    ) -> PhotoResult | None:
        """Search Pexels, preferring a market-cued query for people-likely slots.
        Falls back to the plain query when the cue returns nothing (availability)."""
        if self._market_cue and intent in _PEOPLE_INTENTS:
            cued = await self._pexels.search(
                f"{self._market_cue} {query}", orientation=orientation
            )
            if cued is not None:
                return cued
        return await self._pexels.search(query, orientation=orientation)

    async def _take_best_scraped(
        self, query: str | None, intent: str
    ) -> ImageMetadata | None:
        """Rank unused scraped candidates against the slot. Returns None if the
        best match doesn't clear the threshold — caller falls through to Pexels.
        """
        candidates = [
            c for c in self._pool
            if c.url not in self._used_urls and _looks_like_image(c.url)
        ]
        if not candidates:
            return None

        if self._use_llm_tiebreaker:
            result = await rank_candidates_with_llm_tiebreaker(
                query, intent, candidates
            )
        else:
            result = rank_candidates(query, intent, candidates)

        if result.chosen is not None:
            logger.debug(
                "Scraped image picked for '%s' (intent=%s): score=%.2f decision=%s",
                query, intent, result.chosen_score, result.decision,
            )
            return result.chosen
        logger.debug(
            "No scraped image cleared threshold for '%s' (intent=%s); top=%.2f",
            query, intent,
            result.scores[0].score if result.scores else 0.0,
        )
        return None


def _picsum_photo(
    seed: str, orientation: str, alt: str | None
) -> PhotoResult:
    digest = hashlib.md5(seed.encode("utf-8")).hexdigest()[:10]
    if orientation == "portrait":
        w, h = 600, 800
    elif orientation == "square":
        w, h = 600, 600
    else:
        w, h = 1200, 800
    return PhotoResult(
        url=f"https://picsum.photos/seed/{digest}/{w}/{h}",
        alt=alt or seed,
        photographer=None,
        photographer_url=None,
        source="picsum",
    )


_IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".avif")


def _looks_like_image(url: str) -> bool:
    lowered = url.lower().split("?", 1)[0]
    # PDF/DOCX images arrive as base64 data URLs — accept them so the generated
    # site uses the document's own imagery. Phase 4 (CMS push) will swap each
    # data URL for an uploaded media URL before persisting the schema.
    if lowered.startswith("data:image/"):
        return True
    if lowered.endswith(_IMAGE_EXTS):
        return True
    # Many CDNs serve images without extensions; accept https URLs with
    # /image/ or /photo/ in the path as a heuristic.
    return any(token in lowered for token in ("/image", "/photo", "/img/", "cdn"))
