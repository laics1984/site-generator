"""
Detect the business's market/locale from scraped content, so generated imagery
reflects the actual audience instead of a random global stock demographic.

Signals (strongest first): ccTLD in links, phone country code, currency, and
place names. Returns a `MarketContext` whose `demonym` is fed into image queries
(e.g. "Southeast Asian team in a modern office") and the marketing rubric, and
whose country/region feeds `place_query_cue` for scenery queries
(e.g. "office skyline Malaysia").

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


@dataclass(frozen=True)
class _Market:
    """Detection signals + naming for one country."""

    region: str
    demonym: str
    region_demonym: str  # what stock libraries tag when the country is uncertain
    cctld: tuple[str, ...] = ()
    phone: tuple[str, ...] = ()
    currency: tuple[str, ...] = ()
    places: tuple[str, ...] = ()


def _region(region: str, region_demonym: str):
    """Builder for one region's market entries."""

    def make(demonym: str, **signals) -> _Market:
        return _Market(region, demonym, region_demonym, **signals)

    return make


_sea = _region("Southeast Asia", "Southeast Asian")
_east_asia = _region("East Asia", "East Asian")
_south_asia = _region("South Asia", "South Asian")
_mideast = _region("Middle East", "Middle Eastern")
_europe = _region("Europe", "European")
_north_america = _region("North America", "North American")
_latam = _region("Latin America", "Latin American")
_africa = _region("Africa", "African")
_oceania = _region("Oceania", "Australian")

_MARKETS: dict[str, _Market] = {
    # --- Southeast Asia (primary market) ------------------------------------
    "Malaysia": _sea("Malaysian", cctld=(".my",), phone=("+60",), currency=("rm", "myr"),
                     places=("malaysia", "kuala lumpur", "selangor", "penang", "johor",
                             "petaling jaya", "putrajaya", "ipoh", "kuching")),
    "Singapore": _sea("Singaporean", cctld=(".sg",), phone=("+65",), currency=("s$", "sgd"),
                      places=("singapore",)),
    "Philippines": _sea("Filipino", cctld=(".ph",), phone=("+63",), currency=("php", "₱"),
                        places=("philippines", "manila", "cebu", "quezon", "davao", "makati")),
    "Thailand": _sea("Thai", cctld=(".th",), phone=("+66",), currency=("thb", "฿"),
                     places=("thailand", "bangkok", "chiang mai", "phuket")),
    "Indonesia": _sea("Indonesian", cctld=(".id",), phone=("+62",), currency=("rp", "idr"),
                      places=("indonesia", "jakarta", "bandung", "surabaya", "bali")),
    "Vietnam": _sea("Vietnamese", cctld=(".vn",), phone=("+84",), currency=("vnd", "₫"),
                    places=("vietnam", "hanoi", "ho chi minh", "da nang")),
    "Brunei": _sea("Bruneian", cctld=(".bn",), phone=("+673",), currency=("bnd",),
                   places=("brunei", "bandar seri begawan")),
    "Cambodia": _sea("Cambodian", cctld=(".kh",), phone=("+855",), currency=("khr",),
                     places=("cambodia", "phnom penh", "siem reap")),
    "Myanmar": _sea("Burmese", cctld=(".mm",), phone=("+95",), currency=("mmk",),
                    places=("myanmar", "yangon", "mandalay")),
    # --- East Asia ------------------------------------------------------------
    "Japan": _east_asia("Japanese", cctld=(".jp",), phone=("+81",), currency=("jpy",),
                        places=("japan", "tokyo", "osaka", "kyoto", "nagoya")),
    "South Korea": _east_asia("Korean", cctld=(".kr",), phone=("+82",), currency=("krw", "₩"),
                              places=("south korea", "seoul", "busan", "incheon")),
    "Taiwan": _east_asia("Taiwanese", cctld=(".tw",), phone=("+886",), currency=("twd",),
                         places=("taiwan", "taipei", "kaohsiung")),
    "Hong Kong": _east_asia("Hong Kong", cctld=(".hk",), phone=("+852",), currency=("hkd",),
                            places=("hong kong", "kowloon")),
    "China": _east_asia("Chinese", cctld=(".cn",), phone=("+86",), currency=("cny", "rmb"),
                        places=("china", "beijing", "shanghai", "shenzhen", "guangzhou")),
    # --- South Asia -------------------------------------------------------------
    "India": _south_asia("Indian", cctld=(".in",), phone=("+91",), currency=("inr", "₹"),
                         places=("india", "mumbai", "delhi", "bangalore", "bengaluru",
                                 "chennai", "hyderabad", "kolkata", "pune")),
    "Pakistan": _south_asia("Pakistani", cctld=(".pk",), phone=("+92",), currency=("pkr",),
                            places=("pakistan", "karachi", "lahore", "islamabad")),
    "Bangladesh": _south_asia("Bangladeshi", cctld=(".bd",), phone=("+880",), currency=("bdt",),
                              places=("bangladesh", "dhaka", "chittagong")),
    "Sri Lanka": _south_asia("Sri Lankan", cctld=(".lk",), phone=("+94",), currency=("lkr",),
                             places=("sri lanka", "colombo", "kandy")),
    # --- Middle East --------------------------------------------------------------
    "United Arab Emirates": _mideast("Emirati", cctld=(".ae",), phone=("+971",), currency=("aed",),
                                     places=("united arab emirates", "dubai", "abu dhabi", "sharjah")),
    "Saudi Arabia": _mideast("Saudi", cctld=(".sa",), phone=("+966",), currency=("sar",),
                             places=("saudi arabia", "riyadh", "jeddah")),
    # --- Europe ---------------------------------------------------------------------
    "United Kingdom": _europe("British", cctld=(".uk",), phone=("+44",), currency=("gbp", "£"),
                              places=("united kingdom", "london", "manchester", "birmingham",
                                      "edinburgh", "glasgow", "leeds", "bristol")),
    "Ireland": _europe("Irish", cctld=(".ie",), phone=("+353",),
                       places=("ireland", "dublin", "cork", "galway")),
    "Germany": _europe("German", cctld=(".de",), phone=("+49",),
                       places=("germany", "berlin", "munich", "hamburg", "frankfurt", "cologne")),
    "France": _europe("French", cctld=(".fr",), phone=("+33",),
                      places=("france", "paris", "lyon", "marseille", "toulouse")),
    "Spain": _europe("Spanish", cctld=(".es",), phone=("+34",),
                     places=("spain", "madrid", "barcelona", "valencia", "seville")),
    "Italy": _europe("Italian", cctld=(".it",), phone=("+39",),
                     places=("italy", "rome", "milan", "naples", "turin")),
    "Netherlands": _europe("Dutch", cctld=(".nl",), phone=("+31",),
                           places=("netherlands", "amsterdam", "rotterdam", "the hague", "utrecht")),
    # --- North America (phone +1 is shared US/Canada, so it's not a signal) ----------
    "United States": _north_america("American", cctld=(".us",), currency=("usd",),
                                    places=("united states", "new york", "los angeles", "chicago",
                                            "houston", "san francisco", "seattle", "boston",
                                            "california", "texas", "florida")),
    "Canada": _north_america("Canadian", cctld=(".ca",), currency=("cad",),
                             places=("canada", "toronto", "vancouver", "montreal", "calgary", "ottawa")),
    # --- Latin America ---------------------------------------------------------------
    "Brazil": _latam("Brazilian", cctld=(".br",), phone=("+55",), currency=("brl",),
                     places=("brazil", "sao paulo", "são paulo", "rio de janeiro", "brasilia")),
    "Mexico": _latam("Mexican", cctld=(".mx",), phone=("+52",), currency=("mxn",),
                     places=("mexico", "mexico city", "guadalajara", "monterrey")),
    # --- Africa -----------------------------------------------------------------------
    "Nigeria": _africa("Nigerian", cctld=(".ng",), phone=("+234",), currency=("ngn", "₦"),
                       places=("nigeria", "lagos", "abuja", "port harcourt")),
    "Kenya": _africa("Kenyan", cctld=(".ke",), phone=("+254",), currency=("kes",),
                     places=("kenya", "nairobi", "mombasa")),
    "South Africa": _africa("South African", cctld=(".za",), phone=("+27",), currency=("zar",),
                            places=("south africa", "johannesburg", "cape town", "durban", "pretoria")),
    # --- Oceania ------------------------------------------------------------------------
    "Australia": _oceania("Australian", cctld=(".au",), phone=("+61",), currency=("aud",),
                          places=("australia", "sydney", "melbourne", "brisbane", "perth", "adelaide")),
    "New Zealand": _oceania("New Zealand", cctld=(".nz",), phone=("+64",), currency=("nzd",),
                            places=("new zealand", "auckland", "wellington", "christchurch")),
}

