"""List every piece of copy that will not read, in a downloaded site JSON.

    cd backend && .venv/bin/python scripts/audit_site_inks.py ~/Downloads/watr-site.json

Read-only. The JSON is what the preview's "Download site JSON" saves — the
generated tree exactly as the preview renders it. Each text node is judged
against the surface it actually sits on (`section_content._own_fill`, the
model every ink pass uses): under 4.5:1 (3:1 at 24px and up) is reported, and
so is a solid button under 3:1 against its band.

Colour tokens resolve the way the RENDERER resolves them: a colour-system v2
token (`var(--builder-color-on-surface-inverse, …)`) through `color_system.derive`
over the site's palette, the same derivation `preview/lib/colorSystem.ts`
mirrors — not through the fallback hex written beside it. Where the two
disagree, the line says so: the generator inked for one colour and the page
paints another.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.brand import ThemeTokens  # noqa: E402
from app.models.builder_schema import BuilderElement, BuilderElementContent, GeneratedSite  # noqa: E402
from app.services import section_content as sc  # noqa: E402
from app.services.theme import _contrast  # noqa: E402

_TOKEN_RE = re.compile(r"var\(--builder-color-([a-z0-9-]+)\s*,\s*(#[0-9a-fA-F]{6})\s*\)")


def _resolver(theme: ThemeTokens):
    derived: dict[str, str] = {}
    if theme.color_system_version == 2:
        from app.services.color_system import derive

        derived = derive(theme.palette.model_dump())
    mismatches: list[str] = []

    def resolve(value: object) -> object:
        if not isinstance(value, str) or not derived:
            return value

        def swap(m: re.Match) -> str:
            name, fallback = m.group(1), m.group(2)
            live = derived.get(name)
            if live and live.lower() != fallback.lower():
                mismatches.append(f"--builder-color-{name}: written {fallback}, renders {live}")
            return live or fallback

        return _TOKEN_RE.sub(swap, value)

    return resolve, mismatches


def _px(value: object) -> float:
    m = re.match(r"\s*([\d.]+)px", str(value or ""))
    return float(m.group(1)) if m else 16.0


def audit(site: GeneratedSite) -> list[str]:
    theme = site.theme if isinstance(site.theme, ThemeTokens) else ThemeTokens.model_validate(site.theme)
    resolve, mismatches = _resolver(theme)
    lines: list[str] = []

    def walk(node: BuilderElement, bg, trail: list[str], inherited: tuple[str, str] | None = None) -> None:
        styles = {k: resolve(v) for k, v in (node.styles or {}).items()}
        # `color` inherits: copy with none of its own wears its nearest
        # ancestor's — the case a node-by-node check never sees.
        if styles.get("color"):
            inherited = ((node.styles or {})["color"], f"{node.name}")
        elif inherited:
            styles["color"] = resolve(inherited[0])
        probe = node.model_copy(update={"styles": styles})
        band = bg
        fill = sc._own_fill(probe, theme, bg)
        if isinstance(fill, tuple):
            bg = fill
        elif fill is not sc._NO_FILL:
            bg = None  # a photo nobody measured: cannot be judged here
        raw_fill = (node.styles or {}).get("background") or (node.styles or {}).get("backgroundColor")
        here = trail + [f"{node.name} [{str(raw_fill)[:70]}]" if raw_fill else str(node.name)]
        if node.type in ("text", "link") and isinstance(node.content, BuilderElementContent) and bg is not None:
            raw = (node.styles or {}).get("color") or (
                f"{inherited[0]} (inherited from {inherited[1]})" if inherited else None
            )
            parsed = sc._parse_color(styles.get("color"), theme) if raw else None
            if parsed:
                ink = sc._to_hex(parsed[0])
                floor = 3.0 if _px(styles.get("fontSize")) >= 24 else 4.5
                ratio = _contrast(ink, sc._to_hex(bg))
                if ratio < floor:
                    text = (node.content.innerText or "")[:40]
                    lines.append(
                        f"TEXT {ratio:.2f}:1 (< {floor}) {text!r}\n"
                        f"     color: {raw}\n     on {sc._to_hex(bg)} via " + " > ".join(here)
                    )
            if node.type == "link" and band is not None:
                opaque = sc.opaque_surface_of(probe, theme)
                if opaque and _contrast(opaque, sc._to_hex(band)) < 3.0:
                    lines.append(
                        f"BUTTON fill {opaque} is {_contrast(opaque, sc._to_hex(band)):.2f}:1 on its "
                        f"band {sc._to_hex(band)} via " + " > ".join(here)
                    )
        if isinstance(node.content, list):
            for child in node.content:
                if isinstance(child, BuilderElement):
                    walk(child, bg, here, inherited)

    page_bg = tuple(int(theme.page.background[i : i + 2], 16) for i in (1, 3, 5))
    for page in site.pages:
        for index, element in enumerate(page.body_schema.elements):
            before = len(lines)
            walk(element, page_bg, [f"/{page.slug} #{index}"])
            if len(lines) > before:
                lines.insert(before, f"--- page /{page.slug}, section {index} ({element.name})")
    if mismatches:
        lines.append("--- tokens the generator and the renderer resolve differently")
        lines.extend(sorted(set(mismatches)))
    return lines


def main(path: str) -> None:
    site = GeneratedSite.model_validate(json.loads(Path(path).read_text()))
    findings = audit(site)
    print("\n".join(findings) if findings else "No unreadable copy found.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
