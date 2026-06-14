"""Static UX / accessibility audit over generated BuilderElement output.

A pre-delivery check grounded in the ui-ux-pro-max ``ux-guidelines.csv`` rules
(MIT) — the subset that can be verified from a BuilderElement tree + theme:

  - alt-text          (Accessibility, high) — images need a text alternative
  - aria-label        (Accessibility, high) — links/buttons need an accessible name
  - color-contrast    (Accessibility, high) — text vs background ≥ 4.5:1 (AA body)
  - readable-font-size (Responsive,   high) — body text not smaller than 12px
  - image-dimensions  (Layout/CLS,    high) — images declare width/height

Behavioural rules (smooth scroll, focus states, reduced motion, …) can't be
checked statically and are out of scope here. The audit is advisory: it returns
findings; it never mutates or blocks generation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.models.builder_schema import (
    BuilderElement,
    BuilderElementContent,
    GeneratedSite,
)
from app.services.theme import _contrast

_HEX_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_VAR_RE = re.compile(r"var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)")
# Body text below this many px trips readable-font-size.
_MIN_FONT_PX = 12.0

# rule id → (category, severity) from ux-guidelines.csv.
_RULES = {
    "alt-text": ("Accessibility", "high"),
    "aria-label": ("Accessibility", "high"),
    "color-contrast": ("Accessibility", "high"),
    "readable-font-size": ("Responsive", "high"),
    "image-dimensions": ("Layout", "high"),
}


@dataclass(frozen=True)
class Finding:
    rule: str
    category: str
    severity: str
    page: str
    detail: str


def _builder_vars(site: GeneratedSite) -> dict[str, str]:
    """Map --builder-color-* / --builder-font-* vars to concrete values from the
    pushed builder_styles, so var() colours can be resolved for contrast checks."""
    out: dict[str, str] = {}
    bs = site.builder_styles or {}
    colors = bs.get("colors") if isinstance(bs, dict) else None
    if isinstance(colors, dict):
        for k, v in colors.items():
            if isinstance(v, str):
                out[f"--builder-color-{_kebab(k)}"] = v
    return out


def _kebab(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "-", name).lower()


def _resolve_color(value: Any, varmap: dict[str, str]) -> str | None:
    """Concrete #hex for a CSS colour value, resolving var() against varmap and
    its fallback. Returns None for anything non-hex (named colours, gradients)."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    m = _VAR_RE.search(v)
    if m:
        name, fallback = m.group(1), (m.group(2) or "").strip()
        resolved = varmap.get(name)
        if resolved and _HEX_RE.match(resolved):
            return resolved.lower()
        return resolved if (resolved := _hex_or_none(fallback)) else None
    return _hex_or_none(v)


def _hex_or_none(v: str | None) -> str | None:
    if v and _HEX_RE.match(v.strip()):
        return v.strip().lower()
    return None


def _font_px(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    m = re.match(r"^([\d.]+)px$", value.strip())
    return float(m.group(1)) if m else None


def _styles(el: BuilderElement) -> dict[str, Any]:
    return el.styles if isinstance(el.styles, dict) else {}


def _bg_of(el: BuilderElement) -> str | None:
    s = _styles(el)
    return s.get("backgroundColor") or s.get("background")


def _walk(
    el: BuilderElement,
    inherited_bg: str | None,
    page: str,
    varmap: dict[str, str],
    out: list[Finding],
) -> None:
    cur_bg = _bg_of(el) or inherited_bg
    content = el.content

    if isinstance(content, BuilderElementContent):
        _check_leaf(el, content, cur_bg, page, varmap, out)
    elif isinstance(content, list):
        for child in content:
            if isinstance(child, BuilderElement):
                _walk(child, cur_bg, page, varmap, out)


def _add(out: list[Finding], rule: str, page: str, detail: str) -> None:
    cat, sev = _RULES[rule]
    out.append(Finding(rule=rule, category=cat, severity=sev, page=page, detail=detail))


def _check_leaf(
    el: BuilderElement,
    content: BuilderElementContent,
    cur_bg: str | None,
    page: str,
    varmap: dict[str, str],
    out: list[Finding],
) -> None:
    is_image = el.type == "image" or bool(content.src)
    if is_image and content.src:
        if not (content.alt and content.alt.strip()):
            _add(out, "alt-text", page, f"image '{el.name}' has no alt text")
        if not (content.width or content.height or _styles(el).get("aspectRatio")):
            _add(out, "image-dimensions", page,
                 f"image '{el.name}' declares no width/height (layout shift risk)")

    if el.type == "link":
        label = (content.innerText or content.ariaLabel or "").strip()
        if not label:
            _add(out, "aria-label", page, f"link '{el.name}' has no text or aria-label")

    text = (content.innerText or "").strip()
    if text:
        size = _font_px(_styles(el).get("fontSize"))
        if size is not None and size < _MIN_FONT_PX:
            _add(out, "readable-font-size", page,
                 f"text '{el.name}' is {size:g}px (< {_MIN_FONT_PX:g}px)")
        fg = _resolve_color(_styles(el).get("color"), varmap)
        bg = _resolve_color(cur_bg, varmap)
        if fg and bg and _contrast(fg, bg) < 4.5:
            ratio = _contrast(fg, bg)
            _add(out, "color-contrast", page,
                 f"text '{el.name}' {fg} on {bg} is {ratio:.1f}:1 (< 4.5:1)")


def audit_site(site: GeneratedSite) -> list[Finding]:
    """Audit every page (plus header/footer) of a generated site."""
    varmap = _builder_vars(site)
    out: list[Finding] = []
    for page in site.pages:
        roots = page.body_schema.elements if page.body_schema else []
        for el in roots:
            _walk(el, None, page.slug or "/", varmap, out)
    for chrome, label in ((site.header_schema, "<header>"), (site.footer_schema, "<footer>")):
        if isinstance(chrome, BuilderElement):
            _walk(chrome, None, label, varmap, out)
    return out


def summarize(findings: list[Finding]) -> dict[str, int]:
    """Counts by severity, for logging a one-line summary."""
    out = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out
