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
    _UNPINNABLE_VISION_KINDS,
    bears_text,
    SlotUsage,
    _tokens,
    rank_candidates,
    rank_candidates_with_llm_tiebreaker,
)
from app.services.image_sampling import sample_photo
from app.services.text_detection import verify_one, verify_url
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


def _unfit_for_background(meta: ImageMetadata | None) -> bool:
    """True when a scraped image must not fill a full-bleed BACKGROUND slot
    because it already carries words of its own.

    A background has the section's headline drawn over it. An image that is
    itself a headline — the source's own hero graphic, a promo banner, a price
    list — puts two sets of words in the same space, and no scrim fixes that
    because the problem is the wording, not the contrast.

    Rejecting here is cheap: the resolver falls through to stock for the
    background, and the image stays fully eligible for the inline/featured slots
    that draw nothing on top of it (see `_unfit_for_featured_pin`, which gates
    the opposite direction).
    """
    return bears_text(meta)


def _unfit_for_featured_pin(
    meta: ImageMetadata | None, slot_usage: SlotUsage, *, allow_portrait: bool = False
) -> bool:
    """True when a scraped image the LLM bound via image_ref must not be
    honored as-is for a content section's featured slot.

    A background/texture-style scrape (a CSS background, or a role the
    render-evidence pass tagged "background") may fill a full-bleed
    background pin — that's the source's own art-directed backdrop — but it
    must never win an inline "featured" slot (about, features, services,
    team, gallery, a split-hero side image): it's decorative, not a photo of
    the section's subject. A `background` slot_usage is exempt outright.

    A headshot is rejected on the same grounds unless the slot opted into
    portraits (`allow_portrait`, see image_match.rank_candidates) — a bound
    committee photo on a services card is still the wrong photo, whoever chose
    it. Portraits are checked before the background exemption: a face stretched
    full-bleed is never right.
    """
    if meta is None:
        return False
    if not allow_portrait and (meta.role == "portrait" or meta.vision_portrait):
        return True
    if slot_usage == "background":
        return False
    if meta.role == "background" or meta.source_usage == "css_background":
        return True
    return meta.vision_kind in _UNPINNABLE_VISION_KINDS


