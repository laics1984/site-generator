"""
Design director — composes the DesignManifest before any page is built.

This is the "AI design brain → design manifest" seam described in
docs/DESIGN_ENGINE.md. It decides the site's chrome archetypes (header/footer
layout philosophy) from brand DNA, records *why* each choice was made and how
confident it is, and hands one manifest to plan_to_site so no builder makes an
independent styling decision.

Selection model (deliberate, never random):
  0. OVERRIDE — an explicit caller pin (e.g. a generation request that names
     the archetype) wins over everything below and bypasses the fit list, so a
     user can choose a chrome the fit table would never surface. Confidence 1.0.
  1. FIT — an ordered candidate list per industry (hard override) or mood.
     Only archetypes that suit the brand ever enter the list.
  2. SEEDED ROTATION — the brand name picks a stable index into the list, so
     one brand regenerates identically while different brands diverge.
  3. DIVERSITY — the diversity engine's recent-usage history bends the pick
     away from what this site (or the last few sites) just used, still within
     the fit list. See services/diversity.py; fail-open.

An LLM pass is intentionally NOT in this loop yet: chrome archetypes are a
small closed vocabulary where a fit table outperforms a 7-9B model's judgement,
and the existing design-brain passes (palette/fonts/section variants) already
cover the open-ended choices. The manifest is the place a future LLM pass
would write into.
"""

from __future__ import annotations

import logging

from app.models.brand import BrandMood
from app.models.design_manifest import (
    OVERLAY_CAPABLE_HEADERS,
    SELF_CHROME_HEADERS,
    DesignDecision,
    DesignManifest,
    FooterArchetype,
    HeaderArchetype,
)
from app.services.diversity import pick_diverse, recent_choices, seeded_index

logger = logging.getLogger(__name__)


# --- fit tables --------------------------------------------------------------
# Ordered best-fit-first. These express layout philosophy per brand mood:
# quiet hairline chrome for technical brands, centered editorial stacks for
# luxury, floating pills for playful ones. "classic" stays in every list — it
# is never wrong, merely never *interesting*, so it anchors the rotation.

_HEADER_FIT: dict[BrandMood, list[HeaderArchetype]] = {
    "modern": ["glass-blur", "classic", "floating-pill"],
    "technical": ["minimal-line", "classic", "glass-blur"],
    "luxury": ["centered-stack", "minimal-line", "classic"],
    "editorial": ["minimal-line", "centered-stack", "classic"],
    "playful": ["floating-pill", "classic", "glass-blur"],
    "friendly": ["classic", "glass-blur", "floating-pill"],
}

_FOOTER_FIT: dict[BrandMood, list[FooterArchetype]] = {
    "modern": ["mega", "cta-banner", "minimal-centered"],
    "technical": ["minimal-centered", "mega", "cta-banner"],
    "luxury": ["editorial", "minimal-centered", "mega"],
    "editorial": ["editorial", "minimal-centered", "mega"],
    "playful": ["cta-banner", "mega", "minimal-centered"],
    "friendly": ["mega", "cta-banner", "minimal-centered"],
}

# Industry pins override the mood table — these express a researched brief
# (childcare's bright, simple chrome; nonprofit's trust-first classic bar)
# rather than a taste rotation, hence the higher confidence they carry.
_HEADER_FIT_BY_INDUSTRY: dict[str, list[HeaderArchetype]] = {
    "childcare": ["classic", "floating-pill"],
    "nonprofit": ["classic", "glass-blur", "centered-stack"],
}
_FOOTER_FIT_BY_INDUSTRY: dict[str, list[FooterArchetype]] = {
    "childcare": ["mega", "cta-banner"],
    "nonprofit": ["cta-banner", "mega"],
}

_DEFAULT_MOOD: BrandMood = "modern"


def _fit_candidates(
    mood: BrandMood | None,
    industry: str | None,
    mood_table: dict[BrandMood, list],
    industry_table: dict[str, list],
) -> tuple[list, bool]:
    """(ordered candidates, industry_pinned) for one chrome area."""
    norm_industry = (industry or "").strip().lower()
    if norm_industry in industry_table:
        return list(industry_table[norm_industry]), True
    return list(mood_table.get(mood or _DEFAULT_MOOD, mood_table[_DEFAULT_MOOD])), False


