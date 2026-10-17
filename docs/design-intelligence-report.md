# Design intelligence: investigation and implementation report

Branch `feature/design-intelligence` · 2026-10-17. Plan:
`~/.claude/plans/pasted-content-id-cd57-role-floofy-summit.md`.

Every number here comes from `backend/scripts/design_benchmark.py`
(nine fixture businesses in `backend/tests/fixtures/design_bench/`). It runs
offline with the LLM passes off, so it measures the deterministic floor. The
screenshots come from `backend/scripts/design_screenshots.py` (the real preview
renderer, using real Pexels photos through one shared cache, so the revisions
compared differ only in design). Visual judgements are marked as such.

## 1. Root causes

**Verified** — each one is checked against the diffs or the code at HEAD:

1. **The industry's design knowledge was cut off from the look decision.**
   - `f0a7a8a` ("Improvement", 2026-10-15) landed the design-identity plan and deleted `generate_design_language`.
   - That deletion was right: it pinned one look, and 14 of 18 kindergartens shipped `kindergarten-sky`.
   - But its replacement concept call (`design_direction._concept_prompt`) sent only `Industry voice:`.
   - The industry *design* line no longer reached the model choosing the look, and nothing replaced it. Variety went up; art direction went down.
2. **The section pass contradicted the chosen look.**
   - `design_brain` was given the industry design line ("bento grids for saas…") but never the chosen scheme.
   - Its explicit pick outranks the scheme's `layout_bias`, so a coherent palette and type sat over off-language compositions.
3. **Selection was seeded, not evidenced.**
   - Positioning, audience, summary and differentiators never reached `score_schemes`.
   - `/from-source` had no personality at all.
   - `industry_affinity` was lost because the fit list is seed-rotated.
4. **No subcategory existed.**
   - The CMS taxonomy has 13 slugs. An IT-support firm was "saas", and a Montessori casa, a tuition centre and a baby nursery were all "childcare".
5. **Only childcare had a visual brief.**
   - SaaS, technology and agencies had one generic line, unchanged since `6791ff4`.
6. **The industry decided light vs dark.**
   - `_INDUSTRY_SCHEME_LEAN` outweighed the logo 2:1, so every SaaS and agency site was dark.
7. **The evaluator was a log line.**
   - `ux_audit` ran in `try/except: pass` and was discarded.
   - Its contrast rule also read `var(--builder-page-background, #ffffff)` as white, which reported light copy on every dark site as failing contrast.
8. **Found while verifying (latent, and pre-existing for brand names):**
   - Renderers flatten every `builderStyles` scalar, the design manifest included, into CSS custom properties on the site root.
   - In a server-rendered `style` attribute, one apostrophe (a brand named "St. Mary's") opens a CSS string that swallows every later token, including the colour and button tokens.

**Not established:**
- That the pre-`f0a7a8a` output was *better*. The measured history shows it was *uniform* (see §11).
- `CHILDCARE_VISUAL_PROMPT` exists in git only in its final form, so its earlier wording is known only from the plan's citation.

## 2–3. Historical logic recovered, with commits

