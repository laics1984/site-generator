"""Shared line-kind predicates for team member role/bio text.

Both the scraper (which harvests a person's card) and the post-LLM sanitizer
(which validates what reaches the page) need the same notion of "this line is
site chrome, not this person's details". Keeping them here stops the two copies
from drifting.

The filters reject text by *kind*, never by length: a directory card
legitimately packs credentials, served populations and an address as short
separate lines, and those lines ARE the bio.

``roster_is_people`` is the group-level member of the same family: the one
question a single card can never answer.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_BOILERPLATE_PHRASES = (
    "read more",
    "read bio",
    "read full",
    "view profile",
    "view bio",
    "view all",
    "full profile",
    "learn more",
    "find out more",
    "see more",
    "show more",
    "click here",
    "contact us",
    "get in touch",
    "book now",
    "book an appointment",
    "make an appointment",
    "enquire now",
    "get directions",
    "view map",
    "email us",
    "call us",
    "send message",
    "send a message",
    "sign up",
    "subscribe",
    "share this",
    "follow us",
    "back to",
)

_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{5,}\d")

_ROLE_MAX_LEN = 90
_BIO_MAX_LEN = 480

# Roles that mean "this person started or owns the business", used to decide
# whether the homepage shows a small founders band instead of the full roster.
# Deliberately narrow, because the heading it drives ("Meet the founders")
# asserts a fact about real people. Excluded on purpose:
#   - bare "Partner"/"Director" — a law firm has many partners and "Director of
#     Nursing" is a department head; only the qualified forms name the person
#     running the business.
#   - "Principal" — in childcare/education that is the school head, not a
#     founder, and childcare is a first-class industry here.
_FOUNDER_ROLE_PHRASES = (
    "founder",       # "founder", "co-founder", "cofounder"
    "founding",      # "founding partner", "founding director"
    "owner",         # "owner", "co-owner", "business owner"
    "proprietor",
    "managing director",
    "managing partner",
)

# People a founders band introduces before it stops being "the two people who
# started this" and becomes a leadership roster. One number, three consumers:
# the scaffold weave (page_inference), the homepage roster policy (generate) and
# the template choice (section_content) must agree, or a page asks for a band
# and gets a staff grid.
FOUNDERS_BAND_MAX = 3


def looks_like_founder_role(value: str | None) -> bool:
    """True when a job title names a founder/owner rather than a staff role.

    Substring matching on a title that has already passed ``looks_like_team_role``
    — these phrases don't occur inside unrelated titles, and the prefix forms
    ("Co-Founder", "Founding Partner") all contain the bare stem.
    """
    if not looks_like_team_role(value):
        return False
    low = " ".join((value or "").split()).lower()
    return any(phrase in low for phrase in _FOUNDER_ROLE_PHRASES)


def has_contact_token(text: str) -> bool:
    """True when a line carries an email, URL or phone number.

    Ordered cheap-first: the substring tests reject almost everything before the
    phone regex is reached.
    """
    if "@" in text:
        return True
    low = text.lower()
    if "http://" in low or "https://" in low or "www." in low:
        return True
    return any(
        sum(ch.isdigit() for ch in m.group(0)) >= 7 for m in _PHONE_RE.finditer(text)
    )


def is_boilerplate_line(line: str) -> bool:
    """True for CTA/nav copy that is never part of a person's own details."""
    low = line.lower().strip(" :|-")
    return any(phrase in low for phrase in _BOILERPLATE_PHRASES)


def looks_like_team_role(value: str | None) -> bool:
    """True when a value is plausibly a job title.

    Rejects what the scraper's positional fallback used to accept: CTA labels,
    phone numbers, and prose sentences that are really bio copy.
    """
    if not value:
        return False
    text = " ".join(value.split()).strip(" :|-")
    if not text or len(text) > _ROLE_MAX_LEN:
        return False
    if is_boilerplate_line(text) or has_contact_token(text):
        return False
    # A title is a noun phrase. A sentence that long is bio copy in the role slot.
    return not (text.endswith(".") and len(text.split()) > 8)


def _contains_name(line_low: str, name_low: str) -> bool:
    return bool(name_low) and name_low in line_low


def clean_team_bio(
    bio: str | None,
    *,
    other_names: tuple[str, ...] = (),
    haystack: str | None = None,
) -> str | None:
    """Keep a bio's own factual lines; drop chrome, contact details and bleed.

    ``other_names`` are the block's other members — a line naming one of them
    belongs to that person's card, not this one.

    ``haystack`` is the page's already-normalized source text. When given, each
    line must appear in it verbatim. This is a substring test on purpose: the
    fuzzy branch of ``is_grounded_in_source`` runs an O(n*m) SequenceMatcher
    over the whole page, and a block carries up to 24 bios. A bio line that is
    not a substring has been rewritten, which is exactly what we drop.
    """
    if not bio:
        return None
    others = [n.lower() for n in other_names if n]
    kept: list[str] = []
    for raw in bio.splitlines():
        line = " ".join(raw.split())
        if len(line) <= 3:
            continue
        if is_boilerplate_line(line) or has_contact_token(line):
            continue
        line_low = line.lower()
        if any(_contains_name(line_low, name) for name in others):
            continue
        if haystack is not None and " ".join(line_low.split()) not in haystack:
            continue
        kept.append(line)
    if not kept:
        return None
    return "\n".join(kept)[:_BIO_MAX_LEN]