async def compose_design_manifest(
    *,
    brand_name: str,
    mood: BrandMood | None,
    industry: str | None,
    color_scheme: str = "light",
    header_override: HeaderArchetype | None = None,
    footer_override: FooterArchetype | None = None,
) -> DesignManifest:
    """Compose the site's DesignManifest. Pure decision-making — no rendering.

    `header_override`/`footer_override` pin an archetype explicitly (a caller's
    stated intent): the pin wins over the fit list, seed and diversity, and is
    recorded as a confidence-1.0 decision. Passing an archetype the fit table
    would never surface for this brand is the whole point — that is how a user
    reaches, say, a floating-pill header on a nonprofit.

    Never raises: the diversity lookups fail open, and the fit tables always
    yield at least one candidate, so a manifest is always produced.
    """
    seed = brand_name.strip() or "site"
    decisions: list[DesignDecision] = []

    header_candidates, header_pinned = _fit_candidates(
        mood, industry, _HEADER_FIT, _HEADER_FIT_BY_INDUSTRY
    )
    footer_candidates, footer_pinned = _fit_candidates(
        mood, industry, _FOOTER_FIT, _FOOTER_FIT_BY_INDUSTRY
    )

    header_avoid = await recent_choices("header", site_key=seed)
    footer_avoid = await recent_choices("footer", site_key=seed)

    header = header_override or pick_diverse(
        header_candidates, seed=seed, salt="header", avoid=header_avoid
    )
    footer = footer_override or pick_diverse(
        footer_candidates, seed=seed, salt="footer", avoid=footer_avoid
    )

    def _decision(
        area: str,
        choice: str,
        candidates: list,
        pinned: bool,
        avoided: set[str],
        overridden: bool,
    ) -> DesignDecision:
        if overridden:
            rationale = (
                f"explicit generation override pinned '{choice}' — bypasses the "
                f"{'industry pin' if pinned else 'mood fit list'}"
            )
            return DesignDecision(
                area=area, choice=choice, rationale=rationale, confidence=1.0
            )
        seeded = candidates[seeded_index(seed, area, len(candidates))]
        if pinned:
            rationale = f"industry '{industry}' pins the {area} vocabulary; seeded rotation chose '{choice}'"
            confidence = 0.9
        elif choice != seeded and choice in candidates:
            rationale = (
                f"mood '{mood or _DEFAULT_MOOD}' fit list; diversity engine steered off "
                f"recently-used {sorted(avoided & set(candidates))} to '{choice}'"
            )
            confidence = 0.6
        else:
            rationale = f"mood '{mood or _DEFAULT_MOOD}' fit list, seeded rotation for brand '{seed}'"
            confidence = 0.75
        return DesignDecision(area=area, choice=choice, rationale=rationale, confidence=confidence)

    decisions.append(
        _decision(
            "header", header, header_candidates, header_pinned,
            header_avoid, header_override is not None,
        )
    )
    decisions.append(
        _decision(
            "footer", footer, footer_candidates, footer_pinned,
            footer_avoid, footer_override is not None,
        )
    )

    if header in SELF_CHROME_HEADERS:
        decisions.append(
            DesignDecision(
                area="header-overlay",
                choice="floating",
                rationale=(
                    f"'{header}' is overlay-native: it floats over full-bleed heroes "
                    "with its own chrome — no ink flip, no background reveal on scroll"
                ),
                confidence=1.0,
            )
        )
    elif header not in OVERLAY_CAPABLE_HEADERS:
        decisions.append(
            DesignDecision(
                area="header-overlay",
                choice="disabled",
                rationale=f"'{header}' keeps its own chrome at all times; transparent overlay would read broken",
                confidence=1.0,
            )
        )

    manifest = DesignManifest(
        seed=seed,
        mood=mood,
        industry=industry,
        color_scheme="dark" if color_scheme == "dark" else "light",
        header_archetype=header,
        footer_archetype=footer,
        decisions=decisions,
    )
    logger.info(
        "Design manifest: header=%s footer=%s (brand=%s mood=%s industry=%s)",
        header, footer, seed, mood, industry,
    )
    return manifest


# Decision areas that feed the diversity history. Chrome comes from the
# manifest's own fields; the rest are decision-log entries appended by
# plan_to_site (palette hex, homepage hero template). Interior heroes and
# per-section picks stay audit-only — their variety is already handled by
# rotation/LLM, and flooding the history would dilute the chrome signal.
_RECORDED_DECISION_AREAS = frozenset({"palette", "hero-homepage"})


async def record_manifest_choices(manifest: DesignManifest) -> None:
    """Feed the diversity history after a successful build. Fail-open."""
    from app.services.diversity import record_choice

    await record_choice("header", manifest.header_archetype, site_key=manifest.seed)
    await record_choice("footer", manifest.footer_archetype, site_key=manifest.seed)
    for decision in manifest.decisions:
        if decision.area in _RECORDED_DECISION_AREAS:
            await record_choice(decision.area, decision.choice, site_key=manifest.seed)
