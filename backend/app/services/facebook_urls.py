"""Turn whatever the user pasted into a Facebook Page reference.

Pure, no I/O. People paste a lot of shapes — a share link with 40 characters of
tracking junk, a mobile URL, an `@handle`, the numeric `profile.php?id=` form,
a deep link to the Photos tab. All of them mean "this Page", and none of them
should be an error the user has to fix by hand.

The one thing worth being strict about is `kind`: a Facebook URL can just as
easily be a personal profile, a group, an event or a single post permalink.
Those are not business Pages, and generating a site from one produces something
confidently wrong. We detect the shape and say which it is.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from dataclasses import dataclass
from typing import Literal

RefKind = Literal["page", "profile", "group", "event", "post", "unknown"]

_CANONICAL_HOST = "https://www.facebook.com"

# Share/analytics params that carry no meaning for us. Anything else in the
# query string is dropped too — the canonical URL is built from scratch.
_TRACKING_PARAMS = frozenset(
    {"mibextid", "fbclid", "rdid", "ref", "refid", "sk", "__tn__", "__cft__", "eav"}
)

# Path prefixes that identify something other than a business Page.
_NON_PAGE_PREFIXES: dict[str, RefKind] = {
    "groups": "group",
    "events": "event",
    "story.php": "post",
    "photo.php": "post",
    "watch": "post",
    "reel": "post",
    "marketplace": "unknown",
    "share": "unknown",
}

# Tab segments that hang off a Page URL and carry no identity of their own.
_TAB_SEGMENTS = frozenset(
    {
        "about", "about_details", "about_contact_and_basic_info",
        "photos", "photos_stream", "videos", "reviews", "posts", "community",
        "shop", "services", "menu", "offers", "events", "live", "notes",
        "timeline", "info", "app", "insights", "reels",
    }
)

# Segments that are Facebook chrome, never a handle.
_RESERVED_HANDLES = frozenset(
    {
        "login", "signup", "help", "policies", "privacy", "terms", "settings",
        "search", "home", "messages", "notifications", "bookmarks", "gaming",
        "business", "ads", "developers", "careers", "pages", "profile.php",
        "people", "public", "hashtag", "sharer", "plugins", "dialog", "tr",
    }
)

_HANDLE_RE = re.compile(r"^[A-Za-z0-9.\-_]{1,80}$")
_NUMERIC_RE = re.compile(r"^\d{5,}$")
# "/p/Some-Business-Name-100064123456789" — the modern Page permalink.
_P_SEGMENT_RE = re.compile(r"^(?P<slug>.+?)-(?P<id>\d{6,})$")


class FacebookUrlError(ValueError):
    """The pasted link isn't a business Page. Carries a friendly status code."""

    def __init__(self, message: str, status: int = 422):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class FacebookRef:
    """Which Page to read, and whether it is a Page at all."""

    handle: str | None
    page_id: str | None
    canonical_url: str
    kind: RefKind = "page"

    @property
    def node(self) -> str:
        """The Graph API node id — numeric id when we have one, else the handle."""
        return self.page_id or self.handle or ""


def _clean_query(query: str) -> dict[str, list[str]]:
    parsed = parse_qs(query, keep_blank_values=False)
    return {k: v for k, v in parsed.items() if k.lower() not in _TRACKING_PARAMS}


def _canonical(handle: str | None, page_id: str | None) -> str:
    if handle:
        return f"{_CANONICAL_HOST}/{handle}"
    return f"{_CANONICAL_HOST}/profile.php?id={page_id}"


def _reject(kind: RefKind) -> None:
    """Raise the message that names what the link actually is."""
    messages: dict[RefKind, str] = {
        "profile": (
            "That looks like a personal Facebook profile, not a business Page. "
            "Open the business's Page and copy its link instead."
        ),
        "group": (
            "That's a Facebook group, not a business Page. Groups don't carry the "
            "About and contact details a site is built from."
        ),
        "event": (
            "That's a Facebook event, not a business Page. Use the link to the "
            "Page that's hosting it."
        ),
        "post": (
            "That's a link to a single post, not to the Page itself. Open the "
            "Page and copy the link from its address bar."
        ),
        "unknown": (
            "That Facebook link doesn't point at a business Page. Open the Page "
            "and copy the link from its address bar."
        ),
    }
    raise FacebookUrlError(messages.get(kind, messages["unknown"]))


