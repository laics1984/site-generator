"""Say what a logo file is made of: its plate (if any) and the mark's inks.

    cd backend && .venv/bin/python scripts/inspect_logo.py https://watr.org.my/path/logo.png
    cd backend && .venv/bin/python scripts/inspect_logo.py ./logo.png

Read-only. "plate" is an opaque background the file paints around the mark
(a white JPEG background) — never judged as ink, so it never moves the header
or footer band. "inks" are the mark's own colours with their share of the mark;
those are what `header_footer` holds to 3:1 against the band.
"""

from __future__ import annotations

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.logo import extract_palette_from_image_bytes, read_mark  # noqa: E402


def main(source: str) -> None:
    if source.startswith(("http://", "https://")):
        resp = httpx.get(source, follow_redirects=True, timeout=20,
                         headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        data = resp.content
    else:
        data = Path(source).read_bytes()
    reading = read_mark(data)
    print(f"plate: {reading.backdrop or 'none (the mark sits on transparency)'}")
    print("inks (share of the mark):")
    for hex_color, share in reading.inks:
        print(f"  {hex_color}  {share:6.1%}")
    print(f"brand palette (whites/blacks dropped by design): "
          f"{extract_palette_from_image_bytes(data).palette}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
