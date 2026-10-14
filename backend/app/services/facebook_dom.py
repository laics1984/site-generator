"""Read a rendered Facebook Page: markup in, facts out. Pure — no I/O.

`facebook_render` drives the browser; everything that turns its HTML into facts
lives here, so the tests drive it with inline strings and the suite stays
offline. Four rules keep it honest:

1. **A wall is what Facebook TELLS us it is, not what its markup mentions.**
   The wall test was a denylist of markup needles (`login_form`, `/login/?next=`)
   matched against the whole HTML — and Facebook bundles its login *dialog* into
   every page it serves, so those needles are present on the pages that render
   perfectly. Every public read failed, for as long as the check existed. The
   signal that actually distinguishes them is the URL we ended up on: a wall is
   a redirect to `/login`, which is Facebook stating it.
2. **A login wall is an error, not a thin result.** Returning a Page carrying
   only a name would send a near-empty, half-invented site downstream.
3. **Facebook's words, never a business's.** Every closed set here — tab names,
   panel headings, field labels, service options, "recommends" — is Facebook's
   own UI vocabulary. A denylist of business nouns is a silent off switch.
4. **The inline payload over the painted DOM.** A rendered Page ships the
   GraphQL data it painted from as `<script type="application/json">`: each
   photo's original (`uri`, `width`, `height`) where the DOM shows a square
   crop, a post's whole `message.text` where the DOM shows "… See more". Those
   keys are Facebook's schema names and outlive its generated class names by
   years. The DOM still decides WHICH posts a visitor can see; the payload only
   says what they really are. Measured on a public Photos tab: every tile was a
   `c0.169.1536.1536a` crop while the payload carried the 2048x1536 original —
   no hero could ever qualify off the tiles, and every site photo was a crop
   of a crop.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Comment, Tag

from app.config import settings
from app.models.facebook import (
    SERVICE_OPTIONS,
    FacebookHours,
    FacebookPage,
    FacebookPhoto,
    FacebookPost,
    FacebookReview,
    FacebookVideo,
)
from app.services.facebook_urls import FacebookRef, unwrap_outbound
from app.services.image_urls import is_cropped_photo_url, photo_identity
from app.services.nav_extraction import social_link_for_href


class FacebookRenderError(Exception):
    """A public read that could not produce a Page. Carries a status code."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


LOGIN_WALL_MESSAGE = (
    "Facebook redirected us to its login screen instead of showing this Page. "
    "Sign in once (./dev.sh fb-login) or connect a Page access token, then try "
    "again."
)

# Paths Facebook sends you to INSTEAD of the thing you asked for. Landing on one
# is the wall — it is Facebook's own statement, not our guess about its markup,
# and it is what catches the retired mbasic hosts now that they 302 to /login.
_WALL_PATH_RE = re.compile(r"^/(login|checkpoint|recover|privacy/consent)\b")

# Phrases that only appear when Facebook is refusing, matched against VISIBLE
# TEXT — never raw HTML, where Facebook's own string tables and login dialog
# would match on every page including the good ones. Deliberately whole phrases:
# a bare "log in" is on every Facebook page ever served, signed in or not.
#
# The wall's other tell is structural and already checked below: a login screen
# carries no `og:title`, so a Page with no identity fails regardless of wording.
_LOGIN_WALL_MARKERS = (
    "you must log in to continue",
    "this content isn't available right now",
    "this page isn't available",
)

# Markup that carries no words a reader sees. Skipped by every text scan: a
# rendered Facebook Page is ~900KB of mostly inline script, and a plain
# `get_text()` over the lot buried the label scan in minified JavaScript.
_NON_TEXT_TAGS = ("script", "style", "noscript", "template")

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
#
# An empty attribute is a label we don't read but must still RECOGNISE: it
# stops a scan from taking it as a value, and it is what tells
# `_scan_labelled` which way a run of About rows is laid out.
_TEXT_LABELS: dict[str, str] = {
    "address": "single_line_address",
    "service area": "service_area",
    "phone": "phone",
    "phone number": "phone",
    "mobile": "phone",
    "email": "email",
    "email address": "email",
    "website": "website",
    "price range": "price_range",
    "price": "price_range",
    "categories": "category",
    "category": "category",
    "founded": "founded",
    "products": "products",
    "mission": "mission",
    "impressum": "",
    "hours": "",
    # The About panel's social rows: value a handle, label the platform. The
    # profile itself is read off the row's outbound link (`_social_profiles`),
    # where it is a URL rather than a handle.
    "instagram": "", "tiktok": "", "youtube": "", "twitter": "", "linkedin": "",
    "pinterest": "", "whatsapp": "", "snapchat": "", "threads": "",
    # NO "about". On a Page, "About" is a navigation TAB — it appears three
    # times in the chrome and never once as a field label, so scanning from it
    # only ever lands on the next tab ("Photos"), which then became the
    # business's own description. The About prose has a better source that is
    # already read: `og:description`, which `parse_public_html` falls back to.
}

