"""Replay a captured Facebook read through the reader's own parsers and report
what it yields — offline, no browser, no network.

Every Facebook reader change states its before/after here, on real markup
(`scripts/capture_facebook_page.py` makes the captures):

    cd backend && .venv/bin/python scripts/measure_facebook_read.py            # every capture
    cd backend && .venv/bin/python scripts/measure_facebook_read.py core -v    # one, with posts

The replay is `facebook_render.fetch_page` minus the browser: the ladder page is
parsed by `parse_public_html`, and every captured view step is folded in order by
the same `facebook_dom.fold_view` the live reader uses.
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.facebook import FacebookPage, FacebookPhoto  # noqa: E402
from app.services.facebook_dom import finish_views, fold_view, parse_public_html  # noqa: E402
from app.services.facebook_render import _VIEWS  # noqa: E402
from app.services.facebook_urls import parse_ref  # noqa: E402

CAPTURES = Path(__file__).resolve().parents[1] / ".facebook-captures"
_READERS = {view.tab: view.read for view in _VIEWS}


def _html(folder: Path, filename: str) -> str:
    return gzip.decompress((folder / filename).read_bytes()).decode("utf-8")


def replay(folder: Path) -> FacebookPage:
    """The FacebookPage the live reader would have produced from this capture."""
    manifest = json.loads((folder / "manifest.json").read_text())
    ref = parse_ref(manifest["source_url"])
    ladder = manifest["ladder"]
    page = parse_public_html(_html(folder, ladder["file"]), ref, final_url=ladder["url"])
    originals: dict[str, FacebookPhoto] = {}
    for step in manifest["steps"]:
        if not fold_view(page, originals, _READERS[step["view"]], _html(folder, step["file"]), step["url"]):
            break
    finish_views(page, originals)
    return page


def yield_of(page: FacebookPage) -> dict[str, int]:
    """The numbers a reader change is judged by."""
    pictures = page.post_photo_urls
    captioned = {
        photo.url for post in page.posts if (post.message or "").strip()
        for photo in post.pictures
    }
    photos = [photo for post in page.posts for photo in post.photo_sources]
    return {
        "posts": len(page.posts),
        "posts_with_text": len(page.posts_with_text),
        "posts_with_pictures": sum(1 for post in page.posts if post.pictures),
        "pictures": len(pictures),
        "pictures_with_caption": sum(1 for url in pictures if url in captioned),
        "photos_with_auto_alt": sum(1 for photo in photos if photo.auto_alt),
        "videos": sum(len(post.videos) for post in page.posts),
        "posts_dated": sum(1 for post in page.posts if post.created_time),
        "posts_with_id": sum(1 for post in page.posts if post.post_id),
        "reviews": len(page.reviews),
        "cover": int(bool(page.cover_photo_url)),
        "profile_picture": int(bool(page.profile_picture_url)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("names", nargs="*", help="capture folder names (default: all)")
    parser.add_argument("-v", "--verbose", action="store_true", help="list every post")
    args = parser.parse_args()
    folders = [CAPTURES / name for name in args.names] or sorted(
        path for path in CAPTURES.glob("*") if (path / "manifest.json").exists()
    )
    if not folders:
        raise SystemExit(f"No captures in {CAPTURES}. Run scripts/capture_facebook_page.py first.")
    for folder in folders:
        page = replay(folder)
        print(f"\n{folder.name} — {page.name} ({page.fetched_via}, missing={page.missing_fields})")
        for key, value in yield_of(page).items():
            print(f"  {key:22s} {value}")
        if args.verbose:
            for post in page.posts:
                text = (post.message or "").replace("\n", " ")[:90]
                print(
                    f"  - {len(post.photo_sources)} photo(s), {len(post.videos)} video(s) | "
                    f"{(post.created_time or '?')[:10]} | {text}"
                )


if __name__ == "__main__":
    main()
