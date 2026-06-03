"""
One-off migration: rewrite every legacy ``type: "section"`` element to
``type: "container"`` in page schemas already saved in the webtree CMS.

Why this exists
---------------
The site generator used to emit page sections with ``type="section"``. The
builder has no renderer for ``section`` (both render switches fall through to an
empty default), so those pages open blank. The generator is now fixed to emit
``container``, but pages pushed *before* the fix still carry ``section`` in the
schema stored in the CMS DB — fixing the generator does nothing for them. This
script rewrites the stored schemas in place.

Scope of the rewrite
--------------------
- Each page's draft ``bodySchema`` (the real source of the bug).
- The entity-scoped layout ``headerSchema`` / ``footerSchema`` (defensive — the
  generator never put ``section`` there, but hand-edits could).
- With ``--publish``, pages that are currently *published* get re-published so
  the live revision reflects the fix too (draft-only pages are left as drafts).

Safety
------
- Dry-run by default. Pass ``--apply`` to actually write.
- Operates only on the entity tokens you pass — nothing global.

Usage
-----
    # from the backend/ directory, with the venv that has requirements installed
    python -m scripts.migrate_section_to_container \\
        --email you@example.com --password 'secret' \\
        --entity TOKEN1 [--entity TOKEN2 ...] \\
        [--apply] [--publish]

Credentials/base URL fall back to env: CMS_EMAIL, CMS_PASSWORD, CMS_API_BASE_URL.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Any

# Allow running as a module from backend/ (python -m scripts.migrate_...).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.cms_client import CmsApiError, CmsClient  # noqa: E402

LEGACY_TYPE = "section"
TARGET_TYPE = "container"


def _rewrite_element(node: Any, counter: list[int]) -> Any:
    """Recursively rewrite section→container in a single element dict.

    Walks ``content`` whether it's a list of child elements or a leaf content
    object. Mutates and returns ``node``; bumps ``counter[0]`` per rewrite.
    """
    if not isinstance(node, dict):
        return node
    if node.get("type") == LEGACY_TYPE:
        node["type"] = TARGET_TYPE
        counter[0] += 1
    content = node.get("content")
    if isinstance(content, list):
        node["content"] = [_rewrite_element(child, counter) for child in content]
    # Leaf content (BuilderElementContent) never holds nested elements/types,
    # so there's nothing to recurse into there.
    return node


def _rewrite_elements(elements: Any, counter: list[int]) -> Any:
    if not isinstance(elements, list):
        return elements
    return [_rewrite_element(el, counter) for el in elements]


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    """The builder endpoint returns the payload at top level, but tolerate a
    ``{"data": {...}}`` wrapper just in case the API shape differs."""
    if "bodySchema" not in payload and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


async def _migrate_entity(
    client: CmsClient,
    entity_token: str,
    *,
    apply: bool,
    publish: bool,
) -> dict[str, int]:
    stats = {"pages": 0, "pages_changed": 0, "body_rewrites": 0, "layout_rewrites": 0, "published": 0}

    pages = await client.list_pages(entity_token)
    stats["pages"] = len(pages)
    if not pages:
        print(f"  entity {entity_token[:8]}…: no pages")
        return stats

    # --- entity-scoped layout (header/footer) — read once off any page -------
    first_id = str(pages[0].get("id"))
    payload = _unwrap(await client.get_builder_payload(entity_token, first_id))
    layout = payload.get("layout") or {}
    layout_version_id = layout.get("versionId")

    header = payload.get("headerSchema") or {}
    footer = payload.get("footerSchema") or {}
    menus = payload.get("menus") or []
    layout_counter = [0]
    header["elements"] = _rewrite_elements(header.get("elements"), layout_counter)
    footer["elements"] = _rewrite_elements(footer.get("elements"), layout_counter)
    if layout_counter[0]:
        stats["layout_rewrites"] = layout_counter[0]
        print(
            f"  layout: {layout_counter[0]} section→container in header/footer"
            + ("" if apply else " (dry-run)")
        )
        if apply:
            result = await client.save_page_layout(
                entity_token,
                first_id,
                expected_layout_version_id=layout_version_id,
                header_schema=header,
                footer_schema=footer,
                menus=menus,
            )
            layout_version_id = result.get("versionId") or layout_version_id

    # --- per-page body schema ----------------------------------------------
    for meta in pages:
        page_id = str(meta.get("id"))
        slug = meta.get("slug") or ("/" if meta.get("isHomepage") else "?")
        p = _unwrap(await client.get_builder_payload(entity_token, page_id))
        page_meta = p.get("page") or {}
        base_draft_version = int(page_meta.get("draftVersion") or 1)
        status = page_meta.get("status")
        body = p.get("bodySchema") or {}

        counter = [0]
        body["elements"] = _rewrite_elements(body.get("elements"), counter)
        if not counter[0]:
            continue

        stats["pages_changed"] += 1
        stats["body_rewrites"] += counter[0]
        print(
            f"  page {slug!r} ({page_id[:8]}…): {counter[0]} section→container"
            + ("" if apply else " (dry-run)")
        )
        if not apply:
            continue

        result = await client.save_page_draft(
            entity_token, page_id, base_draft_version=base_draft_version, body_schema=body
        )
        new_draft_version = int(result.get("draftVersion") or base_draft_version + 1)

        # Re-publish only pages that already had a live revision, so the public
        # site reflects the fix. Draft-only pages stay drafts.
        if publish and status == "published" and layout_version_id:
            await client.publish_page(
                entity_token,
                page_id,
                expected_draft_version=new_draft_version,
                expected_layout_version_id=layout_version_id,
            )
            stats["published"] += 1
            print(f"    re-published {slug!r}")

    return stats


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default=os.getenv("CMS_EMAIL"))
    parser.add_argument("--password", default=os.getenv("CMS_PASSWORD"))
    parser.add_argument(
        "--entity",
        action="append",
        default=[],
        help="Entity API token to migrate (repeatable).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually write changes. Without this flag the script is a dry-run.",
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Re-publish pages that are currently published so the live site updates.",
    )
    args = parser.parse_args()

    if not args.email or not args.password:
        parser.error("CMS credentials required (--email/--password or CMS_EMAIL/CMS_PASSWORD).")
    if not args.entity:
        parser.error("At least one --entity TOKEN is required.")

    client = CmsClient.for_default()
    print(f"CMS: {client.base_url}")
    try:
        await client.login(args.email, args.password)
    except CmsApiError as exc:
        print(f"Login failed: {exc}", file=sys.stderr)
        return 1

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"Mode: {mode}{' + PUBLISH' if args.publish else ''}\n")

    totals = {"pages": 0, "pages_changed": 0, "body_rewrites": 0, "layout_rewrites": 0, "published": 0}
    for token in args.entity:
        print(f"Entity {token[:8]}…")
        try:
            stats = await _migrate_entity(
                client, token, apply=args.apply, publish=args.publish
            )
        except CmsApiError as exc:
            print(f"  ERROR: {exc}", file=sys.stderr)
            return 2
        for k in totals:
            totals[k] += stats[k]
        print()

    print("Summary:")
    print(f"  pages scanned     : {totals['pages']}")
    print(f"  pages changed     : {totals['pages_changed']}")
    print(f"  body rewrites     : {totals['body_rewrites']}")
    print(f"  layout rewrites   : {totals['layout_rewrites']}")
    if args.publish:
        print(f"  pages re-published: {totals['published']}")
    if not args.apply and (totals["body_rewrites"] or totals["layout_rewrites"]):
        print("\nDry-run only — re-run with --apply to write these changes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
