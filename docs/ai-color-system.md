# AI-directed color themes: implementation and validation

Implemented across the generator, public renderer, builder and CMS styles bridge. The detected business category influences the creative palette alongside the business's own identity, audience, positioning, personality, content, observed brand colors, available imagery and explicit design direction. It is evidence for the art director, rather than a fixed industry-to-color table.

New outputs opt into color-system version 2. Existing unversioned sites retain their rendering and builder customization behavior. No database migration or dependency was added. No production website was recolored or deployed during this work.

## Existing architecture and problems

The existing path was:

    Source content / logo
      -> brand detection and grounded personality
      -> scored design concepts and diversity history
      -> ThemeTokens
      -> styled BuilderElement page / header / footer schema
      -> builder_styles and generator preview
      -> CMS layout version and published page revision pins
      -> Nuxt public renderer

The generator already owned theme selection in theme.py and art direction in design_direction.py. It stored six canonical colors: primary, secondary, accent, surface, text and background. Typography, hero choices, layout rhythm and other design decisions were part of the same theme. Preview CSS was mechanically vendored from the public renderer. The builder already had theme controls and a styles-save contract, while generator regeneration recreated content.

The weaknesses were connected:

- The model selected a prepared design concept; its palette still came from deterministic families. Different brown logo seeds could both become the same orange primary and teal accent.
- Secondary also served as body ink and dark section fill. That prevented a genuinely supporting secondary color from working consistently.
- Fixed hue rotation and family snapping reduced brand fidelity and variety.
- Monochrome extraction could present synthetic blue as an observed logo color.
- Literal colors and semantic expressions were mixed in generated styling, so replacing four stored colors could leave parts of the site unchanged.
- Contrast repair missed some neutral text and inherited opacity. White foregrounds were not appropriate for every action fill.
- Preview had no palette-only proposal/save workflow. Full generation and full CMS push were unsuitable for changing only live colors.

Existing uncommitted accessibility work and concurrent multilingual/source-extraction work were preserved. The file inventory below describes this color implementation, rather than attributing all current workspace changes to it.

## Implemented pipeline

### Creative choice and evidence

The existing reasoning response accepts one optional, complete palette with primary, secondary, accent and surface. It uses the existing concept shortlist, grounded brand brief, personality and diversity history. It receives bounded lists of real brand, website and image colors, audience, business context and explicit direction. Website color observation reuses the existing rendered scrape; image evidence reuses existing metadata and image analysis. There is no new vision request.

The prompt asks for a coherent color relationship appropriate to this business: analogous, complementary, split complementary, monochromatic, neutral with a selective accent, or another appropriate relationship. It discourages repetitive industry defaults, excessive saturation and indiscriminate accents. Existing brand evidence and explicit choices take precedence over broad category associations. Without an explicit direction, a genuine observed primary is kept recognizable. Multiple observed colors remain available to the model when it chooses supporting colors.

All four proposed colors must validate together. A missing, malformed or partial proposal is discarded as a unit; the validated concept palette remains the fallback. Model failure during initial generation also retains a complete fallback. Failed palette regeneration returns an error and leaves the current palette intact. Monochrome/no-color extraction now remains unknown rather than claiming generic blue as evidence.

The AI palette flag is enabled by default. It reuses the existing art-direction request. When a single concept is pinned, one reasoning request is still needed to choose its palette; disabling the feature retains the previous path. Deterministic manual edits do not call AI.

### Deterministic colors and semantic application

The four creative values stay canonical, including a manually selected dark surface. Text and background are derived programmatically, preserving the six-color storage contract. The existing theme entry point applies this palette while retaining typography, layout, hero configuration and other non-color choices.

The color utility uses bounded OKLab/OKLCH transformations, chroma reduction into sRGB gamut and established sRGB luminance/contrast calculations. It derives readable foregrounds, hover/active states, supporting tints, section bands, surface hierarchy, inverse surfaces, text and meaningful control borders. Python and TypeScript share numerical fixtures; TypeScript has one authored source in the public renderer and is vendored into preview and builder. Both runtimes cache derivation for up to 256 normalized palettes. No calculations perform network or database I/O.

Normal text targets 4.5:1; large text and meaningful controls target 3:1. Base brand colors are preserved while readable foregrounds or derived tonal variants change first. Transparency and inherited opacity are considered by the generator's final text check. This follows [WCAG text contrast](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html), [non-text contrast](https://www.w3.org/WAI/WCAG22/Understanding/non-text-contrast.html) and the [CSS Color 4 OKLab definition](https://www.w3.org/TR/css-color-4/#ok-lab).

