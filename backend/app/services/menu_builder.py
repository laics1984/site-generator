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
  1. Builds `menus[]` from the source navigation when available, otherwise
     from the GeneratedSite's `page_tree`
  2. Wraps the existing header/footer BuilderElements into the layout payload
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator
from typing import Any, NamedTuple
from uuid import uuid4

from app.config import settings
from app.models.builder_schema import BuilderElement, GeneratedSite, NavGroup, PageNode
from app.models.content_blocks import MultilingualPlan, NavLink
from app.models.design_manifest import SELF_CHROME_HEADERS
from app.services.locale import locale_label
from app.services.nav_groups import group_pages, ranks_as

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


def _group_weight(group: NavGroup) -> int:
    """A category ranks as the kind of thing it is, not as whichever page in it
    happens to sort first: "Programmes" with the services, "Admissions" where
    Pricing goes."""
    return _TYPE_NAV_WEIGHT.get(ranks_as(group.kind) or "", _DEFAULT_NAV_WEIGHT)


def top_nav_rank(node: Any) -> int | None:
    """The page's position in the source nav's TOP level — None when the nav
    lists it inside a dropdown (its ``nav_rank`` is then its place there) or
    not at all. Duck-typed: page trees are drawn from plans and pages too."""
    return node.nav_rank if getattr(node, "nav_parent", None) is None else None


def _owner_ranked(nodes: list[PageNode]) -> bool:
    return any(top_nav_rank(n) is not None for n in nodes)


def _is_contact(node: PageNode) -> bool:
    from app.services.page_inference import _infer_page_type

    return _infer_page_type(node.slug, node.title) == "contact"


def _menu_children(node: PageNode) -> list[PageNode]:
    """A node's children that belong in a menu.

    Pages the source reaches from a listing are left out: MMTA's nine committee
    members each have a page, and the source puts them on the committee grid,
    not in the header. Nesting them would invent a nine-item dropdown and a
    nine-row footer column the site never had.

    They keep their place in the tree either way — this is a menu decision, not
    a hierarchy one, so breadcrumbs still read Home > Committee > the person.
    A hidden page that ends up at the TOP level (its roster wasn't generated)
    is not filtered: the footer is then the only way to reach it.
    """
    return [child for child in node.children if not child.menu_hidden]


def _uid() -> str:
    return str(uuid4())


def has_menu_groups(
    page_tree: list[PageNode] | None, nav_groups: list[NavGroup] | None = None
) -> bool:
    """Whether the footer menu will carry any grouped column — a top-level page
    with menu-visible children, or a navigation category. The mega footer's
    grid exists for those; a flat site gives it nothing to fill (see
    header_footer.build_footer)."""
    return any(_menu_children(node) for node in page_tree or []) or any(
        group.members for group in nav_groups or ()
    )


def _primary_tree(page_tree: list[PageNode] | None) -> list[PageNode]:
    # Translated pages are reachable through the language menu, never through
    # the primary or footer menus: a reader should not meet "Committee" and
    # "Committee (Bahasa Malaysia)" side by side in one menu.
    return [n for n in (page_tree or []) if not n.locale]


def _walk(nodes: list[PageNode]) -> Iterator[PageNode]:
    for node in nodes:
        yield node
        yield from _walk(node.children)


def _header_candidate(node: PageNode) -> bool:
    return (
        not node.is_homepage
        and not node.locale
        and node.slug.lower() not in ("privacy", "terms")
        and not _is_contact(node)
    )


class _HeaderMenu(NamedTuple):
    """The header's entries in order (before the cap) and each one's dropdown
    — the one selection the menu and its categories are both drawn from."""

    pages: list[PageNode]
    children: Callable[[PageNode], list[PageNode]]


