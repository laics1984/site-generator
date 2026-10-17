"""Screenshot dumped benchmark sites through the real preview renderer.

    # 1. build sites (real photos through a cache, so two revisions match)
    cd backend && PEXELS_API_KEY=… .venv/bin/python scripts/design_benchmark.py \
        --no-variants --real-stock /tmp/stock.json --dump /tmp/sites
    # 2. serve the frontend (any port), then:
    cd backend && .venv/bin/python scripts/design_screenshots.py /tmp/sites /tmp/shots \
        --url http://localhost:5176/bench.html --widths 1280,390

For each site the layout `/api/preview/layout` would return is computed
in-process, the pair is injected into `frontend/bench.html` (the same
PreviewFrame → PreviewSiteShell → SchemaRenderer tree PagePreview mounts), and
the frame's document is copied into a page of the target width for a full-page
screenshot — element screenshots inside a small iframe clip.

Screenshots are for human review and before/after evidence. They are never an
input to generation (the runtime evaluator is deterministic on purpose).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.builder_schema import GeneratedSite  # noqa: E402
from app.routers.preview import preview_layout  # noqa: E402

_VIEWPORT_HEIGHT = 900


async def shoot(sites: Path, out: Path, url: str, widths: list[int], only: set[str] | None) -> list[Path]:
    from playwright.async_api import async_playwright

    out.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        for path in sorted(sites.glob("*.json")):
            if only and path.stem not in only:
                continue
            raw = json.loads(path.read_text())
            layout = await preview_layout(GeneratedSite.model_validate(raw))
            payload = json.dumps({"site": raw, "layout": layout})
            for width in widths:
                page = await browser.new_page(viewport={"width": width, "height": _VIEWPORT_HEIGHT})
                await page.add_init_script(f"window.__BENCH__ = {payload};")
                await page.goto(url)
                await page.wait_for_function("window.__BENCH_READY__ === true", timeout=30000)
                frame = page.frames[1] if len(page.frames) > 1 else page.main_frame
                html = await frame.content()
                await page.close()
                shot = await browser.new_page(viewport={"width": width, "height": _VIEWPORT_HEIGHT})
                await shot.set_content(html, wait_until="networkidle", timeout=60000)
                await shot.wait_for_timeout(600)
                target = out / f"{path.stem}-{width}.jpg"
                await shot.screenshot(path=str(target), full_page=True, type="jpeg", quality=72)
                await shot.close()
                written.append(target)
        await browser.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sites", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--url", default="http://localhost:5176/bench.html")
    parser.add_argument("--widths", default="1280,390")
    parser.add_argument("--only", default="", help="comma-separated site keys")
    args = parser.parse_args()
    widths = [int(w) for w in args.widths.split(",") if w]
    only = {k for k in args.only.split(",") if k} or None
    for path in asyncio.run(shoot(args.sites, args.out, args.url, widths, only)):
        print(path)


if __name__ == "__main__":
    main()
