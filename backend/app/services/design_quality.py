"""Design Quality Evaluator — does the finished site meet the bar it was designed to?

Runs once on the finished ``GeneratedSite`` (``plan_to_site``'s last step), and
on any dumped site JSON (``scripts/design_benchmark.py --score-dir``), because
it is pure over the BuilderElement tree. Three layers, all deterministic — no
model call is spent on judging (the screenshot review lives in the offline
benchmark, where its cost buys a baseline rather than a per-site toll):

1. **The existing audits, reused, not restated** — ``ux_audit``'s accessibility,
   legibility, responsive, rhythm, heading and generic-copy rules. Their SEO
   rules stay in the build log and out of the design score.
2. **Composition checks** — the same template twice on a page, one layout
   family dominating it, more than one gradient band, decoration piling up,
   copy too dense for its section, no action in the homepage hero or at its
   close, a type face the theme never chose.
3. **The manifest's own contract** — the creative direction lists what it must
   not look like (``CreativeDirection.anti_patterns``); a site that shows one
   anyway is a section that violates its own design brief.

Findings name their page, section index and node, so a correction can be
targeted. ``correct`` fixes the ones a safe, local change can fix — strip an
ambient backdrop, drop a shaped seam, soften a dramatic shadow, give a cropped
portrait its rectangle back — in ONE round, then the site is evaluated again.
Nothing loops and nothing regenerates: a finding no local change can fix is
reported for a human, and the build never fails on it (CLAUDE.md: the audit
raises the generator, it doesn't block it).
"""

from __future__ import annotations

import colorsys
import logging
import re
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from app.models.builder_schema import BuilderElement, BuilderElementContent, GeneratedSite
from app.models.design_manifest import DesignManifest, DesignQuality, QualityFinding
from app.services.style_tokens import IMAGE_SHAPES, SHAPED_IMAGE_NAMES

logger = logging.getLogger(__name__)

# How much one finding costs the 0..100 score. A (code, page) pair is charged
# once however many nodes repeat it, so one systemic slip cannot zero a site.
_SEVERITY_COST = {"high": 10, "medium": 5, "low": 2}

# Dimension each finding code belongs to (subscores). Codes not listed count
# toward "coherence".
_DIMENSION: dict[str, str] = {
    "alt-text": "accessibility",
    "aria-label": "accessibility",
    "image-dimensions": "accessibility",
    "color-contrast": "legibility",
    "readable-font-size": "legibility",
    "copy-dense": "legibility",
    "mobile-touch-target": "responsive",
    "mobile-fixed-width": "responsive",
    "heading-hierarchy": "hierarchy",
    "hero-without-action": "conversion",
    "no-closing-action": "conversion",
    "cta-missing": "conversion",
    "rhythm-monotone": "rhythm",
    "family-dominates": "rhythm",
    "repeated-template": "rhythm",
    "decoration-heavy": "restraint",
    "gradient-heavy": "restraint",
    "purple-blue-gradient": "originality",
    "generic-headline": "originality",
    "near-repeat": "originality",
    "font-outside-theme": "coherence",
    "shape-crops-portrait": "imagery",
    "portrait-in-landscape-slot": "imagery",
}

# ux_audit rules that are SEO, not design: logged, not scored.
_SEO_RULES = frozenset({
    "seo-title-length", "seo-desc-length", "seo-title-unique", "seo-desc-unique",
    "og-image-missing", "orphan-page",
})

# Composition thresholds.
_FAMILY_DOMINANCE = 3  # one layout family this many times on a page
_DECORATION_PER_SECTION = 0.6  # dividers + textures + ambient motion per body section
_DENSE_TEXT_CHARS = 900  # one text node in a body section
_DENSE_CARDS = 9  # repeated items in one grid
_DRAMATIC_BLUR_PX = 40
_NEAR_REPEAT = 0.25  # distinctiveness below this is a near-repeat
_AMBIENT_PRESETS = frozenset({"aurora", "silk"})
_GRADIENT_RE = re.compile(r"(linear|radial|conic)-gradient\(")
_HEX_IN_RE = re.compile(r"#[0-9a-fA-F]{6}\b")
_URL_RE = re.compile(r"url\(")
_SHADOW_BLUR_RE = re.compile(r"(?:-?\d+(?:\.\d+)?px\s+){2}(\d+(?:\.\d+)?)px")
_CARD_GROUP_HINT = re.compile(r"grid|cards?|list|row", re.IGNORECASE)