def _header_menu(page_tree: list[PageNode]) -> _HeaderMenu:
    """What the header shows.

    If the source nav was captured (a page carries a top-level ``nav_rank``),
    the owner's curation is authoritative: their items, their order, nothing
    added — pages the owner left out of their nav stay out of ours (the footer
    carries them). Each page sits where THEIR header put it, read off
    ``nav_rank``/``nav_parent`` rather than the URL tree: "Admissions" at
    /enrol/apply-now stays top level, "History" at /company/history stays in
    About's dropdown, and a dropdown keeps the owner's order. A page's other
    children (pages the owner's nav does not place) follow its listed ones,
    as before. The tree itself — breadcrumbs, the footer — keeps the URL
    hierarchy: this is a menu decision, like ``nav_groups``.

    Without nav evidence, page-type weights order the top-level pages and the
    tree supplies the dropdowns.
    """
    nodes = [n for n in _walk(page_tree) if not n.locale]
    listed = sorted(
        (n for n in nodes if _header_candidate(n) and top_nav_rank(n) is not None),
        key=top_nav_rank,  # type: ignore[arg-type]
    )
    if not listed:
        roots = [n for n in page_tree if _header_candidate(n)]
        return _HeaderMenu(
            sorted(roots, key=lambda n: (_fallback_weight(n), n.slug)), _menu_children
        )

    shown = {n.slug for n in listed}
    dropdowns: dict[str, list[PageNode]] = {}
    for node in nodes:
        if node.nav_parent in shown and node.nav_rank is not None:
            dropdowns.setdefault(node.nav_parent, []).append(node)
    for items in dropdowns.values():
        items.sort(key=lambda n: n.nav_rank)  # type: ignore[arg-type, return-value]
    # Placed by the owner's nav somewhere this header shows. Anything else —
    # a page whose dropdown was not generated, a Contact the header leaves to
    # its CTA — stays in its tree parent's dropdown, as before.
    placed = shown | {n.slug for items in dropdowns.values() for n in items}

    def children(node: PageNode) -> list[PageNode]:
        return [
            *dropdowns.get(node.slug, []),
            *(child for child in _menu_children(node) if child.slug not in placed),
        ]

    return _HeaderMenu(listed, children)


def plan_nav_groups(
    page_tree: list[PageNode] | None,
    *,
    industry: str | None,
    language: str | None,
) -> list[NavGroup]:
    """The categories the menus list top-level pages under — decided once, at
    generation, and stored on the site (``GeneratedSite.nav_groups``), so the
    header, the footer, the preview and the push all read one answer.

    Only a header that cannot hold its pages gets any: below
    ``MAX_PRIMARY_ITEMS`` every page keeps its own top-level link, which is the
    flat nav small and medium sites have always had. Past it, the pages that
    would have been dropped from the header are grouped with the pages they
    belong with instead (services/nav_groups.py); a page no category claims
    stays a link, and the cap still applies to what is left.
    """
    pages, children = _header_menu(_primary_tree(page_tree))
    if len(pages) <= MAX_PRIMARY_ITEMS:
        return []
    groups = group_pages(
        pages,
        industry=industry,
        language=language,
        has_children=frozenset(n.slug for n in pages if children(n)),
        owner_order=_owner_ranked(pages),
    )
    if groups:
        logger.info(
            "primary menu grouped (%d pages): %s",
            len(pages),
            "; ".join(f"{g.label} ← {', '.join(g.members)}" for g in groups),
        )
    return groups


def route_navigation(
    page_tree: list[PageNode],
    *,
    industry: str | None,
    language: str | None,
) -> list[NavLink]:
    """A planned site's navigation as NavLinks — the route plan a two-page
    preview freezes (routers/generate.py `_navigation_for`) so pages not built
    yet are already in its menus. Every page keeps its place; the categories
    ``plan_nav_groups`` decides for the tree fold in exactly as they do on a
    full build, so the two never disagree about what the header groups."""
    groups = plan_nav_groups(page_tree, industry=industry, language=language)

    def link(item: dict[str, Any]) -> NavLink:
        return NavLink(
            label=item["label"],
            href=item["href"],
            children=[link(child) for child in item.get("children") or []],
        )

    return [link(item) for item in _folded_items(page_tree, groups)]


