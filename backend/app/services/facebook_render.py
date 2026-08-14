"""Read a public Facebook Page by rendering it, when there's no access token.

Best-effort by construction. Facebook changes this markup without notice and
login-walls datacentre IPs freely, so everything here is defensive and the
result is always flagged `partial` — a token reads far more, and the UI uses
that flag to say so.

Two rules keep this honest:

1. **A login wall is an error, not a thin result.** Returning a Page carrying
   only a name would send a near-empty, half-invented site downstream. We raise
   and tell the user a token fixes it.
2. **Parsing is a pure function of the HTML.** `parse_public_html` takes markup
   and returns a `FacebookPage`, so the tests drive it with inline strings and
   the suite stays offline.
"""

from __future__ import annotations

import json
import logging
import re

from bs4 import BeautifulSoup

from app.config import settings
from app.models.facebook import FacebookPage, FacebookHours
from app.services.browser import browser_context, rendered_page
from app.services.facebook_urls import FacebookRef

logger = logging.getLogger(__name__)


class FacebookRenderError(Exception):
    """A public read that could not produce a Page. Carries a status code."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


LOGIN_WALL_MESSAGE = (
    "Facebook asked us to log in before showing this Page. Connect a Page "
    "access token to read it, or try again with a Page that's fully public."
)

_LOGIN_WALL_MARKERS = (
    "you must log in to continue",
    "log in to continue",
    "login_form",
    "/login/?next=",
    "loginform",
    "content_not_available",
    "this content isn't available right now",
)

# "1,234 likes · 56 talking about this · 78 were here"
_LIKES_RE = re.compile(r"([\d,.\s]+)\s*(?:likes|people like this|followers)", re.I)
_COUNT_CHUNK_RE = re.compile(
    r"[\d,.\s]+\s*(?:likes|people like this|followers|talking about this|were here|"
    r"check-ins|check ins)\s*·?\s*",
    re.I,
)

# Label → attribute, scanned over the rendered text. Line-based rather than
# selector-based on purpose: Facebook's class names are generated and churn
# every few weeks, while the visible labels are stable and translated only when
# the locale changes (we always request en-US).
_TEXT_LABELS: dict[str, str] = {
    "address": "single_line_address",
    "phone": "phone",
    "mobile": "phone",
    "email": "email",
    "website": "website",
    "price range": "price_range",
    "categories": "category",
    "category": "category",
    "founded": "founded",
    "products": "products",
    "mission": "mission",
    "about": "about",
    "impressum": "",
}

_HOURS_LINE_RE = re.compile(
    r"^(?P<day>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*[:\-–]?\s*"
    r"(?P<open>\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)\s*(?:-|–|to)\s*"
    r"(?P<close>\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)$",
    re.I,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")


def is_login_wall(html: str) -> bool:
    """True when Facebook served a login/consent interstitial instead of the Page."""
    lowered = (html or "").lower()
    if not lowered:
        return True
    return any(marker in lowered for marker in _LOGIN_WALL_MARKERS)


def _meta(soup: BeautifulSoup, prop: str) -> str | None:
    tag = soup.find("meta", attrs={"property": prop}) or soup.find(
        "meta", attrs={"name": prop}
    )
    if not tag:
        return None
    value = (tag.get("content") or "").strip()
    return value or None


def _split_og_description(raw: str | None) -> tuple[str | None, int | None]:
    """Facebook packs the blurb behind engagement counts.

    "Acme Coffee, Kuala Lumpur. 1,234 likes · 56 talking about this · 78 were
    here. Specialty coffee roasted in-house." → (blurb, 1234)
    """
    if not raw:
        return None, None
    fans: int | None = None
    match = _LIKES_RE.search(raw)
    if match:
        digits = re.sub(r"[^\d]", "", match.group(1))
        if digits:
            try:
                fans = int(digits)
            except ValueError:
                fans = None
    # Strip the separators the removed count chunk left behind, and a leading
    # full stop (what's left of "…were here. "). A TRAILING full stop is the
    # business's own punctuation and stays — this text lands verbatim in
    # raw_text, and quietly editing it there edits what the site may claim.
    stripped = _COUNT_CHUNK_RE.sub("", raw).strip(" ·|-").lstrip(" .·|-").rstrip(" ·|-")
    # What's left often still leads with "Name, City, Country." — keep it; the
    # location IS a fact the Page published, and guessing which clause to drop
    # risks throwing away the blurb itself.
    return (stripped or None), fans


def _json_ld_blocks(soup: BeautifulSoup) -> list[dict]:
    out: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            out.append(data)
        elif isinstance(data, list):
            out.extend(d for d in data if isinstance(d, dict))
    return out


def _text_lines(soup: BeautifulSoup) -> list[str]:
    text = soup.get_text(separator="\n")
    return [line.strip() for line in text.split("\n") if line.strip()]


def _scan_labelled(lines: list[str]) -> dict[str, str]:
    """Pull "Label" → next-line values out of the rendered text.

    Line-based because Facebook's generated class names churn constantly while
    the visible labels don't.
    """
    found: dict[str, str] = {}
    for index, line in enumerate(lines):
        key = line.rstrip(":").strip().lower()
        field = _TEXT_LABELS.get(key)
        if not field or field in found:
            continue
        for candidate in lines[index + 1 : index + 3]:
            lowered = candidate.rstrip(":").strip().lower()
            if lowered in _TEXT_LABELS:  # the next label — this one had no value
                break
            if len(candidate) > 1:
                found[field] = candidate
                break
    return found


def _scan_hours(lines: list[str]) -> list[FacebookHours]:
    hours: list[FacebookHours] = []
    seen: set[str] = set()
    for line in lines:
        match = _HOURS_LINE_RE.match(line)
        if not match:
            continue
        day = match.group("day").title()
        if day in seen:
            continue
        seen.add(day)
        hours.append(
            FacebookHours(
                day=day,
                opens=match.group("open").strip(),
                closes=match.group("close").strip(),
            )
        )
    return hours


def parse_public_html(html: str, ref: FacebookRef) -> FacebookPage:
    """Build a `FacebookPage` from rendered public markup. Pure — no I/O.

    Raises `FacebookRenderError` when the markup is a login wall or carries no
    usable identity: a Page with nothing but a URL is worse than an error,
    because everything downstream would have to invent the rest.
    """
    if is_login_wall(html):
        raise FacebookRenderError(LOGIN_WALL_MESSAGE, status=422)

    soup = BeautifulSoup(html or "", "lxml")

    og_title = _meta(soup, "og:title")
    name = (og_title or ref.handle or "").strip()
    if not name:
        raise FacebookRenderError(
            "Couldn't read anything from that Facebook Page.", status=422
        )

    blurb, fan_count = _split_og_description(_meta(soup, "og:description"))
    lines = _text_lines(soup)
    labelled = _scan_labelled(lines)

    # JSON-LD, when Facebook emits it, is more trustworthy than a text scan.
    for block in _json_ld_blocks(soup):
        address = block.get("address")
        if isinstance(address, dict):
            labelled.setdefault(
                "single_line_address",
                ", ".join(
                    str(address[k]).strip()
                    for k in ("streetAddress", "addressLocality", "addressRegion", "postalCode")
                    if address.get(k)
                ),
            )
        for src, dest in (("telephone", "phone"), ("email", "email"), ("url", "website")):
            if isinstance(block.get(src), str) and block[src].strip():
                labelled.setdefault(dest, block[src].strip())

    email = labelled.get("email")
    if email and not _EMAIL_RE.fullmatch(email):
        email = None
    phone = labelled.get("phone")
    if phone and not _PHONE_RE.fullmatch(phone.strip()):
        phone = None

    about = labelled.get("about") or blurb

    page = FacebookPage(
        name=name,
        canonical_url=_meta(soup, "og:url") or ref.canonical_url,
        page_id=ref.page_id,
        username=ref.handle,
        category=labelled.get("category"),
        about=about,
        description=blurb if about != blurb else None,
        mission=labelled.get("mission"),
        products=labelled.get("products"),
        founded=labelled.get("founded"),
        price_range=labelled.get("price_range"),
        phone=phone,
        emails=[email] if email else [],
        website=labelled.get("website"),
        single_line_address=labelled.get("single_line_address") or None,
        hours=_scan_hours(lines),
        fan_count=fan_count,
        profile_picture_url=_meta(soup, "og:image"),
        fetched_via="render",
        # Always partial: a public render never sees emails, structured hours,
        # posts or reviews, so the UI should always offer the token.
        partial=True,
        missing_fields=_missing_for(labelled, blurb),
    )
    return page


def _missing_for(labelled: dict[str, str], blurb: str | None) -> list[str]:
    """Field groups a token would unlock, for the preview checklist."""
    missing = ["posts", "reviews"]
    if not labelled.get("phone") and not labelled.get("email"):
        missing.insert(0, "contact")
    if not blurb and not labelled.get("about"):
        missing.append("profile")
    return missing


def _candidate_urls(ref: FacebookRef) -> list[str]:
    """Cheapest-and-richest first. mbasic serves plain server-rendered HTML."""
    node = ref.handle or (f"profile.php?id={ref.page_id}" if ref.page_id else "")
    if not node:
        return []
    if ref.handle:
        return [
            f"https://mbasic.facebook.com/{node}/about",
            f"https://www.facebook.com/{node}/about",
            f"https://www.facebook.com/{node}",
        ]
    return [
        f"https://mbasic.facebook.com/{node}",
        f"https://www.facebook.com/{node}",
    ]


async def fetch_page(ref: FacebookRef) -> FacebookPage:
    """Render the public Page and parse it. Raises `FacebookRenderError`."""
    if not settings.facebook_render_fallback_enabled:
        raise FacebookRenderError(
            "Reading public Facebook Pages is turned off. Connect a Page access "
            "token to continue.",
            status=422,
        )

    timeout_ms = int(settings.facebook_timeout_seconds * 1000)
    last_error: FacebookRenderError | None = None

    # One browser for the whole candidate ladder — each URL is a fallback for
    # the last, so paying the launch cost per attempt would triple it.
    async with browser_context() as context:
        for url in _candidate_urls(ref):
            try:
                async with rendered_page(context, url, timeout_ms=timeout_ms) as page:
                    html = await page.content()
            except Exception as exc:  # RenderError, Playwright timeouts, transport
                logger.info("Facebook render: %s failed (%s)", url, exc)
                last_error = FacebookRenderError(
                    f"Couldn't open that Facebook Page: {exc}"
                )
                continue
            try:
                fb_page = parse_public_html(html, ref)
            except FacebookRenderError as exc:
                logger.info("Facebook render: %s unusable (%s)", url, exc)
                last_error = exc
                continue
            logger.info("Facebook render: read '%s' from %s", fb_page.name, url)
            return fb_page

    raise last_error or FacebookRenderError(
        "Couldn't read that Facebook Page.", status=422
    )
