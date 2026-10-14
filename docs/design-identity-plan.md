# Design identity: distinctive sites from one business's evidence

Status: **approved 2026-10-11 with all four decisions; in progress.**

## Context

Sites generated for businesses in one industry come out looking alike. Kindergartens
are the worst case. The ask: make each business read as its own brand through its
evidence (personality, logo, photography, copy), not through its industry label.
The ask also covers multi-source understanding, layout variety, repetition
detection and validation.

Scope note: `docs/facebook-holistic-generation-plan.md` (approved 2026-10-11) already
plans most of the ingestion, consolidation, media-inventory and coverage work:
P2 provenance and quality, P4 index-answering consolidation, P6 allocation, and P7
coverage report. This plan **reuses those phases and doesn't re-plan them.** It adds
the dimension that plan doesn't cover: design identity.

## Progress and corrections

Each phase records what it changed and its before/after numbers from
`backend/scripts/measure_design_diversity.py` here.

### Phase 0 — done (2026-10-11)

- `services/design_fingerprint.py`: `DesignFingerprint`, `fingerprint_of(site)`
  read off the built homepage (hero, chrome, scheme, hue bucket, canvas, heading
  font, radius class, section template sequence, band sequence, divider, title
  inks), and a weighted `similarity`. Pure; tests in `test_design_fingerprint.py`.
- `scripts/measure_design_diversity.py`: 10 fixture brands per industry with ten
  different logo colours, through the real `plan_to_site`, LLM passes off,
  deterministic fake stock, optional isolated history DB (`--history`).
- Correction found while building it: a brand colour's hue is a **chroma**
  question, not HLS saturation (HLS calls the slate `#111827` 39% saturated).

**Baseline** (`--history`, code defaults; `pal` = distinct primary hue buckets):

| industry | schemes | pal | font | scheme | hero | seq | mean-sim | max-sim |
|---|---|---|---|---|---|---|---|---|
| childcare | off | 3 | 1 | 1 | 1 | 4 | 0.821 | 1.000 |
| restaurant | off | 7 | 4 | 1 | 1 | 2 | 0.682 | 0.885 |
| professional-services | off | 8 | 4 | 1 | 1 | 6 | 0.579 | 0.885 |
| health-wellness | off | 8 | 1 | 1 | 1 | 5 | 0.702 | 0.885 |
| nonprofit | off | 7 | 1 | 1 | 1 | 3 | 0.767 | 0.935 |
| childcare | on | 3 | 1 | 5 | 1 | 6 | 0.704 | 0.970 |
| health-wellness | on | 8 | 1 | 8 | 1 | 9 | 0.579 | 0.825 |

Two findings beyond the audit: the heading font collapses to one face for
health-wellness and nonprofit too (industry keywords outrank everything in
`_pick_pairing`), and the homepage hero is one template in every industry at
code defaults (`HERO_FULLBLEED_ALL_PAGES=true`; the operator's `.env` sets it
false, which only changes interiors for photo-forward moods).

### Phase 1 — done (2026-10-11)

