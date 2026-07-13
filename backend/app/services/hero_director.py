"""
Per-page hero art direction — deterministic, no LLM.

Every page's FIRST impression is its hero, and template selection used to
converge on one variant per mood (the design-brain pass was even instructed to
keep "a consistent hero treatment" across pages), so all pages of a nonprofit
site rendered the identical split-column hero. This module replaces the LLM's
hero pick with a deterministic per-page directive:

  * the homepage leads with the mood's signature treatment (immersive
    full-bleed for photo-forward moods, washed split for SaaS-ish moods);
  * interior pages draw from a small mood-approved rotation, seeded by the
    brand + slug so regeneration is idempotent but pages differ from each
    other;
  * when the SOURCE site led with a CSS background image, the homepage is
    forced full-bleed and pins that image (see
    ImageResolver.strongest_source_background) — a background stays a
    background.

Coherence comes from the closed per-mood variant set plus the shared theme;
variety comes from rotating within it. Feasibility is still enforced by
select_template (an image-led directive on an imageless page silently degrades
to the content-preference order), so a directive is a strong lean, not a hard
override.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from app.config import settings
from app.models.content_blocks import BrandMood, PagePlan

# Hero templates that render no image slot (see app/templates/section_catalog.json).
# For these the photo policy skips image resolution entirely so a scraped photo
# isn't burned on a slot that never shows it.
IMAGELESS_HERO_IDS = frozenset(
    {"hero-gradient", "hero-centered-minimal", "hero-minimal", "hero-asymmetric-display"}
)


@dataclass(frozen=True)
class HeroDirective:
    """How one page's hero should be art-directed."""

    template_id: str  # catalog id; select_template feasibility still gates
    layout: Literal["split", "background"]  # HeroBlock normalisation / visual policy
    wants_wash: bool = False  # split heroes: paint an abstract washed background
    pin_source_background: bool = False  # prefer the source's own CSS background


# Shorthand treatments. A split-column hero always carries the abstract wash
# (requirement: a split first-hero never sits on a flat page background).
_BACKGROUND = HeroDirective("hero-background-bold", "background")
_SPLIT_WASHED = HeroDirective("hero-modern-split", "split", wants_wash=True)
_EDITORIAL = HeroDirective("hero-editorial", "split")
_BENTO = HeroDirective("hero-bento", "split")
_GRADIENT = HeroDirective("hero-gradient", "split")
_CENTERED = HeroDirective("hero-centered-minimal", "split")
_MINIMAL = HeroDirective("hero-minimal", "split")
_ASYMMETRIC = HeroDirective("hero-asymmetric-display", "split")
# Blob-masked photo beside sticker-chip copy; light band, dark ink. Only moods
# whose catalog gate allows it (see the template's `moods` field) rotate it in.
_PLAYFUL_SPLIT = HeroDirective("hero-playful-split", "split")


@dataclass(frozen=True)
class _MoodSpec:
    """One mood's hero language: homepage lead + page-type leans + rotation."""

    homepage: HeroDirective
    by_page_type: dict[str, HeroDirective]
    rotation: tuple[HeroDirective, ...]


# Nonprofit is the fully art-directed set (2026 lean: emotional immersive
# homepage, editorial storytelling, oversized-type minimal for transactional
# pages). Other industries fall back to their mood's spec below.
_NONPROFIT_SPEC = _MoodSpec(
    homepage=_BACKGROUND,
    by_page_type={
        "about": _EDITORIAL,
        "services": _SPLIT_WASHED,
        "contact": _CENTERED,
        "team": _EDITORIAL,
        "faq": _CENTERED,
    },
    rotation=(_EDITORIAL, _SPLIT_WASHED, _GRADIENT),
)

# Per-mood specs. Homepage keeps each mood's CURRENT lead treatment (see
# _apply_hero_photo_policy: modern/technical read split as on-brand, everything
# else leads photo-forward full-bleed); interiors rotate 2–3 compact variants
# so pages stop being clones. No interior rotation contains the full-bleed
# hero — interiors stay compact so content isn't pushed below the fold
# (see apply_hero_cta_policy).
_MOOD_SPECS: dict[BrandMood, _MoodSpec] = {
    "modern": _MoodSpec(
        homepage=_SPLIT_WASHED,
        by_page_type={"contact": _CENTERED},
        rotation=(_SPLIT_WASHED, _BENTO, _GRADIENT),
    ),
    "technical": _MoodSpec(
        homepage=_SPLIT_WASHED,
        by_page_type={"contact": _CENTERED},
        rotation=(_SPLIT_WASHED, _MINIMAL, _CENTERED),
    ),
    "luxury": _MoodSpec(
        homepage=_BACKGROUND,
        by_page_type={"contact": _CENTERED},
        rotation=(_EDITORIAL, _CENTERED, _MINIMAL),
    ),
    "editorial": _MoodSpec(
        homepage=_BACKGROUND,
        by_page_type={"contact": _MINIMAL},
        rotation=(_EDITORIAL, _ASYMMETRIC, _MINIMAL),
    ),
    "playful": _MoodSpec(
        homepage=_BACKGROUND,
        by_page_type={"contact": _CENTERED},
        rotation=(_PLAYFUL_SPLIT, _BENTO, _GRADIENT),
    ),
    "friendly": _MoodSpec(
        homepage=_BACKGROUND,
        by_page_type={"contact": _CENTERED},
        rotation=(_SPLIT_WASHED, _EDITORIAL, _GRADIENT),
    ),
}

