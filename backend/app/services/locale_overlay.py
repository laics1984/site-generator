"""A page's text in another language, as an overlay on its ONE shared tree.

docs/multilingual-plan.md §3: the CMS stores one body per page and each other
language as an overlay keyed by element id (webtree-cms-api
App\\Services\\Localization\\OverlayApplier — this module is its Python mirror,
and `test_locale_overlay.OverlayMirrorTest` pins the shared constants).

Three uses, one representation:

- `derive` turns a page's string map (primary string → translation, from
  `translations.translate_strings`) into an overlay on the BUILT tree, by the
  same matching the same-page bilingual pass uses (`match`).
- `apply` puts an overlay on a copy of the tree — how a translated page's
  preview body is made, so the preview is the shared tree plus the overlay,
  exactly what the CMS serves, never a second build that could differ.
- `missing` lists what an overlay doesn't cover yet, the CMS's publish test.

Menus are overlaid by item id too (`menu_overlay` builds that part of a layout
overlay, `apply_menus` applies it), mirroring OverlayApplier::applyToMenus.
"""

from __future__ import annotations

import copy
import hashlib
from datetime import datetime, timezone
from collections.abc import Callable, Iterator
from typing import Any

from app.models.builder_schema import BuilderElement, BuilderElementContent

# Mirrors OverlayApplier::TEXT_FIELDS: the only fields an overlay may change.
TEXT_FIELDS = ("innerText", "alt", "title", "ariaLabel", "caption")

# Mirrors OverlayApplier::FILE_NAME_EXTENSIONS. A text that is only a file's
# name (an image alt a site builder filled with "menu cover.jpg") is in no
# language, so no language is missing it: never asked of a translator, never
# blocking a publish.
FILE_NAME_EXTENSIONS = ("jpg", "jpeg", "png", "gif", "webp", "svg", "avif", "heic", "bmp",
                        "tif", "tiff", "pdf", "doc", "docx", "mp4", "mov")


def is_file_name(text: str) -> bool:
    """`menu cover.jpg`, `kindies_edited.jpg` — a name with a file extension
    and nothing else: no path, no line break, no second-language pair."""
    value = text.strip()
    stem, dot, extension = value.rpartition(".")
    return bool(
        dot and stem.strip() and len(value) <= 160
        and extension.lower() in FILE_NAME_EXTENSIONS
        and not any(char in value for char in "/\\|\n")
    )


def needs_translation(value: object) -> bool:
    """A field value some other language must say in its own words."""
    return isinstance(value, str) and bool(value.strip()) and not is_file_name(value)

# Quotation marks a mapper wraps around a string (a testimonial's “…”): the
# planner's string is inside them, and the translation wears the same marks.
_QUOTES = "\"'“”‘’「」『』«»"


# --- matching a built tree's strings to a string map ------------------------------------


def text_of(element: BuilderElement) -> str | None:
    content = element.content
    if element.type == "text" and isinstance(content, BuilderElementContent):
        return (content.innerText or "").strip() or None
    return None


def joined_lines(element: BuilderElement) -> tuple[str, BuilderElement] | None:
    """(the whole string, its first line) for a container of text lines only —
    a split headline's group, whose lines join with a space back into the one
    string the planner wrote."""
    content = element.content
    if element.type != "container" or not isinstance(content, list) or len(content) < 2:
        return None
    texts = [text_of(child) for child in content]
    if any(text is None for text in texts):
        return None
    return " ".join(texts), content[0]  # type: ignore[arg-type]


def lookup(text: str, mapping: dict[str, str]) -> str | None:
    """`text`'s translation, also through quote marks a mapper added."""
    if text in mapping:
        return mapping[text]
    inner = text.strip(_QUOTES).strip()
    if inner != text and inner in mapping:
        lead = text[: len(text) - len(text.lstrip(_QUOTES))]
        tail = text[len(text.rstrip(_QUOTES)):]
        return f"{lead}{mapping[inner]}{tail}"
    return None


# --- overlays ---------------------------------------------------------------------------------


