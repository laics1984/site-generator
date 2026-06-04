"""
Theme generation: seed color + brand mood → full design tokens.

Methodologies applied (the "best practice" the user asked for):

1. **60-30-10 colour rule.** Background ~60% neutral, secondary ~30%, accent ~10%.
   We pick `surface` as a faint tint of primary so the page reads cohesive without
   any single block screaming "BRAND COLOR HERE".

2. **WCAG AA contrast.** We compute relative luminance (sRGB → linear → Y) and
   guarantee text-on-background ≥ 4.5:1 for body copy. If primary fails as a
   button background against white text, we darken it until it passes 4.5:1.

3. **Colour theory.** Secondary = dark neutral with a hint of primary's hue
   (avoids the "designer cliché" of pure black). Accent = split-complementary
   hue from primary (30° from the complement) — more harmonious than pure
   complement (180°).

4. **Modular type scale.** We pick the heading vs body font family by mood;
   the schema_builder enforces the size ratios (1.250 = major third).

5. **Touch targets & radius vocabulary.** Button radius scales by mood:
   sharper for "technical"/"editorial", rounder for "friendly"/"playful".
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

from app.models.brand import (
    BrandMood,
    Buttons,
    ColorPalette,
    PageTokens,
    ThemeTokens,
    Typography,
)


# --- colour math ----------------------------------------------------------------


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    s = hex_color.lstrip("#")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _rgb_to_hls(r: int, g: int, b: int) -> tuple[float, float, float]:
    return colorsys.rgb_to_hls(r / 255, g / 255, b / 255)


def _hls_to_rgb(h: float, l: float, s: float) -> tuple[int, int, int]:
    r, g, b = colorsys.hls_to_rgb(h, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return round(r * 255), round(g * 255), round(b * 255)


def _relative_luminance(hex_color: str) -> float:
    """sRGB → linear → Y per WCAG 2.x."""

    def chan(c: int) -> float:
        c_ = c / 255
        return c_ / 12.92 if c_ <= 0.03928 else ((c_ + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(hex_color)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def _contrast(a_hex: str, b_hex: str) -> float:
    la, lb = _relative_luminance(a_hex), _relative_luminance(b_hex)
    light, dark = max(la, lb), min(la, lb)
    return (light + 0.05) / (dark + 0.05)


def _adjust_lightness(hex_color: str, delta: float) -> str:
    h, l, s = _rgb_to_hls(*_hex_to_rgb(hex_color))
    r, g, b = _hls_to_rgb(h, l + delta, s)
    return _rgb_to_hex(r, g, b)


def _rotate_hue(hex_color: str, degrees: float) -> str:
    """Rotate hue by `degrees` (0-360)."""
    h, l, s = _rgb_to_hls(*_hex_to_rgb(hex_color))
    h_new = (h + degrees / 360.0) % 1.0
    r, g, b = _hls_to_rgb(h_new, l, s)
    return _rgb_to_hex(r, g, b)


def _ensure_contrast_against(
    bg_hex: str, fg_hex: str, *, min_ratio: float = 4.5
) -> str:
    """
    Darken or lighten `fg_hex` until contrast vs `bg_hex` meets `min_ratio`.
    Returns the adjusted foreground.
    """
    fg = fg_hex
    bg_lum = _relative_luminance(bg_hex)
    # If bg is light, push fg darker; if bg is dark, push fg lighter.
    direction = -0.05 if bg_lum > 0.5 else 0.05
    for _ in range(20):
        if _contrast(bg_hex, fg) >= min_ratio:
            return fg
        fg = _adjust_lightness(fg, direction)
    return fg


def _text_for_background(bg_hex: str) -> str:
    """Pick white or near-black for best contrast against `bg_hex`."""
    return "#ffffff" if _relative_luminance(bg_hex) < 0.5 else "#0f172a"


# --- palette construction -------------------------------------------------------


@dataclass(frozen=True)
class MoodSpec:
    radius: int
    secondary_lightness: float  # final L for the secondary (dark neutral)
    surface_tint_strength: float  # how much primary to mix into surface (0..1)
    page_width_mode: str
    heading_font: str
    body_font: str
    google_fonts: tuple[str, ...]
    # --- 2025/26 trend tokens (passed through to ThemeTokens) -------------------
    type_scale_ratio: float = 1.25
    use_glass: bool = False
    background_strategy: str = "flat"  # flat | mesh | grain | mesh+grain
    shadow_scale: str = "soft"  # soft | elevated | dramatic
    display_font: str | None = None  # None → reuse heading_font


# Tuned per mood. Heading/body picks aim for distinctive but legible pairings.
# Always include the google_fonts CSV so the generator UI / public site can
# preload them via <link rel="stylesheet">.
MOOD_SPECS: dict[BrandMood, MoodSpec] = {
    "modern": MoodSpec(
        radius=12,
        secondary_lightness=0.16,
        surface_tint_strength=0.05,
        page_width_mode="contained",
        heading_font='"Schibsted Grotesk", system-ui, sans-serif',
        body_font='"Hanken Grotesk", system-ui, sans-serif',
        google_fonts=(
            "Schibsted Grotesk:wght@400;500;600;700;800",
            "Hanken Grotesk:wght@400;500;600",
        ),
        type_scale_ratio=1.30,
        use_glass=True,
        background_strategy="mesh",
        shadow_scale="elevated",
        display_font='"Schibsted Grotesk", system-ui, sans-serif',
    ),
    "luxury": MoodSpec(
        radius=4,
        secondary_lightness=0.12,
        surface_tint_strength=0.04,
        page_width_mode="contained",
        heading_font='"Cormorant Garamond", Georgia, serif',
        body_font='"Jost", system-ui, sans-serif',
        google_fonts=(
            "Cormorant Garamond:wght@500;600;700",
            "Jost:wght@400;500;600",
        ),
        type_scale_ratio=1.33,
        use_glass=False,
        background_strategy="grain",
        shadow_scale="soft",
        display_font='"Cormorant Garamond", Georgia, serif',
    ),
    "friendly": MoodSpec(
        radius=20,
        secondary_lightness=0.20,
        surface_tint_strength=0.10,
        page_width_mode="contained",
        heading_font='"Gabarito", system-ui, sans-serif',
        body_font='"Figtree", system-ui, sans-serif',
        google_fonts=(
            "Gabarito:wght@500;700;800",
            "Figtree:wght@400;500;600",
        ),
        type_scale_ratio=1.28,
        use_glass=True,
        background_strategy="mesh",
        shadow_scale="elevated",
        display_font='"Gabarito", system-ui, sans-serif',
    ),
    "technical": MoodSpec(
        radius=6,
        secondary_lightness=0.14,
        surface_tint_strength=0.03,
        page_width_mode="contained",
        heading_font='"Chivo", system-ui, sans-serif',
        body_font='"IBM Plex Sans", system-ui, sans-serif',
        google_fonts=(
            "Chivo:wght@500;700;800",
            "IBM Plex Sans:wght@400;500;600",
        ),
        type_scale_ratio=1.22,
        use_glass=False,
        background_strategy="flat",
        shadow_scale="soft",
        display_font='"Chivo", system-ui, sans-serif',
    ),
    "editorial": MoodSpec(
        radius=8,
        secondary_lightness=0.14,
        surface_tint_strength=0.05,
        page_width_mode="contained",
        heading_font='"Fraunces", Georgia, serif',
        body_font='"Hanken Grotesk", system-ui, sans-serif',
        google_fonts=(
            "Fraunces:opsz,wght@9..144,400;9..144,600;9..144,700",
            "Hanken Grotesk:wght@400;500;600",
        ),
        type_scale_ratio=1.40,
        use_glass=False,
        background_strategy="grain",
        shadow_scale="dramatic",
        display_font='"Fraunces", Georgia, serif',
    ),
    "playful": MoodSpec(
        radius=24,
        secondary_lightness=0.18,
        surface_tint_strength=0.08,
        page_width_mode="contained",
        heading_font='"Syne", system-ui, sans-serif',
        body_font='"Figtree", system-ui, sans-serif',
        google_fonts=(
            "Syne:wght@600;700;800",
            "Figtree:wght@400;500;600",
        ),
        type_scale_ratio=1.45,
        use_glass=True,
        background_strategy="mesh+grain",
        shadow_scale="dramatic",
        display_font='"Syne", system-ui, sans-serif',
    ),
}


def _build_palette(seed_hex: str, mood: BrandMood) -> ColorPalette:
    spec = MOOD_SPECS[mood]

    # Primary: ensure it's not too pale to read as a colour. Pull toward L=0.45 if very light.
    h, l, s = _rgb_to_hls(*_hex_to_rgb(seed_hex))
    if l > 0.7:
        l = 0.5
        s = max(s, 0.45)
    elif l < 0.15:
        l = 0.25
    primary = _rgb_to_hex(*_hls_to_rgb(h, l, max(s, 0.40)))

    # Secondary: same hue family but very dark + slightly desaturated. Avoids
    # the "pure black" cliché; ties the dark neutral to the brand.
    secondary_h = h
    secondary_l = spec.secondary_lightness
    secondary_s = min(s * 0.55, 0.22)
    secondary = _rgb_to_hex(*_hls_to_rgb(secondary_h, secondary_l, secondary_s))

    # Accent: split-complementary (~150° from primary). More harmonious than
    # pure complement. Boosted saturation; mid-lightness.
    accent_hex = _rotate_hue(primary, 150)
    ah, _, _ = _rgb_to_hls(*_hex_to_rgb(accent_hex))
    accent = _rgb_to_hex(*_hls_to_rgb(ah, 0.55, 0.65))

    background = "#ffffff"

    # Surface: white with a faint primary tint for warmth without screaming.
    bg_r, bg_g, bg_b = _hex_to_rgb(background)
    pr_r, pr_g, pr_b = _hex_to_rgb(primary)
    tint = spec.surface_tint_strength
    surface = _rgb_to_hex(
        round(bg_r * (1 - tint) + pr_r * tint),
        round(bg_g * (1 - tint) + pr_g * tint),
        round(bg_b * (1 - tint) + pr_b * tint),
    )
    # Surface must stay light; clip to L>=0.95 to guarantee body text contrast.
    sh, sl, ss = _rgb_to_hls(*_hex_to_rgb(surface))
    if sl < 0.95:
        surface = _rgb_to_hex(*_hls_to_rgb(sh, 0.97, min(ss, 0.20)))

    # Body text: near-black tied to secondary hue. Auto-bumped if it ever fails.
    text = _ensure_contrast_against(background, "#0f172a", min_ratio=7.0)

    return ColorPalette(
        primary=primary,
        secondary=secondary,
        accent=accent,
        text=text,
        background=background,
        surface=surface,
    )


# --- curated palette (Tailwind scale) -------------------------------------------
#
# Modern, accessible, instantly-familiar web colours. We keep the brand HUE from
# the logo but snap to the nearest Tailwind family, then build the palette from
# that family's curated shades + the slate neutral. This avoids muddy/off-trend
# extracted hex while preserving brand identity. ("Web-safe" 216 is obsolete;
# this is the current-web equivalent.)
TAILWIND: dict[str, dict[str, str]] = {
    "red": {"50": "#fef2f2", "500": "#ef4444", "600": "#dc2626", "700": "#b91c1c"},
    "orange": {"50": "#fff7ed", "500": "#f97316", "600": "#ea580c", "700": "#c2410c"},
    "amber": {"50": "#fffbeb", "500": "#f59e0b", "600": "#d97706", "700": "#b45309"},
    "yellow": {"50": "#fefce8", "500": "#eab308", "600": "#ca8a04", "700": "#a16207"},
    "lime": {"50": "#f7fee7", "500": "#84cc16", "600": "#65a30d", "700": "#4d7c0f"},
    "green": {"50": "#f0fdf4", "500": "#22c55e", "600": "#16a34a", "700": "#15803d"},
    "emerald": {"50": "#ecfdf5", "500": "#10b981", "600": "#059669", "700": "#047857"},
    "teal": {"50": "#f0fdfa", "500": "#14b8a6", "600": "#0d9488", "700": "#0f766e"},
    "cyan": {"50": "#ecfeff", "500": "#06b6d4", "600": "#0891b2", "700": "#0e7490"},
    "sky": {"50": "#f0f9ff", "500": "#0ea5e9", "600": "#0284c7", "700": "#0369a1"},
    "blue": {"50": "#eff6ff", "500": "#3b82f6", "600": "#2563eb", "700": "#1d4ed8"},
    "indigo": {"50": "#eef2ff", "500": "#6366f1", "600": "#4f46e5", "700": "#4338ca"},
    "violet": {"50": "#f5f3ff", "500": "#8b5cf6", "600": "#7c3aed", "700": "#6d28d9"},
    "purple": {"50": "#faf5ff", "500": "#a855f7", "600": "#9333ea", "700": "#7e22ce"},
    "fuchsia": {"50": "#fdf4ff", "500": "#d946ef", "600": "#c026d3", "700": "#a21caf"},
    "pink": {"50": "#fdf2f8", "500": "#ec4899", "600": "#db2777", "700": "#be185d"},
    "rose": {"50": "#fff1f2", "500": "#f43f5e", "600": "#e11d48", "700": "#be123c"},
    # Neutral for text / dark surfaces.
    "slate": {"50": "#f8fafc", "500": "#64748b", "600": "#475569", "700": "#334155", "900": "#0f172a"},
}
_SLATE_900 = TAILWIND["slate"]["900"]


def _nearest_tailwind_hue(seed_hex: str) -> str:
    """Nearest Tailwind colour family to a seed hex (by hue). Low-saturation
    seeds (greyscale logos) fall back to a tasteful blue."""
    h, _, s = _rgb_to_hls(*_hex_to_rgb(seed_hex))
    if s < 0.12:
        return "blue"
    seed_deg = h * 360.0
    best, best_dist = "blue", 1e9
    for name, shades in TAILWIND.items():
        if name == "slate":
            continue
        hh, _, _ = _rgb_to_hls(*_hex_to_rgb(shades["500"]))
        dist = abs(((hh * 360.0 - seed_deg + 180.0) % 360.0) - 180.0)
        if dist < best_dist:
            best_dist, best = dist, name
    return best


def _snap_palette(seed_hex: str) -> ColorPalette:
    """Curated palette: brand hue snapped to the nearest Tailwind family."""
    family = _nearest_tailwind_hue(seed_hex)
    shades = TAILWIND[family]
    accent_family = _nearest_tailwind_hue(_rotate_hue(shades["500"], 150))
    if accent_family == family:  # keep accent distinct from primary
        accent_family = "amber" if family != "amber" else "teal"
    return ColorPalette(
        primary=shades["600"],
        secondary=_SLATE_900,
        accent=TAILWIND[accent_family]["500"],
        text=_ensure_contrast_against("#ffffff", _SLATE_900, min_ratio=7.0),
        background="#ffffff",
        surface=shades["50"],
    )


def build_theme(
    seed_hex: str | None,
    mood: BrandMood = "modern",
    palette_mode: str = "tailwind",
) -> ThemeTokens:
    """
    Top-level factory. `seed_hex` is the primary color (usually from the logo).
    Falls back to a tasteful default if missing.

    `palette_mode`:
      - "tailwind" (default): snap the brand hue to a curated Tailwind family —
        modern, accessible, on-trend colours.
      - "derive": the legacy free-form HSL derivation.
    Either way `mood` still drives typography, radius, and page width.
    """
    seed = (seed_hex or "#2563eb").lower()
    if not seed.startswith("#") or len(seed) != 7:
        seed = "#2563eb"

    palette = (
        _snap_palette(seed) if palette_mode == "tailwind" else _build_palette(seed, mood)
    )
    spec = MOOD_SPECS[mood]

    # Button background needs ≥4.5:1 against white text.
    button_bg = palette.primary
    button_text = _text_for_background(button_bg)
    if _contrast(button_bg, button_text) < 4.5:
        button_bg = _ensure_contrast_against(button_text, button_bg, min_ratio=4.5)

    typography = Typography.model_validate(
        {
            "headingFont": spec.heading_font,
            "bodyFont": spec.body_font,
            "google_fonts": list(spec.google_fonts),
        }
    )
    buttons = Buttons(background=button_bg, text=button_text, radius=spec.radius)
    page = PageTokens.model_validate(
        {
            "widthMode": spec.page_width_mode,
            "maxWidth": 1280,
            "background": palette.background,
        }
    )

    # Section rhythm: alternate light tints to avoid a wall of white. CTAs get
    # the inverted treatment via inverted_cta=True (CTA section uses photo bg).
    section_rotation: list[str] = ["background", "surface", "background"]

    return ThemeTokens(
        palette=palette,
        typography=typography,
        buttons=buttons,
        page=page,
        mood=mood,
        section_rotation=section_rotation,  # type: ignore[arg-type]
        inverted_cta=True,
        type_scale_ratio=spec.type_scale_ratio,
        use_glass=spec.use_glass,
        background_strategy=spec.background_strategy,  # type: ignore[arg-type]
        shadow_scale=spec.shadow_scale,  # type: ignore[arg-type]
        display_font=spec.display_font,
    )
