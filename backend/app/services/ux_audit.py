"""Static UX / accessibility audit over generated BuilderElement output.

A pre-delivery check grounded in the ui-ux-pro-max ``ux-guidelines.csv`` rules
(MIT) — the subset that can be verified from a BuilderElement tree + theme:

  - alt-text          (Accessibility, high) — images need a text alternative
  - aria-label        (Accessibility, high) — links/buttons need an accessible name
  - color-contrast    (Accessibility, high) — text vs background ≥ 4.5:1 (AA body)
  - readable-font-size (Responsive,   high) — body text not smaller than 12px
  - image-dimensions  (Layout/CLS,    high) — images declare width/height
  - mobile-touch-target (Responsive, high) — explicit button target ≥ 44px
  - mobile-fixed-width (Responsive, medium) — fixed width needs a mobile cap

Behavioural rules (smooth scroll, focus states, reduced motion, …) can't be
checked statically and are out of scope here. The audit is advisory: it returns
findings; it never mutates or blocks generation.
"""

from __future__ import annotations

import re
from types import SimpleNamespace
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from app.models.builder_schema import (
    BuilderElement,
    BuilderElementContent,
    GeneratedSite,
)
from app.services.section_content import _has_opaque_gradient, _has_real_photo
from app.services.style_tokens import heading_ink
from app.services.theme import _contrast, _neutral_inks

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
    "mobile-touch-target": ("Responsive", "high"),
    "mobile-fixed-width": ("Responsive", "medium"),
    "seo-title-length": ("SEO", "high"),
    "seo-desc-length": ("SEO", "high"),
    "seo-title-unique": ("SEO", "high"),
    "seo-desc-unique": ("SEO", "medium"),
    "heading-hierarchy": ("SEO", "high"),
    "cta-missing": ("SEO", "medium"),
    "og-image-missing": ("SEO", "medium"),
    "orphan-page": ("SEO", "medium"),
    "rhythm-monotone": ("Design", "low"),
    "generic-headline": ("Content", "low"),
}

# This many body sections of one visual weight in a row reads as a stack of
# equal boxes rather than a composed page.
_MONOTONE_RUN = 3


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
        # Derived from the palette by the renderers, never stored.
        inks = [colors.get(k) for k in ("secondary", "text", "background")]
        if all(isinstance(c, str) and _HEX_RE.match(c) for c in inks):
            out["--builder-color-heading"] = heading_ink(*inks)
        neutrals = {k: colors.get(k) for k in ("background", "surface", "text", "secondary")}
        if all(isinstance(c, str) and _HEX_RE.match(c) for c in neutrals.values()):
            light, dark = _neutral_inks(SimpleNamespace(**neutrals))
            out["--builder-color-ink-light"] = light
            out["--builder-color-ink-dark"] = dark
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


# A fill the audit cannot read off the styles — a photo nobody recorded, a
# gradient. Resolves to no colour, so text on it is not judged at all, rather
# than judged against the `backgroundColor` the photo paints over (which is how
# white copy on a bright photo used to pass as "white on navy").
_UNREADABLE_BG = "unreadable"


def _bg_of(el: BuilderElement) -> str | None:
    if el.painted_surface:  # measured by whoever painted it, in-process
        return el.painted_surface
    s = _styles(el)
    if _has_real_photo(s) or _has_opaque_gradient(s):
        return _UNREADABLE_BG
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
    responsive = el.responsiveStyles
    mobile_styles = {
        **_styles(el), **(responsive.mobile or {} if responsive else {}),
    }
    width = _font_px(mobile_styles.get("width"))
    maximum = mobile_styles.get("maxWidth")
    if width is not None and width > 320 and maximum != "100%":
        _add(out, "mobile-fixed-width", page,
             f"'{el.name}' has a {width:g}px fixed mobile width without a 100% cap")
    target = _font_px(mobile_styles.get("minHeight"))
    height = _font_px(mobile_styles.get("height"))
    if el.type == "link" and mobile_styles.get("display") in {"flex", "inline-flex"}:
        explicit = max(target or 0, height or 0)
        if explicit and explicit < 44:
            _add(out, "mobile-touch-target", page,
                 f"button '{el.name}' declares a {explicit:g}px mobile target (< 44px)")
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
        size = _font_px(mobile_styles.get("fontSize"))
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


