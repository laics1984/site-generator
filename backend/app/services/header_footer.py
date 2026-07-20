"""
Themed header + footer BuilderElement trees.

The header carries the logo, primary nav (anchored to the body sections via
hash links), and a primary CTA. The footer carries the logo, nav columns,
contact info, legal links, and photo credits.

Both pull all colours / radii / spacing from the ThemeTokens so the result
feels like one cohesive design system, not a stack of independent sections.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from app.models.brand import BrandIdentity, ThemeTokens
from app.models.builder_schema import (
    BuilderElement,
    BuilderElementContent,
    PageNode,
    ResponsiveStyles,
)
from app.models.design_manifest import (
    SELF_CHROME_HEADERS,
    FooterArchetype,
    HeaderArchetype,
)
from app.services.template_filler import (
    fill_chrome_template,
    get_template,
    resolve_chrome_tokens,
)
from app.services.theme import (
    _contrast,
    _ensure_contrast_against,
    _text_for_background,
)


def _rgba(hex_color: str, alpha: float) -> str:
    """rgba() string for a #rrggbb hex — used for translucent chrome/hairlines."""
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f"rgba({r}, {g}, {b}, {alpha})"

# The builder's HeaderSettings "Divider" control is a boxShadow preset on the
# __header root element. This is the exact "Subtle" preset value
# (HEADER_SHADOW_PRESETS in builder src/lib/site-navigation.ts) — emitting the
# same string makes the builder's Divider panel show "Subtle" as selected.
HEADER_DIVIDER_SUBTLE = "0 1px 3px 0 rgba(15, 23, 42, 0.06)"

# Default header logo height; some industries lead with a larger, more
# prominent mark (childcare: warm, friendly, front-and-centre for parents).
_DEFAULT_LOGO_HEIGHT = "52px"
_LOGO_HEIGHT_BY_INDUSTRY: dict[str, str] = {"childcare": "68px"}

# Industries whose logo must never get the contrast "chip" (a boxed background
# behind the mark) — it reads as an unwanted border against their light chrome.
_NO_LOGO_LOCKUP_INDUSTRIES = frozenset({"childcare"})


def _uid() -> str:
    return str(uuid4())


def _text(
    inner: str,
    *,
    name: str = "Text",
    styles: dict[str, Any] | None = None,
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name=name,
        type="text",
        styles={"width": "auto", **(styles or {})},
        content=BuilderElementContent(innerText=inner),
    )


def _container(
    children: list[BuilderElement],
    *,
    name: str = "Container",
    styles: dict[str, Any] | None = None,
    mobile: dict[str, Any] | None = None,
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name=name,
        type="container",
        styles={
            "display": "flex",
            "flexDirection": "column",
            "width": "100%",
            **(styles or {}),
        },
        content=children,
        responsiveStyles=ResponsiveStyles(mobile=mobile) if mobile else None,
    )


def _menu_element(
    *,
    slot: str,
    variant: str,
    label: str,
    color_mode: str = "manual",
    styles: dict[str, Any] | None = None,
) -> BuilderElement:
    """A shared-menu block. The builder resolves its items from the entity's
    ``menus[]`` via ``slot`` + region, so navigation stays editable in the
    builder's Menus panel instead of being baked into hardcoded links."""
    return BuilderElement(
        id=_uid(),
        name="Menu",
        type="menu",
        styles={"width": "100%", **(styles or {})},
        content=BuilderElementContent(
            slot=slot,
            variant=variant,
            menuLabel=label,
            colorMode=color_mode,
        ),
    )


def _image(
    src: str,
    alt: str,
    *,
    name: str = "Brand Logo",
    styles: dict[str, Any] | None = None,
    href: str | None = None,
    aria_label: str | None = None,
) -> BuilderElement:
    # The element NAME matters: the builder only treats a header image as the
    # brand logo (auto aspect, no min-height, no cover-crop) when its name
    # matches /brand/i — see builder image-component.tsx `isBrandLogoPlaceholder`
    # and createBrandElement, which names the slot "Brand". A name like "Logo"
    # falls through to the generic content-image frame (120px min-height +
    # object-fit:cover), which renders the logo as a cropped band. Default to
    # "Brand Logo" so the logo is recognised and delete-protected in the header.
    return BuilderElement(
        id=_uid(),
        name=name,
        type="image",
        styles=styles or {"height": "32px", "width": "auto"},
        content=BuilderElementContent(
            src=src, alt=alt, href=href, ariaLabel=aria_label
        ),
    )


