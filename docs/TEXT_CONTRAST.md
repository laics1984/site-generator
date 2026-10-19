# Shared card foreground contrast

## Root cause and correction

The v2 theme binder searched for a theme variable anywhere inside a background.
For `color-mix(in srgb, primary 6%, background)`, it classified the entire card
as solid primary and wrote `on-primary` ink. In the Kingdomcity preview this
meant pale `#fef9f5` copy on a light pink card, at approximately **1.07:1**.

Only a complete `var(...)` expression is now classified as a single theme
surface. The final foreground check resolves both mix components and their
weights, uses premultiplied alpha, and composites translucent fills over their
actual parents. A nested badge or button paints its own surface. Background
shorthand takes precedence over `backgroundColor`.

Gradient checks use their stops and samples within each interval, including
overlaid layers; an average is not a contrast guarantee. Unknown photographic
surfaces are left unchanged. Unsupported paint and gradients for which no
single foreground can reach AA require visual review; their backgrounds are
never rewritten by this foreground correction.

Readable existing copy retains its color. Failing copy takes an existing
semantic ink when one clears **4.5:1 across the measured surface**; otherwise
the existing contrast adjustment selects a passing foreground when possible.
Headings use the same floor because their mobile size may be normal text.
Existing weight, size and font differences preserve the title/body hierarchy.

## Implementation and compatibility

- `palette_styles.py`: whole-token matching and a foreground-only final check.
- `section_content.py`: strict color-expression parsing, alpha-correct mixes,
  and inherited surface samples.
- `schema_builder.py`: collect the final generated foreground variables.
- `palette_updates.py`: remeasure those variables against palette changes,
  including existing button/page overrides.
- `test_ai_palette.py`: mixtures, parent transparency, nested badges, gradients,
  photos, palette changes, raw repair boundaries and idempotence.
- `scripts/repair_preview_inks.py`: bounded repair of an exported preview.

The existing `builder_styles.cssVars` channel carries per-node foregrounds.
Text uses `var(--builder-contrast-<node-id>, <semantic fallback>)`, so existing
renderers need no changes. Palette patches keep their existing shape and do not
replace page objects. This does not introduce a database migration, endpoint,
global theme change, network dependency or render-time contrast observer.

The check updates foreground bindings only. It does not change card fills,
outlines, spacing, responsive rules, content, interactions or animations.
The earlier generation pipeline's existing photo and gradient treatments are
unchanged; the standalone repair does not rerun that pipeline.

## Repair an existing preview

From the generator's **More actions → Download site JSON**, retain the original
export and run from `backend/`:

```sh
.venv/bin/python scripts/repair_preview_inks.py original.json corrected.json
```

The script refuses to overwrite either an existing output or its input. It
projects only foreground changes onto the raw JSON, preserving metadata and
all non-color properties. It performs no CMS writes and publishes nothing.
Applying it again is idempotent. Existing browser-only previews do not gain
durable storage from this correction; retain the repaired export.

## Verification: Kingdomcity preview, 18 October 2026

- Original 24-page export: **593 measurable body-text nodes**, of which **81**
  failed 4.5:1: 34 process headings, 34 process descriptions and 13 age badges.
- Foreground-only repaired export: the same 593 measured nodes, **zero failures**.
  The 89 body-text nodes on unmeasured photographic surfaces were excluded from
  numerical claims and retained unchanged.
- Shared catalog under this palette: **294 measured nodes**, zero failures after
  binding; 30 nodes on unmeasured backgrounds excluded. The previously flagged
  button/link candidates pass this check; no links failed in the actual export.
- Browser inspection of all three Locations cards: computed fill
  `color(srgb 0.978118 0.94251 0.936314)` stayed unchanged. Titles/descriptions now
  use `#160a08`, giving **17.38:1**. Number badges retain pale copy on red at
  **6.90:1**.
- Verified desktop at 1920×1080 and mobile at 390×844, including stacked cards
  and smaller mobile text. Screenshots and the corrected JSON are retained in
  the local ignored `.cache/contrast-review/` directory.
- Structural comparison permits only text `styles.color` and derived
  `builder_styles.cssVars` differences. No content regeneration or publication.

Run the focused checks from `backend/`:

```sh
.venv/bin/python -m pytest tests/test_ai_palette.py tests/test_surface_ink.py tests/test_ux_audit.py tests/test_cta_band_contrast.py -q
```

The focused checks pass **162 tests**. The full suite passes **3,040 tests** and
reports four failures reproducible on unchanged HEAD: CSS
parity with the sibling builder's clamp-toggle rule, and three media/DNS upload
tests. They are outside this change. The frontend production build passes.

## Delivery and remaining limits

No live site was published, so no CDN cache was purged. Deploying generator
code fixes future output; existing published schema must be explicitly repaired
and republished through the normal workflow to receive updated foregrounds.
Palette updates recalculate the derived variables through the existing API.

