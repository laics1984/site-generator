"""Evidence that means something only inside one industry family.

The same words mean different things in different businesses: "admission" on a
museum's site is a ticket, on a kindergarten's it is how a child joins;
"registered with" on a SaaS site is an account, on a kindergarten's it is the
licence a parent looks for. So these readings are gated on the INDUSTRY — every
archetype of the family inherits them, nothing outside it is touched — and
they sit here, in one place, rather than spread through the generic signal
tables they extend.

Two kinds of answer, both evidence only:

* **sections a page has earned** (`family_signals`), read by
  `page_inference._detect_content_signals` beside its generic signals:
  - childcare: an enrolment PROCESS — a section the source headed as one AND
    laid out as steps. Steps have no fact sanitizer, so wording alone ("enrol
    now") never asks the model for a process it would have to invent.
  - saas: testimonials — the source presents customer quotes. The
    testimonials sanitizer then keeps only quotes the source states.
* **facts to show as stated**:
  - childcare (`safety_facts`): the safety and licensing sentences parents
    look for, verbatim, handed to the planner to place in copy;
  - saas (`footer_facts`): the business's regulatory disclosure line
    (services/disclosures.py) and its app-store listings, carried to the
    footer exactly as the source states and links them.
  Never composed: absent from the source, absent from the site.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.profile_text import split_bio_segments

if TYPE_CHECKING:
    from app.models.content_blocks import SectionType, SourceContent

_ENROLMENT_HEADING = re.compile(
    r"\b(enrol(l)?(ment|ing)?|admissions?|registration|how to (apply|join|register)|"
    r"joining us|apply(ing)? for a place)\b",
    re.IGNORECASE,
)
_TESTIMONIAL_CUES = re.compile(
    r"\b(testimonials?|what (our )?(customers|clients|users) (say|are saying)|"
    r"customer stor(y|ies)|hear from our (customers|clients)|loved by|"
    r"(our )?customers love)\b",
    re.IGNORECASE,
)
# A safety fact names a concrete safeguard. Each is a phrase a parent reads as
# a commitment, so a sentence is shown only verbatim.
_SAFETY_CUES = re.compile(
    r"\b(registered with|licen[cs]ed by|approved by|first[- ]aid|cctv|"
    r"(teacher|staff|adult)[- ]to[- ](child|children|pupil)[- ]ratio|ratio of \d+\s*:\s*\d+|"
    r"secure (pick[- ]?up|drop[- ]?off|entry|access)|background[- ]check(ed|s)?|"
    r"police[- ]check(ed)?|safeguarding|child protection|fire drills?)\b",
    re.IGNORECASE,
)
_MAX_FACT_CHARS = 240
_MAX_FACTS = 4


def stated_sentences(
    text: str, *patterns: re.Pattern[str], max_chars: int, limit: int
) -> list[str]:
    """The sentences of `text` matching EVERY pattern, verbatim (whitespace
    collapsed), in source order, de-duplicated. One splitter for every
    "show it as stated" reader (`profile_text.split_bio_segments`)."""
    found: list[str] = []
    for _line, raw in split_bio_segments(text or ""):
        sentence = " ".join(raw.split())
        if (
            sentence
            and len(sentence) <= max_chars
            and all(p.search(sentence) for p in patterns)
            and sentence not in found
        ):
            found.append(sentence)
            if len(found) >= limit:
                break
    return found


def family_signals(industry: str | None, page: SourceContent) -> list[SectionType]:
    """Sections this page has earned as a member of its industry family."""
    if industry == "childcare":
        if any(
            candidate.card_kind == "steps" and _ENROLMENT_HEADING.search(candidate.heading)
            for candidate in page.section_candidates or []
        ):
            return ["process"]
        return []
    if industry == "saas":
        haystack = " ".join([page.raw_text or "", " ".join(page.headings or [])])
        return ["testimonials"] if _TESTIMONIAL_CUES.search(haystack) else []
    return []


def safety_facts(source: SourceContent | None, industry: str | None) -> list[str]:
    """The safety and licensing sentences a childcare source states, verbatim,
    across its pages (site-wide facts, so the first pages that state them)."""
    if source is None or industry != "childcare":
        return []
    found: list[str] = []
    for page in (source, *(source.discovered_pages or [])):
        for sentence in stated_sentences(
            page.raw_text or "", _SAFETY_CUES, max_chars=_MAX_FACT_CHARS, limit=_MAX_FACTS
        ):
            if sentence not in found:
                found.append(sentence)
        if len(found) >= _MAX_FACTS:
            break
    return found[:_MAX_FACTS]


# Industries whose footer carries the source's own regulatory line and app
# listings. Gated on the INDUSTRY so every archetype inside it inherits them.
FOOTER_FACT_FAMILIES: frozenset[str] = frozenset({"saas"})


@dataclass(frozen=True)
class FooterFacts:
    """What the footer states for this site beyond its chrome — each verbatim
    from the source, each absent when the source states none."""

    disclosure: str | None = None
    app_links: tuple[tuple[str, str], ...] = ()


def footer_facts(source: SourceContent | None, industry: str | None) -> FooterFacts:
    """The footer facts for this site. Site-wide by nature (a footer is), so
    the first page that states each one speaks for the site."""
    if source is None or (industry or "") not in FOOTER_FACT_FAMILIES:
        return FooterFacts()
    pages = (source, *(source.discovered_pages or []))
    disclosure = next((" ".join(p.disclosures) for p in pages if p.disclosures), None)
    links: dict[str, str] = {}
    for page in pages:
        for link in page.app_links:
            links.setdefault(link.label, link.href)
    return FooterFacts(disclosure=disclosure, app_links=tuple(links.items()))