def _logo_mark(
    brand: BrandIdentity,
    theme: ThemeTokens,
    lockup: str | None = None,
    ink: str | None = None,
    logo_height: str = "52px",
) -> BuilderElement:
    """
    Returns a logo BuilderElement — either the uploaded image or a typographic
    monogram. Both are themed against the palette. When `lockup` is set, the
    image sits in a contrast chip so it stays legible on header chrome that is
    too close to the logo's own brightness. `ink` colours the typographic
    wordmark so it matches the header's menu ink (falls back to secondary).
    """
    if brand.logo_url or brand.logo_data_url:
        # The logo IS the home link — standard convention, and it lets the
        # primary menu drop the redundant "Home" item entirely.
        img = _image(
            brand.logo_url or brand.logo_data_url or "",
            alt=brand.name,
            # Big by default — the header starts prominent and shrinks to ~80%
            # on scroll (shrinkOnScroll). Height-scaled with width auto so the
            # renderer keeps the logo's aspect (see webtree-public ImageBlock
            # brand-logo branch).
            styles={"height": logo_height, "width": "auto", "display": "block"},
            href="/",
            aria_label=f"{brand.name} — home",
        )
        if lockup:
            return _container(
                [img],
                name="Logo lockup",
                styles={
                    "backgroundColor": lockup,
                    "paddingTop": "6px",
                    "paddingBottom": "6px",
                    "paddingLeft": "12px",
                    "paddingRight": "12px",
                    "borderRadius": "10px",
                    "display": "inline-flex",
                    "alignItems": "center",
                    "width": "auto",
                },
            )
        return img

    # Typographic mark: first letter in a circle in primary color.
    initial = brand.name.strip()[:1].upper() or "•"
    return _container(
        [
            _container(
                [
                    _text(
                        initial,
                        name="Monogram",
                        styles={
                            "color": theme.buttons.text,
                            "fontWeight": 700,
                            "fontSize": "24px",
                            "lineHeight": 1,
                        },
                    )
                ],
                name="Mark",
                # Matches the image-logo height (52px) so the text and image
                # brand marks read at the same scale.
                styles={
                    "width": "48px",
                    "height": "48px",
                    "borderRadius": "9999px",
                    "backgroundColor": theme.palette.primary,
                    "alignItems": "center",
                    "justifyContent": "center",
                    "display": "flex",
                },
            ),
            # Wordmark is a link to home — same convention as the image logo.
            BuilderElement(
                id=_uid(),
                name="Wordmark",
                type="link",
                styles={
                    "fontFamily": theme.typography.heading_font,
                    "fontWeight": 700,
                    "fontSize": "18px",
                    "color": ink or theme.palette.secondary,
                    "textDecoration": "none",
                },
                content=BuilderElementContent(
                    innerText=brand.name, href="/", ariaLabel=f"{brand.name} — home"
                ),
            ),
        ],
        name="Brand",
        styles={
            "flexDirection": "row",
            "alignItems": "center",
            "gap": "10px",
            "width": "auto",
        },
    )


def _header_chrome(
    brand: BrandIdentity,
    theme: ThemeTokens,
    industry: str | None = None,
) -> tuple[str, str, str | None]:
    """
    Choose a header background / foreground that keeps the logo and nav
    readable. The header follows the theme's own scheme — a light theme gets a
    light header with near-black ink, a dark theme a dark header with white ink
    — so the menu ink is consistent site-wide instead of flipping with the
    homepage hero. Separation from a same-band hero comes from the subtle
    divider shadow on the header root, not from a band flip.

    The 3rd value, `logo_lockup`, is a contrast chip for uploaded logos only
    when the logo would blend into the actual header background: dark header +
    dark logo => white chip; light header + light logo => dark chip.
    """
    background = theme.palette.background
    foreground = _text_for_background(background)
    if _contrast(background, foreground) < 4.5:
        foreground = _ensure_contrast_against(background, foreground, min_ratio=4.5)

    header_is_dark = foreground == "#ffffff"
    lockup = None
    # Childcare brief: a clean logo, no chip. The pastel light header + a bright
    # (logo_is_light) mark would otherwise get a dark contrast box that reads as
    # an unwanted border — suppress it and let the logo sit free on the header.
    if (industry or "").strip().lower() in _NO_LOGO_LOCKUP_INDUSTRIES:
        return background, foreground, None
    if brand.logo_is_light is False and header_is_dark:
        lockup = "#ffffff"
    elif brand.logo_is_light is True and not header_is_dark:
        lockup = theme.palette.secondary

    return background, foreground, lockup


