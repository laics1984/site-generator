"""Generate industry vocabulary from the CMS-owned catalog.

Run from backend: python3 scripts/sync_industries.py [--check]
Copies are generated code, not independently maintained catalogs. Like the
section catalog, they keep generation offline and reproducible.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SOURCE = BACKEND.parent.parent / "webtree-cms-api/resources/data/industries.json"
PYTHON = BACKEND / "app/models/industry_catalog.py"
TYPESCRIPT = BACKEND.parent / "frontend/src/lib/industryCatalog.ts"
HEADER = "Generated from webtree-cms-api/resources/data/industries.json.\nRun backend/scripts/sync_industries.py; do not edit by hand."


def load_catalog(path: Path) -> dict[str, str]:
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate industry: {key}")
            result[key] = value
        return result
    catalog = json.loads(path.read_text(), object_pairs_hook=unique)
    if not isinstance(catalog, dict) or "other" not in catalog:
        raise ValueError("Industry catalog must be a map containing other")
    if any(not re.fullmatch(r"[a-z]+(?:-[a-z]+)*", key)
           or not isinstance(label, str) or not label.strip()
           for key, label in catalog.items()):
        raise ValueError("Industries require slug keys and non-empty labels")
    return catalog


def generated_files(catalog: dict[str, str]) -> dict[Path, str]:
    labels = json.dumps(catalog, ensure_ascii=False, indent=4)
    literal = "\n".join(f'    "{slug}",' for slug in catalog)
    python = f'"""{HEADER}"""\nfrom typing import Literal\n\nINDUSTRY_LABELS: dict[str, str] = {labels}\n\nIndustryCategoryLiteral = Literal[\n{literal}\n]\n'
    union = "\n".join(f"  | '{slug}'" for slug in catalog)
    typescript = f"/** {HEADER.replace(chr(10), chr(10) + ' * ')} */\nexport type IndustryCategory =\n{union}\n"
    return {PYTHON: python, TYPESCRIPT: typescript}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--source", type=Path, default=SOURCE)
    args = parser.parse_args()
    try:
        files = generated_files(load_catalog(args.source))
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Cannot read industry source: {exc}\n")
    stale = []
    for path, expected in files.items():
        if args.check:
            if not path.exists() or path.read_text() != expected:
                stale.append(str(path))
        else:
            path.write_text(expected)
    if stale:
        print("Industry catalog drift; run backend/scripts/sync_industries.py:\n" + "\n".join(stale))
        return 1
    print(f"Industry catalog {'in sync' if args.check else 'synchronized'}: {len(files)} generated files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