class ImageResolver:
    """
    Stateful resolver scoped to one site generation.

    Tracks which scraped images have been used so multiple sections get
    different photos. Keeps a list of attributions to surface later.

    `use_llm_tiebreaker`: when True (default), invoke a tiny LLM judge for
    scraped images whose heuristic match score is in the ambiguous band
    (0.30 – 0.55). Bounded: only fires when at least 2 candidates are within
    0.10 of the top score. Disable for deterministic / fast generations.

    `stock_only`: never return a source photo, whatever the pool holds. The
    mechanism for stock-images-only generation is upstream — the handler hands
    us a SourceContent with no photography, so the pool is empty anyway (see
    services/source_images.py) — but an empty pool does NOT close the pinned
    branch: `resolve(pinned_url=...)` looks the URL up in the pool, finds
    nothing, and every gate then passes a `None` meta, so the pin is honoured
    as `source="scraped"`. This flag closes that, and makes the invariant
    assertable here rather than only across a whole generated tree.
    """

    def __init__(
        self,
        scraped_images: list[str] | None = None,
        scraped_metadata: list[ImageMetadata] | None = None,
        pexels: PexelsClient | None = None,
        use_llm_tiebreaker: bool = True,
        stock_only: bool = False,
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
        self._stock_only = stock_only
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

    async def _reject_text_backgrounds(
        self,
        picked: ImageMetadata | None,
        query: str | None,
        intent: str,
        *,
        prefer: list[ImageMetadata] | None,
        slot_usage: SlotUsage,
        min_long_edge: int,
        allow_portrait: bool,
    ) -> ImageMetadata | None:
        """Re-rank past any winner that turns out to carry its own headline.

        The prefetch screen (services/text_detection) covers a capped sample of
        the pool, which on a multi-page scrape is a small fraction of it — a
        newsletter scan sitting on page nine is exactly what slips through. So
        the image that actually WINS a background slot is screened here, on
        demand, whatever the sample covered.

        Stamping the flag is what removes it: `bears_text` reads it, and
        `rank_candidates` filters on that, so re-ranking simply returns the next
        best candidate. Bounded by `ocr_verify_budget` — each miss costs a
        download plus an inference, and a source whose every image is a text
        graphic should fall through to stock rather than screen the whole pool.
        """
        if slot_usage != "background":
            return picked
        for _ in range(max(0, settings.ocr_verify_budget)):
            if picked is None or not await verify_one(picked):
                return picked
            logger.info(
                "Hero/background candidate %s carries its own text; re-ranking",
                picked.url[:120],
            )
            picked = await self._take_best_scraped(
                query, intent, prefer=prefer, slot_usage=slot_usage,
                min_long_edge=min_long_edge, allow_portrait=allow_portrait,
            )
        return picked

    async def _sampled_fields(
        self, url: str, known_hex: str | None, slot_usage: SlotUsage
    ) -> tuple[float | None, Literal["light", "dark"] | None, float | None]:
        """(luminance, band, focal_y) for a scraped photo, reading pixels only
        when it's worth it.

        Scraped images carry no `dominant_color`, so without this every one of
        them lands on `photo_background`'s blind mid-cast and a dead-centre
        crop. One download buys both the adaptive scrim and the framing — but
        only for full-bleed slots, where the photo covers the viewport and both
        actually show. Inline slots keep the metadata-only path.
        """
        lum, band = _band_fields(known_hex)
        if slot_usage != "background":
            return lum, band, None
        sample = await sample_photo(url)
        if sample is None:
            return lum, band, None
        # A colour the scraper already knew wins — it describes the source's own
        # rendering; ours is a re-read of the same bytes.
        if known_hex:
            return lum, band, sample.focal_y
        return sample.luminance, band_for_luminance(sample.luminance), sample.focal_y

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
        allow_portrait: bool = False,
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

        `allow_portrait`: set only by slots that are ABOUT a person or that
        replay the source's own photos (gallery cells, directory rosters).
        Every other slot excludes scraped headshots — see
        image_match.rank_candidates.
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

        if self._stock_only:
            # A bound source photo is still a source photo. Nulled rather than
            # branched around because everything below already handles "no pin".
            pinned_url = None

        if pinned_url:
            meta = next((c for c in self._pool if c.url == pinned_url), None)
            # Honour the bound photo unless it's unfit for a full-bleed
            # background (too small, a headshot, the wrong shape, or already
            # carrying words of its own), or it's a decorative/background-style
            # scrape being asked to fill an inline featured slot — then fall
            # through so the resolver reaches Pexels for a real photo instead.
            # An LLM image_ref binds by topic and cannot see the picture, so a
            # promo banner captioned "our restaurant" is exactly the kind of
            # pin that arrives here and must not be honoured full-bleed.
            # An LLM image_ref binds by topic and never sees the picture, so a
            # pinned background is screened on demand too — the pin bypasses
            # ranking, and with it every filter that reads the text flag.
            if slot_usage == "background" and meta is not None:
                await verify_one(meta)
            if (
                not _below_hero_bg_min(meta, min_long_edge)
                and not (slot_usage == "background" and _unfit_for_background(meta))
                and not _unfit_for_featured_pin(
                    meta, slot_usage, allow_portrait=allow_portrait
                )
            ):
                self._used_urls.add(pinned_url)
                lum, band, focal_y = await self._sampled_fields(
                    pinned_url, meta.dominant_color if meta else None, slot_usage
                )
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
                    focal_y=focal_y,
                )
            logger.debug(
                "Pinned %s photo %s unfit for slot_usage=%s (size/role/aspect/"
                "decorative, min %dpx); deferring to stock",
                intent, pinned_url, slot_usage, min_long_edge,
            )

        orientation = _INTENT_TO_ORIENTATION[intent]

        # 1. Scraped pool — rank against the slot's image_query
        if not self._stock_only and intent in _SCRAPED_ELIGIBLE_INTENTS:
            picked = await self._take_best_scraped(
                query, intent, prefer=prefer, slot_usage=slot_usage,
                min_long_edge=min_long_edge, allow_portrait=allow_portrait,
            )
            picked = await self._reject_text_backgrounds(
                picked, query, intent,
                prefer=prefer, slot_usage=slot_usage,
                min_long_edge=min_long_edge, allow_portrait=allow_portrait,
            )
            if picked is not None:
                self._used_urls.add(picked.url)
                # Carry the band from the scraper's colour hint when it has one,
                # else read it off the pixels for a full-bleed slot; None for
                # everything else → luminance pass applies the §8.4 light default.
                lum, band, focal_y = await self._sampled_fields(
                    picked.url, picked.dominant_color, slot_usage
                )
                return PhotoResult(
                    url=picked.url,
                    alt=picked.alt or alt_fallback or (query or "Source image"),
                    photographer=None,
                    photographer_url=None,
                    source="scraped",
                    luminance=lum,
                    band=band,
                    focal_y=focal_y,
                )

        # 2. Pexels — locale-cued for people-likely slots, plain-query fallback.
        # Full-bleed slots additionally screen out photographs OF text: the
        # scraped pool is guarded by _reject_text_backgrounds above, and a hero
        # that falls through to stock must not lose that guarantee.
        if query and self._pexels.configured:
            photo = await self._search_pexels(
                query, orientation, intent, screen_text=(slot_usage == "background")
            )
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

    async def _first_text_free(
        self, ranked: list[PhotoResult], budget: int
    ) -> tuple[PhotoResult | None, int]:
        """Best candidate that isn't a photograph OF text. Returns (photo, budget).

        `ranked` is already in preference order (relevance, or colour distance
        for the abstract wash); this walks it and returns the first one the OCR
        screen clears, so rejecting a candidate costs a step down the SAME
        batch rather than another Pexels round-trip.

        The budget bounds how much OCR one slot may spend, not whether the slot
        gets an image: when it runs out we return the next candidate unscreened
        — unjudged, but never one already known to carry text. None means every
        candidate in the batch was screened and every one failed, which says the
        query itself resolves to text ("sheet music", "conference slides"); the
        caller walks on to the next chain query rather than settling.

        A no-op costing nothing when `budget` is 0 — which is every non-
        background slot, and every slot at all when the OCR pass is off.
        """
        for photo in ranked:
            if budget <= 0:
                return photo, budget
            budget -= 1
            if not await verify_url(photo.url):
                return photo, budget
            logger.info(
                "Stock candidate %s is a photograph of text; skipping for background slot",
                photo.url[:120],
            )
        return None, budget

    async def _search_pexels(
        self, query: str, orientation: str, intent: str, *, screen_text: bool = False
    ) -> PhotoResult | None:
        """Search Pexels, preferring a market-cued query for people-likely slots.

        `screen_text` runs each batch's picks through the OCR screen and keeps
        walking past any photograph OF text — set for full-bleed background
        slots only, where our own headline is drawn over the result. It is not
        a stock-quality problem: "sheet music" and "therapist speaking at a
        conference" are on-topic queries that legitimately return printed
        notation and slide walls. See text_detection.verify_url.


        Each chain query fetches a batch and we keep the result whose own alt
        text best matches the slot (Pexels' first hit is often a tangent).
        Already-used photos are skipped so multi-section sites don't repeat;
        falls through the chain when a cue exhausts its results (availability).

        A batch's top pick is only accepted when its alt text has REAL
        relevance to either the slot's own query (best case: it matches the
        actual topic) OR the chain candidate that was searched for (the
        industry/contextual fallback entries are deliberately DIFFERENT
        wording from the slot query — e.g. "restaurant interior food
        service" for a slot query like "our signature experience" — so
        checking only against the slot query would wrongly reject an
        on-target fallback hit). Zero relevance to BOTH means Pexels handed
        back a genuine tangent unrelated to anything we searched for, so we
        keep walking the chain instead of returning it. The chain's final
        entry is a deliberately broad, always-real term (see
        `_stock_query_chain`), so it's accepted even at zero relevance rather
        than falling all the way to the gradient placeholder.

        A batch is also screened for vibe before ranking: any candidate whose
        alt text reads as sad/angry/distressed/chaotic (`_has_negative_vibe`)
        is dropped, so a downbeat tangent never wins just for sharing a
        keyword. If that screen would empty the whole batch (all 15 results
        happened to read negative — rare, the wordlist is narrow), we fall
        back to the unfiltered batch rather than dead-ending the chain here.
        """
        chain = _stock_query_chain(
            query, intent, self._market_cue, self._industry_category, self._place_cue
        )
        budget = settings.ocr_verify_budget if screen_text else 0
        for i, candidate in enumerate(chain):
            photos = await self._pexels.search_many(candidate, orientation=orientation)
            fresh = [p for p in photos if p.url not in self._seen_pexels_urls]
            if not fresh:
                continue
            upbeat = [p for p in fresh if not _has_negative_vibe(p.alt)]
            pool = upbeat or fresh
            # sorted(reverse=True) is stable, so with screening off this picks
            # exactly what max() picked, ties included.
            ranked = sorted(
                pool,
                key=lambda p: _stock_relevance(p, query, self._market_cue),
                reverse=True,
            )
            best, budget = await self._first_text_free(ranked, budget)
            if best is None:
                continue  # whole batch was text; try the next chain query
            relevant = (
                _stock_relevance(best, query, self._market_cue) > 0
                or _stock_relevance(best, candidate, self._market_cue) > 0
            )
            if relevant or i == len(chain) - 1:
                return best
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
        # Every caller of this renders the result full-bleed with text over it,
        # so the OCR screen applies unconditionally here — an "abstract" query
        # returns typographic poster art often enough to matter.
        budget = settings.ocr_verify_budget
        reuse_pool: list[PhotoResult] = []
        for candidate in chain:
            photos = await self._pexels.search_many(candidate, orientation=orientation)
            colored = [p for p in photos if p.avg_color]
            fresh = [p for p in colored if p.url not in self._seen_pexels_urls]
            if fresh:
                ranked = sorted(
                    fresh, key=lambda p: color_distance(p.avg_color, color_target_hex)
                )
                best, budget = await self._first_text_free(ranked, budget)
                if best is not None:
                    self._seen_pexels_urls.add(best.url)
                    lum, band = _band_fields(best.avg_color)
                    return replace(best, luminance=lum, band=band, is_abstract=True)
            reuse_pool.extend(colored)

        if reuse_pool:
            # Every candidate here is already in _seen_pexels_urls — if one
            # weren't, it would have been in `fresh` above and returned
            # already — so this is purely a dedup-exhaustion fallback, not a
            # first use. No need to re-add it to _seen_pexels_urls.
            # Screened like the fresh path: mostly free, since the verdict for
            # anything already judged above is in the URL cache.
            ranked = sorted(
                reuse_pool, key=lambda p: color_distance(p.avg_color, color_target_hex)
            )
            best, budget = await self._first_text_free(ranked, budget)
            if best is None:
                return None
            logger.debug(
                "resolve_abstract_bg: fresh Pexels pool exhausted for '%s' (dedup) "
                "— reusing already-seen colour-matched candidate %s",
                query, best.url,
            )
            lum, band = _band_fields(best.avg_color)
            return replace(best, luminance=lum, band=band, is_abstract=True)

        return None

    async def _take_best_scraped(
        self,
        query: str | None,
        intent: str,
        *,
        prefer: list[ImageMetadata] | None = None,
        slot_usage: SlotUsage = "any",
        min_long_edge: int = 0,
        allow_portrait: bool = False,
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
            # Words already in the picture disqualify it from a slot that draws
            # our headline over it — but only from that slot.
            and not (slot_usage == "background" and _unfit_for_background(c))
        ]
        if not candidates:
            return None

        # Page-local first: a confident match among THIS page's images wins over
        # a (possibly bigger) image that belongs to another page.
        if prefer:
            prefer_urls = {c.url for c in prefer}
            local = [c for c in candidates if c.url in prefer_urls]
            if local:
                result = await self._rank(
                    query, intent, local, slot_usage=slot_usage,
                    allow_portrait=allow_portrait,
                )
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
                if (
                    not allow_portrait
                    or slot_usage == "background"
                    or intent in ("hero", "about")
                ):
                    eligible = [
                        c for c in eligible
                        if c.role != "portrait" and not c.vision_portrait
                    ]
                if eligible:
                    best = max(eligible, key=lambda c: (c.width or 0) * (c.height or 0))
                    logger.debug(
                        "Page-local size-fallback for '%s' (intent=%s): %s",
                        query, intent, best.url,
                    )
                    return best

        result = await self._rank(
            query, intent, candidates, slot_usage=slot_usage,
            allow_portrait=allow_portrait,
        )
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
        allow_portrait: bool = False,
    ):
        """Heuristic ranking, optionally with the bounded LLM tiebreaker."""
        if self._use_llm_tiebreaker:
            return await rank_candidates_with_llm_tiebreaker(
                query, intent, candidates, slot_usage=slot_usage,
                allow_portrait=allow_portrait,
            )
        return rank_candidates(
            query, intent, candidates, slot_usage=slot_usage,
            allow_portrait=allow_portrait,
        )

    def strongest_source_background(self, min_dim: int = 900) -> ImageMetadata | None:
        """The best unused image the SOURCE site used as a CSS background, or
        None. Drives the hero director: when the source led with a full-bleed
        background, the generated homepage should too (and pin that image).

        Guards against pinning a tiny tiled texture full-screen: known
        dimensions must reach `min_dim` on the long edge; unknown dimensions
        are accepted only when render evidence shows near-viewport coverage.

        Also refuses a background that carries its own wording. The source may
        well have laid live HTML text over it, but we would be laying OUR
        headline over a picture that already reads as one — and this is the
        likeliest place to meet such an image, since a site's own hero graphic
        is exactly what a CSS background scrape returns.
        """
        def _qualifies(c: ImageMetadata) -> bool:
            if c.source_usage != "css_background" or c.url in self._used_urls:
                return False
            if not _looks_like_image(c.url):
                return False
            if _unfit_for_background(c):
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


