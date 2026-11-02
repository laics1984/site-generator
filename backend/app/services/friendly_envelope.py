"""The friendly envelope — what a children's site never looks like.

The design-identity work took the LOOK away from childcare, and rightly: one
pinned look shipped 14 of 18 kindergartens in one palette. What it kept is that
an industry still owns legibility and what its audience reads as safe (CLAUDE.md
"No industry owns a look"). For a parent choosing a kindergarten that is a
light, warm page: a black footer, a photo under a near-black scrim or a charcoal
palette reads cold, whatever scheme produced it. Nothing stated that, so seven
independent paths reached it — the logo's light/dark vote, the AI palette, the
footer's `secondary` band, the header's logo band, full-bleed photo heroes, the
rhythm's dark band and the CTA pool. Measured: 28 of 28 stress builds over the
childcare schemes shipped at least one dark surface.

This module is the one statement of the rule, in two halves:

* **Composition** — owned by the industry, whatever the colours: copy never
  sits on a darkened photo or a dark brand gradient (`refuses_template`, read
  from the catalog's own structure), no chrome that needs one
  (`refused_headers`), and a hero's picture shows children (`hero_query`,
  `backdrop_query`).
* **Colour** — the industry on a LIGHT build (`holds`): the canvas is light
  (`theme.resolve_color_scheme`), an AI palette that would make it dark or
  hueless is refused (`keeps`), and every surface is a pastel (`is_pastel`) —
  chrome, the opening and every band wear the brand's own pastel (`pastel`),
  the ask a deeper one of the same hue (`deep_pastel`). An
  explicit Dark choice by the operator is the one way out, and it turns off
  this half only.

Every value derives from THIS site's palette (`color_system`), so schools still
differ: the envelope says what a friendly site never is, never which look it is.
Kept to `color_system` and the manifest's vocabulary so the theme, the chrome
and the selectors can all ask it without an import cycle.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.models.design_manifest import SELF_CHROME_HEADERS
from app.services.color_system import BAND_MIN_SOURCE_CHROMA, derive, from_oklch, oklch

if TYPE_CHECKING:
    from app.models.brand import ThemeTokens

# IndustryCategory values whose audience is children and the parents choosing
# for them. A new one is a data edit here; every rule below follows.
FRIENDLY_INDUSTRIES: frozenset[str] = frozenset({"childcare"})

# A gradient stop that IS the palette's dark neutral — not a mix of it (a
# colour-mix argument carries a percentage before its comma). `hero-gradient`
# and `cta-gradient` run their white copy over exactly this.
_DARK_GRADIENT_STOP = re.compile(r"[(,]\s*var\(--builder-color-secondary\b[^()]*\)\s*[,)]")

# Words that put a child in a stock photograph. A setting ("montessori
# classroom", "kindergarten building") is not one: it can come back empty.
_SHOWS_CHILDREN = re.compile(
    r"\b(child|children|kids?|toddlers?|preschoolers?|bab(?:y|ies)|boys?|girls?|pupils?)\b",
    re.IGNORECASE,
)


def is_friendly(industry: str | None) -> bool:
    """Whether `industry` is one whose sites wear the envelope."""
    return (industry or "").strip().lower() in FRIENDLY_INDUSTRIES


def holds(theme: ThemeTokens) -> bool:
    """Whether the colour half applies: a friendly industry on a light build.
    Only an explicit Dark choice makes a friendly site dark, and that choice
    owns its colours."""
    return is_friendly(theme.industry) and theme.color_scheme == "light"


# --- colour -------------------------------------------------------------------

# What a friendly surface is: light and soft, in OKLCH. Dark fails the
# lightness, and so does a vivid brand band (a #db2777 CTA) whose dark ink
# technically reads; a loud yellow (L 0.86, C 0.17) fails the chroma. Every
# band role (L 0.955, C <= 0.045), the page, the surface and the deep pastel
# below pass. Controls (a brand button) are not surfaces and are never asked.
PASTEL_MIN_LIGHTNESS = 0.85
PASTEL_MAX_CHROMA = 0.09
# The ask's band: deeper than every other pastel so it stands apart. Measured
# over eleven brand hues: L 0.87 is >= 1.27:1 off band-1, the page and the
# surface (the CTA floor is 1.25), carries the dark ink at 12:1+ and keeps
# the brand button >= 3.1:1 — at 0.86 that button fell to 3.0 and the ink
# pass repainted it near-black.
DEEP_PASTEL_LIGHTNESS = 0.87
DEEP_PASTEL_MAX_CHROMA = 0.08


def is_pastel(surface_hex: str) -> bool:
    """Whether a friendly site may paint `surface_hex` behind copy or chrome."""
    light, chroma, _ = oklch(surface_hex)
    return light >= PASTEL_MIN_LIGHTNESS and chroma <= PASTEL_MAX_CHROMA


def pastel(theme: ThemeTokens) -> str:
    """The site's friendly band: the colour system's `band-1`, a pastel of the
    brand's own leading hue (a quiet wash for a brand with none). A role, so it
    follows a palette edit in every renderer."""
    return derive(theme.palette.model_dump())["band-1"]


def band_sequence(theme: ThemeTokens, count: int, *, two_hue: bool) -> list[str]:
    """The fill of each of a page's `count` sections, in order.

    One hue in three steps: the opening on the brand tint (`band-1`, the
    header's and footer's band too), then the page colour and the tint in
    turn; the ask's deep pastel is the CTA pass's (`deep_pastel`). A scheme
    that speaks in colour (`two_hue`: playroom, soft-nursery) alternates its
    colour bands between the tint and ONE companion — `band-2`, the accent's
    pastel — never two colours side by side. It replaces a cycle through all
    four band roles, whose hue followed the section's position, and a mix of
    tints 1.05:1 apart (`band-1` against `surface`) that read as noise."""
    roles = derive(theme.palette.model_dump())
    colours = [roles["band-1"], roles["band-2"]] if two_hue else [roles["band-1"]]
    page = theme.palette.background
    return [
        colours[0] if index == 0 else page if index % 2 else colours[(index // 2) % len(colours)]
        for index in range(count)
    ]


def deep_pastel(theme: ThemeTokens) -> str:
    """The band a friendly site's ask stands apart on: the brand's own hue,
    deeper than every other pastel on the page."""
    _, chroma, hue = oklch(theme.palette.primary)
    return from_oklch(DEEP_PASTEL_LIGHTNESS, min(chroma, DEEP_PASTEL_MAX_CHROMA), hue)


def chrome_band(theme: ThemeTokens) -> str | None:
    """The band the header and footer sit on when the envelope holds; None
    defers to the archetype's own band."""
    return pastel(theme) if holds(theme) else None


