"""Read a Facebook Page through the Graph API.

The sanctioned path, and the only one that returns structured hours, emails and
posts rather than whatever survives a public render. Needs a Page access token;
without one the caller falls back to `facebook_render`.

The reason this file is more than one request: **Graph fails the entire request
when a single requested field is not permitted for the token.** A token that can
read the Page but lacks `pages_read_engagement` turns one `fields=` list
containing `emails` into a total failure — no name, no About, nothing. So the
fields are split into groups and each optional group is allowed to fail on its
own, recording itself in `missing_fields` so the UI can tell the user exactly
what a better token would unlock.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings
from app.models.facebook import (
    FacebookHours,
    FacebookPage,
    FacebookPost,
    FacebookReview,
)
from app.services.facebook_urls import FacebookRef

logger = logging.getLogger(__name__)


class FacebookGraphError(Exception):
    """A Graph read that could not produce a Page. Carries a status code."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# Field groups. "core" must succeed; the rest degrade to missing_fields.
_CORE_FIELDS = (
    "id,name,username,link,category,category_list,about,description,website,"
    "picture.width(720).height(720),cover"
)
_OPTIONAL_GROUPS: dict[str, str] = {
    "contact": "phone,emails,single_line_address,location,hours",
    "profile": "mission,products,founded,price_range,fan_count",
}

# Human labels for the preview's "facts found" checklist.
GROUP_LABELS: dict[str, str] = {
    "contact": "Phone, email, address and opening hours",
    "profile": "Mission, products, founded date",
    "posts": "Recent posts and their photos",
    "reviews": "Customer recommendations",
}

_DAY_NAMES = {
    "mon": "Monday", "tue": "Tuesday", "wed": "Wednesday", "thu": "Thursday",
    "fri": "Friday", "sat": "Saturday", "sun": "Sunday",
}


def _redact(text: str, token: str) -> str:
    """Never let a token reach a log line or an error shown to the user."""
    if not token:
        return text
    return text.replace(token, "***")


def _graph_url(node: str) -> str:
    base = settings.facebook_graph_base_url.rstrip("/")
    return f"{base}/{settings.facebook_graph_version}/{node}"


async def _get(
    client: httpx.AsyncClient, node: str, fields: str, token: str
) -> dict[str, Any]:
    """One Graph read. Raises FacebookGraphError with the token redacted."""
    try:
        response = await client.get(
            _graph_url(node),
            params={"fields": fields, "access_token": token},
        )
    except httpx.HTTPError as exc:
        raise FacebookGraphError(
            _redact(f"Couldn't reach Facebook: {exc}", token)
        ) from None

    if response.status_code >= 400:
        detail = ""
        try:
            payload = response.json()
            detail = (payload.get("error") or {}).get("message") or ""
        except ValueError:
            detail = response.text[:200]
        raise FacebookGraphError(
            _redact(detail or f"Facebook returned {response.status_code}.", token),
            status=response.status_code if response.status_code < 500 else 502,
        )

    try:
        return response.json()
    except ValueError:
        raise FacebookGraphError("Facebook returned an unreadable response.") from None


def _parse_hours(raw: dict[str, Any] | None) -> list[FacebookHours]:
    """Graph gives {"mon_1_open": "09:00", "mon_1_close": "18:00", ...}.

    Multiple ranges per day are collapsed into the first open and the last
    close — a split lunch break is detail a landing page doesn't carry, and
    inventing a "closed 13:00-14:00" line the Page never stated would be a
    fabricated fact.
    """
    if not isinstance(raw, dict):
        return []
    by_day: dict[str, list[tuple[str, str]]] = {}
    for key, value in raw.items():
        parts = key.split("_")
        if len(parts) != 3 or parts[2] not in ("open", "close"):
            continue
        day = parts[0].lower()
        if day not in _DAY_NAMES:
            continue
        slot = by_day.setdefault(day, [])
        index = int(parts[1]) - 1 if parts[1].isdigit() else 0
        while len(slot) <= index:
            slot.append(("", ""))
        opens, closes = slot[index]
        slot[index] = (value, closes) if parts[2] == "open" else (opens, value)

    out: list[FacebookHours] = []
    for short in ("mon", "tue", "wed", "thu", "fri", "sat", "sun"):
        ranges = [r for r in by_day.get(short, []) if r[0] and r[1]]
        if not ranges:
            continue
        out.append(
            FacebookHours(day=_DAY_NAMES[short], opens=ranges[0][0], closes=ranges[-1][1])
        )
    return out


def _parse_posts(raw: dict[str, Any] | None, limit: int) -> list[FacebookPost]:
    data = (raw or {}).get("data") or []
    posts: list[FacebookPost] = []
    for item in data[:limit]:
        if not isinstance(item, dict):
            continue
        message = (item.get("message") or "").strip() or None
        image = item.get("full_picture") or None
        if not message and not image:
            continue  # nothing to ground or show
        posts.append(
            FacebookPost(
                message=message,
                created_time=item.get("created_time"),
                image_url=image,
                permalink=item.get("permalink_url"),
            )
        )
    return posts


