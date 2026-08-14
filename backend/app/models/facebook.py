"""The Facebook Page a site can be built from.

This model is the anti-hallucination boundary. A fact that is not a field here
cannot reach the generated site: the reader fills it, the mapper turns it into
`SourceContent` + a contact dict, and `facebook_authority` overwrites whatever
the LLM wrote for the fact-bearing blocks with these values. Nothing else is
allowed to become a claim about the business.

So every field is Optional and nothing is invented to fill a gap — an absent
field means "the Page didn't say", which downstream turns into a *missing*
section, never a padded one.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FetchPath = Literal["graph", "render"]


class FacebookHours(BaseModel):
    """One day's opening hours, exactly as the Page states them."""

    day: str  # "Monday"
    opens: str  # "09:00"
    closes: str  # "18:00"

    def as_line(self) -> str:
        return f"{self.day} {self.opens}–{self.closes}"


class FacebookPost(BaseModel):
    """A published post. Used as grounding text and as a photo source.

    Deliberately never rendered as a dated feed: "15% off this Friday" is true
    on the Page and wrong baked into permanent site copy.
    """

    message: str | None = None
    created_time: str | None = None
    image_url: str | None = None
    permalink: str | None = None


class FacebookReview(BaseModel):
    """A customer recommendation, copied verbatim with its real attribution."""

    text: str
    author: str
    rating: float | None = None
    created_time: str | None = None


class FacebookPage(BaseModel):
    # --- identity ---------------------------------------------------------
    name: str
    canonical_url: str
    page_id: str | None = None
    username: str | None = None
    category: str | None = None
    categories: list[str] = Field(default_factory=list)

    # --- copy -------------------------------------------------------------
    about: str | None = None
    description: str | None = None
    mission: str | None = None
    products: str | None = None
    founded: str | None = None
    price_range: str | None = None

    # --- contact ----------------------------------------------------------
    phone: str | None = None
    emails: list[str] = Field(default_factory=list)
    website: str | None = None
    street: str | None = None
    city: str | None = None
    state: str | None = None
    zip_code: str | None = None
    country: str | None = None
    single_line_address: str | None = None
    hours: list[FacebookHours] = Field(default_factory=list)

    # --- social proof -----------------------------------------------------
    fan_count: int | None = None
    rating_count: int | None = None
    overall_star_rating: float | None = None
    reviews: list[FacebookReview] = Field(default_factory=list)

    # --- media ------------------------------------------------------------
    profile_picture_url: str | None = None
    cover_photo_url: str | None = None
    posts: list[FacebookPost] = Field(default_factory=list)

    # --- provenance -------------------------------------------------------
    # Drives the preview's trust surface. Because the user never explicitly
    # chose "Facebook mode" — we routed their link automatically — the preview
    # has to be unambiguous about what was actually read.
    fetched_via: FetchPath = "graph"
    partial: bool = Field(
        default=False,
        description=(
            "True when some fields could not be read: the render fallback was "
            "used, or the token lacked permission for an optional field group."
        ),
    )
    missing_fields: list[str] = Field(
        default_factory=list,
        description="Field-group names that could not be read, for the UI checklist.",
    )

    # ------------------------------------------------------------------ #
    # Derived views. Kept here so the mapper, the authority pass and the UI
    # all agree on what "has an address" means.
    # ------------------------------------------------------------------ #

    @property
    def address_line(self) -> str | None:
        """The best single-line address the Page gives, or None."""
        if self.single_line_address:
            return self.single_line_address.strip() or None
        parts = [self.street, self.city, self.state, self.zip_code, self.country]
        joined = ", ".join(p.strip() for p in parts if p and p.strip())
        return joined or None

    @property
    def hours_text(self) -> str | None:
        """Opening hours as one readable string, or None."""
        if not self.hours:
            return None
        return "; ".join(h.as_line() for h in self.hours)

    @property
    def primary_email(self) -> str | None:
        return self.emails[0] if self.emails else None

    @property
    def post_photo_urls(self) -> list[str]:
        """De-duplicated post images, newest first, order preserved."""
        seen: list[str] = []
        for post in self.posts:
            if post.image_url and post.image_url not in seen:
                seen.append(post.image_url)
        return seen

    @property
    def posts_with_text(self) -> list[FacebookPost]:
        return [p for p in self.posts if (p.message or "").strip()]