def parse_ref(raw: str, *, strict: bool = True) -> FacebookRef:
    """Normalize a pasted Facebook link into a `FacebookRef`.

    ``strict`` (the default) raises `FacebookUrlError` for profiles, groups,
    events and post permalinks. Pass ``strict=False`` to get the classified ref
    back instead — useful for tests and for telling the UI *why* a link was
    rejected.
    """
    value = (raw or "").strip()
    if not value:
        raise FacebookUrlError("Enter a Facebook Page link.", status=400)

    # "@acmecoffee" — the handle form people copy out of bios.
    if value.startswith("@"):
        handle = value[1:].strip().strip("/")
        if not _HANDLE_RE.match(handle):
            raise FacebookUrlError("That doesn't look like a Facebook Page handle.", status=400)
        return FacebookRef(handle=handle, page_id=None, canonical_url=_canonical(handle, None))

    if "//" not in value:
        value = f"https://{value}"

    try:
        parts = urlsplit(value)
    except ValueError:
        raise FacebookUrlError("That link couldn't be read.", status=400) from None

    segments = [s for s in parts.path.split("/") if s]
    query = _clean_query(parts.query)

    # profile.php?id=<numeric> — a Page or a person; Graph tells them apart, we
    # can't from the URL alone, so it stays a "page" and the reader decides.
    if segments and segments[0].lower() == "profile.php":
        ids = query.get("id") or []
        if ids and _NUMERIC_RE.match(ids[0]):
            page_id = ids[0]
            return FacebookRef(
                handle=None, page_id=page_id, canonical_url=_canonical(None, page_id)
            )
        raise FacebookUrlError("That profile link is missing its id.", status=400)

    if not segments:
        raise FacebookUrlError(
            "That's the Facebook home page, not a business Page.", status=400
        )

    head = segments[0].lower()

    if head in _NON_PAGE_PREFIXES:
        kind = _NON_PAGE_PREFIXES[head]
        if strict:
            _reject(kind)
        return FacebookRef(handle=None, page_id=None, canonical_url=value, kind=kind)

    # "/people/Name/<id>" is always a personal profile.
    if head == "people":
        if strict:
            _reject("profile")
        return FacebookRef(handle=None, page_id=None, canonical_url=value, kind="profile")

    # "/p/Some-Business-100064123456789"
    if head == "p" and len(segments) >= 2:
        match = _P_SEGMENT_RE.match(segments[1])
        if match:
            page_id = match.group("id")
            return FacebookRef(
                handle=None, page_id=page_id, canonical_url=_canonical(None, page_id)
            )
        raise FacebookUrlError("That Page link couldn't be read.", status=400)

    # "/pages/Some-Business/123456789" — the legacy vanity form.
    if head == "pages":
        numeric = next((s for s in reversed(segments) if _NUMERIC_RE.match(s)), None)
        if numeric:
            return FacebookRef(
                handle=None, page_id=numeric, canonical_url=_canonical(None, numeric)
            )
        raise FacebookUrlError("That Page link is missing its id.", status=400)

    if head in _RESERVED_HANDLES:
        if strict:
            _reject("unknown")
        return FacebookRef(handle=None, page_id=None, canonical_url=value, kind="unknown")

    # A plain "/<handle>" with optional tab segments after it.
    handle = segments[0]
    if not _HANDLE_RE.match(handle):
        raise FacebookUrlError("That doesn't look like a Facebook Page link.", status=400)

    # A post permalink hides behind a valid handle: "/<handle>/posts/<id>".
    if len(segments) >= 2:
        second = segments[1].lower()
        if second in ("posts", "videos", "photos") and len(segments) >= 3:
            if strict:
                _reject("post")
            return FacebookRef(
                handle=handle, page_id=None, canonical_url=_canonical(handle, None), kind="post"
            )
        if second not in _TAB_SEGMENTS and not _NUMERIC_RE.match(second):
            # An unrecognised second segment — still the same Page, the extra
            # path is just a view we don't know about.
            pass

    if _NUMERIC_RE.match(handle):
        return FacebookRef(handle=None, page_id=handle, canonical_url=_canonical(None, handle))

    return FacebookRef(handle=handle, page_id=None, canonical_url=_canonical(handle, None))