- Childcare's ten look pins removed; the look is the `playroom` scheme
  (`band_tint`, `heading_ink`, `hero_title_accent`, `photo_cast`, `logo_height`
  are new scheme fields; tints and title inks derive from the site's palette).
  Chrome industry pins removed for childcare **and** nonprofit.
- Logo leads the light palette everywhere, outranking the LLM's curated slug.
  `theme.has_brand_hue` is chroma-based (≥ 0.15) and is the one test for
  "does this colour have a hue" — palette path, Tailwind snap and fingerprint.
- `PaletteStrategy` / `TypeDirection` vocabularies (models/brand.py); curated
  palettes tagged; six non-pastel childcare palettes; six direction-only font
  pairings (`MoodSpec.direction_pairings`, outside `font_pool`).
- Two new friendly-reachable schemes: `nature-studio`, `storybook-editorial`.
- `INDUSTRY_LOCKED_MOOD` → `content_blocks.resolve_mood`, one precedence at all
  three call sites. Divider and `build_header` lost their industry parameter.
- Prompts: the childcare personality line and `CHILDCARE_VISUAL_PROMPT` no
  longer prescribe pastel/rounded; photo-honesty rules unchanged.
- `DESIGN_SCHEMES_ENABLED` now defaults **true**.

**Corrections found on the way:**
- The hero industry specs (`hero_director._INDUSTRY_SPECS`) moved to Phase 4,
  where homepage-hero preferences per scheme are designed; Phase 1 left them.
- The first browser check of schemes (they had never shipped) found two
  renderer traps, fixed generator-side and pinned in
  `TreatmentRenderingTest`: decorated eyebrows stretched / mis-centred, and
  transparent cards painted with the theme grain (renderer resolves
  `transparent` to white — flagged as its own cross-repo task). A third
  (stretched monogram mark at 375px) predates this work; flagged separately.
- `test_a_flat_card_scheme_leaves_no_invisible_filled_card` was vacuous (its
  fixture produced no cards); it now asserts it checked something.

**Numbers** (`--history`, schemes on by default now):

| industry | pal | font | scheme | hero | seq | mean-sim (before → after) | max-sim |
|---|---|---|---|---|---|---|---|
| childcare | 3 → 8 | 1 → 7 | 1 → 6 | 1 | 4 → 5 | **0.821 → 0.467** | 1.000 → 0.730 |
| restaurant | 7 | 4 → 6 | 6 | 1 | 5 | 0.682 → 0.488 | 0.810 |
| professional-services | 8 | 4 → 6 | 8 | 1 | 8 | 0.579 → 0.461 | 0.843 |
| health-wellness | 8 | 1 → 8 | 8 | 1 | 9 | 0.702 → 0.459 | 0.695 |
| nonprofit | 7 | 1 → 7 | 6 | 1 | 4 | 0.767 → 0.460 | 0.723 |

The homepage hero is still one template everywhere — Phase 4's job.

### Phase 2 — done (2026-10-12), live check open

- `DetectedBrand` carries the brief: `audience`, `positioning`,
  `differentiators`, `personality` (six `AxisReading`s, models/brand.py),
  `unknowns`, and a computed `mood_basis` kept out of the model's schema
  (`SkipJsonSchema`). Same `detect_brand` call — no new round-trip.
- `services/brand_brief.ground_brief`: a differentiator is a FACT — grounded or
  dropped, and every number in it must be in the source (fuzzy grounding alone
  accepted "since 1988" against "since 1998"); an evidence span that is not
  source text is cleared, leaving an inference; null axes stay unknown.
- `mood_from_personality`: the mood is derived as the nearest of six
  prototypes once ≥3 axes are known, and outranks the detected enum. The
  detection prompt no longer maps industries to moods.
- The copy planner sees `DetectedBrand.copy_payload()` — identity fields,
  grounded differentiators, unknowns — never the inferences.
- `scaffold_enforcement.normalize_for_grounding` is public (it is the required
  input of the public `is_grounded_in_normalized`).
- Frontend: `lib/brandBrief.ts` (defensive view of the opaque payload) and
  `components/BrandBriefCard.tsx` — "What we understood" in the page picker,
  facts / inferences / unknowns labelled.

**Open:** the detection prompt grew (~400 input, ~250 output tokens). Measure
latency and reading quality against real sources once the local model server
answers (it was loading during this phase); a Claude run was not made because
it bills the operator's key.

### Phase 3 — done (2026-10-12)

- `services/design_direction.resolve_direction`: the one home for colour
  scheme → scored concepts → (model pick) → theme → hero height. All four
  former spellings call it (both generate endpoints, plan_to_site's no-theme
  path, the recipe preview — the last two with `use_model=False`).
- Scoring is pure (`brand_alignment`, `content_fit`, `score_schemes`); every
  scheme carries a `personality` target point. Ties fall to the brand-seeded
  rotation, so with no brief and no history the output is unchanged (harness
  numbers identical to Phase 1).
- The model picks among concepts by index (`ConceptChoice`); it replaced
  `generate_design_language`. `build_theme`'s `palette_choice`/`font_choice`
  and four menu helpers were dead after that and are removed; their lockstep
  tests retired, the avoidance tests kept.
- `DESIGN_CONCEPT_CANDIDATES` (default 3; 1 = no call) is the fast/standard
  mode. No builder change was needed: the account rides the existing `scheme`
  decision's rationale.

**Numbers** with `--personas` (each brand given one of five fixture briefs):

| industry | pal | font | scheme | seq | mean-sim | max-sim |
|---|---|---|---|---|---|---|
| childcare | 7 | 6 | 5 | 7 | 0.434 | 0.905 |
| restaurant | 7 | 7 | 6 | 5 | 0.497 | 0.855 |
| health-wellness | 9 | 7 | 5 | 8 | 0.468 | 0.898 |

Max similarity rises: brands sharing a fixture persona land on the same
direction, which is intended (same personality, same language). Telling two
such sites apart is Phase 5's fingerprint distance, not more randomness.

### Phase 4 — done (2026-10-12)

- Homepage hero: scheme `homepage_hero` (holds under either full-bleed setting;
  under "banded" only a photo-led lead) and `type_led_hero` for a source known to
  have no photograph (`ContentProfile.has_hero_photo`, tri-state). Industry hero
  specs folded into the friendly mood (the gradient hero is back in its rotation
  — `enforce_fill_contrast` covers what the childcare exclusion guarded).
- Adjacency swap in `block_to_section`; the first bound tried ("no worse ranked
  than the repeat") never fired on a mood's top family and was relaxed to "a
  family the site's preference names".
- Zigzag gated by `alternate_splits`; `visual_weight` + advisory
  `audit_design`.
- Harness: fixture brands now carry six measured photos and a contact page (with
  no contact page every CTA is dropped as dead and CTA-requiring heroes became
  infeasible — a fixture artefact); `--no-photos`, `--fullbleed`.

**Numbers** (`--history`, schemes on, photos on): homepage hero templates per
industry 1 → 2–4; mean similarity childcare 0.467 → **0.371**, restaurant 0.488 →
0.394, professional-services 0.461 → 0.390, health-wellness 0.459 → 0.385,
nonprofit 0.460 → 0.369. Photo-less sources (`--no-photos`): 5–6 hero templates,
mean similarity ~0.33.

### Phase 5 — done (2026-10-12)

- Fingerprints are recorded per site (`report_and_record_fingerprint`, area
  `"fingerprint"`, no schema change) and read back raw (`recent_records`).
- Freshness in the direction is fingerprint distance over the theme tokens of
  each realised concept (every fit-list candidate is now built — a handful of
  pure `build_theme` calls), not "was this scheme used".
- The manifest gets a `distinctiveness` decision that names no other site.
- Correction: the plan said the builder would label a new decision area; the
  builder's Design decisions panel no longer exists (`design-manifest.ts` is an
  archetype-swap helper now), so no cross-repo change was needed.

**Numbers** (`--history`, photos on): max similarity childcare 0.730 → **0.680**,
professional-services 0.843 → 0.610, health-wellness 0.695 → 0.614, nonprofit
0.723 → 0.630; heading faces per ten sites 6–8 → 8–9. Mean similarity childcare
0.360. With `--personas`, same-persona brands still share a direction by design.

### Phase 6 — done in part (2026-10-12), with two deliberate cuts

- **Done:** `DesignScheme.photo_tone` replaces the stock chain's industry tone
  rule (`_UPBEAT_INDUSTRIES` searched every kindergarten, restaurant and
  charity "joyful"); `ImageResolver(photo_tone=…)`, threaded to all three chain
  call sites. None keeps the legacy rule, so schemes-off is unchanged.
- **Cut — direction-aware re-ranking of the SOURCE's own photos.** Which of the
  business's real photographs leads should answer to relevance and measured
  quality, not to the chosen style: ranking them by taste trades honesty for
  look, and for most crawls the only measured attributes are width/height,
  which `_size_bonus` already uses.
- **Cut — read-time photo hues as personality signals.** Incidental photo
  colour is weak evidence of brand personality (a kindergarten's photos are
  green because of its garden, not because the brand is "organic"), and it
  would add read-time downloads on the crawl path.
- **Left with the Facebook plan:** its P2 (provenance, measured quality) and P6
  (offering cards take their own post's photo, grouped walls). P6 depends on
  that plan's P3/P4 consolidation, so it belongs to that sequence, not here.

### Phase 7 — done (2026-10-12)

- `prompts.GENERIC_HEADLINES`: one list of stock headings, two readers — the copy
  rule in `WEBSITE_COPY_GUIDANCE` (headings name the business's programmes,
  places, methods and `brand.differentiators`; never fill `brand.unknowns`) and
  the advisory `ux_audit.audit_copy` (`generic-headline`, read off `htmlTag`
  h1–h3, split headline groups read whole).
- Correction: quoting the whole list pushed the system prompt past an 8K-context
  model's input budget (`test_scaffold_batching`), so the prompt quotes four
  examples (`_PROMPTED_HEADLINES`) and the audit checks all twenty.
- The harness has fixed fixture copy, so it does not measure this; a live run
  against real sources does (open, with Phase 2's live check).

### Phase 8 — done, UI not yet visually checked (2026-10-12)

- `services/source_conflicts.py`: `merge_sources` records phone/email
  disagreements on `SourceContent.source_conflicts` (base's value kept); the
  read preview lists them. Addresses/hours are not compared (free text rarely
  matches even when it agrees). `services/contact_text.py` is the one phone/email
  extraction (`profile_text` now uses it).
- Website + Facebook Page: a second "Facebook Page" field under a website URL;
  the Page is read after the website and folded in (`composeSources` folds any
  number of reads; `combineSourcePreviews` is base + addition). The Facebook
  authority pass now runs only when the Page IS the source — added to a website
  it would have deleted the website's own testimonials and branches.
- Scanned PDFs are read with OCR (`text_detection.read_document_page`, 2000px,
  `OCR_DOCUMENT_MAX_PAGES` = 10) instead of refused.
- **Open:** screenshot the new source-panel field and conflict notice.

---

## 1. Evidence: why same-industry sites converge (measured, not assumed)

### 1a. Measurement 1: the theme for 10 childcare brands

I called `build_theme` in-process (read-only) for 10 childcare brand names, each with
a different logo colour (red, green, amber, blue, violet, none, teal, orange, black,
pink).

| | childcare, today | childcare, schemes on | restaurant (control) |
|---|---|---|---|
| distinct palettes | **3** (logo colour ignored: a red logo got sky blue) | 3 | 9 |
| distinct heading fonts | **1** (Fredoka, every brand) | 1 | 3 |
| radius | 20 for all | 12–34 | — |
| design scheme | none (feature off) | 5 | — |

### 1b. Measurement 2: production history

Source: the running container's `design_choices` table, read-only. It holds 60 sites
recorded since 09-17.

- The homepage hero was `hero-background-bold` on **56 of 60** sites.
- Childcare covered 18 generations across 5 schools: Tadika Murni, GloryKids,
  My Montessori, The Core Learning Center and MyKiddyLand.
  - Palette: `kindergarten-sky` 14×, `montessori-nature` 4×. The third palette was
    never used.
  - Header: only `classic` or `floating-pill`.
  - Footer: only `mega` or `cta-banner`.
  - Hero: always the full-bleed background hero.

### 1c. Root causes, with code locations

**A. Hard-coded industry looks.** Each of these pins the childcare look on its own,
and together they make one fixed template:

| Pin | Where |
|---|---|
| Mood locked to `friendly` | `models/content_blocks.py:1490` `INDUSTRY_LOCKED_MOOD`, applied at `routers/generate.py:1361,1666` |
| Logo hue ignored; only 3 curated pastel palettes | `theme.py:1364` `_INDUSTRY_CURATED_PALETTE`, `theme.py:825-827`. Pinned by `test_childcare_industry.test_brand_logo_hue_is_ignored_for_the_pastel_set` |
| Font always Fredoka/Nunito (the industry keywords score it uniquely highest) | `theme.py:_pick_pairing`, `_INDUSTRY_FONT_KEYWORDS["childcare"]` |
| Every flat section repainted in the same 6 pastels, in the same order | `section_content.py:1907` `_CHILDCARE_PASTELS`, `apply_childcare_pastel_rhythm`, called at `schema_builder.py:5230` |
| Every section title recoloured with the same 6 inks, in the same order | `section_content.py:1924` `_CHILDCARE_HEADING_INKS` |
| Hero title always two-tone with fixed inks | `schema_builder.py:2079-2114` `_CHILDCARE_HERO_INKS`, `_midpoint_word_split` |
| Divider always a wave (outranks mood and scheme) | `schema_builder.py:782` `_DIVIDER_SHAPE_BY_INDUSTRY` |
| Chrome pinned | `design_director.py:76,80`, `header_footer.py:154` (68px logo), `schema_builder.py:798` ("Request a Tour") |
| Hero spec pinned | `hero_director.py:_CHILDCARE_SPEC` |
| Prompts prescribe the look ("cream/sky/mint/peach pastel… rounded friendly display type") | `industry_personality.py:76-90`, `prompts.py:99` `CHILDCARE_VISUAL_PROMPT` |

**B. The anti-sameness layer ships off.** `config.py:326`
`design_schemes_enabled=False`, and `.env` doesn't set it. Sixteen authored design
schemes, 5 of which fit childcare, have never run in production.

**C. The LLM pick defeats the diversity history.** `generate_design_language`
(`design_brain.py:288`) chooses a palette slug from the industry menu (3 entries for
childcare). An explicit pick is "never overridden by history" (`theme.py:1576-1590`).
The prompt receives only name, mood, industry, the industry personality text and the
logo hex. It gets no source evidence and no recent-site avoidance. Same prompt, same
menu, same answer: hence `kindergarten-sky` 14 times.

**D. Brand understanding is one 6-value enum.** `DetectedBrand` (`planner.py:141`)
holds site name, tagline, summary, `brand_mood` (6 values), industry and a colour
hint. There is no audience, positioning, differentiators, personality axes, evidence,
or unknowns, and for childcare even the mood is overwritten by the lock.

**E. Homepage hero is fixed per mood.** In `hero_director._MOOD_SPECS`, four of six
moods plus the childcare and nonprofit specs lead with `_BACKGROUND`. The editorial,
asymmetric, minimal and bento heroes are never homepage leads. Weak or missing
photography still gets a stock full-bleed photo instead of a typography-led opening.

**F. Layout rhythm has no page context.** `block_to_section` picks each section
alone. Nothing stops adjacent sections sharing a layout family. `friendly` ranks
`grid` first (`section_content.py:861`), so features and services become card grids.
`apply_about_zigzag` always alternates image sides.

**G. Repetition is tracked per field, never as a whole design.** `design_choices`
records header, footer, palette, scheme and hero-homepage separately.
`hero-homepage` is written and never read. The homepage section sequence, type,
decoration and rhythm aren't recorded at all.

**H. Photography never informs design.** Dominant colour is read only to tune the
hero scrim (`image_sampling.py`). The theme is built before images are measured
(`generate.py:1707` runs before the prefetch at line ~1740).

**I. Duplicated orchestration.** Mood, colour scheme, design language, theme and
avoid-sets are resolved in three places:

- `generate.py:1356-1415` (from-source)
- `generate.py:1660-1718` (with-pages)
- `schema_builder.py:4818`

A fix to one silently misses the others.

**J. No guard against generic copy.** The prompts have no anti-cliché rule and no
check for one. The childcare voice line ("sell outcomes: confidence, curiosity,
belonging") steers the model toward "Where little minds grow".

**K. Ingestion gaps not covered by the Facebook plan:**

- A website and a Facebook Page can't be combined. There is one URL field
  (`App.composeSources`).
- `source_merge` unions facts but never flags conflicts, such as two phone numbers.
- Scanned PDFs are refused (`doc_parser.py:84`), even though rapidocr is already a
  dependency.

### 1d. What's already right and is reused, not rebuilt

- `design_schemes.py`: the style-pack model, gate vocabulary tests, and the
  scales-not-absolutes rule.
- `diversity.py`: `pick_diverse`, the `design_choices` table, and fail-open
  behaviour.
- `design_director.py` and `DesignManifest`, which records decisions with rationale.
- `hero_director`.
- `select_template` feasibility and gates.
- The `variety_seed` bound.
- `design_brain` per-section recipe.
- `merge_sources`.
- The index-answering LLM contract (`paste_structure`, `bio_condense`).
- `is_grounded_in_source`.
- `ux_audit`.
- The preview harness.

The catalog has 71 templates and every renderer reads inline styles, so **no
renderer, CMS or catalog change is needed** for the core plan.

---

## 2. Approach in one sentence

Make the **design direction** (an extended `DesignScheme`) the one home for every
look decision: palette strategy, type direction, decoration, hero lead and rhythm.
Choose it from a **brand brief** (personality axes with evidence), the content and
imagery actually available, and its **distance from recent sites' fingerprints**.
Demote every industry pin to a soft prior. Mood remains, derived from the
personality, so every mood-keyed table keeps working.

### Decisions (answered 2026-10-11; all four took the recommended option)

- **Q1:** the childcare look becomes **one direction among several**. It is the
  `playroom` scheme, chosen only when the evidence fits. It is not retired and not
  the default.
- **Q2:** **the logo colour leads the palette everywhere**, childcare included.
  Curated strategy palettes are used only when the logo has no usable hue.
- **Q3:** **website + Facebook as two simultaneous sources**, in Phase 8.
- **Q4:** concepts are **scored deterministically (top 3), then one reasoning call
  picks one by index**, replacing `generate_design_language`. A candidate count of
  1 skips the call.

The first implementation step copies this plan to
`docs/design-identity-plan.md` (the precedent is the Facebook plan) and adds a
"Progress and corrections" section that each phase updates.

---

## 3. Phases, in recommended order of impact per effort

Each phase notes which of the brief's phases (P1–P8) it covers.

### Phase 0: Diversity baseline harness (covers P1)

- **New:** `backend/scripts/measure_design_diversity.py`.
  - Makes the measurement in §1a repeatable: N fixture brands × industries, through
    the direction / theme / manifest path, with the LLM off.
  - Prints distinct palettes, type directions, schemes, homepage heroes, homepage
    section sequences, and mean pairwise fingerprint similarity.
- Every later phase states its before/after numbers here, the same idiom as
  `measure_facebook_read.py`.

### Phase 1: Un-pin the industry look; turn schemes on (covers P4/P5; biggest visible win, mostly data)

1. **Generalize the childcare passes into scheme fields** (`design_schemes.py`), so
   any scheme can use them and childcare doesn't force them.
   - `section_tint`: `"theme" | "multi-tint"`. Multi-tint derives light tints from
     **this site's palette hues** rather than fixed hexes, so even the pastel
     direction varies per brand.
   - `heading_ink`: `"theme" | "per-section-accent"`.
   - `hero_title_accent`: on/off. Replaces the hard-coded two-tone.
   - `divider_shape`: already a scheme field. Delete `_DIVIDER_SHAPE_BY_INDUSTRY`.
   - `apply_childcare_pastel_rhythm` and `apply_childcare_heading_colors` become
     generic passes keyed on the scheme, still run at the same point in
     `schema_builder.py:5225`.
   - The `playroom` scheme carries today's childcare look.
2. **Palette:**
   - Delete `_INDUSTRY_CURATED_PALETTE`, so a usable logo hue leads for every
     industry (decision Q2). The logo hue now drives the existing `_snap_palette`
     path.
   - Tag curated palettes with a `strategies` axis: `warm-neutral`, `earth`,
     `bold-accent`, `muted`, `mono`, `high-contrast`, `soft-natural`, `pastel`.
   - Author about 6 non-pastel palettes that fit childcare (earth, academic navy,
     bold contemporary, warm neutral).
   - The scheme states its `palette_strategy`. This is a data edit inside the
     existing `_curated_candidates` filter chain (one more `_x_narrow` stage).
3. **Type:**
   - Add `type_direction` to `FontPairing` and `DesignScheme`: `editorial-serif`,
     `geometric`, `humanist`, `rounded`, `grotesk-minimal`.
   - `_pick_pairing` filters on direction first; industry keywords then become a
     **soft** tiebreak.
   - Add about 6 pairings so each direction has ≥2 options. Keep the 2-family cap
     and latin-ext subsets.
4. **Mood lock:**
   - Delete `INDUSTRY_LOCKED_MOOD`.
   - The "never dark by default" lean for childcare stays: that is legibility, not
     look.
   - Phase 2's personality decides between friendly and playful.
5. **Chrome and hero pins** become scheme affinities:
   - Remove the childcare rows from `_HEADER_FIT_BY_INDUSTRY`,
     `_FOOTER_FIT_BY_INDUSTRY` and `_LOGO_HEIGHT_BY_INDUSTRY`.
   - Fold `hero_director._CHILDCARE_SPEC` into scheme `hero_policy` / homepage hero.
   - The nonprofit pins get the same treatment.
6. **Prompts:**
   - Rewrite the childcare `industry_personality.design` and `voice` lines to be
     evidence-led ("derive from the brand's own colours and photography").
   - Strip the palette and look prescriptions from `CHILDCARE_VISUAL_PROMPT`; keep
     its imagery-safety and photo-honesty rules.
7. **Ship `DESIGN_SCHEMES_ENABLED=true`** once the Phase 0 numbers and a preview
   screenshot pass. `conftest` keeps it pinned off for unrelated tests.

- **Tests:** rewrite `test_childcare_industry.py`. About 20 tests pin superseded
  decisions; each gets the new rationale, e.g. "logo hue leads" replaces "logo hue
  ignored". Extend `test_design_schemes.GateVocabularyTest` to cover the new
  vocabularies.
- **Risk:** this reverses parts of a brief the user supplied (decision Q1). Schemes
  on changes every site's density and radius, so visual QA is needed on 3 real
  sources per industry.
- **Expected:** for 10 childcare brands, ≥6 palettes (logo-led), ≥4 type
  directions, 5 schemes.

### Phase 2: Brand brief: personality with evidence (covers P3, plus the profile part of P4)

- **Extend `DetectedBrand` in the same `detect_brand` call (no new LLM call):**

  | Field | Notes |
  |---|---|
  | `audience` | |
  | `positioning` | |
  | `differentiators[]` | verbatim spans |
  | `personality` | 6 axes, each `{value: -2..2 \| null, evidence: span, basis: fact \| inference}` |
  | `unknowns[]` | |

- **Validation discards and never patches:**
  - A span that fails `is_grounded_in_source` against `raw_text` is dropped (for
    differentiators) or downgraded to `inference` (for an axis).
  - A null axis is **unknown** and is ignored by scoring.
- **Deterministic signals are folded in** (no AI):
  - logo saturation and lightness (`BrandIdentity.extracted_palette`);
  - where already measured, source-photo dominant hues (the Facebook pixel pass
    today; crawl and document photos in Phase 6).
- **`brand_mood` becomes derived:** nearest mood prototype on the axes. Explicit and
  user moods still win, so every mood table keeps working unchanged.
- **Cache key:** `_DETECT_BRAND_CACHE` keeps its key; the payload just grows.
- **Planner:** gets `positioning` and `differentiators` in its `brand` payload, which
  feeds copy specificity (Phase 7).
- **Frontend:** mirror the new fields in `types.ts` (`DetectedBrand`) and show a
  small "What we understood" block in the page picker, with facts, inferences and
  unknowns labelled.
- **Tests:** a new `test_brand_brief.py` with a fake client covering grounded,
  ungrounded and null axes, plus derived-mood mapping.
- **Risk:** prompt and output growth on the lean brand-detection budget
  (`brand_detection_max_chars`). Measure the latency delta.

### Phase 3: One design-direction service and concept selection (covers P4)

- **New `services/design_direction.py`:** the single home for what §1c-I resolves in
  three places. That is mood → colour scheme → candidate concepts → theme →
  manifest seed. The three call sites shrink to one call each.
- **Concepts are deterministic.** Each candidate is one fit-approved scheme, its
  palette and pairing (via the existing `build_theme` choice paths), and its
  homepage hero.
- **Concepts are scored on measurable criteria:**

  | Criterion | How it is measured |
  |---|---|
  | brand alignment | distance from the personality to each scheme's new `personality` target point (data, known axes only) |
  | content compatibility | a photo-led direction needs hero-eligible photos; an editorial one needs prose length |
  | image availability | count of hero-eligible photos |
  | distinctiveness | fingerprint distance from recent sites (Phase 5) |
  | component availability | feasible catalog variants for the planned section kinds |
  | accessibility | already guaranteed downstream; a scheme whose fixed elements cannot meet contrast is excluded |

- **Mode** is set by `DESIGN_CONCEPT_CANDIDATES` (config.py; default 3).
  - `1` = fast: the best-scored concept, no LLM call.
  - `>1`: **one** reasoning call chooses among the top-K concepts **by index**,
    seeing their rationale and the brand brief.
- **This replaces `generate_design_language`**, so it costs the same number of
  calls as today. An invalid reply or a failed call falls back to the top score.
  The LLM can no longer pick a palette slug that bypasses history.
- **Manifest:** records the chosen concept, every candidate's scores and the
  rationale (a `direction` decision area). The builder's `design-manifest.ts` gets
  one label (a cross-repo one-liner).
- **Tests:** `test_design_direction.py`:
  - scoring is a pure function;
  - the index contract;
  - fallback paths;
  - the three call sites agree (one function).

### Phase 4: Content-driven layout and rhythm (covers P5)

- **Homepage hero:** the scheme gains `homepage_hero` preferences, gated by
  evidence.
  - With no hero-eligible photo, a typography-led hero is taken:
    `hero-asymmetric-display`, `hero-editorial` or `hero-minimal`. This avoids a
    stock full-bleed photo that misrepresents the business.
  - The floating pill's `force_background` still wins.
  - Code: `hero_director.plan_site_heroes`.
- **Adjacency rule:**
  - `block_to_section(previous_family=…)` demotes a candidate sharing the previous
    section's `_layout_family`.
  - It only demotes, and only when an equal-or-better mood-ranked alternative is
    feasible.
  - Code: the same bound as the `variety_seed` rotation in `section_content.py`.
- **Gating:**
  - `apply_about_zigzag` runs only when the scheme sets `alternate_splits`.
    Otherwise splits keep one side.
  - `friendly`'s grid-first order yields to the scheme's `layout_bias`.
- **Visual weight:** each template id gets one weight class (`heavy` / `medium` /
  `light`, derived from layoutVariant and image slots). An advisory rhythm check
  in `ux_audit` flags ≥3 consecutive sections of the same weight.
- **No new components in this phase.** Add one only if Phase 0 shows a direction
  that can't be expressed with the 71 templates. That would be a catalog entry,
  synced as usual.
- **Tests:** extend `test_section_content` and `test_hero_policy`.

### Phase 5: Design fingerprint and repetition detection (covers P7)

- **`DesignFingerprint` is computed from the manifest and the built tree.** No
  content is stored, only design tokens. Its fields:
  - hero id;
  - header and footer;
  - scheme;
  - palette strategy, hue bucket (12 bins) and lightness class;
  - type direction;
  - homepage section-kind sequence and template ids;
  - card treatment, divider and decoration flags.
- **Stored** as area `"fingerprint"` in the existing `design_choices` table, with
  JSON in `choice`. No schema change (DESIGN_ENGINE.md already names this
  extension point).
- **`diversity.recent_fingerprints()`** plus a pure weighted similarity function.
- **Used before the build** inside Phase 3 scoring, which is cheap and needs no
  regenerate loop.
- **Reported after the build** on the manifest: `similarity_to_recent`, the nearest
  site, and the matching fields.
- **Exempt from steering:** explicit user pins and a strong logo hue. Brand beats
  novelty.
- The write-only `hero-homepage` history is subsumed.
- **Tests:** similarity properties; fail-open; pins exempt.

### Phase 6: Image intelligence for the chosen direction (covers P6)

- Execute the Facebook plan's P2 (one inventory with provenance and quality) and P6
  (offering cards take their own post's photo; grouped walls), **generalized to
  crawl and document images.** Their provenance fields live on the shared
  `ImageMetadata`.
- **New here:**
  - Direction-aware ranking: a tie-break term in `image_match` ranking, using
    measured attributes only (aspect, luminance, dominant hue, people count when
    vision ran).
  - Direction photography keywords appended to stock queries in `media.py`.
  - A cheap dominant-hue histogram of the top hero-eligible source photos, computed
    at read time so it is available before the theme. This feeds Phase 2 signals.
- The honesty rules are unchanged:
  - stock never stands for a real facility, person or child;
  - galleries are source-only;
  - the childcare consent decision D4 stands.

### Phase 7: Specific, non-generic copy (covers the brief's §16)

- **`prompts.py`:**
  - one rule: headings name this business's real programmes, activities, places and
    differentiators (now in the brief);
  - a data list `GENERIC_HEADLINES` of stock phrases to avoid.
- **`ux_audit`:** an advisory `generic-headline` finding (it never blocks, per the
  CLAUDE.md audit rule). The Phase 0 harness counts them across a batch.
- **Tests:** audit detection; the prompt carries the list.

### Phase 8: Ingestion gaps the Facebook plan doesn't cover (covers P2)

1. **Conflict report.**
   - `merge_sources` keeps the base value and records
     `SourceContent.source_conflicts` (`field`, `values`, `sources`) for phone,
     email, address, hours and name disagreements.
   - Shown in the preview. Never silently overwritten.
   - Mirror the field in `types.ts`.
2. **Website + Facebook as two simultaneous sources (decision Q3).**
   - A second URL slot reuses `merge_sources`.
   - Facebook facts keep their authority pass for contact and reviews.
3. **Scanned-PDF fallback** through the existing rapidocr reader
   (`text_detection`), capped by pages and a setting.

- **Tests:** `test_source_merge` (conflicts) and `test_doc_parser` (OCR fallback).

### Phase 9: Validation and regression (covers P8)

- **`ux_audit` gains:**
  - rhythm (Phase 4);
  - generic headlines (Phase 7);
  - fingerprint similarity (Phase 5);
  - the Facebook plan's P7 source-coverage report, generalized.
- **Regression test** over the Phase 0 harness with fixed fixtures. For 10 childcare
  brands:
  - ≥6 palettes;
  - ≥4 type directions;
  - ≥3 homepage hero templates;
  - mean pairwise fingerprint similarity ≤ a threshold set from the measured
    baseline;
  - plus the same check for restaurant and other industries.

---

## 4. Performance and cost

- **Zero additional LLM calls by default.**
  - The personality rides the existing `detect_brand` call.
  - The concept pick replaces `generate_design_language`.
- Fingerprints, scoring and similarity are pure Python, in milliseconds.
- One extra `design_choices` row per site.
- Fonts stay at 2 families.

## 5. Verification

- `cd backend && .venv/bin/python -m pytest tests`
- `scripts/check_catalog_contract.py`, `scripts/sync_catalog.py --check` (only if a
  catalog entry is added)
- `frontend npm run build`
- `scripts/measure_design_diversity.py` before and after each phase. The numbers go
  in the PR.
- Generate 3 real childcare sources (Tadika Murni, GloryKids, The Core Learning
  Center, from cached crawls) in-process with TestClient. Screenshot their homepages
  side by side through the preview harness (the `preview-visual-check` memory) at
  1280px and 375px.
