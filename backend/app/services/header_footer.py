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
from app.services.theme import (
    _contrast,
    _ensure_contrast_against,
    _text_for_background,
)

# The builder's HeaderSettings "Divider" control is a boxShadow preset on the
# __header root element. This is the exact "Subtle" preset value
# (HEADER_SHADOW_PRESETS in builder src/lib/site-navigation.ts) — emitting the
# same string makes the builder's Divider panel show "Subtle" as selected.
HEADER_DIVIDER_SUBTLE = "0 1px 3px 0 rgba(15, 23, 42, 0.06)"


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
            styles={"height": "36px", "width": "auto", "display": "block"},
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
                            "fontSize": "18px",
                            "lineHeight": 1,
                        },
                    )
                ],
                name="Mark",
                styles={
                    "width": "36px",
                    "height": "36px",
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
    header_bg, header_fg, logo_lockup = _header_chrome(brand, theme)
    if overlay and brand.logo_is_light is False:
        # A dark image logo floating over a dark full-bleed hero needs its
        # contrast chip even if the solid header wouldn't (the renderer can
        # recolor text ink, not bitmaps).
        logo_lockup = logo_lockup or "#ffffff"
    nav_menu = _menu_element(
        slot="primary",
        variant="header-inline",
        label="Primary navigation",
        color_mode="manual",
        styles={
            "flex": "1 1 0%",
            "color": header_fg,
            "fontSize": "15px",
        },
    )
    # Text-bearing header elements get the ink marker; while the overlay
    # header is transparent the renderer forces their colour to white
    # (.wt-page-header--overlay .wt-header-ink). The CTA button keeps its own
    # solid background/text and is deliberately NOT marked.
    nav_menu.classes = "wt-header-ink"
    logo = _logo_mark(brand, theme, lockup=logo_lockup, ink=header_fg)
    logo.classes = "wt-header-ink"

    cta_children: list[BuilderElement] = []
    if primary_cta:
        label, href = primary_cta
        cta_children.append(
            BuilderElement(
                id=_uid(),
                name="Header CTA",
                type="link",
                styles={
                    "backgroundColor": theme.buttons.background,
                    "color": theme.buttons.text,
                    "paddingTop": "10px",
                    "paddingBottom": "10px",
                    "paddingLeft": "20px",
                    "paddingRight": "20px",
                    "borderRadius": f"{theme.buttons.radius}px",
                    "fontWeight": 600,
                    "fontSize": "14px",
                    "textDecoration": "none",
                    "display": "inline-flex",
                    "alignItems": "center",
                },
                content=BuilderElementContent(innerText=label, href=href),
            )
        )

    bar = _container(
        [
            logo,
            nav_menu,
            *cta_children,
        ],
        name="Header bar",
        styles={
            "flexDirection": "row",
            "alignItems": "center",
            "justifyContent": "space-between",
            "width": "100%",
            "maxWidth": f"{theme.page.max_width}px",
            "marginLeft": "auto",
            "marginRight": "auto",
            "paddingLeft": "24px",
            "paddingRight": "24px",
            "paddingTop": "16px",
            "paddingBottom": "16px",
            "gap": "24px",
        },
    )

    return BuilderElement(
        id=_uid(),
        name="Site Header",
        type="__header",
        styles={
            "width": "100%",
            "backgroundColor": header_bg,
            # The builder's "Divider" setting reads this boxShadow — emit the
            # exact "Subtle" preset so a hard border never separates the
            # header from the page (a 1px solid line read as a black rule on
            # dark palettes once the header background became visible).
            "boxShadow": HEADER_DIVIDER_SUBTLE,
            "position": "sticky",
            "top": "0",
            "zIndex": "50",
            # No backdrop blur by default — it forced a frosted-glass look on
            # every header and there was no way to opt out. The builder now
            # exposes a "Frosted glass" toggle (HeaderSettings) for anyone who
            # wants it; leave the header opaque here.
        },
        content=[bar],
    )


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
) -> BuilderElement:
    """
    Themed footer with grouped sub-page navigation.

    Layout depends on what's in the page_tree:
      * Brand column (logo + tagline) — always
      * One column per top-level page that has children (Services / Case Studies / …)
      * "Company" column for top-level pages without children (About / Contact / …)
      * "Legal" column for privacy / terms (always at the right edge)
      * Contact info appears under the brand column

    Flat sites (no children anywhere) fall back to the simpler three-column
    layout (brand + explore + contact) for visual balance.
    """
    on_dark_text = "#ffffff"
    on_dark_muted = "rgba(255,255,255,0.65)"

    # ---- brand column (logo and/or wordmark + tagline + contact details) -------
    # The footer sits on a dark background. When a usable logo exists we show it
    # alone (mirroring the header) — stacking a text wordmark beneath it just
    # duplicates the mark. We fall back to the light text wordmark only when there
    # is no logo, or the logo is known to be dark (so it would vanish on the dark
    # footer and the text keeps the brand reliably visible).
    brand_col_children: list[BuilderElement] = []

    logo_src = brand.logo_url or brand.logo_data_url
    show_wordmark = (not logo_src) or (brand.logo_is_light is False)
    if logo_src:
        # In the footer this is a plain content image — the builder's dedicated
        # logo sizing is header-only (isBrandLogoPlaceholder requires source ===
        # 'header'). The generic image frame defaults to 120px tall + object-fit:
        # cover, and a width of "auto" collapses to 0px because the inner fill is
        # absolutely positioned and contributes no intrinsic width. So pin an
        # explicit width + height, object-fit:contain (no crop), left-aligned,
        # and min-height 0.
        brand_col_children.append(
            _image(
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
        )

    if show_wordmark:
        brand_col_children.append(
            _text(
                brand.name,
                name="Brand",
                styles={
                    "fontFamily": theme.typography.heading_font,
                    "fontSize": "24px",
                    "fontWeight": 700,
                    "letterSpacing": "-0.01em",
                    "color": on_dark_text,
                },
            )
        )
    if brand.tagline:
        brand_col_children.append(
            _text(
                brand.tagline,
                name="Tagline",
                styles={
                    "color": on_dark_muted,
                    "fontSize": "14px",
                    "lineHeight": 1.5,
                    "marginTop": "12px",
                    "maxWidth": "280px",
                },
            )
        )
    if contact:
        for key, value in contact.items():
            brand_col_children.append(
                _text(
                    f"{key.capitalize()}: {value}",
                    name=key.capitalize(),
                    styles={
                        "color": on_dark_muted,
                        "fontSize": "13px",
                        "lineHeight": 1.7,
                        "marginTop": "4px",
                    },
                )
            )
    brand_col = _container(
        brand_col_children,
        name="Brand column",
        styles={
            "gap": "8px",
            "alignItems": "flex-start",
            "flex": "1 1 240px",
            "minWidth": "0",
        },
    )

    # ---- shared footer navigation menu ------------------------------------
    # Sub-page navigation is a single grouped ``menu`` element bound to the
    # ``footer`` slot; its hierarchy (group headers + child links) comes from
    # the entity's footer menu (built in menu_builder.build_menus). colorMode
    # "auto" lets the builder pick an accessible text colour against the dark
    # footer background.
    footer_menu = _menu_element(
        slot="footer",
        variant="footer-columns",
        label="Footer navigation",
        color_mode="auto",
        styles={"flex": "2 1 320px", "minWidth": "0"},
    )

    # The builder applies an element's `styles` to its outer wrapper but does
    # NOT honour `gridTemplateColumns` on a plain `container` (only on 2Col/3Col,
    # whose track count it controls). A `display:grid` container therefore
    # collapses to a single implicit column, stacking the brand + nav into the
    # left edge. Use flex with proportional, wrapping children instead — the
    # builder renders plain flex containers faithfully.
    grid = BuilderElement(
        id=_uid(),
        name="Footer grid",
        type="container",
        styles={
            "display": "flex",
            "flexDirection": "row",
            "flexWrap": "wrap",
            "alignItems": "flex-start",
            "gap": "48px",
            "width": "100%",
        },
        content=[brand_col, footer_menu],
        responsiveStyles=ResponsiveStyles(
            mobile={"flexDirection": "column", "gap": "32px"}
        ),
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

    # Bottom bar: copyright · legal menu · media credits.
    from datetime import datetime

    year = datetime.now().year
    legal_left = _text(
        f"© {year} {brand.name}. All rights reserved.",
        name="Copyright",
        styles={"color": on_dark_muted, "fontSize": "12px"},
    )
    bottom_children: list[BuilderElement] = [legal_left]
    if social_links:
        # Items come from the entity's menu-social (built in menu_builder from
        # the scraped profile URLs) — the element only binds the slot.
        bottom_children.append(
            _menu_element(
                slot="social",
                variant="social-inline",
                label="Social",
                color_mode="auto",
                styles={"width": "auto", "fontSize": "12px"},
            )
        )
    if has_legal:
        bottom_children.append(
            _menu_element(
                slot="legal",
                variant="footer-legal",
                label="Legal",
                color_mode="auto",
                styles={"width": "auto", "fontSize": "12px"},
            )
        )
    credits_text = " · ".join(media_credits) if media_credits else ""
    if credits_text:
        bottom_children.append(
            _text(
                credits_text,
                name="Photo credits",
                styles={"color": on_dark_muted, "fontSize": "12px"},
            )
        )
    legal_bar = _container(
        bottom_children,
        name="Legal bar",
        styles={
            "flexDirection": "row",
            "justifyContent": "space-between",
            "alignItems": "center",
            "width": "100%",
            "paddingTop": "24px",
            "marginTop": "32px",
            "borderTop": "1px solid rgba(255,255,255,0.12)",
            "gap": "16px",
            "flexWrap": "wrap",
        },
    )

    inner = _container(
        [grid, legal_bar],
        name="Footer content",
        styles={
            "maxWidth": f"{theme.page.max_width}px",
            "marginLeft": "auto",
            "marginRight": "auto",
            "paddingLeft": "24px",
            "paddingRight": "24px",
            "paddingTop": "64px",
            "paddingBottom": "32px",
            "width": "100%",
            "gap": "0",
        },
    )

    return BuilderElement(
        id=_uid(),
        name="Site Footer",
        type="__footer",
        styles={
            "width": "100%",
            "backgroundColor": theme.palette.secondary,
            "color": on_dark_text,
            "fontFamily": theme.typography.body_font,
        },
        content=[inner],
    )
