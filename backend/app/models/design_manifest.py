"""
Design Manifest — the recorded source of truth for a site's design decisions.

The generation pipeline already makes real design decisions (curated palette,
font pairing, per-page hero directives, per-section template picks, luminance
rhythm), but they were made in scattered modules and never recorded anywhere a
human or a later pass could inspect. The manifest fixes that:

  Business data (brand / industry / mood / seed)
        │  design_director.compose_design_manifest()
        ▼
  DesignManifest      chrome archetypes + rationale + confidence per decision
        │  plan_to_site() consumes it (header/footer archetype, overlay policy)
        ▼
  GeneratedSite.design_manifest    serialized alongside the site for audit

Two rules keep this honest:
  * every archetype choice carries a DesignDecision (what, why, how sure);
  * the manifest never stores derived pixels — colours, fonts and spacing stay
    in ThemeTokens; the manifest stores *choices* and their reasons.

The chrome archetype vocabularies below are the contract between the director
(services/design_director.py) and the builders (services/header_footer.py).
Adding an archetype means: add the Literal here, a builder branch there, and a
fit entry in the director — nothing else.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# --- chrome archetype vocabularies ------------------------------------------

# Navigation archetypes. Each is a different layout philosophy, not a recolour:
#   classic        logo left · inline nav · solid CTA; solid chrome (legacy look)
#   glass-blur     classic bar on translucent frosted chrome (backdrop blur)
#   floating-pill  inset rounded bar floating over the page, page shows around it
#   centered-stack logo centered over a centered nav row (editorial/luxury)
#   minimal-line   logo + nav on a hairline-ruled bar, ghost CTA (quiet/technical)
HeaderArchetype = Literal[
    "classic",
    "glass-blur",
    "floating-pill",
    "centered-stack",
    "minimal-line",
]

# Footer archetypes:
#   mega             brand column + grouped nav columns + legal bar, dark (legacy)
#   minimal-centered centered brand + inline nav + legal, calm single column
#   cta-banner       conversion banner (headline + CTA) above the mega grid
#   editorial        oversized wordmark on a light band, slim nav/legal row
FooterArchetype = Literal[
    "mega",
    "minimal-centered",
    "cta-banner",
    "editorial",
]

# Archetypes that can float over a full-bleed hero (behavior.overlay). Two
# overlay styles exist:
#   * reveal style (classic, glass-blur, centered-stack, minimal-line) — the
#     root goes transparent with white ink over the hero's dark scrim, then
#     reveals its solid chrome past the scroll offset;
#   * self-chrome style (floating-pill) — the bar carries its own chrome at
#     all times, so it floats over the hero as-is: no ink flip (its nodes
#     carry no wt-header-ink markers) and no background reveal on scroll
#     (behavior.revealBackgroundOnScroll: false; root stays transparent).
OVERLAY_CAPABLE_HEADERS: frozenset[str] = frozenset(
    {"classic", "glass-blur", "floating-pill", "centered-stack", "minimal-line"}
)

# The self-chrome subset of the above. Members overlay without the transparent
# phase: renderers must not flip their ink or reveal a background on scroll.
SELF_CHROME_HEADERS: frozenset[str] = frozenset({"floating-pill"})


class DesignDecision(BaseModel):
    """One recorded design decision: what was chosen, why, and how confident
    the director is. Confidence is the director's own calibration — 1.0 for a
    hard industry rule, lower for seeded-rotation picks — so a future review
    pass (or the builder UI) knows which decisions are safe to revisit."""

    area: str  # "header" | "footer" | "palette" | "typography" | ...
    choice: str
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)


class DesignManifest(BaseModel):
    """A site's design-decision record. Composed once per generation by
    design_director.compose_design_manifest(), consumed by plan_to_site, and
    serialized onto GeneratedSite for audit/debugging.

    Versioned so downstream consumers (builder, CMS) can evolve their reading
    of it without guessing.
    """

    version: int = 1

    # --- brand DNA inputs the decisions were made from -----------------------
    seed: str = ""  # brand/site name; keys all seeded rotations (idempotent)
    mood: str | None = None
    industry: str | None = None
    color_scheme: Literal["light", "dark"] = "light"

    # --- chrome choices ------------------------------------------------------
    header_archetype: HeaderArchetype = "classic"
    footer_archetype: FooterArchetype = "mega"

    # --- decision log --------------------------------------------------------
    decisions: list[DesignDecision] = Field(default_factory=list)

    def decision_for(self, area: str) -> DesignDecision | None:
        for d in self.decisions:
            if d.area == area:
                return d
        return None

    @property
    def header_overlay_capable(self) -> bool:
        return self.header_archetype in OVERLAY_CAPABLE_HEADERS

    @property
    def header_overlay_reveals(self) -> bool:
        """Whether the overlay reveals its background past the scroll offset.

        False for self-chrome archetypes (floating pill): the bar keeps its
        own chrome permanently, so the layout payload emits
        ``revealBackgroundOnScroll: false`` and renderers never solidify it.
        """
        return self.header_archetype not in SELF_CHROME_HEADERS