def source_hash(element: BuilderElement) -> str | None:
    """sha1 of an element's translatable text — mirrors OverlayApplier::sourceHash,
    so a translation made here reads as current on the CMS until the text changes."""
    content = element.content
    if not isinstance(content, BuilderElementContent):
        return None
    parts = [
        f"{field}\0{value.strip()}"
        for field in TEXT_FIELDS
        if isinstance(value := getattr(content, field, None), str) and value.strip()
    ]
    return hashlib.sha1("\n".join(parts).encode()).hexdigest() if parts else None


def source_meta(element: BuilderElement, origin: str) -> dict | None:
    """An overlay entry's meta: who wrote it, and what primary text it was made
    from — the hash, that text field by field, and when. The builder lists a
    translation as "changed since translated" when the hash no longer matches,
    and diffs `sourceText` against the text now (builder
    docs/translation-sync-plan.md, "Tracking what changed"; same keys as the
    builder's `stampSourceHashes`)."""
    digest = source_hash(element)
    if digest is None:
        return None
    content = element.content
    text = {
        field: value
        for field in TEXT_FIELDS
        if isinstance(value := getattr(content, field, None), str) and value.strip()
    }
    return {
        "origin": origin,
        "sourceHash": digest,
        "sourceText": text,
        "translatedAt": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
    }


def _walk(elements: list[BuilderElement]) -> Iterator[BuilderElement]:
    for element in elements:
        yield element
        if isinstance(element.content, list):
            yield from _walk(element.content)


def derive(
    elements: list[BuilderElement], mapping: dict[str, str], *, origin: str
) -> dict[str, dict]:
    """`{"elements": …, "meta": …}` for every string of the tree `mapping` translates.

    A split headline is one string: its translation goes on the lead line and
    the accent line is hidden — another language rarely splits at the same
    word, and an overlay can't blank a field, only hide an element."""
    out: dict[str, dict] = {"elements": {}, "meta": {}}

    def put(element: BuilderElement, patch: dict) -> None:
        out["elements"][element.id] = {**out["elements"].get(element.id, {}), **patch}
        if (meta := source_meta(element, origin)) is not None:
            out["meta"][element.id] = meta

    for element in _walk(elements):
        if (group := joined_lines(element)) is not None and (line := lookup(group[0], mapping)):
            put(group[1], {"innerText": line})
            for other in element.content[1:]:  # type: ignore[index]
                out["elements"][other.id] = {"hidden": True}
            continue
        content = element.content
        if not isinstance(content, BuilderElementContent):
            continue
        for field in TEXT_FIELDS:
            value = getattr(content, field, None)
            if isinstance(value, str) and value.strip() and element.id not in out["elements"]:
                if (translated := lookup(value.strip(), mapping)) is not None:
                    put(element, {field: translated})
    return out


def as_text_map(elements: list[BuilderElement], overlay: dict | None) -> dict[str, str]:
    """The overlay read back as primary string → translation — `derive`'s
    inverse, for reusing one page's words on another tree (the header)."""
    patches = (overlay or {}).get("elements") or {}
    out: dict[str, str] = {}
    for element in _walk(elements):
        patch = patches.get(element.id) or {}
        content = element.content
        if not isinstance(content, BuilderElementContent):
            continue
        for field in TEXT_FIELDS:
            value, translated = getattr(content, field, None), patch.get(field)
            if isinstance(value, str) and value.strip() and isinstance(translated, str) and translated.strip():
                out.setdefault(value.strip(), translated)
    return out


def missing(elements: list[BuilderElement], overlay: dict | None) -> list[BuilderElement]:
    """Text-bearing elements the overlay neither covers nor hides — mirrors
    OverlayApplier::missingStrings, so what this says is what the CMS's publish
    step will refuse."""
    patches = (overlay or {}).get("elements") or {}

    def hidden(element: BuilderElement) -> bool:
        return bool((patches.get(element.id) or {}).get("hidden"))

    found: list[BuilderElement] = []

    def visit(items: list[BuilderElement]) -> None:
        for element in items:
            if hidden(element):
                continue
            content = element.content
            if isinstance(content, BuilderElementContent) and any(
                needs_translation(getattr(content, f, None)) for f in TEXT_FIELDS
            ):
                patch = patches.get(element.id) or {}
                if not any(isinstance(patch.get(f), str) and patch[f].strip() for f in TEXT_FIELDS):
                    found.append(element)
            if isinstance(element.content, list):
                visit(element.content)

    visit(elements)
    return found


