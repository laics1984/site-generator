"""Capture what the anonymous Facebook reader sees, for offline replay.

Facebook's markup is undocumented and changes without notice, so a parser
change should be judged against real captured Pages, not only against inline
test strings. This drives the SAME browser setup (`browser.browser_context`) and
the SAME navigation as `facebook_render.fetch_page` — the About/Page ladder, then
each view with its lazy-load scrolls — and saves every step's HTML, gzipped, with
a manifest. `scripts/measure_facebook_read.py` replays a capture through the
reader's own parsers.

Captures hold real people's names and words (captions, reviewers), so they go
to `backend/.facebook-captures/` — gitignored — and never into the repo.

    cd backend && .venv/bin/python scripts/capture_facebook_page.py \\
        https://www.facebook.com/<page>/

One capture costs the page loads of one real read (≈4 renders). It reads only
what a logged-out visitor is served and stops at the first wall, like the reader.
"""

from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.services.browser import browser_context, rendered_page  # noqa: E402
from app.services.facebook_dom import load_view  # noqa: E402
from app.services.facebook_render import _VIEWS, _candidate_urls, _scroll  # noqa: E402
from app.services.facebook_urls import parse_ref, tab_url  # noqa: E402

CAPTURES = Path(__file__).resolve().parents[1] / ".facebook-captures"


def _save(folder: Path, name: str, html: str) -> str:
    filename = f"{name}.html.gz"
    (folder / filename).write_bytes(gzip.compress(html.encode("utf-8")))
    return filename


async def capture(url: str) -> Path:
    ref = parse_ref(url)
    folder = CAPTURES / (ref.handle or ref.page_id or "page")
    folder.mkdir(parents=True, exist_ok=True)
    timeout_ms = int(settings.facebook_timeout_seconds * 1000)
    manifest: dict = {"source_url": url, "steps": []}

    async with browser_context() as context:
        ladder_url = None
        for candidate in _candidate_urls(ref):
            try:
                async with rendered_page(context, candidate, timeout_ms=timeout_ms) as page:
                    html, final_url = await page.content(), page.url
            except Exception as exc:  # noqa: BLE001 — report and try the next rung
                print(f"ladder: {candidate} failed ({exc})")
                continue
            manifest["ladder"] = {"file": _save(folder, "ladder", html), "url": final_url,
                                  "requested": candidate}
            ladder_url = candidate
            print(f"ladder: {candidate} → {final_url} ({len(html):,} chars)")
            break
        if ladder_url is None:
            raise SystemExit("No ladder rung rendered; nothing captured.")

        for view in _VIEWS:
            view_url = tab_url(ref, view.tab)
            if not view_url or view_url == ladder_url:
                continue
            name = view.tab or "timeline"
            try:
                async with rendered_page(context, view_url, timeout_ms=timeout_ms) as page:
                    steps = settings.facebook_view_scroll_steps if view.scroll else 0
                    for step in range(steps + 1):
                        if step:
                            await _scroll(page)
                        html = await page.content()
                        manifest["steps"].append({
                            "view": view.tab, "step": step, "url": page.url,
                            "file": _save(folder, f"{name}_{step}", html),
                        })
                        print(f"{name} step {step}: {len(html):,} chars")
                        if load_view(html, page.url).is_wall:
                            print(f"{name}: walled — stopping, as the reader does")
                            raise StopAsyncIteration
            except StopAsyncIteration:
                break
            except Exception as exc:  # noqa: BLE001 — a failed view costs only itself
                print(f"{name}: unavailable ({exc})")

    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"saved {folder}")
    return folder


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("url", help="A Facebook Page URL")
    asyncio.run(capture(parser.parse_args().url))


if __name__ == "__main__":
    main()
