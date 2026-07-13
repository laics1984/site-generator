"""
Fill shared section-catalog templates with generated content.

Python mirror of ``webtree/builder/src/lib/section-catalog.ts`` (`materializeTemplate`).
The catalog (`app/templates/section_catalog.json`, vendored from the builder) is
the single source of truth for renderable section shapes; this module fills a
chosen template's slots with LLM content and emits a `BuilderElement` tree.

Keep this in lock-step with the TS engine. Marker directives on a catalog node:

  - ``$slot``      bind this node's content from ``scope[$slot]`` (by node type)
  - ``$repeat``    clone ``content[0]`` once per item in ``scope[$repeat]``
  - ``$bento``     like ``$repeat``, but stamp cloned tiles with varied grid spans
  - ``$gridFit``   on a ``$repeat`` grid, pick 2Col/3Col by item count
  - ``$content``   fill from a registered factory (e.g. the contact form default)
  - ``$styleSlot`` inject a (resolved) value into a CSS style property

Image resolution is injected as an async callback (`resolve_image`) so this
module has no heavy dependencies and is unit-testable in isolation. Theme flows
entirely through CSS vars baked into the catalog styles + the builderStyles
payload — no brand values are inlined here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.models.builder_schema import BuilderElement, BuilderElementContent

CATALOG_PATH = Path(__file__).resolve().parent.parent / "templates" / "section_catalog.json"

# query -> (resolved url, average colour hex or None). The avg colour drives the
# adaptive dark-overlay intensity for photo backgrounds.
ResolveImage = Callable[[str], Awaitable[tuple[str, "str | None"]]]
ContentFactory = Callable[[], dict[str, Any]]
# Theme colours for brand-tinted photo overlays: {"primary": hex, "secondary": hex}.
ThemeColors = dict[str, str]


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text())


@lru_cache(maxsize=1)
def catalog_by_id() -> dict[str, dict[str, Any]]:
    return {section["id"]: section for section in load_catalog()["sections"]}


def get_template(template_id: str) -> dict[str, Any] | None:
    return catalog_by_id().get(template_id)


def templates_for_type(section_type: str) -> list[dict[str, Any]]:
    return [s for s in load_catalog()["sections"] if s["sectionType"] == section_type]


# --- internals ------------------------------------------------------------------


def _bento_spans(index: int, count: int) -> dict[str, str]:
    """Grid spans for the i-th bento tile on a 6-column, auto-flow:dense grid.

    A large lead tile, periodic wide tiles, and standard half-width tiles give a
    modular "bento" rhythm; `dense` auto-flow packs any item count cleanly.
    """
    if index == 0 and count >= 3:
        return {"gridColumn": "span 4", "gridRow": "span 2"}  # large lead
    if index > 0 and index % 5 == 0:
        return {"gridColumn": "span 4"}  # periodic wide
    return {"gridColumn": "span 2"}  # standard (3 per row)


def _base_fields(node: dict[str, Any], styles: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": node["name"],
        "type": node["type"],
        "styles": styles,
    }
    if node.get("classes") is not None:
        out["classes"] = node["classes"]
    if node.get("visible") is not None:
        out["visible"] = node["visible"]
    if node.get("responsiveStyles") is not None:
        out["responsiveStyles"] = node["responsiveStyles"]
    if node.get("motion") is not None:
        out["motion"] = node["motion"]
    return out


def _image_src(value: Any) -> str | None:
    """A non-resolving read of an image value's URL (None means 'needs query')."""
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        return value.get("src") or None
    return None


async def _resolve_image(value: Any, resolve_image: ResolveImage) -> dict[str, str]:
    """Normalize an image slot value to ``{src, alt}``, resolving a query if needed."""
    if isinstance(value, str):
        return {"src": value, "alt": ""}
    if not isinstance(value, dict):
        return {"src": "", "alt": ""}
    src = value.get("src") or ""
    alt = value.get("alt") or ""
    if not src and value.get("query"):
        src, _avg = await resolve_image(value["query"])
    return {"src": src or "", "alt": alt}


async def _bind_slot(
    node_type: str,
    value: Any,
    base: dict[str, Any],
    resolve_image: ResolveImage,
) -> BuilderElementContent:
    if node_type == "link":
        v = value if isinstance(value, dict) else {}
        return BuilderElementContent(
            **{
                **base,
                "innerText": str(v.get("innerText") or v.get("label") or ""),
                "href": str(v.get("href") or "#"),
            }
        )
    if node_type == "image":
        img = await _resolve_image(value, resolve_image)
        return BuilderElementContent(**{**base, "src": img["src"], "alt": img["alt"]})
    if node_type == "video":
        # Raw iframe embed (maps, players): value is {src} or a bare URL string.
        src = value.get("src") if isinstance(value, dict) else value
        return BuilderElementContent(**{**base, "src": str(src or "")})
    return BuilderElementContent(**{**base, "innerText": str(value)})


