"""Color-only proposals and persistence; never rebuilds a page or chooses media."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import BaseModel, Field

from app.models.brand import CreativePalette, ThemeTokens
from app.models.builder_schema import GeneratedSite
from app.services.theme import apply_creative_palette
from app.services.color_system import derive, ensure_contrast


class PaletteRequest(BaseModel):
    site: GeneratedSite
    palette: CreativePalette
    save: bool = False


class RegeneratePaletteRequest(BaseModel):
    site: GeneratedSite
    design_direction: str | None = Field(default=None, max_length=1000)


class PalettePatch(BaseModel):
    # Deliberately no pages, media, navigation, SEO or component configuration.
    theme: ThemeTokens
    builder_styles: dict[str, Any]
    primary_color: str
    secondary_color: str


def current_theme(site: GeneratedSite) -> ThemeTokens:
    theme = ThemeTokens.model_validate(site.theme)
    if theme.color_system_version != 2 or (site.builder_styles or {}).get("colorSystemVersion") != 2:
        raise ValueError("AI recoloring supports sites made with the new color system. This older site's colors were kept.")
    return theme


def palette_patch(site: GeneratedSite, palette: CreativePalette) -> PalettePatch:
    original = current_theme(site)
    updated = apply_creative_palette(original, palette)
    styles = merge_palette_styles(site.builder_styles or {}, original, updated)
    # Compound backgrounds follow the live palette, so their foregrounds must
    # be remeasured too. The existing cssVars channel avoids rewriting pages or
    # expanding the color-only patch contract.
    from app.services.palette_styles import site_surface_foregrounds
    surface_vars = site_surface_foregrounds(site, updated, builder_styles=styles)
    if surface_vars:
        styles["cssVars"] = {**styles.get("cssVars", {}), **surface_vars}
    return PalettePatch(theme=updated, builder_styles=styles,
                        primary_color=updated.palette.primary, secondary_color=updated.palette.secondary)


def merge_palette_styles(existing: dict, original: ThemeTokens, updated: ThemeTokens) -> dict:
    """Keep owner overrides, typography, brand baseline and every non-color key."""
    styles = deepcopy(existing)
    generated = updated.to_builder_styles()
    styles["colorSystemVersion"] = 2
    styles["colors"] = {**styles.get("colors", {}), **generated["colors"]}
    buttons = styles.setdefault("buttons", {})
    if buttons.get("background") in (None, original.palette.primary):
        previous_default_ink = derive(original.palette.model_dump())["on-primary"]
        chosen_ink = buttons.get("text")
        buttons["background"] = generated["buttons"]["background"]
        buttons["text"] = (generated["buttons"]["text"] if chosen_ink in (None, previous_default_ink)
                           else ensure_contrast(chosen_ink, updated.palette.primary))
    page = styles.setdefault("page", {})
    if page.get("background") in (None, original.palette.background):
        page["background"] = updated.page.background
    return styles


def patched_site(site: GeneratedSite, patch: PalettePatch) -> GeneratedSite:
    return site.model_copy(update=patch.model_dump())


def design_inputs(source: Any, brand: Any, detected: Any = None) -> dict:
    observed = list(getattr(brand, "extracted_palette", []) or [])
    genuine_mark = getattr(brand, "logo_source", None) != "og-image"
    images = list(getattr(source, "image_metadata", []) or [])
    measured = [m.dominant_color for m in images if m.dominant_color]
    for metadata in images:
        review = getattr(metadata, "facebook_photo_review", None)
        if review is not None:
            measured.extend(getattr(review, "palette", []) or [])
    return {
        "audience": getattr(detected, "audience", None),
        "brand_colors": observed[:6] if genuine_mark else [],
        "reference_colors": list(getattr(source, "reference_colors", []) or [])[:8],
        "image_colors": (measured + ([] if genuine_mark else observed))[:6],
        "business_context": (getattr(detected, "brand_summary", None) or getattr(source, "description", None)
                             or getattr(source, "raw_text", ""))[:1600],
        # A pasted brief may contain explicit visual preferences. It remains
        # design evidence only, never new factual copy supplied to the planner.
        "design_direction": ((getattr(source, "raw_text", "") or "")[:1000] or None) if getattr(source, "source_kind", "") == "paste" else None,
    }


async def save_preview_palette(site: GeneratedSite, patch: PalettePatch) -> None:
    if not site.generation_project_id:
        return
    from app.services.generation_projects import save_project_palette
    await save_project_palette(site.generation_project_id, patch, unpublished_only=True)
