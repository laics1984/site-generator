"""
Tile icons: a small curated glyph set, emitted as inline SVG data URIs.

Why data URIs rather than an icon package: the glyph has to survive four
renderers written in three frameworks — the builder (React), webtree-public
(Nuxt/Vue), the mirrored preview port, and any future consumer of the pushed
JSON. If each fetched icons from its own package they would drift in weight,
metrics and coverage, and a missing name would fail differently in each. An
inline SVG travels *inside* the document as an ordinary image `src`, so every
renderer just draws an `<img>` and there is nothing to keep in sync. The same
reasoning already produced `media.monogram_avatar_url` and section_content's
WhatsApp glyph.

Colour is applied at fill time (`template_filler._icon_src`) from the theme, so
one glyph definition serves every brand without a per-palette variant.

Paths are 24x24, single-colour, stroke-free (fill-rule friendly), drawn from the
MIT-licensed Lucide set's geometry. Keep them simple: a tile icon renders at
~36px, where detail is lost anyway.
"""

from __future__ import annotations

import re
from urllib.parse import quote

# name -> SVG path data on a 24x24 viewBox.
GLYPHS: dict[str, str] = {
    "clock": "M12 2a10 10 0 100 20 10 10 0 000-20zm1 5h-2v6l5 3 1-1.7-4-2.3V7z",
    "shield": "M12 2l8 3v6c0 5-3.4 9.7-8 11-4.6-1.3-8-6-8-11V5l8-3z",
    "bolt": "M13 2L4.5 13.5H11l-1 8.5 8.5-11.5H12l1-8.5z",
    "chart": "M4 20h16v2H4v-2zm2-8h3v7H6v-7zm5-6h3v13h-3V6zm5 3h3v10h-3V9z",
    "check": "M9 16.2L4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4L9 16.2z",
    "star": "M12 2l3 6.5 7 .9-5 4.8 1.2 7L12 17.8 5.8 21.2 7 14.2 2 9.4l7-.9L12 2z",
    "heart": "M12 21C7 17 2 13.4 2 8.9A5 5 0 0112 6a5 5 0 0110 2.9C22 13.4 17 17 12 21z",
    "users": "M9 12a4 4 0 100-8 4 4 0 000 8zm0 2c-3.3 0-8 1.7-8 4v3h16v-3c0-2.3-4.7-4-8-4z"
             "m9-2a3 3 0 100-6 3 3 0 000 6zm5 6v3h-3v-3c0-1.4-.7-2.5-1.8-3.3 3 .4 4.8 1.8 4.8 3.3z",
    "globe": "M12 2a10 10 0 100 20 10 10 0 000-20zm0 2c1.4 0 3 2.4 3.5 6h-7C9 6.4 10.6 4 12 4z"
             "M4.3 10h3.2a20 20 0 000 4H4.3a8 8 0 010-4zm0 6h3.4c.5 2.6 1.5 4.4 2.3 5a8 8 0 01-5.7-5z"
             "m9.7 5c.8-.6 1.8-2.4 2.3-5h3.4a8 8 0 01-5.7 5zM16.5 14a20 20 0 000-4h3.2a8 8 0 010 4h-3.2z"
             "M9.5 14a18 18 0 010-4h5a18 18 0 010 4h-5z",
    "lock": "M17 9V7a5 5 0 00-10 0v2H5v13h14V9h-2zm-8-2a3 3 0 016 0v2H9V7z",
    "gear": "M12 8a4 4 0 100 8 4 4 0 000-8zm9.4 4l1.8 1.4-1.8 3.2-2.2-.7a7.6 7.6 0 01-1.8 1l-.4 2.3h-3.6"
            "l-.4-2.3a7.6 7.6 0 01-1.8-1l-2.2.7-1.8-3.2L4.6 12l-1.8-1.4 1.8-3.2 2.2.7a7.6 7.6 0 011.8-1"
            "L9 4.8h3.6l.4 2.3a7.6 7.6 0 011.8 1l2.2-.7 1.8 3.2L21.4 12z",
    "sparkle": "M12 2l2 6 6 2-6 2-2 6-2-6-6-2 6-2 2-6zm7 12l1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3z",
    "leaf": "M20 4C10 4 4 9 4 16c0 1.5.3 2.8.8 4l2-2c2.8.4 9.2-.4 12-9-2 5-6.4 7.4-10.6 7.6"
            "C9.4 12.4 13.4 9 20 8V4z",
    "phone": "M6.6 2.5l3.2 3.2-2.1 2.1a13 13 0 006.5 6.5l2.1-2.1 3.2 3.2-2.6 2.6C11.6 20.2 3.8 12.4 4 4.9L6.6 2.5z",
    "mail": "M2 5h20v14H2V5zm2 2v.4l8 5 8-5V7H4zm16 3.2l-8 5-8-5V17h16v-6.8z",
    "map-pin": "M12 2a7 7 0 00-7 7c0 5 7 13 7 13s7-8 7-13a7 7 0 00-7-7zm0 9.5A2.5 2.5 0 1112 6.5a2.5 2.5 0 010 5z",
    "calendar": "M7 2v2H4v18h16V4h-3V2h-2v2H9V2H7zM6 10h12v10H6V10z",
    "book": "M4 3h11a4 4 0 014 4v14H8a4 4 0 01-4-4V3zm2 2v12a2 2 0 002 2h9V7a2 2 0 00-2-2H6z",
    "truck": "M2 5h12v10H2V5zm13 3h3.6l2.4 3.2V15h-6V8zM6 20a2 2 0 100-4 2 2 0 000 4zm11 0a2 2 0 100-4 2 2 0 000 4z",
    "wrench": "M21 5.5l-3.5 3.5-2.5-2.5L18.5 3A6 6 0 0010 10.8L3 17.8 6.2 21l7-7A6 6 0 0021 5.5z",
    "palette": "M12 3a9 9 0 000 18c1.7 0 2-1 1.4-1.9-.7-1 .1-2.1 1.3-2.1H17a4 4 0 004-4c0-5.5-4-10-9-10z"
               "M7 11a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm4-3a1.5 1.5 0 110-3 1.5 1.5 0 010 3zm5 1a1.5 1.5 0 110-3 1.5 1.5 0 010 3z",
    "award": "M12 2a6 6 0 100 12 6 6 0 000-12zM8.5 14.6L7 22l5-2.4L17 22l-1.5-7.4a8 8 0 01-7 0z",
    "target": "M12 2a10 10 0 100 20 10 10 0 000-20zm0 4a6 6 0 110 12 6 6 0 010-12zm0 3.5a2.5 2.5 0 100 5 2.5 2.5 0 000-5z",
    "smile": "M12 2a10 10 0 100 20 10 10 0 000-20zM8.5 9.5a1.5 1.5 0 113 0 1.5 1.5 0 01-3 0zm4 0a1.5 1.5 0 113 0 1.5 1.5 0 01-3 0zM12 18a5 5 0 01-4.6-3h9.2A5 5 0 0112 18z",
}