def apply(
    elements: list[BuilderElement],
    overlay: dict | None,
    localize_href: Callable[[str], str] | None = None,
) -> list[BuilderElement]:
    """A COPY of `elements` with the overlay's text, hidden elements removed and
    internal links localized — mirrors OverlayApplier::applyToSchema."""
    patches = (overlay or {}).get("elements") or {}

    def one(element: BuilderElement) -> BuilderElement | None:
        patch = patches.get(element.id) or {}
        if patch.get("hidden") is True:
            return None
        clone = element.model_copy(deep=True)
        content = clone.content
        if isinstance(content, list):
            clone.content = [kept for child in content if (kept := one(child)) is not None]
        elif isinstance(content, BuilderElementContent):
            for field in TEXT_FIELDS:
                value = patch.get(field)
                if isinstance(value, str) and value.strip():
                    setattr(content, field, value)
            if localize_href and isinstance(content.href, str):
                content.href = localize_href(content.href)
        return clone

    return [kept for element in copy.copy(elements) if (kept := one(element)) is not None]


# --- menus ------------------------------------------------------------------------------

# A language menu's items each name their own language and point at that
# language's home: never relabelled, never localized (OverlayApplier::applyToMenus).
LANGUAGE_MENU = "language"


def menu_overlay(
    menus: list[dict[str, Any]], titles: dict[str, str], labels: dict[str, str]
) -> dict[str, dict[str, dict[str, str]]]:
    """`{menu id: {item id: {"label"}}}` — a layout overlay's `menus`, for the
    menus as they will be saved (item ids are minted per build, so this is
    always worked out against the menus actually in hand).

    An item linking a page translated in this language takes that page's
    title there (`titles`: primary slug → translated title); any other item —
    a legal link, a group heading, a page not built in this language — takes
    its label's translation (`labels`: primary label → translation)."""
    out: dict[str, dict[str, dict[str, str]]] = {}

    def visit(menu_id: str, items: list[dict[str, Any]]) -> None:
        for item in items or []:
            href = str(item.get("href") or "")
            slug = href.split("#", 1)[0].strip("/") if href.startswith("/") else None
            label = (titles.get(slug) if slug is not None else None) or labels.get(str(item.get("label") or "").strip())
            if label and item.get("id"):
                out.setdefault(menu_id, {})[str(item["id"])] = {"label": label}
            visit(menu_id, item.get("children") or [])

    for menu in menus:
        if menu.get("purpose") != LANGUAGE_MENU:
            visit(str(menu.get("id")), menu.get("items") or [])
    return out


def apply_menus(
    menus: list[dict[str, Any]],
    overlay: dict | None,
    localize_href: Callable[[str], str] | None = None,
) -> list[dict[str, Any]]:
    """A COPY of `menus` with the overlay's labels and internal links localized —
    mirrors OverlayApplier::applyToMenus, the language menu included."""
    labels = (overlay or {}).get("menus") or {}

    def items(entries: list[dict[str, Any]], own: dict, localize: Callable[[str], str] | None) -> list:
        out = []
        for entry in entries:
            item = dict(entry)
            label = (own.get(str(item.get("id") or "")) or {}).get("label")
            if isinstance(label, str) and label.strip():
                item["label"] = label
            if localize and isinstance(item.get("href"), str):
                item["href"] = localize(item["href"])
            if isinstance(item.get("children"), list):
                item["children"] = items(item["children"], own, localize)
            out.append(item)
        return out

    return [
        {**menu, "items": items(
            menu.get("items") or [], labels.get(str(menu.get("id"))) or {},
            None if menu.get("purpose") == LANGUAGE_MENU else localize_href,
        )}
        for menu in menus
    ]
