"""
Canonicalize a source page's map embed URL.

Sibling of ``video_embed.py``, same shape and same contract: one pure function
turns whatever a scraped ``<iframe>`` carries into the string a ``video``
element's ``content.src`` needs, or None. ``video`` is the builder's only
iframe primitive — a map and a player are the same element type with different
URLs (see ``template_filler._bind_slot``) — so this is the second whitelist over
one DOM walk, not a second pipeline.

Why a whitelist and not "any iframe": the input is EVERY frame on a real page.
brightkids' homepage carries four, of which three are Google Tag Manager and a
Facebook like-box. Accepting the rest would embed trackers and chat widgets as
page content.

Two URL families reach us, and they need opposite treatment:

1. **Already an embed** — ``/maps/embed?pb=…`` (the "Share → Embed a map"
   product), ``/maps/embed/v1/…`` (the Embed API), or any viewer URL that
   already carries ``output=embed``. These are passed through verbatim. The
   ``pb`` payload encodes the exact pin, zoom and place id the site owner
   chose; rebuilding it from parts would silently move their map.
2. **A viewer URL somebody pasted into an iframe** — ``/maps/place/NAME/@lat,lng``
   or ``/maps?q=…`` with no ``output``. Framing those renders Google's
   "refused to connect" page, so they are rebuilt into the keyless
   ``maps.google.com/maps?q=…&output=embed`` form that
   ``section_content.maps_embed_url`` already proves renders.

One downstream hazard shapes the output: the CMS runs every schema value keyed
``src`` through ``MediaUrlResolver::normalize``, which splits on top-level
COMMAS to support multi-layer CSS backgrounds and drops any resulting fragment
that is not a URL. ``…/embed/v1/view?center=3.2,101.6`` would arrive as
``…center=3.2``. So commas are percent-encoded on the way out — a no-op for the
``pb`` form (which has none) and transparent to Google, which decodes ``%2C``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import ParseResult, parse_qs, quote_plus, unquote_plus, urljoin, urlparse

# `<iframe … src="…">` — tolerated for the same reason video_embed tolerates it:
# a src attribute may hold a pasted snippet rather than a bare URL.
_IFRAME_SRC = re.compile(r"""<iframe[^>]*\ssrc=["']([^"']+)["']""", re.I)

# Any Google ccTLD: google.com, google.com.my, google.co.uk, maps.google.com.
# Anchored at a dot or the start so `notgoogle.com` and `google.com.evil.com`
# do not match.
_GOOGLE_HOST = re.compile(r"(?:^|\.)google\.[a-z]{2,3}(?:\.[a-z]{2})?$", re.I)

_OSM_HOST = re.compile(r"(?:^|\.)openstreetmap\.org$", re.I)

# Paths under /maps that are not a viewable map: the JS SDK, tile and static
# endpoints. They never legitimately appear as an iframe src, but /maps-prefix
# matching would otherwise sweep them in.
_GOOGLE_NON_MAP_PATHS = ("/maps/api/", "/maps/vt")

# `@3.2165489,101.627416,17z` in a viewer path — the pin, when no place is named.
_PATH_COORDS = re.compile(r"@(-?\d+\.\d+),(-?\d+\.\d+)")

# `/maps/place/Bright+Kids+HQ/@…` and `/maps/search/kepong+kindergarten`.
_PATH_PLACE = re.compile(r"/maps/(?:place|search|dir)/([^/@?]+)", re.I)

# Query keys a viewer URL states its subject in, best-first.
_PLACE_QUERY_KEYS = ("q", "query", "center", "ll", "daddr")

# A lat/lng pair. Good enough to BUILD an embed URL from, never good enough to
# caption one — "3.2165,101.6274" under a map is worse than no caption at all.
_BARE_COORDS = re.compile(r"^-?\d+(?:\.\d+)?,\s*-?\d+(?:\.\d+)?$")


@dataclass(frozen=True)
class ParsedMap:
    """A canonical, frameable map reference."""

    provider: str  # "google" | "osm"
    embed_url: str
    # The place the URL names, when it names one. Only a `q=`/`/place/` URL
    # does; the `pb=` form encodes its label in an undocumented, positional
    # payload, and guessing at it would caption a map with a language code.
    place: str | None = None


def _comma_safe(url: str) -> str:
    """Percent-encode commas so the CMS's CSS-layer splitter leaves the URL whole.

    See the module docstring — ``MediaUrlResolver::normalize`` treats a
    top-level comma in any ``src`` as a background-layer separator.
    """
    return url.replace(",", "%2C")


def _place_from_query(query: dict[str, list[str]]) -> str | None:
    for key in _PLACE_QUERY_KEYS:
        for value in query.get(key, []):
            text = value.strip()
            if text:
                return text
    return None


def _display_place(place: str | None) -> str | None:
    """A place fit to caption a map with, or None."""
    if not place or _BARE_COORDS.match(place):
        return None
    return place


def _place_from_path(path: str) -> str | None:
    match = _PATH_PLACE.search(path)
    if match:
        # `/place/` segments are `+`-joined and percent-encoded.
        place = unquote_plus(match.group(1)).strip()
        if place:
            return place
    coords = _PATH_COORDS.search(path)
    if coords:
        return f"{coords.group(1)},{coords.group(2)}"
    return None


def _host(parsed: ParseResult) -> str:
    """Bare lowercase hostname — userinfo and port stripped.

    Both are attacker-controlled decoration around the part that decides trust:
    ``https://google.com@evil.test/maps`` has netloc ``google.com@evil.test``
    and host ``evil.test``.
    """
    return parsed.netloc.rpartition("@")[2].partition(":")[0].lower()


def _google(url: str, parsed: ParseResult) -> ParsedMap | None:
    if not _GOOGLE_HOST.search(_host(parsed)):
        return None
    path = parsed.path or "/"
    if any(path.startswith(prefix) for prefix in _GOOGLE_NON_MAP_PATHS):
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)

    # Family 1 — already frameable, so never rebuilt. `output=embed` covers the
    # `maps.google.com/maps?q=…&output=embed` form the generator itself emits,
    # which means a site that was BUILT by this tool round-trips unchanged.
    already_embed = path.startswith("/maps/embed") or any(
        "embed" in value.lower() for value in query.get("output", [])
    )
    if already_embed:
        return ParsedMap("google", _comma_safe(url), _display_place(_place_from_query(query)))

    # Family 2 — a viewer URL. Framed as-is Google refuses the connection, so
    # rebuild it into the keyless embed form. Same construction as
    # `section_content.maps_embed_url`, which is the proven-renderable shape.
    if not (path == "/maps" or path.startswith("/maps/")):
        return None
    place = _place_from_query(query) or _place_from_path(path)
    if not place:
        return None
    return ParsedMap(
        "google",
        f"https://maps.google.com/maps?q={quote_plus(place)}&output=embed",
        _display_place(place),
    )


def _openstreetmap(url: str, parsed: ParseResult) -> ParsedMap | None:
    if not _OSM_HOST.search(_host(parsed)):
        return None
    # Only the export widget is frameable; the main viewer sets X-Frame-Options.
    if not parsed.path.startswith("/export/embed.html"):
        return None
    return ParsedMap("osm", _comma_safe(url), None)


# Providers, in match order. Adding one is a function and a line — the DOM walk,
# the block, the mapper and the catalog template are all provider-agnostic.
_PROVIDERS: tuple[Callable[[str, ParseResult], ParsedMap | None], ...] = (
    _google,
    _openstreetmap,
)


def parse_map_src(raw: str | None, base_url: str = "") -> ParsedMap | None:
    """Canonicalize one map embed reference, or None if it is not a known map.

    ``base_url`` resolves relative and protocol-relative srcs, exactly as
    ``video_embed.parse_video_src`` does: page builders of a certain vintage
    emit ``//maps.google.com/maps?…``, which has no scheme of its own.
    """
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate:
        return None
    snippet = _IFRAME_SRC.search(candidate)
    if snippet:
        candidate = snippet.group(1).strip()
    if base_url:
        candidate = urljoin(base_url, candidate)
    elif candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if not candidate.lower().startswith(("http://", "https://")):
        return None

    parsed = urlparse(candidate)
    for provider in _PROVIDERS:
        found = provider(candidate, parsed)
        if found is not None:
            return found
    return None
