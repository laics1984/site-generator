"""
Canonicalize a source page's video embed URL.

Python mirror of ``frontend/src/preview/lib/videoEmbed.ts`` (itself vendored
verbatim from ``webtree-public/lib/videoEmbed.ts``, and mirrored again in
``builder/src/lib/video-embed.ts``). The renderer parses whatever string ends up
in a video element's ``content.src``; this module decides what that string is.
If the two disagree, a video extracted here silently fails to render there — so
``tests/test_video_embeds.VideoEmbedParityTest`` reads the TS file and asserts
every pattern in it has a counterpart here. Keep the two in lockstep.

Two rules deliberately DIVERGE from the TS, and both are load-bearing:

1. **No ``provider: "other"`` fallback.** The renderer accepts any bare https URL
   because a human may have pasted one into the builder and it must not be lost.
   Here the input is every ``<iframe>`` on a scraped page, so the same tolerance
   would embed Google Tag Manager, reCAPTCHA, Facebook like-boxes and chat
   widgets as page content. Verified on brightkids' homepage: 4 iframes, of which
   3 are trackers and widgets. The whitelist IS the feature.

2. **Playlists are rejected.** ``youtube.com/embed/videoseries?list=PL…`` carries
   no video id, and the shared ``embed/([A-Za-z0-9_-]{6,})`` pattern happily
   matches the literal ``videoseries`` — canonicalizing to a dead
   ``/embed/videoseries`` with the ``list`` param dropped. That is an upstream bug
   in ``videoEmbed.ts``; rather than reproduce it, this returns None. Fix it
   upstream and re-mirror before supporting playlists here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin

# Mirrors YOUTUBE_ID in videoEmbed.ts.
_YOUTUBE_ID = r"[A-Za-z0-9_-]{6,}"

# Order mirrors YOUTUBE_PATTERNS in videoEmbed.ts — `embed` before `watch`, so a
# URL matching both canonicalizes the same way on both sides.
_YOUTUBE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(rf"youtu\.be/({_YOUTUBE_ID})", re.I),
    re.compile(rf"youtube(?:-nocookie)?\.com/embed/({_YOUTUBE_ID})", re.I),
    re.compile(rf"youtube\.com/shorts/({_YOUTUBE_ID})", re.I),
    re.compile(rf"youtube\.com/watch\?(?:.*&)?v=({_YOUTUBE_ID})", re.I),
    re.compile(rf"youtube\.com/v/({_YOUTUBE_ID})", re.I),
)

_VIMEO_PATTERN = re.compile(r"vimeo\.com/(?:video/)?(\d+)", re.I)

# `<iframe … src="…">` — the TS tolerates a pasted snippet, so we do too.
_IFRAME_SRC = re.compile(r"""<iframe[^>]*\ssrc=["']([^"']+)["']""", re.I)

# Not a video id but a playlist marker; see module docstring.
_PLAYLIST_IDS = frozenset({"videoseries"})


@dataclass(frozen=True)
class ParsedVideo:
    """A canonical, renderable embed reference."""

    provider: str  # "youtube" | "vimeo"
    video_id: str
    embed_url: str
    thumbnail_url: str | None


def _youtube(video_id: str) -> ParsedVideo | None:
    if video_id.lower() in _PLAYLIST_IDS:
        return None
    return ParsedVideo(
        provider="youtube",
        video_id=video_id,
        embed_url=f"https://www.youtube.com/embed/{video_id}",
        # YouTube's own poster. Used for the lite-embed facade and, when a video
        # page has no photography at all, as the page's og:image.
        thumbnail_url=f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
    )


def _vimeo(video_id: str) -> ParsedVideo:
    return ParsedVideo(
        provider="vimeo",
        video_id=video_id,
        embed_url=f"https://player.vimeo.com/video/{video_id}",
        # Vimeo posters need an oEmbed round-trip; not worth a network call at
        # scrape time. VideoBlock renders the iframe directly when it is None.
        thumbnail_url=None,
    )


def parse_video_src(raw: str | None, base_url: str = "") -> ParsedVideo | None:
    """Canonicalize one embed reference, or None if it is not a known provider.

    ``base_url`` resolves relative and — the case that matters most in the wild —
    PROTOCOL-RELATIVE srcs. Old page builders emit ``//www.youtube.com/embed/ID``,
    which has no scheme of its own; ``urljoin`` promotes it to the base's.
    brightkids' entire video gallery is written that way, so without this every
    embed on it parses as nothing.
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

    for pattern in _YOUTUBE_PATTERNS:
        match = pattern.search(candidate)
        if match:
            return _youtube(match.group(1))
    vimeo = _VIMEO_PATTERN.search(candidate)
    if vimeo:
        return _vimeo(vimeo.group(1))
    return None
