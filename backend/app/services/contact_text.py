"""
Contact details in free text — the one spelling of "is this a phone number /
an email address" for readers that only have prose.

`profile_text.has_contact_token` asks whether a line carries one (a person's
card line is contact details, not a role); `source_conflicts` asks what they
are, to compare two sources' claims. One pattern serves both, so a number the
card reader treats as a phone is the number the conflict check compares.

(`facebook_dom._FIELD_SHAPE` is a different question — does a scanned label's
VALUE have a field's shape, matched whole — and keeps its own patterns.)
"""

from __future__ import annotations

import re

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{5,}\d")
# A run of digits shorter than this is a year, a price or a postcode, not a phone.
MIN_PHONE_DIGITS = 7


def phones_in(text: str) -> list[str]:
    """Phone-shaped runs with at least MIN_PHONE_DIGITS digits, as written."""
    return [
        m.group(0).strip()
        for m in PHONE_RE.finditer(text or "")
        if sum(ch.isdigit() for ch in m.group(0)) >= MIN_PHONE_DIGITS
    ]


def emails_in(text: str) -> list[str]:
    return [m.group(0).strip(".") for m in EMAIL_RE.finditer(text or "")]


def phone_key(phone: str) -> str:
    """Comparable identity of a number: its last eight digits, so the same line
    written with and without a country code ("+60 3-1234 5678" / "03-1234 5678")
    is one number."""
    return "".join(ch for ch in phone if ch.isdigit())[-8:]