New generated schemas bind theme-dependent styling to semantic expressions at the existing final mapping boundary. Secondary is a supporting brand color; body ink and inverse surfaces have separate roles. Existing design schemes retain their distribution and geometry. There is no universal section-color sequence. Opaque supporting tints give small badges known foreground/background pairs, while actions, cards, forms, links and decorations use predictable roles. Text-bearing containers no longer fade readable copy through inherited opacity.

Logos, photo URLs, data-image layers, crop/geometry, explicit component fills and semantic status colors are preserved. Real photos use stable foreground/scrim roles with the existing sampling and overlay decisions so palette edits do not require fetching or replacing images. Decorative SVG grain is distinguished from a photographic background. Form error ink retains its red role and is contrast-adjusted for the actual surrounding surface.

Public styles opt into the same derived roles only when colorSystemVersion is 2. Scoped versioned CSS supplies action states, focus indicators, form borders/placeholders/help, supported pills and copy opacity. The builder adapter preserves the marker through normalization, merging and serialization; linked default buttons follow primary edits while independent overrides and the original brand baseline are retained. Preview carries the public adapter and CSS through the existing vendoring pattern.

### Preview, persistence and publishing

The generator preview now has four individual color controls, an optional design direction, Regenerate colors with AI, Save palette and Cancel. Manual previews are debounced and validated. AI returns a proposal for review before Save. Stale responses are ignored when the preview changes. A patch contains only theme, builder_styles, primary_color and secondary_color; it cannot return replacement pages.

Unrelated content regeneration carries an optional palette override. Staged preview saves use the existing project JSON and transaction; the four colors are reapplied to the currently stored snapshot, preventing a stale tab from restoring older typography or component settings. Remaining-page generation uses that saved theme. No new customization or locking subsystem was introduced.

Publish colors only is a separate action. It authenticates with the existing configured CMS target and builder session, reads current live styles, merges palette-related fields and preserves independent live style customizations. It never invokes the page/media/menu/full-site push path. The selected live site must already support version 2.

The CMS styles-save endpoint accepts an optional expectedLayoutVersionId. The comparison runs inside the existing locked layout transaction before creating a version; stale writers receive HTTP 409 with LAYOUT_VERSION_CONFLICT and cause no layout/revision mutation. Existing clients that omit the field retain the previous contract. Successful saves use existing layout versioning and published-revision repinning; public reads remain published-only.

A matching staged extension snapshot is updated only after CMS success. A subsequent snapshot-save failure is reported distinctly from a successful live save. Host-aware caching and publication propagation remain unchanged. The UI shows that caches may still be refreshing and offers Check public colors / Refresh live colors. Verification reads one ordinary anonymous public HTML response without credentials, cache bypass or purge; it is not confirmation for every route or CDN location.

## Endpoint and storage impact

| Contract | Change |
| --- | --- |
| POST /api/preview/palette | Generator-only deterministic preview; optional save into an existing unpublished staged project. Returns PalettePatch. |
| POST /api/brand/regenerate-palette | Generator-only AI proposal with the existing design concept pinned. Returns PalettePatch; invalid proposal leaves current colors. |
| POST /api/cms/palette/current | Authenticated selected-target live styles/version read; optional expected palette adds public verification state. |
| POST /api/cms/palette | Explicit styles-only publish; requires expected_layout_version_id and a supported live site. |
| Existing generator requests | Optional palette_override, preserving the chosen palette during content regeneration. |
| Existing CMS builder styles save | Optional expectedLayoutVersionId and version-2 marker validation. Stale expected versions return 409. |
| Existing theme/styles/project JSON | Optional color_system_version / colorSystemVersion marker, compact palette brief and palette override. Six canonical colors remain. |

No public endpoint was added or made writable. Public SEO, host resolution, page body schema ownership, layout pins and cache keys were not changed. No migration, new table, new package or purge infrastructure is required. Deploy the updated public renderer and builder with the CMS bridge before enabling generation of version-2 outputs in an older environment.

## Before and after evidence

