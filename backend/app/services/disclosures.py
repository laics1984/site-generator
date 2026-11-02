"""A regulated business's own disclosure line, carried to its new footer.

A licensed financial business states who licenses or regulates it — usually
one line in the site footer ("XYZ Sdn Bhd is licensed by Bank Negara Malaysia
under the Financial Services Act 2013"). The crawl reads every page's footer
already and throws it away as chrome, so the generated site dropped the one
sentence such a business is required to show.

**Verbatim or nothing.** A sentence is kept only when it pairs a regulatory
verb with a named authority, and it is copied exactly as the source wrote it —
nothing here composes, summarises or translates a claim about a licence, and a
source that states none gets no line. It is read from markup the crawl already
fetched; it costs no request.

Shown for the SaaS family, fintech included (`family_evidence.footer_facts`,
the one gate), so every archetype in the family inherits it.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from app.services.family_evidence import stated_sentences

if TYPE_CHECKING:
    from bs4 import BeautifulSoup

# Both halves must be in one sentence: a verb alone ("registered users") or an
# authority alone ("news from the central bank") states no licence.
_VERB = re.compile(
    r"\b(licen[cs]ed|regulated|authori[sz]ed|registered|approved|supervised|"
    r"a member of|holds? an? \w+ licen[cs]e)\b",
    re.IGNORECASE,
)
_AUTHORITY = re.compile(
    r"\b(Bank Negara|Securities Commission|Monetary Authority|Financial Conduct "
    r"Authority|Prudential Regulation|Financial Services (Authority|Commission)|"
    r"Central Bank|Reserve Bank|Bank of [A-Z][a-z]+|Labuan (FSA|Financial)|PIDM|"
    r"FINRA|FDIC|SEC|FCA|MAS|BNM|OJK|BSP|ASIC|AUSTRAC|FinCEN|"
    r"Financial Services Act|Money Services Business|Payment Services Act)\b"
)
_MAX_CHARS = 320
_MAX_LINES = 2
_FOOTER_HINT = re.compile(r"footer", re.IGNORECASE)


def regulatory_sentences(text: str) -> list[str]:
    """The sentences of `text` that state a licence or a regulator, verbatim
    (whitespace collapsed), in order, at most `_MAX_LINES`."""
    return stated_sentences(text, _VERB, _AUTHORITY, max_chars=_MAX_CHARS, limit=_MAX_LINES)


def from_footer(soup: BeautifulSoup) -> list[str]:
    """Disclosure sentences in the page's footer — `<footer>` or a container
    the markup names as one (page builders rarely use the element)."""
    blocks = list(soup.find_all("footer"))
    if not blocks:
        blocks = [
            tag for tag in soup.find_all(("div", "section"))
            if _FOOTER_HINT.search(" ".join(tag.get("class") or []) + " " + (tag.get("id") or ""))
        ][:3]
    found: list[str] = []
    for block in blocks:
        for sentence in regulatory_sentences(block.get_text("\n")):
            if sentence not in found:
                found.append(sentence)
    return found[:_MAX_LINES]
