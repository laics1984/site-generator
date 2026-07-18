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

import asyncio
import hashlib
import logging
from dataclasses import replace
from typing import Literal
from urllib.parse import quote

import httpx

from app.config import settings
from app.models.content_blocks import ImageMetadata
from app.services.image_match import (
    SlotUsage,
    _tokens,
    rank_candidates,
    rank_candidates_with_llm_tiebreaker,
)
from app.services.image_styling import (
    band_for_luminance,
    color_distance,
    relative_luminance,
)
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

# Intents where scraped imagery is plausible. Avatars always skip the scraped
# pool (Pexels portraits beat a brand brochure's random pages). CTA backgrounds
# ARE eligible: on photo-rich sources the section background should be the
# business's own photo, not an anonymous stock atmosphere.
_SCRAPED_ELIGIBLE_INTENTS: frozenset[str] = frozenset(
    {"hero", "about", "generic", "feature", "cta_bg"}
)

# Intents likely to depict people, where a market/locale cue ("Southeast Asian …")
# keeps stock imagery on-audience. Atmospheric CTA backgrounds / logos are skipped.
_PEOPLE_INTENTS: frozenset[str] = frozenset({"hero", "about", "avatar", "feature", "generic"})


def _below_hero_bg_min(meta: ImageMetadata | None, min_long_edge: int) -> bool:
    """True when a scraped image is unfit to fill a full-bleed background:
    too small, a measured grid headshot, or the wrong shape for a wide band.

    Unknown dimensions pass (return False): CSS-background URLs frequently omit
    size and the source clearly used them full-bleed, so we reject on measured
    evidence, not missing data. The role veto is the evidence-based rejection
    that still catches a high-resolution headshot the size rule would pass.
    A ``min_long_edge`` of 0 disables the gate (non-background slots).
    """
    if min_long_edge <= 0 or meta is None:
        return False
    # A grid headshot is never a background, whatever its resolution.
    if meta.role == "portrait":
        return True
    long_edge = max(meta.width or 0, meta.height or 0)
    if 0 < long_edge < min_long_edge:
        return True
    # Shape gate: an inline square-ish/portrait-orientation photo can't fill a
    # wide band without an awkward crop. CSS backgrounds are exempt — the
    # source composed them full-bleed already.
    if (
        meta.width
        and meta.height
        and meta.source_usage != "css_background"
        and meta.width / meta.height < settings.hero_bg_min_aspect
    ):
        return True
    return False


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

    def mark_used(self, urls: set[str] | list[str]) -> None:
        """Reserve scraped URLs already placed by the ref-binding pass
        (services/image_refs.py), so slot resolution won't re-pick them and
        render the same photo twice on a page."""
        self._used_urls.update(urls)

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
        slot_usage: SlotUsage = "any",
        pinned_url: str | None = None,
    ) -> PhotoResult:
        """Returns a usable PhotoResult. Always succeeds — the gradient placeholder is the final fallback.

        `prefer`: images that belong to the page being rendered. They're ranked
        ahead of the rest of the site-wide pool so a page's hero/section uses the
        photo the source actually placed on THAT page, not the biggest one
        anywhere on the site. Falls through to the full pool when none of the
        page's own images fit the slot.

        `slot_usage`: how the slot renders the image (see image_match.SlotUsage).
        'inline' keeps source CSS backgrounds out of side/featured slots;
        'background' pins them first for full-bleed slots.

        `pinned_url`: a scraped photo the LLM already bound to this slot via
        image_ref (services/image_refs.py). It wins outright — no ranking, no
        stock fallback — because the source page actually used this photo for
        this section.
        """
        # A full-bleed slot stretches its photo edge-to-edge (background-size:
        # cover), so a small scraped source image softens when upscaled. For any
        # background slot, require a minimum long edge; too-small candidates are
        # skipped so Pexels supplies a crisp full-size photo instead (§ below).
        # Heroes are taller/full-viewport, so they demand a larger minimum than
        # ordinary section bands.
        if slot_usage == "background":
            min_long_edge = (
                settings.hero_min_background_dim
                if intent == "hero"
                else settings.section_min_background_dim
            )
        else:
            min_long_edge = 0

        if pinned_url:
            meta = next((c for c in self._pool if c.url == pinned_url), None)
            # Honour the bound photo unless it's unfit for a full-bleed
            # background (too small, a headshot, or the wrong shape) — then
            # fall through so the resolver reaches Pexels for a crisp shot.
            if not _below_hero_bg_min(meta, min_long_edge):
                self._used_urls.add(pinned_url)
                lum, band = _band_fields(meta.dominant_color if meta else None)
                return PhotoResult(
                    url=pinned_url,
                    alt=(meta.alt if meta and meta.alt else None)
                    or alt_fallback
                    or (query or "Source image"),
                    photographer=None,
                    photographer_url=None,
                    source="scraped",
                    luminance=lum,
                    band=band,
                )
            logger.debug(
                "Pinned %s background %s unfit for full-bleed (size/role/aspect, "
                "min %dpx); deferring to stock",
                intent, pinned_url, min_long_edge,
            )

        orientation = _INTENT_TO_ORIENTATION[intent]

        # 1. Scraped pool — rank against the slot's image_query
        if intent in _SCRAPED_ELIGIBLE_INTENTS:
            picked = await self._take_best_scraped(
                query, intent, prefer=prefer, slot_usage=slot_usage,
                min_long_edge=min_long_edge,
            )
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

    async def prewarm_stock(
        self,
        slots: list[tuple[str | None, ImageIntent]],
        *,
        concurrency: int = 6,
    ) -> None:
        """Concurrently warm the Pexels result cache for every query the render
        will request, so the (serial, order-dependent) render path hits a hot
        cache instead of waiting on a network round-trip per slot.

        Output-identical: this only pre-fetches into the shared per-query cache
        (services/pexels.py). Selection, dedup order and rotation are untouched
        — they still run in the render loop exactly as before. A slot we miss
        here simply resolves live during render, as today.

        No-op unless Pexels is configured. Errors are swallowed: pre-warming is
        best-effort and never blocks generation.
        """
        if not self._pexels.configured:
            return

        # Expand each slot into its full stock-query chain (the render's own
        # fallback chain) and de-dupe by (query, orientation) so we issue each
        # API call once. Mirrors _search_pexels' chain + orientation choice.
        wanted: dict[tuple[str, str], None] = {}
        for query, intent in slots:
            if not query:
                continue
            orientation = _INTENT_TO_ORIENTATION[intent]
            chain = _stock_query_chain(
                query,
                intent,
                self._market_cue,
                self._industry_category,
                self._place_cue,
            )
            for candidate in chain:
                wanted[(candidate, orientation)] = None
        if not wanted:
            return

        sem = asyncio.Semaphore(max(1, concurrency))

        async def _warm(q: str, orientation: str, client: httpx.AsyncClient) -> None:
            async with sem:
                try:
                    await self._pexels.search_many(
                        q, orientation=orientation, client=client  # type: ignore[arg-type]
                    )
                except Exception:  # noqa: BLE001 — pre-warm is advisory only
                    logger.debug("Pexels pre-warm failed for %r", q, exc_info=True)

        async with httpx.AsyncClient(timeout=settings.pexels_timeout_seconds) as client:
            await asyncio.gather(
                *(_warm(q, orientation, client) for (q, orientation) in wanted)
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

    async def resolve_abstract_bg(
        self,
        query: str,
        *,
        color_target_hex: str,
        intent: ImageIntent = "cta_bg",
    ) -> PhotoResult | None:
        """Resolve an abstract background photo whose dominant colour sits CLOSEST
        to ``color_target_hex`` (the theme), rather than by text relevance.

        Used for the split-hero wash and the photoless full-bleed hero: the image
        reads as on-brand texture, so colour match matters more than topical
        relevance. Returns the nearest-colour genuine Pexels photo, or None when
        Pexels is unconfigured / the chain returns nothing / nothing returned has
        a usable average colour (the caller then decides whether to fall back to
        a flat band or a gradient hero — we never substitute a random off-colour
        stock photo here).

        Every hero/wash on a site can share this exact query (see
        schema_builder._abstract_theme_query — one deterministic, theme-coloured
        phrase for the whole site), so the ``_seen_pexels_urls`` dedup can
        exhaust the fresh pool for a page processed late in generation even
        though Pexels still has plenty of on-colour results left. Unlike a
        distinct photographic subject, reusing an abstract/atmospheric texture
        across two pages' hero backgrounds isn't the kind of visible duplication
        dedup exists to prevent — so when no chain query has a FRESH (unseen)
        match, we fall back to the best colour match across every candidate
        already fetched in this call, seen or not, instead of returning None
        and silently degrading the hero to a flat colour.
        """
        if not (query and self._pexels.configured):
            return None
        orientation = _INTENT_TO_ORIENTATION[intent]
        chain = _stock_query_chain(
            query, intent, self._market_cue, self._industry_category, self._place_cue
        )
        reuse_pool: list[PhotoResult] = []
        for candidate in chain:
            photos = await self._pexels.search_many(candidate, orientation=orientation)
            colored = [p for p in photos if p.avg_color]
            fresh = [p for p in colored if p.url not in self._seen_pexels_urls]
            if fresh:
                best = min(
                    fresh, key=lambda p: color_distance(p.avg_color, color_target_hex)
                )
                self._seen_pexels_urls.add(best.url)
                lum, band = _band_fields(best.avg_color)
                return replace(best, luminance=lum, band=band)
            reuse_pool.extend(colored)

        if reuse_pool:
            # Every candidate here is already in _seen_pexels_urls — if one
            # weren't, it would have been in `fresh` above and returned
            # already — so this is purely a dedup-exhaustion fallback, not a
            # first use. No need to re-add it to _seen_pexels_urls.
            best = min(
                reuse_pool, key=lambda p: color_distance(p.avg_color, color_target_hex)
            )
            logger.debug(
                "resolve_abstract_bg: fresh Pexels pool exhausted for '%s' (dedup) "
                "— reusing already-seen colour-matched candidate %s",
                query, best.url,
            )
            lum, band = _band_fields(best.avg_color)
            return replace(best, luminance=lum, band=band)

        return None

    async def _take_best_scraped(
        self,
        query: str | None,
        intent: str,
        *,
        prefer: list[ImageMetadata] | None = None,
        slot_usage: SlotUsage = "any",
        min_long_edge: int = 0,
    ) -> ImageMetadata | None:
        """Rank unused scraped candidates against the slot. Returns None if the
        best match doesn't clear the threshold — caller falls through to Pexels.

        When `prefer` is given, the page's own images are ranked first; only if
        none of them fit the slot do we consider the rest of the site-wide pool.

        `min_long_edge` (>0 for the full-bleed hero background) drops candidates
        whose known dimensions are too small to fill it without softening, so the
        size fallback and ranker never pick a photo the hero would have to upscale.
        """
        candidates = [
            c for c in self._pool
            if c.url not in self._used_urls and _looks_like_image(c.url)
            and not _below_hero_bg_min(c, min_long_edge)
        ]
        if not candidates:
            return None

        # Page-local first: a confident match among THIS page's images wins over
        # a (possibly bigger) image that belongs to another page.
        if prefer:
            prefer_urls = {c.url for c in prefer}
            local = [c for c in candidates if c.url in prefer_urls]
            if local:
                result = await self._rank(query, intent, local, slot_usage=slot_usage)
                if result.chosen is not None:
                    logger.debug(
                        "Page-local scraped image for '%s' (intent=%s): score=%.2f decision=%s",
                        query, intent, result.chosen_score, result.decision,
                    )
                    return result.chosen
                # No lexical match — but these are the site's real page photos.
                # Pick the largest unexcluded one rather than deferring to stock.
                # This bypasses the ranker, so the slot-usage and featured-slot
                # gates must be re-applied: a source CSS background never fills
                # an inline slot, and a grid headshot (role=portrait) never
                # fills a hero/about slot or any background — on a directory
                # page the biggest image by area IS a headshot.
                eligible = [c for c in local if c.role not in {"decoration", "logo"}]
                if slot_usage == "inline":
                    eligible = [c for c in eligible if c.source_usage != "css_background"]
                if slot_usage == "background" or intent in ("hero", "about"):
                    eligible = [c for c in eligible if c.role != "portrait"]
                if eligible:
                    best = max(eligible, key=lambda c: (c.width or 0) * (c.height or 0))
                    logger.debug(
                        "Page-local size-fallback for '%s' (intent=%s): %s",
                        query, intent, best.url,
                    )
                    return best

        result = await self._rank(query, intent, candidates, slot_usage=slot_usage)
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

    async def _rank(
        self,
        query: str | None,
        intent: str,
        candidates: list[ImageMetadata],
        *,
        slot_usage: SlotUsage = "any",
    ):
        """Heuristic ranking, optionally with the bounded LLM tiebreaker."""
        if self._use_llm_tiebreaker:
            return await rank_candidates_with_llm_tiebreaker(
                query, intent, candidates, slot_usage=slot_usage
            )
        return rank_candidates(query, intent, candidates, slot_usage=slot_usage)

    def strongest_source_background(self, min_dim: int = 900) -> ImageMetadata | None:
        """The best unused image the SOURCE site used as a CSS background, or
        None. Drives the hero director: when the source led with a full-bleed
        background, the generated homepage should too (and pin that image).

        Guards against pinning a tiny tiled texture full-screen: known
        dimensions must reach `min_dim` on the long edge; unknown dimensions
        are accepted only when render evidence shows near-viewport coverage.
        """
        def _qualifies(c: ImageMetadata) -> bool:
            if c.source_usage != "css_background" or c.url in self._used_urls:
                return False
            if not _looks_like_image(c.url):
                return False
            if c.role not in {"hero", "background", "unknown"} and c.intent != "hero":
                return False
            long_edge = max(c.width or 0, c.height or 0)
            if long_edge:
                return long_edge >= min_dim
            # CSS bg URLs often carry no dimensions — trust hero-grade signals
            # (measured hero/background role or promoted hero intent) instead.
            return c.role in {"hero", "background"} or c.intent == "hero"

        qualifying = [c for c in self._pool if _qualifies(c)]
        if not qualifying:
            return None
        return max(qualifying, key=lambda c: (c.width or 0) * (c.height or 0))


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
    "childcare": "children playing learning kindergarten classroom",
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
    # Childcare before the generic school bucket: candid children-at-play beats
    # an empty "learning space" — the design brief bans empty classrooms.
    if tokens & {
        "kindergarten", "preschool", "childcare", "daycare", "nursery",
        "montessori", "toddler", "toddlers", "children", "kids",
    }:
        return "children playing learning kindergarten"
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