# --- tree helpers -----------------------------------------------------------------


def _children(el: BuilderElement) -> list[BuilderElement]:
    return [c for c in el.content if isinstance(c, BuilderElement)] if isinstance(el.content, list) else []


def _walk(el: BuilderElement) -> Iterator[BuilderElement]:
    yield el
    for child in _children(el):
        yield from _walk(child)


def _bg_values(el: BuilderElement) -> list[str]:
    styles = el.styles or {}
    return [v for k in ("background", "backgroundImage") if isinstance(v := styles.get(k), str)]


def _has_gradient_fill(section: BuilderElement) -> bool:
    """A gradient band with no photograph in it (a scrim over a photo is the
    photo's, not a gradient band)."""
    values = [v for node in _walk(section) for v in _bg_values(node)]
    if any(_URL_RE.search(v) for v in values):
        return False
    return any(_GRADIENT_RE.search(v) for v in values)


def _first_family(font: str | None) -> str | None:
    if not font or font.lstrip().startswith("var("):
        return None
    return font.split(",")[0].strip().strip("'\"").lower() or None


def _hue(hex_color: str) -> tuple[float, float, float]:
    raw = hex_color.lstrip("#")
    r, g, b = (int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4))
    return colorsys.rgb_to_hls(r, g, b)


def _is_blue_purple(hex_color: str) -> bool:
    h, light, sat = _hue(hex_color)
    return 0.6 <= h <= 0.83 and sat > 0.35 and 0.15 < light < 0.85


def _px(value: object) -> float | None:
    """A declared image dimension ("800", "800px", 800) as a number."""
    match = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(px)?\s*$", str(value)) if value is not None else None
    return float(match.group(1)) if match else None


def _hue_buckets(colors) -> set[int]:
    """Distinct 30° hue buckets among the CHROMATIC colours (neutrals, near
    whites and near blacks carry no hue to count)."""
    buckets: set[int] = set()
    for color in colors:
        if not isinstance(color, str) or not _HEX_IN_RE.fullmatch(color.strip()):
            continue
        h, light, sat = _hue(color.strip())
        if sat >= 0.25 and 0.08 < light < 0.96:
            buckets.add(int(h * 12) % 12)
    return buckets


# --- context ---------------------------------------------------------------------------


@dataclass
class _Context:
    site: GeneratedSite
    manifest: DesignManifest | None
    anti_patterns: frozenset[str]
    fonts: frozenset[str]
    primary: str | None


def _context(site: GeneratedSite) -> _Context:
    raw = site.design_manifest
    manifest = None
    if isinstance(raw, dict):
        try:
            manifest = DesignManifest.model_validate(raw)
        except Exception:  # noqa: BLE001 — an old or partial record still gets audited
            manifest = None
    elif isinstance(raw, DesignManifest):
        manifest = raw
    theme = site.theme
    typography = getattr(theme, "typography", None) if theme is not None else None
    if isinstance(theme, dict):
        typography = theme.get("typography")
    fonts: set[str] = set()
    for key in ("heading_font", "body_font", "mono_font", "accent_font"):
        value = typography.get(key) if isinstance(typography, dict) else getattr(typography, key, None)
        if (family := _first_family(value)) is not None:
            fonts.add(family)
    palette = theme.get("palette") if isinstance(theme, dict) else getattr(theme, "palette", None)
    primary = palette.get("primary") if isinstance(palette, dict) else getattr(palette, "primary", None)
    direction = manifest.direction if manifest else None
    return _Context(
        site=site,
        manifest=manifest,
        anti_patterns=frozenset(direction.anti_patterns) if direction else frozenset(),
        fonts=frozenset(fonts),
        primary=primary,
    )


