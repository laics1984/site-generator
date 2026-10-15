"""Replay language analysis over captured sources and print what it decides.

Offline, no network. Every `language_analysis` change states its before/after
here (docs/multilingual-plan.md, "Progress and corrections"):

    cd backend && .venv/bin/python scripts/measure_language_analysis.py              # every capture
    cd backend && .venv/bin/python scripts/measure_language_analysis.py mmta.org.my -v
    cd backend && .venv/bin/python scripts/measure_language_analysis.py --fixtures   # Scenarios A-F

Captures come from `scripts/capture_language_source.py` (gitignored, real copy);
`--fixtures` adds the committed synthetic Scenarios A-F in
`tests/fixtures/languages/`, so the replay runs even where no capture exists.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.content_blocks import SourceContent  # noqa: E402
from app.services.language_analysis import analyze  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1]
CAPTURES = BACKEND / ".language-captures"
FIXTURES = BACKEND / "tests" / "fixtures" / "languages"


def load(path: Path) -> tuple[SourceContent, str | None]:
    """A capture or a fixture file → (source, brand name the reader found)."""
    data = json.loads(path.read_text())
    return SourceContent.model_validate(data["source"]), data.get("brand_name")


def report(name: str, path: Path, verbose: bool) -> None:
    source, brand = load(path)
    profile = analyze(source, brand_name=brand)
    pages = 1 + len(source.discovered_pages)
    print(f"\n{name} — {pages} page(s) — {profile.pattern}, primary {profile.primary}")
    print(f"  {profile.reason}")
    for item in profile.languages:
        flag = "substantial" if item.substantial else "below threshold"
        print(
            f"  {item.code:8s} share {item.share:6.1%}  amount {item.amount:6d}  "
            f"confidence {item.confidence:.2f}  coverage {item.coverage:.2f}  {flag}"
        )
    if verbose:
        for line in profile.evidence:
            print(f"  - {line}")
        for page in profile.pages:
            print(f"  page {page.path:40s} {page.locale or '?':8s} url={page.url_locale or '-':6s} declared={page.declared or '-'}")
        for pair in profile.pairs[:12]:
            print(f"  pair [{pair.kind}] {pair.first[:40]!r} ({pair.first_lang}) / {pair.second[:40]!r} ({pair.second_lang})")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", nargs="*", help="capture names (default: all)")
    parser.add_argument("--fixtures", action="store_true", help="also replay Scenarios A-F")
    parser.add_argument("-v", "--verbose", action="store_true", help="evidence, pages and pairs")
    args = parser.parse_args()
    files = [CAPTURES / f"{name}.json" for name in args.names] or sorted(CAPTURES.glob("*.json"))
    if args.fixtures:
        files += sorted(FIXTURES.glob("*.json"))
    if not files:
        raise SystemExit(
            f"No captures in {CAPTURES}. Run scripts/capture_language_source.py first, "
            "or pass --fixtures for the synthetic Scenarios A-F."
        )
    for path in files:
        report(path.stem, path, args.verbose)


if __name__ == "__main__":
    main()
