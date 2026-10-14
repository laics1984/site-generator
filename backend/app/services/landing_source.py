"""Recover journey evidence from existing Facebook/PDF text without I/O.

Readers keep owning ingestion and page boundaries. This is a private planning
view: it does not rewrite source text, media, navigation or discovered pages.
Only explicit headings/labels qualify; body keywords never invent a pain point.
"""

from __future__ import annotations

import re
from itertools import chain

from app.models.content_blocks import SectionCandidate, SourceContent
from app.models.facebook import FACT_LABELS, FactLabel

CONVERSION_SOURCE_KINDS = frozenset({"facebook", "pdf"})
JOURNEY_ROLES = ("problem", "solution", "proof", "action")
_ROLE_PATTERNS = tuple(re.compile(pattern, re.I) for pattern in (
    r"\b(problem[s]?|challenge[s]?|pain points?|your needs|the need)\b|masalah|cabaran|问题|痛点",
    r"\b(solution[s]?|how we help|our services|what we offer|benefits?|features?)\b|penyelesaian|解决方案|解決方案",
    r"\b(testimonials?|reviews?|case studies|proven results|our results|credentials|certifications|awards)\b|ulasan|testimoni|客户评价|客戶評價|客户案例|客戶案例",
    r"\b(get started|contact us|book (?:a|your)|request a quote|join us)\b|hubungi|联系我们|聯繫我們|立即预约|立即預約",
))
# Aliases emitted by the Facebook fact digest or commonly found in brochures.
# They apply only to the scoped conversion sources, not existing URL recipes.
_FACT_ROLES = {
    FactLabel.PRODUCTS.casefold(): 1, "services": 1,
    FactLabel.REVIEWS.casefold(): 2,
    "contact": 3, "get in touch": 3,
}
# The Facebook digest's own labels, plus the phone spellings brochures use.
_SOURCE_LABELS = FACT_LABELS | {"telephone", "tel"}
_SEQUENTIAL = re.compile(r"\b(step\s*\d|phase\s*\d|timeline|history)\b|langkah\s*\d|步骤|步驟", re.I)
_MIN_CONTENT_CHARS = 40
_MAX_JOURNEY_SECTIONS = 12
_EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE = re.compile(r"\b(?:phone|telephone|tel)\s*:\s*(\+?[\d ()-]{7,24}\d)", re.I)
_URL = re.compile(r"https?://[^\s<>\"“”]+", re.I)
_HEADING_PREFIX = re.compile(r"^(?:the|our|your|customer|client|more)\s+", re.I)


def heading_role(heading: str, *, fact_labels: bool = False) -> int | None:
    matches = [i for i, pattern in enumerate(_ROLE_PATTERNS) if pattern.search(heading)]
    if fact_labels:
        alias = _FACT_ROLES.get(heading.strip().casefold())
        if alias is not None and alias not in matches:
            matches.append(alias)
    return matches[0] if len(matches) == 1 else None


def section_role(section: SectionCandidate, *, fact_labels: bool = False) -> int | None:
    text = " ".join([section.prose, *[card.body for card in section.cards]]).strip()
    if len(text) < _MIN_CONTENT_CHARS or section.card_kind in {"steps", "people", "documents"}:
        return None
    if _SEQUENTIAL.search(section.heading):
        return None
    return heading_role(section.heading, fact_labels=fact_labels)