# --- header ---------------------------------------------------------------------


def build_header(
    brand: BrandIdentity,
    theme: ThemeTokens,
    nav_items: list[tuple[str, str]],  # (label, href) — used as a fallback when page_tree is None
    primary_cta: tuple[str, str] | None = None,
    page_tree: list[PageNode] | None = None,
    overlay: bool = False,
    industry: str | None = None,
    archetype: HeaderArchetype = "classic",
    social_links: list[tuple[str, str]] | None = None,
) -> BuilderElement:
    """
    Sticky header: logo · nav · CTA, max-width contained. Chrome follows the
    theme scheme (see _header_chrome) so the menu ink is one consistent
    white-or-near-black choice site-wide; the bottom edge carries the builder's
    "Subtle" divider shadow preset instead of a hard border.

    Navigation is rendered as a shared ``menu`` element bound to the ``primary``
    slot — the builder resolves its items from the entity's ``menus[]`` (built in
    ``menu_builder.build_menus`` from the page tree) and handles the desktop
    inline layout plus the mobile hamburger collapse on its own. ``nav_items`` /
    ``page_tree`` are no longer consumed here; they drive the menu list upstream.

    ``overlay`` marks a header that floats transparent over full-bleed heroes
    and solidifies on scroll. The element ALWAYS carries its real solid chrome
    — the renderer restores exactly these styles when it solidifies
    (pickRootBackgroundStyles in webtree-public) and strips them during the
    transparent phase, forcing white ink on the ``wt-header-ink`` elements
    stamped below. Never emit a transparent backgroundColor here: it would
    make the solidified header transparent too.
    """
    norm_industry = (industry or "").strip().lower()
    header_bg, header_fg, logo_lockup = _header_chrome(brand, theme, industry)
    if (
        overlay
        and brand.logo_is_light is False
        and norm_industry not in _NO_LOGO_LOCKUP_INDUSTRIES
    ):
        # A dark image logo floating over a dark full-bleed hero needs its
        # contrast chip even if the solid header wouldn't (the renderer can
        # recolor text ink, not bitmaps).
        logo_lockup = logo_lockup or "#ffffff"
    # The logo is the one computed subtree the shared catalog can't express
    # (image vs monogram vs contrast-chip lockup) — built here, injected via
    # the template's `$subtree: "logo"` node. It carries the ink marker: while
    # an overlay header is transparent the renderer forces `wt-header-ink`
    # elements to white (.wt-page-header--overlay .wt-header-ink). Self-chrome
    # archetypes (floating pill) skip the marker — their bar chromes itself
    # during overlay, so a white flip would break on the light pill.
    logo = _logo_mark(
        brand,
        theme,
        lockup=logo_lockup,
        ink=header_fg,
        logo_height=_LOGO_HEIGHT_BY_INDUSTRY.get(norm_industry, _DEFAULT_LOGO_HEIGHT),
    )
    if archetype not in SELF_CHROME_HEADERS:
        logo.classes = "wt-header-ink"

    # --- materialize from the shared catalog ---------------------------------
    # The per-archetype layout (bar structure, chrome, ghost vs solid CTA, the
    # divider/borderless rules, wt-header-ink markers on static nodes) lives in
    # the shared section catalog (chrome-header-*), single-sourced with the
    # builder. This function contributes only what a static template cannot:
    # the computed logo subtree, the CTA content, and the resolved theme tokens.
    template = get_template(f"chrome-header-{archetype}")
    if template is None:  # pragma: no cover — catalog out of sync
        raise ValueError(f"chrome-header-{archetype} missing from section catalog")
    content: dict[str, Any] = {
        "logo": logo,
        "has_social": bool(social_links),
    }
    if primary_cta:
        label, href = primary_cta
        content["cta"] = {"innerText": label, "href": href}
    header = fill_chrome_template(template, content)
    return resolve_chrome_tokens(header, _header_tokens(theme, header_bg, header_fg))