# Facebook's own UI chrome, which is never a field's VALUE. A closed set of
# Facebook's vocabulary rather than a business one — the same bet `_TEXT_LABELS`
# already makes, and a far safer one than guessing at industry nouns. It also
# separates the About panel's sections, which is what `_scan_labelled` reads a
# run's layout from.
_CHROME_VALUES = frozenset(
    {
        "posts", "about", "photos", "videos", "reels", "more", "home", "live",
        "mentions", "events", "reviews", "groups", "shop", "jobs", "offers",
        "community", "following", "followers", "likes", "albums", "intro",
        "featured", "see all", "see more on facebook", "verified account",
        "log in", "forgotten account?", "page transparency", "places lived",
        "contact and basic info", "privacy and legal info",
        "websites and social links", "contact info", "see all photos",
        # Panel headings, all three caught in the wild: a signed-in read of a
        # tadika reported its category as "Personal details" and NASA's as
        # "Details", and an anonymous read of Vans reported "Basic info" as its
        # website. A heading sits exactly where a value sits — one line under
        # the label — so nothing but this set separates them.
        "details", "personal details", "basic info",
    }
)

_HOURS_LINE_RE = re.compile(
    r"^(?P<day>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*[:\-–]?\s*"
    r"(?P<open>\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)\s*(?:-|–|to)\s*"
    r"(?P<close>\d{1,2}(?::\d{2})?\s*(?:AM|PM)?)$",
    re.I,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
# A website value has to be dereferenceable: an explicit scheme, or a bare host
# with a dot and a TLD-shaped tail. "Basic info" and "tadika_murni_1988" are
# neither — an underscore is not legal in a hostname label.
_URL_RE = re.compile(
    r"^(?:https?://\S+|(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}(?:[/?#]\S*)?$)",
    re.I,
)
_PHONE_RE = re.compile(r"\+?\d[\d\s().\-]{6,}\d")

# "Rating · 4.8 (120 reviews)" on a rated Page; "Not yet rated (3 reviews)" on
# one whose recommendations carry no stars.
_RATING_RE = re.compile(
    r"^(?:rating\s*·\s*(?P<stars>[0-5](?:\.\d)?)|not yet rated)\s*\((?P<count>[\d,]+)\s+reviews?\)$",
    re.I,
)
_RECOMMENDS_RE = re.compile(r"\brecommends\b", re.I)
_NOT_RECOMMENDED_RE = re.compile(r"\b(?:doesn['’]t|does not) recommend\b", re.I)

_MESSAGE_SELECTOR = '[data-ad-preview="message"], [data-ad-comet-preview="message"]'
_FACEBOOK_HOSTS = frozenset({"facebook.com", "www.facebook.com", "m.facebook.com"})
# A post's own text, not a novel: generous enough for any real caption (the
# grounding haystack wants all of it), bounded against a runaway node.
_MAX_POST_CHARS = 2000
# A truncated DOM caption shorter than this can't safely pick its full text
# out of the payload — "Hi" is the start of too many messages.
_MIN_CAPTION_KEY = 12
_SEE_MORE_RE = re.compile(r"(?:…|\.\.\.)?\s*see more$", re.I)


# --- one rendered view ----------------------------------------------------------


@dataclass(frozen=True)
class RenderedView:
    """One rendered tab, parsed once. The markup is ~900KB, and the wall test,
    the label scan and every reader below all want the same visible lines."""

    soup: BeautifulSoup
    lines: list[str]
    url: str

    @property
    def is_wall(self) -> bool:
        """True when Facebook refused the Page instead of serving it.

        `url` is where the render actually landed — the authoritative signal,
        and the only one that distinguishes a wall from a Page whose bundled
        login dialog merely mentions logging in.
        """
        try:
            path = urlparse(self.url).path or ""
        except ValueError:
            path = ""
        if _WALL_PATH_RE.match(path):
            return True
        lowered = "\n".join(self.lines).lower()
        return any(marker in lowered for marker in _LOGIN_WALL_MARKERS)


def load_view(html: str, final_url: str = "") -> RenderedView:
    soup = BeautifulSoup(html or "", "lxml")
    return RenderedView(soup=soup, lines=_text_lines(soup), url=final_url)


def is_login_wall(html: str, *, final_url: str = "") -> bool:
    """`RenderedView.is_wall` for a caller holding only markup."""
    if not (html or "").strip():
        return True
    return load_view(html, final_url).is_wall


def _visible_text(node: BeautifulSoup | Tag) -> str:
    """The node's words: every string a reader sees, one per line.

    Filters rather than deleting. The obvious implementation — decompose the
    script tags, then `get_text()` — also destroys the `application/ld+json`
    block, which is a `<script>` and is the most trustworthy source of address
    and phone on the page. Skipping the same nodes on the way out costs one
    ancestor walk and leaves the document intact for every other reader.
    """
    chunks: list[str] = []
    for string in node.find_all(string=True):
        if isinstance(string, Comment):
            continue
        if any(parent.name in _NON_TEXT_TAGS for parent in string.parents):
            continue
        text = string.strip()
        if text:
            chunks.append(text)
    return "\n".join(chunks)


def _text_lines(node: BeautifulSoup | Tag) -> list[str]:
    return [line.strip() for line in _visible_text(node).split("\n") if line.strip()]


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


# --- labelled fields ------------------------------------------------------------

# A field whose value has a checkable shape. Applied HERE rather than only
# downstream, because a label followed by something unusable ("Email address"
# above a section heading) otherwise fills the slot and blocks the real value
# from being found further along the page.
_FIELD_SHAPE: dict[str, re.Pattern[str]] = {"email": _EMAIL_RE, "phone": _PHONE_RE}


def _label_key(line: str) -> str:
    return line.rstrip(":").strip().lower()


def _scan_labelled(lines: list[str]) -> dict[str, str]:
    """Pull labelled values out of the rendered text.

    Line-based because Facebook's generated class names churn constantly while
    the visible labels don't. A row is labelled one of three ways, and each is
    read on its own terms:

    * **Inline** — `Price range · ££` states its own pairing.
    * **A run of rows** — labels exactly two lines apart, one layout for the
      whole run. Facebook's About panel puts contact rows value-first (the
      address, then `Address`; the area, then `Service area`), so a run that
      ends on a label and starts on a value is read backward. Reading it
      forward pairs every label with the NEXT row's value: on a measured
      kindergarten Page the street address became the service area and the
      address was lost. A run that opens on a label reads forward.
    * **A lone label** — forward first, then backward, the forward pass
      winning, so a backward read can only fill a gap, never move an answer.

    A chrome line (`_CHROME_VALUES`) is never a value and always ends a run.
    """
    found: dict[str, str] = {}

    def offer(field: str, candidate: str | None) -> bool:
        text = (candidate or "").strip()
        if len(text) <= 1:
            return False
        lowered = _label_key(text)
        if lowered in _TEXT_LABELS or lowered in _CHROME_VALUES:
            return False
        shape = _FIELD_SHAPE.get(field)
        if shape and not shape.fullmatch(text):
            return False
        found[field] = text
        return True

    for line in lines:
        head, separator, tail = line.partition(" · ")
        field = _TEXT_LABELS.get(_label_key(head)) if separator else None
        if field and field not in found:
            offer(field, tail)

    for segment in _segments(lines):
        is_label = [_label_key(line) in _TEXT_LABELS for line in segment]
        for start, end in _label_runs(is_label):
            before = start > 0 and not is_label[start - 1]
            after = end + 1 < len(segment) and not is_label[end + 1]
            value_first = before and not after and end > start
            for index in range(start, end + 1, 2):
                field = _TEXT_LABELS[_label_key(segment[index])]
                if not field or field in found:
                    continue
                above = segment[index - 1] if index > 0 and not is_label[index - 1] else None
                below = (
                    segment[index + 1]
                    if index + 1 < len(segment) and not is_label[index + 1]
                    else None
                )
                for candidate in (above, below) if value_first else (below, above):
                    if offer(field, candidate):
                        break
    return found


def _segments(lines: list[str]) -> Iterator[list[str]]:
    """Stretches of lines between chrome lines."""
    segment: list[str] = []
    for line in lines:
        if _label_key(line) in _CHROME_VALUES:
            if segment:
                yield segment
            segment = []
        else:
            segment.append(line)
    if segment:
        yield segment


def _label_runs(is_label: list[bool]) -> Iterator[tuple[int, int]]:
    """(first, last) label index of each run of labels exactly two lines apart."""
    indices = [i for i, label in enumerate(is_label) if label]
    start = previous = None
    for index in indices:
        if previous is not None and index - previous == 2:
            previous = index
            continue
        if start is not None:
            yield start, previous
        start = previous = index
    if start is not None:
        yield start, previous


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


# --- the inline payload ---------------------------------------------------------


@dataclass(frozen=True)
class _Payload:
    # Photo identity → the largest uncropped rendition the payload names.
    originals: dict[str, FacebookPhoto]
    # Every message text the payload carries, verbatim, in document order.
    messages: tuple[str, ...]
    # (author, message) for each story that names its actor.
    stories: tuple[tuple[str, str], ...]
    # The Page's own posts, whole: words, date, id, pictures, videos.
    posts: tuple[FacebookPost, ...] = ()


# The Page's own feeds, by the schema names its data gives them: the timeline,
# and the reels collection the About page renders. A story anywhere else — a
# reviewer's recommendation on /reviews — is someone else's words.
_PAGE_FEEDS = frozenset({"timeline_list_feed_units", "aggregated_fb_shorts"})
# A story inside a story is a reshare: the Page's message, someone else's post.
_NESTED_STORY = frozenset({"attached_story"})


def _payload_blobs(soup: BeautifulSoup) -> Iterator[object]:
    for tag in soup.find_all("script", attrs={"type": "application/json"}):
        try:
            yield json.loads(tag.string or "")
        except (TypeError, ValueError):
            continue


def _objects(node: object, *, skip: frozenset[str] = frozenset()) -> Iterator[dict]:
    """Every JSON object under `node`, depth-first in document order, never
    entering a key in `skip`."""
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            yield current
            stack.extend(reversed([value for key, value in current.items() if key not in skip]))
        elif isinstance(current, list):
            stack.extend(reversed(current))


def _feed_stories(blob: object) -> Iterator[dict]:
    """The outermost Story objects inside one of the Page's own feeds."""
    stack: list[tuple[object, bool]] = [(blob, False)]
    while stack:
        current, in_feed = stack.pop()
        if isinstance(current, dict):
            if in_feed and current.get("__typename") == "Story" and "creation_time" in current:
                yield current
                continue
            stack.extend(reversed([
                (value, in_feed or key in _PAGE_FEEDS) for key, value in current.items()
            ]))
        elif isinstance(current, list):
            stack.extend(reversed([(value, in_feed) for value in current]))


def _payload_photo(node: dict) -> FacebookPhoto | None:
    uri, width, height = node.get("uri"), node.get("width"), node.get("height")
    if not (isinstance(uri, str) and isinstance(width, int) and isinstance(height, int)):
        return None
    if width <= 0 or height <= 0 or is_cropped_photo_url(uri):
        return None
    if not (photo_identity(uri) or "").startswith("fbcdn:"):
        return None
    return FacebookPhoto(url=uri, width=width, height=height)


def _message_text(node: dict) -> str:
    message = node.get("message")
    if isinstance(message, dict) and isinstance(message.get("text"), str):
        return message["text"].strip()
    return ""


def _first_actor(node: dict) -> str:
    actors = node.get("actors")
    if isinstance(actors, list) and actors and isinstance(actors[0], dict):
        name = actors[0].get("name")
        return name.strip() if isinstance(name, str) else ""
    return ""


def _photo_object(node: dict) -> FacebookPhoto | None:
    """A `Photo` object's largest uncropped rendition, carrying Facebook's own
    description of it when the object states one."""
    renditions = [
        photo for value in node.values()
        if isinstance(value, dict) and (photo := _payload_photo(value)) is not None
    ]
    if not renditions:
        return None
    best = max(renditions, key=_area)
    alt = node.get("accessibility_caption")
    if isinstance(alt, str) and alt.strip():
        best = best.model_copy(update={"auto_alt": alt.strip()})
    return best


def _image_uri(value: object) -> str | None:
    """`{"uri": …}`, `{"image": {"uri": …}}` or a bare URI, on Facebook's CDN."""
    if isinstance(value, dict):
        value = value.get("uri") if "uri" in value else _image_uri(value.get("image"))
    if isinstance(value, str) and (photo_identity(value) or "").startswith("fbcdn:"):
        return value
    return None


def _positive(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


def _video_object(node: dict) -> FacebookVideo | None:
    """A `Video` object: its own permalink and the frame Facebook shows for it."""
    url = next(
        (value for key in ("permalink_url", "url", "shareable_url")
         if isinstance(value := node.get(key), str) and _is_facebook_link(value)),
        None,
    )
    if url is None:
        return None
    thumbnail = next(
        (uri for key in ("preferred_thumbnail", "thumbnailImage", "first_frame_thumbnail")
         if (uri := _image_uri(node.get(key)))),
        None,
    )
    video_id = node.get("id")
    return FacebookVideo(
        url=url,
        video_id=str(video_id) if video_id else None,
        thumbnail_url=thumbnail,
        width=_positive(node.get("width")),
        height=_positive(node.get("height")),
        duration_ms=_positive(node.get("playable_duration_in_ms")),
    )


def _feed_post(story: dict) -> FacebookPost | None:
    """One of the Page's own posts, read whole from its Story object.

    The pictures come from the story's `attachments` only — never the actor's
    avatar or a commenter's — and a reshared story's are skipped with its words.
    """
    own = list(_objects(story, skip=_NESTED_STORY))
    message = next((text for node in own if (text := _message_text(node))), "")
    photos: dict[str, FacebookPhoto] = {}
    videos: dict[str, FacebookVideo] = {}
    for attachments in (node["attachments"] for node in own if isinstance(node.get("attachments"), list)):
        for node in _objects(attachments):
            kind = node.get("__typename")
            if kind == "Photo" and (photo := _photo_object(node)) is not None:
                keep_larger_originals(photos, {photo_identity(photo.url) or photo.url: photo})
            elif kind == "Video" and (video := _video_object(node)) is not None:
                held = videos.get(video.video_id or video.url)
                if held is None or (video.thumbnail_url and not held.thumbnail_url):
                    videos[video.video_id or video.url] = video
    if not (message or photos or videos):
        return None
    permalink = story.get("permalink_url")
    if not (isinstance(permalink, str) and _is_facebook_link(permalink)):
        permalink = next((video.url for video in videos.values()), None)
    created, post_id = story.get("creation_time"), story.get("post_id")
    return FacebookPost(
        message=message[:_MAX_POST_CHARS] or None,
        created_time=(
            datetime.fromtimestamp(created, tz=timezone.utc).isoformat()
            if isinstance(created, int) and created > 0 else None
        ),
        post_id=str(post_id) if post_id else None,
        permalink=permalink,
        photos=list(photos.values()),
        videos=list(videos.values()),
    )


def _inline_payload(soup: BeautifulSoup) -> _Payload:
    originals: dict[str, FacebookPhoto] = {}
    messages: dict[str, None] = {}
    stories: dict[tuple[str, str], None] = {}
    posts: dict[str, FacebookPost] = {}
    for blob in _payload_blobs(soup):
        for node in _objects(blob):
            photo = _photo_object(node) if node.get("__typename") == "Photo" else _payload_photo(node)
            if photo is not None:
                keep_larger_originals(originals, {photo_identity(photo.url) or photo.url: photo})
            text = _message_text(node)
            if text:
                messages.setdefault(text)
                actor = _first_actor(node)
                if actor:
                    stories.setdefault((actor, text))
        for story in _feed_stories(blob):
            post = _feed_post(story)
            if post is not None:
                posts.setdefault(post.post_id or _fingerprint(post.message or "") or post.permalink or "", post)
    return _Payload(originals, tuple(messages), tuple(stories), tuple(posts.values()))


def _area(photo: FacebookPhoto) -> int:
    return (photo.width or 0) * (photo.height or 0)


def keep_larger_originals(into: dict[str, FacebookPhoto], found: dict[str, FacebookPhoto]) -> None:
    """Fold `found` into `into`, keeping the largest rendition per picture —
    and Facebook's description of it, whichever rendition carried that."""
    for identity, photo in found.items():
        best = into.get(identity)
        if best is None:
            into[identity] = photo
            continue
        larger, smaller = (photo, best) if _area(photo) > _area(best) else (best, photo)
        if not larger.auto_alt and smaller.auto_alt:
            larger = larger.model_copy(update={"auto_alt": smaller.auto_alt})
        into[identity] = larger


def _fingerprint(text: str) -> str:
    """Letters and digits only: the DOM paints emoji as `<img>` and wraps lines
    where the payload has newlines, so only the words compare."""
    return re.sub(r"[\W_]+", "", text.casefold())


def _full_caption(shown: str, messages: tuple[str, ...]) -> str | None:
    """The whole caption behind a DOM caption cut off at "… See more"."""
    shown = _SEE_MORE_RE.sub("", shown or "").rstrip(" …").strip()
    key = _fingerprint(shown)
    if len(key) >= _MIN_CAPTION_KEY:
        for text in messages:
            if _fingerprint(text).startswith(key):
                return text[:_MAX_POST_CHARS]
    return shown[:_MAX_POST_CHARS] or None


# --- posts and photos -----------------------------------------------------------


def _hidden_within_page(node) -> bool:
    """Ignore hidden media within main; modal accessibility wrappers aren't access walls.

    Facebook marks the entire outer application aria-hidden while its login
    dialog overlays a publicly served Page. Only check the Page's own subtree;
    actual access refusal is handled by the wall test before any reader runs.
    """
    for element in (node, *node.parents):
        if element.get("aria-hidden") == "true" or element.has_attr("hidden"):
            return True
        if element.get("role") == "main":
            break
    return False


def _linked_photos(link, seen: set[str]) -> list[FacebookPhoto]:
    """Attachment URLs only, never UI art or avatars. Sizes come from the
    payload (`apply_originals`) or the decoded bytes — HTML dimensions are
    layout claims."""
    photos = []
    for image in link.select("img[src]")[:20]:
        url = image.get("src", "")
        parsed = urlparse(url)
        if not (parsed.hostname or "").endswith((".fbcdn.net", ".fbsbx.com")):
            continue
        if "/images/" in parsed.path or "/t39.30808-1/" in parsed.path or url in seen:
            continue
        if _hidden_within_page(image):
            continue
        seen.add(url)
        photos.append(FacebookPhoto(url=url))
    return photos


def _is_facebook_link(href: str) -> bool:
    return urlparse(href).hostname in _FACEBOOK_HOSTS


def _article_posts(view: RenderedView, payload: _Payload) -> list[FacebookPost]:
    """The posts a visitor can see, each with its whole caption.

    A post is kept for its photos OR its words: a text-only update is grounding
    the site can use, and a video post's caption says what the business does
    even though its frames are no use here.
    """
    posts = []
    for article in view.soup.select('[role="article"]')[: settings.facebook_max_posts]:
        if _hidden_within_page(article):
            continue
        photos: dict[str, FacebookPhoto] = {}
        seen: set[str] = set()
        permalink = None
        for link in article.select("a[href]"):
            href = urljoin(view.url, link.get("href", ""))
            if not _is_facebook_link(href):
                continue
            if not any(part in urlparse(href).path for part in ("/photo", "/posts/", "/permalink/")):
                continue
            for photo in _linked_photos(link, seen):
                photos[photo.url] = photo
                permalink = href
        # An explicit post-message element, never surrounding UI text.
        node = article.select_one(_MESSAGE_SELECTOR)
        message = _full_caption(node.get_text(" ", strip=True), payload.messages) if node else None
        if photos or message:
            posts.append(FacebookPost(message=message, photos=list(photos.values()), permalink=permalink))
    return posts


def _photo_tiles(view: RenderedView, known: list[FacebookPost]) -> list[FacebookPost]:
    """Photos-tab tiles, which sit outside any post article and carry no caption."""
    seen = {photo.url for post in known for photo in post.pictures}
    tiles = []
    for link in view.soup.select("a[href]"):
        href = urljoin(view.url, link.get("href", ""))
        if not _is_facebook_link(href):
            continue
        # A dedicated photo link is provenance; a Photos navigation tab isn't.
        if urlparse(href).path not in {"/photo", "/photo/", "/photo.php"}:
            continue
        if _hidden_within_page(link):
            continue
        photos = _linked_photos(link, seen)
        if photos:
            tiles.append(FacebookPost(photos=photos, permalink=href))
        if len(seen) >= settings.facebook_photo_max_images:
            break
    return tiles


def _cover_photo(soup: BeautifulSoup) -> str | None:
    """The Page's cover, by the attribute Facebook stamps on it for its own
    performance logging — a name it chose, not a generated class."""
    image = soup.find("img", attrs={"data-imgperflogname": "profileCoverPhoto"})
    src = (image.get("src") or "").strip() if image else ""
    return src if photo_identity(src) else None


# --- Intro facts ----------------------------------------------------------------


def _intro_categories(lines: list[str]) -> list[str]:
    """`Page · Food and drink · Caterer · Snack van` — every category the Page
    files itself under, where the label scan finds only the first."""
    for index, line in enumerate(lines):
        if line == "Page" and index + 1 < len(lines) and lines[index + 1].startswith("·"):
            rest = lines[index + 1]
        elif line.startswith("Page ·"):
            rest = line[len("Page"):]
        else:
            continue
        return [item.strip() for item in rest.split("·") if item.strip()]
    return []


def _intro_text(lines: list[str]) -> str | None:
    """The Intro card's bio — the line under Facebook's "Intro" heading."""
    for index, line in enumerate(lines[:-1]):
        if line == "Intro":
            return lines[index + 1]
    return None


def _untruncated(about: str | None, intro: str | None) -> str | None:
    """The Intro's whole text when `og:description` cut the same text short.

    Facebook clips the og blurb ("…preparing them...") and prefixes it with the
    Page's name and city; the Intro card shows the bio whole. Only a bio that
    carries the clipped text's last words replaces it, so an unrelated line
    under "Intro" can never stand in for the Page's description.
    """
    if not about or not intro or not about.rstrip().endswith(("...", "…")):
        return None
    tail = _fingerprint(about.rstrip().rstrip(".…"))[-30:]
    return intro if len(tail) >= _MIN_CAPTION_KEY and tail in _fingerprint(intro) else None


def _service_options(lines: list[str]) -> list[str]:
    """`Takeaway · Roadside collection` — kept verbatim, and only when every
    item is Facebook's own service-option vocabulary. Two items at least: a
    lone "Delivery" is as likely a word in a post as a row in the Intro."""
    for line in lines:
        items = [item.strip() for item in line.split("·")]
        if len(items) >= 2 and all(item.casefold() in SERVICE_OPTIONS for item in items):
            return items
    return []


def _rating(lines: list[str]) -> tuple[float | None, int | None]:
    for line in lines:
        match = _RATING_RE.match(line)
        if match:
            stars = match.group("stars")
            return (float(stars) if stars else None), int(match.group("count").replace(",", ""))
    return None, None


def _social_profiles(view: RenderedView) -> list[str]:
    """The Page's profiles elsewhere, off its own outbound links.

    Facebook routes every outbound link through `l.facebook.com`, so the
    Instagram row is a link to facebook.com in the markup and a handle in the
    text; unwrapped, it is the profile URL. Links inside posts are someone's
    content, not the Page's identity, and are skipped.
    """
    profiles: list[str] = []
    for anchor in view.soup.select('a[href*="facebook.com/l.php"]'):
        if anchor.find_parent(attrs={"role": "article"}):
            continue
        destination = unwrap_outbound(anchor.get("href", ""))
        link = social_link_for_href(destination, view.url) if destination else None
        if link and link.label != "Facebook" and link.href not in profiles:
            profiles.append(link.href)
    return profiles


# --- recommendations ------------------------------------------------------------


def _recommendations(view: RenderedView, payload: _Payload) -> list[FacebookReview]:
    """`<Name> recommends <Page>.` stories, with the words the reviewer wrote.

    The heading names the reviewer and the verdict; the text is the story's own
    `message` in the payload, matched by that reviewer — the DOM shows it
    between a date and a reactions counter with nothing marking where it
    starts. A "doesn't recommend" is not a testimonial, and a recommendation
    with no words has nothing to quote.
    """
    texts: dict[str, str] = {}
    for author, text in payload.stories:
        texts.setdefault(author, text)
    reviews: list[FacebookReview] = []
    for article in view.soup.select('[role="article"]'):
        if len(reviews) >= settings.facebook_max_reviews:
            break
        if _hidden_within_page(article):
            continue
        heading = article.find(["h2", "h3", "h4"])
        title = heading.get_text(" ", strip=True) if heading else ""
        if not _RECOMMENDS_RE.search(title) or _NOT_RECOMMENDED_RE.search(title):
            continue
        reviewer = heading.find("a")
        author = reviewer.get_text(" ", strip=True) if reviewer else ""
        text = texts.get(author)
        if author and text:
            reviews.append(FacebookReview(text=text, author=author))
    return reviews


# --- views ----------------------------------------------------------------------


@dataclass(frozen=True)
class ViewRead:
    """What one rendered tab offers the Page. Every field is a gap-fill: see
    `merge_view`, which never overwrites a fact an earlier view supplied."""

    posts: list[FacebookPost] = field(default_factory=list)
    reviews: list[FacebookReview] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    service_options: list[str] = field(default_factory=list)
    social_profiles: list[str] = field(default_factory=list)
    cover_photo_url: str | None = None
    intro: str | None = None
    rating_count: int | None = None
    overall_star_rating: float | None = None
    originals: dict[str, FacebookPhoto] = field(default_factory=dict)


def _read_common(view: RenderedView, payload: _Payload) -> ViewRead:
    """What every tab shows: the header's cover, the Intro, the photo data."""
    stars, count = _rating(view.lines)
    return ViewRead(
        categories=_intro_categories(view.lines),
        service_options=_service_options(view.lines),
        social_profiles=_social_profiles(view),
        cover_photo_url=_cover_photo(view.soup),
        intro=_intro_text(view.lines),
        rating_count=count,
        overall_star_rating=stars,
        originals=payload.originals,
    )


def read_timeline(view: RenderedView) -> ViewRead:
    """The Page's posts: whole from its feeds' data, then any the painted
    articles add — `merge_posts` joins the two readings of one post."""
    payload = _inline_payload(view.soup)
    posts = [*payload.posts, *_article_posts(view, payload)]
    return replace(_read_common(view, payload), posts=posts)


def read_photos(view: RenderedView) -> ViewRead:
    payload = _inline_payload(view.soup)
    posts = [*payload.posts, *_article_posts(view, payload)]
    return replace(_read_common(view, payload), posts=posts + _photo_tiles(view, posts))


def read_reviews(view: RenderedView) -> ViewRead:
    payload = _inline_payload(view.soup)
    return replace(_read_common(view, payload), reviews=_recommendations(view, payload))


def merge_view(page: FacebookPage, read: ViewRead) -> None:
    """Fold one view into the Page. Fills gaps only — a fact an earlier view
    (above all the ladder's About read) supplied is never overwritten."""
    for name in ("cover_photo_url", "rating_count", "overall_star_rating"):
        if getattr(page, name) is None and getattr(read, name) is not None:
            setattr(page, name, getattr(read, name))
    for name in ("categories", "service_options", "social_profiles"):
        if not getattr(page, name) and getattr(read, name):
            setattr(page, name, list(getattr(read, name)))
    if not page.category and page.categories:
        page.category = page.categories[0]
    page.about = _untruncated(page.about, read.intro) or page.about
    merge_posts(page, read.posts)
    _merge_reviews(page, read.reviews)


def _held_post(page: FacebookPage, post: FacebookPost) -> FacebookPost | None:
    """The post the Page already holds that `post` is another reading of: the
    same id, the same words, or — when one of the two has no words — a picture
    they share. Two captioned posts sharing a picture stay two posts."""
    key = _fingerprint(post.message or "")
    pictures = {photo_identity(photo.url) or photo.url for photo in post.pictures}
    for held in page.posts:
        if post.post_id and held.post_id == post.post_id:
            return held
        if key and held.message and _fingerprint(held.message) == key:
            return held
        if (not key or not held.message) and pictures & {
            photo_identity(photo.url) or photo.url for photo in held.pictures
        }:
            return held
    return None


def merge_posts(page: FacebookPage, posts: list[FacebookPost]) -> None:
    """Fold posts into the Page, joining what is one post read twice. Every
    reader folds through here — the render's views and the Graph read alike.

    The About page's reels, the timeline's data and its painted articles all
    describe the same posts. A post already held gains what the new reading
    adds — its date, id, permalink, words, pictures — instead of arriving again
    as a stranger: before this, a captioned story and the caption-less tiles of
    its own photos stayed two half-posts, neither of them whole.

    Two caps, kept apart: pictures by `facebook_photo_max_images`, text-only
    posts by `facebook_max_posts`. One shared cap would let a full Photos tab
    crowd out every caption, or a wordy timeline every photo.
    """
    seen = {photo_identity(url) or url for url in page.post_photo_urls}
    profile = photo_identity(page.profile_picture_url or "") or page.profile_picture_url
    known_videos = {video.video_id or video.url for held in page.posts for video in held.videos}

    def fresh(photo: FacebookPhoto) -> bool:
        """Claim a picture for the Page: new, not the mark, within the cap."""
        identity = photo_identity(photo.url) or photo.url
        if identity in seen or identity == profile or len(seen) >= settings.facebook_photo_max_images:
            return False
        seen.add(identity)
        return True

    for post in posts:
        photos = [photo for photo in post.photo_sources if fresh(photo)]
        videos = []
        for video in post.videos:
            if (video.video_id or video.url) in known_videos:
                continue
            known_videos.add(video.video_id or video.url)
            frame = FacebookPhoto(url=video.thumbnail_url) if video.thumbnail_url else None
            videos.append(video if frame is None or fresh(frame) else video.model_copy(update={"thumbnail_url": None}))
        held = _held_post(page, post)
        if held is not None:
            for name in ("message", "created_time", "post_id", "permalink"):
                if not getattr(held, name) and getattr(post, name):
                    setattr(held, name, getattr(post, name))
            held.photos.extend(photos)
            held.videos.extend(videos)
            continue
        if not (photos or videos) and (
            not post.message or len(page.posts_with_text) >= settings.facebook_max_posts
        ):
            continue
        page.posts.append(post.model_copy(update={"photos": photos, "videos": videos, "image_url": None}))


def _merge_reviews(page: FacebookPage, reviews: list[FacebookReview]) -> None:
    seen = {(review.author, review.text) for review in page.reviews}
    for review in reviews:
        if len(page.reviews) >= settings.facebook_max_reviews:
            return
        if (review.author, review.text) not in seen:
            seen.add((review.author, review.text))
            page.reviews.append(review)


def apply_originals(page: FacebookPage, originals: dict[str, FacebookPhoto]) -> None:
    """Swap every crop or thumbnail for the original the payload named, with
    the size Facebook states for it. A photo no payload named stays as read;
    the pixel pass still measures whatever is kept."""

    def upgrade(photo: FacebookPhoto) -> FacebookPhoto:
        original = originals.get(photo_identity(photo.url) or "")
        if original is None:
            return photo
        keep = _area(photo) >= _area(original) and not is_cropped_photo_url(photo.url)
        better = photo if keep else original
        alt = photo.auto_alt or original.auto_alt
        return better if better.auto_alt == alt else better.model_copy(update={"auto_alt": alt})

    for post in page.posts:
        post.photos = [upgrade(photo) for photo in post.photos]
    if page.cover_photo_url:
        page.cover_photo_url = upgrade(FacebookPhoto(url=page.cover_photo_url)).url


def fold_view(
    page: FacebookPage,
    originals: dict[str, FacebookPhoto],
    read: Callable[[RenderedView], ViewRead],
    html: str,
    url: str,
) -> bool:
    """Fold one rendered step of a view into `page`; False when it is a wall,
    which ends the views stage. The live reader and the offline replay of a
    capture (`scripts/measure_facebook_read.py`) fold through this one step."""
    loaded = load_view(html, url)
    if loaded.is_wall:
        return False
    found = read(loaded)
    merge_view(page, found)
    keep_larger_originals(originals, found.originals)
    return True


def finish_views(page: FacebookPage, originals: dict[str, FacebookPhoto]) -> None:
    """Close the views stage: originals for every crop, and what is missing."""
    apply_originals(page, originals)
    page.missing_fields = missing_fields_for(page)


def missing_fields_for(page: FacebookPage) -> list[str]:
    """The field groups this read didn't reach, for the preview checklist.

    Derived from what was actually read rather than assumed per fetch path: a
    render that found the Page's recommendations has nothing missing there, and
    a Page that states "(0 reviews)" has no recommendations to miss.
    """
    missing = []
    if not page.phone and not page.emails:
        missing.append("contact")
    if not (page.about or page.description or page.mission):
        missing.append("profile")
    if not page.posts:
        missing.append("posts")
    if not page.reviews and page.rating_count != 0:
        missing.append("reviews")
    return missing


# --- the Page itself ------------------------------------------------------------


def parse_public_html(
    html: str,
    ref: FacebookRef,
    *,
    final_url: str = "",
    signed_in: bool = False,
) -> FacebookPage:
    """Build a `FacebookPage` from rendered markup. Pure — no I/O.

    Raises `FacebookRenderError` when the render hit a login wall or carries no
    usable identity: a Page with nothing but a URL is worse than an error,
    because everything downstream would have to invent the rest.
    """
    if not (html or "").strip():
        raise FacebookRenderError(LOGIN_WALL_MESSAGE, status=422)

    view = load_view(html, final_url)
    if view.is_wall:
        raise FacebookRenderError(LOGIN_WALL_MESSAGE, status=422)
    soup = view.soup

    og_title = _meta(soup, "og:title")
    name = (og_title or ref.handle or "").strip()
    if not name:
        raise FacebookRenderError(
            "Couldn't read anything from that Facebook Page.", status=422
        )

    blurb, fan_count = _split_og_description(_meta(soup, "og:description"))
    labelled = _scan_labelled(view.lines)

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
    # Shape-gated for the same reason email and phone are, and it was the one
    # scanned field with no gate: `to_source_content` turns this into a
    # `NavLink(label="Website", href=…)` on every page of the site, so a value
    # that isn't a URL ships a dead link in the footer of the whole build. Two
    # real ones: "Basic info" (a panel heading) and "tadika_murni_1988" (a
    # handle, not an address).
    website = labelled.get("website")
    if website and not _URL_RE.match(website.strip()):
        website = None

    page = FacebookPage(
        name=name,
        canonical_url=_meta(soup, "og:url") or ref.canonical_url,
        page_id=ref.page_id,
        username=ref.handle,
        category=labelled.get("category"),
        about=blurb,
        mission=labelled.get("mission"),
        products=labelled.get("products"),
        founded=labelled.get("founded"),
        price_range=labelled.get("price_range"),
        phone=phone,
        emails=[email] if email else [],
        website=website,
        service_area=labelled.get("service_area"),
        single_line_address=labelled.get("single_line_address") or None,
        hours=_scan_hours(view.lines),
        fan_count=fan_count,
        profile_picture_url=_meta(soup, "og:image"),
        fetched_via="render_session" if signed_in else "render",
        # Always partial: no render, signed in or not, sees structured hours or
        # a post's date, so the UI should always offer the token.
        partial=True,
    )
    read = read_timeline(view)
    merge_view(page, read)
    apply_originals(page, read.originals)
    page.missing_fields = missing_fields_for(page)
    return page
