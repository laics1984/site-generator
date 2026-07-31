"""
Hybrid image scorer: rank scraped images against a slot's image_query.

Used by ImageResolver to decide whether a scraped image (from a URL scrape or a
document upload) fits the LLM's requested visual for a given slot — or whether
we should fall through to Pexels for a more topical stock photo.

Strategy
--------
Each candidate gets a score in [0.0, 1.0] built from three signals:

  lexical (0–0.6):  token overlap between the LLM's image_query and the image's
                    alt text + URL path tokens. Stop-word filtered.
  intent  (0–0.2):  scraper's intent guess matches the slot's intent (hero/about).
  size    (0–0.2):  prefer larger declared dimensions; penalise <400px.

Decision band
-------------
  >= 0.62          confident match — use this candidate, skip the LLM judge
  0.30 – 0.62      ambiguous — optionally invoke the LLM judge to break ties
  <  0.30          poor fit — return None so the resolver falls through to Pexels

(Raised from 0.55 — that let weak/stale scraped matches on non-primary slots
auto-win over a more topical stock photo. hero/about still bypass this via
intent-pinning below, so the raise only affects features/team/gallery/etc.)

The LLM judge is a tiebreaker, not a per-slot evaluator. It only fires when:
- best score is in the ambiguous band, AND
- there are at least 2 candidates within 0.10 of the best (a real tie)

That keeps the extra LLM calls bounded — typically 0–2 per site.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlparse

from app.models.content_blocks import ImageMetadata

if TYPE_CHECKING:
    # Heavy import chain (llm -> config -> pydantic_settings/httpx). Kept lazy so
    # the pure, deterministic ranking logic can be imported + unit-tested alone.
    from app.services.llm import LlmClient

logger = logging.getLogger(__name__)


# --- tokenisation ---------------------------------------------------------------


_STOP_WORDS = frozenset(
    [
        "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
        "has", "have", "in", "is", "it", "its", "of", "on", "or", "our",
        "that", "the", "to", "was", "were", "with", "we", "you", "your",
        "this", "they", "their", "us", "us", "but", "not", "no", "yes",
        "photo", "image", "picture", "img", "shot", "view",
    ]
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    if not text:
        return set()
    return {
        t for t in _TOKEN_RE.findall(text.lower())
        if len(t) >= 3 and t not in _STOP_WORDS
    }


def _path_tokens(url: str) -> set[str]:
    """URL path tokens — many CDNs use slug-style filenames like /coffee-beans-roasting.jpg."""
    if not url or url.startswith("data:"):
        return set()
    try:
        parsed = urlparse(url)
    except ValueError:
        return set()
    return _tokens(parsed.path)


# --- scoring --------------------------------------------------------------------


@dataclass
class ImageScore:
    candidate: ImageMetadata
    score: float
    lexical: float
    intent_bonus: float
    size_bonus: float
    matched_tokens: tuple[str, ...]


def _lexical_score(query: str, candidate: ImageMetadata) -> tuple[float, tuple[str, ...]]:
    """Jaccard-style overlap between query tokens and (alt + url path +
    vision caption + source context heading/caption) tokens.

    The vision caption (when the opt-in pass ran) is what rescues authentic
    photos with hashed CDN filenames and empty alt text — without it they
    have zero lexical evidence and always lose to stock. The context heading
    (the heading the image sat under on the source page) plays the same role
    on the render path: it's free section-topic evidence.

    Returns (score in [0, 0.6], matched tokens).
    """
    q_tokens = _tokens(query)
    if not q_tokens:
        return 0.0, ()
    c_tokens = (
        _tokens(candidate.alt)
        | _path_tokens(candidate.url)
        | _tokens(candidate.vision_caption or "")
        | _tokens(candidate.context_heading)
        | _tokens(candidate.caption)
    )
    if not c_tokens:
        return 0.0, ()
    matched = q_tokens & c_tokens
    if not matched:
        return 0.0, ()
    # Jaccard-ish, capped at 0.6 so lexical alone can't claim a confident match.
    raw = len(matched) / max(1, len(q_tokens))
    return min(0.6, raw * 0.6 / 0.4), tuple(sorted(matched))


def _intent_bonus(slot_intent: str, candidate_intent: str) -> float:
    """0.2 if intents match exactly. 0.1 if candidate is 'generic' (versatile).
    0.0 otherwise. Penalises mismatches like avatar slot getting a logo image.
    """
    if slot_intent == candidate_intent:
        return 0.2
    if candidate_intent == "generic":
        return 0.1
    return 0.0


def _size_bonus(candidate: ImageMetadata) -> float:
    """Reward larger declared sizes; 0 if unknown; negative if obviously tiny."""
    w, h = candidate.width, candidate.height
    if not w and not h:
        return 0.05  # unknown — small neutral credit
    max_dim = max(w or 0, h or 0)
    if max_dim < 200:
        return -0.1
    if max_dim < 400:
        return 0.0
    if max_dim < 800:
        return 0.1
    return 0.2


def score_candidate(
    query: str, slot_intent: str, candidate: ImageMetadata
) -> ImageScore:
    lexical, matched = _lexical_score(query, candidate)
    intent_b = _intent_bonus(slot_intent, candidate.intent)
    size_b = _size_bonus(candidate)
    total = max(0.0, min(1.0, lexical + intent_b + size_b))
    return ImageScore(
        candidate=candidate,
        score=total,
        lexical=lexical,
        intent_bonus=intent_b,
        size_bonus=size_b,
        matched_tokens=matched,
    )


# --- main entry: rank + decide --------------------------------------------------


CONFIDENT_THRESHOLD = 0.62
AMBIGUOUS_THRESHOLD = 0.30
TIE_BAND = 0.10

# Measured roles that can never fill a content slot, whatever their lexical
# score. The scraper already drops decorations on the render path; this gate
# also covers doc-upload and legacy callers that build ImageMetadata directly.
_EXCLUDED_ROLES = frozenset({"decoration", "logo"})

# Vision kinds that must never be intent-pinned as the site's authentic
# hero/about visual: a promo banner or UI screenshot in the hero slot is the
# classic scrape failure. They stay rankable for ordinary slots (a screenshot
# is legitimate for a SaaS feature card) — this only vetoes the pin.
_UNPINNABLE_VISION_KINDS = frozenset({"logo", "banner", "screenshot", "graphic", "map"})

# --- words burned into the pixels ------------------------------------------------
#
# A full-bleed background has OUR headline drawn over it. If the image already
# carries a headline, tagline or price list of its own, the two collide: two
# sets of words at two sizes fighting for the same space, and no scrim can fix
# it because the problem is the words, not the contrast. The source's own hero
# graphic is the usual offender — a scrape faithfully picks it up precisely
# because the source used it full-bleed.
#
# Such an image is NOT unusable. With nothing drawn over it, it reads fine as a
# featured/side image or as an untitled card, so these signals bar the
# BACKGROUND slot only and leave inline slots alone.

# Kinds that carry wording by definition (see image_vision._JUDGE_SYSTEM):
# a banner IS "promotional graphic with overlaid text", a screenshot is UI
# chrome and labels, a logo is a wordmark, a map is place names. `graphic` is
# excluded — an illustration or pattern often has no words at all.
_TEXT_BEARING_VISION_KINDS = frozenset({"logo", "banner", "screenshot", "map"})

# Filename/alt tokens naming an artwork whose whole purpose is to carry a
# message. Deliberately high-precision, not exhaustive: this is the only signal
# available when the vision pass is off (settings.llm_vision_model unset), and a
# false positive costs a real photo its hero.
#
# Excluded on purpose, despite sounding right: "banner", "hero", "header",
# "cover", "promo". In web filenames those describe a PLACEMENT — a wide image
# at the top of a page — far more often than they describe text artwork, so
# banner.jpg is usually just a photograph. Only words that name the artwork's
# purpose belong here.
_TEXT_IN_IMAGE_HINTS = (
    "poster", "flyer", "leaflet", "infographic", "advert", "billboard",
    "headline", "wordmark", "price-list", "pricelist", "menu-board",
    "coupon", "voucher",
)


def bears_text(meta: ImageMetadata | None) -> bool:
    """Whether an image likely has readable words baked into its pixels.

    Signals in order of how directly they observe the pixels:

    1. OCR (`ocr_has_text`, services/text_detection.py) and the vision pass
       (`vision_has_text`) — both read the image itself. `vision_kind` covers
       the kinds that carry wording by definition. Trusted above everything
       below. Either saying yes is enough: they use different evidence, and a
       missed banner costs more than one photo losing its background slot.
    2. the source's own rendering — `role == "background"` is assigned only when
       the scraper measured >=24 characters of live HTML text inside that
       element (image_evidence.classify_role). A designer laid a headline on
       this image, which is strong evidence it hasn't got one of its own: nobody
       stacks two headlines. That PROVES it works as a backdrop, so it clears
       the naming signal below.
    3. the URL/alt naming, which is weak but costs nothing.

    Without vision, (3) catches only what the source happened to name honestly.
    Detecting a headline burned into an otherwise ordinary photograph needs the
    model, or OCR — pixel statistics alone do not separate that case from a
    normal photo.

    SCOPE: source images only. The parameter type enforces it — stock photos are
    `pexels.PhotoResult` and never become `ImageMetadata`, so they cannot reach
    this function. That is deliberate, not incidental: a baked-in headline is a
    property of a site's own artwork, and Pexels ships photographs, not posters.
    Keeping stock out also keeps the vision pass bounded (it annotates the
    scraped pool only — see routers/generate._annotate_source_images).
    """
    if meta is None:
        return False
    if meta.ocr_has_text or meta.vision_has_text:
        return True
    if meta.vision_kind in _TEXT_BEARING_VISION_KINDS:
        return True
    if meta.role == "background":
        return False  # measured: the source put its own live text on this image
    haystack = f"{meta.url or ''} {meta.alt or ''}".lower()
    return any(hint in haystack for hint in _TEXT_IN_IMAGE_HINTS)

# Slots where exactly one real image usually exists on the source and the
# scraper has already identified it (the hero / lead about image). For these,
# an exact intent match is decisive on its own — we prefer the site's authentic
# image over a stock keyword guess, even with no lexical/alt overlap. This is
# also the most reliable way to keep imagery on-market (the real photo depicts
# the real audience) instead of a random stock face.
PRIMARY_INTENTS = frozenset({"hero", "about"})

# How the requesting slot renders its image. 'inline' slots (split-hero side
# image, feature/gallery cards) exclude source CSS backgrounds; 'background'
# slots pin them first; 'any' keeps legacy behaviour.
SlotUsage = Literal["inline", "background", "any"]


@dataclass
class RankResult:
    chosen: ImageMetadata | None
    chosen_score: float
    decision: str  # 'confident' | 'tiebreaker' | 'fallback'
    scores: list[ImageScore]


def rank_candidates(
    query: str | None,
    slot_intent: str,
    candidates: list[ImageMetadata],
    *,
    slot_usage: SlotUsage = "any",
    allow_portrait: bool = False,
) -> RankResult:
    """Pure-Python ranking — no LLM. Returns the best candidate or None.

    Lexical-gate rule: a candidate must have at least one matched token to be
    considered. Without lexical evidence, intent+size bonuses alone can stack
    to ~0.4, which would otherwise pick a coffee photo for a "dental clinic"
    slot just because it was the right intent and big enough.

    slot_usage says how the slot renders the image: an 'inline' slot (split-hero
    side image, feature card, gallery cell) must never receive an image the
    source used as a CSS background — it was composed to sit behind text, and
    cropped into an <img> it reads as a scrape failure. A 'background' slot
    prefers the source's own background images over inline photos.

    `allow_portrait` opts a slot INTO head-and-shoulders photos of people; see
    the filter below for why it defaults off.

    Use rank_candidates_with_llm_tiebreaker() if you want the LLM judge to
    settle ambiguous cases.
    """
    candidates = [c for c in candidates if c.role not in _EXCLUDED_ROLES]
    if slot_usage == "inline":
        candidates = [c for c in candidates if c.source_usage != "css_background"]
    # Portraits are OPT-IN, because almost no slot wants one. A scraped site's
    # headshots come from its team/committee/directory page, and a headshot
    # only means something in a slot that is ABOUT that person (a team card, a
    # testimonial author). Dropped into any other slot it shows a stranger's
    # face where the slot's subject belongs — a services grid rendering four
    # committee members as if they were the services. Team/testimonial avatars
    # come from stock (intent="avatar" skips the scraped pool entirely), so the
    # opt-in is for the slots that replay the source's own photos: gallery
    # cells, and directory rosters built from scraped profiles.
    if not allow_portrait:
        candidates = [
            c for c in candidates if c.role != "portrait" and not c.vision_portrait
        ]
    elif slot_usage == "background":
        # A grid headshot stretched edge-to-edge behind text reads as a scrape
        # failure — a face filling the viewport — whatever the slot allows.
        candidates = [c for c in candidates if c.role != "portrait"]
    if slot_usage == "background" and slot_intent == "hero":
        # Gallery cells were cropped for a grid, never art-directed full-bleed;
        # keep them off the hero background specifically.
        candidates = [c for c in candidates if c.role != "gallery"]
    if slot_usage == "background":
        # An image that already carries a headline, tagline or price list can't
        # take ours on top of it. It stays rankable for every inline slot — see
        # bears_text — because nothing is drawn over those.
        candidates = [c for c in candidates if not bears_text(c)]
    # Large featured slots (hero / about) must be real featured photographs. A
    # banner, UI screenshot, graphic (e.g. a QR code) or map blown up as the
    # hero/about image reads as a scrape failure, so exclude those here, not
    # just from the intent-pin below. They stay eligible for ordinary content
    # slots (a screenshot is legitimate on a small SaaS feature card).
    if slot_intent in PRIMARY_INTENTS:
        candidates = [
            c for c in candidates
            if c.role != "portrait"  # never, whatever the slot opted into
            and (c.vision_kind is None or c.vision_kind not in _UNPINNABLE_VISION_KINDS)
        ]
    if not candidates:
        return RankResult(chosen=None, chosen_score=0.0, decision="fallback", scores=[])

    # Primary slots (hero / about): an exact intent match is decisive — pin the
    # site's authentic image and skip the lexical gate. Pick the largest /
    # best-described one when several match. Candidates the vision pass
    # identified as banners/screenshots/graphics are not pinnable.
    if slot_intent in PRIMARY_INTENTS:
        intent_matches = [
            c for c in candidates
            if c.intent == slot_intent
            and (c.vision_kind is None or c.vision_kind not in _UNPINNABLE_VISION_KINDS)
        ]
        if intent_matches:
            # Background slots: the source's own background image outranks any
            # inline photo, whatever its size — it was art-directed to sit
            # behind text, so it is the authentic full-bleed visual.
            pinned = sorted(
                (score_candidate(query or "", slot_intent, c) for c in intent_matches),
                key=lambda s: (
                    slot_usage == "background"
                    and s.candidate.source_usage == "css_background",
                    s.size_bonus,
                    s.lexical,
                ),
                reverse=True,
            )
            return RankResult(
                chosen=pinned[0].candidate,
                chosen_score=max(pinned[0].score, CONFIDENT_THRESHOLD),
                decision="intent-pinned",
                scores=pinned,
            )

    if not query:
        return RankResult(chosen=None, chosen_score=0.0, decision="fallback", scores=[])

    scored = sorted(
        (score_candidate(query, slot_intent, c) for c in candidates),
        key=lambda s: s.score,
        reverse=True,
    )
    # Drop candidates with zero lexical match — non-lexical bonuses alone
    # aren't enough evidence that an image fits the slot.
    lexical_qualifiers = [s for s in scored if s.lexical > 0]
    if not lexical_qualifiers:
        return RankResult(
            chosen=None,
            chosen_score=scored[0].score if scored else 0.0,
            decision="fallback",
            scores=scored,
        )

    top = lexical_qualifiers[0]
    if top.score >= CONFIDENT_THRESHOLD:
        return RankResult(
            chosen=top.candidate,
            chosen_score=top.score,
            decision="confident",
            scores=scored,
        )
    if top.score < AMBIGUOUS_THRESHOLD:
        return RankResult(chosen=None, chosen_score=top.score, decision="fallback", scores=scored)
    # Ambiguous band — accept top but flag for caller (LLM tiebreaker entry point).
    return RankResult(
        chosen=top.candidate,
        chosen_score=top.score,
        decision="tiebreaker",
        scores=scored,
    )


async def rank_candidates_with_llm_tiebreaker(
    query: str | None,
    slot_intent: str,
    candidates: list[ImageMetadata],
    *,
    llm: LlmClient | None = None,
    slot_usage: SlotUsage = "any",
    allow_portrait: bool = False,
) -> RankResult:
    """Heuristic first; if ambiguous AND multiple candidates are tied near the
    top, ask the LLM to break the tie. Falls through to fallback if the LLM
    says none of them fit.
    """
    initial = rank_candidates(
        query, slot_intent, candidates, slot_usage=slot_usage,
        allow_portrait=allow_portrait,
    )
    if initial.decision != "tiebreaker":
        return initial

    # Only invoke LLM if there's actually a tie at the top
    top = initial.scores[0]
    contenders = [s for s in initial.scores if (top.score - s.score) <= TIE_BAND]
    if len(contenders) < 2:
        return initial

    from app.services.llm import LlmError  # lazy: heavy import only when judging

    try:
        picked_index = await _llm_pick_best(query or "", slot_intent, contenders, llm=llm)
    except LlmError as exc:
        logger.warning("Image-judge LLM call failed: %s — using heuristic top", exc)
        return initial

    if picked_index is None:
        # LLM said "none fit" — fall back to Pexels
        return RankResult(
            chosen=None,
            chosen_score=top.score,
            decision="fallback",
            scores=initial.scores,
        )
    chosen = contenders[picked_index].candidate
    return RankResult(
        chosen=chosen,
        chosen_score=top.score,
        decision="tiebreaker",
        scores=initial.scores,
    )


# --- LLM judge ------------------------------------------------------------------


_JUDGE_SYSTEM = """You are picking the most-relevant image for a website slot.
Reply ONLY with a single JSON object: {"pick": <index>|null}.
- "pick": the array index of the best candidate, 0-based.
- If none of the candidates fits the slot well, reply {"pick": null}.
Do not explain. Do not return anything except the JSON object."""


async def _llm_pick_best(
    query: str,
    slot_intent: str,
    contenders: list[ImageScore],
    *,
    llm: LlmClient | None = None,
) -> int | None:
    """Ask the LLM to break a tie between near-tied heuristic candidates."""
    from pydantic import BaseModel

    from app.config import settings  # lazy: heavy import only when judging
    from app.services.llm import (  # lazy: heavy import only when judging
        chat_json_cached,
        get_reasoning_llm,
    )

    class _JudgePick(BaseModel):
        pick: int | None

    client = llm or get_reasoning_llm()
    payload = {
        "slot_intent": slot_intent,
        "slot_query": query,
        "candidates": [
            {
                "index": i,
                "alt": s.candidate.alt,
                "url_filename": s.candidate.url.rsplit("/", 1)[-1][:120],
                "intent_guess": s.candidate.intent,
                "width": s.candidate.width,
                "height": s.candidate.height,
                "matched_tokens": list(s.matched_tokens),
            }
            for i, s in enumerate(contenders)
        ],
    }
    # Cached: temp-0 judging is deterministic, so identical slot/candidate sets
    # (repeat slots in one render, or an unchanged regeneration) reuse the pick
    # instead of paying another reasoning call on the render critical path.
    result = await chat_json_cached(
        client,
        system_prompt=_JUDGE_SYSTEM,
        user_prompt=json.dumps(payload, ensure_ascii=False),
        schema=_JudgePick,
        temperature=settings.judge_temperature,
    )
    if result.pick is None:
        return None
    if 0 <= result.pick < len(contenders):
        return result.pick
    return None
