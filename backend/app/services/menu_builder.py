"""
Emit `menus[]` + wrapped `headerSchema` / `footerSchema` payloads in the exact
shape webtree's `PUT /pages/{id}/layout` endpoint expects.

Our existing `services/header_footer.py` returns plain BuilderElement trees
(types `__header` / `__footer`). The CMS expects them WRAPPED in a richer
structure with behavior + preset + slot references:

    headerSchema: { elements, behavior, preset, slots }
    footerSchema: { elements,           preset, slots }

Slots are STRING IDs that match the canonical menu IDs in
`builder/src/lib/site-navigation.ts:136`:
    menu-primary, menu-utility, menu-footer, menu-legal, menu-social

This module:
  1. Builds `menus[]` from the GeneratedSite's `page_tree`
  2. Wraps the existing header/footer BuilderElements into the layout payload
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import uuid4

from app.models.builder_schema import BuilderElement, PageNode

logger = logging.getLogger(__name__)


# Canonical IDs from builder/src/lib/site-navigation.ts
PRIMARY_MENU_ID = "menu-primary"
UTILITY_MENU_ID = "menu-utility"
FOOTER_MENU_ID = "menu-footer"
LEGAL_MENU_ID = "menu-legal"
SOCIAL_MENU_ID = "menu-social"


def _uid() -> str:
    return str(uuid4())


def build_menus(
    page_tree: list[PageNode] | None,
    *,
    legal_pages: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """
    Build the SiteMenu list webtree expects in PUT /layout.

    `page_tree` is GeneratedSite.page_tree — the rooted forest of pages.
    `legal_pages` is the [(title, href)] list for the Legal column
    (Privacy / Terms / etc.) — these don't appear in the primary nav.

    Returns at minimum a primary menu. Adds footer + legal menus when there
    are pages to fill them. Empty menus are omitted (the builder treats slots
    referencing a missing menu as "no menu" — harmless).
    """
    legal_pages = legal_pages or []
    primary_items: list[dict[str, Any]] = []
    if page_tree:
        for node in page_tree:
            slug = node.slug.lower()
            if slug in ("privacy", "terms"):
                # Legal pages live in their own menu, not the primary nav.
                continue
            href = "/" if node.is_homepage else f"/{node.slug}"
            primary_items.append(_menu_item(node.title, href))

    menus: list[dict[str, Any]] = [
        _menu(PRIMARY_MENU_ID, "Primary", "primary", primary_items),
    ]

    # Footer menu: hierarchical so the footer-columns block renders grouped
    # columns. Top-level pages with children become group headers (the parent
    # link + its children). Standalone top-level pages are gathered under a
    # non-linking "Company" group header so they still get a column.
    footer_items: list[dict[str, Any]] = []
    if page_tree:
        for node in page_tree:
            slug = node.slug.lower()
            if slug in ("privacy", "terms") or node.is_homepage:
                continue
            href = f"/{node.slug}"
            if node.children:
                footer_items.append(
                    _menu_item(
                        node.title,
                        href,
                        children=[
                            _menu_item(child.title, f"/{child.slug}")
                            for child in node.children
                        ],
                    )
                )
            else:
                # Flat pages become their own top-level column rather than being
                # bundled under a single "Company" heading. The footer-columns
                # block lays top-level items out in a 2–3 column grid, so loose
                # pages spread across the footer width instead of stacking into
                # one tall vertical list.
                footer_items.append(_menu_item(node.title, href))
    if footer_items:
        menus.append(_menu(FOOTER_MENU_ID, "Footer", "footer", footer_items))

    # Legal menu: privacy + terms etc.
    if legal_pages:
        menus.append(
            _menu(
                LEGAL_MENU_ID,
                "Legal",
                "legal",
                [_menu_item(label, href) for label, href in legal_pages],
            )
        )

    return menus


def wrap_header(
    header_element: BuilderElement,
    *,
    menus: list[dict[str, Any]],
    sticky: bool = True,
) -> dict[str, Any]:
    """
    Wrap a `__header` BuilderElement into a BuilderTemplateHeader payload.

    Slots reference canonical menu IDs only if those menus exist in `menus`.
    """
    menu_ids = {m["id"] for m in menus}
    return {
        "elements": [header_element.model_dump(mode="json")],
        "behavior": {
            "position": "sticky" if sticky else "static",
            "overlay": False,
        },
        "preset": {"id": None},
        "slots": {
            "primaryMenuId": PRIMARY_MENU_ID if PRIMARY_MENU_ID in menu_ids else None,
            "utilityMenuId": UTILITY_MENU_ID if UTILITY_MENU_ID in menu_ids else None,
        },
    }


def wrap_footer(
    footer_element: BuilderElement,
    *,
    menus: list[dict[str, Any]],
) -> dict[str, Any]:
    """Wrap a `__footer` BuilderElement into a BuilderTemplateFooter payload."""
    menu_ids = {m["id"] for m in menus}
    return {
        "elements": [footer_element.model_dump(mode="json")],
        "preset": {"id": None},
        "slots": {
            "footerMenuId": FOOTER_MENU_ID if FOOTER_MENU_ID in menu_ids else None,
            "legalMenuId": LEGAL_MENU_ID if LEGAL_MENU_ID in menu_ids else None,
            "socialMenuId": SOCIAL_MENU_ID if SOCIAL_MENU_ID in menu_ids else None,
        },
    }


def _menu(
    menu_id: str, name: str, purpose: str, items: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "id": menu_id,
        "name": name,
        "purpose": purpose,
        "items": items,
    }


def _menu_item(
    label: str,
    href: str,
    *,
    children: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": _uid(),
        "label": label,
        "href": href,
        "visible": True,
    }
    if children:
        item["children"] = children
    return item
