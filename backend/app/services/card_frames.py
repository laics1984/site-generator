"""A card stands off the band it sits on.

One home for two questions every pass that touches a card asks: **which nodes
are cards**, and **does this card read as a card** on its band. The scheme's
card treatment (`schema_builder._walk_modernize`), the floor pass that runs on
the finished tree (`separate_cards`) and the design-quality regression net
(`design_quality._check_card_bands`) all read it, so they cannot disagree.

**A card is found by its structure, not its name.** The treatment pass used to
key on a node name ending in "card", and the catalog names its cells
"Step Cell", "Member Cell", "Tier Cell", "Stat Cell", "FAQ Item" — so every one
of them shipped the catalog's light-page styling into whatever palette the site
had, the scheme's treatment never reached them, and nothing judged them. A
named card still counts; so does any framed container that REPEATS (two or more
siblings of one name), which is what a `$repeat` fan-out leaves behind.

**Separation is measured in OKLab ΔE, not in contrast ratio.** WCAG's ratio is
for text; for two large surfaces it compresses near black and rejects the
standard light card outright (Tailwind's white card on gray-50 is 1.04:1 with a
1.18:1 border, and reads fine). Measured in ΔE: that light card is 0.015 with a
0.057 border; Tailwind's dark card (gray-800 on gray-900) is 0.068; the dark
"How we work" grid that prompted this was 0.038 — a fill that is neither the
band's own colour nor a step off it.

**The two sides of the page separate a card differently.** On a light band a
hairline or a shadow does the work and the fill may sit close (`LIGHT_FILL_MIN`
or a frame). On a dark band a shadow is invisible and a hairline cannot rescue a
fill that almost matches — that near miss is exactly what reads as "the same
colour" — so the FILL must step off (`DARK_FILL_MIN`, the elevation model dark
UI uses), unless the card deliberately wears the band's own colour and an
outline that reads. A card with no fill (the `flat` / `outline` treatments) is
not judged: dissolving into the band is what it asked for.

The remedy keeps the card's composition: on a dark band its fill moves away
from the band along its own hue (never across the light/dark line, so the ink
the copy already wears stays right); on a light band its frame is restated as a
hairline that reads. A palette role is written as its token whenever one is the
answer, so the card follows a palette edit in the builder.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass

from app.models.brand import ThemeTokens
from app.models.builder_schema import BuilderElement

# OKLab ΔE floors (see the module docstring for the measurements behind them).
LIGHT_FILL_MIN = 0.03
DARK_FILL_MIN = 0.06
RULE_MIN = 0.04
# Under this a fill IS the band's colour: a deliberate same-colour card.
SAME_FILL = 0.005

_CARD_TYPES = frozenset({"container", "2Col", "3Col"})
_FRAME_KEYS = ("borderRadius", "border", "backgroundColor")
_BORDER_COLOR = re.compile(r"(rgba?\([^)]*\)|#[0-9a-fA-F]{3,6}\b|var\(.+\)|color-mix\(.+\))\s*$")
# Roles a lifted fill may be written as, so it follows a palette edit.
_FILL_ROLES = ("surface-elevated", "surface", "background", "surface-alternate",
               "surface-muted", "surface-inverse")
_LIFT_STEP = 0.005


def delta_e(a: str, b: str) -> float:
    """Euclidean distance in OKLab — how far apart two surfaces look."""
    from app.services.color_system import oklch

    (la, ca, ha), (lb, cb, hb) = oklch(a), oklch(b)
    return math.dist((la, ca * math.cos(ha), ca * math.sin(ha)),
                     (lb, cb * math.cos(hb), cb * math.sin(hb)))


# --- which nodes are cards ---------------------------------------------------


def _children(node: BuilderElement) -> list[BuilderElement]:
    content = node.content
    return [c for c in content if isinstance(c, BuilderElement)] if isinstance(content, list) else []


def is_named_card(node: BuilderElement) -> bool:
    """A styled container that names itself a card. Catalogue cards nest a bare
    wrapper "*Card" around the real styled one, so a frame is required."""
    styles = node.styles or {}
    return (
        node.type in _CARD_TYPES
        and (node.name or "").strip().lower().endswith("card")
        and any(k in styles for k in _FRAME_KEYS)
    )


def _repeated_frame(node: BuilderElement) -> bool:
    """A rounded box with a fill or a border of its own, and no photograph — a
    photo tile's surface is its picture, not a card's."""
    from app.services.section_content import _has_real_photo

    styles = node.styles or {}
    fill = str(styles.get("backgroundColor") or "").strip().lower()
    border = str(styles.get("border") or "").strip().lower()
    return (
        bool(styles.get("borderRadius"))
        and (fill not in ("", "transparent", "none") or border not in ("", "none", "0"))
        and not _has_real_photo(styles)
    )


def card_children(node: BuilderElement) -> list[BuilderElement]:
    """The children of `node` that are cards: named ones, and framed
    containers that repeat (two or more siblings of one name)."""
    kids = _children(node)
    names = Counter((c.name or "").strip() for c in kids if c.type in _CARD_TYPES)
    return [
        c for c in kids
        if is_named_card(c)
        or (c.type in _CARD_TYPES and (c.name or "").strip()
            and names[(c.name or "").strip()] >= 2 and _repeated_frame(c))
    ]


# --- does a card read as one -------------------------------------------------


@dataclass(frozen=True)
class CardFrame:
    """How one card presents against the band behind it."""

    band: str
    fill: str
    dark: bool  # the band takes the palette's light ink
    fill_delta: float
    rule_delta: float  # 0 with no readable border
    shadow: bool

    @property
    def separates(self) -> bool:
        if self.dark:
            return self.fill_delta >= DARK_FILL_MIN or (
                self.fill_delta < SAME_FILL and self.rule_delta >= RULE_MIN
            )
        return self.fill_delta >= LIGHT_FILL_MIN or self.rule_delta >= RULE_MIN or self.shadow


def _rule_hex(border: object, below: tuple[int, int, int], theme: ThemeTokens) -> str | None:
    from app.services.section_content import _composite, _parse_color, _to_hex

    if not isinstance(border, str):
        return None
    value = border.strip()
    if not value or value.lower() in ("none", "0") or re.match(r"0(px)?\b", value):
        return None
    match = _BORDER_COLOR.search(value)
    parsed = _parse_color(match.group(1), theme) if match else None
    return _to_hex(_composite(parsed[0], parsed[1], below)) if parsed else None


def frame_of(card: BuilderElement, below: tuple[int, int, int] | None,
             theme: ThemeTokens) -> CardFrame | None:
    """`card`'s frame over `below` (the RGB behind it), or None when it is not
    judged: an unreadable band (a photo nobody measured), or a card that paints
    no fill of its own or one no pass could read."""
    from app.services.section_content import _own_fill, _to_hex, band_of_surface

    if below is None:
        return None
    fill = _own_fill(card, theme, below)
    if not isinstance(fill, tuple):
        return None
    band, fill_hex = _to_hex(below), _to_hex(fill)
    styles = card.styles or {}
    rule = _rule_hex(styles.get("border"), below, theme)
    shadow = str(styles.get("boxShadow") or "").strip().lower() not in ("", "none")
    return CardFrame(
        band=band, fill=fill_hex,
        dark=band_of_surface(band, theme) == "dark",
        fill_delta=delta_e(fill_hex, band),
        rule_delta=delta_e(rule, band) if rule else 0.0,
        shadow=shadow,
    )


def walk_cards(section: BuilderElement, theme: ThemeTokens
               ) -> Iterator[tuple[BuilderElement, CardFrame]]:
    """Every card in `section` with its frame, each judged against the surface
    actually behind it — the section's band, or the panel a card sits inside.
    A card restated by the caller between yields is read back with its new fill
    for its own children."""
    from app.services.section_content import _UNREADABLE, _NO_FILL, _expand_hex, _hex_to_rgb, _own_fill

    def walk(node: BuilderElement, below):
        cards = {id(c) for c in card_children(node)}
        for child in _children(node):
            if id(child) in cards:
                frame = frame_of(child, below, theme)
                if frame is not None:
                    yield child, frame
            own = _own_fill(child, theme, below) if below is not None else _UNREADABLE
            yield from walk(child, None if own is _UNREADABLE else (below if own is _NO_FILL else own))

    page = _hex_to_rgb(_expand_hex(theme.page.background))
    own = _own_fill(section, theme, page)
    yield from walk(section, None if own is _UNREADABLE else (page if own is _NO_FILL else own))


# --- the remedy ----------------------------------------------------------------


def _token(role: str, value: str) -> str:
    return f"var(--builder-color-{role}, {value})"


def _lifted_fill(frame: CardFrame, theme: ThemeTokens) -> str | None:
    """A fill that steps off a dark band: a palette role when one does it
    without a jarring jump, else the card's own colour moved along its hue —
    away from the band, and never across into the light half (the copy on it
    is already inked for a dark card)."""
    from app.services.color_system import derive, from_oklch, oklch
    from app.services.section_content import band_of_surface

    def fits(candidate: str) -> bool:
        return (DARK_FILL_MIN <= delta_e(candidate, frame.band) <= 2 * DARK_FILL_MIN
                and band_of_surface(candidate, theme) == "dark")

    roles = derive(theme.palette.model_dump())
    fitting = [(delta_e(roles[r], frame.band), r) for r in _FILL_ROLES if fits(roles[r])]
    if fitting:
        role = min(fitting)[1]
        return _token(role, roles[role])
    light, chroma, hue = oklch(frame.fill)
    away = 1.0 if light >= oklch(frame.band)[0] else -1.0
    for direction in (away, -away):
        step = 1
        while 0.0 <= (lifted := light + direction * step * _LIFT_STEP) <= 1.0:
            candidate = from_oklch(lifted, chroma, hue)
            if band_of_surface(candidate, theme) != "dark":
                break
            if delta_e(candidate, frame.band) >= DARK_FILL_MIN:
                return candidate
            step += 1
    return None


def _readable_rule(frame: CardFrame, theme: ThemeTokens) -> str:
    """A hairline that reads on a light band: the palette's `border` role when
    it does, else the band mixed toward its own ink until it does."""
    from app.services.color_system import derive, mix
    from app.services.theme import ink_on

    roles = derive(theme.palette.model_dump())
    if delta_e(roles["border"], frame.band) >= RULE_MIN:
        return _token("border", roles["border"])
    ink = ink_on(theme.palette, frame.band)
    amount = 0.1
    while amount < 1.0 and delta_e(mix(frame.band, ink, amount), frame.band) < RULE_MIN:
        amount += 0.02
    return mix(frame.band, ink, amount)


def _restated_border(border: object, color: str) -> str:
    """`border` in `color`, keeping the author's width and style."""
    if isinstance(border, str) and (m := re.match(r"\s*(\d+(?:\.\d+)?px)\s+(solid|dashed|dotted)\b", border)):
        return f"{m.group(1)} {m.group(2)} {color}"
    return f"1px solid {color}"


def separate(card: BuilderElement, frame: CardFrame, theme: ThemeTokens) -> bool:
    """Restate `card` so it stands off its band; False when it can't be done
    locally (a gradient fill, or no shade of its hue that stays dark)."""
    from app.services.palette_styles import surface_foregrounds

    styles = card.styles
    if frame.dark:
        if styles.get("background") or styles.get("backgroundImage"):
            return False
        lifted = _lifted_fill(frame, theme)
        if lifted is None:
            return False
        styles["backgroundColor"] = lifted
        # The copy was inked for the old fill; the new one is opaque, so the
        # card is a surface of its own and its copy is re-held to AA on it.
        surface_foregrounds([card], theme, bind=True)
        return True
    styles["border"] = _restated_border(styles.get("border"), _readable_rule(frame, theme))
    return True


def separate_cards(elements: list[BuilderElement], theme: ThemeTokens) -> int:
    """The floor pass: every card on a page stands off the band it sits on.
    Runs on the FINISHED colours (after the v2 rebind), so the band it judges
    against is the one the renderer paints. Returns how many it restated."""
    restated = 0
    for section in elements:
        for card, frame in walk_cards(section, theme):
            if not frame.separates and separate(card, frame, theme):
                restated += 1
    return restated
