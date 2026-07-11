"""Curated homepage landing patterns.

Conversion-optimized homepage section sequences distilled from the ui-ux-pro-max
landing catalogue (MIT, github.com/nextlevelbuilder/ui-ux-pro-max-skill), expressed
in this project's ``SectionType`` vocabulary and tagged by industry.

The generator's homepage was previously a single fixed order
(hero → features → testimonials → cta) for every site. ``homepage_sections``
chooses an industry-appropriate sequence instead, falling back to that order when
nothing matches. Selection is deterministic; passing a ``seed`` (e.g. the brand
name) varies the pick among equally-fitting patterns.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import get_args

from app.models.content_blocks import SectionType

_VALID_SECTIONS = frozenset(get_args(SectionType))
_DEFAULT_SECTIONS: tuple[str, ...] = ("hero", "features", "testimonials", "cta")

# A homepage is ONE scaffold and is generated in ONE LLM call — page-multipass
# only chunks by source-text length, never by section count. Past ~10 sections
# the single call's output overflows the model's token budget and the backend
# returns an empty stream (a 502 for the whole generation). This ceiling keeps
# any pattern (plus its merged extras) within a size one call can produce; it
# sits above every real pattern (≤6) so it only ever catches a pathological one.
_MAX_HOMEPAGE_SECTIONS = 8


@dataclass(frozen=True)
class LandingPattern:
    """A homepage section sequence and the industries it suits.

    ``sections`` uses only ``SectionType`` values; ``homepage_sections`` still
    normalises the result (hero first, cta last) as a safety net.
    """

    name: str
    industries: tuple[str, ...]
    sections: tuple[str, ...]


# Ordered so the first pattern matching an industry is its most representative
# default (seed-less selection picks that one); later matches add variety once a
# seed is supplied.
_LANDING_PATTERNS: tuple[LandingPattern, ...] = (
    LandingPattern("menu-led", ("restaurant",),
                   ("hero", "menu", "gallery", "testimonials", "cta")),
    LandingPattern("pricing-led", ("saas",),
                   ("hero", "features", "pricing", "faq", "cta")),
    LandingPattern("services-process", ("professional-services", "consultancy", "agency"),
                   ("hero", "services", "process", "testimonials", "cta")),
    LandingPattern("mission-led", ("nonprofit",),
                   ("hero", "about", "testimonials", "team", "cta")),
    # Childcare homepage journey: emotional hero → why parents choose us
    # (features) → learning philosophy (about) → programs by age (services) →
    # parent testimonials → book-a-tour CTA. Parents, not children, are the
    # audience. Kept to 6 sections: the deeper story (a-day/process, teachers,
    # FAQ, gallery) lives on its own pages — a single homepage scaffold can't be
    # split across LLM calls, and packing ~10 sections into one generation call
    # exhausts the model's token budget (empty response → 502).
    LandingPattern("parent-trust", ("childcare",),
                   ("hero", "features", "about", "services", "testimonials", "cta")),
    LandingPattern("day-in-the-life", ("childcare",),
                   ("hero", "about", "services", "gallery", "testimonials", "cta")),
    LandingPattern("portfolio-grid", ("personal", "agency"),
                   ("hero", "gallery", "about", "cta")),
    LandingPattern("visual-gallery", ("ecommerce", "restaurant", "agency", "personal"),
                   ("hero", "gallery", "testimonials", "cta")),
    LandingPattern("trust-authority", ("consultancy", "nonprofit", "professional-services"),
                   ("hero", "about", "services", "testimonials", "cta")),
    LandingPattern("social-proof", ("professional-services", "consultancy", "personal"),
                   ("hero", "about", "testimonials", "cta")),
    LandingPattern("features-led", ("saas", "agency", "ecommerce", "other"),
                   ("hero", "features", "testimonials", "cta")),
    LandingPattern("minimal", ("other", "personal"),
                   ("hero", "features", "cta")),
)


def _seeded_index(seed: str | None, n: int) -> int:
    """Stable index in [0, n) from a seed (0 when no seed or single option)."""
    if not seed or n <= 1:
        return 0
    return int(hashlib.sha256(seed.encode("utf-8")).hexdigest(), 16) % n


def _normalize(sections: list[str]) -> list[str]:
    """Keep only known section types and force hero first / cta last — the landing
    catalogue's near-universal conversion structure.

    Also caps the total at ``_MAX_HOMEPAGE_SECTIONS`` (trimming from the end of
    the body, so hero and the closing cta always survive) — a hard guard against
    a pattern producing a homepage too large to generate in one LLM call."""
    seen: list[str] = []
    for s in sections:
        if s in _VALID_SECTIONS and s not in seen:
            seen.append(s)
    body = [s for s in seen if s not in ("hero", "cta")]
    body = body[: _MAX_HOMEPAGE_SECTIONS - 2]  # leave room for hero + cta
    return ["hero", *body, "cta"]


def homepage_sections(
    industry: str | None,
    *,
    extra_sections: list[str] | None = None,
    seed: str | None = None,
) -> list[str]:
    """Industry-fit homepage section order.

    Deterministic; ``seed`` varies the choice among equally-fitting patterns.
    Falls back to the standard features-led order when no pattern matches the
    industry. ``extra_sections`` are merged in just before the closing CTA.
    """
    norm = (industry or "").strip().lower()
    candidates = [p for p in _LANDING_PATTERNS if norm in p.industries]
    chosen = (
        candidates[_seeded_index(seed, len(candidates))].sections
        if candidates
        else _DEFAULT_SECTIONS
    )
    sections = list(chosen)
    if extra_sections:
        insert_at = len(sections) - 1 if sections and sections[-1] == "cta" else len(sections)
        for s in extra_sections:
            if s not in sections:
                sections.insert(insert_at, s)
                insert_at += 1
    return _normalize(sections)