def _finding(code: str, severity: str, message: str, page: str | None = None,
             section: int | None = None, node: str | None = None) -> QualityFinding:
    return QualityFinding(code=code, severity=severity, message=message, page=page, section=section, node=node)  # type: ignore[arg-type]


def _body(page) -> list[BuilderElement]:
    return list(page.body_schema.elements) if page.body_schema else []


def _is_hero(section: BuilderElement) -> bool:
    return (section.name or "").lower().startswith("hero")


# --- checks ------------------------------------------------------------------------


def _check_audits(ctx: _Context) -> Iterator[QualityFinding]:
    """The existing UX audits, folded in once per (rule, page)."""
    from app.services import ux_audit

    findings = ux_audit.audit_site(ctx.site) + ux_audit.audit_design(ctx.site) + ux_audit.audit_copy(ctx.site)
    grouped: Counter[tuple[str, str, str]] = Counter()
    detail: dict[tuple[str, str, str], str] = {}
    for f in findings:
        if f.rule in _SEO_RULES:
            continue
        key = (f.rule, f.page, f.severity)
        grouped[key] += 1
        detail.setdefault(key, f.detail)
    for (rule, page, severity), count in grouped.items():
        suffix = f" (+{count - 1} more)" if count > 1 else ""
        yield _finding(rule, severity, detail[(rule, page, severity)] + suffix, page=page)


def _check_composition(ctx: _Context) -> Iterator[QualityFinding]:
    from app.services.design_fingerprint import template_id_of
    from app.services.section_content import _layout_family

    for page in ctx.site.pages:
        slug = page.slug or "/"
        sections = _body(page)
        ids = [template_id_of(s) for s in sections]
        seen: dict[str, int] = {}
        for index, tid in enumerate(ids):
            if tid and not tid.startswith("hero-"):
                if tid in seen:
                    yield _finding(
                        "repeated-template", "medium",
                        f"'{tid}' is used twice on the page (sections {seen[tid]} and {index})",
                        page=slug, section=index, node=sections[index].name,
                    )
                else:
                    seen[tid] = index
        families = Counter(
            fam for tid in ids if tid and not tid.startswith("hero-")
            and (fam := _layout_family(tid)) is not None
        )
        for family, count in families.items():
            if count >= _FAMILY_DOMINANCE:
                yield _finding(
                    "family-dominates", "low",
                    f"{count} sections share the '{family}' composition", page=slug,
                )

        gradients = [i for i, s in enumerate(sections) if _has_gradient_fill(s)]
        budget = 1 if "gradient-heavy" in ctx.anti_patterns else 2
        if len(gradients) > budget:
            yield _finding(
                "gradient-heavy", "medium",
                f"{len(gradients)} gradient bands on one page (sections {gradients})",
                page=slug, section=gradients[-1],
            )

        body = [s for s in sections if not _is_hero(s)]
        decoration = sum(
            (1 if s.divider is not None else 0)
            + (1 if (s.backgroundTexture or "flat") != "flat" else 0)
            + (1 if s.motion is not None and s.motion.preset in _AMBIENT_PRESETS else 0)
            for s in sections
        )
        if body and decoration > max(2, round(len(body) * _DECORATION_PER_SECTION)):
            yield _finding(
                "decoration-heavy", "low",
                f"{decoration} decorative layers (seams, textures, ambient motion) on {len(sections)} sections",
                page=slug,
            )

        for index, section in enumerate(sections):
            for node in _walk(section):
                content = node.content
                if isinstance(content, BuilderElementContent) and node.type == "text":
                    text = content.innerText or ""
                    if len(text) > _DENSE_TEXT_CHARS and "profile" not in (section.name or "").lower():
                        yield _finding(
                            "copy-dense", "low",
                            f"{len(text)} characters in one text block", page=slug,
                            section=index, node=node.name,
                        )
                        break
                kids = _children(node)
                if len(kids) >= _DENSE_CARDS and _CARD_GROUP_HINT.search(node.name or ""):
                    yield _finding(
                        "copy-dense", "low", f"{len(kids)} items in one grid", page=slug,
                        section=index, node=node.name,
                    )
                    break