def _header_tokens(
    theme: ThemeTokens, header_bg: str, header_fg: str
) -> dict[str, str]:
    """The header half of the chrome token contract (see template_filler)."""
    return {
        "header.bg": header_bg,
        "header.fg": header_fg,
        "header.bg@72": _rgba(header_bg, 0.72),
        "header.bg@88": _rgba(header_bg, 0.88),
        "header.fg@10": _rgba(header_fg, 0.10),
        "header.fg@12": _rgba(header_fg, 0.12),
        "header.fg@35": _rgba(header_fg, 0.35),
        "divider.subtle": HEADER_DIVIDER_SUBTLE,
        "buttons.bg": theme.buttons.background,
        "buttons.fg": theme.buttons.text,
        "buttons.radiusPx": f"{theme.buttons.radius}px",
        "pill.radiusPx": f"{max(20, theme.buttons.radius + 14)}px",
        "pill.maxWidthPx": f"{max(720, theme.page.max_width - 96)}px",
        "page.maxWidthPx": f"{theme.page.max_width}px",
        "font.heading": theme.typography.heading_font,
        "font.body": theme.typography.body_font,
    }


# --- footer ---------------------------------------------------------------------


def build_footer(
    brand: BrandIdentity,
    theme: ThemeTokens,
    nav_items: list[tuple[str, str]],
    contact: dict[str, str] | None = None,
    media_credits: list[str] | None = None,
    page_tree: list[PageNode] | None = None,
    extra_legal_nav: list[tuple[str, str]] | None = None,
    social_links: list[tuple[str, str]] | None = None,
    archetype: FooterArchetype = "mega",
    primary_cta: tuple[str, str] | None = None,
) -> BuilderElement:
    """
    Themed footer with grouped sub-page navigation.

    ``archetype`` selects the layout philosophy (see models/design_manifest.py):
      * ``mega`` — dark brand column + grouped nav columns + legal bar (legacy;
        byte-identical to pre-archetype output)
      * ``cta-banner`` — a conversion banner (headline + primary CTA) above the
        mega grid; degrades to ``mega`` when no ``primary_cta`` is given
      * ``minimal-centered`` — calm centered column on the theme's light band
      * ``editorial`` — oversized ghost wordmark on the light band, slim nav row

    Layout of the nav grid depends on what's in the page_tree:
      * Brand column (logo + tagline) — always
      * One column per top-level page that has children (Services / Case Studies / …)
      * "Company" column for top-level pages without children (About / Contact / …)
      * "Legal" column for privacy / terms (always at the right edge)
      * Contact info appears under the brand column

    Flat sites (no children anywhere) fall back to the simpler three-column
    layout (brand + explore + contact) for visual balance.
    """
    if archetype == "cta-banner" and not primary_cta:
        archetype = "mega"

    # Chrome per archetype: the dark archetypes sit on `secondary` (the dark,
    # primary-hued neutral) with white ink; the light archetypes sit on
    # `surface` with the band's contrast-correct ink. On a dark color scheme
    # `surface` is itself dark, so the "light" archetypes stay coherent there.
    if archetype in ("minimal-centered", "editorial"):
        footer_bg = theme.palette.surface
    else:
        footer_bg = theme.palette.secondary
    ink = _text_for_background(footer_bg)
    footer_is_dark = ink == "#ffffff"
    # Exact legacy literals on dark so the default mega output stays
    # byte-identical; _rgba formatting (with spaces) only reaches new archetypes.
    on_dark_text = ink
    on_dark_muted = "rgba(255,255,255,0.65)" if footer_is_dark else _rgba(ink, 0.65)

    # ---- content composition ---------------------------------------------------
    # Structure lives in the shared catalog (chrome-footer-*); this function
    # decides WHAT appears (the conditional content contract) and resolves the
    # theme tokens. When a usable logo exists we show it alone (mirroring the
    # header); the text wordmark appears only when there is no logo, or the
    # logo would vanish into the footer band (dark on dark / light on light).
    logo_src = brand.logo_url or brand.logo_data_url
    show_wordmark = (not logo_src) or (
        brand.logo_is_light is (False if footer_is_dark else True)
    )
    logo_el: BuilderElement | None = None
    if logo_src:
        # In the footer this is a plain content image — the builder's dedicated
        # logo sizing is header-only (isBrandLogoPlaceholder requires source ===
        # 'header'). The generic image frame defaults to 120px tall + object-fit:
        # cover, and a width of "auto" collapses to 0px because the inner fill is
        # absolutely positioned and contributes no intrinsic width. So pin an
        # explicit width + height, object-fit:contain (no crop), left-aligned,
        # and min-height 0.
        logo_el = _image(
            logo_src,
            alt=brand.name,
            styles={
                "height": "40px",
                "minHeight": "0px",
                "width": "150px",
                "objectFit": "contain",
                "objectPosition": "left",
                "display": "block",
            },
        )

    # Whether to emit a legal menu element: only when there are privacy/terms
    # pages, otherwise the block renders the builder's "assign a menu" stub.
    has_legal = any(
        href.lstrip("/").lower() in ("privacy", "terms")
        for _, href in (extra_legal_nav or [])
    ) or bool(
        page_tree
        and any(node.slug.lower() in ("privacy", "terms") for node in page_tree)
    )

    from datetime import datetime

    year = datetime.now().year
    content: dict[str, Any] = {
        "logo": logo_el,
        "brand_wordmark": brand.name if show_wordmark else None,
        "tagline": brand.tagline or None,
        # `_name` keeps the legacy per-key element names ("Email", "Phone").
        "contact_lines": [
            {"_name": key.capitalize(), "text": f"{key.capitalize()}: {value}"}
            for key, value in (contact or {}).items()
        ],
        # Only the editorial tree binds this slot; other archetypes ignore it.
        "ghost_wordmark": brand.name,
        "copyright": f"© {year} {brand.name}. All rights reserved.",
        "credits": " · ".join(media_credits) if media_credits else None,
        "has_legal": has_legal,
        "has_social": bool(social_links),
    }
    if archetype == "cta-banner":
        # Headline prefers the brand's own tagline; the button is the same
        # primary CTA the header carries, so the site closes on the action it
        # opened with. (No-CTA callers were already degraded to mega above.)
        cta_label, cta_href = primary_cta  # type: ignore[misc]  # guarded above
        content["cta_headline"] = (
            brand.tagline
            if brand.tagline and len(brand.tagline) <= 90
            else "Ready when you are."
        )
        content["cta"] = {"innerText": cta_label, "href": cta_href}

    # --- materialize from the shared catalog ---------------------------------
    # Layout (grid vs centered stack vs CTA banner vs ghost wordmark, hairline
    # placement, legal-bar alignment) lives in the shared section catalog
    # (chrome-footer-*), single-sourced with the builder.
    template = get_template(f"chrome-footer-{archetype}")
    if template is None:  # pragma: no cover — catalog out of sync
        raise ValueError(f"chrome-footer-{archetype} missing from section catalog")
    footer = fill_chrome_template(template, content)
    return resolve_chrome_tokens(
        footer, _footer_tokens(theme, footer_bg, ink, footer_is_dark)
    )


def _footer_tokens(
    theme: ThemeTokens, footer_bg: str, ink: str, footer_is_dark: bool
) -> dict[str, str]:
    """The footer half of the chrome token contract (see template_filler).

    Exact legacy literals on dark bands so the default mega output stays
    byte-identical; the _rgba formulas only reach light-band archetypes.
    """
    return {
        "footer.bg": footer_bg,
        "footer.ink": ink,
        "footer.muted": (
            "rgba(255,255,255,0.65)" if footer_is_dark else _rgba(ink, 0.65)
        ),
        "footer.hairline": (
            "1px solid rgba(255,255,255,0.12)"
            if footer_is_dark
            else f"1px solid {_rgba(ink, 0.15)}"
        ),
        "footer.ghost": _rgba(ink, 0.16),
        "page.maxWidthPx": f"{theme.page.max_width}px",
        "buttons.bg": theme.buttons.background,
        "buttons.fg": theme.buttons.text,
        "buttons.radiusPx": f"{theme.buttons.radius}px",
        "font.heading": theme.typography.heading_font,
        "font.body": theme.typography.body_font,
        "font.display": (
            getattr(theme, "display_font", None) or theme.typography.heading_font
        ),
    }