# --- is this group of cards a roster of people? ---------------------------------
#
# A card in isolation is unclassifiable. A product tile, a facility card and a
# staff card are the same object at close range: a square photo, a Title-Case
# caption, maybe a paragraph. Only the GROUP disambiguates — the same reasoning,
# and the same 0.6 share, as ``section_extraction._classify``'s `people` rule.
#
# The discriminator is what a card SAYS, not what it looks like:
#
#   - Geometry is no help. LumiBright's product-category tiles are 800x800 PNGs,
#     so every portrait-aspect test in the codebase waves them through.
#   - Vocabulary is worse. ``scraper._NON_NAME_TAIL_TOKENS`` was written for a
#     childcare site's "Innovation Centre" and had nothing whatsoever to say
#     about "Crowd Control Barricade" or "Zeosorb Absorbent Granules" — a
#     denylist in the wrong industry is a silent off switch, not a gate.
#
# A job title, a sentence of personal prose, or a personal contact is something
# no product tile, menu item, facility card or portfolio thumbnail carries. That
# is the whole rule, and it is industry-neutral by construction.
_ROSTER_PEOPLE_MIN_SHARE = 0.6


# A spec line is the one kind of card text that is about the OBJECT, and it is
# what defeats a naive "does this card carry any text?" reading of the rule
# above: LumiBright's product detail pages caption every tile with
# "Weight : 8.0 kg Open : 950mm(H) x 2300mm(L)", which `looks_like_team_role`
# happily accepts as a job title (short, no full stop, no contact token).
#
# Detected by physical units and dimension pairs — physics, not industry
# vocabulary, the same way `has_contact_token` leans on a phone regex rather
# than a list of area codes. A person's title or story does carry numbers
# ("Director since 1998", "Level 3 Coach", "Mother of 3") but not numbers
# welded to units.
#
# The trailing lookahead is case-SENSITIVE inside a case-insensitive pattern on
# purpose: "2.5mColor" is a run-together spec sheet and must match, while
# "3 monkeys" must not. Under a plain `\b` or an `(?![a-z])` that re.I folds,
# both go the same way.
_MEASUREMENT_RE = re.compile(
    r"\d\s*(?:mm|cm|km|kg|ml|litres?|liters?|inch(?:es)?|ft|lbs?|oz|hp|[vwl]|m)"
    r"(?!(?-i:[a-z]))"
    r"|\d\s*[x\u00d7]\s*\d",
    re.IGNORECASE,
)


def looks_like_spec_line(text: str | None) -> bool:
    """True when a line states a measurement — a dimension, weight or capacity."""
    return bool(text) and bool(_MEASUREMENT_RE.search(text))


def card_has_person_evidence(card: object) -> bool:
    """True when a card says something about a PERSON, not just names a thing.

    Duck-typed so one predicate reads a scraped ``ProfileCandidate`` and a
    generated ``TeamMember`` alike — the same ``getattr`` style
    ``section_content._team_content`` and ``generate._roster_members`` already
    use across these two shapes.

    Every text field is read through ``looks_like_spec_line``. "This card has
    words on it" is not the test — a product tile has words on it. The test is
    whether those words could be a job title or a person's story.
    """
    role = getattr(card, "role", None)
    if looks_like_team_role(role) and not looks_like_spec_line(role):
        return True
    for attr in ("bio", "description"):
        text = getattr(card, attr, None) or ""
        if any(
            line.strip() and not looks_like_spec_line(line)
            for line in text.splitlines()
        ):
            return True
    if (getattr(card, "email", None) or "") or (getattr(card, "phone", None) or ""):
        return True
    return bool(getattr(card, "social_links", None))


def roster_is_people(
    cards: Sequence[object], *, declared: Sequence[bool] | None = None
) -> bool:
    """True when a group of cards earns being read as people.

    A lone card has no group to agree with, so it has to vouch for itself; past
    that the majority rules, which is what stops one stray "mailto:" in a
    product grid from carrying the whole rack.

    ``declared`` marks cards whose own markup called them a profile
    (``scraper._PROFILE_CONTAINER_HINTS`` — ``class="team-member"`` and friends).
    A site that labels its cards has stated the kind outright, which beats
    anything their text could carry; the group rule is for the cards that say
    nothing at all.
    """
    total = len(cards)
    if not total:
        return False
    flags = declared if declared is not None else ()
    evidenced = sum(
        1
        for index, card in enumerate(cards)
        if card_has_person_evidence(card)
        or (index < len(flags) and flags[index])
    )
    return evidenced >= max(1, total * _ROSTER_PEOPLE_MIN_SHARE)
