"""Find the business's WhatsApp number on the site we are copying.

A source site that already offers WhatsApp does so in one of a few places: a
`wa.me` link in the header or footer, an `api.whatsapp.com/send?phone=` button,
a `whatsapp://send` deep link, or — for a multi-branch business — a per-branch
number the model lifted into a LocationsBlock. All of them state a number the
business publishes itself, which is what makes re-publishing it on the generated
site the same act rather than a new disclosure.

The country code is never inferred. `wa.me/60123456789` already carries one;
a bare `tel:` or a national-format string does not, and guessing produces a
link that opens a chat with a stranger — the same rule facebook_authority
applies when it refuses to derive `whatsapp` from a Page's phone number.

The widget config this builds is the CMS's shape
(webtree-cms-api App\\Support\\WhatsApp\\WhatsAppWidgetConfig). The API
re-normalizes on arrival, so this only has to be honest, not exhaustive.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from app.models.content_blocks import SourceContent

__all__ = [
    "build_whatsapp_widget",
    "discover_whatsapp_number",
    "whatsapp_number_from_href",
]

# E.164: up to 15 digits, and no real country+subscriber pair is shorter than 8.
_MIN_DIGITS = 8
_MAX_DIGITS = 15

_WA_HOSTS = ("wa.me", "api.whatsapp.com", "web.whatsapp.com", "whatsapp.com")

# `wa.me/<digits>` — the path IS the number. Anything else on that host (a
# `/qr/` share code, a marketing page) is not.
_WA_ME_PATH = re.compile(r"^/(\+?\d[\d\s\-().]*)$")

# Bare wa.me links inside prose, for sites that print the link as text.
_WA_IN_TEXT = re.compile(
    r"(?:https?://)?(?:api\.)?wa(?:\.me|\.link)/(\+?\d[\d\-]{6,20})",
    re.IGNORECASE,
)


def _canonical_digits(raw: str) -> str | None:
    """The E.164 digits in `raw`, or None when it cannot carry a country code.

    Mirrors WhatsAppNumber::normalize on the CMS side — kept in step so a number
    this discovers is a number that endpoint will accept.
    """
    digits = re.sub(r"\D+", "", raw or "")

    # "00" is the international access code: the same number written for a
    # landline dialler.
    if digits.startswith("00"):
        digits = digits[2:]

    # A surviving leading zero is a national trunk prefix — the country code is
    # missing, and there is nothing to normalize to.
    if not digits or digits.startswith("0"):
        return None

    return digits if _MIN_DIGITS <= len(digits) <= _MAX_DIGITS else None


def whatsapp_number_from_href(href: str | None) -> str | None:
    """The number a click-to-chat href opens, or None if it is not one.

    Handles every form a real site uses: `https://wa.me/60123456789`,
    `https://api.whatsapp.com/send?phone=60123456789`, and the
    `whatsapp://send?phone=` deep link.
    """
    if not href or not isinstance(href, str):
        return None

    raw = href.strip()

    if not raw:
        return None

    try:
        parsed = urlparse(raw if "//" in raw or ":" in raw else f"//{raw}")
    except ValueError:
        return None

    host = (parsed.netloc or "").lower().removeprefix("www.")
    scheme = (parsed.scheme or "").lower()

    is_whatsapp_host = any(host == domain or host.endswith(f".{domain}") for domain in _WA_HOSTS)

    if not is_whatsapp_host and scheme != "whatsapp":
        return None

    # `?phone=` wins wherever it is present — api.whatsapp.com/send and the
    # whatsapp:// deep link both carry the number there rather than in the path.
    phone = parse_qs(parsed.query or "").get("phone", [None])[0]

    if phone:
        return _canonical_digits(phone)

    if host == "wa.me" or host.endswith(".wa.me"):
        match = _WA_ME_PATH.match(parsed.path or "")
        if match:
            return _canonical_digits(match.group(1))

    return None


def _numbers_in_source(source: SourceContent) -> list[str]:
    """Every WhatsApp number this one page states, most trustworthy first.

    Order is confidence, not position: a link the site rendered as a button is
    a stronger claim than a URL that happens to appear in prose.
    """
    found: list[str] = []

    def remember(number: str | None) -> None:
        if number and number not in found:
            found.append(number)

    # Social profile links: nav_extraction already recognised these as
    # platform links, so a WhatsApp entry here is a deliberate contact button.
    for link in source.social_links:
        remember(whatsapp_number_from_href(link.href))

    for href in source.links:
        remember(whatsapp_number_from_href(href))

    for match in _WA_IN_TEXT.finditer(source.raw_text or ""):
        remember(_canonical_digits(match.group(1)))

    return found


def discover_whatsapp_number(source: SourceContent | None) -> str | None:
    """The site's WhatsApp number, from the source we crawled.

    The primary page wins over a discovered sub-page: a number in the header or
    footer of the page the owner gave us is the business's own, while one found
    three pages deep is as likely to belong to a partner or an author.
    """
    if source is None:
        return None

    for number in _numbers_in_source(source):
        return number

    for page in source.discovered_pages:
        for number in _numbers_in_source(page):
            return number

    return None


def build_whatsapp_widget(
    number: str | None,
    *,
    site_name: str | None = None,
) -> dict[str, object] | None:
    """The widget config to push for a discovered number, or None.

    Enabled on arrival: the source site already published this number as a
    contact button, so the generated site carrying the same button is continuity
    rather than a new decision. The owner can switch it off in the builder's
    Site panel or in admin Site settings.

    Everything else is left at the CMS's defaults — the generator knows the
    number, not the business's opening hours or which pages should carry it, and
    inventing those would be a config the owner has to undo rather than one they
    can build on.
    """
    if not number:
        return None

    business = (site_name or "").strip()
    prefill = (
        f"Hi {business}! I found you on your website and would like to know more."
        if business
        else "Hi! I found you on your website and would like to know more."
    )

    return {
        "enabled": True,
        "phone": number,
        "displayMode": "labeled",
        "label": "Chat with us",
        "position": "bottom-right",
        "prefill": {"message": prefill, "includePageUrl": False},
    }
