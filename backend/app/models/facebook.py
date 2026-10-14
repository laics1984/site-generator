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

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# How the Page was read. Mirrored in frontend/src/lib/types.ts — the UI tells
# the user which path ran, because it explains what's missing and what would fix
# it. `render_session` is a render signed in with a saved browser session
# (services/browser_session.py) — a rescue for a Page that refuses a logged-out
# visitor, and a thinner read than the anonymous one everywhere else.
FetchPath = Literal["graph", "render", "render_session"]


class FactLabel(StrEnum):
    """The labels `facebook_source.build_raw_text` writes into the fact digest.

    One spelling, because the digest is also PARSED: `landing_source` splits
    it back into sections at these labels, so a label written here and missing
    there glues one fact onto the previous section.
    """

    ABOUT = "About"
    CATEGORIES = "Categories"
    MISSION = "Mission"
    PRODUCTS = "Products and services"
    SERVICE_OPTIONS = "Service options"
    FOUNDED = "Founded"
    PRICE_RANGE = "Price range"
    ADDRESS = "Address"
    SERVICE_AREA = "Service area"
    PHONE = "Phone"
    EMAIL = "Email"
    WEBSITE = "Website"
    ALSO_ON = "Also on"
    HOURS = "Opening hours"
    UPDATES = "Updates from the Page"
    EVENTS = "Events from the Page"
    REVIEWS = "What customers say"
    FOLLOWERS = "Followers on Facebook"
    RATING = "Rating"


FACT_LABELS = frozenset(label.casefold() for label in FactLabel)

# How a Page says it serves people — Facebook's own closed vocabulary, shown
# as one `·`-separated Intro line ("Takeaway · Roadside collection"). A line is
# read as service options only when EVERY item is in this set, the same bet
# `facebook_dom._CHROME_VALUES` makes: Facebook's words, never a business's.
# Both spellings Facebook ships (en-GB and en-US) are listed.
SERVICE_OPTIONS = frozenset({
    "dine-in", "takeaway", "takeout", "delivery", "no-contact delivery",
    "drive-through", "drive-thru", "kerbside pickup", "curbside pickup",
    "kerbside collection", "roadside collection", "roadside pickup",
    "in-store collection", "in-store pickup", "in-store shopping",
    "outdoor seating", "catering", "reservations", "walk-ins welcome",
    "good for groups", "good for kids", "table service",
})

# Graph's `restaurant_services` flags → the names the Page itself displays.
GRAPH_SERVICE_OPTIONS = {
    "delivery": "Delivery", "takeout": "Takeaway", "pickup": "In-store collection",
    "outdoor": "Outdoor seating", "catering": "Catering", "reserve": "Reservations",
    "walkins": "Walk-ins welcome", "groups": "Good for groups", "kids": "Good for kids",
    "waiter": "Table service",
}


class FacebookHours(BaseModel):
    """One day's opening hours, exactly as the Page states them."""

    day: str  # "Monday"
    opens: str  # "09:00"
    closes: str  # "18:00"

    def as_line(self) -> str:
        return f"{self.day} {self.opens}–{self.closes}"


class FacebookPhoto(BaseModel):
    url: str
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    # Facebook's own automatic description of the picture — its
    # `accessibility_caption`, "May be an image of child, gelato and text".
    # Machine-made and hedged, so it is evidence about the pixels for matching
    # and screening, never a caption the business wrote and never site copy.
    auto_alt: str | None = None


class FacebookVideo(BaseModel):
    """A video a post carries. The frame Facebook shows for it is a picture of
    the business like any photo; the video itself is never downloaded."""

    url: str  # the video's own permalink on facebook.com
    video_id: str | None = None
    thumbnail_url: str | None = None
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    duration_ms: int | None = Field(default=None, ge=0)


class FacebookPost(BaseModel):
    """A published post. Used as grounding text and as a picture source.

    Deliberately never rendered as a dated feed: "15% off this Friday" is true
    on the Page and wrong baked into permanent site copy.
    """

    message: str | None = None
    created_time: str | None = None
    image_url: str | None = None
    permalink: str | None = None
    post_id: str | None = None
    photos: list[FacebookPhoto] = Field(default_factory=list)
    videos: list[FacebookVideo] = Field(default_factory=list)

    @property
    def photo_sources(self) -> list[FacebookPhoto]:
        """Attachment originals first, with the legacy full-picture fallback."""
        photos = list(self.photos)
        if self.image_url and all(p.url != self.image_url for p in photos):
            photos.append(FacebookPhoto(url=self.image_url))
        return photos

    @property
    def video_frames(self) -> list[FacebookPhoto]:
        """The frame Facebook shows for each video, as a picture. Its size is
        the pixel pass's to measure: the video's own size is not the frame's."""
        return [FacebookPhoto(url=video.thumbnail_url) for video in self.videos if video.thumbnail_url]

    @property
    def pictures(self) -> list[FacebookPhoto]:
        """Every picture the post shows — its photos, then its video frames —
        all of them illustrating the same caption."""
        photos = self.photo_sources
        known = {photo.url for photo in photos}
        return [*photos, *(frame for frame in self.video_frames if frame.url not in known)]


class FacebookEvent(BaseModel):
    """An event the Page published. Grounding, like a post, and never a dated
    listing on the site: a past event is history, a future one goes stale."""

    name: str
    description: str | None = None
    start_time: str | None = None
    place: str | None = None
    url: str | None = None


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
    service_options: list[str] = Field(default_factory=list)

    # --- contact ----------------------------------------------------------
    phone: str | None = None
    emails: list[str] = Field(default_factory=list)
    website: str | None = None
    # Profiles on other platforms the Page links to (Instagram, TikTok…), as
    # URLs `nav_extraction.social_link_for_href` accepted. Labels are derived
    # from that same classifier wherever they are shown, so they can't drift.
    social_profiles: list[str] = Field(default_factory=list)
    service_area: str | None = None
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
    events: list[FacebookEvent] = Field(default_factory=list)

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
        """De-duplicated post pictures (photos and video frames), in post order."""
        seen: list[str] = []
        for post in self.posts:
            for photo in post.pictures:
                if photo.url not in seen:
                    seen.append(photo.url)
        return seen

    @property
    def photo_urls(self) -> list[str]:
        """Every picture this read names — mark, cover and post photos."""
        heads = [url for url in (self.profile_picture_url, self.cover_photo_url) if url]
        return [*heads, *(url for url in self.post_photo_urls if url not in heads)]

    @property
    def posts_with_text(self) -> list[FacebookPost]:
        return [p for p in self.posts if (p.message or "").strip()]
