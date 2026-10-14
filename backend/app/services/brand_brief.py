"""
The brand brief — what a business IS, read once, before anything is designed.

Brand detection used to return one six-value mood enum (plus name, summary,
industry, a colour hint), and for childcare even that was overwritten by an
industry lock. So the design engine knew which INDUSTRY a site was and almost
nothing about the BUSINESS, and two kindergartens were indistinguishable to it.
The brief adds, in the same detection call (no new LLM round-trip):

* ``personality`` — six axes (models/brand.PERSONALITY_AXES), each a value on
  -2..+2 with the verbatim source span it rests on, or unknown;
* ``audience`` / ``positioning`` — short interpretations, for design only;
* ``differentiators`` — what sets the business apart, as VERBATIM source spans;
* ``unknowns`` — what the source does not say, so nothing downstream guesses.

This module is the deterministic half, the same division the rest of the
pipeline uses: the model reads, code decides what the reading is allowed to
mean.

FACTS / INFERENCES / UNKNOWNS are kept apart by construction:

* a differentiator is a FACT, so it must be grounded in the source or it is
  dropped — including every number it states (fuzzy grounding alone would
  accept "20 years" against "2 years");
* a personality reading is an INFERENCE; its evidence span is kept only when it
  really is source text, otherwise cleared, leaving an un-evidenced inference
  rather than a fabricated quote;
* an axis the model left null is UNKNOWN and is ignored, never assumed middle.

The mood is DERIVED from the personality when enough of it is known
(`mood_from_personality`): the enum detection returns is a single categorical
guess that leans on the industry (the old prompt literally mapped "childcare →
friendly"), while the nearest of six prototypes over several evidenced axes is a
reading of the business itself. Every mood-keyed table downstream keeps working
because the output is still a ``BrandMood``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, TypeVar

from app.models.brand import BrandMood, BrandPersonality
from app.services.scaffold_enforcement import (
    normalize_for_grounding,
    is_grounded_in_normalized,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.services.planner import DetectedBrand

_Brief = TypeVar("_Brief", bound="DetectedBrand")

# A mood is derived only from a personality the source actually spoke to.
MIN_AXES_FOR_MOOD = 3
_MAX_DIFFERENTIATORS = 5
_MAX_UNKNOWNS = 5
_MAX_SHORT_TEXT = 160

# Each mood as a point on the six axes (-2 = first pole, +2 = second). These are
# the moods' existing meaning (see BrandMood) restated in the axes' vocabulary,
# not new taste: luxury is sophisticated, premium and calm; playful is playful,
# expressive and energetic; technical is minimal and structured.
_MOOD_PROTOTYPES: dict[BrandMood, dict[str, int]] = {
    "modern": {
        "playful_sophisticated": 1, "traditional_contemporary": 2,
        "minimal_expressive": -1, "calm_energetic": 0,
        "organic_structured": 1, "premium_accessible": 1,
    },
    "luxury": {
        "playful_sophisticated": 2, "traditional_contemporary": -1,
        "minimal_expressive": -1, "calm_energetic": -1,
        "organic_structured": 0, "premium_accessible": -2,
    },
    "friendly": {
        "playful_sophisticated": -1, "traditional_contemporary": 0,
        "minimal_expressive": 0, "calm_energetic": -1,
        "organic_structured": -1, "premium_accessible": 2,
    },
    "technical": {
        "playful_sophisticated": 1, "traditional_contemporary": 1,
        "minimal_expressive": -2, "calm_energetic": -1,
        "organic_structured": 2, "premium_accessible": 0,
    },
    "editorial": {
        "playful_sophisticated": 2, "traditional_contemporary": 0,
        "minimal_expressive": 1, "calm_energetic": -1,
        "organic_structured": 0, "premium_accessible": -1,
    },
    "playful": {
        "playful_sophisticated": -2, "traditional_contemporary": 1,
        "minimal_expressive": 2, "calm_energetic": 2,
        "organic_structured": 0, "premium_accessible": 1,
    },
}

_NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")


def mood_from_personality(
    personality: BrandPersonality, *, min_axes: int = MIN_AXES_FOR_MOOD
) -> BrandMood | None:
    """The mood whose prototype sits nearest the brand's KNOWN axes (mean
    squared distance), or None when fewer than ``min_axes`` are known — too
    little evidence to outrank detection's own answer. Ties keep declaration
    order, so the result is deterministic."""
    known = personality.known()
    if len(known) < min_axes:
        return None

    def distance(mood: BrandMood) -> float:
        prototype = _MOOD_PROTOTYPES[mood]
        return sum((prototype[axis] - value) ** 2 for axis, value in known.items()) / len(known)

    return min(_MOOD_PROTOTYPES, key=distance)


def _numbers_grounded(text: str, haystack: str) -> bool:
    return all(number in haystack for number in _NUMBER_RE.findall(text))


def _is_fact(text: str, haystack: str) -> bool:
    """Grounded the way a quoted claim must be: the span is source text (the
    pipeline's standard test) and no number in it was changed on the way."""
    return is_grounded_in_normalized(text, haystack) and _numbers_grounded(
        normalize_for_grounding(text), haystack
    )


def _short(text: str | None) -> str | None:
    cleaned = " ".join((text or "").split())
    return cleaned[:_MAX_SHORT_TEXT] or None


def ground_brief(detected: _Brief, raw_text: str | None) -> _Brief:
    """The brief with every claim held to the source. Never raises; with no
    source text to check against, nothing can be called a fact, so the
    differentiators and evidence spans are dropped and the inferences stay."""
    haystack = normalize_for_grounding(raw_text or "")

    differentiators: list[str] = []
    for item in detected.differentiators:
        claim = " ".join((item or "").split())
        if claim and claim not in differentiators and _is_fact(claim, haystack):
            differentiators.append(claim)
        if len(differentiators) == _MAX_DIFFERENTIATORS:
            break

    readings = {}
    for axis, reading in detected.personality.readings().items():
        evidence = " ".join((reading.evidence or "").split()) or None
        if evidence and not is_grounded_in_normalized(evidence, haystack):
            evidence = None
        readings[axis] = reading.model_copy(update={"evidence": evidence})
    personality = BrandPersonality(**readings)

    unknowns = [u for u in (_short(u) for u in detected.unknowns) if u][:_MAX_UNKNOWNS]

    update: dict[str, object] = {
        "differentiators": differentiators,
        "personality": personality,
        "unknowns": unknowns,
        "audience": _short(detected.audience),
        "positioning": _short(detected.positioning),
    }
    derived = mood_from_personality(personality)
    if derived is not None:
        update["brand_mood"] = derived
        update["mood_basis"] = "personality"
    return detected.model_copy(update=update)
