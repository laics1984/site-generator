"""Fit a staff biography onto a team card using the source's own sentences.

A grid introduces people; a person's own page tells their story. So a card
wants two or three sentences and a profile page wants all of them — and the
difference is editorial, which is the one kind of judgement a heuristic cannot
make. `truncate_bio`'s boundary cut is the deterministic answer, and it is a
bound, not an edit: it keeps whatever comes first, so a bio that opens with
where someone grew up loses the qualification that made them worth introducing.

**The model returns sentence numbers, never text.** It says which of a bio's
sentences to keep; this module slices them. That is the same contract
`paste_structure` runs on, and it buys the same three properties:

1. **Every surviving word is verbatim source text.** A condensed bio is a
   subsequence of the original by construction, so it passes
   `clean_team_bio`'s verbatim haystack and every other grounding gate with no
   exemption anywhere. Nothing downstream learns that this pass exists.
2. **A credential cannot be invented.** This matters more here than anywhere
   else in the pipeline: a bio is a claim about a named, identifiable person's
   professional qualifications, published under their photograph. A free
   rewrite could turn "practising obstetrician and gynaecologist, then a
   gynaecologic oncologist" into "senior cancer specialist" — fluent, plausible
   and false. An index cannot do that.
3. **The reply is small however long the biographies are**, and one call covers
   a whole block.

Failure is always a fallback, never an error: no LLM configured, the flag off,
an `LlmError`, or indices that don't line up all leave every bio exactly as it
was. A shortened card is an improvement; a missing one is a regression.
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

from app.config import settings
from app.services.llm import LlmClient, LlmError, chat_json_cached, get_llm
from app.services.profile_text import join_bio_segments, split_bio_segments
from app.services.prompts import TEAM_BIO_CONDENSE_PROMPT

logger = logging.getLogger(__name__)


class CondensedBio(BaseModel):
    """One person's kept sentences, addressed by index."""

    member: int
    keep: list[int] = Field(default_factory=list)


class BioCondensePlan(BaseModel):
    bios: list[CondensedBio] = Field(default_factory=list)


def _render_prompt(entries: list[tuple[str, str]]) -> str:
    """People and their numbered sentences, each with its length.

    The length is there because the budget is arithmetic, and a model asked to
    hit a character target without it simply cannot: measured on watr.org.my's
    bios against a 400-character target, it returned 1110, 1120 and 727 — it
    dropped the hobbies and kept every professional sentence, which is the right
    ranking and the wrong length. Given the numbers it can budget; `_apply`
    still enforces the total, because ranking is the judgement worth asking for
    and addition is not.
    """
    blocks = []
    for index, (name, bio) in enumerate(entries):
        numbered = "\n".join(
            f"  [{i}] ({len(sentence)} chars) {sentence}"
            for i, (_line, sentence) in enumerate(split_bio_segments(bio))
        )
        blocks.append(f"Person {index}: {name}\n{numbered}")
    return "\n\n".join(blocks)


def _apply(bio: str, keep: list[int], target_chars: int) -> str | None:
    """The kept sentences, in SOURCE order and within budget, or None.

    Sorting rather than trusting the model's order is what keeps the result a
    subsequence: a reply that reordered sentences would be rewriting the bio
    with the source's words, which is the one thing indices are meant to
    prevent. Duplicates go the same way.

    The budget is then enforced HERE rather than trusted to the reply. The
    division is the pipeline's usual one — the model supplies the judgement
    (which sentences carry this person), deterministic code does the
    arithmetic — and without it the pass silently under-delivers: a 1385-char
    bio came back at 1110 against a 400-char target.

    Trailing sentences go first. A biography is front-loaded by convention —
    who someone is, then how they got there — which is the same bet
    `truncate_bio` makes, one granularity up. The FIRST KEPT sentence is never
    trimmed away, so a card always says something about the person; which
    sentence that is remains the model's call, and it legitimately skips a
    scene-setting opener for the one that states the qualification.
    """
    segments = split_bio_segments(bio)
    wanted = sorted({index for index in keep if 0 <= index < len(segments)})
    if not wanted:
        return None
    while len(wanted) > 1 and _length_of(segments, wanted) > target_chars:
        wanted.pop()
    if len(wanted) == len(segments):
        # Nothing dropped — leave the bio exactly as it was rather than
        # round-tripping it through the splitter.
        return None
    condensed = join_bio_segments([segments[index] for index in wanted])
    # A subsequence can never be longer, so this only fires on a degenerate
    # rejoin; treating it as a failure is cheaper than reasoning about it.
    return condensed if condensed and len(condensed) < len(bio) else None


def _length_of(segments: list[tuple[int, str]], wanted: list[int]) -> int:
    """What `join_bio_segments` will produce, measured without building it."""
    return len(join_bio_segments([segments[index] for index in wanted]))


async def condense_bios(
    entries: list[tuple[str, str]],
    *,
    target_chars: int,
    client: LlmClient | None = None,
) -> dict[int, str]:
    """Condensed bios by entry index. Entries it leaves out keep what they had.

    ``entries`` are ``(name, bio)`` for ONE team block, so the model sees the
    people together — which is what lets it drop a fact one card states twice —
    and can never move a fact between two different sections' rosters.
    """
    if not entries:
        return {}
    if client is None:
        client = get_llm()
    if client is None:
        return {}

    try:
        plan = await chat_json_cached(
            client,
            system_prompt=TEAM_BIO_CONDENSE_PROMPT.format(target=target_chars),
            user_prompt=_render_prompt(entries),
            schema=BioCondensePlan,
        )
    except LlmError as exc:
        logger.info("Bio condense: LLM declined (%s); keeping full bios", exc)
        return {}
    except Exception:
        logger.exception("Bio condense failed; keeping full bios")
        return {}

    condensed: dict[int, str] = {}
    for item in plan.bios:
        if not 0 <= item.member < len(entries):
            continue
        result = _apply(entries[item.member][1], item.keep, target_chars)
        if result is not None:
            condensed[item.member] = result
    return condensed