async def _fill_node(
    node: dict[str, Any],
    scope: dict[str, Any],
    resolve_image: ResolveImage,
    factories: dict[str, ContentFactory],
    theme: ThemeColors | None,
) -> BuilderElement | None:
    # $styleSlot: inject a resolved image into a CSS property (e.g. a hero/CTA
    # background). When theme colours are supplied and the target is the
    # background image, build a brand-tinted overlay whose darkness adapts to the
    # photo's average luminance; otherwise use the template's static format.
    styles = node["styles"]
    style_slot = node.get("$styleSlot")
    if style_slot:
        value = scope.get(style_slot["slot"])
        url, avg = (_image_src(value) or ""), None
        if not url and isinstance(value, dict) and value.get("query"):
            url, avg = await resolve_image(value["query"])
        if url:
            prop = style_slot["property"]
            if theme and prop == "backgroundImage":
                from app.services.image_styling import photo_background

                styles = {
                    **styles,
                    prop: photo_background(
                        avg, url,
                        theme.get("secondary", "#0f172a"),
                        theme.get("primary", "#2563eb"),
                    ),
                }
            else:
                styles = {**styles, prop: style_slot["format"].replace("{}", url)}

    base = _base_fields(node, styles)

    # $repeat: clone the item template per item in the named list.
    if node.get("$repeat"):
        items = scope.get(node["$repeat"]) or []
        content = node.get("content")
        item_template = content[0] if isinstance(content, list) and content else None
        children: list[BuilderElement] = []
        if item_template:
            for item in items:
                el = await _fill_node(item_template, item, resolve_image, factories, theme)
                if el is not None:
                    children.append(el)
        # $gridFit: pick column-layout type by item count (mirror of the TS engine).
        # Prefer 3 columns, but drop to 2 whenever a 3-wide grid would strand a lone
        # card in the last row (count % 3 == 1, e.g. 4 → 2×2, 7 → 2+2+2+1) — a single
        # orphan beside a big gap reads as broken. Counts that leave a 2-card last row
        # (% 3 == 2) keep 3 columns; that row is balanced enough.
        if node.get("$gridFit"):
            n = len(children)
            base = {**base, "type": "2Col" if (n <= 2 or n % 3 == 1) else "3Col"}
        return BuilderElement(id=str(uuid4()), content=children, **base)

    # $bento: like $repeat, but stamp each cloned tile with varied grid spans so a
    # plain CSS-grid container reads as a bento (modular, mixed-size) layout.
    if node.get("$bento"):
        items = scope.get(node["$bento"]) or []
        content = node.get("content")
        item_template = content[0] if isinstance(content, list) and content else None
        tiles: list[BuilderElement] = []
        if item_template:
            for item in items:
                el = await _fill_node(item_template, item, resolve_image, factories, theme)
                if el is not None:
                    tiles.append(el)
        for i, el in enumerate(tiles):
            el.styles = {**(el.styles or {}), **_bento_spans(i, len(tiles))}
        return BuilderElement(id=str(uuid4()), content=tiles, **base)

    # $content: fill from a registered factory.
    if node.get("$content"):
        factory = factories.get(node["$content"])
        data = factory() if factory else {}
        return BuilderElement(
            id=str(uuid4()), content=BuilderElementContent(**data), **base
        )

    # $slot: bound leaf (text / link / image). Missing/empty optional -> dropped.
    if node.get("$slot"):
        value = scope.get(node["$slot"])
        if value is None or value == "":
            return None
        node_content = node.get("content")
        slot_base = node_content if isinstance(node_content, dict) else {}
        bound = await _bind_slot(node["type"], value, slot_base, resolve_image)
        return BuilderElement(id=str(uuid4()), content=bound, **base)

    # Container: recurse children in the same scope.
    content = node.get("content")
    if isinstance(content, list):
        children = []
        for child in content:
            el = await _fill_node(child, scope, resolve_image, factories, theme)
            if el is not None:
                children.append(el)
        # Prune a container that filled to nothing — e.g. a card/list-item whose
        # text slots were all blank (an LLM-produced empty item). Left in, it
        # renders as a stray placeholder box. Cascades up through bare wrappers.
        # Keep it only if it carries its own decorative background image.
        if not children and not (
            styles.get("backgroundImage") or styles.get("background")
        ):
            return None
        return BuilderElement(id=str(uuid4()), content=children, **base)

    # Static leaf: literal content kept verbatim.
    leaf = content if isinstance(content, dict) else {}
    return BuilderElement(id=str(uuid4()), content=BuilderElementContent(**leaf), **base)


async def fill_template(
    template: dict[str, Any],
    content: dict[str, Any],
    *,
    resolve_image: ResolveImage,
    content_factories: dict[str, ContentFactory] | None = None,
    theme: ThemeColors | None = None,
) -> BuilderElement:
    """Build a concrete ``BuilderElement`` tree from a catalog entry + content.

    When ``theme`` ({"primary","secondary"} hex) is supplied, photo backgrounds
    get a brand-tinted, luminance-adaptive overlay; otherwise the template's
    static overlay format is used.
    """
    root = await _fill_node(
        template["tree"], content, resolve_image, content_factories or {}, theme
    )
    if root is None:
        raise ValueError(f"Template {template.get('id')!r} filled to nothing")
    return root