# Childcare art direction: the homepage leads with large authentic photography
# (children learning/playing — emotional connection for parents), interiors stay
# soft and compact and — critically — LIGHT-BACKGROUND WITH DARK TEXT to match
# the bright Scandinavian brief. The gradient hero (hardcoded white text over a
# secondary→primary gradient) is deliberately excluded: with childcare's pastel
# primary the gradient's light end left white text illegible, so interiors use
# washed splits, centered-minimal, and editorial — all dark ink on a light band.
_CHILDCARE_SPEC = _MoodSpec(
    homepage=_BACKGROUND,
    by_page_type={
        "about": _EDITORIAL,
        "team": _EDITORIAL,
        "contact": _CENTERED,
        "faq": _CENTERED,
    },
    rotation=(_PLAYFUL_SPLIT, _SPLIT_WASHED, _CENTERED, _EDITORIAL),
)

_INDUSTRY_SPECS: dict[str, _MoodSpec] = {
    "nonprofit": _NONPROFIT_SPEC,
    "childcare": _CHILDCARE_SPEC,
}

_DEFAULT_SPEC = _MOOD_SPECS["friendly"]


def _rotation_index(seed: str, slug: str, size: int) -> int:
    """Stable per-page index into a rotation. md5 (not hash()) so the pick
    survives interpreter restarts — regeneration must be idempotent."""
    digest = hashlib.md5(f"{seed}:{slug}".encode()).hexdigest()
    return int(digest[:8], 16) % size


def plan_site_heroes(
    pages: list[PagePlan],
    *,
    mood: BrandMood | None,
    industry: str | None,
    has_source_background: bool,
    seed: str,
) -> dict[str, HeroDirective]:
    """Assign every page a HeroDirective, keyed by slug.

    ``has_source_background``: the source site led with a CSS background image
    — the homepage is forced full-bleed and pins it, whatever the spec says.
    ``seed`` (brand/site name) keeps the interior rotation stable per site.
    """
    spec = _INDUSTRY_SPECS.get((industry or "").strip().lower())
    if spec is None:
        spec = _MOOD_SPECS.get(mood) if mood else None  # type: ignore[arg-type]
    if spec is None:
        spec = _DEFAULT_SPEC

    # Site-wide full-bleed policy: EVERY page opens with a background hero —
    # a real/stock photo or the colour-matched abstract — so the transparent
    # floating header engages on every page, not just the homepage. Imagery
    # fallbacks (and the compact-hero degrade when nothing genuine resolves)
    # are handled downstream by _apply_hero_directive; that degrade also keeps
    # the header solid on such a page, so readability never regresses.
    if settings.hero_fullbleed_all_pages:
        return {
            page.slug: (
                HeroDirective(
                    "hero-background-bold", "background", pin_source_background=True
                )
                if (page.is_homepage or page.page_type == "home") and has_source_background
                else _BACKGROUND
            )
            for page in pages
        }

    directives: dict[str, HeroDirective] = {}
    interior_seen: list[str] = []
    for page in pages:
        if page.is_homepage or page.page_type == "home":
            directive = spec.homepage
            if has_source_background:
                directive = HeroDirective(
                    "hero-background-bold", "background", pin_source_background=True
                )
            directives[page.slug] = directive
            continue

        directive = spec.by_page_type.get(page.page_type)
        if directive is None:
            start = _rotation_index(seed, page.slug, len(spec.rotation))
            # Nudge consecutive rotation picks apart: if the seeded pick equals
            # the previous rotation page's template, step to the next variant.
            directive = spec.rotation[start]
            if interior_seen and interior_seen[-1] == directive.template_id:
                directive = spec.rotation[(start + 1) % len(spec.rotation)]
            interior_seen.append(directive.template_id)
        directives[page.slug] = directive
    return directives