The resolver supports the catalog's sRGB mixes and gradient shapes. Gradient
sampling is conservative but is not a pixel-perfect photographic audit or a
proof across every possible CSS interpolation mode. Unmeasurable backgrounds,
external/custom edits outside the generator's palette workflow, or gradients
spanning incompatible contrast ranges still need rendered review. No background
redesign is used to hide these limits.

## Follow-up: circular number badges on mobile

The process template already defines square number badges: 56×56px on desktop,
48×48px on tablet, and 44×44px on mobile. The shared responsive width heuristic
overrode the narrow widths to `100%`, producing a 272×44px capsule on the tested
phone layout.

The generator preview's `frontend/src/preview/lib/responsiveRuntime.ts` and the
public renderer's `lib/responsiveRuntime.ts` now exempt fixed square circular
`inline-flex` containers from that fluid-width fallback. Detection uses the
authored dimensions and radius, not a section name. Ordinary cards remain
fluid, and oversized boxes still use the existing overflow protection.
No template, color, text size, padding, or card positioning is changed.

Regression coverage in `webtree-public/lib/responsiveRuntime.test.ts` checks
mobile/tablet circles and ordinary fluid cards. Browser measurements confirm
all three badges at 44×44px on mobile, 48×48px on tablet and 56×56px on desktop.
The updated screenshot is `.cache/contrast-review/mobile-circle.png`.

This follow-up changes rendering only: no payload/schema, SEO, database, backend
support, publication or cache purge is required. The generator preview updates
through HMR; published sites receive the correction when the public renderer is
deployed, without content regeneration. No deployment was performed here.

## Follow-up: one background for the inset CTA, across all moods

`cta-banner` previously painted two independent surfaces: the outer section
selected a band for its neighbours, while the rounded inner wrapper painted
its own gradient/dark fill. The final band-selection rule also required the
inner panel to contrast with the outer section, reinforcing the two-color
result shown in the Locations preview.

`schema_builder.unify_cta_backgrounds()` now removes that inner paint before
the final CTA band selection. The wrapper remains transparent and retains
its padding, radius, border, typography, alignment, motion and buttons. Both
wrappers explicitly use a flat texture so a global texture cannot reintroduce
another layer. The outer color still follows the existing theme/mood/scheme
palette and neighbour/AA checks; no fixed color or per-mood color table was
introduced. This applies to all six moods and both color-system versions.
Photo CTA templates and unrelated card sections are unchanged.

Files for this follow-up: `backend/app/services/schema_builder.py`,
`backend/tests/test_cta_band_contrast.py`, and this change note. The former
panel/backdrop contrast constraint was removed because there is only one
surface to judge. Copy is rechecked even when the outer color stays unchanged.
The existing palette foreground check keeps later palette edits readable.

The current 24-page preview was updated without regeneration or publication:
19 inset CTAs now share their existing outer fill. The Locations CTA remains
brand red `#a22a2f`, its inner background is transparent, and the heading/body
remain pale `#fef9f5` at **6.90:1**. The white action button and all layout
dimensions remain unchanged. Desktop (1920×1080) and mobile (390×844) computed
styles confirm one flat fill and no inner background image.

Artifacts: `.cache/contrast-review/kingdomcity-single-cta.json`,
`cta-single-desktop.png` and `cta-single-mobile.png` in the same directory.
The source export remains available separately. Focused CTA, section-band and
palette checks pass **111 tests**. The full suite passes **3,052 tests**, with
the same four previously verified baseline CSS/media/DNS failures.

No API contract, database migration, new renderer support, SEO change or cache
purge is required. Future generated schemas carry the rule; existing published
content requires an explicit update through the normal publishing workflow.
Nothing was published or deployed in this session.

## Follow-up: remove the inner CTA frame (19 October 2026)

The transparent CTA content wrapper now explicitly has no border, outline or
box shadow, including side-specific borders and mobile/tablet overrides. This
supersedes the earlier decision to retain its outline. Its padding, alignment,
typography, background color selection and button styling remain unchanged.
The layout wrapper stays in place but no longer appears as an inset card.

Updated `schema_builder.unify_cta_backgrounds()` and its all-mood regression
tests. All **43 focused CTA/section tests pass**. Browser checks at 1920×1080
and 390×844 confirm a zero-width, `none` border, no visible outline and no box
shadow. The 19 CTA wrappers in the current preview were updated without
regeneration. Snapshots are `.cache/contrast-review/kingdomcity-cta-no-frame.json`,
`cta-no-frame-desktop.png` and `cta-no-frame-mobile.png`.

No API, schema, database, SEO or cache-policy changes. Nothing was published,
deployed or purged. Existing published content still requires an explicit
schema update through the normal publishing workflow.