def _text_sections(source: SourceContent) -> list[SectionCandidate]:
    """Use exact extracted headings or short, explicitly marked role headings."""
    known = {h.strip().casefold().rstrip(":") for h in source.headings if h.strip()}
    if source.title:
        known.add(source.title.strip().casefold())
    sections: list[SectionCandidate] = []
    current: SectionCandidate | None = None
    lines: list[str] = []

    def read_line(line: str) -> None:
        nonlocal current, lines
        text = line.strip()
        if not text:
            return
        marked = text.startswith("#")
        clean = text.lstrip("#").strip() if marked else text
        label, separator, remainder = clean.partition(":")
        label = label.strip()
        key = label.casefold()
        short_heading = len(label) <= 100 and len(label.split()) <= 12 and not re.search(r"[.!?\"“”]", label)
        explicit_role = short_heading and heading_role(label, fact_labels=True) is not None
        bare_label = _HEADING_PREFIX.sub("", label, count=1)
        bare_role = explicit_role and (
            key in _FACT_ROLES
            or any(pattern.fullmatch(label) or pattern.fullmatch(bare_label) for pattern in _ROLE_PATTERNS)
        )
        boundary = (
            clean.casefold().rstrip(":") in known
            or (separator and key in _SOURCE_LABELS)
            or (explicit_role and (separator or marked or bare_role))
            or (short_heading and _SEQUENTIAL.search(label))
        )
        if boundary:
            if current is not None:
                current.prose = "\n".join(lines)
            current = SectionCandidate(heading=label if separator else clean, level=2)
            lines = []
            sections.append(current)
            if separator and remainder.strip():
                # Facebook may put a labelled multiline brief inside About.
                # Inspect its first line too, without treating arbitrary body
                # sentences mentioning "problem" as a new heading.
                if key in _SOURCE_LABELS:
                    read_line(remainder)
                else:
                    lines.append(remainder.strip())
        elif current is not None:
            lines.append(text)

    # A discovered PDF page's title may be absent from its body text.
    if source.title and heading_role(source.title, fact_labels=True) is not None:
        read_line(source.title)
    for line in source.raw_text.splitlines():
        read_line(line)
    if current is not None:
        current.prose = "\n".join(lines)
    return sections


def journey_sections(source: SourceContent) -> list[tuple[int, SectionCandidate]]:
    """Complete independent evidence, or [] so the selected recipe is retained."""
    if source.source_kind not in CONVERSION_SOURCE_KINDS:
        return []
    sections = source.section_candidates or _text_sections(source)
    if any(s.card_kind == "steps" or _SEQUENTIAL.search(s.heading) for s in sections):
        return []
    evidence = [
        (role, section) for section in sections
        if (role := section_role(section, fact_labels=True)) is not None
    ]
    if not 3 <= len(evidence) <= _MAX_JOURNEY_SECTIONS or not {0, 1, 2}.issubset(role for role, _ in evidence):
        return []
    return evidence


def homepage_source(source: SourceContent) -> SourceContent:
    """A PDF homepage can summarize its own document's discovered pages.

    The document splitter may have made Solutions/Reviews separate pages. Their
    facts can support a homepage overview without removing or rewriting those
    pages. An already-complete homepage stays page-local; other kinds never merge.
    """
    if source.source_kind != "pdf" or not source.discovered_pages or journey_sections(source):
        return source
    texts = [source.raw_text]
    headings = list(source.headings)
    for page in source.discovered_pages:
        if page.source_kind != "pdf":
            continue
        if page.raw_text.strip() and page.raw_text.strip() not in source.raw_text:
            texts.append("\n".join(part for part in (page.title, page.raw_text) if part))
        headings.extend(part for part in [page.title, *page.headings] if part)
    return source.model_copy(update={
        "raw_text": "\n\n".join(texts),
        "headings": list(dict.fromkeys(headings)),
        # This broader text projection is not the entry page's old section tree.
        "section_candidates": [],
    })


def text_action_links(source: SourceContent) -> list[str]:
    """Only explicit destinations; PDF readers currently leave links empty."""
    if source.source_kind not in CONVERSION_SOURCE_KINDS:
        return []
    emails = (f"mailto:{match.group(0)}" for match in _EMAIL.finditer(source.raw_text))
    phones = (
        f"tel:{match.group(1).strip()}" for match in _PHONE.finditer(source.raw_text)
        if 7 <= sum(char.isdigit() for char in match.group(1)) <= 15
    )
    urls = (match.group(0).rstrip(".,;)'") for match in _URL.finditer(source.raw_text))
    links: list[str] = []
    seen: set[str] = set()
    for href in chain(emails, phones, urls):
        if href in seen:
            continue
        links.append(href)
        seen.add(href)
        if len(links) == 15:
            break
    return links
