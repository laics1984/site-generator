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
import hashlib
from dataclasses import dataclass
from typing import Literal

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


def band_colors(palette: ColorPalette, band: Literal["light", "dark"]) -> tuple[str, str]:
    """(background, text) brand colours for a luminance band.

    light → `surface` (a faint primary tint, near the page colour) with dark text;
    dark → `secondary` (the dark, primary-hued neutral) with white text. Both stay
    inside the one brand palette, so the page alternates *luminance* of one hue
    family rather than clashing hues. Text comes from `_text_for_background`, so a
    section's band and its font colour always agree. See
    SECTION_VISUAL_POLICY_SPEC.md §6/§7.
    """
    bg = palette.secondary if band == "dark" else palette.surface
    return bg, _text_for_background(bg)


# --- palette construction -------------------------------------------------------


@dataclass(frozen=True)
class FontPairing:
    """One heading/body type pairing plus the Google Fonts specs to load it.

    `heading_font`/`body_font` are complete CSS font-family strings (with system
    fallbacks) so the public site renders before the web fonts load. `google_fonts`
    are the `Family:wght@...` specs the <link> loader needs. `display_font` is an
    optional oversized hero face; None reuses the heading font.

    Alternate pairings are curated from the ui-ux-pro-max typography catalogue
    (MIT, github.com/nextlevelbuilder/ui-ux-pro-max-skill), bucketed by mood.
    """

    heading_font: str
    body_font: str
    google_fonts: tuple[str, ...]
    display_font: str | None = None
    # Industry/use-case descriptors (from the catalogue's "Best For" + keywords).
    # Used to prefer the pairing that best fits the site's industry; see
    # _pick_pairing. Empty tags simply never win an industry match.
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class CuratedPalette:
    """A hand-designed, industry-tagged palette from the ui-ux-pro-max colour
    catalogue (MIT, github.com/nextlevelbuilder/ui-ux-pro-max-skill).

    Stored as source tokens; `_palette_from_curated` maps them onto the builder's
    6-token ColorPalette while preserving our light-background / dark-band model:
    `dark` (the catalogue's Foreground) becomes both the body text and the dark
    section background, `tint` becomes the light section surface.
    """

    name: str
    categories: tuple[str, ...]  # IndustryCategory values this palette suits
    primary: str
    accent: str
    dark: str  # darkest token → body text + dark-band background
    tint: str  # light page tint → light section surface


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
    # The ui-ux-pro-max style this mood embodies (catalogue style name). Carried
    # onto ThemeTokens as design lineage; the trend tokens above are the tuned
    # expression of it, not derived from it.
    style: str = ""
    # Industry/use-case tags for the default pairing (the scalar fields above).
    default_font_tags: tuple[str, ...] = ()
    # Curated alternate pairings for this mood (same personality, different faces).
    # The scalar fields above are the default pairing (pool index 0); build_theme
    # picks one from `font_pool` by industry fit, then by a per-site seed.
    alt_pairings: tuple[FontPairing, ...] = ()

    @property
    def font_pool(self) -> tuple[FontPairing, ...]:
        """Default pairing (from the scalar fields) followed by the alternates."""
        default = FontPairing(
            heading_font=self.heading_font,
            body_font=self.body_font,
            google_fonts=self.google_fonts,
            display_font=self.display_font,
            tags=self.default_font_tags,
        )
        return (default, *self.alt_pairings)


