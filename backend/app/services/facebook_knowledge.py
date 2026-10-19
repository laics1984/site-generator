"""Understand what a Facebook Page's business offers, from its own words.

A Page states its programmes, services and facilities nowhere as structure:
they are words inside an About blurb and a feed of captions. Nothing could
gate a page, a card or a gallery group on them, so a kindergarten that plainly
runs a Nursery and a Preschool programme got a homepage and no Programmes
page. This module adds the one structuring step that was missing, on the
contract `paste_structure`, `bio_condense` and `record_pages` already run on:

**The model answers in numbers; deterministic code does the rest.** The Page's
facts are cut into numbered evidence units (About, products, each post, each
event). ONE `chat_json_cached` call says, per unit, which category of the
photo profile's vocabulary it shows, whether it is lasting or time-bound, and
which units state which offering or facility. Then validation **discards and
never patches**:

- any index out of range discards the whole reply (a model that misnumbered
  one unit has misread the list);
- a category outside the profile's vocabulary is dropped;
- an offering or facility NAME must be a verbatim span of one of its own units
  (`scaffold_enforcement.is_grounded_in_source`) — an invented "Montessori
  Programme" is dropped;
- an offering needs at least one relevant unit of its own.

Nothing the call returns reaches the site as prose: an offering's body is its
units' own cleaned lines. A prompt injection in a caption can at worst move an
index.

**What is no longer true stays out of the grounding text** (`is_stale`). A past
event always — its date says so, so that half needs no model and runs with the
call off. A post the call marks time-bound once it is older than
`FACEBOOK_STALE_POST_DAYS`. The digest is rebuilt without them
(`facebook_source.build_raw_text(exclude=)`), and their photos stay in the pool
with the caption taken off, so "50% off this weekend" never reaches copy while
the picture of the business still can.

Every failure (flag off, no client, `LlmError`, an invalid reply) leaves no
offerings and no facilities, which is exactly the read without this module.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from pydantic import BaseModel, Field

from app.config import settings
from app.models.content_blocks import ImageMetadata, SourceContent
from app.models.facebook import FacebookPage, FacebookPost
from app.models.page_knowledge import Facility, Offering, OfferingKind, PageKnowledge, StaleItem
from app.services.facebook_photo_profiles import PhotoProfile, profile_for
from app.services.facebook_source import build_raw_text, clean_caption, event_is_past
from app.services.llm import LlmClient, LlmError, chat_json_cached, get_llm
from app.services.offering_pages import picture_id
from app.services.prompts import FACEBOOK_KNOWLEDGE_PROMPT
from app.services.scaffold_enforcement import is_grounded_in_source

logger = logging.getLogger(__name__)

# What a unit shows the model; a caption past this is the same post repeating.
_UNIT_PROMPT_CHARS = 600
_MAX_NAME_CHARS = 80


# --- evidence units -------------------------------------------------------------


@dataclass
class _Unit:
    index: int
    label: str  # "About", "Products", "Post 2026-10-01 (3 photos)", "Event …"
    text: str
    post: FacebookPost | None = None
    event_past: bool = False
    published_at: str | None = None
    picture_ids: list[str] = field(default_factory=list)


def evidence_units(page: FacebookPage, *, today: date) -> list[_Unit]:
    """The Page's facts as numbered units, built deterministically."""
    units: list[_Unit] = []

    def add(label: str, text: str | None, **extra) -> None:
        text = (text or "").strip()
        if text and len(units) < settings.facebook_knowledge_max_units:
            units.append(_Unit(index=len(units), label=label, text=text, **extra))

    add("About", "\n".join(part for part in (page.about, page.description, page.mission) if part))
    add("Products and services", page.products)
    for post in page.posts:
        caption = clean_caption(post.message)
        if not caption:
            continue
        pictures = [picture_id(photo.url) for photo in post.pictures if photo.url]
        day = (post.created_time or "")[:10]
        count = len(pictures)
        label = "Post" + (f" {day}" if day else "") + (
            f" ({count} photo{'s' if count != 1 else ''})" if count else "")
        add(label, caption, post=post, published_at=post.created_time, picture_ids=pictures)
    for event in page.events:
        day = (event.start_time or "")[:10]
        add(f"Event {day}".strip(), "\n".join(p for p in (event.name, event.description) if p),
            event_past=event_is_past(event, today), published_at=event.start_time)
    return units


