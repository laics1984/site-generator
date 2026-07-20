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
from typing import Any, NamedTuple
from uuid import uuid4

from app.config import settings
from app.models.builder_schema import BuilderElement, GeneratedSite, PageNode
from app.models.design_manifest import SELF_CHROME_HEADERS

logger = logging.getLogger(__name__)


# Canonical IDs from builder/src/lib/site-navigation.ts
PRIMARY_MENU_ID = "menu-primary"
UTILITY_MENU_ID = "menu-utility"
FOOTER_MENU_ID = "menu-footer"
LEGAL_MENU_ID = "menu-legal"
SOCIAL_MENU_ID = "menu-social"

# Primary nav cap, including Home and Contact. Standard UX guidance is 5–7
# top-level items; pages that don't make the cut stay reachable via the
# footer columns and their parent's hub page.
MAX_PRIMARY_ITEMS = 7

# Max children per primary-menu dropdown. Beyond this the parent's hub page
# and the footer column carry the full list.
MAX_DROPDOWN_ITEMS = 8

# Fallback ordering for pages the source nav didn't rank, keyed on the
# inferred page type. Lower ⇒ earlier in the header. Contact is handled
# separately (always included, always last).
_TYPE_NAV_WEIGHT: dict[str, int] = {
    "services": 0,
    "menu": 1,
    "work": 1,
    "pricing": 2,
    "about": 3,
    "team": 4,
    "blog": 5,
    "process": 6,
    "faq": 7,
    "gallery": 8,
    "testimonials": 9,
}
_DEFAULT_NAV_WEIGHT = 9


def _fallback_weight(node: PageNode) -> int:
    # Local import: page_inference imports nothing from this module, so this
    # stays cycle-free while reusing the slug/title → page-type heuristics.
    from app.services.page_inference import _infer_page_type

    return _TYPE_NAV_WEIGHT.get(_infer_page_type(node.slug, node.title), _DEFAULT_NAV_WEIGHT)


def _is_contact(node: PageNode) -> bool:
    from app.services.page_inference import _infer_page_type

    return _infer_page_type(node.slug, node.title) == "contact"


def _uid() -> str:
    return str(uuid4())