# Tuned per mood. Heading/body picks aim for distinctive but legible pairings.
# Always include the google_fonts CSV so the generator UI / public site can
# preload them via <link rel="stylesheet">.
MOOD_SPECS: dict[BrandMood, MoodSpec] = {
    "modern": MoodSpec(
        style="Glassmorphism",
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
        default_font_tags=("saas", "fintech", "tech", "startup", "software", "app"),
        alt_pairings=(
            FontPairing(
                heading_font='"Space Grotesk", system-ui, sans-serif',
                body_font='"DM Sans", system-ui, sans-serif',
                google_fonts=(
                    "Space Grotesk:wght@400;500;600;700",
                    "DM Sans:wght@400;500;700",
                ),
                tags=("tech", "startup", "saas", "developer", "ai", "software"),
            ),
            FontPairing(
                heading_font='"Outfit", system-ui, sans-serif',
                body_font='"Work Sans", system-ui, sans-serif',
                google_fonts=(
                    "Outfit:wght@300;400;500;600;700",
                    "Work Sans:wght@300;400;500;600;700",
                ),
                tags=("portfolio", "agency", "landing", "creative", "brand"),
            ),
            FontPairing(
                heading_font='"Plus Jakarta Sans", system-ui, sans-serif',
                body_font='"Plus Jakarta Sans", system-ui, sans-serif',
                google_fonts=("Plus Jakarta Sans:wght@400;500;600;700",),
                tags=("dashboard", "tools", "health", "minimal", "showcase"),
            ),
        ),
    ),
    "luxury": MoodSpec(
        style="Minimalism & Swiss Style",
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
        default_font_tags=(
            "luxury", "hospitality", "jewellery", "jewelry", "real estate",
            "premium", "fashion",
        ),
        alt_pairings=(
            FontPairing(
                heading_font='"Playfair Display", Georgia, serif',
                body_font='"Inter", system-ui, sans-serif',
                google_fonts=(
                    "Playfair Display:wght@400;500;600;700",
                    "Inter:wght@300;400;500;600;700",
                ),
                tags=(
                    "luxury", "fashion", "spa", "beauty", "editorial",
                    "magazine", "ecommerce", "e-commerce",
                ),
            ),
            FontPairing(
                heading_font='"Cormorant", Georgia, serif',
                body_font='"Montserrat", system-ui, sans-serif',
                google_fonts=(
                    "Cormorant:wght@400;500;600;700",
                    "Montserrat:wght@300;400;500;600;700",
                ),
                tags=(
                    "fashion", "luxury", "jewelry", "jewellery",
                    "ecommerce", "e-commerce",
                ),
            ),
            FontPairing(
                heading_font='"Cinzel", Georgia, serif',
                body_font='"Josefin Sans", system-ui, sans-serif',
                google_fonts=(
                    "Cinzel:wght@400;500;600;700",
                    "Josefin Sans:wght@300;400;500;600;700",
                ),
                tags=(
                    "real estate", "property", "architecture",
                    "interior design", "luxury",
                ),
            ),
        ),
    ),
    "friendly": MoodSpec(
        style="Soft UI Evolution",
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
        default_font_tags=(
            "consumer", "lifestyle", "wellness", "friendly", "community",
        ),
        alt_pairings=(
            FontPairing(
                heading_font='"Fredoka", system-ui, sans-serif',
                body_font='"Nunito", system-ui, sans-serif',
                google_fonts=(
                    "Fredoka:wght@400;500;600;700",
                    "Nunito:wght@300;400;500;600;700",
                ),
                tags=(
                    "children", "kids", "education", "gaming",
                    "creative", "entertainment",
                ),
            ),
            FontPairing(
                heading_font='"Varela Round", system-ui, sans-serif',
                body_font='"Nunito Sans", system-ui, sans-serif',
                google_fonts=(
                    "Varela Round",
                    "Nunito Sans:wght@300;400;500;600;700",
                ),
                tags=("children", "kids", "pet", "wellness", "friendly"),
            ),
            FontPairing(
                heading_font='"Lora", Georgia, serif',
                body_font='"Raleway", system-ui, sans-serif',
                google_fonts=(
                    "Lora:wght@400;500;600;700",
                    "Raleway:wght@300;400;500;600;700",
                ),
                tags=(
                    "health", "wellness", "spa", "meditation", "yoga", "organic",
                ),
            ),
        ),
    ),
    "technical": MoodSpec(
        style="Flat Design",
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
        default_font_tags=(
            "engineering", "b2b", "developer", "dev tools", "technical", "dashboard",
        ),
        alt_pairings=(
            FontPairing(
                heading_font='"JetBrains Mono", ui-monospace, monospace',
                body_font='"IBM Plex Sans", system-ui, sans-serif',
                google_fonts=(
                    "JetBrains Mono:wght@400;500;600;700",
                    "IBM Plex Sans:wght@300;400;500;600;700",
                ),
                tags=(
                    "developer", "documentation", "code", "tech blog",
                    "cli", "saas",
                ),
            ),
            FontPairing(
                heading_font='"Fira Code", ui-monospace, monospace',
                body_font='"Fira Sans", system-ui, sans-serif',
                google_fonts=(
                    "Fira Code:wght@400;500;600;700",
                    "Fira Sans:wght@300;400;500;600;700",
                ),
                tags=("dashboard", "analytics", "data", "admin", "saas"),
            ),
            FontPairing(
                heading_font='"Exo", system-ui, sans-serif',
                body_font='"Roboto Mono", ui-monospace, monospace',
                google_fonts=(
                    "Exo:wght@300;400;500;600;700",
                    "Roboto Mono:wght@300;400;500;700",
                ),
                tags=("science", "research", "documentation", "data"),
            ),
        ),
    ),
    "editorial": MoodSpec(
        style="Storytelling-Driven",
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
        default_font_tags=(
            "media", "agency", "portfolio", "editorial", "magazine", "blog",
        ),
        alt_pairings=(
            FontPairing(
                heading_font='"Newsreader", Georgia, serif',
                body_font='"Roboto", system-ui, sans-serif',
                google_fonts=(
                    "Newsreader:wght@400;500;600;700",
                    "Roboto:wght@300;400;500;700",
                ),
                tags=(
                    "news", "blog", "magazine", "journalism", "media", "content",
                ),
            ),
            FontPairing(
                heading_font='"Libre Bodoni", Georgia, serif',
                body_font='"Public Sans", system-ui, sans-serif',
                google_fonts=(
                    "Libre Bodoni:wght@400;500;600;700",
                    "Public Sans:wght@300;400;500;600;700",
                ),
                tags=(
                    "magazine", "publication", "editorial", "journalism", "media",
                ),
            ),
        ),
    ),
    "playful": MoodSpec(
        style="Vibrant & Block-based",
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
        default_font_tags=(
            "entertainment", "food", "restaurant", "gaming", "playful", "events",
        ),
        alt_pairings=(
            FontPairing(
                heading_font='"Abril Fatface", Georgia, serif',
                body_font='"Merriweather", Georgia, serif',
                google_fonts=(
                    "Abril Fatface",
                    "Merriweather:wght@300;400;700",
                ),
                tags=(
                    "vintage", "brewery", "restaurant", "food",
                    "portfolio", "creative",
                ),
            ),
            FontPairing(
                heading_font='"Righteous", system-ui, sans-serif',
                body_font='"Poppins", system-ui, sans-serif',
                google_fonts=(
                    "Righteous",
                    "Poppins:wght@300;400;500;600;700",
                ),
                tags=(
                    "music", "entertainment", "events", "festival", "performance",
                ),
            ),
            FontPairing(
                heading_font='"Barlow Condensed", system-ui, sans-serif',
                body_font='"Barlow", system-ui, sans-serif',
                google_fonts=(
                    "Barlow Condensed:wght@400;500;600;700",
                    "Barlow:wght@300;400;500;600;700",
                ),
                tags=("sports", "fitness", "gym", "athletic"),
            ),
        ),
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


def _dark_palette(seed_hex: str) -> ColorPalette:
    """A dark-scheme palette: dark page + surfaces, light text, vivid brand
    primary/accent. Three dark shades give the section rhythm room (background =
    page, surface = elevated band, secondary = darkest/CTA band); text and band
    fonts come out light via the same `_text_for_background` path the light theme
    uses. Greyscale seeds fall back to a tasteful blue. WCAG guards still apply in
    build_theme (light text ≥ 7:1 on the dark page; button ≥ 4.5:1)."""
    h, _, s = _rgb_to_hls(*_hex_to_rgb(seed_hex))
    tint = 0.18 if s >= 0.12 else 0.05  # subtle hue tint in the neutrals
    background = _rgb_to_hex(*_hls_to_rgb(h, 0.11, tint))
    surface = _rgb_to_hex(*_hls_to_rgb(h, 0.17, tint))
    secondary = _rgb_to_hex(*_hls_to_rgb(h, 0.07, tint))
    if s >= 0.12:
        primary = _rgb_to_hex(*_hls_to_rgb(h, 0.60, max(s, 0.55)))
    else:
        primary = TAILWIND["blue"]["500"]
    accent_hex = _rotate_hue(primary, 150)
    ah, _, _ = _rgb_to_hls(*_hex_to_rgb(accent_hex))
    accent = _rgb_to_hex(*_hls_to_rgb(ah, 0.62, 0.70))
    text = _ensure_contrast_against(background, "#f8fafc", min_ratio=7.0)
    return ColorPalette(
        primary=primary,
        secondary=secondary,
        accent=accent,
        text=text,
        background=background,
        surface=surface,
    )


# Hand-designed palettes from the ui-ux-pro-max colour catalogue (MIT), each
# tagged with the IndustryCategory values it suits. Only light-background palettes
# are kept (our model is light bg + dark text + dark band); dark-themed catalogue
# entries are intentionally excluded. `dark` = catalogue Foreground, `tint` =
# catalogue Background.
_CURATED_PALETTES: tuple[CuratedPalette, ...] = (
    # restaurant
    CuratedPalette("Restaurant", ("restaurant",), "#DC2626", "#A16207", "#450A0A", "#FEF2F2"),
    CuratedPalette("Bakery / Cafe", ("restaurant",), "#92400E", "#B45309", "#78350F", "#FEF3C7"),
    CuratedPalette("Brewery / Winery", ("restaurant",), "#7C2D12", "#A16207", "#450A0A", "#FEF2F2"),
    # agency
    CuratedPalette("Creative Agency", ("agency",), "#EC4899", "#0891B2", "#831843", "#FDF2F8"),
    CuratedPalette("Design Studio", ("agency",), "#4F46E5", "#EA580C", "#312E81", "#EEF2FF"),
    CuratedPalette("Coworking / Studio", ("agency",), "#F59E0B", "#2563EB", "#78350F", "#FFFBEB"),
    # saas
    CuratedPalette("SaaS", ("saas",), "#2563EB", "#EA580C", "#1E293B", "#F8FAFC"),
    CuratedPalette("Analytics", ("saas",), "#1E40AF", "#D97706", "#1E3A8A", "#F8FAFC"),
    CuratedPalette("AI Platform", ("saas",), "#7C3AED", "#0891B2", "#1E1B4B", "#FAF5FF"),
    # professional-services
    CuratedPalette("B2B Service", ("professional-services",), "#0F172A", "#0369A1", "#020617", "#F8FAFC"),
    CuratedPalette("Legal", ("professional-services", "consultancy"), "#1E3A8A", "#B45309", "#0F172A", "#F8FAFC"),
    CuratedPalette("Real Estate", ("professional-services",), "#0F766E", "#0369A1", "#134E4A", "#F0FDFA"),
    CuratedPalette("Medical Clinic", ("professional-services",), "#0891B2", "#16A34A", "#134E4A", "#F0FDFA"),
    # ecommerce
    CuratedPalette("E-commerce", ("ecommerce",), "#059669", "#EA580C", "#064E3B", "#ECFDF5"),
    CuratedPalette("E-commerce Luxury", ("ecommerce",), "#1C1917", "#A16207", "#0C0A09", "#FAFAF9"),
    CuratedPalette("Subscription Box", ("ecommerce",), "#D946EF", "#EA580C", "#86198F", "#FDF4FF"),
    CuratedPalette("Marketplace", ("ecommerce",), "#7C3AED", "#16A34A", "#4C1D95", "#FAF5FF"),
    # consultancy
    CuratedPalette("Banking / Finance", ("consultancy",), "#0F172A", "#A16207", "#020617", "#F8FAFC"),
    CuratedPalette("Insurance", ("consultancy",), "#0369A1", "#16A34A", "#0C4A6E", "#F0F9FF"),
    # nonprofit
    CuratedPalette("Non-profit / Charity", ("nonprofit",), "#0891B2", "#EA580C", "#164E63", "#ECFEFF"),
    CuratedPalette("Community", ("nonprofit",), "#7C3AED", "#16A34A", "#4C1D95", "#FAF5FF"),
    CuratedPalette("Religious / Faith", ("nonprofit",), "#7C3AED", "#A16207", "#4C1D95", "#FAF5FF"),
    # personal
    CuratedPalette("Portfolio", ("personal",), "#18181B", "#2563EB", "#09090B", "#FAFAFA"),
    CuratedPalette("Freelancer", ("personal",), "#6366F1", "#16A34A", "#312E81", "#EEF2FF"),
    CuratedPalette("Magazine / Blog", ("personal",), "#18181B", "#EC4899", "#09090B", "#FAFAFA"),
)


def _palette_from_curated(c: CuratedPalette) -> ColorPalette:
    """Map a curated palette's source tokens onto the builder's 6-token palette,
    keeping our light-bg / dark-band invariants and the WCAG text guard."""
    background = "#ffffff"
    # Light section surface: the catalogue's page tint, clamped to stay clearly
    # light (so body text keeps contrast on alternating sections).
    surface = c.tint if _relative_luminance(c.tint) >= 0.9 else "#f8fafc"
    # `dark` doubles as the dark-band background and the body text colour.
    text = _ensure_contrast_against(background, c.dark, min_ratio=7.0)
    return ColorPalette(
        primary=c.primary,
        secondary=c.dark,
        accent=c.accent,
        text=text,
        background=background,
        surface=surface,
    )


def _has_brand_hue(seed_hex: str) -> bool:
    """True when the seed carries a usable brand hue (not greyscale). Mirrors the
    saturation threshold _nearest_tailwind_hue uses to fall back to generic blue."""
    return _rgb_to_hls(*_hex_to_rgb(seed_hex))[2] >= 0.12


def _curated_palette(
    seed_hex: str | None, industry: str | None, font_seed: str | None
) -> ColorPalette:
    """Pick a curated palette by industry fit, then by nearest hue to the brand
    seed (so the logo colour still steers the choice); the seed breaks ties. With
    no usable brand hue (no/greyscale logo), falls back to a font-seed-deterministic
    pick. Unknown/empty industries consider the whole set."""
    norm = (industry or "").strip().lower()
    candidates = [c for c in _CURATED_PALETTES if norm in c.categories] or list(
        _CURATED_PALETTES
    )
    if seed_hex and _has_brand_hue(seed_hex):
        seed_h = _rgb_to_hls(*_hex_to_rgb(seed_hex))[0] * 360.0

        def hue_dist(c: CuratedPalette) -> float:
            ph = _rgb_to_hls(*_hex_to_rgb(c.primary))[0] * 360.0
            return abs(((ph - seed_h + 180.0) % 360.0) - 180.0)

        nearest = min(hue_dist(c) for c in candidates)
        near = [c for c in candidates if hue_dist(c) - nearest < 1e-9]
        chosen = near[_seeded_index(font_seed, len(near))]
    else:
        chosen = candidates[_seeded_index(font_seed, len(candidates))]
    return _palette_from_curated(chosen)


# Maps the generator's controlled IndustryCategory vocabulary (and free-text
# industry strings) to keywords we look for in a FontPairing's tags. "other" and
# unknown industries yield no keywords → selection falls back to seeded variety.
_INDUSTRY_FONT_KEYWORDS: dict[str, tuple[str, ...]] = {
    "restaurant": ("restaurant", "food", "cafe", "dining", "brewery", "culinary"),
    "agency": ("agency", "creative", "portfolio", "studio", "brand", "media"),
    "saas": (
        "saas", "software", "startup", "tech", "app", "dashboard", "developer",
        "data", "analytics", "ai", "cli", "documentation",
    ),
    "professional services": (
        "professional", "corporate", "business", "consulting", "services",
        "finance", "legal", "real estate", "property",
    ),
    "ecommerce": (
        "ecommerce", "e-commerce", "shop", "retail", "store", "fashion",
        "jewelry", "jewellery", "product",
    ),
    "consultancy": (
        "consulting", "consultancy", "corporate", "business", "professional",
        "advisory", "finance",
    ),
    "nonprofit": ("nonprofit", "charity", "community", "cause", "social", "foundation"),
    "personal": (
        "personal", "portfolio", "blog", "creator", "resume", "music",
        "entertainment",
    ),
}


def _industry_keywords(industry: str | None) -> tuple[str, ...]:
    """Keywords to match against pairing tags for a given industry signal.

    Accepts both the controlled IndustryCategory values and free-text industry
    strings. Returns () for empty/"other"/unknown so selection degrades to the
    seeded variety pick.
    """
    if not industry:
        return ()
    norm = industry.strip().lower().replace("-", " ")
    if not norm or norm == "other":
        return ()
    kws = set(_INDUSTRY_FONT_KEYWORDS.get(norm, ()))
    # Fold in the industry's own words too, so free-text industries still match.
    kws.update(w for w in norm.split() if len(w) > 2)
    return tuple(kws)


def _seeded_index(font_seed: str | None, n: int) -> int:
    """Stable index in [0, n) from a seed (0 when no seed or single option).

    Uses a stable hash (not Python's per-process-salted `hash()`) so the same
    seed always resolves to the same index across process restarts.
    """
    if not font_seed or n <= 1:
        return 0
    return int(hashlib.sha256(font_seed.encode("utf-8")).hexdigest(), 16) % n


def resolve_color_scheme(
    override: str | None = None,
    brand_color_scheme: str | None = None,
    logo_is_light: bool | None = None,
) -> str:
    """Resolve light vs dark with SOP precedence (most explicit wins):

      1. `override` — an explicit per-generation choice (UI toggle / payload).
      2. `brand_color_scheme` — a stored brand preference.
      3. smart default — a predominantly *light* logo is usually drawn for a dark
         canvas, so default such brands to dark. The logo only sets the default;
         any explicit choice above overrides it (never a silent force).
      4. light.
    """
    for choice in (override, brand_color_scheme):
        if choice in ("light", "dark"):
            return choice  # type: ignore[return-value]
    return "dark" if logo_is_light else "light"


def _pick_pairing(
    spec: MoodSpec, font_seed: str | None, industry: str | None = None
) -> FontPairing:
    """Choose one pairing from the mood's pool.

    Selection order:
      1. **Industry fit** — prefer the pairing whose tags best match the site's
         industry. Ties are broken by the seed, so the choice stays meaningful
         *and* stable.
      2. **Seeded variety** — no pairing matches the industry (or no industry
         signal) → pick deterministically from the whole pool via `font_seed`, so
         same-mood sites still vary.
      3. **Default** — no seed and no match → the mood's default pairing (index 0),
         reproducing the original single-pairing behaviour.
    """
    pool = spec.font_pool
    keywords = _industry_keywords(industry)
    if keywords:
        def score(p: FontPairing) -> int:
            blob = " ".join(p.tags).lower()
            return sum(1 for k in keywords if k in blob)

        best = max(score(p) for p in pool)
        if best > 0:
            matches = [p for p in pool if score(p) == best]
            return matches[_seeded_index(font_seed, len(matches))]
    return pool[_seeded_index(font_seed, len(pool))]


def build_theme(
    seed_hex: str | None,
    mood: BrandMood = "modern",
    palette_mode: str = "tailwind",
    font_seed: str | None = None,
    industry: str | None = None,
    color_scheme: str = "light",
) -> ThemeTokens:
    """
    Top-level factory. `seed_hex` is the primary color (usually from the logo).
    Falls back to a tasteful default if missing.

    `palette_mode`:
      - "tailwind" (default): snap the brand hue to a curated Tailwind family —
        modern, accessible, on-trend colours.
      - "curated": pick a hand-designed, industry-tagged palette nearest the brand
        hue (see _curated_palette).
      - "auto": "curated" when the seed has no usable brand hue (greyscale / no
        logo colour, where "tailwind" would just fall back to generic blue),
        otherwise "tailwind".
      - "derive": the legacy free-form HSL derivation.
    Either way `mood` still drives typography, radius, and page width.

    `color_scheme="dark"` overrides the palette with a dark-scheme one (dark page +
    surfaces, light text, vivid brand primary/accent); typography/mood are unchanged
    and the existing band/rhythm machinery handles light text automatically.

    Font pairing is chosen from the mood's pool by (1) `industry` fit when given,
    then (2) `font_seed` (a stable per-site id, typically the brand name) for
    variety/tie-breaks. With neither, the mood's default pairing is used —
    preserving the original single-pairing behaviour.
    """
    raw = (seed_hex or "").strip().lower()
    has_seed = raw.startswith("#") and len(raw) == 7
    seed = raw if has_seed else "#2563eb"

    # A real, non-greyscale logo colour drives the brand-tailwind snap. No colour
    # at all (or greyscale) → "auto" prefers a curated industry palette over the
    # generic-blue fallback.
    brand_hue = has_seed and _has_brand_hue(seed)
    if color_scheme == "dark":
        # Dark scheme owns palette construction (the curated/Tailwind paths are
        # light-only); brand hue still drives the primary/accent.
        palette = _dark_palette(seed)
    elif palette_mode == "curated" or (palette_mode == "auto" and not brand_hue):
        palette = _curated_palette(seed if has_seed else None, industry, font_seed)
    elif palette_mode in ("tailwind", "auto"):
        palette = _snap_palette(seed)
    else:
        palette = _build_palette(seed, mood)
    spec = MOOD_SPECS[mood]
    pairing = _pick_pairing(spec, font_seed, industry)

    # Button background needs ≥4.5:1 against white text.
    button_bg = palette.primary
    button_text = _text_for_background(button_bg)
    if _contrast(button_bg, button_text) < 4.5:
        button_bg = _ensure_contrast_against(button_text, button_bg, min_ratio=4.5)

    typography = Typography.model_validate(
        {
            "headingFont": pairing.heading_font,
            "bodyFont": pairing.body_font,
            "google_fonts": list(pairing.google_fonts),
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
        display_font=pairing.display_font or pairing.heading_font,
        color_scheme=color_scheme,  # type: ignore[arg-type]
        style=spec.style,
    )