def _folded_items(
    nodes: list[PageNode],
    nav_groups: list[NavGroup] | None,
    *,
    dropdown_cap: int | None = None,
    children_of: Callable[[PageNode], list[PageNode]] = _menu_children,
) -> list[dict[str, Any]]:
    """One menu item per top-level entry: a page with its own children, or a
    category at the place of its head (with no head, of its first member). The
    header and the footer both fold through here, so they cannot list a page
    under two headings, nor order one category two ways: where nobody ranked
    the pages and a category folded, every entry takes its weight — a page its
    type's, a category its kind's (``_group_weight``) — so both menus run in
    the header's order. An owner's ranking is kept as given.

    A category degrades rather than inventing: a head that is not among
    ``nodes`` leaves its members standing alone, and a written heading with
    fewer than two of its members present is no heading at all.
    """
    by_slug = {n.slug: n for n in nodes}
    folded: dict[str, tuple[NavGroup, PageNode | None, list[PageNode]]] = {}
    for group in nav_groups or ():
        head = by_slug.get(group.slug) if group.slug is not None else None
        if (group.slug is not None and head is None) or group.slug in folded:
            continue
        members = [
            by_slug[s] for s in dict.fromkeys(group.members)
            if s in by_slug and s != group.slug and s not in folded
        ]
        if not members or (head is None and len(members) < 2):
            continue
        for node in (head, *members):
            if node is not None:
                folded[node.slug] = (group, head, members)

    def page(node: PageNode, extra: list[PageNode] | None = None) -> dict[str, Any]:
        children = [*children_of(node), *(extra or [])][:dropdown_cap]
        return _menu_item(
            node.title,
            f"/{node.slug}",
            children=[_menu_item(c.title, f"/{c.slug}") for c in children] or None,
        )

    # Weighed only when a category folded into a menu nobody ranked; otherwise
    # the order given is the order kept.
    by_weight = bool(folded) and not _owner_ranked(nodes)
    entries: list[tuple[int, dict[str, Any]]] = []
    for node in nodes:
        if node.slug not in folded:
            weight = (-1 if node.is_homepage else _fallback_weight(node)) if by_weight else 0
            entries.append((weight, page(node)))
            continue
        group, head, members = folded[node.slug]
        if node is not (head or members[0]):
            continue
        if head is not None:
            item = page(head, members)
        else:
            item = _menu_item(
                group.label,
                "",  # a heading that is no page: the renderers draw a button
                children=[_menu_item(m.title, f"/{m.slug}") for m in members[:dropdown_cap]],
            )
        entries.append((_group_weight(group) if by_weight else 0, item))
    if by_weight:
        entries.sort(key=lambda entry: entry[0])  # stable: ties keep their order
    return [item for _, item in entries]


