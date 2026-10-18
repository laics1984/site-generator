"""List every piece of copy that will not read, in a downloaded site JSON.

    cd backend && .venv/bin/python scripts/audit_site_inks.py ~/Downloads/watr-site.json

Read-only. The JSON is what the preview's "Download site JSON" saves — the
generated tree exactly as the preview renders it. Each text node is judged
against the surface it actually sits on (`section_content._own_fill`, the
model every ink pass uses): under 4.5:1 (3:1 at 24px and up) is reported, and
so is a solid button under 3:1 against its band.

Two band checks ride along. DIVIDER: a shaped edge whose fill is not the colour
of the section it reveals (a wedge of one colour cut into a band of another).
CTA-BAND: a flat CTA band under 1.25:1 against the section before it, or —
when it closes the page — against the footer.

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
    # A fluid size (`clamp(38px, 4.74vw, 61px)`) is judged at its minimum.
    m = re.search(r"([\d.]+)px", str(value or ""))
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
    footer = _band_of(site.footer_schema, theme, resolve) if site.footer_schema else None
    for page in site.pages:
        for index, element in enumerate(page.body_schema.elements):
            before = len(lines)
            walk(element, page_bg, [f"/{page.slug} #{index}"])
            if len(lines) > before:
                lines.insert(before, f"--- page /{page.slug}, section {index} ({element.name})")
        lines.extend(_band_findings(page, theme, resolve, footer))
    if mismatches:
        lines.append("--- tokens the generator and the renderer resolve differently")
        lines.extend(sorted(set(mismatches)))
    return lines


def _flat_hex(value: object, theme: ThemeTokens, resolve) -> str | None:
    parsed = sc._parse_color(resolve(value), theme) if isinstance(value, str) else None
    return sc._to_hex(parsed[0]) if parsed and parsed[1] >= 0.999 else None


def _band_of(node: BuilderElement | None, theme: ThemeTokens, resolve) -> str | None:
    """The flat colour a section (or the footer) paints, first opaque fill down
    its wrapper chain; None for a photo or gradient band."""
    while node is not None:
        styles = node.styles or {}
        image = str(styles.get("backgroundImage") or "")
        if "url(" in image and "data:" not in image or "gradient(" in str(styles.get("background") or image):
            return None
        own = _flat_hex(styles.get("backgroundColor") or styles.get("background"), theme, resolve)
        if own:
            return own
        kids = [c for c in node.content if isinstance(c, BuilderElement)] if isinstance(node.content, list) else []
        node = kids[0] if len(kids) == 1 else None
    return None


def _band_findings(page, theme: ThemeTokens, resolve, footer: str | None) -> list[str]:
    from app.services.design_fingerprint import template_id_of

    sections = page.body_schema.elements
    found: list[str] = []
    for index, section in enumerate(sections):
        divider = section.divider
        for side, edge, other in (("top", divider and divider.top, index - 1),
                                  ("bottom", divider and divider.bottom, index + 1)):
            if not edge or not edge.color:
                continue
            fill = _flat_hex(edge.color, theme, resolve)
            neighbour = _band_of(sections[other], theme, resolve) if 0 <= other < len(sections) else None
            if fill != neighbour:
                found.append(f"DIVIDER /{page.slug} #{index} {side} edge {fill} reveals "
                             f"#{other} painted {neighbour} ({section.name})")
        if not template_id_of(section).startswith("cta"):
            continue
        band = _band_of(section, theme, resolve)
        if band is None:
            continue
        before = _band_of(sections[index - 1], theme, resolve) if index else None
        after = footer if index == len(sections) - 1 else None
        for label, other in (("section before", before), ("footer", after)):
            if other and _contrast(band, other) < 1.25:
                found.append(f"CTA-BAND /{page.slug} #{index} {band} is {_contrast(band, other):.2f}:1 "
                             f"against the {label} {other}")
    return found


def main(path: str) -> None:
    site = GeneratedSite.model_validate(json.loads(Path(path).read_text()))
    findings = audit(site)
    print("\n".join(findings) if findings else "No unreadable copy found.")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
