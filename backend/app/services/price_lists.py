"""What a source states about price — the one home for "is this a price list".

Four readers ask it, and they used to ask it three different ways:

- ``page_inference`` earns a page its pricing section when the copy states a
  price list (``states_price_list``).
- ``source_router.split_raw_text`` keeps a price list inside ONE planner chunk
  (``price_list_spans``). A long page is planned in ~6,000-character chunks and
  every chunk writes its own pricing block; when the cut fell between two plans,
  no chunk saw the whole table. Fowable's brief ended a chunk on Growth's
  feature list and opened the next on Growth's price, and the seven chunks
  wrote seven different tables — one package "From RM 119", Growth "Contact for
  pricing", Growth at Starter's price.
- ``planner`` keeps the chunk's pricing block that states the most DIFFERENT
  prices (``amounts``): three plans all "From RM 119" is one price restated.
- ``scaffold_enforcement`` keeps a tier only when its amounts are the source's.
"""

from __future__ import annotations

import re
from typing import Collection

# A stated price: a currency code or symbol and an amount.
PRICE = re.compile(
    r"(?:\b(?:RM|MYR|USD|SGD|IDR|THB|EUR|GBP)\s*\d+(?:[.,]\d+)?|[$€£]\s*\d+(?:[.,]\d+)?)",
    re.I,
)

# A price list states several prices, each for a period: "RM 119 / month",
# "RM 1,428 a year", "$29/mo". One price is a fee mentioned in passing; two
# DIFFERENT recurring amounts are plans, packages or fee tiers.
_PRICE_PERIOD = re.compile(
    rf"(?P<price>{PRICE.pattern})\s*(?:/|per|an?|each|every)?\s*"
    r"(?:month|mo|year|yr|annum|week|term|semester|session|class|lesson|hour|hr)s?\b"
    r"|(?P<billed>" + PRICE.pattern + r")[^.\n]{0,20}\b(?:monthly|annually|yearly|weekly)\b",
    re.IGNORECASE,
)
_PRICE_LIST_MIN = 2

_AMOUNT = re.compile(r"\d[\d,]*(?:\.\d+)?")

# Two price lines further apart than this are two mentions, not one list. A
# plan card runs ~600 characters in a designed brief (Fowable's longest is
# Growth's); twice that is still one table.
_PRICE_LIST_GAP_CHARS = 1200


def amounts(text: str) -> set[str]:
    """The numbers a text states, commas ignored ("RM 1,428" → "1428")."""
    return {match.group(0).replace(",", "") for match in _AMOUNT.finditer(text or "")}


def _period_amounts(text: str) -> set[str]:
    return {
        re.sub(r"\s+", "", (match.group("price") or match.group("billed")).lower())
        for match in _PRICE_PERIOD.finditer(text)
    }


def states_price_list(text: str) -> bool:
    """Two or more different amounts, each with a billing period."""
    return len(_period_amounts(text)) >= _PRICE_LIST_MIN


def price_list_spans(lines: list[str], headings: Collection[str]) -> list[tuple[int, int]]:
    """Inclusive line ranges, each holding one price list whole.

    A list is a run of price-bearing lines no more than ``_PRICE_LIST_GAP_CHARS``
    apart that states a price list. It opens at its heading — and the headings
    directly above that, so "Simple plans…" travels with its plans — and closes
    before the next heading after its last price. ``headings`` is matched
    case-insensitively against whole lines, as the splitter matches them.
    """
    heading_set = {h.strip().lower() for h in headings if h.strip()}

    def is_heading(index: int) -> bool:
        return lines[index].strip().lower() in heading_set

    priced = [i for i, line in enumerate(lines) if PRICE.search(line)]
    runs: list[list[int]] = []
    for index in priced:
        if runs and sum(len(lines[i]) + 1 for i in range(runs[-1][-1] + 1, index)) <= _PRICE_LIST_GAP_CHARS:
            runs[-1].append(index)
        else:
            runs.append([index])

    def within_reach(first: int, last: int) -> bool:
        return sum(len(lines[i]) + 1 for i in range(first, last + 1)) <= _PRICE_LIST_GAP_CHARS

    spans: list[tuple[int, int]] = []
    for run in runs:
        if not states_price_list("\n".join(lines[i] for i in run)):
            continue
        start = run[0]
        while start > 0 and not is_heading(start) and within_reach(start - 1, run[0] - 1):
            start -= 1
        if is_heading(start):
            while start > 0 and is_heading(start - 1):
                start -= 1
        else:
            start = run[0]
        end = run[-1]
        while end + 1 < len(lines) and not is_heading(end + 1) and within_reach(run[-1] + 1, end + 1):
            end += 1
        spans.append((start, end))
    return spans