| Commit | What it held | Recovered as |
|---|---|---|
| `6791ff4` | `industry_personality` design lines; childcare's pastel spec | the industry fallback line; the pastel voice is now the **gentle-nurturing** and **expressive-contemporary** directions, chosen on evidence |
| `3c42d01` | `INDUSTRY_LOCKED_MOOD`, curated childcare pastels, wave divider | `soft-nursery` scheme (soft pastel tints of the brand's own hues); the pastel palettes are its fallback for a hueless logo |
| `e3f6af6` | `generate_design_language` (industry design brief → the model) | each concept line now carries its direction's brief |
| `f0a7a8a` | removed the pins and the industry design line | pins stay removed; the knowledge returns as data (`creative_directions.py`) |
| `af5e130` | "avoid rainbow for childcare" in the palette prompt | replaced by the direction's own `palette_voice` |

## 4. Childcare

- Seven directions:
  - organic-editorial
  - contemporary-minimalist
  - expressive-contemporary
  - nature-learning
  - documentary-story (needs 8 or more own photos)
  - contemporary-academic
  - gentle-nurturing
- Each direction carries a brief, an imagery line, a palette voice, a hero preference and an image shape, plus:
  - a parent-conversion CTA intent (visit, tour, trial class, WhatsApp);
  - anti-patterns the evaluator checks.
- Archetypes: kindergarten, montessori-preschool, nursery-daycare, enrichment-centre.
- Each archetype has a focus line for the copy planner and a homepage journey: `approach-led` for Montessori, `class-led` for enrichment.
- Heroes get child-oriented shapes: arch, pebble or blob.
  - They are `borderRadius`-only, so all three renderers draw them, and the photo's mat follows the shape.
  - The evaluator reverts a mask that would crop a portrait.
- The absolute bans on pastel and rainbow are gone from both prompts. Colour voice is now the direction's choice, never a default and never forbidden.

## 5. SaaS, technology and agencies

- Seven directions:
  - product-led-minimal
  - b2b-saas
  - editorial-tech
  - premium-enterprise
  - creative-studio
  - developer-centric
  - human-centered-tech
- Archetypes keep the four kinds apart: saas-product, developer-platform, it-services, digital-agency, marketing-agency, tech-consultancy.
  - An IT firm filed under `saas` or `consultancy` gets technology directions and a `solutions-led` journey, not a product's pricing page.
- New `TECH_VISUAL_PROMPT` (1.4k chars) covers:
  - source evidence first;
  - no invented UI, clients or metrics;
  - stock that names a subject;
  - the generic-AI tells to avoid.
- A new `split-plain` hero (the split without the abstract wash) is the opening for the calm and technical directions.
- The light/dark lean is the direction's: developer-centric and creative-studio lean dark, product, enterprise and human-centred lean light.

## 6–8. The three modules

| Module | Where | What it does |
|---|---|---|
| A. Design Intelligence Engine | `business_archetype.py`, `creative_directions.py`, `design_direction.resolve_direction`, `DesignManifest.direction` (v2) | scores directions on personality alignment, archetype, the business's own words, material and freshness. The seed only breaks ties. Each top direction is realised by its best scheme, and the one existing model call picks among distinct directions |
| B. Website Generation Engine | `planner._design_payload`, `design_brain`, `hero_director`, `landing_patterns`, `schema_builder` | the planner gets visual brief → archetype focus → direction. The section pass gets the direction and the scheme's top-ranked variants only. The direction states the hero, image shape, journey, header CTA, motion and canvas |
| C. Design Quality Evaluator | `design_quality.py` | reuses `ux_audit` and adds composition, conversion, font, imagery and anti-pattern checks. It runs one bounded correction round (ambient motion, wave seams, dramatic shadows, a mask on a portrait), records the report on `DesignManifest.quality`, and never blocks |

Prompt hierarchy:

    global principles → industry voice + visual brief → archetype focus → direction → manifest → generation → evaluation

The industry examples were removed from the design-brain system prompt, which is now global only.

## 9. Diversity

- Directions differ per business on evidence; the seed only breaks ties.
- The fingerprint's hero token now includes the shape (`hero-modern-split@arch`), so the measured similarity reflects the actual hero composition.

## 10. Architecture decisions

- No new services or calls. The archetype rides the existing detection call; the direction rides the existing concept call.
- The CMS industry taxonomy is unchanged. The archetype is generator-internal, never pushed.
- `CREATIVE_DIRECTIONS_ENABLED` and `DESIGN_QUALITY_ENABLED` are pinned off in the suite (`conftest`), as design schemes are. With directions off, schemes are scored exactly as before.
- The builder-styles copy of the manifest is made CSS-safe (`design_manifest.builder_styles_manifest`). `GeneratedSite.design_manifest` keeps the exact text.

## 11. Evidence

Three revisions on the same nine businesses with the same photos:

- `d9e87cc` is before the design-identity rewrite, built through a small adapter. Moods are approximated: childcare is "friendly", others take the current personality mood.
- HEAD is before this branch.
- AFTER is this branch.

| | d9e87cc | HEAD | AFTER |
|---|---|---|---|
| childcare mean / max similarity | **0.859 / 0.935** | 0.269 / 0.393 | **0.246 / 0.360** |
| distinct homepage heroes (9 sites) | 2 | 6 | 6 |
| distinct heading fonts | 5 | 7 | 7 |
| distinct creative directions | — | — | 8 |
| mean design-quality score (new evaluator) | 85.3 | 92.0 | 93.1 |
| SaaS-industry pair similarity | 0.427 | **0.198** | 0.330 |

Screenshots: `docs/design-intelligence/three-way-childcare.jpg`, `three-way-tech.jpg`, `mobile-after.jpg`.

**Visual judgements (subjective):**
- **`d9e87cc` childcare.** All five schools were one page: a full-bleed photo with a pastel two-tone headline. The voice was cohesive, but there was no identity per school.
- **HEAD childcare.** Varied mostly by palette and type; three of five homepages still opened on the same full-bleed composition.
- **AFTER childcare.**
  - Each school has a composition that fits it. The Montessori is a calm arched editorial split; the academic kindergarten is arched and structured.
  - The playful centres get blob masks; the nursery gets a pebble on soft pastel bands.
  - The traditional kindergarten and the Montessori share the arched editorial opening, separated by type and palette.
- **AFTER tech.**
  - The abstract gradient-blob washes are gone, and SaaS and IT are light and calmer.
  - The consultancy reads more premium.
  - The creative studio stays on a dark poster canvas.
  - The SaaS pair is MORE alike than at HEAD: both of their directions are realised by `calm-clinical` under their moods.

**Correctness fixes found by the screenshots:**
- The arched photo kept a rectangular mat.
- The calm directions sat on loud washes.
- The manifest/CSS breakage described in §1, item 8.

## 12. Tests

- 2,955 passed, 3 skipped. 1 failure exists at HEAD too: `test_push_data_urls::MonogramSurvivesUploadTest`, an AsyncMock of `get_storage_usage`.
- New modules:
  - `test_business_archetype` — detection, healing, whole-word matching, prompt enum
  - `test_creative_directions` — vocabulary and reachability for every direction × industry × mood
  - `test_direction_selection` — evidence beats seed, the gentle nursery vs a lively daycare, an IT firm is not a SaaS product, the flag off is unchanged, one call
  - `test_design_quality` — every check, a bounded and idempotent correction, the CSS-safe manifest
  - `test_image_shapes` — catalog node vocabulary
  - `test_design_benchmark` — no score regression past the committed `baseline.json`, the same direction across logo variants, no extra model call
- `npm run build` passes.

## 13. AI usage and cost

- **Calls per generation:** unchanged — 1 concept, 1 palette, 1 section recipe, plus content.
- **Prompt chars, HEAD → AFTER:**

| Prompt | HEAD | AFTER | Change |
|---|---|---|---|
| scaffold system | 11,639 | 11,606 | −33 |
| brand detection | 3,943 | 4,316 | +373 (archetype enum) |
| concept system | 2,387 | 2,508 | |
| concept user prompt, Montessori example | 1,677 | 2,021 | |
| palette system | 1,162 | 1,283 | |
| design-brain system | 1,758 | 1,654 | |

- **Planner design payload per content call:**
  - childcare: about 7.7k → 8.5k chars (+~180 tokens);
  - saas/agency: 0 → 2.1k chars (+~520 tokens), the price of having a technology brief at all.
- The legacy `/from-source` system prompt now always carries the tech brief, +1.5k chars.

## 14. Limitations

- **The benchmark is the deterministic floor.**
  - The model's concept pick, the AI palette and the section recipe are off, and stock is cached.
  - No live LLM generation was run: the local AI server was unreachable from this process.
  - Archetype detection on the local model is therefore unverified; the deterministic fallback is tested.
- **The fixture copy is placeholder.** Card descriptions read "A sentence that explains…", so screenshots judge composition, not copy.
- **The `/from-source` path still has no brand brief** (no personality), so its direction leans on archetype and evidence words.
- **`stacked` (two overlapping photos) was not built.** It needs a new catalog variant in the builder.
- **The renderer still flattens `designManifest` into CSS variables.** The generator now ships it CSS-safe. The right fix is upstream (webtree-public `flattenStyleTokens` should skip the manifest), then mirror it in the preview.
- **The compact mobile header stretches the monogram logo into a wide pill.** This exists at HEAD too.