def build_menus(
    page_tree: list[PageNode] | None,
    *,
    legal_pages: list[tuple[str, str]] | None = None,
    social_links: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """
    Build the SiteMenu list webtree expects in PUT /layout.

    `page_tree` is GeneratedSite.page_tree — the rooted forest of pages.
    `legal_pages` is the [(title, href)] list for the Legal column
    (Privacy / Terms / etc.) — these don't appear in the primary nav.

    Returns at minimum a primary menu. Adds footer + legal menus when there
    are pages to fill them. Empty menus are omitted (the builder treats slots
    referencing a missing menu as "no menu" — harmless).

    Primary-menu policy (header is *curated*, footer is *complete*):
      * Home never appears — the header logo links to the homepage.
      * If the source nav was captured (any node carries ``nav_rank``), the
        owner's curation is authoritative: their items, their order, nothing
        added. "Get Involved" may well matter more than Contact — we don't
        re-rank what the owner already ranked.
      * Without nav evidence, fall back to page-type weights with Contact
        last — but Contact only when the source actually had a contact page
        (``from_source``), or when there's no source evidence at all (doc
        uploads / thin crawls, where convention is the best guess).
      * Hard cap at ``MAX_PRIMARY_ITEMS``; overflow pages stay in the footer
        menu only.
      * Parents carry one level of children (capped at ``MAX_DROPDOWN_ITEMS``)
        — desktop dropdown flyouts, indented mobile-drawer entries.
    """
    legal_pages = legal_pages or []
    primary_items: list[dict[str, Any]] = []
    if page_tree:
        candidates = [
            n
            for n in page_tree
            if not n.is_homepage and n.slug.lower() not in ("privacy", "terms")
        ]
        nav_curated = any(n.nav_rank is not None for n in candidates)

        if nav_curated:
            # The owner's header nav, verbatim. Pages the owner left out of
            # their nav stay out of ours (footer carries them).
            ranked = sorted(
                (n for n in candidates if n.nav_rank is not None),
                key=lambda n: n.nav_rank,  # type: ignore[arg-type, return-value]
            )
            selected = ranked[:MAX_PRIMARY_ITEMS]
            demoted = ranked[MAX_PRIMARY_ITEMS:]
        else:
            has_source_evidence = any(n.from_source for n in page_tree)
            contact_nodes = [n for n in candidates if _is_contact(n)]
            include_contact = [
                n
                for n in contact_nodes[:1]
                if not has_source_evidence or n.from_source
            ]
            others = [n for n in candidates if not _is_contact(n)]
            others.sort(key=lambda n: (_fallback_weight(n), n.slug))
            budget = MAX_PRIMARY_ITEMS - len(include_contact)
            selected = [*others[:budget], *include_contact]
            demoted = others[budget:]

        if demoted:
            logger.info(
                "primary menu capped at %d items — footer-only pages: %s",
                MAX_PRIMARY_ITEMS,
                ", ".join(n.slug for n in demoted),
            )

        for node in selected:
            dropdown = [
                _menu_item(child.title, f"/{child.slug}")
                for child in node.children[:MAX_DROPDOWN_ITEMS]
            ]
            primary_items.append(
                _menu_item(node.title, f"/{node.slug}", children=dropdown or None)
            )

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

    # Social menu: profile links scraped from the source — external, so they
    # open in a new tab.
    if social_links:
        menus.append(
            _menu(
                SOCIAL_MENU_ID,
                "Social",
                "social",
                [
                    _menu_item(label, href, target="_blank", rel="noopener noreferrer")
                    for label, href in social_links
                ],
            )
        )

    return menus


def wrap_header(
    header_element: BuilderElement,
    *,
    menus: list[dict[str, Any]],
    sticky: bool = True,
    overlay: bool = False,
    reveal_background_on_scroll: bool = True,
    scroll_reveal_offset: int | None = None,
    shrink_on_scroll: bool = False,
    scroll_shrink_offset: int | None = None,
    shrink_amount: int | None = None,
) -> dict[str, Any]:
    """
    Wrap a `__header` BuilderElement into a BuilderTemplateHeader payload.

    Slots reference canonical menu IDs only if those menus exist in `menus`.
    `overlay` marks a transparent header floating over a full-bleed hero; the
    renderer owns the scroll-solidify behaviour this flag implies.
    `reveal_background_on_scroll=False` (self-chrome archetypes: the floating
    pill) keeps the overlay header transparent at every scroll position — the
    bar carries its own chrome, so there is no background to reveal. Emitted
    only when False; renderers treat a missing field as True (legacy default).
    `scroll_reveal_offset` is the scrolled-pixel threshold before the floating
    header gains its background (renderer clamps 0-600, defaults 80 if absent).
    `shrink_on_scroll` compacts the header (logo + row padding) once scrolled
    past `scroll_shrink_offset` px, to `shrink_amount` percent of its original
    size (renderer clamps offset 0-600, amount 50-100). On overlay headers the
    renderer shares the reveal offset, so both effects fire at one scroll moment.
    """
    menu_ids = {m["id"] for m in menus}
    behavior: dict[str, Any] = {
        "position": "sticky" if sticky else "static",
        "overlay": overlay,
    }
    if not reveal_background_on_scroll:
        behavior["revealBackgroundOnScroll"] = False
    if scroll_reveal_offset is not None:
        behavior["scrollRevealOffset"] = scroll_reveal_offset
    if shrink_on_scroll:
        behavior["shrinkOnScroll"] = True
        if scroll_shrink_offset is not None:
            behavior["scrollShrinkOffset"] = scroll_shrink_offset
        if shrink_amount is not None:
            behavior["shrinkAmount"] = shrink_amount
    return {
        "elements": [header_element.model_dump(mode="json")],
        "behavior": behavior,
        "preset": {"id": None},
        "slots": {
            "primaryMenuId": PRIMARY_MENU_ID if PRIMARY_MENU_ID in menu_ids else None,
            "utilityMenuId": UTILITY_MENU_ID if UTILITY_MENU_ID in menu_ids else None,
            "socialMenuId": SOCIAL_MENU_ID if SOCIAL_MENU_ID in menu_ids else None,
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


class LayoutPayload(NamedTuple):
    """The three layout artefacts `PUT /pages/{id}/layout` takes."""

    menus: list[dict[str, Any]]
    header: dict[str, Any]
    footer: dict[str, Any]


def build_layout_payload(site: GeneratedSite) -> LayoutPayload:
    """Assemble the menus + wrapped header/footer a site pushes to the CMS.

    The preview endpoint routes through here so the preview renders the exact
    payload the push would: `menus[]` is what a `menu` element resolves its
    items from, and the header `behavior` block is what drives overlay/shrink —
    so a preview built from the raw `header_schema` renders a header with no nav
    and no overlay, i.e. not the page the visitor gets. The push path
    (services/push_orchestrator) inlines the same three calls; keep the two in
    lockstep — tests/test_preview_layout.py asserts they produce one payload.

    Raises ValueError when the site has no header/footer schema; callers map it
    onto whatever error type their transport speaks.
    """
    legal_pages = [
        (p.title, f"/{p.slug or ''}".rstrip("/") or "/")
        for p in site.pages
        if p.slug.lower() in ("privacy", "terms")
    ]
    menus = build_menus(
        site.page_tree,
        legal_pages=legal_pages,
        social_links=site.social_links,
    )
    if site.header_schema is None or site.footer_schema is None:
        raise ValueError(
            "GeneratedSite is missing header_schema or footer_schema — "
            "rebuild the site with plan_to_site() before pushing."
        )
    # Self-chrome archetypes (floating pill) overlay without ever revealing a
    # background — the manifest records the archetype; absent/legacy manifests
    # default to reveal-style (True).
    header_archetype = (site.design_manifest or {}).get("header_archetype")
    reveal_background = header_archetype not in SELF_CHROME_HEADERS
    header = wrap_header(
        site.header_schema,
        menus=menus,
        overlay=site.header_overlay,
        reveal_background_on_scroll=reveal_background,
        scroll_reveal_offset=(
            settings.header_scroll_reveal_offset if site.header_overlay else None
        ),
        # Shrink applies to every header (independent of overlay); the
        # renderer shares the reveal offset when overlay is on, so reuse it.
        shrink_on_scroll=settings.header_shrink_enabled,
        scroll_shrink_offset=settings.header_scroll_reveal_offset,
        shrink_amount=settings.header_shrink_amount,
    )
    footer = wrap_footer(site.footer_schema, menus=menus)
    return LayoutPayload(menus=menus, header=header, footer=footer)


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
    target: str | None = None,
    rel: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": _uid(),
        "label": label,
        "href": href,
        "visible": True,
    }
    if target:
        item["target"] = target
    if rel:
        item["rel"] = rel
    if children:
        item["children"] = children
    return item
