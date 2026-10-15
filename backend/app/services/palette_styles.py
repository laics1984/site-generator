"""Bind newly generated color styles to the existing CSS-variable channel.

Only generator-owned, versioned outputs enter here. It never rewrites an
existing owner's schema, image URLs, copy, status colors or component options.
"""
from __future__ import annotations

import re
import math

from app.models.brand import ThemeTokens
from app.models.builder_schema import BuilderElement
from app.services.color_system import derive, oklch, luminance, ensure_contrast, contrast


def rendering_theme(theme: ThemeTokens) -> ThemeTokens:
    """A transient projection for legacy templates that use secondary as ink.

    The canonical supporting color stays in theme.palette and builder_styles;
    templates see the derived neutral while their token bindings are assembled.
    """
    if theme.color_system_version != 2:
        return theme
    colors = derive(theme.palette.model_dump())
    return theme.model_copy(update={"palette": theme.palette.model_copy(update={
        # The existing photo solver measures this same stable neutral scrim.
        # Solid sections are subsequently bound to the themed inverse role.
        "secondary": colors["photo-scrim"],
    })})


def bind_theme_styles(elements: list[BuilderElement], canonical: ThemeTokens,
                      rendered: ThemeTokens) -> None:
    if canonical.color_system_version != 2:
        return
    from app.services.style_tokens import band_tints, brand_ink, emphasis_ink, _muted
    colors = derive(canonical.palette.model_dump())
    palette = rendered.palette
    backgrounds = {
        palette.background.lower(): "background", palette.surface.lower(): "surface",
        palette.secondary.lower(): "surface-inverse", palette.primary.lower(): "primary",
        palette.accent.lower(): "accent",
    }
    for i, value in enumerate(band_tints(rendered), 1):
        backgrounds.setdefault(value.lower(), f"band-{i}")
    ink_roles = {palette.primary.lower(): "primary", brand_ink(rendered).lower(): "primary",
                 palette.accent.lower(): "accent", emphasis_ink(rendered).lower(): "accent"}
    values = {**backgrounds, palette.text.lower(): "text",
              _muted(palette.text).lower(): "text-muted", _muted(palette.secondary).lower(): "text-muted"}
    def token(name: str, fallback: str | None = None) -> str:
        return f"var(--builder-color-{name}, {fallback or colors.get(name, palette.text)})"

    def css(value: object, prop: str, photo: bool, surface: str | None = None) -> object:
        if not isinstance(value, str):
            return value
        if "data:image" in value:
            from app.services.section_content import _split_layers
            layers = _split_layers(value)
            if len(layers) == 1:
                return value
            return ", ".join(str(css(layer, prop, photo, surface)) for layer in layers)
        if "url(" in value and "gradient(" not in value:
            return value
        if not photo and "gradient(" in value and surface in ("background", "surface", "surface-inverse"):
            # Preserve gradient geometry, but keep its stops within one
            # luminance family so a palette edit cannot strand copy between
            # a bright brand stop and a dark one.
            role = "surface-inverse" if surface == "surface-inverse" else "surface"
            value = re.sub(r"var\(--builder-color-[a-z0-9-]+(?:,\s*#[0-9a-fA-F]{6})?\)|#[0-9a-fA-F]{6}\b",
                           lambda m: token(role), value)
            value = re.sub(r"rgba?\(\s*\d+\s*,\s*\d+\s*,\s*\d+(?:\s*,\s*([\d.]+))?\s*\)",
                           lambda m: f"color-mix(in srgb, {token(role)} {float(m[1] or 1)*100:g}%, transparent)", value)
            return value
        if "var(" in value:
            # Secondary in old catalog templates always meant neutral ink/band.
            if photo:
                value = re.sub(r"--builder-color-(primary|secondary)(?=[,\s)])", "--builder-color-photo-scrim", value)
            return re.sub(r"--builder-color-secondary(?=[,\s)])", "--builder-color-surface-inverse", value)
        def role_for(raw: str) -> str | None:
            if photo:
                if raw in (palette.secondary.lower(), palette.primary.lower()):
                    return "photo-scrim"
                if raw in (palette.background.lower(), palette.surface.lower()):
                    return "photo-wash" if luminance(raw) > .2 else "photo-scrim"
            return values.get(raw)
        def hex_value(match: re.Match) -> str:
            raw = match.group(0).lower()
            name = role_for(raw)
            return token(name, raw) if name else match.group(0)
        value = re.sub(r"#[0-9a-fA-F]{6}\b", hex_value, value)
        def rgba_value(match: re.Match) -> str:
            raw = "#"+"".join(f"{int(match[i]):02x}" for i in (1,2,3))
            name = role_for(raw)
            if not name:
                return match.group(0)
            alpha = float(match[4] or 1)
            return f"color-mix(in srgb, {token(name)} {alpha*100:g}%, transparent)"
        return re.sub(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)(?:\s*,\s*([\d.]+))?\s*\)", rgba_value, value)

    def walk(node: BuilderElement, surface: str = "background", in_photo: bool = False,
             fixed_surface: str | None = None, photo_ink: str = "photo-ink") -> bool:
        surrounding_surface = surface
        styles = dict(node.styles or {})
        from app.services.section_content import _has_real_photo, opaque_surface_of, surface_of, _parse_color, _to_hex
        # A `data:` URI is an icon or grain tile, not a photograph.
        own_photo = _has_real_photo(styles)
        photo = in_photo or own_photo
        if own_photo and node.painted_surface:
            photo_ink = "photo-ink" if contrast("#ffffff", node.painted_surface) >= 4.5 else "photo-ink-dark"
        own = styles.get("backgroundColor") or styles.get("background")
        uses_button = node.type == "link" and isinstance(own, str) and "--builder-button-background" in own
        known_fill = False
        # Small translucent brand chips must validate against their OWN tint,
        # not the section behind them. Use the existing opaque tint pair so it
        # remains readable even when the surrounding band changes on recolor.
        tint = None
        fill = _parse_color(own, rendered)
        if node.type in ("text", "link") and fill and 0 < fill[1] < .3:
            tint = next((name for name in ("primary", "accent")
                         if _to_hex(fill[0]) == getattr(palette, name).lower()), None)
            if tint:
                surface = f"{tint}-tint"
                own = styles["backgroundColor"] = token(surface)
                known_fill = True
                fixed_surface = None
                photo = False
        if isinstance(own, str):
            known_fill = known_fill or own.lower() in backgrounds
            surface = backgrounds.get(own.lower(), surface)
            found = re.search(r"var\(--builder-color-([a-z0-9-]+)(?=[,\s)])", own)
            if found and found[1] in colors:
                known_fill = True
                surface = "surface-inverse" if found[1] == "secondary" else found[1]
            if known_fill or uses_button:
                fixed_surface = None
        if not own_photo and (own or styles.get("backgroundImage")):
            # An opaque fill of its own is what this node's copy sits on, over
            # a photo or not: a solid hero button is inked for the BUTTON, not
            # the photo's scrim (white label on a white button otherwise).
            opaque = opaque_surface_of(node, rendered)
            if opaque:
                photo = False
                if not known_fill and not uses_button and "var(" not in str(own or "") and "gradient(" not in str(styles.get("backgroundImage") or own):
                    fixed_surface = opaque
            painted = opaque or (None if photo else surface_of(node, rendered))
            if painted and not known_fill and not uses_button:
                surface = "surface-inverse" if luminance(painted) < .35 else "surface"
        raw_ink = styles.get("color")
        node.styles = {key: css(value, key, photo, surface) for key, value in styles.items()}
        # Runtime blocks (menus/forms) inherit the pair for their actual band,
        # rather than assuming every label sits on the page background.
        if node.type != "text" and (own or styles.get("backgroundImage")) and f"on-{surface}" in colors:
            node.styles["--builder-local-ink"] = (ensure_contrast(palette.text, fixed_surface)
                                                  if fixed_surface else token(f"on-{surface}"))
            brand_role = f"primary-ink-on-{surface}"
            node.styles["--builder-local-brand-ink"] = (ensure_contrast(palette.primary, fixed_surface)
                    if fixed_surface else token(brand_role if brand_role in colors else f"on-{surface}"))
            focus_surface = surrounding_surface if node.type == "link" else surface
            node.styles["--builder-local-focus"] = token(f"primary-border-on-{focus_surface}")
            node.styles["--builder-local-error"] = (ensure_contrast("#dc2626", fixed_surface)
                                                    if fixed_surface else token(f"error-ink-on-{surface}"))
        if node.type == "link" and surface in ("primary", "secondary", "accent") and own:
            node.classes = ((node.classes or "") + " wt-theme-action").strip()
            for state in ("hover", "active"):
                node.styles[f"--builder-action-{state}"] = token(f"{surface}-{state}")
                node.styles[f"--builder-action-on-{state}"] = token(f"on-{surface}-{state}")
            node.styles["border"] = f"1px solid {token(surface+'-border-on-'+surrounding_surface)}"
        if node.type in ("text", "link") and isinstance(raw_ink, str):
            brand = None
            if photo and "url(" not in str(own or "") and not styles.get("backgroundColor"):
                # A fixed neutral photo pair preserves measured legibility on a
                # color-only update; photographic source/crop never changes.
                role = photo_ink
            else:
                brand = ink_roles.get(raw_ink.lower())
                if not brand and re.fullmatch(r"#[0-9a-fA-F]{6}", raw_ink):
                    _, chroma, hue = oklch(raw_ink)
                    if chroma > .04:
                        name, distance = min(((name, abs(math.atan2(math.sin(hue-oklch(getattr(palette,name))[2]), math.cos(hue-oklch(getattr(palette,name))[2])))) for name in ("primary", "accent")), key=lambda v: v[1])
                        if distance < .3:
                            brand = name
                if not brand:
                    for name in ("primary", "accent"):
                        if f"--builder-color-{name}" in raw_ink:
                            brand = name
                role = (f"{tint}-tint-ink" if tint and brand == tint else
                        f"on-{surface}" if tint else
                        f"{brand}-ink-on-{surface}" if brand else f"on-{surface}")
                if role not in colors:
                    role = f"on-{surface}"
            parsed_ink = _parse_color(raw_ink, rendered)
            neutral = parsed_ink is not None and oklch(_to_hex(parsed_ink[0]))[1] < .04
            if role in colors and (neutral or brand or raw_ink.lower() in values or "--builder-color-" in raw_ink or raw_ink.lower() in ink_roles):
                node.styles["color"] = (ensure_contrast(getattr(palette, brand) if brand else palette.text, fixed_surface)
                                        if fixed_surface else token(role))
                # Informative text must not inherit a decorative opacity that
                # invalidates its chosen pair. Photo washes remain on parents.
                node.styles.pop("opacity", None)
        if node.type == "link" and (uses_button or (surface == "primary" and own and not fixed_surface)):
            # Default primary actions follow the builder's existing button
            # customization channel, including an independently chosen fill.
            node.classes = " ".join(dict.fromkeys(((node.classes or "") + " wt-theme-action").split()))
            node.styles["backgroundColor"] = f"var(--builder-button-background, {canonical.buttons.background})"
            node.styles["color"] = f"var(--builder-button-text, {canonical.buttons.text})"
            node.styles["border"] = f"1px solid {token('primary-border-on-'+surrounding_surface)}"
            for state in ("hover", "active"):
                node.styles[f"--builder-action-{state}"] = f"var(--builder-button-{state})"
                node.styles[f"--builder-action-on-{state}"] = f"var(--builder-button-on-{state})"
        for variant in (node.responsiveStyles.mobile if node.responsiveStyles else None,
                        node.responsiveStyles.tablet if node.responsiveStyles else None):
            if isinstance(variant, dict):
                for key, value in list(variant.items()):
                    variant[key] = css(value, key, photo)
        has_copy = node.type in ("text", "link", "menu", "contactForm")
        if isinstance(node.content, list):
            for child in node.content:
                if isinstance(child, BuilderElement):
                    has_copy = walk(child, surface, photo, fixed_surface, photo_ink) or has_copy
        if has_copy:
            # Container opacity composites ALL descendants. Making a label
            # opaque cannot repair that inherited wash, so only new generated
            # copy-bearing groups lose it; decorative layers keep their alpha.
            try:
                opacity = str(node.styles.get("opacity", 1))
                if float(opacity.rstrip("%")) / (100 if "%" in opacity else 1) < 1:
                    node.styles.pop("opacity", None)
            except ValueError:
                pass
        if node.divider:
            for edge in (node.divider.top, node.divider.bottom):
                if edge and edge.color:
                    edge.color = str(css(edge.color, "backgroundColor", photo))
        return has_copy
    for element in elements:
        element.classes = ((element.classes or "") + " wt-color-system-v2").strip()
        walk(element)
