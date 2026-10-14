"""
Facts two combined sources state differently — found, reported, never resolved
behind the user's back.

`source_merge.merge_sources` unions two readers' content (a website and its
Facebook Page, a crawl and a brochure). When both state a phone number or an
email and they share none, one of them is stale or wrong, and the generated
site can only show one. The merge keeps the FIRST source's (it is the base, and
a website or Page is maintained more often than a brochure is reprinted), and
records the disagreement on `SourceContent.source_conflicts` so the preview
can say which value the site will use and the user can correct it.

Only phone and email: both have an unambiguous shape in prose
(services/contact_text.py) and compare exactly. Addresses and opening hours are
free text two sources rarely write identically even when they agree, so a
textual comparison would raise false alarms on most real pairs.
"""

from __future__ import annotations

from app.models.content_blocks import ConflictingValue, SourceConflict, SourceContent
from app.services.contact_text import emails_in, phone_key, phones_in

_SOURCE_LABELS = {
    "url": "website",
    "facebook": "Facebook Page",
    "pdf": "document",
    "docx": "document",
    "paste": "pasted text",
}


def _all_text(source: SourceContent) -> str:
    return "\n".join([source.raw_text or "", *(p.raw_text or "" for p in source.discovered_pages)])


def _first_by_key(values: list[str], key) -> dict[str, str]:
    out: dict[str, str] = {}
    for value in values:
        out.setdefault(key(value), value)
    return out


def conflicts_between(base: SourceContent, addition: SourceContent) -> list[SourceConflict]:
    """The facts `base` and `addition` both state and share no value of."""
    base_text, addition_text = _all_text(base), _all_text(addition)
    base_label = _SOURCE_LABELS.get(base.source_kind, base.source_kind)
    addition_label = _SOURCE_LABELS.get(addition.source_kind, addition.source_kind)
    out: list[SourceConflict] = []
    for field, extract, key in (
        ("phone", phones_in, phone_key),
        ("email", emails_in, str.lower),
    ):
        ours = _first_by_key(extract(base_text), key)
        theirs = _first_by_key(extract(addition_text), key)
        if not ours or not theirs or ours.keys() & theirs.keys():
            continue
        kept = next(iter(ours.values()))
        out.append(
            SourceConflict(
                field=field,  # type: ignore[arg-type]
                kept=kept,
                values=[
                    ConflictingValue(source=base_label, value=kept),
                    ConflictingValue(source=addition_label, value=next(iter(theirs.values()))),
                ],
            )
        )
    return out