| Area | Before | After |
| --- | --- | --- |
| Brand recognition | Brown seeds #8b6b4f and #8f6e52 both mapped to primary #ea580c, secondary #0f172a, accent #14b8a6 and surface #fff7ed in the legacy childcare fallback. | The versioned pipeline preserves #8b6b4f and #8f6e52 as distinct creative primaries, with the chosen supporting palette intact. |
| Normal neutral text | #888888 on white measured 3.544886:1. | Correction produces #767676 at 4.542225:1. |
| Secondary | Supporting hue, body text and dark section fill competed for one token. | Supporting hue, neutral copy and inverse surface have separate semantic roles. |
| Harmony and diversity | Concept/family selection and recurring hue rotation constrained outcomes. | AI chooses four related colors from business evidence; technical variants derive from that complete proposal. |
| Hierarchy | Literal fills and translucent chips could flatten sections or lose contrast. | Scheme-specific bands, elevated cards, opaque tints and readable on-colors respond together. |
| Recoloring | Regeneration could rebuild content, and literal styling resisted theme edits. | Palette-only patches keep pages, images, navigation, SEO and non-color settings intact. |
| Maintenance | Renderer-specific math and variable usage could drift. | One authored TypeScript implementation, Python numerical parity fixtures and adapter/CSS mirror checks. |

Six deterministic design fixtures were rendered: Little Acorns (clay/sage/gold/cream childcare), Bright Harbour (blue/coral/gold/ivory childcare), Ember Table (restaurant), Wellspace (healthcare), Northfield (professional services) and dark Orbit Works (technology). They exercise distinct plausible directions, including contrasting briefs within one industry. These are hand-authored test palettes, not evidence of actual model creativity.

The [screenshot gallery and machine-readable evidence](/Users/benjamin/.codex/visualizations/2026/10/15/01a13e4b-0db9-7bb0-a491-2509cabeb3a7/color-theme-review/index.md) contain the rendered comparisons and numerical before/after results. No pre-change screenshot baseline was collected; the before/after evidence above is numerical and source-grounded.

## Files changed for this implementation

Paths in this inventory are relative to the named repository. Some implementation files were committed externally while this long-running workspace task was in progress; the inventory covers the implementation, not just the current unstaged diff.

| Repository / files | Purpose |
| --- | --- |
| site-generator: .env.example; backend/app/config.py | Feature flag and operational documentation. |
| site-generator: backend/app/deployment/process_local.py | Classify the bounded pure derivation cache in the existing deployment audit. |
| site-generator: backend/app/models/brand.py | Complete four-color proposal, optional theme version and existing styles emission. |
| site-generator: backend/app/models/builder_schema.py; content_blocks.py | Compact palette brief and bounded observed website color evidence. |
| site-generator: backend/app/services/design_direction.py; theme.py | Creative selection/regeneration in the existing art-director and theme entry points. |
| site-generator: backend/app/services/color_system.py | Deterministic perceptual derivation, contrast and normalized cache. |
| site-generator: backend/app/services/palette_styles.py | Final semantic binding for versioned generated schemas and stable photo treatment. |
| site-generator: backend/app/services/palette_updates.py | Typed palette-only patches, style merging and compact design inputs. |
| site-generator: backend/app/services/palette_publish.py; palette_verification.py | Styles-only live publishing, post-success snapshot update and anonymous cache-aware observation. |
| site-generator: backend/app/services/generation_projects.py | Transactional palette-only persistence into existing project JSON. |
| site-generator: backend/app/services/logo.py; scraper.py; planner.py; prompts.py | Genuine evidence handling, website color observation and grounded prompting. |
| site-generator: backend/app/services/schema_builder.py; section_content.py; style_tokens.py | Integrate semantic binding, composited contrast and supporting/neutral role separation while preserving earlier accessibility work. |
| site-generator: backend/app/services/cms_client.py | Existing styles bridge read/save with optional expected version. |
| site-generator: backend/app/routers/brand.py; preview.py; cms.py; generate.py | Additive palette contracts and generation override. |
| site-generator: backend/tests/conftest.py; test_logo.py; test_ai_palette.py; test_cms_list_layout.py | Deterministic legacy fixtures, genuine logo evidence, palette/contrast/persistence/concurrency regressions and cross-renderer drift checks. |
| site-generator: backend/scripts/check_color_themes.py | No-network generation of the six real-schema visual review fixtures. |
| site-generator: frontend/src/App.tsx; components/PalettePanel.tsx; components/PublishDrawer.tsx | Preview editing/review/save/cancel and separate live color publishing/verification. |
| site-generator: frontend/src/lib/api.ts; types.ts | Typed additive requests and color patches. |
| site-generator: frontend/scripts/vendor-color-system.mjs; src/preview/lib/colorSystem.ts; src/preview/lib/styles.ts; src/preview/preview.css | Shared math and mechanically synchronized public rendering styles. |
| site-generator: docs/ai-color-system.md | This implementation, validation and rollout report. |
| webtree-public: lib/colorSystem.ts; lib/colorSystem.fixtures.json; lib/colorSystem.test.ts | Authored TypeScript derivation and numerical/behavioral parity coverage. |
| webtree-public: lib/styles.ts; app/assets/css/main.css | Version-gated token adapter, form/action/foreground behavior and scoped CSS. |
| builder: src/lib/color-system.ts; color-system.fixtures.json; color-system.test.ts | Vendored shared derivation and builder persistence/override/parity tests. |
| builder: src/lib/builder-styles.ts; src/index.css | Preserve version through editing; semantic roles and matching scoped behavior. |
| webtree-cms-api: app/Http/Controllers/BuilderStyleController.php | Optional concurrent styles-save guard within the existing transaction. |
| webtree-cms-api: tests/Feature/RePinPublishedPagesTest.php | Stale-write rejection, non-color preservation, repinning and published API read coverage. |

