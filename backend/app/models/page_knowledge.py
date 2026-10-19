"""What a single-page source is ABOUT, structured from its own words.

Produced by `services/facebook_knowledge.py` from a Facebook Page's About,
products, posts and events: the business's offerings (programmes, services,
products) and facilities, each pointing back at the posts that state it. It is
structure over the source, never new copy — every name is a verbatim span of
its own evidence and every body is that evidence's own lines — so a consumer
may plan pages and cards from it with no grounding exemption anywhere.

Photos are stored by IDENTITY (`image_urls.photo_identity`), never by URL, and
are looked up in the source's live photo pool: a signed link renewed at
generation still matches, and a photo the stock-only mode or a dead link
removed can never come back through this model.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

OfferingKind = Literal["programme", "service", "product"]


class Offering(BaseModel):
    name: str
    kind: OfferingKind = "service"
    # The units' own cleaned lines, in source order: what the source says
    # about this offering, verbatim.
    body: str = ""
    # The permalink of its first post, and that post's date.
    link: str | None = None
    published_at: str | None = None
    # The pictures its own posts show, best first, by photo identity.
    photo_ids: list[str] = Field(default_factory=list)
    # Characters of evidence no other offering shares — what a page of its own
    # would have to be written from.
    own_chars: int = 0


class Facility(BaseModel):
    name: str
    body: str = ""
    photo_ids: list[str] = Field(default_factory=list)


class StaleItem(BaseModel):
    """A time-bound post or a past event left out of the grounding text."""

    label: str
    date: str | None = None


class PageKnowledge(BaseModel):
    offerings: list[Offering] = Field(default_factory=list)
    facilities: list[Facility] = Field(default_factory=list)
    stale: list[StaleItem] = Field(default_factory=list)
    # "llm" when the understanding call answered and validated; "none" when it
    # was off or failed — the deterministic half (past events, caption
    # categories) still ran.
    structured_by: Literal["llm", "none"] = "none"
