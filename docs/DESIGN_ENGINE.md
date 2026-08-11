# Design Engine — Manifest, Chrome Archetypes, Diversity

**Status:** implemented (phase 1). This document is the ADR + map for the
design-engine layer added between AI generation and rendering.

## Problem

Generated sites converged on one look: every site got the identical header
(logo · inline nav · CTA on a solid bar) and the identical dark mega footer,
whatever the brand. Section/hero variety already existed (design brain, hero
director, luminance rhythm), but the *chrome* — the first and last thing a
visitor sees on every page — was a single hardcoded layout, so sites read as
"different colours on the same template". Design decisions were also scattered
across modules with no recorded rationale, making them impossible to audit or
evolve.

## Decision

Introduce a thin **design-engine layer** with three parts, all inside the
existing pipeline (no new service, no renderer changes):

```
Business data (brand · industry · mood · seed)
      │  services/design_director.compose_design_manifest()
      │      fit tables → seeded rotation → diversity avoidance
      ▼
DesignManifest (models/design_manifest.py)          ← single source of truth
      │  plan_to_site() consumes it
      ├── build_header(archetype=…)   5 navigation archetypes
      ├── build_footer(archetype=…)   4 footer archetypes
      └── overlay policy              (OVERLAY_CAPABLE_HEADERS gate)
      ▼
GeneratedSite.design_manifest        serialized decision log (audit artifact)
      │  after successful build
      ▼
services/diversity.py                SQLite usage history feeds the NEXT site
```

### 1. Design Manifest (`models/design_manifest.py`)

A versioned Pydantic model recording every chrome choice plus a
`DesignDecision` (area, choice, rationale, confidence) per decision. Rules:

* the manifest stores **choices and reasons**, never derived pixels — colours,
  fonts, spacing stay in `ThemeTokens`;
* every archetype choice carries a decision entry, so a later pass (or the
  builder UI) can see *why* a site looks the way it does and which decisions
  are low-confidence enough to revisit;
* it is serialized onto `GeneratedSite.design_manifest`; renderers derive
  nothing from it (the chrome itself flows through the normal
  header/footer BuilderElement schemas).

### 2. Chrome archetypes (`services/header_footer.py`)

Header archetypes — different **layout philosophies**, not recolours:

| Archetype        | Philosophy                                                    | Overlay style |
|------------------|---------------------------------------------------------------|---------------|
| `classic`        | logo · inline nav · solid CTA on solid chrome (legacy)        | reveal |
| `glass-blur`     | classic bar on translucent frosted chrome (backdrop blur)     | reveal |
| `floating-pill`  | inset rounded bar floating over the page                      | **self-chrome** |
| `centered-stack` | brand mark centered over a slim nav row (editorial/luxury)    | reveal |
| `minimal-line`   | hairline-ruled bar, ghost CTA, extra air (technical/quiet)    | reveal |

Two overlay styles: **reveal** headers run the classic transparent phase over a
full-bleed hero (root stripped, `wt-header-ink` nodes forced white, solid
chrome revealed past the scroll offset). The **self-chrome** pill is
overlay-native: it floats over the hero with its own bar chrome from scroll 0
— its nodes carry no ink markers and the layout payload emits
`revealBackgroundOnScroll: false`, so renderers never solidify it
(`SELF_CHROME_HEADERS` in `models/design_manifest.py`).

Footer archetypes:

| Archetype          | Philosophy                                                   |
|--------------------|--------------------------------------------------------------|
| `mega`             | dark brand column + grouped nav columns + legal bar (legacy) |
| `cta-banner`       | conversion banner (headline + primary CTA) above the grid    |
| `minimal-centered` | calm centered column on the theme's light band               |
| `editorial`        | oversized ghost wordmark, slim nav row, light band           |

Compatibility invariants (verified against both renderers):

* `classic` / `mega` defaults are **byte-identical** to pre-engine output —
  every call site that doesn't pass an archetype gets the legacy chrome.
* All archetypes are plain flex `BuilderElement` trees (the builder does not
  honour grid on plain containers) using the existing shared-menu elements,
  so the builder editor and webtree-public render them with no changes.
* Overlay: webtree-public's `ContainerBlock` already neutralises
  `backgroundColor/backdropFilter/borderBottomColor/boxShadow` on the header
  root during the transparent phase, so `glass-blur` and `minimal-line`
  overlay cleanly. `floating-pill` overlays WITHOUT the transparent phase:
  its root is genuinely transparent and its bar chromes itself, so the
  director records a `header-overlay: floating` decision, the layout payload
  says `revealBackgroundOnScroll: false` (a behavior flag both renderers
  already honour), and no pill node carries `wt-header-ink` — nothing flips
  white, nothing solidifies on scroll.
* Light-band footers compute their ink via `_text_for_background`, and the
  existing `enforce_text_contrast` pass runs over the footer as before.

### 3. Selection: fit → seed → diversity (`services/design_director.py`, `services/diversity.py`)

