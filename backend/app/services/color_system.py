"""Perceptual derivation for the existing six-color theme contract.

AI owns the four creative colors. This module owns gamut, readable pairs and
semantic variants. Mirrored by webtree-public/lib/colorSystem.ts; numerical
fixtures exercise both implementations. No network or render-time I/O.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

HEX = re.compile(r"^#[0-9a-f]{6}$", re.I)
FIELDS = ("primary", "secondary", "accent", "text", "background", "surface")


def normalize_hex(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Use a hex color such as #2563eb")
    value = value.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", value):
        value = "#" + "".join(c * 2 for c in value[1:])
    if not HEX.fullmatch(value):
        raise ValueError("Use a hex color such as #2563eb")
    return value


def rgb(hex_color: str) -> tuple[float, float, float]:
    return tuple(int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))  # type: ignore[return-value]


def linear(c: float) -> float:
    return c / 12.92 if c <= .04045 else ((c + .055) / 1.055) ** 2.4


def luminance(color: str) -> float:
    r, g, b = map(linear, rgb(color))
    return .2126 * r + .7152 * g + .0722 * b


def contrast(a: str, b: str) -> float:
    x, y = sorted((luminance(a), luminance(b)))
    return (y + .05) / (x + .05)


def oklch(color: str) -> tuple[float, float, float]:
    r, g, b = map(linear, rgb(color))
    l = (.4122214708*r + .5363325363*g + .0514459929*b) ** (1/3)
    m = (.2119034982*r + .6806995451*g + .1073969566*b) ** (1/3)
    s = (.0883024619*r + .2817188376*g + .6299787005*b) ** (1/3)
    light = .2104542553*l + .793617785*m - .0040720468*s
    a = 1.9779984951*l - 2.428592205*m + .4505937099*s
    b_ = .0259040371*l + .7827717662*m - .808675766*s
    return light, math.hypot(a, b_), math.atan2(b_, a)


def _linear_rgb(light: float, chroma: float, hue: float) -> tuple[float, float, float]:
    a, b = chroma * math.cos(hue), chroma * math.sin(hue)
    l = (light + .3963377774*a + .2158037573*b) ** 3
    m = (light - .1055613458*a - .0638541728*b) ** 3
    s = (light - .0894841775*a - 1.291485548*b) ** 3
    return (4.0767416621*l - 3.3077115913*m + .2309699292*s,
            -1.2684380046*l + 2.6097574011*m - .3413193965*s,
            -.0041960863*l - .7034186147*m + 1.707614701*s)


def from_oklch(light: float, chroma: float, hue: float) -> str:
    light, chroma = max(0., min(1., light)), max(0., chroma)
    channels = _linear_rgb(light, chroma, hue)
    if not all(-1e-7 <= c <= 1 + 1e-7 for c in channels):
        lo, hi = 0., chroma
        for _ in range(24):
            mid = (lo + hi) / 2
            if all(-1e-7 <= c <= 1 + 1e-7 for c in _linear_rgb(light, mid, hue)):
                lo = mid
            else:
                hi = mid
        channels = _linear_rgb(light, lo, hue)
    def channel(c: float) -> int:
        c = max(0., min(1., c))
        encoded = 12.92*c if c <= .0031308 else 1.055*c**(1/2.4)-.055
        return math.floor(encoded * 255 + .5)
    return "#" + "".join(f"{channel(c):02x}" for c in channels)


def tone(color: str, light: float, chroma: float | None = None) -> str:
    _, c, h = oklch(color)
    return from_oklch(light, c if chroma is None else chroma, h)


def mix(a: str, b: str, amount: float) -> str:
    # Interpolate in Cartesian OKLab, avoiding an arbitrary hue for neutrals.
    al, ac, ah = oklch(a)
    bl, bc, bh = oklch(b)
    t = max(0., min(1., amount))
    x = ac*math.cos(ah)*(1-t) + bc*math.cos(bh)*t
    y = ac*math.sin(ah)*(1-t) + bc*math.sin(bh)*t
    return from_oklch(al*(1-t)+bl*t, math.hypot(x, y), math.atan2(y, x))


def ensure_contrast(foreground: str, background: str, target: float = 4.5) -> str:
    if contrast(foreground, background) >= target:
        return foreground
    light, chroma, hue = oklch(foreground)
    candidates: list[tuple[float, str]] = []
    for end in (0., 1.):
        if contrast(from_oklch(end, chroma, hue), background) < target:
            continue
        lo, hi = 0., 1.
        for _ in range(24):
            t = (lo + hi) / 2
            value = from_oklch(light + (end-light)*t, chroma, hue)
            if contrast(value, background) >= target:
                hi = t
            else:
                lo = t
        value = from_oklch(light + (end-light)*hi, chroma, hue)
        candidates.append((abs(oklch(value)[0]-light), value))
    if candidates:
        return min(candidates, key=lambda p: p[0])[1]
    return max(("#000000", "#ffffff"), key=lambda c: contrast(c, background))


def readable(background: str, dark: str, light: str, target: float = 4.5) -> str:
    candidate = max((dark, light), key=lambda c: contrast(c, background))
    return ensure_contrast(candidate, background, target)


def creative_colors(values: dict[str, str]) -> dict[str, str]:
    values = {key: normalize_hex(values[key]) for key in ("primary", "secondary", "accent", "surface")}
    # The four creative choices stay canonical, including a manually selected
    # dark canvas. Restraint belongs in the prompt and derived surfaces; silently
    # bleaching the chosen surface would make the editor and preview disagree.
    surface = values["surface"]
    dark = luminance(surface) < .2
    sl, sc, sh = oklch(surface)
    background = from_oklch(max(.12, sl-.055) if dark else min(.995, sl+.025), min(sc*.7, .035), sh)
    text = ensure_contrast(tone(surface, .94 if dark else .18, .018), background, 7.)
    return {**values, "surface": surface, "background": background, "text": text}


@lru_cache(maxsize=256)
def _derive(values: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    p = dict(zip(FIELDS, values))
    bg, surface = p["background"], p["surface"]
    dark_page = luminance(bg) < .2
    dark = tone(p["secondary"], .16, .022)
    light = tone(surface, .985, .008)
    out = dict(p)
    out.update({"surface-inverse": dark, "ink-dark": dark, "ink-light": light,
                "text-inverse": readable(dark, dark, light),
                "surface-elevated": tone(surface, min(.995, oklch(surface)[0]+(.04 if dark_page else .012))),
                "surface-muted": mix(bg, p["secondary"], .08 if dark_page else .045),
                "surface-alternate": mix(surface, p["secondary"], .10 if dark_page else .055),
                "photo-ink": "#ffffff", "photo-ink-dark": "#000000",
                "photo-scrim": "#101820", "photo-wash": "#ffffff"})
    out["text"] = ensure_contrast(p["text"], bg)
    out["heading"] = out["text"]
    muted = mix(out["text"], bg, .28)
    out["text-muted"] = ensure_contrast(muted, bg)
    out["border"] = mix(bg, out["text"], .18)
    out["border-strong"] = ensure_contrast(mix(bg, out["text"], .40), bg, 3.)
    for name in ("background", "surface", "surface-elevated", "surface-muted", "surface-alternate", "surface-inverse"):
        out["on-"+name] = readable(out[name], dark, light)
    for name in ("primary", "secondary", "accent"):
        color = p[name]
        l, c, h = oklch(color)
        direction = .045 if l < .4 else -.045
        out[name+"-hover"] = from_oklch(l+direction, c, h)
        out[name+"-active"] = from_oklch(l+direction*1.8, c, h)
        out[name+"-ink"] = ensure_contrast(color, bg)
        out[name+"-tint"] = mix(bg, color, .16 if dark_page else .10)
        out[name+"-tint-ink"] = ensure_contrast(color, out[name+"-tint"])
        out["on-"+name+"-tint"] = readable(out[name+"-tint"], dark, light)
        out[name+"-border"] = ensure_contrast(color, bg, 3.)
        for suffix in ("", "-hover", "-active"):
            out["on-"+name+suffix] = readable(out[name+suffix], dark, light)
    for index, color in enumerate((p["primary"], p["secondary"], p["accent"], p["surface"]), 1):
        out[f"band-{index}"] = mix(bg, color, .15 if dark_page else .075)
        out[f"on-band-{index}"] = readable(out[f"band-{index}"], dark, light)
    for surface in ("background", "surface", "surface-inverse", "primary", "secondary", "accent", "band-1", "band-2", "band-3", "band-4"):
        out[f"error-ink-on-{surface}"] = ensure_contrast("#dc2626", out[surface])
        for brand in ("primary", "secondary", "accent"):
            out[f"{brand}-ink-on-{surface}"] = ensure_contrast(p[brand], out[surface])
            out[f"{brand}-border-on-{surface}"] = ensure_contrast(p[brand], out[surface], 3.)
    return tuple(out.items())


def derive(colors: dict[str, str]) -> dict[str, str]:
    return dict(_derive(tuple(normalize_hex(colors[key]) for key in FIELDS)))


def css_vars(colors: dict[str, str]) -> dict[str, str]:
    return {"--builder-color-"+key: value for key, value in derive(colors).items()}
