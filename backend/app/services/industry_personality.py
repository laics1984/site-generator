"""Per-industry design personality: 2026 award-trend art direction in two lines.

Each entry is a compact brief the LLM prompts inject verbatim:
- ``voice``  — how the copy should sound for this industry.
- ``design`` — the signature 2026 visual moves (typography, color energy,
  layout ideas) an award-calibre site in this industry leans on.

Kept deliberately short: the planner user prompt and the design-brain payload
are token-budgeted, so every entry must earn its tokens. The vocabulary lines
up with what the deterministic side already does (mood font pools, curated
palettes, landing patterns) so the LLM's copy and the generator's styling pull
in the same direction instead of fighting.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class IndustryPersonality:
    voice: str
    design: str


_PERSONALITIES: dict[str, IndustryPersonality] = {
    "restaurant": IndustryPersonality(
        voice="appetite-led, sensory, warm hospitality — name real dishes and places",
        design=(
            "full-bleed food photography, oversized serif display type, warm "
            "earthy palette, the menu treated as a designed object"
        ),
    ),
    "agency": IndustryPersonality(
        voice="confident, provocative, case-led — show, don't claim",
        design=(
            "asymmetric editorial layouts, huge display type, kinetic contrast, "
            "one restrained accent used once per view"
        ),
    ),
    "saas": IndustryPersonality(
        voice="outcome-first, crisp, zero fluff — lead with what the user ships",
        design=(
            "bento grids, high-contrast grotesk type, deliberate dark/light band "
            "rhythm, one bold accent reserved for metrics and CTAs"
        ),
    ),
    "professional-services": IndustryPersonality(
        voice="assured, plain-spoken, credential-backed",
        design=(
            "quiet Swiss grid, disciplined whitespace, muted palette with a "
            "single trust accent, serif/grotesk pairing"
        ),
    ),
    "ecommerce": IndustryPersonality(
        voice="product-first, benefit-punchy — the product is the hero",
        design=(
            "gallery-led hero, big price/offer typography, tactile cards, accent "
            "reserved for offers and CTAs"
        ),
    ),
    "consultancy": IndustryPersonality(
        voice="incisive, senior, insight-led — one sharp idea per section",
        design=(
            "editorial columns, pull-quote scale headlines, ink-on-paper "
            "palette, sparse accent underlines"
        ),
    ),
    "nonprofit": IndustryPersonality(
        voice="human, urgent, hopeful — people over programs",
        design=(
            "emotional documentary imagery, humanist type, warm accessible "
            "palette, accent reserved for the donate action"
        ),
    ),
    "childcare": IndustryPersonality(
        voice=(
            "warm, reassuring, parent-first — sell outcomes (confidence, "
            "curiosity, belonging), never facilities alone; joyful but never "
            "childish or sales-heavy; the parent should feel 'I can picture "
            "my child thriving here'"
        ),
        design=(
            "soft organic layouts with rounded corners and curved section "
            "dividers, Scandinavian calm with playful hand-drawn accents, "
            "cream/sky/mint/peach pastel palette with one brighter accent, "
            "rounded friendly display type, candid photography of children "
            "learning and teachers caring — never corporate, dark, or clip-art"
        ),
    ),
    "personal": IndustryPersonality(
        voice="first-person, distinct, unhedged",
        design=(
            "portfolio grid or single-column narrative, expressive display "
            "face, one signature color against neutrals"
        ),
    ),
    "other": IndustryPersonality(
        voice="clear, specific, benefit-led",
        design=(
            "clean modern grid, strong type scale, single accent, no decoration "
            "without purpose"
        ),
    ),
}


def personality_for(industry: str | None) -> IndustryPersonality:
    """The personality brief for an industry category (falls back to 'other')."""
    return _PERSONALITIES.get((industry or "").strip().lower(), _PERSONALITIES["other"])


def personality_prompt_lines(industry: str | None) -> str:
    """The two-line brief injected into LLM prompts."""
    p = personality_for(industry)
    return f"voice: {p.voice}. design: {p.design}."