Never random:

1. **Fit** — an ordered candidate list per industry (hard pin: childcare,
   nonprofit) or per mood. Only archetypes that suit the brand enter the list.
2. **Seeded rotation** — the brand name hashes to a stable index
   (md5, same idiom as `hero_director`), so one brand regenerates identically
   while different brands diverge.
3. **Diversity** — SQLite history (`design_choices` table in the existing
   sitegen DB) records every site's picks. The next generation avoids what
   this site *and* the last few sites just used — still within the fit list,
   and fail-open: any DB trouble degrades to the seeded pick.

Kill switches: `DESIGN_ENGINE_ENABLED` (off → legacy classic/mega for every
site) and `DIVERSITY_ENGINE_ENABLED` (off → pure seeded rotation). The test
suite disables the diversity history (`tests/conftest.py`) so structural
assertions stay order-independent.

## Why not an LLM pass for chrome?

Chrome archetypes are a small closed vocabulary where a fit table beats a
7–9B model's judgement, and the existing design-brain passes (curated palette,
font pairing, per-section template variants) already cover the open-ended
choices. The manifest is deliberately the place a future LLM pass would write
into — it can fill `DesignDecision` entries with its own rationale and
confidence without any pipeline change.

## Phase 2 (implemented)

* **Complete decision log** — `plan_to_site` now folds the remaining design
  decisions into the manifest: `palette` (primary hex), `typography` (heading
  face), `hero-homepage` + per-page `hero:{slug}` (hero-director directives),
  and `section:{page}:{idx}` (design-brain LLM picks). The manifest is the one
  audit record for a generated site.
* **Diversity beyond chrome** — `record_manifest_choices` also records the
  `palette` and `hero-homepage` decision areas into the history
  (`_RECORDED_DECISION_AREAS`). Interior heroes and per-section picks stay
  audit-only so the chrome signal isn't diluted.
* **Template-variety rotation** — `block_to_section(variety_seed=…)` rotates
  the candidate head (top 3, deduped, feasibility-gated) for **text-only**
  sections, seeded by brand. Imagery-led preference is a hard signal and is
  never rotated (features/services synthesize card imagery from titles, so
  their photo-topped policy always leads); explicit design-brain ids always
  win. This kills the "every modern-mood site opens with the same CTA banner"
  convergence on the deterministic path (LLM off or failed). Threaded via
  `RenderContext.variety_seed`; empty seed (and every direct call without
  one) keeps the legacy order.

## Phase 3 (implemented)

* **Palette avoidance** — `build_theme(avoid_palettes=…)` threads the
  diversity history (`recent_choices("palette", …)`, wired at every
  `build_theme` generation call site) into `_curated_palette`. Avoidance
  rotates strictly **within the fit group** (a brand-hued site stays in its
  hue-near group; industry pins hold), all-avoided falls back to the seeded
  legacy pick, and the non-curated paths (tailwind snap, explicit
  design-language pick) never react to history. Dark builds take part too
  since they gained their own curated table — see "Mood-aware palettes"
  below. The chosen curated
  slug is tracked on `ThemeTokens.palette_slug` (internal — not part of
  BuilderStyles), so the manifest's `palette` decision and the diversity
  history operate on a real palette identity instead of a hex.
* **Manifest rides into the CMS** — `plan_to_site` attaches the serialized
  manifest as `builderStyles.designManifest` (the same flexible-JSON channel
  `googleFonts`/`brandMood` use — no CMS migration). Cross-repo:
  `builder/src/lib/builder-styles.ts` carries `designManifest` through
  `normalizeBuilderStyles`/`mergeBuilderStyles` as an opaque record, so a
  builder edit/save round-trip never strips the decision history off the
  entity. Both renderers ignore it.

## Phase 4 (implemented) — builder "Design decisions" panel

The builder's Styles tab now surfaces the manifest read-only:

* `builder/src/lib/design-manifest.ts` — defensive parser over the opaque
  `BuilderStyles.designManifest` record (legacy/hand-built sites and future
  manifest versions yield null / partial data, never a crash) + human labels
  for the decision areas.
* `builder/src/components/tabs/design-decisions.tsx` — presentation:
  archetype/mood/industry summary chips, then the decision list with a
  confidence meter (green ≥0.85 hard rules, blue ≥0.7 seeded fit, amber
  below — "safest to restyle") and each decision's recorded rationale.
  Collapsed to 6 rows with show-all. Wired into
  `components/tabs/styles-tab.tsx` behind the tab's search filter; hidden
  entirely when no manifest is present.

## Phase 5 (implemented) — archetypes in the shared catalog + in-builder swap

The chrome archetypes moved from imperative Python into the **shared section
catalog** — the same single-sourced spec body sections already use — and the
builder gained a local archetype swap on top of it.