Temporary renderer QA entry pages and fixture payloads were removed from shipping source after the screenshots were captured. Their review artifacts remain outside the production repositories. Unrelated source extraction, multilingual, menu and existing accessibility changes were not reverted or reworked.

## Validation and limitations

| Check | Result |
| --- | --- |
| Full generator backend suite | 2,778 passed; three reproduced baseline failures described below. |
| Focused palette and renderer-mirror suite | 57 passed. |
| Public-renderer suite | 356 passed in 33 files; external-fixture scratch probe excluded. |
| Builder color/styles suite | 44 passed in four files. |
| CMS layout/public endpoint integration suites | 23 passed, 107 assertions, including conflicts, repinning and published reads. |
| Generator frontend production build | Passed. |
| Public Nuxt production build | Passed after removal of the temporary QA route. |
| Builder production build | Passed. |
| Diff whitespace checks | Passed across all four repositories. |

The three generator failures were reproduced against the original code: an existing AsyncMock storage-usage fixture in the monogram upload test and two document-rehosting tests dependent on resolving mmta.org.my. The public test command excludes the pre-existing probe.scratch.test.ts, which expects an external SP fixture directory and otherwise fails with ENOENT. These are not reported as passing checks. Builds retain warnings about the builder's large bundle, public Browserslist data age and Tailwind sourcemaps; they completed successfully.

Browser validation captured 24 home-page combinations (six palettes, desktop/mobile, preview/public) and eight light/dark contact-page combinations. All returned HTTP 200 with no JavaScript page errors or horizontal overflow in the home-page checks. All 142 color/button variables matched across the 12 preview/public pairs. Flat rendered text scans and the checked form labels, placeholders, borders, focus indicators and submit states had no below-target pairs. Screenshots for all five industry groups, both childcare directions and light/dark forms were actually opened and inspected. Manual preview/save used the real local generator API; Cancel restored the palette and non-color page content stayed intact. AI proposal and CMS publishing/verification responses in the UI test were mocked. The real public verification service also recognized all six local Nuxt fixture responses.

Limits remain explicit:

- The configured local AI server was unavailable. Model contracts, invalid outputs and failures were tested with fakes; real-model palette quality remains unverified.
- CMS integration tests ran locally against test fixtures. No production publish, deployed cache refresh or whole-site live recoloring was performed.
- Visual fixtures intentionally have no photographic assets. Photo-layer preservation and contrast logic are tested, but actual photographed hero readability and image/palette harmony were not visually verified in this run.
- The browser scanner skips gradient/image regions. Passing numerical token tests and flat-text checks are not a WCAG certification for every authored element or arbitrary custom override.
- An existing mobile layout difference remains: preview shows a header CTA that public hides. Color tokens agree; complete pixel/layout parity is not claimed or redesigned here.
- Public verification confirms one fetched response. It does not purge caches or prove propagation to every edge location or route.

## Reproduction

From site-generator/backend:

    .venv/bin/python -m pytest -q
    .venv/bin/python -m pytest -q tests/test_ai_palette.py tests/test_cms_list_layout.py
    .venv/bin/python scripts/check_color_themes.py /private/tmp/fowable-color-review.json

From site-generator/frontend, after changing public color math or CSS:

    node scripts/vendor-color-system.mjs ../../webtree-public ../../builder
    node scripts/vendor-preview-css.mjs ../../webtree-public
    npm run build

From webtree-public:

    npm test -- --exclude probe.scratch.test.ts
    npm run build

The builder tests are src/lib/builder-styles.test.ts, color-utils.test.ts, surface-ink.test.ts and color-system.test.ts, run with its Vitest Node API and the existing @ alias; build with npm run build. The CMS checks are RePinPublishedPagesTest, PublicSiteEndpointTest and PublicPageEndpointTest via php artisan test. Local visual checks used temporary entry pages through the real preview and Nuxt components; fixtures, harness copies, reports and screenshots are archived in the gallery directory rather than shipped as application routes.