def build_menus(
    page_tree: list[PageNode] | None,
    *,
    legal_pages: list[tuple[str, str]] | None = None,
    social_links: list[tuple[str, str]] | None = None,
    source_navigation: list[NavLink] | None = None,
    languages: MultilingualPlan | None = None,
    nav_groups: list[NavGroup] | None = None,
) -> list[dict[str, Any]]:
    """
    Build the SiteMenu list webtree expects in PUT /layout.

    `page_tree` is GeneratedSite.page_tree — the rooted forest of pages.
    `legal_pages` is the [(title, href)] list for the Legal column
    (Privacy / Terms / etc.) — these don't appear in the primary nav.
    `nav_groups` (GeneratedSite.nav_groups, from ``plan_nav_groups``) are the
    categories both the header and the footer fold top-level pages into.

    Returns at minimum a primary menu. Adds footer + legal menus when there
    are pages to fill them. Empty menus are omitted (the builder treats slots
    referencing a missing menu as "no menu" — harmless).

    Primary-menu policy (header is *curated*, footer is *complete*):
      * Home never appears — the header logo links to the homepage.
      * Contact never appears — the header's own "Get in touch" CTA already
        routes there, so a duplicate nav entry would be redundant. Contact
        still gets its column in the footer, which is complete rather than
        curated.
      * If the source nav was captured (any node carries ``nav_rank``), the
        owner's curation is otherwise authoritative: their items, their
        order, nothing added. "Get Involved" may well matter more than
        Contact — we don't re-rank what the owner already ranked.
      * Without nav evidence, fall back to page-type weights.
      * A site whose header could not hold its pages carries ``nav_groups``:
        the related ones fold into one dropdown each (``plan_nav_groups``).
      * Hard cap at ``MAX_PRIMARY_ITEMS`` entries; overflow pages stay in the
        footer menu only.
      * Parents carry one level of children (capped at ``MAX_DROPDOWN_ITEMS``)
        — desktop dropdown flyouts, collapsible mobile-drawer entries.
    """
    legal_pages = legal_pages or []
    page_tree = _primary_tree(page_tree) or None
    primary_items: list[dict[str, Any]] = []
    if page_tree:
        header = _header_menu(page_tree)
        entries = _folded_items(
            header.pages, nav_groups,
            dropdown_cap=MAX_DROPDOWN_ITEMS, children_of=header.children,
        )
        primary_items = entries[:MAX_PRIMARY_ITEMS]
        if demoted := entries[MAX_PRIMARY_ITEMS:]:
            logger.info(
                "primary menu capped at %d items — footer-only pages: %s",
                MAX_PRIMARY_ITEMS,
                ", ".join(item["label"] for item in demoted),
            )

    if source_navigation:
        # Keep the source's labels, order, depth and links. In particular, do
        # not apply the generated-page menu's Contact omission or item caps.
        primary_items = [_source_menu_item(item) for item in source_navigation]

    menus: list[dict[str, Any]] = [
        _menu(PRIMARY_MENU_ID, "Primary", "primary", primary_items),
    ]

    # Footer menu: hierarchical so the footer-columns block renders grouped
    # columns. Top-level pages with children, and the navigation categories,
    # become group headers (the heading + its pages, every one — the footer is
    # complete where the header is curated). Flat pages become their own
    # top-level column rather than being bundled under a single "Company"
    # heading: the footer-columns block packs them into its 2–3 columns, so
    # loose pages spread across the footer width instead of stacking into one
    # tall vertical list.
    footer_items: list[dict[str, Any]] = []
    if page_tree:
        footer_items = _folded_items(
            [
                node for node in page_tree
                if node.slug.lower() not in ("privacy", "terms") and not node.is_homepage
            ],
            nav_groups,
        )
    if source_navigation:
        footer_items = [_source_menu_item(item) for item in source_navigation]
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

    # Language menu: one entry per language the site publishes at its own
    # URLs, labelled in that language and carrying its `locale`. Authored, so
    # the words are the owner's to edit; where each one points is the
    # renderer's — THIS page in that language, from the page's published
    # alternates (webtree-public lib/languages.resolveLanguageMenus), so a reader
    # on /about lands on /bm/about, not /bm. The href here is that language's
    # home, the fallback for a renderer that predates alternates. It rides the
    # utility slot: every chrome-header-* declares a "Language menu" node gated
    # on `has_languages`, which build_header sets from the same predicate.
    if languages and languages.serves_language_urls:
        codes = [languages.primary, *languages.secondary]
        menus.append(_menu(UTILITY_MENU_ID, "Language", "language", [
            {
                **_menu_item(locale_label(code), "/" if code == languages.primary
                             else f"/{languages.prefixes.get(code) or code.lower()}"),
                # Stable, so a translation of the layout keeps naming this item.
                "id": f"language-{code}",
                "locale": code,
            }
            for code in codes
        ]))

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
    adaptive_ink: bool = False,
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
    `adaptive_ink` (self-chrome archetypes again) asks the renderer to flip the
    bar's ink/tint/hairline between light and dark as the section beneath it
    changes: a header that never solidifies has no chrome of its own to stay
    legible against, so its ink has to follow the page. Emitted only when True,
    and it is the ONLY thing renderers gate that behaviour on — none of them
    knows the string "floating-pill".
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
    if adaptive_ink:
        behavior["adaptiveInk"] = True
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
        source_navigation=site.source_navigation,
        languages=site.multilingual,
        nav_groups=site.nav_groups,
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
        # Same archetype set, opposite sense: the bar that never reveals a
        # background is exactly the one that must recolour itself per section.
        adaptive_ink=not reveal_background,
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


def _source_menu_item(item: NavLink) -> dict[str, Any]:
    return _menu_item(
        item.label,
        item.href,
        children=[_source_menu_item(child) for child in item.children] or None,
    )