def _has_action(section: BuilderElement) -> bool:
    for node in _walk(section):
        content = node.content
        if node.type == "link" and isinstance(content, BuilderElementContent) and content.href:
            return True
    return False


def _check_conversion(ctx: _Context) -> Iterator[QualityFinding]:
    home = next((p for p in ctx.site.pages if p.is_homepage), None)
    if home is None:
        return
    sections = _body(home)
    if not sections:
        return
    if _is_hero(sections[0]) and not _has_action(sections[0]):
        yield _finding(
            "hero-without-action", "medium", "the homepage hero offers no action",
            page="/", section=0, node=sections[0].name,
        )
    if not any(_has_action(s) for s in sections[-2:]):
        yield _finding(
            "no-closing-action", "low", "the homepage ends without a next step", page="/",
            section=len(sections) - 1,
        )


def _check_fonts(ctx: _Context) -> Iterator[QualityFinding]:
    if not ctx.fonts:
        return
    for page in ctx.site.pages:
        stray: Counter[str] = Counter()
        for section in _body(page):
            for node in _walk(section):
                family = _first_family((node.styles or {}).get("fontFamily"))
                if family and family not in ctx.fonts and family not in ("system-ui", "inherit", "monospace", "sans-serif", "serif"):
                    stray[family] += 1
        for family, count in stray.items():
            yield _finding(
                "font-outside-theme", "medium",
                f"'{family}' set on {count} node(s) but not in the theme's type pairing",
                page=page.slug or "/",
            )


def _check_imagery(ctx: _Context) -> Iterator[QualityFinding]:
    organic = {v for k, v in IMAGE_SHAPES.items() if v and k in ("blob", "pebble")}
    for page in ctx.site.pages:
        for index, section in enumerate(_body(page)):
            for node in _walk(section):
                if node.type != "image" or not isinstance(node.content, BuilderElementContent):
                    continue
                width, height = _px(node.content.width), _px(node.content.height)
                if not (width and height):
                    continue
                portrait = height > width * 1.2
                radius = (node.styles or {}).get("borderRadius")
                if portrait and node.name in SHAPED_IMAGE_NAMES and radius in organic:
                    yield _finding(
                        "shape-crops-portrait", "medium",
                        "an organic mask on a portrait photograph crops its subject",
                        page=page.slug or "/", section=index, node=node.name,
                    )
                ratio = str((node.styles or {}).get("aspectRatio") or "")
                if portrait and re.match(r"^\s*(16|4|3|21)\s*/\s*(9|3|2)\s*$", ratio):
                    yield _finding(
                        "portrait-in-landscape-slot", "medium",
                        f"a portrait photograph in a {ratio} frame", page=page.slug or "/",
                        section=index, node=node.name,
                    )


def _section_title_colors(section: BuilderElement) -> str | None:
    for node in _walk(section):
        if node.htmlTag in ("h1", "h2") or (node.name or "") in ("Heading", "Title"):
            color = (node.styles or {}).get("color")
            if isinstance(color, str):
                match = _HEX_IN_RE.search(color)
                return match.group(0).lower() if match else color
    return None