* **Generation-time override** — `GenerateRequest`/`GenerateWithPagesRequest`
  accept optional `header_archetype`/`footer_archetype`; an explicit pin wins
  over fit/seed/diversity (recorded as a confidence-1.0 decision) and is the
  only way to reach a chrome the fit table would never surface for a brand
  (e.g. floating-pill on a nonprofit).
* **Catalog as the single source of chrome truth** — the 9 archetype trees
  live as `chrome-header-*` / `chrome-footer-*` entries in
  `builder/src/templates/section-catalog.json` (extracted from the Python
  builders with a marker theme, so faithful by construction; vendored copy
  synced as usual). `build_header`/`build_footer` are now thin content
  composers: they decide WHAT appears (logo subtree, CTA, wordmark, contact
  lines, conditional menus) and resolve theme tokens; the trees come from the
  catalog via a sync chrome-subset walker in `template_filler.py`.
* **Chrome directive vocabulary** (mirrored in `section-catalog.ts` — keep in
  lock-step): `$if` (conditional node), `$subtree` (caller-built logo block),
  `$splice` on `$repeat` (+ per-item `_name`), and `{{token}}` style
  placeholders. The generator resolves tokens to concrete hexes (legacy
  byte-identity holds — the exact dark-band literals are in the resolver);
  the builder resolves the same tokens to live CSS vars / `color-mix()`, so a
  swapped chrome re-themes with the palette.
* **In-builder swap** — the archetypes are registered as header/footer
  PRESETS (`lib/chrome-archetypes.ts`), so the existing preset machinery
  carries the site's real logo, menu assignments and CTA across a swap, with
  undo. The Design decisions panel exposes the swap directly; the reducer
  records it on the manifest (`withSwappedArchetype`) and applies the
  overlay rule (`headerBehaviorForArchetype`): swapping to the pill turns
  overlay ON with `revealBackgroundOnScroll: false` (overlay-native; safe on
  any site because both renderers gate the float per-page on the hero's
  `headerOverlaySafe` marker), swapping to a reveal-style archetype restores
  the reveal default and keeps the overlay flag as it was.
* The interim `/api/generate/rechrome` endpoint (builder→generator swap) was
  removed — superseded by the builder-local materialization.

## Mood-aware palettes (implemented)

Curated palettes were tagged by industry only, so a *playful* restaurant and a
*luxury* restaurant resolved to the same colours; and the dark scheme discarded
every curated and design-language pick, deriving its palette algorithmically
from the logo hue. Three changes in `theme.py`:

* **A mood axis.** `CuratedPalette.moods` narrows the candidate set after the
  industry filter. Untagged entries are **wildcards** (`_mood_narrow`), not
  non-matches — the legacy 28 predate the axis, and strict filtering would have
  dropped them all the moment one tagged entry appeared. Mood is also folded
  into the selection seed: the filter alone left mood nearly invisible, because
  wildcards dominate most pools and two moods with same-sized pools resolved to
  the same index. Both together give a mean of ~4 distinct palettes across the
  6 moods per industry, and keep a mood-specific palette out of the wrong brief.
* **A dark catalogue.** `CuratedDarkPalette` + `_CURATED_DARK_PALETTES` is a
  separate table with its own mapper, because the light token model (one `dark`
  + one `tint`) cannot express the three dark rungs a dark palette needs
  (`band` < `page` < `surface`). Slugs are `dark-` prefixed: `record_choice`
  stores a bare string with no scheme column, so an un-prefixed dark "saas"
  would share diversity history with the light one. `_dark_ink` is the mirror
  of `_brand_ink` — it caps the *light* body ink's chroma so paragraphs don't
  read as highlighted. `palette_mode="derive"` still yields the algorithmic
  `_dark_palette`.
* **A non-white page.** `CuratedPalette.page` lets an earthy palette carry its
  dominant 60% in the page itself, and `_light_surface` steps the band off the
  page rather than falling back to a fixed cool slate — which on a warm
  parchment page read as a bug.

`_curated_candidates` / `curated_palette_by_slug` / `curated_palette_options`
take `mood` and `scheme` and **must stay in lockstep** (pinned by
`test_options_and_lookup_mirror_the_candidate_set`); a slug can never resolve
across schemes. `curated_palette_options` quotes the **mapped** palette, not the
source tokens — a curated `dark` of `#0F172A` ships as `#171a22` once
`_brand_ink` has capped it, so the old menu showed the model colours the page
never painted. `resolve_color_scheme` is now called *before*
`generate_design_language` in both generate paths, so the model is offered the
menu for the scheme the site will actually be built with.

## Extension points (roadmap)

* **More archetypes** — adding one means: author a `chrome-*` catalog entry
  (tree + slots), extend the Literal in `models/design_manifest.py`, add fit
  entries in `design_director.py`, and a label in `chrome-archetypes.ts`.
  No renderer changes.
* **Decision re-rolling beyond chrome** — heroes/sections still regenerate
  only via the generator (they carry content + resolved imagery).
* **Section-order signatures** — the `design_choices` table is keyed by
  `area`, so page-composition signatures can join the history without a
  schema change.
