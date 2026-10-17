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

from typing import Any, Literal

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


class DirectionSpec(BaseModel):
    """The creative direction the site was built in (services/creative_directions.py),
    as the authoritative brief every later pass and reviewer reads: what the
    look is, what kind of business it was chosen for, and what it must not do.
    Derived from the direction's data at build time, so the record says what
    THIS site was built to, even after the library evolves."""

    slug: str
    label: str
    archetype: str | None = None
    brief: str = ""
    imagery: str = ""
    palette_voice: str = ""
    image_shape: str = "soft-rect"
    hero_leads: list[str] = Field(default_factory=list)
    section_emphasis: list[str] = Field(default_factory=list)
    cta_strategy: list[str] = Field(default_factory=list)
    anti_patterns: list[str] = Field(default_factory=list)


class QualityFinding(BaseModel):
    """One design-quality finding (services/design_quality.py)."""

    code: str
    severity: Literal["high", "medium", "low"]
    message: str
    page: str | None = None  # page slug ("" is the homepage)
    section: int | None = None  # index of the section on its page
    node: str | None = None  # element name, when one node is at fault
    corrected: bool = False  # a targeted correction fixed it during the build


class DesignQuality(BaseModel):
    """The evaluator's verdict on the finished site. Advisory: generation never
    fails on it (CLAUDE.md: the audit raises the generator, it doesn't block)."""

    score: int = 100  # 0..100
    subscores: dict[str, int] = Field(default_factory=dict)
    findings: list[QualityFinding] = Field(default_factory=list)
    corrections: int = 0  # findings a correction round fixed during the build


class DesignManifest(BaseModel):
    """A site's design-decision record. Composed once per generation by
    design_director.compose_design_manifest(), consumed by plan_to_site, and
    serialized onto GeneratedSite for audit/debugging.

    Versioned so downstream consumers (builder, CMS) can evolve their reading
    of it without guessing.
    """

    # v2: `direction` and `quality` (both optional, additive — the builder
    # carries the manifest as an opaque record).
    version: int = 2

    # --- brand DNA inputs the decisions were made from -----------------------
    seed: str = ""  # brand/site name; keys all seeded rotations (idempotent)
    mood: str | None = None
    industry: str | None = None
    color_scheme: Literal["light", "dark"] = "light"

    # --- chrome choices ------------------------------------------------------
    # The design scheme (services/design_schemes.py) the site was built under.
    # Empty when schemes are disabled — that is the legacy per-mood behaviour,
    # which has no name because it was never a choice.
    design_scheme: str = ""

    header_archetype: HeaderArchetype = "classic"
    footer_archetype: FooterArchetype = "mega"

    # The creative direction the scheme realises; None when the business's
    # industry has no authored directions (or they are switched off).
    direction: DirectionSpec | None = None

    # --- decision log --------------------------------------------------------
    decisions: list[DesignDecision] = Field(default_factory=list)

    # The design-quality evaluator's report on the finished site.
    quality: DesignQuality | None = None

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


# --- the copy that rides in BuilderStyles -------------------------------------
#
# Every renderer flattens BuilderStyles' nested scalars into CSS custom
# properties on the site root (webtree-public lib/styles.ts flattenStyleTokens,
# vendored to the preview) — the manifest included, as `--wt-design-manifest-*`.
# The CSSOM tolerates any value, but a server-rendered `style="…"` attribute is
# re-parsed by the browser, and one unbalanced quote in a value opens a CSS
# string that swallows EVERY declaration after it — the `--builder-color-*` and
# button tokens among them. A brand named "St. Mary's" (the manifest's `seed`)
# or a direction brief ("the studio's own work") was enough. So the BuilderStyles
# copy carries its text in CSS-inert characters; `GeneratedSite.design_manifest`
# keeps the exact record.
_CSS_UNSAFE = str.maketrans({"'": "’", '"': "”", ";": ",", "{": "(", "}": ")", "\\": "/", "<": "‹", ">": "›"})


def css_safe(value: Any) -> Any:
    """`value` with every string's CSS-significant characters replaced by
    look-alikes that tokenise as plain text."""
    if isinstance(value, str):
        return value.translate(_CSS_UNSAFE)
    if isinstance(value, dict):
        return {k: css_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [css_safe(v) for v in value]
    return value


def builder_styles_manifest(manifest: "DesignManifest | dict[str, Any]") -> dict[str, Any]:
    """The manifest as it rides in BuilderStyles (see above)."""
    record = manifest.model_dump() if isinstance(manifest, DesignManifest) else manifest
    return css_safe(record)