def monogram_avatar_url(
    name: str,
    *,
    primary_hex: str = "#64748b",
    secondary_hex: str = "#1e293b",
) -> str:
    """A person's initials on the brand gradient, as a data URI.

    Used instead of a stock portrait when a real, named team member has no
    scraped photo. A Pexels stranger's face captioned with a real employee's
    name is a misattribution — the worst failure mode this section has — so the
    card says "no photo" in a way that still looks designed. No network call.
    """
    initials = "".join(word[0] for word in name.split()[:2] if word).upper() or "?"
    angle = int(hashlib.md5(name.encode("utf-8")).hexdigest()[:2], 16) % 360
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="600" height="600" '
        'viewBox="0 0 600 600">'
        f'<defs><linearGradient id="g" gradientTransform="rotate({angle} 0.5 0.5)">'
        f'<stop offset="0%" stop-color="{primary_hex}"/>'
        f'<stop offset="100%" stop-color="{secondary_hex}"/>'
        "</linearGradient></defs>"
        '<rect width="600" height="600" fill="url(#g)"/>'
        '<text x="50%" y="50%" dy="0.35em" text-anchor="middle" '
        'font-family="Helvetica,Arial,sans-serif" font-size="240" '
        f'font-weight="600" fill="#ffffff" fill-opacity="0.92">{initials}</text>'
        "</svg>"
    )
    return "data:image/svg+xml;utf8," + quote(svg)


