"""
Image resolution layer used by schema_builder.

Picks the best available image for a given query+intent, in this order:
  1. Scraped images from the source page (URL scrape or doc upload), ranked
     against the slot's image_query via services/image_match.py. We use a
     scraped image ONLY when its match score clears the threshold — otherwise
     we let Pexels supply a more topical photo.
  2. Pexels API (fallback for everything that didn't pass the match threshold,
     plus avatars and CTA backgrounds which never use scraped images).
  3. On-brand gradient placeholder (no key required, last resort — a designed
     two-tone SVG in the theme's own colours, not a random stock photo).

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
from urllib.parse import quote

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
        primary_hex: str | None = None,
        secondary_hex: str | None = None,
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
        # Per-seed placeholder counter: two slots that fall to the gradient
        # placeholder with the SAME seed would otherwise render the identical
        # image. We hand each repeat a distinct nonce so the gradient angle
        # differs (first use stays byte-identical, nonce 0).
        self._placeholder_seeds: dict[str, int] = {}
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
        # Brand colours for the last-resort placeholder gradient (see
        # _placeholder_photo) — on-brand instead of a generic grey.
        self._primary_hex = primary_hex or "#64748b"
        self._secondary_hex = secondary_hex or "#1e293b"

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
        prefer: list[ImageMetadata] | None = None,
    ) -> PhotoResult:
        """Returns a usable PhotoResult. Always succeeds — the gradient placeholder is the final fallback.

        `prefer`: images that belong to the page being rendered. They're ranked
        ahead of the rest of the site-wide pool so a page's hero/section uses the
        photo the source actually placed on THAT page, not the biggest one
        anywhere on the site. Falls through to the full pool when none of the
        page's own images fit the slot.
        """
        orientation = _INTENT_TO_ORIENTATION[intent]

        # 1. Scraped pool — rank against the slot's image_query
        if intent in _SCRAPED_ELIGIBLE_INTENTS:
            picked = await self._take_best_scraped(query, intent, prefer=prefer)
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

        # 3. Last resort: an on-brand gradient placeholder (no network, no
        # random stock photo) — see _placeholder_photo. A per-seed nonce keeps
        # repeated placeholders from rendering the identical gradient on a page.
        seed = query or alt_fallback or "site"
        nonce = self._placeholder_seeds.get(seed, 0)
        self._placeholder_seeds[seed] = nonce + 1
        return _placeholder_photo(
            seed,
            orientation,
            alt_fallback,
            primary_hex=self._primary_hex,
            secondary_hex=self._secondary_hex,
            nonce=nonce,
        )

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
        self, query: str | None, intent: str, *, prefer: list[ImageMetadata] | None = None
    ) -> ImageMetadata | None:
        """Rank unused scraped candidates against the slot. Returns None if the
        best match doesn't clear the threshold — caller falls through to Pexels.

        When `prefer` is given, the page's own images are ranked first; only if
        none of them fit the slot do we consider the rest of the site-wide pool.
        """
        candidates = [
            c for c in self._pool
            if c.url not in self._used_urls and _looks_like_image(c.url)
        ]
        if not candidates:
            return None

        # Page-local first: a confident match among THIS page's images wins over
        # a (possibly bigger) image that belongs to another page.
        if prefer:
            prefer_urls = {c.url for c in prefer}
            local = [c for c in candidates if c.url in prefer_urls]
            if local:
                result = await self._rank(query, intent, local)
                if result.chosen is not None:
                    logger.debug(
                        "Page-local scraped image for '%s' (intent=%s): score=%.2f decision=%s",
                        query, intent, result.chosen_score, result.decision,
                    )
                    return result.chosen
                # No lexical match — but these are the site's real page photos.
                # Pick the largest unexcluded one rather than deferring to stock.
                eligible = [c for c in local if c.role not in {"decoration", "logo"}]
                if eligible:
                    best = max(eligible, key=lambda c: (c.width or 0) * (c.height or 0))
                    logger.debug(
                        "Page-local size-fallback for '%s' (intent=%s): %s",
                        query, intent, best.url,
                    )
                    return best

        result = await self._rank(query, intent, candidates)
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

    async def _rank(self, query: str | None, intent: str, candidates: list[ImageMetadata]):
        """Heuristic ranking, optionally with the bounded LLM tiebreaker."""
        if self._use_llm_tiebreaker:
            return await rank_candidates_with_llm_tiebreaker(query, intent, candidates)
        return rank_candidates(query, intent, candidates)


def _placeholder_photo(
    seed: str,
    orientation: str,
    alt: str | None,
    *,
    primary_hex: str = "#64748b",
    secondary_hex: str = "#1e293b",
    nonce: int = 0,
) -> PhotoResult:
    """Last-resort placeholder: a deterministic two-tone SVG gradient in the
    theme's own brand colours, inlined as a data URI (no network call).

    Replaces the old picsum.photos fallback — a random, unrelated stock photo
    that read as a bug rather than a design choice. This always looks
    intentional, and a given (seed, nonce) always renders the same gradient
    angle so repeat generations are stable. `nonce` distinguishes repeated uses
    of the same seed on one page (nonce 0 == the original, byte-identical).
    """
    key = seed if nonce == 0 else f"{seed}#{nonce}"
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()
    if orientation == "portrait":
        w, h = 600, 800
    elif orientation == "square":
        w, h = 600, 600
    else:
        w, h = 1200, 800
    angle = int(digest[:2], 16) % 360
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">'
        f'<defs><linearGradient id="g" gradientTransform="rotate({angle} 0.5 0.5)">'
        f'<stop offset="0%" stop-color="{primary_hex}"/>'
        f'<stop offset="100%" stop-color="{secondary_hex}"/>'
        f"</linearGradient></defs>"
        f'<rect width="{w}" height="{h}" fill="url(#g)"/>'
        f"</svg>"
    )
    avg_hex = _blend_hex(primary_hex, secondary_hex)
    return PhotoResult(
        url="data:image/svg+xml;utf8," + quote(svg),
        alt=alt or "Decorative gradient",
        photographer=None,
        photographer_url=None,
        source="placeholder",
        avg_color=avg_hex,
        luminance=relative_luminance(avg_hex),
        band=band_for_luminance(relative_luminance(avg_hex)),
    )


def _blend_hex(a: str, b: str) -> str:
    """Midpoint colour between two hex codes — used to estimate the
    placeholder gradient's average luminance for the section-band pass."""
    ar, ag, ab = (int(a.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    br, bg, bb = (int(b.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4))
    return f"#{(ar + br) // 2:02x}{(ag + bg) // 2:02x}{(ab + bb) // 2:02x}"


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
        # first — "office skyline Malaysia" beats a random global skyline,
        # then the industry's own atmospheric default, then a generic
        # abstract texture so even an obscure query has a broad final net
        # before the resolver gives up and falls to the placeholder.
        out: list[str] = []
        if place_cue:
            out.append(f"{query} {place_cue}")
        out.append(query)
        industry_default = _INDUSTRY_CONTEXT_QUERIES.get(industry_category.strip().lower())
        if industry_default and industry_default.lower() != query.lower():
            out.append(industry_default)
        out.append(_GENERIC_ATMOSPHERIC_FALLBACK)
        return out

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
    # Universal safety net: an unmapped industry + a query that hits no token
    # bucket would otherwise leave just `[query]` as the whole chain. One more
    # broad, generic-but-real term beats giving up after a single attempt.
    add("modern professional workspace")
    return out


# Last-resort stock query when nothing more specific is known. Reaches Pexels'
# huge generic catalog instead of dropping straight to the placeholder.
_GENERIC_ATMOSPHERIC_FALLBACK = "modern abstract texture gradient"


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
