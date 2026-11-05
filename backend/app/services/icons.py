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

Paths are 24x24, single-colour, stroke-free, filled with the default nonzero
rule: a hole is a subpath wound the other way from the shape around it. The
first set was drawn from the MIT-licensed Lucide set's geometry; the rest are
simple shapes drawn for this set. Keep them simple: a tile icon renders at
~36px, where detail is lost anyway.

The builder MIRRORS this set (`builder/src/lib/tile-icons.ts`) for its icon
picker, and its test runs this module and compares glyphs, keywords and
`icon_data_url` output byte for byte. Add a glyph here, then there.
"""

from __future__ import annotations

import re
from urllib.parse import quote

# name -> SVG path data on a 24x24 viewBox.
GLYPHS: dict[str, str] = {
    "clock": "M12 2a10 10 0 100 20 10 10 0 000-20zM13 7v5l4 2.3-1 1.7-5-3V7h2z",
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
    # Drawn for this set, for the businesses the generator builds most:
    # schools, clinics, shops, food, travel and services.
    "chat": "M4 3h16a2 2 0 012 2v11a2 2 0 01-2 2H9.5L4 22v-4a2 2 0 01-2-2V5a2 2 0 012-2z"
            "M7 10.5a1.5 1.5 0 003 0 1.5 1.5 0 00-3 0zm3.5 0a1.5 1.5 0 003 0 1.5 1.5 0 00-3 0z"
            "m3.5 0a1.5 1.5 0 003 0 1.5 1.5 0 00-3 0z",
    "home": "M12 3l9.5 8.5H19V21h-5v-6h-4v6H5v-9.5H2.5L12 3z",
    "building": "M4 21V3h11v6h5v12H4zM7 6v2h2V6H7zm4 0v2h2V6h-2zm-4 4v2h2v-2H7zm4 0v2h2v-2h-2zm-4 4v2h2v-2H7z"
                "m4 0v2h2v-2h-2zm5-2v2h2v-2h-2zm0 4v2h2v-2h-2z",
    "graduation-cap": "M12 3L1 9l11 6 9-4.9V17h2V9L12 3zM5 13.2V17c0 1.7 3.1 4 7 4s7-2.3 7-4v-3.8L12 17l-7-3.8z",
    "lightbulb": "M12 2a7 7 0 00-4 12.7V17h8v-2.3A7 7 0 0012 2z"
                 "M9 19h6v1.5a1.5 1.5 0 01-1.5 1.5h-3A1.5 1.5 0 019 20.5V19z",
    "rocket": "M12 1.5c3.4 2.3 5.3 6.4 5 11l2.5 3.5V21l-3.5-2.5H8L4.5 21v-5l2.5-3.5c-.3-4.6 1.6-8.7 5-11z"
              "M10 10a2 2 0 004 0 2 2 0 00-4 0zM10 19.5h4L12 23l-2-3.5z",
    "trophy": "M7 3h10v2h4v3a4 4 0 01-4 4h-.4A5 5 0 0113 15.9V19h3v2H8v-2h3v-3.1A5 5 0 017.4 12H7a4 4 0"
              " 01-4-4V5h4V3zM7 7H5v1a2 2 0 002 2V7zm10 0v3a2 2 0 002-2V7h-2z",
    "gift": "M3 8h8v4H3V8zm10 0h8v4h-8V8zM5 13h6v8H5v-8zm8 0h6v8h-6v-8z"
            "M12 7.5C10.3 4.3 6.5 3.9 6.5 6c0 1.8 3 1.8 5.5 1.5z"
            "m0 0c1.7-3.2 5.5-3.6 5.5-1.5 0 1.8-3 1.8-5.5 1.5z",
    "camera": "M9 4h6l1.5 2H20a2 2 0 012 2v10a2 2 0 01-2 2H4a2 2 0 01-2-2V8a2 2 0 012-2h3.5L9 4z"
              "M7.8 13a4.2 4.2 0 008.4 0 4.2 4.2 0 00-8.4 0zm1.8 0a2.4 2.4 0 114.8 0 2.4 2.4 0 01-4.8 0z",
    "music": "M9 3.5L21 1.5v14a3 3 0 11-2-2.8V5.8l-8 1.4v10.3a3 3 0 11-2-2.8V3.5z",
    "search": "M3 10a7 7 0 1014 0 7 7 0 10-14 0zm2 0a5 5 0 0110 0 5 5 0 01-10 0z"
              "M14.6 16l1.4-1.4 6 6-1.4 1.4-6-6z",
    "document": "M5 2h9l5 5v15H5V2zM8 7v1.5h4V7H8zm0 4v1.5h8V11H8zm0 4v1.5h8V15H8z",
    "credit-card": "M4 4h16a2 2 0 012 2v2H2V6a2 2 0 012-2zM2 10h20v8a2 2 0 01-2 2H4a2 2 0 01-2-2v-8z"
                   "m3 5v2h5v-2H5z",
    "cart": "M1 3h3.2l.8 2H22l-3 9H7.6l-.8 2H20v2H4l2-5L3 5H1V3z"
            "M6.5 20.5a1.5 1.5 0 003 0 1.5 1.5 0 00-3 0zm10 0a1.5 1.5 0 003 0 1.5 1.5 0 00-3 0z",
    "tag": "M2 2h9l11 11-9 9L2 11V2zM5 6.5a1.5 1.5 0 003 0 1.5 1.5 0 00-3 0z",
    "cash": "M1 6h22v12H1V6zm8 6a3 3 0 006 0 3 3 0 00-6 0zM3 8v2a2 2 0 002-2H3zm16 0a2 2 0 002 2V8h-2z"
            "M3 14v2h2a2 2 0 00-2-2zm16 2h2v-2a2 2 0 00-2 2z",
    "laptop": "M4 4h16v11H4V4zm2 2v7h12V6H6zM1 17h22v1.5a1.5 1.5 0 01-1.5 1.5h-19A1.5 1.5 0 011 18.5V17z",
    "mobile": "M7 1h10a2 2 0 012 2v18a2 2 0 01-2 2H7a2 2 0 01-2-2V3a2 2 0 012-2zm0 3v14h10V4H7z",
    "play": "M2 12a10 10 0 1020 0 10 10 0 10-20 0zm8-4l6 4-6 4V8z",
    "coffee": "M3 8h14v5a6 6 0 01-6 6H9a6 6 0 01-6-6V8z"
              "m14 1h1.5a3.5 3.5 0 010 7h-1.9l.4-2h1.5a1.5 1.5 0 000-3H17V9zM2 20.5h16V22H2v-1.5z",
    "utensils": "M5 2h1v6h1V2h1v6h1V2h1v7a3 3 0 01-2 2.8V22H7V11.8A3 3 0 015 9V2z"
                "m11 0c2.5 1 4 4 4 8v3h-2v9h-2V2z",
    "bed": "M2 5h2v8h18v6h-2v-2H4v2H2V5zm7 4a2 2 0 10-4 0 2 2 0 004 0zm1-2h9a3 3 0 013 3v2H10V7z",
    "car": "M5 6h14l2.5 6H22v7h-2.5v1.5a1.5 1.5 0 01-3 0V19h-9v1.5a1.5 1.5 0 01-3 0V19H2v-7h.5L5 6z"
           "M6.4 8l-1.6 4h14.4l-1.6-4H6.4zM4.8 15.5a1.2 1.2 0 002.4 0 1.2 1.2 0 00-2.4 0z"
           "m12 0a1.2 1.2 0 002.4 0 1.2 1.2 0 00-2.4 0z",
    "plane": "M21 16v-2l-8-5V3.5a1.5 1.5 0 00-3 0V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13"
             " 19v-5.5l8 2.5z",
    "bus": "M5 2h14a2 2 0 012 2v14h-1v2.5a1.5 1.5 0 01-3 0V18H7v2.5a1.5 1.5 0 01-3 0V18H3V4a2 2 0 012-2z"
           "M5 5v6h14V5H5zM5.8 15a1.2 1.2 0 002.4 0 1.2 1.2 0 00-2.4 0z"
           "m10 0a1.2 1.2 0 002.4 0 1.2 1.2 0 00-2.4 0z",
    "sun": "M7.5 12a4.5 4.5 0 109 0 4.5 4.5 0 10-9 0zM11 1h2v3h-2V1zm0 19h2v3h-2v-3zM1 11h3v2H1v-2z"
           "m19 0h3v2h-3v-2zM4.2 5.6l1.4-1.4 2.1 2.1-1.4 1.4-2.1-2.1z"
           "m12.1 12.1l1.4-1.4 2.1 2.1-1.4 1.4-2.1-2.1zM18.4 4.2l1.4 1.4-2.1 2.1-1.4-1.4 2.1-2.1z"
           "M6.3 16.3l1.4 1.4-2.1 2.1-1.4-1.4 2.1-2.1z",
    "moon": "M20 14.5A8.5 8.5 0 019.5 4a8.5 8.5 0 1010.5 10.5z",
    "drop": "M12 2s7 7.6 7 12.5a7 7 0 01-14 0C5 9.6 12 2 12 2z",
    "paw": "M12 12c3 0 6 3.5 6 6.5 0 1.9-1.5 2.5-3 2.5-1.2 0-2-.6-3-.6s-1.8.6-3 .6c-1.5 0-3-.6-3-2.5C6"
           " 15.5 9 12 12 12zM3 10a2 2 0 104 0 2 2 0 10-4 0zm3.8-4a2.2 2.2 0 104.4 0 2.2 2.2 0 10-4.4 0z"
           "m6 0a2.2 2.2 0 104.4 0 2.2 2.2 0 10-4.4 0zM17 10a2 2 0 104 0 2 2 0 10-4 0z",
    "medical": "M9 3h6v6h6v6h-6v6H9v-6H3V9h6V3z",
    "dumbbell": "M1 10h2V8h3v8H3v-2H1v-4zm17-2h3v2h2v4h-2v2h-3V8zM7 6h3v12H7V6zm7 0h3v12h-3V6z"
                "m-4 5h4v2h-4v-2z",
    "flag": "M4 2h2v20H4V2zm3 1h11l-2.5 4.5L18 12H7V3z",
    "key": "M2 12a5 5 0 1010 0 5 5 0 10-10 0zm3 0a2 2 0 014 0 2 2 0 01-4 0z"
           "m6-1h11v2h-2v3h-2v-3h-2v2h-2v-2h-3v-2z",
    "cloud": "M7 19a5 5 0 01-.9-9.9A6 6 0 0117.7 8 4.5 4.5 0 0118 19H7z",
    "thumbs-up": "M2 10h4v11H2V10z"
                 "m6 11V10l5-8c1.7 0 2.8 1.4 2.4 3L14.6 9H20a2 2 0 012 2.3l-1.4 8A2 2 0 0118.6 21H8z",
    "bell": "M12 2a1.5 1.5 0 011.5 1.6A6 6 0 0118 9.5V15l2 3v1H4v-1l2-3V9.5a6 6 0 014.5-5.9A1.5 1.5 0"
            " 0112 2zM9.5 20h5a2.5 2.5 0 01-5 0z",
    "pencil": "M3 17.3V21h3.7L17.8 9.9l-3.7-3.7L3 17.3z"
              "M20.7 7a1 1 0 000-1.4l-2.3-2.3a1 1 0 00-1.4 0l-1.8 1.8 3.7 3.7L20.7 7z",
    "store": "M3 3h18l1 5a3 3 0 01-3 3 3 3 0 01-3.5-1.4A3 3 0 0112 11a3 3 0 01-3.5-1.4A3 3 0 015 11a3 3 0"
             " 01-3-3l1-5z"
             "M4 12.6V21h6v-5h4v5h6v-8.4a4.9 4.9 0 01-4.5-.9 4.9 4.9 0 01-3.5 1.3 4.9 4.9 0 01-3.5-1.3"
             " 4.9 4.9 0 01-4.5.9z",
    "briefcase": "M9 2h6a2 2 0 012 2v2h4a1 1 0 011 1v12a2 2 0 01-2 2H4a2 2 0 01-2-2V7a1 1 0 011-1h4V4a2 2 0"
                 " 012-2zm0 2v2h6V4H9z",
    "scissors": "M3 6a3 3 0 106 0 3 3 0 10-6 0zm1.5 0a1.5 1.5 0 013 0 1.5 1.5 0 01-3 0z"
                "M3 18a3 3 0 106 0 3 3 0 10-6 0zm1.5 0a1.5 1.5 0 013 0 1.5 1.5 0 01-3 0z"
                "M8.3 7.6L21 19v1.5h-1.5L7.6 9.4l.7-1.8zM8.3 16.4L21 5V3.5h-1.5L7.6 14.6l.7 1.8z",
    "eye": "M12 5C6.5 5 2.7 8.4 1 12c1.7 3.6 5.5 7 11 7s9.3-3.4 11-7c-1.7-3.6-5.5-7-11-7z"
           "M8 12a4 4 0 118 0 4 4 0 11-8 0zm2 0a2 2 0 004 0 2 2 0 00-4 0z",
    "crown": "M2 7l5 4 5-7 5 7 5-4-2 12H4L2 7zm2 13h16v2H4v-2z",
    "diamond": "M6 3h12l4 6-10 12L2 9l4-6z",
    "megaphone": "M3 10h3l10-6v16L6 14H3v-4zm15-1a3 3 0 010 6V9zM7 15h3l1 5H8l-1-5z",
    "percent": "M19 3.5L20.5 5 5 20.5 3.5 19 19 3.5zM4.5 7a2.5 2.5 0 105 0 2.5 2.5 0 10-5 0z"
               "m10 10a2.5 2.5 0 105 0 2.5 2.5 0 10-5 0z",
    "cake": "M11 2h2v4h-2V2z"
            "M4 8h16a1 1 0 011 1v4c-1.5 0-1.5 1.5-3 1.5S16.5 13 15 13s-1.5 1.5-3 1.5S10.5 13 9 13s-1.5"
            " 1.5-3 1.5S4.5 13 3 13V9a1 1 0 011-1z"
            "M3 15.3c1.6.5 2.4-.8 3-.8s1.5 1.5 3 1.5 1.5-1.5 3-1.5 1.5 1.5 3 1.5 1.5-1.5 3-1.5c.6 0 1.4"
            " 1.3 3 .8V22H3v-6.7z",
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
    # The glyphs drawn for this set. Matching is a plain substring test, so no
    # keyword here may hide inside an unrelated word: no "cat" (eduCATion),
    # "rate" (corpoRATE), "key" (monKEY), "rental" (paRENTAL), "hair" (cHAIR),
    # "idea" (IDEAl), "drive" (data-DRIVEn), "app" (APPointment), "fee"
    # (cofFEE), "eat" (grEAT), "bus" (BUSiness), "sing" (uSING), "gem"
    # (manaGEMent), "rest" (inteREST), "wash" (dishWASHer) or "cut" (exeCUTe). Keywords above keep
    # their glyph; a longer phrase here only adds a better match.
    "chat": "chat", "whatsapp": "chat", "enquir": "chat", "inquir": "chat",
    "conversation": "chat", "consult": "chat", "advice": "chat", "faq": "chat",
    "question": "chat", "answer": "chat",
    "home": "home", "house": "home", "residential": "home", "property": "home",
    "real estate": "home", "warehouse": "truck",
    "building": "building", "office": "building", "campus": "building",
    "facilit": "building", "headquarter": "building", "classroom": "building",
    "graduation": "graduation-cap", "school": "graduation-cap", "academy": "graduation-cap",
    "university": "graduation-cap", "college": "graduation-cap", "degree": "graduation-cap",
    "scholar": "graduation-cap", "diploma": "graduation-cap", "tuition": "graduation-cap",
    "ideas": "lightbulb", "insight": "lightbulb", "inspir": "lightbulb",
    "solution": "lightbulb", "tips": "lightbulb",
    "launch": "rocket", "startup": "rocket", "start up": "rocket", "boost": "rocket",
    "accelerat": "rocket", "kickstart": "rocket",
    "trophy": "trophy", "winning": "trophy", "champion": "trophy", "achievement": "trophy",
    "competition": "trophy", "success": "trophy",
    "gift": "gift", "reward": "gift", "bonus": "gift", "voucher": "gift",
    "photo": "camera", "camera": "camera", "gallery": "camera", "picture": "camera",
    "memories": "camera",
    "music": "music", "song": "music", "piano": "music", "guitar": "music", "choir": "music",
    "concert": "music", "melody": "music", "dance": "music",
    "search": "search", "find": "search", "discover": "search", "explore": "search",
    "browse": "search",
    "document": "document", "paperwork": "document", "contract": "document",
    "contractor": "wrench", "policy": "document", "policies": "document",
    "invoice": "document", "certificate": "document", "registration": "document",
    "payment": "credit-card", "card": "credit-card", "checkout": "credit-card",
    "billing": "credit-card", "credit": "credit-card", "instalment": "credit-card",
    "installment": "credit-card",
    "cart": "cart", "purchase": "cart", "buy": "cart", "ecommerce": "cart",
    "e commerce": "cart", "online order": "cart",
    "price": "tag", "pricing": "tag", "deals": "tag", "sale": "tag", "offer": "tag",
    "affordable": "tag", "fees": "tag", "cost": "tag",
    "money": "cash", "cash": "cash", "saving": "cash", "financ": "cash", "loan": "cash",
    "fund": "cash", "invest": "cash", "salary": "cash", "budget": "cash",
    "laptop": "laptop", "computer": "laptop", "software": "laptop", "digital": "laptop",
    "tech": "laptop", "coding": "laptop", "programming": "laptop",
    "mobile": "mobile", "smartphone": "mobile", "sms": "mobile", "text message": "mobile",
    "android": "mobile", "app store": "mobile",
    "video": "play", "watch": "play", "streaming": "play", "live stream": "play",
    "youtube": "play", "film": "play", "movie": "play", "tutorial": "play",
    "coffee": "coffee", "breakfast": "coffee", "brunch": "coffee", "barista": "coffee",
    "espresso": "coffee",
    "meal": "utensils", "food": "utensils", "dining": "utensils", "restaurant": "utensils",
    "lunch": "utensils", "menu": "utensils", "cook": "utensils", "kitchen": "utensils",
    "catering": "utensils", "nutrition": "utensils",
    "sleep": "bed", "hotel": "bed", "accommodation": "bed", "bedroom": "bed",
    "overnight": "bed", "resort": "bed",
    "parking": "car", "driving": "car", "vehicle": "car", "automotive": "car",
    "taxi": "car", "car park": "car", "pickup": "car", "pick up": "car", "drop off": "car",
    "travel": "plane", "flight": "plane", "tours": "plane", "sightseeing": "plane",
    "trips": "plane", "road trip": "plane", "holiday": "plane", "abroad": "plane",
    "airport": "plane", "vacation": "plane",
    "school bus": "bus", "shuttle": "bus", "transit": "bus", "commute": "bus",
    "field trip": "bus",
    "summer": "sun", "outdoor": "sun", "solar": "sun", "daylight": "sun", "morning": "sun",
    "sunshine": "sun",
    "night": "moon", "evening": "moon", "after hours": "moon",
    # Opening times stay a clock, though "business" and "office" now have glyphs.
    "business hours": "clock", "opening hours": "clock", "office hours": "clock",
    "water": "drop", "clean": "drop", "hygien": "drop", "washing": "drop",
    "laundry": "drop", "plumb": "drop",
    "hydrat": "drop", "pool": "drop", "swim": "drop",
    "pets": "paw", "dog": "paw", "puppy": "paw", "kitten": "paw", "cats": "paw",
    "animal": "paw", "veterinar": "paw", "grooming": "paw",
    "medical": "medical", "clinic": "medical", "doctor": "medical", "hospital": "medical",
    "nurse": "medical", "treatment": "medical", "therapy": "medical", "dental": "medical",
    "pharma": "medical", "first aid": "medical",
    "fitness": "dumbbell", "gym": "dumbbell", "workout": "dumbbell", "exercise": "dumbbell",
    "sport": "dumbbell", "strength": "dumbbell", "physical": "dumbbell",
    "milestone": "flag", "start": "flag", "begin": "flag", "journey": "flag",
    "access": "key", "unlock": "key", "login": "key", "rentals": "key", "for rent": "key",
    "tenant": "key", "landlord": "key", "password": "key",
    "cloud": "cloud", "backup": "cloud", "storage": "cloud", "sync": "cloud", "saas": "cloud",
    "hosting": "cloud",
    "recommend": "thumbs-up", "approve": "thumbs-up", "testimonial": "thumbs-up",
    "feedback": "thumbs-up", "positive": "thumbs-up",
    "notification": "bell", "alert": "bell", "remind": "bell", "notify": "bell",
    "update": "bell",
    "write": "pencil", "writing": "pencil", "edit": "pencil", "draw": "pencil",
    "homework": "pencil", "note": "pencil", "blog": "pencil", "content": "pencil",
    "copy": "pencil",
    "store": "store", "shop": "store", "retail": "store", "boutique": "store",
    "outlet": "store", "merchant": "store", "workshop": "wrench",
    "business": "briefcase", "career": "briefcase", "job": "briefcase",
    "professional": "briefcase", "recruit": "briefcase", "hiring": "briefcase",
    "employ": "briefcase", "corporate": "briefcase", "consultancy": "briefcase",
    "interview": "briefcase",
    "hairdress": "scissors", "hairstyl": "scissors", "haircut": "scissors",
    "salon": "scissors", "barber": "scissors", "beauty": "scissors", "tailor": "scissors",
    "vision": "eye", "view": "eye", "visib": "eye", "transparen": "eye",
    "vip": "crown", "exclusive": "crown", "luxury": "crown", "royal": "crown",
    "leader": "crown", "elite": "crown",
    "diamond": "diamond", "jewel": "diamond", "precious": "diamond", "valuable": "diamond",
    "marketing": "megaphone", "announce": "megaphone", "promot": "megaphone",
    "advert": "megaphone", "campaign": "megaphone", "social media": "megaphone",
    "publicity": "megaphone",
    "discount": "percent", "percent": "percent", "promo code": "percent",
    "birthday": "cake", "cake": "cake", "party": "cake", "celebrat": "cake",
    "bakery": "cake", "dessert": "cake",
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
