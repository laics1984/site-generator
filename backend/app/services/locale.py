"""
Detect the business's market/locale from scraped content, so generated imagery
reflects the actual audience instead of a random global stock demographic.

Signals (strongest first): ccTLD in links, phone country code, currency, and
place names. Returns a `MarketContext` whose `demonym` is fed into image queries
(e.g. "Southeast Asian team in a modern office") and the marketing rubric.

Deterministic + dependency-light on purpose — no LLM call, unit-testable alone.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class MarketContext:
    country: str | None  # specific country when confident, else None
    region: str  # e.g. "Southeast Asia"
    demonym: str  # most specific: "Malaysian" when confident, else regional
    region_demonym: str  # always the regional term: "Southeast Asian"


# country -> signals. Region demonym is the fallback when only weak evidence.
_SEA_REGION = "Southeast Asia"
_SEA_DEMONYM = "Southeast Asian"
_MARKETS: dict[str, dict[str, object]] = {
    "Malaysia": {"region": _SEA_REGION, "demonym": "Malaysian", "cctld": [".my"], "phone": ["+60"],
                 "currency": ["rm", "myr"], "places": ["malaysia", "kuala lumpur", "selangor",
                 "penang", "johor", "petaling jaya", "putrajaya", "ipoh", "kuching"]},
    "Singapore": {"region": _SEA_REGION, "demonym": "Singaporean", "cctld": [".sg"], "phone": ["+65"],
                  "currency": ["s$", "sgd"], "places": ["singapore"]},
    "Philippines": {"region": _SEA_REGION, "demonym": "Filipino", "cctld": [".ph"], "phone": ["+63"],
                    "currency": ["php", "₱"], "places": ["philippines", "manila", "cebu", "quezon", "davao", "makati"]},
    "Thailand": {"region": _SEA_REGION, "demonym": "Thai", "cctld": [".th"], "phone": ["+66"],
                 "currency": ["thb", "฿"], "places": ["thailand", "bangkok", "chiang mai", "phuket"]},
    "Indonesia": {"region": _SEA_REGION, "demonym": "Indonesian", "cctld": [".id"], "phone": ["+62"],
                  "currency": ["rp", "idr"], "places": ["indonesia", "jakarta", "bandung", "surabaya", "bali"]},
    "Vietnam": {"region": _SEA_REGION, "demonym": "Vietnamese", "cctld": [".vn"], "phone": ["+84"],
                "currency": ["vnd", "₫"], "places": ["vietnam", "hanoi", "ho chi minh", "da nang"]},
    "Brunei": {"region": _SEA_REGION, "demonym": "Bruneian", "cctld": [".bn"], "phone": ["+673"],
               "currency": ["bnd"], "places": ["brunei", "bandar seri begawan"]},
    "Cambodia": {"region": _SEA_REGION, "demonym": "Cambodian", "cctld": [".kh"], "phone": ["+855"],
                 "currency": ["khr"], "places": ["cambodia", "phnom penh", "siem reap"]},
    "Myanmar": {"region": _SEA_REGION, "demonym": "Burmese", "cctld": [".mm"], "phone": ["+95"],
                "currency": ["mmk"], "places": ["myanmar", "yangon", "mandalay"]},
}

_STRONG = 3  # ccTLD / phone country code
_PLACE = 2  # place name
_WEAK = 1  # currency (often ambiguous, e.g. "RM")
_MIN_CONFIDENT = 3  # below this we only trust the region, not the country


def detect_market(text: str | None, urls: list[str] | None = None) -> MarketContext | None:
    """Best-effort market detection. Returns None when there's no signal."""
    text_l = (text or "").lower()
    compact = text_l.replace(" ", "").replace("-", "")
    urls_l = " ".join(urls or []).lower()

    scores: dict[str, int] = {}
    for country, sig in _MARKETS.items():
        score = 0
        for tld in sig["cctld"]:  # type: ignore[union-attr]
            if tld in urls_l or f"{tld}/" in text_l or f"{tld}'" in text_l or f'{tld}"' in text_l:
                score += _STRONG
        for code in sig["phone"]:  # type: ignore[union-attr]
            if code.replace("+", r"\+") and re.search(re.escape(code), compact):
                score += _STRONG
        for cur in sig["currency"]:  # type: ignore[union-attr]
            if re.search(rf"(?<![a-z]){re.escape(cur)}(?![a-z])", text_l):
                score += _WEAK
        for place in sig["places"]:  # type: ignore[union-attr]
            if place in text_l:
                score += _PLACE
        if score:
            scores[country] = score

    if not scores:
        return None
    country = max(scores, key=lambda c: scores[c])
    sig = _MARKETS[country]
    region = str(sig["region"])
    region_demonym = _SEA_DEMONYM  # all current markets are SEA
    if scores[country] >= _MIN_CONFIDENT:
        return MarketContext(
            country=country, region=region,
            demonym=str(sig["demonym"]), region_demonym=region_demonym,
        )
    # Weak evidence: trust only the region.
    return MarketContext(
        country=None, region=region, demonym=region_demonym, region_demonym=region_demonym,
    )


def image_query_cue(market: MarketContext | None) -> str:
    """The locale phrase to prepend to a stock image query. We use the *regional*
    term (e.g. "Southeast Asian") because stock libraries tag it far more than a
    single country — better coverage — and the resolver falls back to the plain
    query when even that returns nothing. Empty when locale is unknown."""
    if market is None:
        return ""
    return market.region_demonym