# --- the reply ------------------------------------------------------------------


class UnitReading(BaseModel):
    unit: int
    category: str | None = None
    relevant: bool = True
    temporal: str = "lasting"  # "lasting" | "time_bound"; anything else is lasting


class NamedReading(BaseModel):
    name: str
    kind: str = "service"
    units: list[int] = Field(default_factory=list)


class KnowledgeReply(BaseModel):
    units: list[UnitReading] = Field(default_factory=list)
    offerings: list[NamedReading] = Field(default_factory=list)
    facilities: list[NamedReading] = Field(default_factory=list)


def _render_units(units: list[_Unit]) -> str:
    return "\n\n".join(f"[{u.index}] {u.label}: {u.text[:_UNIT_PROMPT_CHARS]}" for u in units)


def _vocabulary(profile: PhotoProfile) -> str:
    return ", ".join(subject.name for subject in profile.subject_hints) or "(none)"


def _in_range(reply: KnowledgeReply, count: int) -> bool:
    indices = [r.unit for r in reply.units] + [
        i for named in (*reply.offerings, *reply.facilities) for i in named.units
    ]
    return all(0 <= i < count for i in indices)


_OFFERING_KINDS: dict[str, OfferingKind] = {
    "programme": "programme", "program": "programme", "class": "programme",
    "service": "service", "package": "service",
    "product": "product",
}


def _grounded_name(name: str, units: list[_Unit]) -> str | None:
    name = re.sub(r"\s+", " ", name).strip(" -–—:·")
    if not name or len(name) > _MAX_NAME_CHARS:
        return None
    return name if any(is_grounded_in_source(name, unit.text) for unit in units) else None


# --- applying it ----------------------------------------------------------------


def is_stale(unit: _Unit, temporal: str, *, today: date) -> bool:
    """The one stale rule: a past event, or a time-bound post past the window."""
    if unit.event_past:
        return True
    if temporal != "time_bound" or unit.post is None or not unit.published_at:
        return False
    try:
        published = datetime.fromisoformat(unit.published_at.replace("Z", "+00:00")).date()
    except ValueError:
        return False
    return today - published > timedelta(days=settings.facebook_stale_post_days)


def _ranked_ids(ids: list[str], pool: dict[str, ImageMetadata]) -> list[str]:
    from app.services.facebook_photo_selection import photo_rank

    present = [i for i in dict.fromkeys(ids) if i in pool]
    return sorted(present, key=lambda i: photo_rank(pool[i]), reverse=True)


def _named(
    readings: list[NamedReading], units: list[_Unit], relevant: set[int],
    pool: dict[str, ImageMetadata],
) -> list[tuple[str, str, list[_Unit], list[str]]]:
    """(name, kind, own units, photo ids) for each reading that validates."""
    out: list[tuple[str, str, list[_Unit], list[str]]] = []
    seen: set[str] = set()
    for reading in readings:
        own = [units[i] for i in dict.fromkeys(reading.units) if i in relevant]
        name = _grounded_name(reading.name, own) if own else None
        if name is None or name.casefold() in seen:
            continue
        seen.add(name.casefold())
        ids = _ranked_ids([pid for unit in own for pid in unit.picture_ids], pool)
        out.append((name, reading.kind, own, ids))
    return out


def _body(units: list[_Unit]) -> str:
    return "\n\n".join(unit.text for unit in units)


def _apply_categories(
    source: SourceContent, units: list[_Unit], categories: dict[int, str],
) -> None:
    """A photo takes its post's category; caption hints are the fallback."""
    by_picture = {pid: categories[u.index] for u in units for pid in u.picture_ids if u.index in categories}
    for meta in source.image_metadata:
        category = by_picture.get(picture_id(meta.url))
        if category is None and meta.facebook_photo_review and meta.facebook_photo_review.classification:
            category = meta.facebook_photo_review.classification[0]
        meta.category = category