# Keyword -> glyph. Ordered longest-first at match time so "customer support"
# beats a bare "support". Every value must exist in GLYPHS.
_KEYWORDS: dict[str, str] = {
    "24/7": "clock", "hour": "clock", "fast": "bolt", "quick": "bolt", "speed": "bolt",
    "instant": "bolt", "same day": "bolt", "time": "clock", "schedule": "calendar",
    "book": "calendar", "appointment": "calendar", "open": "clock",
    "secure": "shield", "security": "shield", "safe": "shield", "privacy": "lock",
    "protect": "shield", "insur": "shield", "compliance": "lock", "encrypt": "lock",
    "data": "chart", "analytic": "chart", "report": "chart", "growth": "chart",
    "result": "chart", "performance": "chart", "roi": "chart", "revenue": "chart",
    "quality": "check", "guarantee": "check", "certified": "award", "accredited": "award",
    "award": "award", "expert": "award", "experience": "award", "trusted": "star",
    "rating": "star", "review": "star", "premium": "star", "best": "star",
    "care": "heart", "health": "heart", "wellbeing": "heart", "family": "heart",
    "love": "heart", "community": "users", "team": "users", "staff": "users",
    "people": "users", "partner": "users", "client": "users", "customer": "users",
    "member": "users", "child": "smile", "kid": "smile", "friendly": "smile",
    "play": "smile", "fun": "smile", "happy": "smile",
    "global": "globe", "worldwide": "globe", "international": "globe", "online": "globe",
    "web": "globe", "reach": "globe", "network": "globe",
    "custom": "gear", "flexible": "gear", "configur": "gear", "integrat": "gear",
    "automat": "gear", "process": "gear", "system": "gear", "tool": "wrench",
    "repair": "wrench", "maintenance": "wrench", "install": "wrench", "fix": "wrench",
    "service": "wrench", "support": "phone", "contact": "phone", "call": "phone",
    "email": "mail", "message": "mail", "newsletter": "mail",
    "location": "map-pin", "near": "map-pin", "local": "map-pin", "visit": "map-pin",
    "address": "map-pin", "deliver": "truck", "shipping": "truck", "logistics": "truck",
    "transport": "truck", "fleet": "truck",
    "design": "palette", "creative": "palette", "brand": "palette", "art": "palette",
    "learn": "book", "course": "book", "curriculum": "book", "training": "book",
    "education": "book", "programme": "book", "program": "book", "knowledge": "book",
    "eco": "leaf", "green": "leaf", "sustain": "leaf", "natural": "leaf",
    "organic": "leaf", "environment": "leaf",
    "innovat": "sparkle", "new": "sparkle", "modern": "sparkle", "ai": "sparkle",
    "smart": "sparkle", "advanced": "sparkle",
    "goal": "target", "focus": "target", "precision": "target", "strategy": "target",
    "mission": "target", "tailored": "target",
    # Stat labels are counts, not capabilities, so they miss the vocabulary
    # above ("Projects delivered" was landing on a delivery truck).
    # "projects" (8) deliberately outranks "deliver" (7) so "Projects
    # delivered" reads as work completed, not as a shipping company.
    "projects": "check", "project": "check", "completed": "check",
    "satisfaction": "smile", "year": "calendar", "since": "calendar",
    "student": "users", "pupil": "users", "graduate": "award", "enrol": "users",
    "square": "map-pin", "branch": "map-pin", "site": "map-pin", "region": "globe",
}

