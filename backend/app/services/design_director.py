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
from app.services.design_schemes import NEUTRAL_SCHEME, DesignScheme
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


def _apply_affinity(candidates: list, affinity: tuple) -> list:
    """Narrow `candidates` to the scheme's preferred archetypes.

    A NARROWING, not a reorder. Reordering would have been a no-op: the picker
    below takes a seeded index across the whole list, so moving an entry to the
    front only changes which brand lands on it, never how often it is chosen. A
    scheme that says "this language wears a hairline bar" has to actually shrink
    the pool or it has said nothing.

    Never an addition, though — the intersection with the fit list is what is
    kept, so an archetype the mood or industry rejected stays rejected. Same
    containment the variety seed has over template selection: steer taste inside
    the approved set, never past it.

    At least two candidates always survive, so the diversity engine keeps
    somewhere to go when a brand regenerates: a scheme naming only one reachable
    archetype gets it plus the fit list's own first choice.
    """
    if not affinity:
        return candidates
    preferred = [c for c in affinity if c in candidates]
    if not preferred:
        return candidates
    if len(preferred) >= 2:
        return preferred
    rest = [c for c in candidates if c not in preferred]
    return preferred + rest[:1]


def _fit_candidates(
    mood: BrandMood | None,
    industry: str | None,
    mood_table: dict[BrandMood, list],
    industry_table: dict[str, list],
    affinity: tuple = (),
) -> tuple[list, bool]:
    """(ordered candidates, industry_pinned) for one chrome area."""
    norm_industry = (industry or "").strip().lower()
    if norm_industry in industry_table:
        return _apply_affinity(list(industry_table[norm_industry]), affinity), True
    base = list(mood_table.get(mood or _DEFAULT_MOOD, mood_table[_DEFAULT_MOOD]))
    return _apply_affinity(base, affinity), False


async def compose_design_manifest(
    *,
    brand_name: str,
    mood: BrandMood | None,
    industry: str | None,
    color_scheme: str = "light",
    header_override: HeaderArchetype | None = None,
    footer_override: FooterArchetype | None = None,
    scheme: DesignScheme | None = None,
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

    scheme = scheme or NEUTRAL_SCHEME
    header_candidates, header_pinned = _fit_candidates(
        mood, industry, _HEADER_FIT, _HEADER_FIT_BY_INDUSTRY, scheme.header_affinity
    )
    footer_candidates, footer_pinned = _fit_candidates(
        mood, industry, _FOOTER_FIT, _FOOTER_FIT_BY_INDUSTRY, scheme.footer_affinity
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

    if not scheme.is_neutral:
        # Recorded first so the panel leads with the site's visual language:
        # header and footer are chosen INSIDE it, not alongside it.
        decisions.insert(
            0,
            DesignDecision(
                area="scheme",
                choice=scheme.slug,
                rationale=(
                    f"{scheme.label} — {scheme.rationale}; fit list for mood "
                    f"'{mood or _DEFAULT_MOOD}' / industry '{industry or 'any'}', "
                    f"seeded rotation for brand '{seed}'"
                ),
                confidence=0.7,
            ),
        )

    manifest = DesignManifest(
        seed=seed,
        mood=mood,
        industry=industry,
        color_scheme="dark" if color_scheme == "dark" else "light",
        design_scheme="" if scheme.is_neutral else scheme.slug,
        header_archetype=header,
        footer_archetype=footer,
        decisions=decisions,
    )
    logger.info(
        "Design manifest: scheme=%s header=%s footer=%s (brand=%s mood=%s industry=%s)",
        scheme.slug, header, footer, seed, mood, industry,
    )
    return manifest


def demote_self_chrome_header(manifest: DesignManifest, *, reason: str) -> bool:
    """Swap a self-chrome header (the floating pill) for the best non-self-chrome
    archetype in the same fit list. Returns True when the manifest changed.

    The pill is only honest over a photo hero: it floats with its own chrome and
    never solidifies, so on a page that opens with a flat band it reads as a
    stray widget sitting on the page background. plan_to_site therefore GIVES
    every page a photo hero (legal pages included) rather than dropping the
    pill — and calls this only when a page still couldn't resolve one, i.e. the
    invariant is genuinely unreachable for this site.

    An explicit caller pin is demoted too: a pinned archetype the site cannot
    render correctly is worse than the next-best fit, and the swap is recorded
    in the decision log with its reason.
    """
    if manifest.header_archetype not in SELF_CHROME_HEADERS:
        return False
    from app.services.design_schemes import by_slug

    candidates, _pinned = _fit_candidates(
        manifest.mood,  # type: ignore[arg-type]
        manifest.industry,
        _HEADER_FIT,
        _HEADER_FIT_BY_INDUSTRY,
        # Same affinity ordering the pill was chosen under, so the fallback is
        # the scheme's own next choice rather than the mood's — the site keeps
        # one coherent visual language even when its first pick is unreachable.
        by_slug(manifest.design_scheme).header_affinity,
    )
    # Best fit first, self-chrome removed. "classic" backstops a fit list that
    # somehow held nothing else — it is never wrong, merely never interesting.
    fallback: HeaderArchetype = next(
        (c for c in candidates if c not in SELF_CHROME_HEADERS), "classic"
    )
    previous = manifest.header_archetype
    manifest.header_archetype = fallback
    # Replace rather than append: decision_for() returns the FIRST match per
    # area, so a stale "header"/"header-overlay" entry would out-rank the swap.
    manifest.decisions = [
        d for d in manifest.decisions if d.area not in ("header", "header-overlay")
    ]
    manifest.decisions.insert(
        0,
        DesignDecision(
            area="header",
            choice=fallback,
            rationale=(
                f"'{previous}' demoted — {reason}; next non-self-chrome archetype "
                "in the fit list"
            ),
            confidence=0.9,
        ),
    )
    logger.info("Header archetype demoted: %s -> %s (%s)", previous, fallback, reason)
    return True


# Decision areas that feed the diversity history. Chrome comes from the
# manifest's own fields; the rest are decision-log entries appended by
# compose_design_manifest (scheme) and plan_to_site (palette hex, homepage hero
# template). Interior heroes and per-section picks stay audit-only — their
# variety is already handled by rotation/LLM, and flooding the history would
# dilute the chrome signal. The scheme earns a slot because it is the widest
# single choice on the site: two consecutive sites sharing one is exactly the
# convergence this history exists to break.
_RECORDED_DECISION_AREAS = frozenset({"palette", "hero-homepage", "scheme"})


async def record_manifest_choices(manifest: DesignManifest) -> None:
    """Feed the diversity history after a successful build. Fail-open."""
    from app.services.diversity import record_choice

    await record_choice("header", manifest.header_archetype, site_key=manifest.seed)
    await record_choice("footer", manifest.footer_archetype, site_key=manifest.seed)
    for decision in manifest.decisions:
        if decision.area in _RECORDED_DECISION_AREAS:
            await record_choice(decision.area, decision.choice, site_key=manifest.seed)