def _drop_stale(
    source: SourceContent, page: FacebookPage, stale: list[_Unit], *, today: date,
) -> None:
    keys = {unit.post.key for unit in stale if unit.post is not None and unit.post.key}
    source.raw_text = build_raw_text(page, exclude=keys, today=today)
    gone = {pid for unit in stale for pid in unit.picture_ids}
    for meta in source.image_metadata:
        if picture_id(meta.url) in gone:
            # The picture is still the business; the words are no longer true.
            meta.caption = ""
            meta.context_heading = ""
            meta.alt = f"{page.name} photo"


async def understand_page(
    page: FacebookPage,
    source: SourceContent,
    *,
    client: LlmClient | None = None,
    today: date | None = None,
) -> PageKnowledge:
    """Stamp `source.page_knowledge`, photo categories and the stale-free digest.

    Runs after the photo review (it ranks an offering's photos with the
    review's order) and before the homepage sections are gated, which ask how
    many offerings there are."""
    today = today or datetime.now(timezone.utc).date()
    profile = next(
        (found for meta in source.image_metadata if (found := profile_for(meta.selection_context))),
        None,
    ) or profile_for("facebook")
    units = evidence_units(page, today=today)
    reply = await _ask(units, profile, client) if units else None

    vocabulary = {subject.name for subject in profile.subject_hints}
    readings = {r.unit: r for r in (reply.units if reply else [])}
    categories = {
        i: r.category for i, r in readings.items() if r.category in vocabulary and r.relevant
    }
    relevant = {u.index for u in units if readings.get(u.index, UnitReading(unit=u.index)).relevant}
    stale = [
        u for u in units
        if is_stale(u, readings.get(u.index, UnitReading(unit=u.index)).temporal, today=today)
    ]
    relevant -= {u.index for u in stale}

    pool = {picture_id(meta.url): meta for meta in source.image_metadata}
    offerings: list[Offering] = []
    facilities: list[Facility] = []
    if reply is not None:
        named = _named(reply.offerings, units, relevant, pool)
        shared = {u.index for _, _, own, _ in named for u in own}
        counts = {i: sum(i in {u.index for u in own} for _, _, own, _ in named) for i in shared}
        for name, kind, own, ids in named:
            first = own[0]
            offerings.append(Offering(
                name=name, kind=_OFFERING_KINDS.get(kind.casefold(), "service"),
                body=_body(own), photo_ids=ids,
                link=first.post.permalink if first.post else None,
                published_at=first.published_at,
                own_chars=sum(len(u.text) for u in own if counts.get(u.index) == 1),
            ))
        facilities = [
            Facility(name=name, body=_body(own), photo_ids=ids)
            for name, _, own, ids in _named(reply.facilities, units, relevant, pool)
        ]

    _apply_categories(source, units, categories)
    if stale:
        _drop_stale(source, page, stale, today=today)
    knowledge = PageKnowledge(
        offerings=offerings, facilities=facilities,
        stale=[StaleItem(label=u.text.splitlines()[0][:80], date=(u.published_at or "")[:10] or None)
               for u in stale],
        structured_by="llm" if reply is not None else "none",
    )
    source.page_knowledge = knowledge
    logger.info(
        "Facebook knowledge for %s: %d offerings, %d facilities, %d stale (%s)",
        page.name, len(offerings), len(facilities), len(stale), knowledge.structured_by,
    )
    return knowledge


async def _ask(
    units: list[_Unit], profile: PhotoProfile, client: LlmClient | None,
) -> KnowledgeReply | None:
    if not settings.facebook_knowledge_enabled:
        return None
    client = client or get_llm()
    try:
        reply = await chat_json_cached(
            client,
            system_prompt=FACEBOOK_KNOWLEDGE_PROMPT.format(categories=_vocabulary(profile)),
            user_prompt=_render_units(units),
            schema=KnowledgeReply,
        )
    except LlmError as exc:
        logger.info("Facebook knowledge: LLM declined (%s); no offerings", exc)
        return None
    except Exception:
        logger.exception("Facebook knowledge failed; no offerings")
        return None
    if not _in_range(reply, len(units)):
        logger.info("Facebook knowledge: reply named a unit out of range; discarded")
        return None
    return reply