# Longest keywords first so a specific phrase wins over a substring of it.
_ORDERED_KEYWORDS: tuple[tuple[str, str], ...] = tuple(
    sorted(_KEYWORDS.items(), key=lambda kv: -len(kv[0]))
)

_WORD_RE = re.compile(r"[^a-z0-9/ ]+")


def icon_name_for(*texts: str | None) -> str | None:
    """The glyph a piece of copy suggests, or None when nothing matches.

    None is a real answer, not a failure: the caller treats icons as
    all-or-nothing across a section, so one unmatched item means the section
    shows no icons rather than an inconsistent grid.
    """
    blob = _WORD_RE.sub(" ", " ".join(t.lower() for t in texts if t))
    if not blob.strip():
        return None
    for keyword, glyph in _ORDERED_KEYWORDS:
        if keyword in blob:
            return glyph
    return None


def icon_data_url(name: str, fill_hex: str = "#0f172a") -> str | None:
    """A 24x24 single-colour glyph as an `image/svg+xml` data URI.

    URL-encoded rather than base64 so the payload stays small and greppable in a
    pushed document. Unknown name → None, so a bad value drops the node instead
    of rendering a broken image.
    """
    path = GLYPHS.get(name)
    if not path:
        return None
    svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' "
        f"fill='{fill_hex}'><path d='{path}'/></svg>"
    )
    return "data:image/svg+xml," + quote(svg, safe="")


def icons_for_items(items: list[dict[str, str | None]]) -> list[str] | None:
    """One glyph name per item, or None unless EVERY item resolves one.

    A half-iconed grid reads as a rendering fault rather than a design, so the
    section commits to icons only when the whole set works — the same
    all-or-nothing shape as `_most_items_have_images`.
    """
    if len(items) < 2:
        return None
    names = [icon_name_for(*(i.get(k) for k in ("title", "label", "description"))) for i in items]
    if not all(names):
        return None
    # Every tile showing the SAME glyph carries no information and reads as a
    # copy-paste error. Partial repetition is fine — two of five tiles being
    # about people genuinely is two of five tiles about people.
    if len(set(names)) < 2:
        return None
    return names  # type: ignore[return-value]