def _parse_reviews(raw: dict[str, Any] | None, limit: int) -> list[FacebookReview]:
    data = (raw or {}).get("data") or []
    reviews: list[FacebookReview] = []
    for item in data[:limit]:
        if not isinstance(item, dict):
            continue
        text = (item.get("review_text") or "").strip()
        author = ((item.get("reviewer") or {}).get("name") or "").strip()
        # Both halves are required: an unattributed quote reads as invented, and
        # a name with no words attached says nothing.
        if not text or not author:
            continue
        rating = item.get("rating")
        reviews.append(
            FacebookReview(
                text=text,
                author=author,
                rating=float(rating) if isinstance(rating, (int, float)) else None,
                created_time=item.get("created_time"),
            )
        )
    return reviews


def build_page(
    ref: FacebookRef,
    core: dict[str, Any],
    optional: dict[str, dict[str, Any]],
    missing: list[str],
) -> FacebookPage:
    """Assemble a `FacebookPage` from the raw Graph payloads. Pure — the tests
    drive this directly with inline dicts and never touch the network."""
    contact = optional.get("contact", {})
    profile = optional.get("profile", {})
    posts_raw = optional.get("posts", {})
    reviews_raw = optional.get("reviews", {})

    location = contact.get("location") or {}
    categories = [
        c.get("name")
        for c in (core.get("category_list") or [])
        if isinstance(c, dict) and c.get("name")
    ]
    picture = ((core.get("picture") or {}).get("data") or {})
    # Graph marks generated placeholder avatars as silhouettes — treating one as
    # a brand mark would put Facebook's grey default head in the site header.
    profile_pic = None if picture.get("is_silhouette") else picture.get("url")

    emails = [e for e in (contact.get("emails") or []) if isinstance(e, str) and e.strip()]

    return FacebookPage(
        name=(core.get("name") or ref.handle or "").strip() or "Untitled",
        canonical_url=core.get("link") or ref.canonical_url,
        page_id=core.get("id") or ref.page_id,
        username=core.get("username") or ref.handle,
        category=core.get("category"),
        categories=categories,
        about=(core.get("about") or "").strip() or None,
        description=(core.get("description") or "").strip() or None,
        mission=(profile.get("mission") or "").strip() or None,
        products=(profile.get("products") or "").strip() or None,
        founded=(profile.get("founded") or "").strip() or None,
        price_range=(profile.get("price_range") or "").strip() or None,
        phone=(contact.get("phone") or "").strip() or None,
        emails=emails,
        website=(core.get("website") or "").strip() or None,
        street=(location.get("street") or "").strip() or None,
        city=(location.get("city") or "").strip() or None,
        state=(location.get("state") or "").strip() or None,
        zip_code=(location.get("zip") or "").strip() or None,
        country=(location.get("country") or "").strip() or None,
        single_line_address=(contact.get("single_line_address") or "").strip() or None,
        hours=_parse_hours(contact.get("hours")),
        fan_count=profile.get("fan_count"),
        rating_count=reviews_raw.get("_rating_count"),
        overall_star_rating=reviews_raw.get("_overall_star_rating"),
        reviews=_parse_reviews(reviews_raw, settings.facebook_max_reviews),
        profile_picture_url=profile_pic,
        cover_photo_url=(core.get("cover") or {}).get("source"),
        posts=_parse_posts(posts_raw, settings.facebook_max_posts),
        fetched_via="graph",
        partial=bool(missing),
        missing_fields=missing,
    )


async def fetch_page(ref: FacebookRef, token: str) -> FacebookPage:
    """Read `ref` through the Graph API. Raises FacebookGraphError on the core read."""
    node = ref.node
    if not node:
        raise FacebookGraphError("No Facebook Page id to read.", status=400)

    missing: list[str] = []
    optional: dict[str, dict[str, Any]] = {}
    timeout = httpx.Timeout(settings.facebook_timeout_seconds)

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": settings.http_user_agent},
    ) as client:
        core = await _get(client, node, _CORE_FIELDS, token)

        for group, fields in _OPTIONAL_GROUPS.items():
            try:
                optional[group] = await _get(client, node, fields, token)
            except FacebookGraphError as exc:
                logger.info(
                    "Facebook: '%s' fields unavailable for this token (%s)", group, exc
                )
                missing.append(group)

        posts_fields = (
            f"posts.limit({settings.facebook_max_posts})"
            "{message,created_time,full_picture,permalink_url}"
        )
        try:
            payload = await _get(client, node, posts_fields, token)
            optional["posts"] = payload.get("posts") or {}
        except FacebookGraphError as exc:
            logger.info("Facebook: posts unavailable for this token (%s)", exc)
            missing.append("posts")

        reviews_fields = (
            f"ratings.limit({settings.facebook_max_reviews})"
            "{review_text,rating,reviewer,created_time},"
            "rating_count,overall_star_rating"
        )
        try:
            payload = await _get(client, node, reviews_fields, token)
            ratings = payload.get("ratings") or {}
            ratings["_rating_count"] = payload.get("rating_count")
            ratings["_overall_star_rating"] = payload.get("overall_star_rating")
            optional["reviews"] = ratings
        except FacebookGraphError as exc:
            logger.info("Facebook: reviews unavailable for this token (%s)", exc)
            missing.append("reviews")

    page = build_page(ref, core, optional, missing)
    logger.info(
        "Facebook Graph: read '%s' (%d posts, %d reviews, missing=%s)",
        page.name, len(page.posts), len(page.reviews), missing or "none",
    )
    return page