def audit_seo(site: GeneratedSite) -> list[Finding]:
    """SEO-specific audit: title/desc lengths, uniqueness, heading hierarchy,
    CTA presence, og:image, and orphan pages.  Thresholds read from config."""
    from app.config import settings

    title_min = settings.seo_title_min_length
    title_max = settings.seo_title_max_length
    desc_min = settings.seo_description_min_length
    desc_max = settings.seo_description_max_length

    out: list[Finding] = []
    titles_seen: dict[str, str] = {}
    descs_seen: dict[str, str] = {}

    for page in site.pages:
        slug = page.slug or "/"
        seo = page.seo

        if seo and seo.title:
            tlen = len(seo.title)
            if tlen < title_min or tlen > title_max:
                _add(out, "seo-title-length", slug,
                     f"SEO title is {tlen} chars (target 50-60)")
            if seo.title in titles_seen:
                _add(out, "seo-title-unique", slug,
                     f"SEO title duplicates '{titles_seen[seo.title]}'")
            else:
                titles_seen[seo.title] = slug

        if seo and seo.description:
            dlen = len(seo.description)
            if dlen < desc_min or dlen > desc_max:
                _add(out, "seo-desc-length", slug,
                     f"SEO description is {dlen} chars (target 140-160)")
            if seo.description in descs_seen:
                _add(out, "seo-desc-unique", slug,
                     f"SEO description duplicates '{descs_seen[seo.description]}'")
            else:
                descs_seen[seo.description] = slug

        if seo and not seo.ogImage:
            _add(out, "og-image-missing", slug, "no og:image set")

        elements = page.body_schema.elements if page.body_schema else []
        _check_heading_hierarchy(elements, slug, out)
        _check_cta_present(elements, slug, out)

    try:
        from app.services.seo import detect_orphan_pages

        for orphan in detect_orphan_pages(site):
            _add(out, "orphan-page", orphan, "page not linked from any other page")
    except Exception:  # noqa: BLE001
        pass

    return out


def _descendants(elements: Iterable[BuilderElement]) -> Iterator[BuilderElement]:
    """Every element in these subtrees, in document order."""
    for el in elements:
        yield el
        content = el.content
        if isinstance(content, list):
            yield from _descendants(content)


def _check_heading_hierarchy(
    elements: list[BuilderElement], slug: str, out: list[Finding]
) -> None:
    """Exactly one <h1> per page.

    Counts `htmlTag`, the field the renderers actually read (a text node renders
    as its htmlTag, defaulting to <div>), whose only writers are
    `schema_builder.apply_heading_levels` and `legal_pages._h1`. It used to
    count elements NAMED "H1" — a convention nothing has ever emitted — so it
    reported "no H1 found" on every page of every site while being structurally
    incapable of seeing a real one, or of catching a page carrying two.
    """
    h1_count = sum(
        1 for el in _descendants(elements) if (el.htmlTag or "").lower() == "h1"
    )
    if h1_count == 0 and elements:
        _add(out, "heading-hierarchy", slug, "no H1 found")
    elif h1_count > 1:
        _add(out, "heading-hierarchy", slug, f"{h1_count} H1 elements (should be 1)")


def _check_cta_present(
    elements: list[BuilderElement], slug: str, out: list[Finding]
) -> None:
    if _has_cta(elements):
        return
    _add(out, "cta-missing", slug, "no CTA block or primary action link found")


def _has_cta(elements: list[BuilderElement]) -> bool:
    for el in _descendants(elements):
        name = (el.name or "").lower()
        if "cta" in name or name in ("contact form", "contact"):
            return True
    return False


def audit_design(site: GeneratedSite) -> list[Finding]:
    """Page rhythm: flags a run of `_MONOTONE_RUN`+ consecutive body sections of
    one visual weight (section_content.visual_weight). Advisory like every
    audit here — it explains a flat page, it never changes one."""
    from app.services.design_fingerprint import template_id_of
    from app.services.section_content import visual_weight

    out: list[Finding] = []
    for page in site.pages:
        weights = [
            visual_weight(tid)
            for section in (page.body_schema.elements if page.body_schema else [])
            if (tid := template_id_of(section)) and not tid.startswith("hero-")
        ]
        run = 1
        for previous, current in zip(weights, weights[1:]):
            run = run + 1 if current is not None and current == previous else 1
            if run == _MONOTONE_RUN:
                _add(out, "rhythm-monotone", page.slug or "/",
                     f"{_MONOTONE_RUN}+ consecutive {current}-weight sections")
    return out


_HEADING_TAGS = frozenset({"h1", "h2", "h3"})
_NON_WORD_RE = re.compile(r"[^a-z0-9' ]+")


def _plain(text: str) -> str:
    return " ".join(_NON_WORD_RE.sub(" ", text.lower().replace("’", "'")).split())


def _heading_text(el: BuilderElement) -> str:
    """A heading's words — its own text, or its lines' when it is a split
    headline group (apply_heading_levels tags the group, not each line)."""
    if isinstance(el.content, BuilderElementContent):
        return el.content.innerText or ""
    return " ".join(_heading_text(child) for child in el.content or [])


def audit_copy(site: GeneratedSite) -> list[Finding]:
    """Headings that are a stock phrase (prompts.GENERIC_HEADLINES) rather than
    something about this business. Advisory: a source that uses the phrase
    itself may keep it, and only a human can tell which this is."""
    from app.services.prompts import GENERIC_HEADLINES

    phrases = [_plain(p) for p in GENERIC_HEADLINES]
    out: list[Finding] = []
    for page in site.pages:
        for el in _descendants(page.body_schema.elements if page.body_schema else []):
            if (el.htmlTag or "").lower() not in _HEADING_TAGS:
                continue
            text = _plain(_heading_text(el))
            hit = next((p for p in phrases if p and p in text), None)
            if hit:
                _add(out, "generic-headline", page.slug or "/", f"'{text}' uses the stock phrase '{hit}'")
    return out


def summarize(findings: list[Finding]) -> dict[str, int]:
    """Counts by severity, for logging a one-line summary."""
    out = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        out[f.severity] = out.get(f.severity, 0) + 1
    return out
