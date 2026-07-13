"""
Brand identity + design tokens.

ThemeTokens.to_builder_styles() emits the exact shape webtree's builder expects
(see webtree/builder/src/lib/builder-styles.ts → BuilderStyles). Keep these in
lockstep — drift breaks editor theme rendering.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


BrandMood = Literal[
    "modern",      # clean, minimalist — SaaS, fintech, tech
    "luxury",      # elegant, premium — hospitality, jewellery, real estate
    "friendly",    # warm, approachable — consumer, lifestyle, wellness
    "technical",   # precise, structured — engineering, B2B, dev tools
    "editorial",   # storytelling, magazine-feel — media, agencies, portfolios
    "playful",     # energetic, bold — entertainment, food, kids, gaming
]


PageWidthMode = Literal["contained", "full"]

# Mirrors MotionIntensity in webtree/builder/src/lib/site-navigation.ts. The
# public renderer scales every motion preset's distance/duration by this;
# "off" disables motion site-wide.
MotionIntensity = Literal["off", "subtle", "balanced", "expressive"]

# Default motion personality per brand mood. Reserved moods (technical,
# editorial) get quieter reveals; playful brands get showier ones. Sites can
# always change this in the builder's Styles tab afterwards.
MOOD_MOTION_INTENSITY: dict[BrandMood, MotionIntensity] = {
    "modern": "balanced",
    "luxury": "balanced",
    "friendly": "balanced",
    "technical": "subtle",
    "editorial": "subtle",
    "playful": "expressive",
}


_HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def _validate_hex(value: object) -> str:
    if not isinstance(value, str) or not _HEX.match(value):
        raise ValueError(f"must be a 6-digit hex color like '#2563eb', got {value!r}")
    return value.lower()


class ColorPalette(BaseModel):
    """
    The 6 tokens the webtree builder understands.

    Constraints we enforce on construction:
    - primary is the brand color (from logo)
    - text vs background must meet WCAG AA (4.5:1) for body copy
    - surface is a near-background tint, slightly tinted toward primary
    """

    primary: str
    secondary: str
    accent: str
    text: str
    background: str
    surface: str

    @field_validator("primary", "secondary", "accent", "text", "background", "surface")
    @classmethod
    def _hex_only(cls, v: object) -> str:
        return _validate_hex(v)


class Typography(BaseModel):
    """
    Font stacks. Each value is a complete CSS font-family string with fallbacks,
    so the public site renders even before Google Fonts loads.
    """

    heading_font: str = Field(alias="headingFont")
    body_font: str = Field(alias="bodyFont")
    # Google Font family names (CSV) for the <link> loader. None = system-only.
    google_fonts: list[str] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class Buttons(BaseModel):
    background: str
    text: str
    radius: int = Field(ge=0, le=48, description="Border radius in px, 0-48")


class PageTokens(BaseModel):
    width_mode: PageWidthMode = Field(default="contained", alias="widthMode")
    max_width: int = Field(default=1280, ge=320, le=1920, alias="maxWidth")
    background: str

    model_config = ConfigDict(populate_by_name=True)


# Bounded min-height for the "banded" hero. The full-bleed hero template falls
# back to min(100dvh, 900px) when this token is absent, so omitting it (the
# "full" default) keeps existing sites full-screen and byte-identical.
HERO_BANDED_MIN_HEIGHT = "460px"

# Site-wide hero photo-background height. "full" = full-bleed full-screen hero;
# "banded" = bounded-height full-bleed photo hero.
HeroBackgroundHeight = Literal["full", "banded"]


class ThemeTokens(BaseModel):
    """
    Full design system for a generated site.

    Mirrors webtree's BuilderStyles plus extras (spacing scale, section rhythm)
    that schema_builder uses internally but the builder doesn't need to know.
    """

    palette: ColorPalette
    typography: Typography
    buttons: Buttons
    page: PageTokens

    mood: BrandMood = "modern"
    # Light vs dark scheme. Drives palette construction in build_theme; the band
    # rhythm + text colours follow automatically. Renderers need nothing extra —
    # the dark hexes flow through the existing builderStyles colour tokens.
    color_scheme: Literal["light", "dark"] = "light"
    # The ui-ux-pro-max style this mood embodies (design lineage / debug metadata).
    style: str = ""
    # Section background rotation drives visual rhythm (avoid wall-of-white).
    # Indices reference palette keys: "background", "surface", "primary".
    section_rotation: list[Literal["background", "surface", "primary"]] = Field(
        default_factory=lambda: ["background", "surface", "background"]
    )
    # Whether to use inverted (dark) sections for CTAs.
    inverted_cta: bool = True

    # --- 2025/26 trend tokens (internal; mostly not part of BuilderStyles) ------
    # These drive schema_builder's modern style vocabulary. Most are NOT emitted
    # in to_builder_styles() because the webtree builder doesn't need them — they
    # only shape the inline styles/classes we bake into the BuilderElement tree.
    # background_strategy is the exception: it IS emitted (as `backgroundTexture`)
    # because the builder's per-section override control needs the theme default
    # to render its "Theme default" state and to recompute grain/mesh client-side
    # when a section's own `BuilderElement.backgroundTexture` overrides it.
    type_scale_ratio: float = Field(
        default=1.25,
        ge=1.1,
        le=1.6,
        description="Modular type-scale ratio. >1.25 → larger, more expressive display type.",
    )
    use_glass: bool = Field(
        default=False,
        description="Apply frosted-glass (backdrop-filter) treatment to cards/overlays.",
    )
    background_strategy: Literal["flat", "mesh", "grain", "mesh+grain"] = Field(
        default="flat",
        description="Decorative section background: flat color, aurora mesh gradient, grain, or both.",
    )
    shadow_scale: Literal["soft", "elevated", "dramatic"] = Field(
        default="soft",
        description="Depth of the card/elevation shadow vocabulary.",
    )
    display_font: str | None = Field(
        default=None,
        description="Optional oversized display font stack for hero headlines; falls back to heading_font.",
    )
    motion_intensity: MotionIntensity | None = Field(
        default=None,
        description="Site-wide motion intensity. None → derived from mood (MOOD_MOTION_INTENSITY).",
    )
    # Hero photo-background height, a site-wide choice. "full" = full-bleed
    # full-screen hero; "banded" = bounded-height full-bleed photo hero. Part of
    # BuilderStyles (emitted below) so it's editable globally in the builder.
    hero_background_height: HeroBackgroundHeight = "full"

    def to_builder_styles(self) -> dict[str, Any]:
        """Serialize as the exact `BuilderStyles` shape the webtree builder expects."""
        colors = {
            "primary": self.palette.primary,
            "secondary": self.palette.secondary,
            "accent": self.palette.accent,
            "text": self.palette.text,
            "background": self.palette.background,
            "surface": self.palette.surface,
        }
        styles: dict[str, Any] = {
            "colors": colors,
            # Preserved brand baseline. `colors` is the active palette the
            # builder mutates via tone presets / manual edits; `brand` is the
            # immutable anchor the builder's "Brand" reset reverts to. Seeded
            # equal to `colors` here — the generated palette IS the brand.
            # Builder-only; the public renderer never reads it.
            "brand": dict(colors),
            "typography": {
                "headingFont": self.typography.heading_font,
                "bodyFont": self.typography.body_font,
                # Carry the Google Fonts CSV so the builder editor and the public
                # site can load the web fonts (rides the existing builderStyles
                # push; stored as flexible JSON, no CMS migration needed).
                "googleFonts": list(self.typography.google_fonts),
            },
            "buttons": {
                "background": self.buttons.background,
                "text": self.buttons.text,
                "radius": self.buttons.radius,
            },
            "page": {
                "widthMode": self.page.width_mode,
                "maxWidth": self.page.max_width,
                "background": self.page.background,
            },
            "motion": {
                "intensity": self.motion_intensity
                or MOOD_MOTION_INTENSITY.get(self.mood, "balanced"),
            },
            "backgroundTexture": self.background_strategy,
            # The brand mood the theme was built for. The builder's section
            # browser reads it to hide catalog templates whose `moods` gate
            # excludes this brand (same gate the generator's mood_allows
            # enforces) — e.g. playful kindergarten blocks never surface while
            # editing a law firm. Builder-only; the public renderer ignores it.
            "brandMood": self.mood,
        }
        # Only emit the hero token when banded; absence → the template's
        # full-screen fallback, so "full" sites stay byte-identical.
        if self.hero_background_height == "banded":
            styles["hero"] = {"minHeight": HERO_BANDED_MIN_HEIGHT}
        return styles


class BrandIdentity(BaseModel):
    """Everything we know about the brand before we start building pages."""

    name: str
    tagline: str | None = None
    logo_url: str | None = None
    logo_data_url: str | None = Field(
        default=None,
        description=(
            "Base64 data URL for the uploaded logo. Used directly in the header "
            "BuilderElement so the builder gets a self-contained image even before "
            "we've persisted to S3/CDN."
        ),
    )
    extracted_palette: list[str] = Field(
        default_factory=list,
        description="Hex colors extracted from the logo, ordered by dominance.",
    )
    logo_is_light: bool | None = Field(
        default=None,
        description=(
            "Whether the visible logo mark itself is predominantly light-colored. "
            "Used to choose a contrast-safe header background."
        ),
    )
    mood: BrandMood | None = None
    industry: str | None = None
    # Optional light/dark preference (e.g. set by the frontend or an LLM cue).
    # None → light. Threaded into build_theme on the generation path.
    color_scheme: Literal["light", "dark"] | None = None