def _check_direction(ctx: _Context) -> Iterator[QualityFinding]:
    """The manifest's own contract: what its creative direction must not show,
    plus the generic AI-template tells no brand asked for."""
    anti = ctx.anti_patterns
    brand_blue_purple = bool(ctx.primary and _HEX_IN_RE.fullmatch(ctx.primary) and _is_blue_purple(ctx.primary))
    for page in ctx.site.pages:
        slug = page.slug or "/"
        sections = _body(page)
        # Rainbow = several HUES. A title flipping between light and dark ink as
        # its band changes is one voice, legibly inked, not a rainbow.
        title_hues = _hue_buckets(
            c for s in sections if not _is_hero(s) and (c := _section_title_colors(s))
        )
        if "rainbow-titles" in anti and len(title_hues) >= 3:
            yield _finding("rainbow-titles", "medium", f"titles in {len(title_hues)} different hues on one page", page=slug)
        tint_hues = _hue_buckets(
            (s.styles or {}).get("backgroundColor") for s in sections if not _is_hero(s)
        )
        if "multi-tint-bands" in anti and len(tint_hues) >= 3:
            yield _finding("multi-tint-bands", "low", f"bands tinted in {len(tint_hues)} different hues on one page", page=slug)
        for index, section in enumerate(sections):
            if "wave-dividers" in anti and section.divider is not None:
                edges = [section.divider.top, section.divider.bottom]
                if any(e is not None and e.shape == "wave" for e in edges):
                    yield _finding("wave-dividers", "low", "a wave seam", page=slug, section=index, node=section.name)
            if "ambient-motion" in anti and section.motion is not None and section.motion.preset in _AMBIENT_PRESETS:
                yield _finding("ambient-motion", "low", f"an ambient '{section.motion.preset}' backdrop", page=slug, section=index, node=section.name)
            for node in _walk(section):
                styles = node.styles or {}
                if "glass-cards" in anti and styles.get("backdropFilter"):
                    yield _finding("glass-cards", "low", "frosted-glass panels", page=slug, section=index, node=node.name)
                    break
                shadow = styles.get("boxShadow")
                if "dramatic-shadows" in anti and isinstance(shadow, str):
                    blurs = [float(b) for b in _SHADOW_BLUR_RE.findall(shadow)]
                    if blurs and max(blurs) >= _DRAMATIC_BLUR_PX:
                        yield _finding("dramatic-shadows", "low", f"a {max(blurs):.0f}px shadow", page=slug, section=index, node=node.name)
                        break
            if not brand_blue_purple:
                for value in (v for node in _walk(section) for v in _bg_values(node)):
                    if not _GRADIENT_RE.search(value) or _URL_RE.search(value):
                        continue
                    stops = _HEX_IN_RE.findall(value)
                    if len(stops) >= 2 and all(_is_blue_purple(c) for c in stops) and len({round(_hue(c)[0], 1) for c in stops}) > 1:
                        yield _finding(
                            "purple-blue-gradient", "medium",
                            "a blue-to-purple gradient the brand's own colours don't call for",
                            page=slug, section=index, node=section.name,
                        )
                        break


def _check_originality(ctx: _Context) -> Iterator[QualityFinding]:
    decision = ctx.manifest.decision_for("distinctiveness") if ctx.manifest else None
    if decision is not None:
        try:
            value = float(decision.choice)
        except ValueError:
            return
        if value < _NEAR_REPEAT:
            yield _finding("near-repeat", "low", f"near-repeat of a recent site ({decision.rationale})")


_CHECKS: tuple[Callable[[_Context], Iterator[QualityFinding]], ...] = (
    _check_audits,
    _check_composition,
    _check_conversion,
    _check_fonts,
    _check_imagery,
    _check_direction,
    _check_originality,
)

# The anti-pattern codes this evaluator implements (creative_directions pins
# its vocabulary to this set).
IMPLEMENTED_ANTI_PATTERNS = frozenset({
    "rainbow-titles", "multi-tint-bands", "dramatic-shadows", "wave-dividers",
    "glass-cards", "purple-blue-gradient", "ambient-motion", "gradient-heavy",
    "repeated-card-grids",
})


def _score(findings: list[QualityFinding]) -> tuple[int, dict[str, int]]:
    charged: dict[tuple[str, str | None], QualityFinding] = {}
    for f in findings:
        if not f.corrected:
            charged.setdefault((f.code, f.page), f)
    total = 0
    by_dim: dict[str, int] = {}
    for f in charged.values():
        cost = _SEVERITY_COST.get(f.severity, 2)
        total += cost
        dim = _DIMENSION.get(f.code, "coherence")
        by_dim[dim] = by_dim.get(dim, 0) + cost
    subscores = {dim: max(0, 100 - cost * 2) for dim, cost in by_dim.items()}
    return max(0, 100 - total), subscores


