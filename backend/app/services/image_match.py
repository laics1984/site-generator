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
  >= 0.55          confident match — use this candidate, skip the LLM judge
  0.30 – 0.55      ambiguous — optionally invoke the LLM judge to break ties
  <  0.30          poor fit — return None so the resolver falls through to Pexels

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
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from app.models.content_blocks import ImageMetadata

if TYPE_CHECKING:
    # Heavy import chain (llm -> config -> pydantic_settings/httpx). Kept lazy so
    # the pure, deterministic ranking logic can be imported + unit-tested alone.
    from app.services.llm import OllamaClient

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
    """Jaccard-style overlap between query tokens and (alt + url path) tokens.

    Returns (score in [0, 0.6], matched tokens).
    """
    q_tokens = _tokens(query)
    if not q_tokens:
        return 0.0, ()
    c_tokens = _tokens(candidate.alt) | _path_tokens(candidate.url)
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


CONFIDENT_THRESHOLD = 0.55
AMBIGUOUS_THRESHOLD = 0.30
TIE_BAND = 0.10

# Slots where exactly one real image usually exists on the source and the
# scraper has already identified it (the hero / lead about image). For these,
# an exact intent match is decisive on its own — we prefer the site's authentic
# image over a stock keyword guess, even with no lexical/alt overlap. This is
# also the most reliable way to keep imagery on-market (the real photo depicts
# the real audience) instead of a random stock face.
PRIMARY_INTENTS = frozenset({"hero", "about"})


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
) -> RankResult:
    """Pure-Python ranking — no LLM. Returns the best candidate or None.

    Lexical-gate rule: a candidate must have at least one matched token to be
    considered. Without lexical evidence, intent+size bonuses alone can stack
    to ~0.4, which would otherwise pick a coffee photo for a "dental clinic"
    slot just because it was the right intent and big enough.

    Use rank_candidates_with_llm_tiebreaker() if you want the LLM judge to
    settle ambiguous cases.
    """
    if not candidates:
        return RankResult(chosen=None, chosen_score=0.0, decision="fallback", scores=[])

    # Primary slots (hero / about): an exact intent match is decisive — pin the
    # site's authentic image and skip the lexical gate. Pick the largest /
    # best-described one when several match.
    if slot_intent in PRIMARY_INTENTS:
        intent_matches = [c for c in candidates if c.intent == slot_intent]
        if intent_matches:
            pinned = sorted(
                (score_candidate(query or "", slot_intent, c) for c in intent_matches),
                key=lambda s: (s.size_bonus, s.lexical),
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
    llm: OllamaClient | None = None,
) -> RankResult:
    """Heuristic first; if ambiguous AND multiple candidates are tied near the
    top, ask the LLM to break the tie. Falls through to fallback if the LLM
    says none of them fit.
    """
    initial = rank_candidates(query, slot_intent, candidates)
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


class _JudgeResponse:
    pass  # placeholder — we use a tiny inline schema below


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
    llm: OllamaClient | None = None,
) -> int | None:
    """Ask the LLM to break a tie between near-tied heuristic candidates."""
    from pydantic import BaseModel

    from app.services.llm import get_llm  # lazy: heavy import only when judging

    class _JudgePick(BaseModel):
        pick: int | None

    client = llm or get_llm()
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
    result = await client.chat_json(
        system_prompt=_JUDGE_SYSTEM,
        user_prompt=json.dumps(payload, ensure_ascii=False),
        schema=_JudgePick,
        temperature=0.0,
        num_ctx=2048,
    )
    if result.pick is None:
        return None
    if 0 <= result.pick < len(contenders):
        return result.pick
    return None
