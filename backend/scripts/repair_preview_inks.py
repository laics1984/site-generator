#!/usr/bin/env python3
"""Repair an exported v2 preview's foregrounds without regenerating/publishing.

Usage: .venv/bin/python scripts/repair_preview_inks.py input.json output.json
The original export is retained; only text colors and derived cssVars change.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.brand import ThemeTokens
from app.models.builder_schema import GeneratedSite
from app.services.palette_styles import site_surface_foregrounds


def repair(raw: dict) -> dict:
    site = GeneratedSite.model_validate(raw)
    theme = ThemeTokens.model_validate(site.theme)
    if theme.color_system_version != 2:
        raise ValueError("Only generated color-system-v2 previews can be repaired.")
    variables = site_surface_foregrounds(site, theme, bind=True)
    repaired_colors: dict[str, str] = {}

    def collect(node):
        if node.type in ("text", "link") and "color" in node.styles:
            repaired_colors[node.id] = node.styles["color"]
        if isinstance(node.content, list):
            for child in node.content:
                collect(child)
    for page in site.pages:
        for node in page.body_schema.elements:
            collect(node)
    for node in (site.header_schema, site.footer_schema):
        if node:
            collect(node)

    # Project onto the RAW export: model defaults/coercion must not change
    # unrelated metadata, even a numeric confidence's JSON representation.
    result = deepcopy(raw)
    def apply(value):
        if isinstance(value, dict):
            if value.get("type") in ("text", "link") and value.get("id") in repaired_colors:
                value.setdefault("styles", {})["color"] = repaired_colors[value["id"]]
            if isinstance(value.get("content"), list):
                apply(value["content"])
        elif isinstance(value, list):
            for child in value:
                apply(child)
    for page in result.get("pages", []):
        apply(page.get("body_schema", {}).get("elements", []))
    for key in ("header_schema", "footer_schema"):
        apply(result.get(key))
    if variables:
        styles = result.setdefault("builder_styles", {})
        styles["cssVars"] = {**styles.get("cssVars", {}), **variables}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.output.resolve() or args.output.exists():
        parser.error("Choose a new output path; existing files are never overwritten.")
    result = repair(json.loads(args.source.read_text()))
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(f"Wrote foreground-only repair to {args.output}. Nothing was published.")


if __name__ == "__main__":
    main()
