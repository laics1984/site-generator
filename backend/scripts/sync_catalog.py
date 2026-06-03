"""
Sync the section-template catalog from the builder (source of truth) into the
generator's vendored copy, or check that they are in sync.

The builder owns `builder/src/templates/section-catalog.json`. The generator
vendors a byte-identical copy at `backend/app/templates/section_catalog.json`
(loaded by template_filler). This script keeps them aligned.

Usage:
  python3 scripts/sync_catalog.py            # copy builder -> generator
  python3 scripts/sync_catalog.py --check    # exit 1 if they differ (for CI)
"""
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
VENDORED = BACKEND / "app" / "templates" / "section_catalog.json"
# backend -> site-generator -> webtree -> builder/src/templates/section-catalog.json
SOURCE = BACKEND.parent.parent / "builder" / "src" / "templates" / "section-catalog.json"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    if not SOURCE.exists():
        print(f"ERROR: builder catalog not found at {SOURCE}")
        return 2
    source_text = SOURCE.read_text()
    vendored_text = VENDORED.read_text() if VENDORED.exists() else None

    if check:
        if source_text == vendored_text:
            print(f"in sync ✓  ({SOURCE.name} == {VENDORED.name})")
            return 0
        print("DRIFT: vendored generator catalog differs from the builder source.")
        print(f"  builder:   {SOURCE}")
        print(f"  generator: {VENDORED}")
        print("  run: python3 scripts/sync_catalog.py")
        return 1

    VENDORED.parent.mkdir(parents=True, exist_ok=True)
    VENDORED.write_text(source_text)
    print(f"synced {SOURCE} -> {VENDORED} ({len(source_text)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
