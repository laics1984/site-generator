"""Capture what the crawler reads from a site, for offline language measurement.

`language_analysis` thresholds are calibrated on real sites, not guesses
(docs/multilingual-plan.md, P0). This runs the SAME reader the app runs
(`scraper.scrape_url`, crawl included) and saves the resulting `SourceContent`
— before any language analysis, so a replay always measures the current rules —
with the brand name the reader found. `scripts/measure_language_analysis.py`
replays the captures.

Captures hold real copy (and real people's names), so they go to
`backend/.language-captures/` — gitignored — and never into the repo.

    cd backend && .venv/bin/python scripts/capture_language_source.py https://mmta.org.my --pages 30
    cd backend && .venv/bin/python scripts/capture_language_source.py https://uniquekindies.com https://watr.org.my

The measurement set the plan names: mmta.org.my (/bm and /zh mirrors),
uniquekindies.com (bilingual headings, mixed prose), watr.org.my and
mykiddyland.com (English only).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.scraper import ScrapeError, scrape_url  # noqa: E402

CAPTURES = Path(__file__).resolve().parents[1] / ".language-captures"


def capture_name(url: str) -> str:
    host = (urlparse(url).hostname or "site").removeprefix("www.")
    return re.sub(r"[^a-z0-9.-]+", "-", host.lower())


async def capture(url: str, *, pages: int, depth: int) -> Path | None:
    started = time.monotonic()
    try:
        result = await scrape_url(url, crawl=True, crawl_max_pages=pages, crawl_max_depth=depth)
    except ScrapeError as exc:
        print(f"{url}: not captured — {exc} (status {exc.status})")
        return None
    source = result.source_content
    CAPTURES.mkdir(parents=True, exist_ok=True)
    path = CAPTURES / f"{capture_name(url)}.json"
    path.write_text(json.dumps({
        "url": url,
        "final_url": result.final_url,
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "crawl_stop_reason": result.crawl_stop_reason,
        "brand_name": result.brand_candidate.name if result.brand_candidate else None,
        "source": source.model_dump(mode="json"),
    }, ensure_ascii=False, indent=2) + "\n")
    print(
        f"{url}: {1 + len(source.discovered_pages)} page(s), "
        f"{len(source.raw_text):,} chars on the entry page, "
        f"{time.monotonic() - started:.1f}s → {path}"
    )
    return path


async def _main(urls: list[str], pages: int, depth: int) -> None:
    for url in urls:
        await capture(url, pages=pages, depth=depth)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("urls", nargs="+", help="site URLs to crawl")
    parser.add_argument("--pages", type=int, default=30, help="crawl page cap (default 30)")
    parser.add_argument("--depth", type=int, default=3, help="crawl depth (default 3)")
    args = parser.parse_args()
    asyncio.run(_main(args.urls, args.pages, args.depth))


if __name__ == "__main__":
    main()