def keeps(candidate: ThemeTokens, current: ThemeTokens) -> bool:
    """Whether a proposed palette (the AI's) keeps a friendly site friendly:
    still light, and led by a colour with a hue. A refused proposal is
    discarded whole — never patched — and the concept's own palette stands."""
    if not holds(current):
        return True
    return holds(candidate) and oklch(candidate.palette.primary)[1] >= BAND_MIN_SOURCE_CHROMA


# --- composition ----------------------------------------------------------------


def _dark_by_design(template: dict[str, Any]) -> bool:
    """A template built to carry light copy on a dark backdrop: its root takes
    a photograph as its background (a scrim is how its copy reads), or a
    gradient through the palette's dark neutral."""
    root = template.get("tree") or {}
    if (root.get("$styleSlot") or {}).get("property") in ("background", "backgroundImage"):
        return True
    fill = str((root.get("styles") or {}).get("background") or "")
    return "gradient(" in fill and bool(_DARK_GRADIENT_STOP.search(fill))


def refuses_template(industry: str | None, template: dict[str, Any] | None) -> bool:
    """Whether a friendly site may not use this catalog template."""
    return template is not None and is_friendly(industry) and _dark_by_design(template)


def refused_headers(industry: str | None) -> frozenset[str]:
    """Header archetypes a friendly site never wears: a self-chrome bar floats
    over a full-bleed photo hero, which the envelope refuses."""
    return SELF_CHROME_HEADERS if is_friendly(industry) else frozenset()


def hero_query(industry: str | None, query: str | None) -> str | None:
    """A hero's stock query, made to show children on a friendly site."""
    if not (query or "").strip() or not is_friendly(industry) or _SHOWS_CHILDREN.search(query or ""):
        return query
    return f"children {query.strip()}"  # type: ignore[union-attr]


def backdrop_query(industry: str | None, family: str) -> str | None:
    """The stock query for a hero's abstract backdrop on a friendly site, in
    the brand's colour family; None defers to the generic one. Measured on
    Pexels across colour families: "wooden toys flat lay" returns blocks,
    puzzles and Montessori materials on pastel grounds, where "kids toys
    pattern" also returned toy guns and a bare "gradient mesh" a dusk sky."""
    return f"{family} pastel wooden toys flat lay" if is_friendly(industry) else None