def _placeholder_photo(
    seed: str,
    orientation: str,
    alt: str | None,
    *,
    primary_hex: str = "#64748b",
    secondary_hex: str = "#1e293b",
    nonce: int = 0,
) -> PhotoResult:
    """Last-resort placeholder: a deterministic brand-coloured SVG, inlined as a
    data URI (no network call).

    Replaces the old picsum.photos fallback — a random, unrelated stock photo
    that read as a bug rather than a design choice. This always looks
    intentional, and a given (seed, nonce) always renders the same image so
    repeat generations are stable. `nonce` distinguishes repeated uses of the
    same seed on one page.

    A flat two-stop ramp is what a "no image" fallback looks like; an aurora of
    offset radial hotspots over that ramp, finished with a grain wash, is what a
    designed background looks like — the same construction the theme's own
    section backgrounds use (services/style_tokens.mesh_gradient / grain_data_uri),
    rebuilt here in SVG because this slot needs a single self-contained image URL
    rather than a stack of CSS layers. It carries the brand hue either way, so
    nothing about slot matching or the luminance band changes.
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
    # Lighter sibling of the brand hue — the aurora needs a third tone to read
    # as depth rather than as two flat washes meeting.
    glow_hex = _blend_hex(primary_hex, "#ffffff")
    # Hotspot placement is seeded too, so two placeholders on one page differ in
    # composition and not just gradient angle.
    spots = [
        (int(digest[i : i + 2], 16) % 100, int(digest[i + 2 : i + 4], 16) % 100)
        for i in (2, 6, 10, 14)
    ]
    tones = (primary_hex, glow_hex, secondary_hex, glow_hex)
    alphas = (0.55, 0.40, 0.45, 0.30)
    radii = (62, 54, 58, 48)
    hotspots = "".join(
        f'<radialGradient id="s{i}" cx="{cx}%" cy="{cy}%" r="{r}%">'
        f'<stop offset="0%" stop-color="{tone}" stop-opacity="{a}"/>'
        f'<stop offset="100%" stop-color="{tone}" stop-opacity="0"/>'
        "</radialGradient>"
        for i, ((cx, cy), tone, a, r) in enumerate(zip(spots, tones, alphas, radii))
    )
    layers = "".join(
        f'<rect width="{w}" height="{h}" fill="url(#s{i})"/>' for i in range(len(spots))
    )
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
        f'viewBox="0 0 {w} {h}">'
        f'<defs><linearGradient id="g" gradientTransform="rotate({angle} 0.5 0.5)">'
        f'<stop offset="0%" stop-color="{primary_hex}"/>'
        f'<stop offset="100%" stop-color="{secondary_hex}"/>'
        f"</linearGradient>{hotspots}"
        "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.8' "
        "numOctaves='2' stitchTiles='stitch'/>"
        "<feColorMatrix type='saturate' values='0'/></filter>"
        f"</defs>"
        f'<rect width="{w}" height="{h}" fill="url(#g)"/>'
        f"{layers}"
        f'<rect width="{w}" height="{h}" filter="url(#n)" opacity="0.09"/>'
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


# Alt-text vibe screening. Pexels has no sentiment/mood search parameter —
# search is plain keyword text — so tone can only be enforced downstream, on
# whatever a batch actually returns. Deliberately narrow: only emotion/outcome
# words (crying, chaos, abandoned), never domain nouns (hospital, patient) —
# a healthcare or legal business's own legitimate subject matter must never
# be screened out just because its industry sounds serious.
_NEGATIVE_VIBE_WORDS = frozenset({
    "sad", "crying", "cry", "tears", "angry", "anger", "furious", "fight",
    "fighting", "argument", "arguing", "conflict", "violence", "violent",
    "war", "protest", "riot", "funeral", "grief", "grieving", "mourning",
    "death", "dying", "dead", "sick", "illness", "emergency", "accident",
    "crash", "disaster", "injury", "injured", "wound", "blood", "pain",
    "suffering", "despair", "hopeless", "depressed", "depression", "anxiety",
    "anxious", "stressed", "stress", "exhausted", "tired", "bored", "boring",
    "lonely", "alone", "isolated", "abandoned", "crime", "criminal",
    "arrest", "prison", "jail", "weapon", "danger", "dangerous", "threat",
    "scared", "fear", "afraid", "panic", "chaos", "destroyed", "broken",
    "damaged", "poverty", "homeless", "hungry", "starving", "bankrupt",
    "evicted", "fired", "layoff", "dirty", "filthy", "decay", "garbage",
})
_POSITIVE_VIBE_WORDS = frozenset({
    "smiling", "smile", "happy", "happiness", "joy", "joyful", "cheerful",
    "laughing", "laughter", "celebrating", "celebration", "excited",
    "confident", "thriving", "success", "successful", "welcoming", "warm",
    "friendly", "bright", "vibrant", "relaxed", "content", "proud",
    "grateful", "fun", "playful", "energetic", "positive", "optimistic",
    "hopeful", "cheer", "cheering", "delighted", "enthusiastic",
})


def _has_negative_vibe(alt: str) -> bool:
    return bool(_tokens(alt) & _NEGATIVE_VIBE_WORDS)


def _stock_relevance(photo: PhotoResult, query: str, market_cue: str) -> float:
    """Token overlap between a stock photo's own alt text and the slot query.

    Used to re-rank a Pexels result batch — the API's first hit is often a
    tangent ("dental clinic" → toothbrush macro). A small bonus rewards alts
    that mention the audience region, so on-market imagery wins ties. A
    smaller bonus rewards an upbeat-described alt, so a cheerful/confident
    photo wins a tie over a flat one — negative-vibe photos are excluded
    entirely upstream in `_search_pexels`, not merely down-weighted here.
    """
    alt_tokens = _tokens(photo.alt)
    if not alt_tokens:
        return 0.0
    q_tokens = _tokens(query)
    score = len(q_tokens & alt_tokens) / len(q_tokens) if q_tokens else 0.0
    cue_tokens = _tokens(market_cue)
    if cue_tokens and cue_tokens & alt_tokens:
        score += 0.25
    if alt_tokens & _POSITIVE_VIBE_WORDS:
        score += 0.15
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

    # Positive-vibe search bias: try an upbeat-qualified variant before the
    # plain query, so Pexels' own results lean cheerful rather than relying
    # solely on downstream re-ranking. Avatars (a person's own face) always
    # read fine as "smiling", any industry. For scene-level people intents,
    # only bias industries where a jovial qualifier fits the brand's own
    # tone (restaurant, childcare, ecommerce, agency, nonprofit) — forcing it
    # onto a professional-services/consultancy/saas query risks fighting a
    # brand that wants to read as composed and serious, not jolly.
    if intent == "avatar":
        add(f"{query} smiling")
    elif industry_category.strip().lower() in _UPBEAT_INDUSTRIES:
        add(f"{query} joyful")

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


# Industries whose own brand tone welcomes a jovial stock-search qualifier
# ("joyful") without fighting a more serious/composed positioning. See the
# positive-vibe search bias in _stock_query_chain.
_UPBEAT_INDUSTRIES = frozenset({
    "restaurant", "childcare", "ecommerce", "agency", "nonprofit",
})


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
