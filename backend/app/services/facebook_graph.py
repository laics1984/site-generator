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
from urllib.parse import urlparse

import httpx

from app.config import settings
from app.models.facebook import (
    GRAPH_SERVICE_OPTIONS,
    FacebookEvent,
    FacebookHours,
    FacebookPage,
    FacebookPhoto,
    FacebookPost,
    FacebookReview,
    FacebookVideo,
)
from app.services.facebook_dom import merge_posts
from app.services.facebook_urls import FacebookRef
from app.services.polite import HostPoliteness, get_politeness

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
# Best-effort extras, read on their own so a permission they need can't take a
# group above down with it. Not reported as missing: no token scope the
# operator could add would make a Page with no Instagram account have one.
_EXTRA_GROUPS: dict[str, str] = {
    "details": "restaurant_services,instagram_business_account{username}",
}

# Graph's throttling codes — application, user, Page and custom rate limits.
# A request a short wait would answer; any other error is final.
_THROTTLED = frozenset({4, 17, 32, 613})
_ATTEMPTS = 3
_FACEBOOK_HOSTS = frozenset({"facebook.com", "www.facebook.com", "m.facebook.com"})

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


def _graph_error(response: httpx.Response) -> tuple[str, int | None]:
    """Graph's own message and code for a failed request."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return response.text[:200], None
    code = error.get("code")
    return error.get("message") or "", code if isinstance(code, int) else None


async def _get(
    client: httpx.AsyncClient,
    node: str,
    fields: str,
    token: str,
    politeness: HostPoliteness | None = None,
) -> dict[str, Any]:
    """One Graph read. Raises FacebookGraphError with the token redacted.

    A throttled request (HTTP 429, a 5xx, or one of Graph's rate-limit codes)
    and a dropped connection are retried after the shared exponential back-off;
    anything else — a missing permission above all — fails at once, because
    the caller's tolerant groups exist to absorb exactly that.
    """
    for attempt in range(1, _ATTEMPTS + 1):
        last = attempt == _ATTEMPTS or politeness is None
        try:
            response = await client.get(
                _graph_url(node),
                params={"fields": fields, "access_token": token},
            )
        except httpx.HTTPError as exc:
            if not last:
                await politeness.back_off(attempt)
                continue
            raise FacebookGraphError(
                _redact(f"Couldn't reach Facebook: {exc}", token)
            ) from None

        if response.status_code >= 400:
            detail, code = _graph_error(response)
            throttled = response.status_code == 429 or response.status_code >= 500 or code in _THROTTLED
            if throttled and not last:
                logger.info("Facebook Graph throttled (%s/%s); backing off", response.status_code, code)
                await politeness.back_off(attempt)
                continue
            raise FacebookGraphError(
                _redact(detail or f"Facebook returned {response.status_code}.", token),
                status=response.status_code if response.status_code < 500 else 502,
            )

        try:
            return response.json()
        except ValueError:
            raise FacebookGraphError("Facebook returned an unreadable response.") from None
    raise FacebookGraphError("Facebook kept refusing the request.")  # pragma: no cover


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
        photos, videos = _parse_attachments(item.get("attachments") or {})
        if videos and image and not photos:
            image = None  # a video post's full picture IS the video's frame
        if not message and not image and not photos and not videos:
            continue  # nothing to ground or show
        posts.append(
            FacebookPost(
                message=message,
                created_time=item.get("created_time"),
                image_url=image,
                permalink=item.get("permalink_url"),
                post_id=item.get("id"),
                photos=photos,
                videos=videos,
            )
        )
    return posts


def _positive(value: Any) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _is_facebook_url(url: Any) -> bool:
    return isinstance(url, str) and (urlparse(url).hostname or "") in _FACEBOOK_HOSTS


def _parse_attachments(raw: dict[str, Any]) -> tuple[list[FacebookPhoto], list[FacebookVideo]]:
    """Original photo media, including multi-photo posts, and the Page's own
    videos with their frames; never link previews, and never a video hosted
    anywhere but facebook.com (a shared YouTube link is someone else's)."""
    photos: dict[str, FacebookPhoto] = {}
    videos: dict[str, FacebookVideo] = {}
    if not isinstance(raw, dict):
        return [], []
    for attachment in (raw.get("data") or [])[:20]:
        if not isinstance(attachment, dict):
            continue
        children = (attachment.get("subattachments") or {}).get("data") or []
        for entry in children[:20] if children else [attachment]:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("type") or ""
            image = (entry.get("media") or {}).get("image") or {}
            src = image.get("src") if isinstance(image.get("src"), str) else None
            if kind in {"photo", "album"} and src:
                photos[src] = FacebookPhoto(
                    url=src, width=_positive(image.get("width")), height=_positive(image.get("height")),
                )
            elif kind.startswith("video"):
                target = entry.get("target") or {}
                url = target.get("url") or entry.get("url")
                if _is_facebook_url(url):
                    videos[url] = FacebookVideo(
                        url=url,
                        video_id=str(target["id"]) if target.get("id") else None,
                        thumbnail_url=src,
                    )
    return list(photos.values()), list(videos.values())


def _largest_image(images: Any) -> FacebookPhoto | None:
    """`images` lists one photo's renditions; the largest is the original."""
    renditions = [
        FacebookPhoto(url=image["source"], width=_positive(image.get("width")),
                      height=_positive(image.get("height")))
        for image in images or [] if isinstance(image, dict) and isinstance(image.get("source"), str)
    ]
    return max(renditions, key=lambda p: (p.width or 0) * (p.height or 0), default=None)


def _parse_uploads(raw: dict[str, Any] | None) -> list[FacebookPost]:
    """The Page's uploaded photos, each with the caption written for it (`name`).

    Album photos carry their words here and nowhere else — a post's
    attachments name the photo, not what was said about it. Folded in through
    `merge_posts`, so a photo already held from its post is not a second post.
    """
    uploads: list[FacebookPost] = []
    for item in (raw or {}).get("data") or []:
        if not isinstance(item, dict):
            continue
        photo = _largest_image(item.get("images"))
        if photo is None:
            continue
        uploads.append(FacebookPost(
            message=(item.get("name") or "").strip() or None,
            created_time=item.get("created_time"),
            permalink=item.get("link") if _is_facebook_url(item.get("link")) else None,
            photos=[photo],
        ))
    return uploads


def _parse_events(raw: dict[str, Any] | None) -> list[FacebookEvent]:
    events: list[FacebookEvent] = []
    for item in (raw or {}).get("data") or []:
        if not isinstance(item, dict) or not (item.get("name") or "").strip():
            continue
        place = item.get("place") or {}
        events.append(FacebookEvent(
            name=item["name"].strip(),
            description=(item.get("description") or "").strip() or None,
            start_time=item.get("start_time"),
            place=(place.get("name") or "").strip() or None if isinstance(place, dict) else None,
            url=f"https://www.facebook.com/events/{item['id']}" if item.get("id") else None,
        ))
    return events


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


def _service_options(raw: Any) -> list[str]:
    """`restaurant_services` flags, named the way the Page itself shows them."""
    if not isinstance(raw, dict):
        return []
    return [name for key, name in GRAPH_SERVICE_OPTIONS.items() if raw.get(key) is True]


def _social_profiles(instagram: Any) -> list[str]:
    username = instagram.get("username") if isinstance(instagram, dict) else None
    if not isinstance(username, str) or not username.strip():
        return []
    return [f"https://www.instagram.com/{username.strip()}"]


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
    details = optional.get("details", {})
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

    page = FacebookPage(
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
        service_options=_service_options(details.get("restaurant_services")),
        phone=(contact.get("phone") or "").strip() or None,
        emails=emails,
        website=(core.get("website") or "").strip() or None,
        social_profiles=_social_profiles(details.get("instagram_business_account")),
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
        events=_parse_events((optional.get("events") or {}).get("events")),
        fetched_via="graph",
        partial=bool(missing),
        missing_fields=missing,
    )
    # The same join and the same caps as a rendered read: an uploaded photo
    # already held from its post is that post, not a second one.
    merge_posts(page, [
        *_parse_posts(posts_raw, settings.facebook_max_posts),
        *_parse_uploads((optional.get("uploads") or {}).get("photos")),
    ])
    return page


async def fetch_page(ref: FacebookRef, token: str) -> FacebookPage:
    """Read `ref` through the Graph API. Raises FacebookGraphError on the core read."""
    node = ref.node
    if not node:
        raise FacebookGraphError("No Facebook Page id to read.", status=400)

    missing: list[str] = []
    optional: dict[str, dict[str, Any]] = {}
    timeout = httpx.Timeout(settings.facebook_timeout_seconds)
    politeness = await get_politeness(urlparse(settings.facebook_graph_base_url).hostname or "graph")
    extras = {
        **_EXTRA_GROUPS,
        # Each needs a permission a Page token may lack, so each is its own
        # request and its own failure.
        "uploads": (
            f"photos.type(uploaded).limit({settings.facebook_photo_max_images})"
            "{name,created_time,link,images}"
        ),
        "events": "events.limit(10){id,name,description,start_time,place{name}}",
    }

    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": settings.http_user_agent},
    ) as client:
        core = await _get(client, node, _CORE_FIELDS, token, politeness)

        for group, fields in _OPTIONAL_GROUPS.items():
            try:
                optional[group] = await _get(client, node, fields, token, politeness)
            except FacebookGraphError as exc:
                logger.info(
                    "Facebook: '%s' fields unavailable for this token (%s)", group, exc
                )
                missing.append(group)
        for group, fields in extras.items():
            try:
                optional[group] = await _get(client, node, fields, token, politeness)
            except FacebookGraphError as exc:
                logger.info("Facebook: '%s' fields unavailable (%s)", group, exc)

        posts_fields = (
            f"posts.limit({settings.facebook_max_posts})"
            "{id,message,created_time,full_picture,permalink_url,"
            "attachments{type,media,url,target{id,url},"
            "subattachments{type,media,url,target{id,url}}}}"
        )
        try:
            payload = await _get(client, node, posts_fields, token, politeness)
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
            payload = await _get(client, node, reviews_fields, token, politeness)
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
