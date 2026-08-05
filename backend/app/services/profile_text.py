"""Shared line-kind predicates for team member role/bio text.

Both the scraper (which harvests a person's card) and the post-LLM sanitizer
(which validates what reaches the page) need the same notion of "this line is
site chrome, not this person's details". Keeping them here stops the two copies
from drifting.

The filters reject text by *kind*, never by length: a directory card
legitimately packs credentials, served populations and an address as short
separate lines, and those lines ARE the bio.
"""

from __future__ import annotations

import re

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