_STRONG = 3  # ccTLD / phone country code
_PLACE = 2  # place name
_WEAK = 1  # currency (often ambiguous, e.g. "RM")
_MIN_CONFIDENT = 3  # below this we only trust the region, not the country


def _bounded(needle: str, haystack: str) -> bool:
    """Substring match that can't bleed into a longer word — ".in" must not
    match ".info", "india" must not match "indiana"."""
    return re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", haystack) is not None


def detect_market(text: str | None, urls: list[str] | None = None) -> MarketContext | None:
    """Best-effort market detection. Returns None when there's no signal."""
    text_l = (text or "").lower()
    compact = text_l.replace(" ", "").replace("-", "")
    urls_l = " ".join(urls or []).lower()

    scores: dict[str, int] = {}
    for country, sig in _MARKETS.items():
        score = 0
        for tld in sig.cctld:
            if _bounded(tld, urls_l) or _bounded(tld, text_l):
                score += _STRONG
        for code in sig.phone:
            if re.search(re.escape(code), compact):
                score += _STRONG
        for cur in sig.currency:
            if _bounded(cur, text_l):
                score += _WEAK
        for place in sig.places:
            if _bounded(place, text_l):
                score += _PLACE
        if score:
            scores[country] = score

    if not scores:
        return None
    country = max(scores, key=lambda c: scores[c])
    sig = _MARKETS[country]
    if scores[country] >= _MIN_CONFIDENT:
        return MarketContext(
            country=country, region=sig.region,
            demonym=sig.demonym, region_demonym=sig.region_demonym,
        )
    # Weak evidence: trust only the region.
    return MarketContext(
        country=None, region=sig.region,
        demonym=sig.region_demonym, region_demonym=sig.region_demonym,
    )


def image_query_cue(market: MarketContext | None) -> str:
    """The locale phrase to prepend to a stock image query. We use the *regional*
    term (e.g. "Southeast Asian") because stock libraries tag it far more than a
    single country — better coverage — and the resolver falls back to the plain
    query when even that returns nothing. Empty when locale is unknown."""
    if market is None:
        return ""
    return market.region_demonym


def place_query_cue(market: MarketContext | None) -> str:
    """The place name to append to scenery/atmosphere stock queries
    ("office skyline Malaysia"). Country when confident, else the region.
    Empty when locale is unknown."""
    if market is None:
        return ""
    return market.country or market.region
