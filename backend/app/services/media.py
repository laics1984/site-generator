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

Returned photos keep their source metadata, but stock photos fetched through
Pexels are not added to footer media credits.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import replace
from typing import Literal

from app.models.content_blocks import ImageMetadata
from app.services.image_match import (
    _tokens,
    rank_candidates,
    rank_candidates_with_llm_tiebreaker,
)
from app.services.image_styling import band_for_luminance, relative_luminance
from app.services.pexels import PexelsClient, PhotoResult, get_pexels_client

logger = logging.getLogger(__name__)


def _band_fields(
    avg_hex: str | None,
) -> tuple[float | None, Literal["light", "dark"] | None]:
    """Derive (luminance, band) from a dominant/average colour hex.

    The band is a 1-bit decision, so a single dominant colour is an adequate
    proxy — no pixel download (SECTION_VISUAL_POLICY_SPEC.md §4.3). Returns
    (None, None) when no colour is known or the hex can't be parsed; the
    luminance pass then applies the §8.4 light default.
    """
    if not avg_hex:
        return None, None
    try:
        lum = relative_luminance(avg_hex)
    except (ValueError, IndexError):
        logger.debug("Could not parse dominant colour %r for band", avg_hex)
        return None, None
    return round(lum, 4), band_for_luminance(lum)


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
        industry_category: str | None = None,
        place_cue: str | None = None,
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
        # Site-level industry (SitePlan.industry_category) — drives the
        # contextual fallback query when a slot's own query finds nothing.
        self._industry_category = (industry_category or "").strip().lower()
        # Place name (country or region, e.g. "Malaysia") appended to
        # non-person/atmospheric queries so scenery matches the locale too.
        self._place_cue = (place_cue or "").strip()

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
                # Carry the band when the scraper supplied a colour hint; else
                # None → luminance pass applies the §8.4 light default.
                lum, band = _band_fields(picked.dominant_color)
                return PhotoResult(
                    url=picked.url,
                    alt=picked.alt or alt_fallback or (query or "Source image"),
                    photographer=None,
                    photographer_url=None,
                    source="scraped",
                    luminance=lum,
                    band=band,
                )

        # 2. Pexels — locale-cued for people-likely slots, plain-query fallback.
        if query and self._pexels.configured:
            photo = await self._search_pexels(query, orientation, intent)
            if photo is not None:
                self._seen_pexels_urls.add(photo.url)
                # avg_color comes free from Pexels → derive the band, no download.
                lum, band = _band_fields(photo.avg_color)
                return replace(photo, luminance=lum, band=band)

        # 3. Picsum deterministic fallback.
        return _picsum_photo(query or alt_fallback or "site", orientation, alt_fallback)

    async def _search_pexels(
        self, query: str, orientation: str, intent: str
    ) -> PhotoResult | None:
        """Search Pexels, preferring a market-cued query for people-likely slots.

        Each chain query fetches a batch and we keep the result whose own alt
        text best matches the slot (Pexels' first hit is often a tangent).
        Already-used photos are skipped so multi-section sites don't repeat;
        falls through the chain when a cue exhausts its results (availability).
        """
        chain = _stock_query_chain(
            query, intent, self._market_cue, self._industry_category, self._place_cue
        )
        for candidate in chain:
            photos = await self._pexels.search_many(candidate, orientation=orientation)
            fresh = [p for p in photos if p.url not in self._seen_pexels_urls]
            if fresh:
                return max(fresh, key=lambda p: _stock_relevance(p, query, self._market_cue))
        return None

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


def _stock_relevance(photo: PhotoResult, query: str, market_cue: str) -> float:
    """Token overlap between a stock photo's own alt text and the slot query.

    Used to re-rank a Pexels result batch — the API's first hit is often a
    tangent ("dental clinic" → toothbrush macro). A small bonus rewards alts
    that mention the audience region, so on-market imagery wins ties.
    """
    alt_tokens = _tokens(photo.alt)
    if not alt_tokens:
        return 0.0
    q_tokens = _tokens(query)
    score = len(q_tokens & alt_tokens) / len(q_tokens) if q_tokens else 0.0
    cue_tokens = _tokens(market_cue)
    if cue_tokens and cue_tokens & alt_tokens:
        score += 0.25
    return score


def _stock_query_chain(
    query: str,
    intent: str,
    market_cue: str,
    industry_category: str = "",
    place_cue: str = "",
) -> list[str]:
    """Ordered stock queries from specific/local to broad/contextual/plain."""
    query = " ".join((query or "").split())
    market_cue = " ".join((market_cue or "").split())
    place_cue = " ".join((place_cue or "").split())
    if not query:
        return []
    if intent not in _PEOPLE_INTENTS:
        # Atmospheric slots (CTA backdrops): try the locale-anchored scene
        # first — "office skyline Malaysia" beats a random global skyline.
        return [f"{query} {place_cue}", query] if place_cue else [query]

    out: list[str] = []

    def add(value: str) -> None:
        value = " ".join(value.split())
        if value and value.lower() not in {q.lower() for q in out}:
            out.append(value)

    if market_cue:
        add(f"{market_cue} {query}")
        if "asian" in market_cue.lower() and market_cue.lower() != "asian":
            add(f"Asian {query}")

    contextual = _contextual_non_person_query(query, industry_category)
    if contextual:
        if place_cue:
            add(f"{contextual} {place_cue}")
        add(contextual)

    add(query)
    return out


# Site-level contextual fallback per SitePlan.industry_category — used when
# the slot's own wording doesn't hit any of the token buckets below.
_INDUSTRY_CONTEXT_QUERIES = {
    "restaurant": "restaurant interior food service",
    "agency": "creative studio team workspace",
    "saas": "modern software team office",
    "professional-services": "professional office consultation",
    "ecommerce": "modern retail product display",
    "consultancy": "business strategy meeting office",
    "nonprofit": "community volunteers working together",
    "personal": "creative professional workspace",
}


def _contextual_non_person_query(query: str, industry_category: str = "") -> str | None:
    """Fallback to places/process/products when localized people stock is poor.

    Slot-specific token buckets first (they read the query itself), then the
    site-level industry default. None when neither knows anything.
    """
    tokens = {
        t.lower()
        for t in query.replace("-", " ").split()
        if t.strip()
    }
    if tokens & {"clinic", "dental", "dentist", "medical", "healthcare", "therapy"}:
        return "modern clinic interior"
    if tokens & {"restaurant", "cafe", "coffee", "food", "dining", "menu"}:
        return "restaurant interior food service"
    if tokens & {"school", "classroom", "education", "training", "learning"}:
        return "modern classroom learning space"
    if tokens & {"factory", "manufacturing", "industrial", "warehouse"}:
        return "modern industrial workspace"
    if tokens & {"team", "people", "staff", "customer", "client", "professional", "meeting"}:
        return "modern professional workspace"
    if tokens & {"product", "retail", "ecommerce", "store"}:
        return "modern retail product display"
    return _INDUSTRY_CONTEXT_QUERIES.get(industry_category.strip().lower()) or None


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
