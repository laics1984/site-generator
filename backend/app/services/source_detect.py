"""Which reader handles a given link.

One link field, several specialist readers behind it. The user pastes a URL and
the backend decides how to read it — a Facebook Page goes to the Facebook
reader, everything else to the HTML scraper. There is deliberately no UI mode
for this: "scrape a website" and "read a Facebook Page" are the same intent
(build from my existing web presence) with different plumbing, and making the
user classify their own link is making them learn our architecture.

The frontend mirrors ``is_facebook_url`` in ``lib/sourceDetect.ts`` purely for
the inline badge/helper/button affordance while typing. This module is the
authority — the frontend never picks an endpoint, so ``curl`` and any non-UI
caller get the same routing.

Adding a reader (Instagram, Google Business Profile, Linktree) means one entry
in ``_HANDLERS`` and a matching orchestrator; no router or UI change.
"""

from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlsplit

SourceHandler = Literal["html", "facebook"]

# Host suffixes that route to the Facebook reader. Matched against the
# registrable host, so "m.facebook.com" and "web.facebook.com" hit
# "facebook.com" while "notfacebook.com" does not (see _host_matches).
_FACEBOOK_HOSTS: tuple[str, ...] = (
    "facebook.com",
    "fb.com",
    "fb.me",
    "facebook.net",
)

_HANDLERS: tuple[tuple[SourceHandler, tuple[str, ...]], ...] = (
    ("facebook", _FACEBOOK_HOSTS),
)

# A bare handle typed without a scheme ("@acmecoffee", "acmecoffee"). Only
# treated as Facebook when it carries the @ sigil — a bare word is far more
# likely a mistyped domain, and guessing wrong sends the user somewhere they
# did not ask to go.
_AT_HANDLE_RE = re.compile(r"^@[A-Za-z0-9.\-_]{1,80}$")


def _host_of(url: str) -> str:
    """Lowercased host with any port and trailing dot stripped, or ''."""
    raw = (url or "").strip()
    if not raw:
        return ""
    # urlsplit puts a scheme-less "facebook.com/x" entirely in `path`.
    if "//" not in raw:
        raw = f"https://{raw}"
    try:
        host = (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""
    return host.rstrip(".")


def _host_matches(host: str, suffixes: tuple[str, ...]) -> bool:
    """True when `host` is one of `suffixes` or a subdomain of one.

    The dot is what stops "notfacebook.com" matching "facebook.com" — a plain
    ``endswith`` would route it to the Facebook reader, which then fails
    confusingly on a site that has nothing to do with Facebook.
    """
    return any(host == s or host.endswith(f".{s}") for s in suffixes)


def is_facebook_url(url: str) -> bool:
    """True for a Facebook link in any of the forms users actually paste."""
    if _AT_HANDLE_RE.match((url or "").strip()):
        return True
    return _host_matches(_host_of(url), _FACEBOOK_HOSTS)


def detect(url: str) -> SourceHandler:
    """The reader for this link. Unknown hosts fall back to the HTML scraper."""
    if _AT_HANDLE_RE.match((url or "").strip()):
        return "facebook"
    host = _host_of(url)
    if not host:
        return "html"
    for handler, suffixes in _HANDLERS:
        if _host_matches(host, suffixes):
            return handler
    return "html"