def evaluate(site: GeneratedSite) -> DesignQuality:
    """The design-quality report for a finished site. Never raises: a check
    that fails is skipped, so an old or partial tree still gets a verdict."""
    ctx = _context(site)
    findings: list[QualityFinding] = []
    for check in _CHECKS:
        try:
            findings.extend(check(ctx))
        except Exception as exc:  # noqa: BLE001 — advisory
            logger.debug("design-quality check %s failed: %s", check.__name__, exc)
    score, subscores = _score(findings)
    return DesignQuality(score=score, subscores=subscores, findings=findings)


def evaluate_site_json(site_json: dict) -> DesignQuality:
    return evaluate(GeneratedSite.model_validate(site_json))


# --- targeted correction ------------------------------------------------------------

_SOFT_SHADOW = "0 8px 24px rgba(15, 23, 42, 0.08)"
_SOFT_RECT_RADIUS = "28px"


def _section_at(site: GeneratedSite, slug: str | None, index: int | None) -> BuilderElement | None:
    if index is None:
        return None
    page = next((p for p in site.pages if (p.slug or "/") == slug), None)
    sections = _body(page) if page is not None else []
    return sections[index] if 0 <= index < len(sections) else None


def _fix_ambient(section: BuilderElement, _f: QualityFinding) -> bool:
    if section.motion is not None and section.motion.preset in _AMBIENT_PRESETS:
        section.motion = None
        return True
    return False


def _fix_wave(section: BuilderElement, _f: QualityFinding) -> bool:
    if section.divider is None:
        return False
    section.divider = None
    return True


def _fix_shadow(section: BuilderElement, _f: QualityFinding) -> bool:
    changed = False
    for node in _walk(section):
        shadow = (node.styles or {}).get("boxShadow")
        if isinstance(shadow, str):
            blurs = [float(b) for b in _SHADOW_BLUR_RE.findall(shadow)]
            if blurs and max(blurs) >= _DRAMATIC_BLUR_PX:
                node.styles = {**node.styles, "boxShadow": _SOFT_SHADOW}
                changed = True
    return changed


def _fix_shape(section: BuilderElement, finding: QualityFinding) -> bool:
    changed = False
    for node in _walk(section):
        if node.type == "image" and node.name == finding.node:
            node.styles = {**(node.styles or {}), "borderRadius": _SOFT_RECT_RADIUS}
            changed = True
    return changed


# finding code → a local fix on the section the finding names.
_FIXES: dict[str, Callable[[BuilderElement, QualityFinding], bool]] = {
    "ambient-motion": _fix_ambient,
    "wave-dividers": _fix_wave,
    "dramatic-shadows": _fix_shadow,
    "shape-crops-portrait": _fix_shape,
}


def correct(site: GeneratedSite, report: DesignQuality) -> DesignQuality:
    """One round of targeted corrections, then the site evaluated again.

    Only findings with a local fix are touched; the rest stand for a human.
    Returns the post-correction report with the fixed findings kept and marked
    ``corrected`` (so the record says what was wrong and what was done), and the
    count of corrections. Never loops: the second evaluation is final."""
    fixed: list[QualityFinding] = []
    for finding in report.findings:
        fix = _FIXES.get(finding.code)
        section = _section_at(site, finding.page, finding.section) if fix else None
        if fix and section is not None and fix(section, finding):
            fixed.append(finding.model_copy(update={"corrected": True}))
    if not fixed:
        return report
    after = evaluate(site)
    return after.model_copy(
        update={"findings": [*fixed, *after.findings], "corrections": len(fixed)}
    )


def evaluate_and_correct(site: GeneratedSite) -> DesignQuality:
    """The build's entry point: evaluate, correct once, re-evaluate."""
    return correct(site, evaluate(site))


def summary(report: DesignQuality) -> dict:
    """A compact view for logs and the benchmark."""
    counts = Counter(f.code for f in report.findings if not f.corrected)
    return {
        "score": report.score,
        "subscores": report.subscores,
        "corrections": report.corrections,
        "findings": dict(sorted(counts.items())),
    }
