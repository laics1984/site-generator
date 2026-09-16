# CLAUDE.md — Webtree Site Generator

Turns a scraped URL or an uploaded PDF/DOCX into a multi-page site whose JSON
matches the webtree **builder** `BuilderElement` schema 1:1, then pushes it to
the webtree CMS. LLM calls go to a **locally/self-hosted** OpenAI-compatible
server by default; a Claude model can be picked per role in the UI's model menu
(see "The Claude API provider").

## Where this repo sits

Four sibling repos under `~/Documents/Projects/webtree/`:

| Repo | Stack | Role |
|---|---|---|
| **site-generator** (here) | FastAPI + React | AI generation/scraping only. Its SQLite is ephemeral crawl state, **not** the app datastore. |
| `webtree-cms-api` | Laravel + MySQL | The real backend. Multi-tenant by `entity_id`; public routes are host-based/tokenless, admin routes are JWT + `entity_api_token`. |
| `webtree-public` | Nuxt | Renders the published sites. Ships through CI: `.github/workflows/deploy_cloudflare.yml` runs `npm run build` and deploys to Cloudflare Workers on push to `master`, so a renderer edit reaches every published site with no regeneration. `npm test` (vitest) is the local gate; `.nuxt`/`.output` are gitignored (they used to be tracked and served — that is no longer true). |
| `webtreesuite-admin-app` | Vue 3 + Vuex | Dashboard. `vue-tsc` is not clean on baseline — don't treat it as a gate. |
| `builder` | React/TS | The visual editor. **Source of truth for the schema and the section catalog.** |

Cross-repo work: check which repo owns the feature before editing. Contact /
lead capture, for example, lives entirely in cms-api + public + admin.

## Commands

```bash
# Run the whole stack (AI server first, then compose)
./dev.sh up            # ./dev.sh down | status
docker compose up --build          # frontend :5174, backend :8001

# Tests — the local py3.11 venv is the reliable path (Docker is often down here)
cd backend && .venv/bin/python -m pytest tests           # 59 test modules
cd backend && .venv/bin/python -m pytest tests/test_media.py -k overlay -x
backend/scripts/run-tests.sh                             # same suite inside the container

# Contracts / checks
cd backend && python3 scripts/sync_catalog.py --check     # catalog parity with builder
cd backend && python3 scripts/check_catalog_contract.py   # catalog block rules
cd frontend && npm run build                              # tsc -b + vite build (the only typecheck)

# Health
curl localhost:8001/health && curl localhost:8001/health/llm
./ai-server/ai.sh status | up | down | pull | logs
```

`backend/.venv` **must be Python 3.11** — PyMuPDF 1.24.10 has no wheel for
Homebrew's default Python. Recreate with `python3.11 -m venv .venv`.
Playwright for render checks: `backend/.venv/bin/playwright install chromium`.

## Pipeline (detail in [ARCHITECTURE.md](ARCHITECTURE.md))

```
URL / document / FB Page / paste → scraper.py | doc_parser.py |
                                    facebook_source.py | paste_source.py → SourceContent
  → planner.py + LLM        → SitePlan (semantic ContentBlocks: hero/features/cta/…)
  → schema_builder.py       → BuilderElement tree   (deterministic; owns ALL styling)
  → push_orchestrator.py    → CMS (pages, media, menus, header/footer, styles, publish)
```

**The LLM never emits `BuilderElement` JSON.** It produces small semantic blocks;
`schema_builder.py` + `style_tokens.py` map them deterministically. Keep it that
way — styling belongs in the mapper (pure, unit-testable), not in prompts, so the
look survives model swaps.

Layered on top: `design_director.py` composes a `DesignManifest` (header/footer
archetypes + a decision log), `diversity.py` steers consecutive sites apart via
SQLite usage history, `hero_director.py`/`theme.py`/`design_brain.py` own hero and
palette choices. See [docs/DESIGN_ENGINE.md](docs/DESIGN_ENGINE.md).

Backend layout: `app/config.py` (all settings), `app/models/`,
`app/routers/` (all mounted under `/api/*` except health), `app/services/` (53
modules — the largest are `schema_builder.py` 4.5k, `scraper.py` 2.6k,
`section_content.py` 1.8k lines).

## Hard contracts — breaking these fails silently

1. **Builder schema parity.** `backend/app/models/builder_schema.py` mirrors
   `../builder/src/lib/site-navigation.ts`. Any drift breaks editor
   compatibility. Change both together. Same for the public schema surface
   (`BrandMood`, `ColorPalette`, `ThemeTokens`, `to_builder_styles`).
2. **Section catalog.** `../builder/src/templates/section-catalog.json` is the
   source of truth; `backend/app/templates/section_catalog.json` is a
   byte-identical vendored copy (`json.dumps(indent=2, ensure_ascii=False)+"\n"`).
   Edit the builder side, then `scripts/sync_catalog.py`.
3. **Header logo naming.** The builder treats a header image as a brand logo only
   when `element.name` matches `/brand/i`; anything else renders as a cropped
   generic image. `header_footer.py` defaults `name="Brand Logo"`. Footer logos
   get no native sizing — they need explicit `width:150px` (not `auto`, which
   collapses to 0), `height:40px`, `minHeight:0`, `objectFit:contain`.
   **Gate rendering on `brand.logo_render_ok`, never on `logo_url`.** Detection
   lives in `services/logo_extraction.py`, which ranks a real mark (header
   `<img>`, inline header `<svg>`, logo-named `<img>`) above a declared icon and
   keeps `og:image` as a **palette source only** — provenance rides along as
   `BrandIdentity.logo_source`. The old order checked apple-touch-icon *first*
   and the actual logo *fourth*, so any site with a touch icon or a social card
   — most sites — rendered its favicon as the brand mark. `_build_brand_candidate`
   sets `logo_render_ok` from the **decoded** pixel size (a `sizes` attribute is
   a claim, not a measurement). Header, footer, `Organization.logo` in the
   JSON-LD, and the push-time media upload all honour it.
4. **Preview renderer is a port, not a rewrite.** `frontend/src/preview/` mirrors
   `webtree-public`'s renderer. **Don't fix bugs there** — a preview that renders
   better than the live site is still a defect. Fix upstream, then mirror.
   `preview.css` is generated: `node frontend/scripts/vendor-preview-css.mjs ../../webtree-public`.
5. **Config has one home.** Every knob is a field on the `Settings` class in
   `backend/app/config.py` (env name = upper-cased field). No module reads
   `os.environ` — keep it that way. The frontend reads no `import.meta.env`; it
   uses relative paths through the Vite proxy.

## Design schemes — the anti-sameness layer

`services/design_schemes.py` is the **single home** for every value that used to
be one answer per mood. A `DesignScheme` bundles shape (`radius_scale`,
`card_treatment`, shadow, texture, divider), density (`padding_scale`,
`card_padding_scale`, `container_max_width`, type ratio, heading weight/tracking,
eyebrow treatment), composition (`layout_bias`, `hero_policy`, chrome affinity)
and colour expression (`section_rotation`, `inverted_cta`). Sixteen are authored;
all 60 (mood, industry) cells offer ≥3.

**Selection is the chrome machinery, reused, not a second one**: fit list →
`diversity.pick_diverse` seeded on the brand name → history avoidance on the
`"scheme"` area. **Threading is the `palette_choice`/`avoid_palettes` idiom,
reused**: `build_theme(scheme_choice=, avoid_schemes=)` at the same three
generation call sites, slug onto `ThemeTokens.design_scheme` (internal like
`palette_slug`, **absent from `to_builder_styles()`** — the wire payload is
unchanged), and every pass reads it back with `design_schemes.for_theme(theme)`.
So a new axis is a data edit, not a parameter threaded through the pipeline.

**Every field defers** (`None` / `"inherit"` / empty tuple) to the per-mood
tables it layers over — `MOOD_SPECS`, `_MOOD_LAYOUT_PREFERENCE`,
`_DIVIDER_SHAPE_BY_MOOD`, `hero_director._MOOD_SPECS`, `_HEADER_FIT`/`_FOOTER_FIT`,
all untouched. That is what makes `DESIGN_SCHEMES_ENABLED=false` a true no-op
rather than a second code path, and `conftest` pins it off for the rest of the
suite (a scheme moves radius, density, measure, layout order and hero policy per
brand, so a structural assertion elsewhere would really be asserting whichever
scheme that fixture's name hashed to).

Gates use the section catalog's vocabulary and semantics: `moods` and
`industries` are hard, **empty = neutral wildcard**, and `industry_affinity` is a
soft rank that never excludes. A gate in the wrong vocabulary is a silent off
switch — `test_design_schemes.GateVocabularyTest` fails on any unreal value, on
an unreachable scheme, and on any cell with fewer than three candidates.

**Scales, not absolutes.** `padding_scale` multiplies what the template chose;
the catalog's root paddings vary on purpose (72px ×30, 104px ×10, 128, 140) and
one flat number would destroy that composition.

Three renderer rules the schemes must obey, all invisible from Python:

- **`gap` and card `minHeight` are the renderer's.**
  `webtree-public/lib/responsiveRuntime.ts` pins them on 21 node **names** with
  `!important` at desktop and tablet (mobile returns `{}`). An inline value
  loses on the published site and wins in both editors — a three-way divergence.
  `apply_density_scale` owns **vertical padding** instead; the name sets are
  mirrored as `RENDERER_PINNED_GAP_NAMES` / `RENDERER_PINNED_MIN_HEIGHT_NAMES`
  and a test parses the TS to catch drift.
- **Lengths are strings.** Vue's `:style` drops `width: 24` as invalid; React
  appends `px`. Use `scaled_px`.
- **Texture deletes gradients.** `SectionBlock.vue` swaps a gradient for
  `var(--builder-color-primary)` when `backgroundTexture` is set — a section gets
  one or the other, never both.

Chrome affinity **narrows** the fit list; reordering it would be a no-op, since
the picker takes a seeded index across the whole list. Still an intersection,
never an addition, and always ≥2 candidates so diversity has room. The pill's
`force_background=True` outranks any `hero_policy` — broken chrome beats taste —
and every contrast pass runs downstream of the scheme's, so no scheme can ship
an illegible page. Builder side: `design-manifest.ts` parses `design_scheme` and
labels the `scheme` decision area; nothing else cross-repo changed.

Tests: `test_design_schemes.py`. Docs: [docs/DESIGN_ENGINE.md](docs/DESIGN_ENGINE.md).

## Adding a new section block

Catalog entry in the builder + sync, then wire the generator:
`SectionType` literal + block model + `ContentBlock` union in `content_blocks.py`
→ mapper in `section_content._MAPPERS` → `planner._SCAFFOLD_BLOCK_SCHEMAS`
→ `page_inference._SIGNAL_PATTERNS` + `_SIGNAL_ELIGIBLE_TYPES`. Hero variants
must **also** land in `hero_director._MOOD_SPECS` rotations or they're never
picked. New variants of an existing type need only the catalog entry.

Catalog rules: no `display:grid` (use 2Col/3Col/flex) — the one sanctioned
exception is a `$bento` fan-out container, which `check_catalog_contract.py`
exempts by node name. `$bento` has full parity: `_bento_placement` in
`template_filler.py` and `bentoPlacement` in `builder/src/lib/section-catalog.ts`
are line-for-line mirrors — keep them in lockstep.

Layout selection precedence is `explicit_id → content preference (_PREFERENCE) →
mood preference (_MOOD_LAYOUT_PREFERENCE, keyed on `layoutVariant`) → pool[0]`,
all gated by `is_feasible`. The `variety_seed` rotation is a **tiebreaker, not a
chooser**: it may reorder templates the mood ranks no worse than the current
leader, never promote one the mood ranks worse, and never promote a photo-led
variant when the source supplied no photography. Before that bound, it displaced
the mood's own pick — `modern` ranks bento first and was still landing on a card
grid. Content preferences must not pin a layout family for text sections either,
or the mood never gets a say.

Two optional gates restrict an entry: `"moods": [...]` (values must be real
`BrandMood`s) and `"industries": [...]` (real `IndustryCategory` values); absent
= neutral. **A gate written in the wrong vocabulary is a silent off switch, not
a gate** — `profile-centered` declared `classic`/`elegant`/`trustworthy`/`calm`
and was unreachable on every site for as long as it existed, while its own test
passed by fabricating those moods. `test_icons.CatalogGateVocabularyTest` now
fails on any unknown value or any mood-gated entry no mood can reach.

A photo-topped policy in `section_content._policy_template_id` overrules the
layout pick for `features`/`services` whose cards carry imagery **the source
actually supplied** — a bound `image_url` or a planner-written `image_query`
(`_items_have_real_images`), read off the block rather than the mapped content.
`_item_image` still backfills a stock query from the card's title, so a photo
layout stays *feasible* when nothing else is available; it just no longer forces
itself, which it used to do on every section ever built. It governs **its own
`layoutVariant` family only**: `features-card-grid`
is `features-image-cards` with the pictures taken out, so it stays overruled,
while a bento or an editorial list is a different composition and competes
(`_leads_with_photo` for photo variants, `_layout_family` for the rest). Scoped
to the whole section type it silently killed 4 of 6 features layouts and 3 of 6
services layouts. The friendly/playful `services-programs-age` rule is more
specific and stays absolute, since no other variant declares its age badge, and
it now also requires the items to actually carry one (`_items_have_audience`).

## A team grid holds people

`profile_text.roster_is_people` is the one gate, applied once, at the only layer
that can answer the question: `scraper._extract_profile_candidates`. Every
consumer inherits it — `page_inference._looks_like_directory_page`,
`_profile_pool_for`, `_scraped_team_members`, `_directory_roster_members`,
`_rostered_names` — so there is no second spelling to keep in sync.

**A card in isolation is unclassifiable.** A product tile, a facility card and a
staff card are the same object at close range: a square photo, a Title-Case
caption, maybe a paragraph. Only the group disambiguates, which is the rule
`section_extraction`'s classifier already states and the portrait walk used to
violate — it decides one card at a time, and
`_confirm_profiles_against_sections` can only *veto*, so when the classifier
finds no card group the walk's guess stands unopposed.

The discriminator is what a card **says**: a job title, a sentence of prose, or a
personal contact, on a majority of the group (0.6, mirroring `_classify`). A card
whose markup declares itself (`_PROFILE_CONTAINER_HINTS`) needs no further
evidence and rides the `declared` argument.

**"Carries text" is not the test** — a product tile carries text. Every field is
read through `looks_like_spec_line`, because `looks_like_team_role` accepts
`"Size: 42 inch(H) x 48 inch(L)"` as a job title (short, no full stop, no contact
token), and LumiBright's detail pages caption every tile that way. Specs are
found by physical units and dimension pairs — physics, not industry vocabulary,
the same way `has_contact_token` leans on a phone regex. A person's title does
carry numbers ("Level 3 Coach", "Director since 1998"); it does not weld one to a
unit. The trailing lookahead is case-sensitive inside a case-insensitive pattern
on purpose, or `"2.5mColor"` and `"3 monkeys"` go the same way.

Neither of the two obvious discriminators works:

- **Geometry.** LumiBright's product tiles are 800×800, so `_has_portrait_aspect`
  and `_measured_portrait` both wave them through.
- **Vocabulary.** `_NON_NAME_TAIL_TOKENS` was written for a childcare site's
  "Innovation Centre" and catches *nothing* in a safety-equipment catalogue, a
  menu, a portfolio or a service grid — `CardRackIsNotARosterTest` pins that 0-of-4
  measurement so nobody re-simplifies the group rule back into a denylist. Adding
  nouns to it to fix a site is fixing that site only.

Getting this wrong cost the products twice over. Three or more candidates stamp
their photos `role="portrait"`, and `source_router._UNPROMPTABLE_ROLES` bars a
portrait from the pool the LLM may bind to a features/services card — so every
tile misread as a person was simultaneously withdrawn from the section that
should have shown it. Fixing the roster gave 6 of 18 product photos per page back
to the products.

Two related rules: a name is one person, so `&` and `/` disqualify one
(`_looks_like_person_name` + its mirror `looks_like_team_member_name` — industry-
neutral, unlike the tail tokens). And a page's own cards outrank the site-wide
`_profile_pool_for` flatten in `_ensure_scraped_team_blocks`; one department's
roster is not another page's team.

The gate deliberately does **not** run again downstream. `_roster_members` and
`_sanitize_team_block` see cards one at a time, stripped of the evidence — a lone
card on a person's own page, or a real person whose CTA role got blanked — so
re-asking there is double jeopardy, and both had to be reverted once already.

Tests: `test_scraper_images.CardRackIsNotARosterTest`,
`test_directory_roster.PageScopedRosterTest`.

## A scraped card vouches for a whole person

`_enrich_plan_profile_cards` is the **one** place that pairs an LLM-written
person with their source card, so it is where everything that card can vouch
for is handed over. It used to pass the portrait alone (it was
`_enrich_plan_profile_photos`), and the bio sitting on the matched
`ProfileCandidate` was dropped on the floor.

The damage is not that one field went missing — it is that **the missing field
then looked like a complete card**. `_ensure_scraped_team_blocks` decides
whether to replace a block wholesale with the scraped roster (which does carry
bios) by asking `any(member.photo_url)`, and enrichment had just put photos
there. So watr.org.my published four team cards with real faces, real names,
invented roles and **no biography**, while four 480-char bios sat one object
away. The tell was in the DOM: `alt="DR. WONG SUM KEONG portrait"` — the
scraper's own uppercase alt — on a title-cased LLM name. `_bind_slot` drops a
`$slot` node whose value is empty, so the card had no bio *node*, not an empty
one, which is why it read as "the template has no bio".

That photo test is a proxy for **"was this block enriched"**, never for "does
it have a photo". Keep the two in step: any field enrichment learns to fill,
it must fill *before* that branch runs, or the branch starts lying again.

Every field backfills **independently, and only into a gap** — the shape the
profile-page refill beside it already had, whose comment records why: *"gating
the contacts refill on a missing photo left it almost never firing."*

Two rules the backfill inherits rather than restates:

- **Grounding is skipped, on purpose.** A `ProfileCandidate` bio is page text by
  construction, so re-checking it against the page is guaranteed-true work — the
  same exemption `_roster_members` documents. `clean_team_bio` still runs (with
  the block's `other_names`, so one person's paragraph cannot bleed onto a
  colleague's card), so a scraped bio is cleaned identically on both paths.
  The LLM's own bio has *already* been through `_sanitize_team_block`'s verbatim
  haystack, which is why a surviving one is never overwritten.
- **`description` must be written with `bio`.** It is a deprecated alias kept in
  sync by `TeamMember.sync_description_aliases`, which only fires on
  **construction** — attribute assignment and `model_copy` both bypass it, and
  `_team_content` reads `bio or description`.

Tests: `test_directory_roster.ScrapedBioBackfillTest`.

## `BIO_MAX_LEN` is a safety rail, not an editorial length

`_extract_profile_bio` keeps every qualifying line inside a card's container, so
a mis-detected container — a whole column, a whole section — would otherwise
make the page's body text somebody's biography. That is the only job the cap
has.

At **480** it was doing a second job it was never meant to do: cutting real
biographies down. On watr.org.my three of four people lost **58-65%** of their
story, and the fourth — 316 characters — came through whole, which is what
pinned the cap as the cause rather than the renderer. It is 2400 now: ~4
paragraphs, comfortably past the longest measured real bio (1385).

Two properties, both of which were absent:

- **One home.** The bound was a bare `480` in `scraper._extract_profile_bio`
  *and* `_BIO_MAX_LEN` in `profile_text.clean_team_bio` — one rule, two
  spellings, so raising it in the obvious place would have changed nothing. It
  is `profile_text.BIO_MAX_LEN`, applied through `truncate_bio`, at both sites.
- **A cut lands on a boundary.** Both were bare character slices, so a bio ended
  mid-word — *"coaching and training both young medical and para-medical st"*.
  A card's **Show more** expands to the stored value, so a half-word is what the
  reader gets *after* asking for the rest: the page states a fragment and looks
  broken. `truncate_bio` cuts at a sentence end when one falls late enough to be
  worth taking, and between words otherwise (`…` only in that second case — a
  sentence end already reads as finished, and punctuating it would state a
  truncation the reader cannot see).

**Under the cap it returns the text unchanged**, which is the case that matters
most and the one every real bio hits: nothing is rewritten, so a short bio is
byte-identical to what the source published.

Nothing in the renderers crops a bio — no `maxHeight`, and the only
`overflow: hidden` on the node is the clamp's own, which expanding lifts. If a
bio ends early, the cap is where to look.

Tests: `test_profile_bio_length.py`,
`test_scraper_images.test_a_runaway_container_is_still_bounded_and_never_cut_mid_word`.

### Fitting a bio to a card — the model picks sentences, never writes them

`truncate_bio` is a bound, not an edit: it keeps whatever comes first, so a bio
opening with where someone grew up loses the qualification that made them worth
introducing. Choosing WHICH sentences a card keeps is editorial judgement, so
`services/bio_condense.py` asks — and **the model answers in sentence numbers**,
the same contract `paste_structure` runs on. Three properties follow:

1. **A condensed bio is a SUBSEQUENCE of the original**, so every word on the
   card is verbatim source text and passes `clean_team_bio`'s verbatim haystack
   and every other grounding gate **with no exemption anywhere**. Nothing
   downstream learns this pass exists.
2. **A credential cannot be invented.** This matters more here than anywhere
   else in the pipeline: a bio is a claim about a named, identifiable person's
   professional qualifications, published under their photograph. A free
   rewrite could turn *"practising obstetrician and gynaecologist, then a
   gynaecologic oncologist"* into *"senior cancer specialist"* — fluent,
   plausible, false, and libellous. An index cannot do that.
3. The reply is small however long the bios are, and **one call covers a whole
   block** — which is also what lets the model drop a fact two cards both state.

**The budget is arithmetic, so code enforces it.** Asked for ~400 characters
with no other help the model returned **1110, 1120 and 727**: it dropped the
hobbies and kept every professional sentence, which is the right ranking and the
wrong length. Two fixes, both needed — each sentence is rendered with its
character count so the model *can* budget, and `_apply` then trims trailing kept
sentences until the total fits. Same division as the rest of the pipeline: the
model supplies the judgement, deterministic code does the mapping. The first
kept sentence is never trimmed away; **which** sentence that is stays the
model's call, and it legitimately skips a scene-setting opener for the one that
states the qualification.

Two more rules, both of which the reply can get wrong:

- **Indices are re-sorted into source order, and de-duplicated.** A reply that
  reordered sentences would be rewriting the bio in the source's own words,
  which is the one thing indices exist to prevent. Note `join_bio_segments`
  orders *lines*, not sentences within one — so a cross-line pair looks correct
  even without the sort, and only a same-line pair tests it.
- **`split_bio_segments`/`join_bio_segments` are inverses**, and newlines are
  hard boundaries: `_extract_profile_bio` newline-joins a directory card's
  distinct facts and a page's paragraphs alike, and the card renders them
  `white-space: pre-line`.

Scope is **`team` blocks only**. A `ProfileBlock` is the page that is *about*
this person and keeps the complete biography — a grid introduces people, a
profile page tells the story. The two carry separate `bio` fields already, so
nothing is threaded and no member is condensed twice. `description` must be
written alongside `bio` (the `_team_content` alias trap again), and a bio
already at or under `team_bio_card_max_chars` never reaches the model at all —
on a directory of short cards this pass makes no calls whatsoever.

Every failure keeps the full bio: flag off, no client, `LlmError`, indices out
of range, an empty selection. A shortened card is an improvement; a missing one
is a regression. `conftest._offline_bio_condense` pins it off for the suite
(mirroring `_offline_paste_structure`), so the team and roster tests keep
asserting `truncate_bio`'s deterministic bound; `test_bio_condense.py` turns it
on and injects a fake client.

Knobs: `TEAM_BIO_CONDENSE_ENABLED` (default on), `TEAM_BIO_CARD_MAX_CHARS`
(400 — roughly what the 4-line clamp shows, so most cards read complete with no
"Show more" at all).

Tests: `test_bio_condense.py`.

## The line-clamp is the whole clamp declaration

A clamped text node (a team-member bio) is truncated inline and offered a
**Show more** by every renderer. That behaviour used to be declared **twice** —
a `wt-clamp` marker class beside the inline `WebkitLineClamp` — so one
behaviour had two sources of truth, and **the half an editor could reach was
not the half the renderers read**: the builder has no free-text `classes`
editor, so the line count could never be changed by anyone.

`getClampLines` reads the style and is the only signal. `wt-clamp` / `wt-clamp-4`
are gone from the catalog (`wt-clamp-4` never had a CSS rule at all).
**Already-published sites ship both**, so they keep their toggle through the
next `webtree-public` deploy with no regeneration — which is exactly why the
class check could be dropped rather than kept alongside.

Four properties declare a clamp and **move as one unit** (`clampStylePatch`): a
`WebkitLineClamp` without `display: -webkit-box` claims to clamp and doesn't.
Expanding writes `WebkitLineClamp: unset`, which the predicate reads back as
"no clamp" — so `unset`, `none` and anything unparseable must all mean null, or
the toggle re-offers itself on text that is already fully shown.

Three renderers, one predicate, none able to import the others':
`webtree-public/lib/blockRuntime.ts`, vendored to
`frontend/src/preview/lib/blockRuntime.ts`, mirrored as
`builder/src/lib/text-clamp.ts` — the `adaptiveInk.ts` idiom.

The builder canvas is the one that cannot just copy the renderer, for two
reasons invisible from the other two:

- **The element's styles land on the WRAPPER**
  (`editor-component-wrapper.tsx`), whose child is a block — and a
  `-webkit-box` wrapping a block does not clamp the way the published site
  does, where the styles sit on the text element itself. The canvas applies the
  clamp to the node that holds the words.
- **The text is `contentEditable`.** Clamping while it is being edited hides the
  copy the author is reaching for, so editing lifts the clamp.

The **Max lines** control lives in the Typography accordion and writes ordinary
inline styles through `updateElement`, the `applyFontFamily` pattern. **No
`BuilderElement` field is added**, so none of the six whitelist places needs an
edit. The canvas toggle is styled with Tailwind utilities rather than a fourth
copy of `.wt-clamp-toggle` — the three-way CSS duplication the adaptive-ink and
self-ink sections warn about.

Tests: `test_text_clamp.py` (cross-repo drift),
`test_section_content.test_no_section_declares_a_clamp_marker_class`;
`webtree-public/lib/blockRuntime.test.ts` and
`builder/src/lib/text-clamp.test.mjs` (`npm test`).

## Semantic HTML is a convention, not a guarantee

A visual page builder puts body copy in a bare `<div>` — Oxygen's
`ct-text-block`, Elementor's `elementor-text-editor`, Divi's `et_pb_text_inner`,
Webflow's rich text. `source_outline.is_text_block` is the **one** answer to
"which tags carry content", and it admits a `div` **only as a leaf**: an element
holding any `_NESTED_BOX_TAGS` is a layout box whose words belong to the boxes
inside it, and emitting it too would restate a page's entire text once per level
of its nesting. Measured: +0.5% on bbc.com and +1.5% on Wikipedia (semantic
markup, so nothing changes), +27% on mykiddyland — the copy that was missing.

`section_extraction` had **three** narrower spellings of that question and none
of them knew about `div`, so mykiddyland.com/about reached the planner as five
headings with `prose=""` under every one. Two consequences, which looked like
two bugs:

- **The page's content never arrived.** Vision, Mission, History, Goal and
  Trainers are `<div class="ct-text-block">`; `emit`'s collector read
  `("p","li","h2","h3","h4")`, so the section tree carried nothing but headings.
  `raw_text` still had the words, because `_extract_body_text` merges
  trafilatura in — but **appended**, so all six headings came first and every
  paragraph after, which is exactly the heading↔prose association
  `section_candidates` exists to preserve. The published page shipped a hero, a
  one-tile gallery, an invented "Our Journey" timeline and a CTA.
- **The ornament became the content.** `_image_only_cards` is gated on the
  section having "no words of its own" — asked in the same wrong vocabulary, so
  every section looked wordless, and the one thing left under each heading was
  the 33x33 star bullet. Five one-tile galleries, `card_kind="gallery"`,
  `_CARD_KIND_SECTIONS` → a `gallery` section, and "Our Vision" published as a
  picture grid of a star.

`_build_card` was the one place that already read `div` — which is why the cards
worked and the sections did not.

**A size the source published is evidence; ignoring it is not neutrality.**
Three rules, one home each:

1. **`sizes` is a declared width** (`image_urls.declared_display_width`).
   WordPress writes `sizes` on essentially every image it renders and leaves
   `width`/`height` off, so on the httpx fast path — where there is no render
   evidence and `classify_role` never runs — the attributes said nothing and the
   star entered the pool as an undeclared photograph, winning the About slot on
   its `context_heading`. Only an absolute px **fallback entry** is read: `100vw`
   and `calc()` describe a width that does not exist without a viewport.
2. **The undersized screen has one home**, `section_extraction.is_decorative_image`,
   asked by `scraper._extract_images` *and* `_build_card`. The pool screened the
   size and the card builder did not, so the same file was furniture in one pass
   and a photograph in the next. `size=`/`in_grid=` let the scraper substitute
   its **measured** geometry without either side re-stating the rule.
3. **A wall shows N different pictures; an ornament is ONE picture stamped N
   times.** The grid exemption exists so a partner/award wall keeps its ~150x60
   tiles, and without that distinction it rescued precisely what it excludes —
   five identically-built sections carrying the same star are a repeating image
   group by every structural test there is. `_shows_one_picture` is conservative
   in the direction that matters: a group whose files cannot be read keeps the
   exemption, because deleting a real badge wall costs the section everything.

The pixel screen (`image_graphics`) measures this star as a flat graphic
(43% clear / 3.2% distinct) and would have withdrawn it — but
`graphic_max_images` is **12 per generation**, ordered by intent alone, and
mykiddyland's pool is 200+. It is a last resort for sites that declare nothing,
not a substitute for reading what the markup does declare.

One more, found while verifying: `emit` asked "does a card already carry these
words?" by substring over the card's finished `body`, which is capped at
`_MAX_CARD_BODY_CHARS` and has its short lines split off into `meta` — so any
long card failed the test and its paragraph leaked back into the section's prose
whole. It reads the card's **span** by identity now, which is what the sibling
child-section subtraction two lines below already did.

Tests: `test_section_extraction.PageBuilderProseTest`,
`test_scraper_images.DeclaredDisplaySizeTest`/`RepeatedOrnamentIsNotAWallTest`,
`test_paste_source.LeafDivCopyTest`.

## The brand mark is not content

A site's own logo must never fill a photo slot. `image_evidence.classify_role`
measures the layout **box**, and its vocabulary has a `"logo"` value it can
never return: a wordmark rendered at 180x180 measures exactly like a square
photograph. So on the render path the logo entered the pool as `role="content"`
and won slots on merit — the About intent-pin (`image_match.PRIMARY_INTENTS`
skips the lexical gate), `media`'s **page-local size fallback** (the logo is
often the biggest file on the page, and on a splash page the *only* one), or an
LLM `image_ref`.

**Four gates already veto `role="logo"`** — `source_router._UNPROMPTABLE_ROLES`,
`image_match._EXCLUDED_ROLES`, the size fallback's own filter in `media.py`, and
`image_refs._unfit_for_kind`. Every one was correct; none ever saw one. The bug
was never a missing gate, it was a missing **producer**. Two now exist, and both
write the same field, so nothing downstream changed:

1. **Structural** (`logo_extraction.brand_mark_urls`, stamped in
   `scraper._parse_rendered_html`). `_find_real_mark` is a lazy generator
   (`_iter_real_marks`) and `extract_logo` takes `next(...)`, so the pool asks
   the **same predicate that decides what the header renders** — they cannot
   disagree. It returns the whole candidate set, not the winner: a header
   lockup and a differently-named white footer variant are two files and one
   brand. **`og:image` is excluded on purpose** — it is the last-resort tier and
   a palette source only, and on most sites it is a real photograph.
2. **Pixels** (`image_graphics`), for the sites that declare nothing.
   webtree.my's splash page is one `<img class="w-96" src="/webtree_greenwhite.png">`
   with no `<header>`, no `alt`, no home link and no "logo" in the src — every
   structural signal absent. Runs at **generation** time (so it fixes a cached
   scrape too), alongside the OCR screen.

**The pixel test is two measurements and needs BOTH.** Transparency says
"authored, not photographed"; flatness (distinct colours as a share of visible
pixels) is what separates a wordmark from a photograph someone cut out of its
background — and a product cutout is legitimate content:

| | clear | distinct |
|---|---|---|
| webtree wordmark | 62.5% | **2.4%** |
| photograph | 0% | 67.6% |
| photograph, cut out | 64.0% | 79.6% |

Both margins are ~10x, so the thresholds read a real gap rather than a sample.
**Transparency alone would withdraw the cutout** — that is the whole reason
flatness is there. It cannot reuse `image_vision`'s prefetched payloads:
`_downscale_to_b64` does `convert("RGB")` and re-encodes as JPEG, destroying
half the evidence.

**A grid cell is never screened, by either producer.** A partner/award wall IS
the section's content and its tiles are flat transparent graphics by
definition — the same exception `classify_role` and `_in_logo_wall` already
make. `_in_logo_wall` now guards `_iter_real_marks`'s header tier too, since
"partner-logo.png" trips every logo hint there is wherever it sits.

Where the slot ends up with nothing, `about` resolves stock: heroes were
already backfilled for a blank `image_query`
(`scaffold_enforcement._backfill_image_query`, generalized from the hero-only
version) and `about` now is too, sharing `_default_about_image_query` with
`_default_block` so an injected and a backfilled about can't drift apart.

Tests: `test_image_graphics.py`, `test_logo_extraction.BrandMarkUrlsTest`,
`test_scraper_images.LogoIsNotContentTest`. `conftest._offline_graphic_screen`
pins the pixel pass off for the suite (it downloads bytes), mirroring
`_offline_ocr`.

## An image whose message is in its pixels is shown whole

A poster, a flyer, a slide, a photo with its title burned in, a QR code. **Every
inline image slot in the catalog crops** (`objectFit: cover`) and every
background overprints, so such an image can fill none of them — and must still
be shown. watr.org.my shipped all four failures at once: its footer DuitNow
donation code stretched behind an About headline and cropped 4:3 into another
About split, an Alpha course poster as a 3:4 editorial hero and og:image, and a
volunteering slide as a split hero.

**Two questions, nested, in `image_match`**:

- `bears_text` — *any* readable words, so nothing may sit behind ours. Includes
  the vision pass's legible shopfront sign (`vision_has_text`).
- `must_show_whole` — the words ARE the content (`shows_words`: OCR's 6%
  coverage flag, `vision_kind == "banner"`, poster/flyer filenames), or a QR
  code. A shop sign loses nothing in a card; a poster does.

`hides_legible_content(meta, slot_usage)` is the one gate — `bears_text` for a
background, `must_show_whole` for everything else — asked by `rank_candidates`,
the resolver's pin path, its page-local size fallback, `strongest_source_background`
and `image_refs._unfit_for_kind`. **Inline slots used to be exempt** on the
reasoning that nothing is drawn over them; it forgot that they crop.

**The QR code is read, not guessed.** OCR measured watr's code at 3.6% — its
caption line only — and `image_graphics` needs transparency, so it passed as a
photograph. `text_detection.read_pixels` now decodes the frame once and returns
both readings (`PixelReading`); OpenCV comes with rapidocr and adds ~10ms. A
successful DECODE is the signal: detection alone produced corner points on a
building facade. Stamped as `ImageMetadata.qr_payload`.

**The caption is derived from the payload** (`qr_codes.purpose_of`), never
written by a model — a guessed purpose on a bank account is a false claim about
where a visitor's money goes. EMVCo merchant codes name their scheme (DuitNow,
PayNow, PromptPay, QRIS, Pix, VietQR) and their ISO 18245 category (8398/8661 →
"give", else "pay"); URLs name their host, with WhatsApp special-cased; `tel:`
and `mailto:` become buttons; anything else gets a generic caption and **no
button** — only http(s)/tel/mailto ever reach an `href`. EMVCo is recognised by
**structure** (opens with tag 00, parses to the mandatory CRC tag 63), not by the
Payload Format Indicator's value: the spec says `01`, watr's code says `02`.

**Placement** (`services/legible_images.py`, called after every pixel pass,
before image refs bind) replays, never narrates — `qr` and `poster` are
`DETERMINISTIC_SECTION_KINDS`:

- A code alone is an ask: one `qr` section after the page's contact section,
  else just before its closing CTA (`closing_insert_index`). Heading = the
  shared purpose title ("Give with DuitNow"), a lone card doesn't repeat it.
- Words are content: `poster` sections after the hero, grouped by the heading
  the source showed them under. A code INSIDE a poster (`_is_code` is false
  when `shows_words`) lends it a tap-through — a phone can't scan its own screen.
- Page = where the source showed it; on most of the site (`sitewide_across_slugs`,
  the video rule, now shared in `source_injection`) = homepage only. watr's
  code is on all six pages.
- **Readings are looked up in the pool by URL**: each crawled page carries its
  own unstamped `ImageMetadata` copies, and the pool de-duplicates by URL.
- Not placed: portraits, decorations, gallery cells (their rack replays them),
  brand marks (a logo-role image is placed only if it decodes as a code —
  `image_graphics` rightly calls a transparent code a flat graphic), player
  stills (`video_embed.is_player_thumbnail` — a YouTube thumbnail carries its
  title and reads exactly like a poster), and anything already on the page.

**Screening coverage.** The prefetch sample is what finds images to PLACE, so
`OCR_MAX_IMAGES` is 48 (a small site's whole pool; watr's is 46, screened in
18.3s in the container against a 60-270s content pass). Correctness never rests
on the cap: whatever wins a slot is verified on demand, and the photos an LLM
bound are verified in one batch (`verify_many(referenced_images(...))`) before
`bind_image_refs`, because a bound card photo never meets the resolver.

**Rendering.** `qr-cards` and `poster-cards` frame the image with `objectFit:
contain` plus the `h-auto` class — the one height all three renderers resolve to
the natural aspect ratio (webtree-public's safelist, the builder canvas's fluid
frame) — so an editor who later sets a height still can't crop it. The code sits
on a literal white mat: a scanner needs a light quiet zone whatever the band.
`poster-cards` carries `lightbox: true` for the fine print. A code is never a
page's og:image (`seo.scan_code_urls`); a poster may be.

Not covered, deliberately: galleries still crop their tiles (the lightbox shows
each whole), and `stock_images_only` strips a source's codes with its photos.

Tests: `test_legible_images.py`, `test_qr_codes.py`,
`test_text_detection.OnDemandVerificationTest`; builder `section-catalog.test.mjs`.

## Stock images only

A request-scoped `stock_images_only` on both generate endpoints dresses the
whole site in Pexels and uses none of the source's photography. **The mechanism
is a supply cut, not a filter**: `services/source_images.without_source_imagery`
returns a copy of the `SourceContent` with `images`, `image_metadata`,
`section_candidates[].image_urls` and `cards[].image_url` emptied, and each
handler rebinds `payload.source` to it once. Every consumer dries up at the same
time — the resolver pool, `_page_images_by_slug`, `promptable_images` (so the
model is never shown an image and cannot emit an `image_ref`), and
`_inject_image_walls`.

A flag on `ImageResolver` could not have done this: five paths write a source
URL into the tree without ever calling the resolver (`image_refs`,
`_wall_gallery_block`, `_build_team`/`_build_profile`/`_build_downloads`, and
`section_content`'s four `{"src": …}` values, which `template_filler` uses
verbatim). `ImageResolver(stock_only=)` exists anyway, as a brace for the one
hole an empty pool leaves open — `resolve(pinned_url=…)` looks the pin up in the
pool, and a **miss** yields `meta=None`, which every gate passes, so the pin
comes back as `source="scraped"` from a resolver that has never seen it.

Three things it deliberately does NOT do:

- **It never touches `profile_candidates[].photo_url`.** Both
  `_scraped_team_members` and `_directory_roster_members` skip a candidate with
  no photo (`if not profile.photo_url: continue`) — a photo-less candidate is
  not a photo-less member, it is *not a member*, so cutting portraits at the
  source empties the roster and `_drop_hollow_team_pages` then deletes the page.
  Portraits are cut one layer later, on the finished plan, by
  `generate._drop_person_photos`, which leaves names/roles/bios and lets all four
  renderers take their existing `monogram_avatar_url` branch. **Never a stock
  face under a real person's name**, in this mode least of all.
- **It keeps artifact imagery**: the brand logo (which never rides on
  `SourceContent` anyway), `document_cards[].image_url`, and the blog/event
  images `content_collections` re-fetches. Each depicts one specific thing; there
  is no stock substitute for "this PDF".
- **It hoists `_market_cues_for` above the strip.** Image URLs are domain
  evidence for `detect_market`, and the cues it returns are the only thing
  keeping stock queries on-market — they matter *more* here, so they are read
  from the source as received.

Galleries are the one rule the mode suspends, at both enforcement points
(`_drop_unbound_gallery_items` and `sanitize_blocks_against_source(
allow_stock_gallery=)`): with no source photography anywhere on the site,
"these are our photos" is not a claim the page is making, and dropping the block
would silently lose a page the user picked.

**`push_orchestrator` is provenance-blind** — by push time a photo is a bare
`src` string — so the rule must have fully taken effect before `plan_to_site`
returns. That boundary is what `test_stock_only.NoSourceUrlSurvivesTest` asserts,
walking the finished tree with the orchestrator's own `_collect_image_srcs`.

Tests: `test_stock_only.py`, `test_media.StockOnlyResolverTest`.

## Generated images are percent-encoded SVG data URIs

Every non-network image this repo makes is an inline SVG: `monogram_avatar_url`
and `_placeholder_photo` emit `data:image/svg+xml;utf8,…`, `icon_data_url` emits
`data:image/svg+xml,…`. **None of them is base64**, and `push_orchestrator`'s
`_decode_data_url` must keep handling both encodings — RFC 2397 allows any number
of `;parameter` segments and only `;base64` selects base64.

Its pattern used to be `data:([^;,]+)(;base64)?,`, which matched neither form: a
`;utf8` payload failed the match outright, and the icon form matched and then
died in `b64decode`. Either way the src became a `_ResolveSkip`, joined `failed`,
and `_strip_invalid_images` **deleted the element**. Every monogram avatar, every
section icon and every gradient placeholder silently vanished on publish while
rendering perfectly in the preview — which renders the pre-push tree, so the two
never disagreed anywhere you could see it.

Tests: `test_push_data_urls.py`.

## A URL is rewritten whole, or it is corrupted

`_apply_src_rewrites` swaps every collected source URL for the CDN URL the CMS
minted. An `image` node's `content.src` was always an exact-key lookup; the
`backgroundImage` / `background` branch was a `str.replace` **per rewrite key
over the raw CSS value**, which is order-dependent and wrong the moment one
collected URL is a **prefix of another**.

A WordPress webp-conversion plugin serves `photo.jpeg` and `photo.jpeg.webp`
side by side, so a crawl collects both and the push uploads both. Replacing the
shorter key first turned

    url('…/kindergarten-selangor.jpeg.webp')

into `<cdn>/1788541037kindergarten-selangor.jpg` **plus the orphaned `.webp`** —
a URL the CMS never minted (the coercer had already normalised `.jpeg`→`.jpg`),
and the longer key then had no text left to match, so its own correct CDN URL
never landed. Seven of mykiddyland's heroes published with a background that
404s, and **a browser paints a dead background as nothing at all**: no broken-
image icon, no fallback, just a blank band. That is why it reads as "the photo
was never added" rather than as an error.

It has a **second** harm that is worse than the first: `_strip_invalid_images`
runs *after* the rewrite and finds a dead reference by looking for the source
URL. The mangled value no longer contains it, so the one net that exists to
delete a broken photo is defeated by the corruption it is there to catch.

`_rewrite_bg_photo_urls` is the mirror of `_extract_bg_photo_urls` — same
`_split_css_layers`, same `_bg_layer_photo` match — so **a URL is rewritten
exactly when it was collected, and so exactly when it was uploaded**. Matching
the layer's URL whole makes a prefix collision unrepresentable rather than
merely unlikely, and it is one pass per value however large the rewrite map
(the old loop scanned the whole string once per key — 241 scans per style value
on this site). `_strip_invalid_images` already read layers this way; the rewrite
was the outlier.

Tests: `test_push_orchestrator.test_a_background_url_is_never_rewritten_by_prefix`
and its siblings, including one that asserts collection and rewriting agree on
what a photo layer is.

## Facebook Page ingest

A third source, routed **automatically**: `POST /api/scrape/start` calls
`source_detect.detect(url)` and dispatches a Facebook link to
`facebook_orchestrator.run_facebook_job` instead of `run_crawl_job`. There is no
new endpoint and no UI mode — "read a website" and "read a Facebook Page" are
one intent with different plumbing, so making the user classify their own link
would be making them learn the architecture. `frontend/src/lib/sourceDetect.ts`
mirrors the predicate for the inline badge/helper/button only; the backend
decides. **Detection must run before the robots check** — facebook.com/robots.txt
refuses unknown agents, so the URL would never reach the reader otherwise.

Fetch chain (`facebook_source.fetch_facebook_page`, injectable for tests):
Graph API when a token exists → **logged-out** Playwright render → the same
render signed in, only when a session exists. Graph fields are
requested in **tolerant groups**, because Graph fails the *whole* request when
one field is not permitted — a missing `pages_read_engagement` would otherwise
turn a missing `emails` into total failure. A denied optional group records
itself in `missing_fields` and sets `partial`; the render path is always
`partial`. A login wall raises 422 rather than returning a thin Page.

**`models/facebook.py::FacebookPage` is the anti-hallucination boundary** — a
fact that isn't a field there cannot reach the site. Three layers enforce it:

1. `facebook_source.build_raw_text` writes every retrieved fact, labelled and
   verbatim, into `SourceContent.raw_text`. That string is the haystack
   `scaffold_enforcement.is_grounded_in_source` matches claims against, so it
   defines exactly what the model may say. Absent fields emit **no label** — a
   bare `Phone:` line would let the model treat the label as its own grounding.
2. `homepage_sections_for` gates each section on its own evidence (no `gallery`
   under 4 photos, no `testimonials` without reviews, no `locations` without an
   address). A section that can't be grounded is never *requested*, so the model
   is never put in the position of padding one.
3. `facebook_authority.enforce_facebook_facts` runs **after** the LLM and after
   `align_page_to_scaffold`, rewriting contact/locations/testimonials/stats from
   the Page and **nulling what the Page never stated**. Same precedent as the
   profile refill in `routers/generate.py` ("the scraped card is the authority").
   It is the answer to a fluent invention that looks grounded enough to survive.

One landing page: `infer_page_scaffolds(..., single_page=True)` returns home +
legal only. The template fan-out would hand back ~5 pages the fidelity net then
strips to nothing.

Other contracts: `source_kind` Literal is mirrored in
`models/content_blocks.py` **and** `frontend/src/lib/types.ts` — change both.
The profile picture is the brand mark (`LogoCandidate(source="logo")` →
`_build_brand_candidate`); the **cover photo never is** (banner with baked-in
text), and a vision judge demotes a photographic avatar to `og-image`, for which
`is_renderable` returns False so the header falls back to the wordmark. Posts are
grounding text plus photos, never a rendered dated feed. Access tokens live in
`facebook_orchestrator._TOKENS` in memory, are popped on every exit path, and are
redacted from Graph errors — `options_json` records only `has_token`.

Category → `IndustryCategory` uses whole-word/stem matching, not substrings:
`"pub"` inside `"Public Figure"` filed a musician's Page as a restaurant.

**The Facebook modules import nothing from `scraper.py` or `doc_parser.py`, and
neither imports them.** What genuinely is shared lives in three neutral modules
that all readers depend on instead: `browser.py` (Chromium lifecycle +
`render_url`), `brand_candidate.py` (`LogoCandidate → BrandIdentity`) and
`source_preview.py` (`ImageCandidate` + `source_preview_payload`). Keep it that
way — each of those replaced a duplicate or a reach into a private name, and
`render_url` returns a **named** `RenderedPage` because the bare
`(final_url, html)` tuple it replaced was being unpacked backwards.

Tests: `test_source_detect.py`, `test_facebook_{urls,graph,render,source,logo,authority,orchestrator,single_page}.py`.
`conftest._offline_facebook` nulls the token, disables the render fallback and
points `browser_session_dir` at a scratch path, so the default chain is empty,
no test can reach Facebook, and none can read the developer's real session.

### Reading a Page without a token

**The tokenless path was dead for as long as it existed, and not for the reason
it looked like.** `is_login_wall` matched `login_form` / `loginform` /
`/login/?next=` as substrings of the whole HTML — and Facebook bundles its login
*dialog* into every page it serves, so those needles were present on the two
candidates that render perfectly. 100% of public reads raised
`LOGIN_WALL_MESSAGE`. The lesson is the one the section-catalog gates and
`locale._bounded` already teach: **a denylist written in the target's own
vocabulary is a silent off switch.**

The wall test is now what Facebook *tells* us: the **final URL** (`_WALL_PATH_RE`
— `/login`, `/checkpoint`, `/recover`), which is why `fetch_page` reads
`page.url` and not the URL it asked for. Two whole phrases still match, against
**visible text only**; the structural backstop is the `og:title` check that was
always there, since a real login screen carries none. `mbasic.*` is gone from
`_candidate_urls` for the same reason — Facebook retired it, so it 302s to
`/login` and cost a full render per read to discover that.

Three more rules the live DOM taught, all pinned in `test_facebook_render.py`:

- **`_visible_text` filters, it does not delete.** The obvious version
  (decompose the `<script>`s, then `get_text()`) also destroys the
  `application/ld+json` block — a `<script>`, and the most trustworthy source of
  address and phone on the page. Skipping the same nodes on the way out costs one
  ancestor walk and leaves the document intact. Stripping matters either way: a
  rendered Page is ~900KB of mostly inline script, which buried the label scan.
- **"About" is a navigation TAB, not a field label.** It appears three times in
  the chrome and never once as a label, so scanning forward from it landed on
  the next tab and `about` became **"Photos"** — a false fact crossing the
  anti-hallucination boundary into site copy. It is out of `_TEXT_LABELS`
  entirely; `og:description` is the real source and `parse_public_html` already
  falls back to it. `_CHROME_VALUES` guards the remaining labels.
- **The About panel labels a row on EITHER side of its value.** "Phone" above
  the number in one place; the email address above "Email address" in another.
  `_scan_labelled` runs forward then backward, the forward pass winning, so the
  second can only fill a gap. `_FIELD_SHAPE` gates email/phone **in the scan**
  rather than only downstream — otherwise a label above a section heading fills
  the slot and blocks the real value further down the page. NASA's
  `public-inquiries@hq.nasa.gov` was being reported as missing while sitting in
  plain sight.
- **A panel heading sits exactly where a value sits** — one line under the
  label — so nothing but `_CHROME_VALUES` separates them. Three were caught in
  the wild by reading real Pages: "Personal details" and "Details" became a
  Page's **category** (which steers `IndustryCategory`, and so the whole visual
  language), and "Basic info" became Vans's **website**. Adding them is the same
  bet `_TEXT_LABELS` makes — a closed set of *Facebook's* vocabulary, never a
  business one.
- **`website` was the one scanned field with no shape gate**, unlike email and
  phone right beside it. `to_source_content` turns it into
  `NavLink(label="Website", href=…)`, which lands in the footer of **every page
  of the site**, so a value that isn't dereferenceable ships a dead link
  sitewide: "Basic info" (chrome) and `tadika_murni_1988` (a handle — an
  underscore isn't legal in a hostname label) were both doing it. `_URL_RE`
  wants a scheme or a dotted host with a TLD-shaped tail.

### Signing in once

**A session is a rescue, not an upgrade — the logged-out render reads more.**
This section used to open with the opposite claim ("logged out, Facebook serves
og: tags and the tab strip; signed in, the About panel renders"), which is how
the feature came to *replace* the anonymous read rather than sit behind it.
Measured on three unrelated Pages, the signed-in render read **14-34 characters
against the anonymous read's 146-355**, and shipped a 422 ("doesn't have enough
public content") for a Page that built fine logged out:

| Page | logged out | signed in |
|---|---|---|
| a Malaysian tadika | 355 chars — name, About, phone, email, address, picture, 761 fans | 34 — name = URL slug, category "Personal details" |
| NASA | 332 — name, category, `public-inquiries@hq.nasa.gov`, website, 28.7M fans | 14 — name, category "Details" |
| Vans | 146 — name, category, About, picture, 19.4M fans | 14 — name, category "Details" |

Two mechanisms, both invisible from the code:

- **Facebook blanks the og: tags for an authenticated request** — they are
  served as `content=""`, not omitted. `parse_public_html` is built on
  `og:title`/`og:description`/`og:image`, so a signed-in read has no identity,
  no blurb and no profile picture, and `name` falls back to `ref.handle`: it
  titled a kindergarten `tadikamurni1988`.
- **The logged-out `/about` view is server-rendered; the signed-in one is the
  React shell.** The Page's own phone and email were absent from the signed-in
  HTML *entirely*, while the operator's **notification tray** ("You approved a
  login.") was in the visible text the label scan reads — the visitor's private
  chrome, one scan away from becoming site copy. NASA's
  `public-inquiries@hq.nasa.gov`, the fact this feature was built to find, comes
  from the anonymous read.

So `default_fetchers` puts the logged-out render first and appends the signed-in
one **only when a session exists** (a second anonymous render would cost a full
Playwright pass to learn nothing), and the chain's existing first-success-wins
loop does the rest — no merge, no new machinery. The session now answers the one
question only it can: a Page that refuses a logged-out visitor outright, which
is the case where the first render raises and the chain falls through. Same
precedent as `mbasic.*` leaving `_candidate_urls`: Facebook changed, and a path
that costs a render to learn nothing comes out.

`./dev.sh fb-login` opens a real Chromium **on the operator's
machine** — the backend runs in a container with no display and cannot — waits
for the `c_user`+`xs` cookies, and POSTs the Playwright storage state to
`POST /api/facebook/session`. `services/browser_session.py` holds it; a
**single optional kwarg** on `browser_context(storage_state=)`, the one place
Chromium is configured, carries it to the render.

- **Expiry is read from the cookies, never invented**, so `load()` refuses a
  dead session and the read degrades to an anonymous render — which works, it
  just sees less.
- **A session is the cookies that carry it.** `save` requires `c_user`+`xs`, so
  a window closed before the login finished fails at capture rather than looking
  like success and dying in a render weeks later.
- **`has_session` is part of the reuse key** (`scrape.job_options`, which the
  tests now drive directly instead of re-spelling). Without it the first read
  after signing in is served the anonymous result cached before it, and signing
  in looks like a no-op. Neither the token nor the session itself ever reaches
  `options_json`.
- **`DEPLOYMENT=hosted` refuses to start** with `FACEBOOK_SESSION_ENABLED=true`:
  one person's cookies replayed for every user is an account handover, and the
  endpoint that accepts them authenticates nobody. Tokens are the multi-user
  answer.
- `fetched_via` gains `render_session` — mirrored in `models/facebook.py`,
  `frontend/src/lib/types.ts` and `FacebookFactsPanel`, which uses it to offer
  the remedy that hasn't run yet.

The UI is `FacebookConnect.tsx` inside the token expander `SourcePanel` already
had. It owns all its own state, so `App.tsx` gains no props and no state.
Automating a personal Facebook account is against Facebook's terms — use a
secondary one. Two rules it states, both of which it got wrong first:

- **A spinner has to be backed by evidence.** The idle state rendered one over
  *"Waiting — this updates on its own."* — asserting a login was under way
  before the operator had run anything, with no window open and nothing pending.
  Nothing on the web side can observe the script, so the claim was
  indistinguishable from a hang, and the command sitting right under it never
  got run. Idle is an instruction now ("your turn"), and the login is **not
  narrated** here at all: the script prints its own progress to the terminal the
  operator just typed into, and a second copy of that story would be a channel
  with nothing behind it. That is also why there is no capture heartbeat.
- **Mounting is the poll gate, so the caller owns it.** `<details>` keeps
  collapsed children in the DOM, so "polls only while the expander is open" was
  never what the code did — typing a Facebook Page link started a 2s poll that
  ran behind a shut panel for the rest of the session. `SourcePanel` mirrors the
  disclosure's `open` into state (so DOM and mount can't disagree across the
  remount an URL edit causes) and renders the body only while it is open;
  unmounting costs nothing, since the token lives in `App` and the session is
  server-side. Polling is **derived** from the answer — unknown, or
  enabled-and-not-connected — rather than managed with a flag, which is what
  makes Disconnect resume watching for free; the imperative version cleared its
  flag and never restarted. A hidden tab skips the fetch and keeps the schedule.

`scripts/facebook_login.py` explains the two failures that produce **no window
at all** — Chromium never downloaded, a profile still locked by another run —
and does it around the `launch_persistent_context` call, not the whole capture:
Playwright's `TimeoutError` is one of its `Error`s, so a later `goto` timeout
caught at that width would be reported as a window that never opened when one is
on screen. An unreachable backend is explained too, since it lands at the worst
moment (already signed in) and the remedy is not obvious — the sign-in survives
in the persistent profile, so a re-run needs no typing.

Tests: `test_browser_session.py`, `test_facebook_render.py`.

## Pasted content

A fourth source, and the only one a user can add *on top of* another: the paste
box rides along with a link or a file (`App.landPreview` merges it into whatever
the reader returned) and is also a mode of its own, where it is the whole
source. `POST /api/paste/preview` serves both — the presence of `base` is the
only difference — so nothing on either side branches on "which kind of paste".

**It splits into pages through the document splitter, not a second one.** Both
flavours become a `ParsedDocument` and go to `doc_structure.split_into_pages`,
so a pasted `## Contact` opens a Contact page exactly like a Word heading does.
Nothing hand-builds a `SourceContent` — the old hidden "paste content directly"
box did, mislabelled it `source_kind="pdf"`, and skipped the preview and page
picker entirely; it is gone.

**The LLM works out the structure, because a paste has none to read.** Every
other reader has ground truth — a crawl has URLs, a PDF has font sizes, a DOCX
has heading styles, a Page has fields. A paste has line shape, and on a real
content brief line shape is *inverted*: the author's own page list (`1. HOME` …
`7. CONTACT`) is shaped exactly like a numbered bullet list, while the layout
labels between it (`Hero`, `Subhead`, `Services grid (four cards)`) are shaped
exactly like headings. That brief produced three junk pages named after
scaffolding and a site titled "Headline options". No refinement of line shape
fixes it; only meaning separates the two.

`services/paste_structure.py` asks. Three properties make it safe:

- **It answers in LINE NUMBERS, never text** — which lines open a page, which
  belong to it, which are notes to a copywriter (`skip_lines`). This module
  then slices the pasted lines. A structure call cannot invent a sentence, the
  reply is small however large the paste, and the copy reaching the planner is
  verbatim what the user typed. Same division of labour as everywhere else: the
  model supplies small semantic judgements, deterministic code does the mapping.
- **Its output is an ordinary `ParsedDocument`** plus `page_topics`, so
  `split_into_pages` still owns bucketing, image placement, slugs and the cap.
  `page_topics` answers the one question the splitter cannot: `classify_page_title`
  returns `None` for "AI AGENTS" and "DIGITAL TRUST & BLOCKCHAIN", so without it
  four of that brief's seven pages collapse into the homepage. Supplied topics
  also dedupe on **slug** instead of page type — several named pages
  legitimately share a type, and "one page per topic" would silently drop all
  but the first. Absent the parameter, every other caller is byte-identical.
- **Every failure is a fallback, never an error**: flag off, paste past
  `paste_structure_max_chars`, `LlmError`, any exception, or an outline whose
  ranges don't line up with the text (overlapping or past the end — *discarded,
  never patched*, since a wrong range moves someone's copy onto the wrong page
  invisibly). The heuristic reader then runs, and the preview says
  `structured_by: "heuristic"` so the degrade is visible.

This is the one preview that spends an LLM call. That is deliberate: for a
paste, structuring *is* the read. It is cached (`chat_json_cached`), and the
expensive per-page generation still sits behind the picker. `conftest` pins
`PASTE_LLM_STRUCTURE_ENABLED` **off** for the suite, so the paste tests keep
asserting the deterministic reader; `test_paste_structure.py` turns it on and
injects a fake client.

`services/source_outline.py` holds what that requires and the crawler already
had: the parsed-source shape (`ParsedDocument`, `OutlineBlock`, `DocImage`,
moved out of `doc_parser` so a paste doesn't depend on PyMuPDF for three
dataclasses) plus `read_html`, the **one** DOM walk that decides which tags
carry content. `scraper._extract_body_text` takes its recall spine from
`text_blocks` there, so "which tags are content, which are chrome" has one
answer for every reader; `read_html` additionally keeps heading levels and
anchors images between blocks, which is what the paste path needs and the flat
text spine threw away.

Rules worth keeping:

- **Explicit markers suppress the guess.** A paste carrying any `#` heading or
  setext underline is read *only* by its markers. The bare-line heuristic (short,
  unpunctuated, blank line above, something below) runs only for a paste with no
  markers at all — otherwise an author's short prose line silently fractures off
  its own page.
- **A number prefix is not a bullet.** `_SYMBOL_BULLET_RE` (a dash is only
  ever a list marker) disqualifies a heading outright; `_NUMBER_PREFIX_RE` is
  stripped and the heading test applied to what remains, because a number is
  also how people number *sections*. One union regex for both is what ate
  `1. HOME`. A numbered line that runs into prose ("1. You talk to the person
  building it.") still ends in a full stop and stays a list item.
- **Bare headings come in two tiers, ranked like a PDF's font sizes.** When a
  paste has both emphatic headings (numbered or CAPITALISED) and plain ones,
  the emphatic rank as level 1 and the plain as level 2, so only the emphatic
  open pages (`doc_parser._pdf_size_to_level` ranks a PDF's distinct heading
  sizes for the same reason). **Both ranks must be present** — a paste whose
  headings are all one style has stated no hierarchy and keeps the single level
  it always had.
- **A tag name sits flush against the `<`.** `looks_like_html` allowing
  whitespace made "Pricing: a < b and c > d" match as a `<b>` tag, and the whole
  paste was then parsed as markup, losing its line structure. Mirrored
  cosmetically (badge only) in `frontend/src/lib/sourcePaste.ts`, same
  arrangement as `sourceDetect.ts`.
- **A relative `src` is counted, never invented.** Pasted markup has no origin,
  so `<img src="/team.jpg">` cannot be resolved; it increments
  `unresolved_images` and the preview says so. A `<base href>` in the paste
  wins if present. An icon/tracking pixel is *filtered*, not unresolved — it
  must not inflate that count.
- **Merging joins by topic, and only a top-level page may answer.** Pasted
  Contact copy lands on `/contact`, not on `/services/emergency-contact` — which
  classifies as "contact" too. Unmatched pages are appended, which is how "paste
  the copy for a page the old site never had" works. The base source keeps its
  identity and everything only a reader could measure (nav links, section
  candidates, profile cards, embeds) rides through `model_copy` untouched, so a
  new `SourceContent` field needs no edit in the merge.
- **`image_candidates` in the response are the paste's own.** The reader that
  produced `base` built its candidates from a live layout, carrying evidence the
  source's metadata cannot reproduce; the frontend appends
  (`sourcePaste.withPastedContent`) rather than round-tripping them through a
  call that never saw them.

`source_kind` gains `"paste"` — mirrored in `models/content_blocks.py` and
`frontend/src/lib/types.ts` as ever. `isSinglePageSource` keys on
`discovered_pages` being empty for every non-URL kind now, so a paste that adds
pages to a Facebook Page gets them.

Tests: `test_paste_source.py`, `test_paste_structure.py`.

## Scroll-adaptive header ink (floating pill)

The `floating-pill` header is the one archetype with no solid phase to fall
back on (`SELF_CHROME_HEADERS`, `revealBackgroundOnScroll: false`), so a single
built-in ink is wrong for half the page: dark nav over a dark hero is invisible.
It instead recolours per section as the visitor scrolls. **Only the pill** —
every other archetype stays legible by solidifying, and is byte-identical to
before.

Three parts, each inert without the next:

1. **Bands.** `schema_builder._stamp_band_markers` appends `wt-band-light` /
   `wt-band-dark` to every top-level section's `classes`, once, after all
   styling passes and for **all** pages (legal pages skip the per-page loop, and
   `demote_self_chrome_header` can fire after it). `_band_class_for` reads the
   **final** styles — `SectionBandPlan.band` is `None` for non-participants and
   five later passes rewrite `backgroundColor`. Precedence: `headerOverlaySafe`
   (the scrim is the surface) → photo/opaque gradient → parsed
   `backgroundColor` composited over the page → page background.
2. **Wire format.** The pill's ink, glass tint and hairline ship as
   `var(--wt-pill-ink|tint|hairline, <built value>)` — catalog
   (`chrome-header-floating-pill`) plus `_logo_mark`'s typographic wordmark.
   **The built value stays in the fallback slot**, so a renderer that sets
   nothing paints exactly what it painted before; `header_footer.built_value` is
   the canonical reader (mirrored as `color-utils.unwrapCssVarFallback`). Not
   the image logo (a bitmap can't be recoloured — that's the `lockup` chip) and
   not the monogram circle.
3. **Gate + flip.** `menu_builder.wrap_header(adaptive_ink=…)` emits
   `behavior.adaptiveInk` only for `SELF_CHROME_HEADERS`; **renderers gate on
   that key alone and never see the archetype's name.** Each shell probes the
   band under the header's own midline on scroll and puts
   `wt-page-header--ink-light|dark` on the `<header>`, which only *defines* the
   three vars — no `!important`, unlike the older `wt-header-ink` overlay rule,
   because a custom property inherits into an inline style instead of fighting
   it.

Geometry is shared (`webtree-public/lib/adaptiveInk.ts`, vendored to
`frontend/src/preview/lib/` and mirrored as `builder/src/lib/adaptive-ink.ts`);
measuring is not, because each shell scrolls something different: `window`, the
iframe's `scrollRoot`, and the builder's scale-transformed
`builder-scroll-area`. On the canvas compare **rects**, never `scrollTop` —
header and sections share the transform, so it cancels.

The CSS var block is hand-duplicated in **three** places that must stay
identical: `PublicSiteShell.vue`'s `<style>`, `builder/src/index.css`, and the
generated `preview.css` (regenerate, don't hand-edit). An archetype swap in the
builder flips `adaptiveInk` with `overlay` in `headerBehaviorForArchetype` —
two halves of one decision. Pickers re-wrap on write
(`preserveCssVarWrapper`), or an edit to the header's text colour would
silently disable adaptation for that site forever.

## A control that paints its own surface states its own ink

`wt-header-ink` marks an element the **header** colours; `wt-self-ink` marks one
that colours **itself**, and is the single subtree the overlay's
`color: #ffffff !important` skips. Today that is one element: the mobile menu
pill.

The rule it enforces: **a rule that paints a surface states the ink in the same
place.** `.wt-ui-menu-button` declared `background: var(--builder-color-background,
…)` and no `color`, so three call sites each guessed one from their
surroundings — the header's overlay phase, the sheet's overlay variant, and a
hard-coded `'#ffffff'` reference surface that was true of nothing. On a dark
palette (`background` is HSL lightness 0.11) the pill went **near-black ink on a
near-black pill** the moment the header solidified on scroll; on a light palette
it was **white on white** at scroll position 0, the same bug with the phases
swapped. `.wt-ui-sheet`, two rules below it, had always stated both.

It now paints `--builder-color-text` on `--builder-color-background`. That pair
needs no measuring: every palette constructor in `theme.py` builds `text` with
`_ensure_contrast_against(background, ink, min_ratio=7.0)`, so it is **7:1 by
construction** — which is why ~310 lines of renderer-side colour maths could go,
including a `pickAccessibleTextColor` whose candidate pool
(`#0f172a`/`#1e293b`/`#334155`) was **all dark** and could not have returned a
light ink even given the right surface.

Three consequences worth keeping:

- **The pill is opaque, so it is not glass and must not read `--wt-pill-ink`.**
  The floating pill's *bar* adapts per band; a control with its own surface that
  inverted with the band would be wrong exactly when the band disagreed with the
  palette. Dropping that branch fixed a light-palette pill over a dark section.
- **The sheet close has no surface of its own** — `background: transparent;
  color: inherit` takes the sheet's pair, which is correct in both the palette
  and dark-gradient variants with one rule. It is `Teleport`ed outside the
  header, so it needs no marker.
- **`text-shadow: none` is load-bearing.** `.wt-page-header--overlay
  .wt-header-ink`'s shadow *inherits* into the pill.

The `:not(.wt-self-ink):not(.wt-self-ink *)` narrowing is hand-duplicated in the
same three places the adaptive-ink block is — `PublicSiteShell.vue`,
`builder/src/index.css`, generated `preview.css` — and the builder's canvas pill
mirrors the pair inline (it has no `wt-ui-*` layer). The builder was the one
place the bug was invisible: it painted the pill with a literal `bg-white`, so
its `'#ffffff'` assumption was locally true.

No backend, schema or catalog change — already-published sites are fixed by the
next `webtree-public` deploy, with no regeneration or re-push.

Tests: `test_self_ink.py` (drift over all three renderers, skipped when a sibling
repo is absent — same idiom as the `RENDERER_PINNED_*` mirror).

Tests: `test_floating_pill_heroes.BandMarkerTest`/`BandClassificationTest`,
`test_header_footer.FloatingPillAdaptiveInkTest`, `test_preview_layout.py`;
`webtree-public/lib/adaptiveInk.test.ts` (vitest);
`builder/src/lib/adaptive-ink.test.mjs` + `section-catalog.test.mjs`
(`npm test`).

## A pass that repaints a section states its ink too

Same rule as the section above, one layer up: `_apply_hero_washed_background`
repaints a split hero with an abstract photo under a heavy brand wash, and the
copy on it belongs to the same decision. The split templates hard-code dark ink
— `var(--builder-color-secondary)` headings, `rgba(15,23,42,…)` body, a ghost
CTA in both — which is right on the light page background they were authored
for and wrong the instant the wash lands: in a **dark scheme the wash IS the
theme's near-black secondary**, so the headline, the description and the
secondary button all shipped black on black at ~1.1:1. Only when a featured
photo resolved, which is what makes it look like an image bug — no photo, no
wash, and `enforce_text_contrast` fixes those exact inks on the flat band.

That pass could not help here, and correctly so: a real photo in the fill means
the surface is unknowable from the styles, so it hands the subtree back
untouched. **The caller knows it, though — it just painted it.** So the wash is
measured (`image_styling.washed_surface_hex`) and handed to the same pass as
`enforce_text_contrast(..., surface=…)`, rather than a second colour rule
growing beside it. `surface` lifts the photo bail-out **for the elements passed
in only** (a photo tile nested inside still owns its own ink) and suppresses the
node's own declared `backgroundColor`, which is a colour the wash covered.
`washed_surface_hex` and `washed_photo_background` read one `_wash_base`, so the
measured surface and the painted one cannot disagree; an unknown/unreadable
photo colour falls back to the base stop, which at 0.80 alpha cannot cross the
light/dark line anyway.

Three rules in the contrast pass this needed, each one a live defect:

- **Links count.** The pass was text-only, so a **ghost button** — a colour and
  no surface of its own — was a blind spot on every band, exactly the vanishing
  it exists to catch. A solid button was already safe: its own fill becomes the
  measured background, so its label is judged against the button. That needs
  `--builder-button-background|text` in `_token_hex`, which mirrors
  `to_builder_styles` and was missing them. A flipped label also takes its
  hairline with it (`_flipped_border` restates the colour and keeps the author's
  width/style/alpha), or the label is legible inside an invisible frame. A
  **computed** outline (`var()`, `color-mix()`) is never rewritten — the only
  hex in it is a fallback or a mix stop, and moving it changes what the
  expression means.
- **Only an OPAQUE fill hides what it sits on.** A translucent chip on a
  photo/gradient band (the gradient hero's `rgba(255,255,255,0.12)` ghost CTA)
  was clearing the "band owns this ink" flag and compositing itself over the
  last known flat colour — a surface that is not there. Its white label went
  near-black.
- **A fill is judged one LAYER at a time.** `cta-gradient` stacks a translucent
  sheen on an opaque brand ramp; read as one string, the sheen's `rgba(…,0)`
  stop made the whole fill look decorative, and the panel's white-on-brand
  headline and body were measured against the page background and flipped to
  near-black on every light-scheme site that used it.

No catalog, schema or renderer change: the fix is ink in the generated tree, so
the preview, the builder and the published site agree by construction.

Tests: `test_hero_policy.WashedHeroInkTest`/`WashedSurfaceTest`,
`test_ux_audit.TextContrastTest`.

## The compact header (below 1024px)

Where `MenuBlock` swaps the nav for the **Menu** button (`max-width: 1023.98px`),
the bar becomes exactly `logo | Menu`: the CTA is hidden and reappears inside the
sheet, and the mark comes down with the bar. On a **phone** the screen gutter
halves as well, 24px → 12px. Mobile + tablet in `responsiveRuntime` are
`≤767.98` and `768–1023.98`, i.e. that breakpoint exactly — one boundary, not
two that can drift.

**It lives in the renderer** (`webtree-public/lib/responsiveRuntime.ts`, mirrored
into `frontend/src/preview/lib/`), which already declares itself the owner:
*"Published payloads may only contain base styles with no responsive overrides.
These layout heuristics keep common header/footer compositions usable at real
narrow widths."* That is also what makes it reach sites published before it
existed. Explicit `responsiveStyles` still win — every rule is guarded by
`hasDeviceOverride` — so the schema stays the authority when it speaks.

**The rules were already there and had never once fired.** `isHeaderImage` and
`isHeaderLink` were `parentType === 'header'`, but every `chrome-header-*` wraps
the header's children in a **"Header bar"** container, so the parent is a
`container` on every generated site. `isHeaderMenu` survived only because
`slot === 'primary'` gave it a second signal — which is why the nav collapsed
correctly while the CTA and the logo did not. Same lesson as the section-catalog
gates, `is_login_wall` and `locale._bounded`: **a gate written in the wrong
vocabulary is a silent off switch.** They read `scope === 'header'` now, the
vocabulary `visitSchemaNodes` actually threads.

Two guards keep the widened link rule off the brand mark, which is a `link` when
the logo is typographic:

- **`isBrandMarkRoot` is `/^brand$/i`, exact.** `chrome-header-centered-stack`
  names its top row "Header brand row" and puts the CTA in it, so a loose
  `/brand/i` subtree test shields the very node the rule exists to hide. The
  image mark needs no entry — an image is never a link.
- **A link to the site root is never a CTA.** Belt and braces: leaving one extra
  link in the bar beats erasing the site's name from every narrow screen.

The mark's compact size reuses `getShrunkLogoStyles` — the scroll shrink's own
helper, mirrored with `builder/src/lib/header-shrink.ts` — at the scroll
shrink's own ratio (0.8), because "narrow" and "scrolled" are the same idea of a
compact header. Plus a **cap at 44px**, the Menu button's `min-height`: a ratio
alone leaves childcare's 68px mark at 54px, a banner beside the button. These
rules are emitted `!important`, so they beat the scroll shrink's inline style
and a narrow scrolled header is compact once, not twice.

**The gutter is the OUTERMOST padded layer, and only that.** Which node carries
it differs per archetype — the bar for `classic`, the root for `floating-pill`,
both rows for `centered-stack` — so it is found by walking (`headerGutterTaken`,
threaded like `inBrandMark`) rather than named. That is what keeps the pill's own
20px capsule padding intact while its 24px screen inset halves: an inset is not a
shape. Layout boxes only (the header root plus `HEADER_ROW_CONTAINER_TYPES`,
reused from `headerShrink`), never a button's own padding, and it only ever
reduces. Phones only: 24px each side spends 12% of a 390px viewport, and a tablet
has the width.

**`centered-stack` is the one archetype the per-node rules cannot fix**: its Menu
lives in a second row, under the logo rather than beside it. Its collapse is
authored in the catalog with the composition it belongs to — `responsiveStyles`
on "Header bar" (column → row, `space-between`) and its two rows (`width: auto`,
`margin-inline: 0`, `gap: 0`). The margins matter: `margin-inline: auto` centres
each row on desktop, and left in place it eats the free space `space-between`
needs, so the logo drifts off the gutter.

**The builder canvas does not run any of this** — it applies explicit
`responsiveStyles` only, so it gets the `centered-stack` collapse and not the
rest. Pre-existing and systemic (no `responsiveRuntime` port exists there), not
something this rule introduced.

Tests: `test_header_footer.CompactHeaderContractTest` pins the header *shape*
the renderer reads (one CTA link, the mark's exact container name, the mark's
single scaling axis); `webtree-public/lib/responsiveRuntime.test.ts` pins the
behaviour.

## Gallery lightbox

`BuilderElement.lightbox: true` on a **tile grid** makes its descendant images
click-to-enlarge on the published site, navigable as one set in DOM order. One
flag defines both the trigger surface and the navigation order, so the runtime
binds a single delegated listener per group. Authored in the catalog
(`gallery-grid`'s "Gallery Grid" node); the fallback `schema_builder._build_gallery`
sets it too.

It rides the same field whitelist as `classes`/`motion` — a new field must be
added to **four** places or it is silently dropped: `CatalogNode` + `baseFields`
in `builder/src/lib/section-catalog.ts`, and `_base_fields` in
`template_filler.py`, plus the `BuilderElement` type on both sides. **A new
gallery variant without the marker just isn't clickable** — nothing errors.

Renderers: `webtree-public/lib/lightbox.ts` + `components/public/GalleryLightbox.vue`
(wired via `useSchemaLightbox` in `PublicSiteShell`), mirrored in
`frontend/src/preview/`. Progressive enhancement only — SSR markup is untouched,
and a linked tile (gallery item pointing at a case-study page) keeps its link
instead of enlarging. Tests: `lib/lightbox.test.ts`, `test_gallery_lightbox.py`.

## Embedded frames (video + maps)

**`video` is the builder's only iframe primitive.** There is no `iframe`, `embed`
or `html` element type and no raw-HTML field — a YouTube player, a Vimeo player
and a Google Map are all `type: "video"` with different `content.src`. The
renderers accept any absolute URL through `parseVideoEmbed`'s `provider: 'other'`
branch, so nothing downstream needs teaching about a new embed host.

One DOM walk feeds both kinds: `scraper._extract_embeds` collects every
`<iframe>/<lite-youtube>/<embed>`, strips header/nav/footer chrome, drops hidden
trackers (`_is_hidden_embed`) and muted hero backdrops (`_is_backdrop_embed`),
then offers each src to `video_embed.parse_video_src` and `map_embed.parse_map_src`
in turn. **The whitelist is the feature** — most frames on a real page are Tag
Manager, reCAPTCHA, chat widgets and like-boxes. `_extract_videos`/`_extract_maps`
are thin wrappers over the one walk. Adding a third kind is a parser module plus
a branch, never another traversal.

Both feed source-injected blocks, so the LLM never authors either
(`DETERMINISTIC_SECTION_KINDS`): an embed id is an opaque external referent, and
an invented one is a stranger's video or the wrong street. The bookkeeping is
shared in `services/source_injection.py` — `accumulate_by_slug`,
`group_by_heading`, `hero_insert_index`/`companion_insert_index`.

Two rules differ between them, both deliberate:

- **Maps get no `repeated_across_slugs` chrome filter.** For a promo reel,
  appearing on every page proves it is furniture; for a map it proves the
  opposite — one address stated everywhere is one shopfront. The `<footer>`
  strip is the chrome rule for maps, and the markup is the right signal.
  Videos keep the filter but on a **proportional** threshold
  (`_VIDEO_CHROME_MIN_SLUGS`/`_MIN_SHARE` → `repeated_across_slugs(min_share=)`),
  never the flat two-slug rule photos use. Two slugs is the NORMAL count for a
  real video, twice over: a video index re-shows what its topic pages show, and
  the entry page is crawled under two slugs anyway (`/` → `""`, `/index.php` →
  `index`). At two, brightkids lost **all 15 of its videos** — the gallery
  shipped as hero + cta, indistinguishable from no extraction at all. A genuine
  sidebar reel is on most of the site, so a share test still catches it.
- **`_inject_maps` yields to an authored `locations` block.** `locations-map-cards`
  already synthesizes a map per branch from the address the model wrote
  (`section_content.maps_embed_url` — a *search*). A source-framed map is the
  more precise artefact (a `pb=` payload is an exact pin), but two maps of one
  place reads as a bug.

`map_embed.parse_map_src` passes through anything already frameable
(`/maps/embed?pb=…`, `/maps/embed/v1/…`, any `output=embed`) and rebuilds viewer
URLs (`/maps/place/…`) into the keyless `output=embed` form — framing a viewer
URL as-is gets a "refused to connect". **Commas are percent-encoded on the way
out**: the CMS runs every `src` through `MediaUrlResolver::normalize`, which
splits on top-level commas for multi-layer CSS backgrounds and drops any
fragment that isn't a URL.

`content.title` on a `video` node is the iframe's accessible name — without it
every map announces itself as "Embedded video". It rides the same whitelist as
`src`, so it is declared in **four** places: `_bind_slot` in `template_filler.py`,
`bindSlotContent` in `builder/src/lib/section-catalog.ts`, and
`BuilderElementContent` on both sides.

**Editing.** A new `sectionType` is invisible in the builder until it is in
`BodySectionType` **and** `bodySectionTypeOrder` (`body-section-templates.ts`) —
`insertableBodySectionDefinitions` filters on that set — plus `sectionTypeLabels`
and a `SectionThumbnail` case in `section-browser.tsx`. Because one element type
serves both embeds, the editor's nouns come from the URL, not the type:
`builder/src/lib/embed-kind.ts` classifies an embed and owns the copy for the
canvas placeholder, the settings accordion and the embed panel. It is a UI
affordance, **not** a parser — `video-embed.ts` stays the single canonicalizer
and a strict mirror of `webtree-public/lib/videoEmbed.ts`.

**Rendering needs no renderer change**, and must not get one: `parseVideoEmbed`'s
`provider: 'other'` branch frames any absolute URL. Tighten it to a player
whitelist and every map on every published site vanishes — `lib/videoEmbed.test.ts`
pins that. A renderer edit there ships through the repo's Cloudflare Workers
workflow on push to `master`; the local gate is `npm test`.

Tests: `test_video_embeds.py`, `test_map_embeds.py` (deliberately parallel — they
share a DOM walk and an injection spine); `builder/src/lib/{embed-kind,section-catalog}.test.mjs`
(`npm test`); `webtree-public/lib/videoEmbed.test.ts` (`npm test`).

## An embed states one sizing axis, and a lone card is a row

Two renderer defects met in the locations section, which is why it looked like
one bug. Both are fixed **upstream**, so already-published sites are repaired by
the next `webtree-public` deploy — no regeneration, no re-push.

**A node's styles never reach the frame that sizes the embed.**
`VideoBlock`'s inner `.wt-video-block__frame` carried an unconditional
`aspect-ratio: 16 / 9`; the node's own `styles` land on the **outer** box, so an
authored `height` was ignored and the frame sized itself from its width alone.
Wherever `width x 9/16` beat that height the frame overflowed downward and —
being `position: relative` — painted *over* the next sibling.
`locations-map-cards` was the only video node in the catalog with a fixed height
and no ratio, so it was the only one that could diverge: at 1280px its map
wanted ~307px against an authored 230px and **covered 77px of the branch
address**, and it did the same on every current iPhone (390–430px) while just
fitting at 375. That is why it read as "the address hides when I resize".
The fix is `height: 100%` on the frame, which makes the ratio a **fallback**:
a definite outer height wins, an `auto` one resolves to `auto` and the ratio
still drives. Same shape as `ImageBlock`'s `.wt-image { width: 100%; height:
100% }` — the inner element defers to the wrapper's authored box. It cannot live
in `responsiveRuntime`: `buildCssRule` emits one flat `[data-wt-node-id]`
selector per node, which can never reach `__frame`.
**The builder canvas had the mirror-image bug** — it renders one box, honoured
the height and ignored `aspectRatio`, falling back to a hard-coded 315px. Three
renderers, three answers. It reads `aspectRatio` now.
So: **a video node states a ratio OR a height, never both and never neither.**
Both is ambiguous; neither leaves the size to whichever default each renderer
happens to carry. Pinned from both sides — `test_renderer_video_sizing.py` and
`section-catalog.test.mjs` walk the catalog and fail on either.

**A lone item in the final row is the row, not a column.** `$gridFit` picks the
column count that best fits the item count, but no count divides every grid: 4
cards in a 3-wide grid, or any odd count in a 2-wide one, leaves the last item
packed into column 1 with the rest of the row void beside it — on desktop *and*
tablet. It reached the non-`$gridFit` grids too (`features-card-grid`,
`services-offer-grid`, `testimonials-quote-grid`, `features/services-two-col`,
`video-grid`, `map-grid`, and `schema_builder._build_gallery`), and `$gridFit`'s
own `n % 3 == 1 → 2Col` branch strands 7 all over again, since 7 is odd too.

The renderer had **two special cases** for this and no rule: a `:only-child`
rule (a grid holding exactly one item) and a tablet-only `three-col` orphan
rule. They are the same sentence written twice, for the two counts someone
happened to hit. One rule replaces both, stated **per breakpoint**, because
"alone in its row" is only answerable against that breakpoint's own column
count — the last item is alone iff its 1-based index is congruent to 1 modulo
the columns, which makes a one-item grid the `n = 0` case rather than a rule of
its own:

```css
.wt-container-block--two-col   > :last-child:nth-child(odd)      /* 2 cols */
.wt-container-block--three-col > :last-child:nth-child(3n + 1)   /* >=1024px */
.wt-container-block--three-col > :last-child:nth-child(odd)      /* 768-1023.98px */
```

**The 3-column test must stay fenced above 1024px**: a three-col grid is *two*
columns wide at tablet, so `3n + 1` would widen a 4th card that is not alone
there. A partial row holding **two** items is deliberately left alone — it reads
as balanced, and widening one of them would unbalance it.

**`$gridFit` itself is still deliberately not touched**: one fix in one place,
and the renderer-side one also reaches sites already published, with no
regeneration and no re-push.

The builder canvas cannot use a media query — it simulates a device by state,
not by viewport width — so `builder/src/lib/column-layout.ts` is the hand-written
mirror, and it owns the column table the canvas *already* needed to draw the
grid (`currentColumnCount`), so there is one answer per device rather than two.

**`$bento` needs its own answer**: its grid is an inline `display: grid` on a
plain container, so no `.wt-container-block--*` class ever reaches it. Its lead
tile is 4 of 6 columns and 2 rows tall, so the two tiles after it fill the
gutter and everything from index 3 lays out 3 to a row; at the tablet grid's 4
columns the lead is already full width and every later tile pairs 2 to a row.
Either way the tail is a plain division and its last tile is alone exactly when
that division leaves a remainder of 1 — `1 / -1` then, which is the whole row at
every column count. **The widths strand different counts** (6 columns: 4, 7, 10;
4 columns: every even count), which is why `_bento_placement` returns
per-breakpoint layers as well as base styles, and emits one only where that
width disagrees with the base. The old "every fifth tile is wide" accent is
gone: it needs 4 free columns at a position where 2 remain, which is what
punched the mid-grid holes that `dense` auto-flow only accidentally repaired at
one count.

**A span WIDER than its grid creates implicit columns**, and those steal space
from the explicit `1fr` tracks. The 4-wide lead sat on a **2-column** mobile
grid, so every bento ever published rendered its phone layout as alternating
382px tiles and **8px slivers** — measured in Chromium, and invisible from the
Python because a span is only wrong relative to a column count that lives in the
catalog's `responsiveStyles`. Beyond the column count a tile is simply the row,
so `_bento_tile_span` says `1 / -1` there. Note `1 / -1` counts **explicit** grid
lines, so it cannot rescue a row implicit columns have already widened — the
clamp has to prevent them, not compensate for them.

Tests: `test_grid_orphan.py` (drift over all three renderers, plus an
independent grid-packing simulation asserting that no bento row ever holds one
tile beside a void); `builder/src/lib/column-layout.test.mjs` and
`section-catalog.test.mjs` (`npm test`).

### `$cycleStyles` — index-aware styling on a `$repeat`

`[{}, {"flexDirection": "row-reverse"}]` on a repeat node stamps
`patches[i % len]` onto child *i*. The general form of what `_bento_placement`
already does for one layout, and applied at **fill** time in both engines
(`template_filler._apply_cycle_styles` ↔ `section-catalog.ts applyCycleStyles`,
line-for-line mirrors like `bentoPlacement`/`_bento_placement`), so the three renderers
cannot disagree about which way a row faces. The alternative — a `classes`
marker plus a `:nth-child(even)` rule — would hand-duplicate CSS across
`PublicSiteShell.vue`, `builder/src/index.css` and generated `preview.css`, the
same three-way duplication the adaptive-ink and self-ink sections warn about.
**It patches BASE styles only**, so the item's own
`responsiveStyles.mobile.flexDirection: "column"` still wins at its breakpoint —
which is what lets a reversed row still stack map-first on a phone.

### `locations-map-split`

The default locations layout (`_PREFERENCE["locations"]`), added **alongside**
`locations-map-cards`, which stays in the catalog and stays reachable via
`explicit_id`, the design brain's `selectable_templates`, and the builder's
section browser. A flex column of full-width rows, each a map/details split with
the map alternating sides.

It removes the *class* of defect rather than one instance: it is not a grid, so
no count can strand a card in a column; its map is sized by ratio, so it cannot
outgrow its box; and it has no card, so there is no `overflow: hidden` left to
clip copy with. Measured: 620/460 at desktop, an even 338/338 at tablet (a
`responsiveStyles.tablet` `flex` reset, since 1.35:1 is too tight there), and
stacked map-above-details on a phone.

**Its slot list is byte-identical to the card grid's**, which is what keeps the
generator side to one preference function — `_locations_content`, `is_feasible`
and `facebook_authority._locations`' single-item rewrite are all untouched.

**Its copy states its ink in tokens, and must.** The card could hard-code
`rgba(15,23,42,0.72)` only because it painted its own white fill underneath.
With the card gone the copy sits straight on the section band, which the
luminance pass may resolve **dark** — a literal near-black address would ship
black on black. `var(--builder-color-secondary)` / `var(--builder-color-text)`
are what let `enforce_text_contrast` measure and flip them. Same rule as
"a pass that repaints a section states its ink too", one layer down.

Tests: `test_locations_split.py`, `test_renderer_video_sizing.py` (cross-repo
drift, skipped when a sibling repo is absent — the `test_self_ink.py` idiom);
`builder/src/lib/section-catalog.test.mjs` (`npm test`).

## Article and event listings

The `articlesList` / `eventsList` element (`_cms_list_element` here, the
builder's own seed and palette item) is rendered by `CmsListBlock.vue` in
webtree-public, a hand-written copy in the builder canvas (`cms-list.tsx`), and a
heading-only port in the preview. Three rules, each paid for on a real site:

- **The list is the section.** It pads itself to the site's column at every
  breakpoint (80 → 32 → 20px), so nothing may wrap it in a padded container.
  The builder's listing template once did: 100px a side on a phone (175px
  cards), 112px on a tablet, a 960px column where the site's is 1280. It seeds
  the list alone now, and webtree-cms-api's
  `unwrap_seeded_article_listing_template_lists` migration unwrapped every
  stored draft and revision still exactly as seeded.
- **It lays out by its own width, never the window's.** The builder canvas
  simulates a device by width, not by viewport, so a media query there answers
  for the wrong device — its Mobile preview drew the desktop grid. Columns are a
  self-sizing grid (`repeat(auto-fill, …)`: cards ≥16rem, capped at 3, or 4 once
  there are four), which also stopped a 1024px tablet getting four 201px cards;
  card direction, type steps and compact pagination ("Previous [5] Next" under
  30rem) are container queries on `wt-cms-list` / `wt-cms-column`. The canvas
  uses the grid declaration verbatim (`cms-list-layout.ts`) and the same
  container widths as Tailwind `@min-[…]/wt-cms-*` variants. Only the section's
  own gutter stays on the viewport, where every other section's is.
- **Headings ink with `--builder-color-heading`, never raw `secondary`.** On a
  dark palette `secondary` is the darkest band colour — Feruni's card titles
  measured **1.04:1**. The token is `secondary` where it reads (4.5:1) and
  `text` where it doesn't, which is this repo's own `ink = text if dark else
  secondary` (`style_tokens`) answered from the colours, because
  `color_scheme` never reaches the wire. **Derived, never stored** — like
  `primaryInk` in the builder, it follows a live palette edit — in
  `webtree-public/lib/styles.ts` (vendored verbatim to the preview) and
  `builder/src/lib/builder-styles.ts`. The same pass moved the category pill to
  `--builder-color-primary-ink` (4.0 → 14.7:1 dark) and the current page number
  to the button pair, and `--wt-color-muted` — which no palette states, so it
  was always light-theme `#6b7280` (4.09:1 dark) under archive descriptions,
  form labels and blockquotes — is now held to 4.5:1 against the background
  (`ensureContrast`, the `primaryInk` idiom): byte-identical on light palettes.

`eventListing` is a template type in all four repos: the public `/events` route
renders from it and 404s without one, so the push creates it whenever the site
has events (`_TEMPLATE_PAGE_DEFAULTS`), as the builder's `ensureTemplatePages`
does.

Tests: `test_cms_list_layout.py` (cross-repo drift: grid formula, container
questions, preview vendoring, heading ink), `test_content_push.py`;
`webtree-public/lib/styles.test.ts`; `builder/src/lib/{builder-styles,cms-detail-templates,page-management}.test.ts`;
`webtree-cms-api` `ArticleListingUnwrapMigrationTest`.

## SEO

`services/seo.py` owns the **data**; the CMS renderer owns injection (it wraps
the JSON-LD in `<script type="application/ld+json">` in `<head>`). Don't emit
markup here.

- Homepage → `Organization` + `WebSite`. Restaurant / childcare /
  professional-services (`_LOCAL_INDUSTRIES`) also get `LocalBusiness`.
  Subpages with a chain → `BreadcrumbList`. An FAQ block → `FAQPage`.
- `extract_og_image` walks hero background → split-hero image → first image, and
  skips data URIs.
- **Exactly one `h1` per page.** Legal pages build theirs via `legal_pages._h1`
  (sets `htmlTag="h1"`); everywhere else `schema_builder.apply_heading_levels`
  stamps the page's first section title `h1` and every later one `h2`, over the
  assembled page so catalog templates and programmatic trees are covered alike.
  Text nodes render as `<div>` otherwise, so an untagged title is invisible to a
  crawler.
  **It finds that title by NODE NAME** (`_TITLE_NAMES`), which is a vocabulary
  and therefore a silent off switch when a section names its title something
  else: `profile-*` ("Name") and `clients-logo-strip` ("Strip Heading") were
  missing, so a person's page shipped its CTA slogan as the `h1` and the
  person's name as a `<div>`. `test_seo.HeadingVocabularyTest` now walks the
  catalog and fails on any body section whose first match isn't the node the
  section declares as its title (`$slot` heading/headline/title/name, outside a
  `$repeat`) — `testimonials-editorial` is the one allowlisted section with no
  title at all.
- **A split headline is ONE heading made of two text nodes**, so the tag goes on
  the group that holds them (`_TITLE_GROUP_NAMES`, derived from `_TITLE_NAMES`)
  and the whole title sits inside the `<h1>`. Tagging the lead line alone
  published a truncated heading — `"Bread baked"` for "Bread baked the slow
  way" — which is the string a crawler reads as the page's subject; inline
  markup inside one node is not the alternative, since the builder editor
  rewrites content to `{innerText}` on blur and would drop it. The lines become
  `<span>`s (a heading takes phrasing content) and each states `display: block`:
  the catalog path's group stacks plain blocks, its `flexDirection`/`gap` inert
  without a `display: flex`, so inline spans would run the two lines together.
  `_HEADING_BOX_RESET` states the box's own `margin`/`fontSize`/`fontWeight`, or
  the UA sheet's `h1` metrics would move a layout that only changed semantics.
  Renderer side, `blockRuntime.getHeadingTag` lets a **container** present
  itself as h1-h6 (`ContainerBlock` in `webtree-public` + the preview mirror);
  anything else keeps the structural `section`/`div`.
- **The builder re-levels as the user edits** — `builder/src/lib/heading-levels.ts`
  is a hand-written mirror of the pass, run from `addToHistory` (the one seam
  every body mutation passes through) and from `LOAD_DATA`. It has to exist
  because a section inserted there comes from the catalog, which declares no
  `htmlTag` in either `baseFields` — so swapping the hero in the editor used to
  drop the page's `<h1>` and duplicating it shipped two. `HeadingMirrorTest`
  fails when the two vocabularies drift (skipped when the sibling repo is
  absent); `heading-levels.test.mjs` covers the behaviour (`npm test` — the
  builder had no runner at all until this landed; vitest now runs all 16 of its
  test files, and the one that was silently red is fixed).
  The builder canvas renders the tag too, now that it means something —
  Tailwind's preflight makes a heading box visually identical to the div it
  replaces, so that is semantics only.
  Zero or multiple is an audit failure — read off `htmlTag`. The check used to
  count elements *named* `"H1"`, which nothing emits, so it reported "no H1
  found" on every page ever generated and could not have caught a real miss.
- Titles must be unique across the site and within the length bounds in
  `ux_audit.py` — duplicate titles/descriptions and missing `ogImage` are flagged
  there. `detect_duplicate_seo` / `detect_orphan_pages` back it.
- **The audit is advisory: it logs and never blocks generation.** If you make it
  blocking you will fail real sites — raise the generator's output quality
  instead.
- `push_orchestrator.py` honours `page.seo.noindex` at push time.

Tests: `test_seo.py`, `test_ux_audit.py`.

## The site favicon

The icon a browser tab, a bookmark and a **Google search result** show beside the
site's name. One column owns it end to end — `entities.entity_favicon` in
cms-api — and `Entity::faviconUrl()` is its one resolver.

**It was three readers disagreeing about one column.** The admin app rebuilt a
legacy `assets/favicons/{first2}/{name}` path (in **five** copy-pasted places),
`MediaUrlResolver::normalize()` returned **null** for that exact shape so the
live site rendered no icon at all, and a second column `seo_favicon_url` was the
only writable one and was never read by `buildSiteDefaults()`. A site could show
its icon in the dashboard and none in a browser tab, and saving one through the
SEO-defaults API was a silent no-op. `seo_favicon_url` is backfilled and dropped;
`faviconUrl` in the SEO-defaults response is now read-only and derived.

`MediaUrlResolver::entityFavicon()` handles the four shapes the column holds —
legacy bare filename, storage key, rooted path, absolute URL — and is built
against `config('app.url')`, **never `url()`**: the admin host writes this value
and the public host serves it, so a request-relative URL is right in exactly one
of the two places it is read. A test pins that.

Generator side, three rules:

- **Extraction is a URL, not bytes.** `logo_extraction.find_favicon` wraps the
  existing `_find_icon` walk (largest declared `sizes`, `mask-icon` skipped).
  `extract_logo` only reaches its icon tier when there is *no* real mark, so on
  any site with a header logo the icon links were parsed and thrown away —
  a favicon is a different question with a different answer. Nothing is fetched
  at scrape time; the frontend renders the URL directly, which doubles as a
  liveness check, and the push fetches once when it actually needs the bytes.
- **The fallback is a model rule, stated once.** `BrandIdentity`'s
  `_default_favicon_to_the_mark` validator fills an unset `favicon_url` from the
  mark, so every constructor gets it — the crawler, the Facebook reader, the
  manual logo upload — and a source with no markup to declare an icon (a PDF, a
  DOCX, a Page) still ships one. Gated on `logo_render_ok`, **reusing** that
  verdict rather than inventing a second: a mark that fails it is an og:image,
  and a 1200x630 social card in a 16px tab is an illegible smear.
- **The push does not use `/api/file/add`.** `POST /api/entities/{token}/favicon`
  instead — a favicon is site chrome, not a media-library asset, and the CMS
  re-encodes it to a **192×192 PNG** (192 because Google only shows a favicon
  that is square and a multiple of 48px; `contain`, not `cover`, because
  centre-cropping a wordmark eats its own letters). `_coerce_to_cms_image`
  already transcodes the `.ico` a `rel="icon"` usually points at. The step is
  **non-fatal** and gated on `push_favicon`, the same restraint
  `push_builder_styles` has: re-pushing must not replace an icon the owner chose.

**`GeneratedSite.brand` is typed `Any`**, so it is a `BrandIdentity` in-process
and a plain **dict** once the frontend posts the site back to `/api/cms/push` —
which is every real push. `_brand_field` reads either, and a bare `getattr`
returns None for the dict form. `_upload_media`'s brand-logo block **had** that
bug; it was fixed in `65e1bc5` and reads `_brand_field` now. (It left one
artefact: `_brand_field` is defined **twice**, byte-identically, at ~690 and
~1050. Python keeps the last, so the first is dead code.)

`webtree-public` needed **no change**:
`usePublicSeo.ts` already emits `<link rel="icon">` from `entity.favicon`, and
Google reads a site's favicon from its home page — a `PublicSitePage`, the one
surface that calls it.

Admin side, `SeoPreview.vue` is the **only** Google/SERP mock in the suite; the
tags and categories forms each had a hand-rolled copy, now replaced. Its
`modes` prop lets the site-icon editor reuse it rather than grow a second one.

Tests: `test_favicon.py` here; `EntityFaviconEndpointTest`,
`EntityFaviconUrlTest` in cms-api.

## Pushing to a live CMS

The destination is chosen **per push**, not per process: `services/cms_targets.py`
resolves a name (`"default"` / `"remote"`) to a `CmsTarget`, and `CmsClient`
already took `base_url` as a constructor arg, so `for_target()` is the whole
seam. The default target is derived from `cms_api_base_url` / `admin_app_base_url`
— still the one home for "where the local CMS is", and still what compose
rewrites for container networking. `CMS_REMOTE_API_BASE_URL` adds the second;
unset, there is one target and the publish drawer is byte-identical to before,
the same restraint `ADMIN_APP_BASE_URL` already uses.

Four rules, each of which was a live bug or a real trap:

- **The wire carries a NAME, never a URL.** `POST /api/cms/push` is
  unauthenticated (SECURITY.md) and forwards the operator's CMS credentials; a
  caller-supplied base URL would make it a credential-forwarding proxy to any
  host. It is also what keeps `url_guard`'s "fixed, known hosts … don't route
  through this guard" true. Same reason there is no `CMS_PASSWORD` setting:
  credentials are typed per push and never stored.
- **Label and remoteness are DERIVED**, never configured — `label` is the API
  `host:port` and `is_remote` is "not this machine". A hostname on the push
  button reports where bytes actually go; a label typed once into `.env` goes
  stale in silence. `.env` currently says `CMS_API_BASE_URL=http://host.docker.internal`
  with no port, so even `"Local"` would already be a half-truth.
- **The already-hosted check reads the TARGET, not settings.** `_is_cms_hosted`
  is the extracted half of `_needs_upload` / `_needs_upload_document`; a
  settings-derived host is wrong for every push that doesn't go to the default
  CMS. `push_orchestrator` imports no `settings` at all now — that is the proof.
  Its `media_host` sibling is deliberately absent: `upload_media` returns the
  CDN URL the CMS minted, so nothing ever constructs one, and the `/storage/` +
  `/api/image/` path arm is already host-agnostic.
- **`_admin_url()` takes the target.** Reading the global setting meant a
  successful production push handed back a `localhost:5000` deep link —
  plausible, silent, and pointing at an entity that isn't there.

Two remote-only failure modes are handled and should stay that way: a redirected
`login` is refused with a descriptive error rather than followed (httpx turns a
301 on POST into a GET, half-applying a push) — one check on login is enough,
since a server that redirects `/api/auth/login` redirects everything; and a
duplicate `entity_url` retries **once** without it and records a `PushStep.warning`,
because that column is `nullable` but `unique` across a whole CMS, so on a shared
production one a site's own address may already belong to another tenant. Detect
it on the `errors` **key** — Laravel's message text is translatable.

Not needed, and adding them would be debt: no 429/back-off logic (no admin route
is throttled) and no CORS work (the browser talks to the *local* backend; the
backend talks to the CMS server-to-server with no `Origin`). No compose edit
either — the whole root `.env` already flows through `env_file`, and a remote
origin must *not* get the `host.docker.internal` rewrite.

Tests: `test_cms_targets.py`, `test_push_orchestrator.py`.

## The local↔production boundary

`app/deployment/` holds **everything that exists solely to answer "is this safe
to run somewhere other than a laptop"** — and nothing else. The rule that keeps
it a boundary you can read: *if a thing has a job besides that, it stays in the
module that does that job.* So the twelve per-process caches did **not** move —
a response cache is part of how `llm.py` works, not part of how this app is
deployed. Only the *list* of them lives here.

Three files, mutually isolated from the pipeline (a test enforces both
directions — `app/deployment/` imports only `app.config`, and only `main.py`
imports `app.deployment`):

- **`profile.py`** — WHERE this process runs. Not where a push lands: that is a
  `CmsTarget` (`services/cms_targets.py`), and merging them would mean either a
  local tool refusing to publish or a hosted one trusting its own machine.
- **`guards.py`** — startup checks, run first in the lifespan. **Errors refuse to
  start** and cover only what exposes you: no auth, SSRF guard off, a CORS list
  still naming localhost or set to `*`. **Warnings log and continue** for what is
  merely unwise: single-process caches, SQLite, an LLM URL pointing at this box.
  The split matters — blocking on the operational ones only teaches people to
  route around the guard. There is **no override flag**: an escape hatch for
  "serve this unauthenticated on the internet" is the footgun the file prevents.
  `DEPLOYMENT_AUTH=proxy` is the sanctioned answer, and it is an attestation,
  not a mechanism.
- **`process_local.py`** — the inventory. Two severities, and only `correctness`
  blocks: `facebook_orchestrator._TOKENS` (a job's token is unreachable from
  another worker, and it is deliberately never persisted) and `polite._registry`
  (per-host rate limiting enforced per process ⇒ N workers crawl N× faster than
  robots.txt allows). The other ten are cache misses — slower, not wrong.

**The inventory cannot go stale**, which is the usual fate of such a list.
`test_deployment_boundary.py` walks the AST of every module under `app/` and
fails on anything classified in neither table. The discriminator is
industry-neutral and exact: **a cache starts empty, a lookup table starts full** —
an empty dict/list/set literal at module scope is runtime state, a populated one
is data, and `@lru_cache` is always process-local. Same drift-test idiom as the
section catalog and `RENDERER_PINNED_GAP_NAMES`. `NOT_PROCESS_LOCAL` records the
four false positives that rule produces (one deliberately-empty lookup table,
three pure memos), each with a reason, so the escape list can't wave things
through.

`DEPLOYMENT=local` is the default and every check is inert under it, so this is a
true no-op for the tool as it runs today — the same property
`DESIGN_SCHEMES_ENABLED=false` has. Adding auth is deliberately **not** done
here: the auth model is guesswork until the deployment is real, and the guard
makes forgetting it impossible rather than papering over it.

Tests: `test_deployment_boundary.py`.

## Same site, one spelling

`services/site_url.py` is the only answer to "is this the same site" (`www.` is
an alias) and "is this the same page" (`crawl_key`: alias, trailing slash and
fragment insensitive). The crawler compared hosts exactly while
`nav_extraction` ignored `www.`, so feruni.com's header named pages — Contact
Us, a whole menu item — that the crawl dropped as "another site". Identity and
address are separate: a page is deduped by `crawl_key` but **requested** by
`rebase_to_origin`, with its path exactly as linked. The old normalizer stripped
the trailing slash from the request too, which un-disallowed
`Disallow: /private/` for `/private` and cost every WordPress page a 301.

`_extract_links` orders crawlable links shallow-first: a homepage lists product
teasers above its footer, and a bounded frontier used to spend its budget on
`/product/x` before `/contact-us`. `CRAWL_MAX_PAGES_CEILING` is the one crawl
ceiling — the API validates against it and `/api/scrape/probe` hands it to the
scope picker (`frontend/src/lib/crawlScope.ts`). Sitemaps read only
`<url>/<loc>`: `<image:loc>` counted a catalogue's photos as pages.

Tests: `test_site_url.py`, `test_crawl_pool.SiteIdentityTest`/`FrontierOrderTest`, `test_sitemap.py`.

## A menu is what the markup declares

`extract_nav_links` reads `<nav>` / `role="navigation"` first. Only when those
yield nothing does it read **declared** menus — containers of
`_DECLARED_MENU_ITEM_CLASSES` (`menu-item`, stamped by WordPress/Drupal menu
walkers). Page-builder kits (feruni.com's Elementor/LaStudio header) emit no
`<nav>`, `<ul>` or role, so the site used to read as having no navigation. A
hidden hamburger panel changes nothing: its links are in the HTML. One parser
covers both markups (`_is_menu_item` + `_menu_list_in`) — no second copy.

Menus built by client-side JS: `scraper._entry_with_navigation` renders **the
entry page only**, and only when its static HTML yields no menu. Advisory — a
failed render keeps the fast parse. Menus fetched only on click are not covered.

Tests: `test_nav_extraction.DeclaredMenuTest`, `test_entry_navigation.py`.

## A bot challenge is a stop, not a missing page

`polite.is_bot_challenge` recognizes a challenge by its vendor's documented
header (`cf-mitigated: challenge`), never by page text — on both fetch paths
(`fast_fetch` → `CHALLENGED`; `browser.rendered_page` → `RenderError.challenged`).
A page httpx saw challenged is **not** retried through Playwright — measured on
feruni.com, headless is challenged too, so the render only burns 3-5s and the
client's bot score. Either way it is
recorded as a retriable failure (the politeness circuit still trips), kept at the
front of `unvisited`, and `CrawlOutcome.stop_reason` becomes `"bot_challenge"`,
which the preview states instead of "stopped at the page cap". **Detection
only**: no stealth, solvers or cookie replay — the fix is the site owner
allowlisting the crawler.

Two consequences, both paid for on feruni.com once Cloudflare blocked the
crawler outright: a crawl stopped by `bot_challenge` / `host_failures` is never
served by `find_reusable` (an allowlist added within the 30-minute window used to
get the same empty crawl back), and `ScrapePreview` refuses "Choose pages" when
such a crawl found no pages — otherwise the generator silently builds the
industry template under the brand's name, which reads as "the pages weren't
scraped".

`stop_reason` is mirrored as `CrawlStopReason` in `frontend/src/lib/types.ts` —
change both. Tests: `test_bot_challenge.py`, `test_crawl_pool.CrawlStopReasonTest`,
`test_crawl_job_reuse.test_a_crawl_the_host_refused_is_never_reused`.

## Record pages: plan once, fill many

A catalogue gives every item its own page from one template (feruni.com: 170
`/product/*`). `record_pages.mark_record_sets` (called from page inference)
groups sub-pages sharing a parent **and** a `template_signature` (each
section's level / has-images / has-cards, in order); `RECORD_SET_MIN_PAGES`+
members get `record_set` (the exemplar slug) and `menu_hidden`. Roster-linked
profile pages, translations and top-level pages are never records.

`routers.generate.split_scaffolds` keeps them away from the content planner.
`build_record_pages` makes **one** `chat_json_cached` call per set — the model
answers in section NUMBERS (the `paste_structure` / `bio_condense` contract)
— then `fill_record_page` fills every page from **its own** section at that
position, so every word and photo is that page's source and nothing moves
between items. `_SLOT_KINDS` is the single registry of block kinds (prompt,
validation and dispatch). Any failure falls back to `default_record_template`.
Record pages join `plan.pages` before the deterministic injectors and skip
alignment. The gallery builder is `source_injection.gallery_block_from_section`,
shared with `_inject_image_walls`.

Two rules verbatim filling needs that paraphrase never did:

- **A shared tail is template chrome.** A heading-less page-builder footer lands
  in the last section's prose (feruni: ~400 chars of share buttons + copyright
  on every product). `without_shared_tails` cuts the words EVERY page of the set
  ends a section with (≥ `_SHARED_TAIL_MIN_WORDS`), before planning and filling.
- **Text appears once.** The hero shows its section's text only as a tagline
  (≤ `_TAGLINE_MAX_CHARS`); longer text goes to that section's content block,
  which the hero's section may also have. Nothing is truncated, nothing repeats.
  A slot this page's section can't fill (cards without text) falls back to the
  section's text rather than dropping it.

One upstream rule catalogues exposed: **a template heading is not chrome.**
`nav_extraction.strip_chrome_sections` used to drop any section whose HEADING
appeared on 2+ pages, so every product lost "Product Specification", "2
Collections"… — feruni's modular pages kept only their title, in the LLM path
too. `_section_identity` keys on heading AND content (prose, images, card
titles): a footer widget repeats both; a catalogue label repeats only the first.
Test: `test_image_walls.ChromeSectionsTest`.

Tests: `test_record_pages.py`; `conftest._offline_record_template` pins the call off.

## The Claude API provider

`services/llm.AnthropicClient` is a second implementation of the `LlmClient`
Protocol. **Which client a role gets is picked in the UI, per request**: the
header's model menu (`frontend/src/components/LlmStatus.tsx`) chooses the local
server or a Claude model separately for *content* and *reasoning*.

**The choice is a ContextVar, not a parameter.** `lib/llmChoice.ts` holds the
selection. `jsonRequest` in `lib/api.ts` — the one request helper — sends it as
`X-Webtree-Llm-Content` / `X-Webtree-Llm-Reasoning` on every call, and
`llm_choice.LlmChoiceMiddleware` (pure ASGI) puts it in a ContextVar for the
life of the request. `get_llm` / `get_reasoning_llm` read it. So the nine
services that call an LLM, and every endpoint that reaches one (paste preview,
page recipe, generation), follow the menu with no edit and no threaded argument.
A task spawned in the request (`asyncio.create_task` copies the context)
inherits it, and that includes a crawl job. **No headers = both roles local**,
which is what tests, curl and scripts get.

Rules the choice needs:

- **The wire carries a NAME** — `local` or an id from `llm_choice.CLAUDE_MODELS`
  — and anything else is a 400, never a silent fallback. The key stays in
  `.env`; `/api/llm/models` lists what may be picked, marked unavailable while
  `ANTHROPIC_API_KEY` is unset. The frontend sends **no headers until it has
  read that menu** and checked the remembered choice (`localStorage`) against
  it, so a stale id cannot turn the first requests into 400s.
- **A per-process cache of an LLM result must key on the choice.**
  `chat_json_cached` already does (class, base_url, resolved model).
  `planner.detect_brand_cached` did not, and replayed the previous model's brand
  for 5 minutes after a switch — it keys on `current().reasoning` now.
- **A local-sized knob must not size a Claude call.** Batching
  (`LLM_CONTEXT_TOKENS` etc.) is global and stays sized for the local model
  whichever model is picked; that is safe in both directions, just more calls on
  Claude. Output budget is per client: `ANTHROPIC_MAX_TOKENS`, never
  `LLM_MAX_TOKENS`.
- **`get_llm(model=…)` is the local vision pass** (`LLM_VISION_MODEL`) and ignores
  the menu.
- `/health/llm` answers for the request's own choice, and always reports both
  roles (top level = content, `reasoning`), one probe per distinct endpoint.

`AnthropicClient` reuses `_validated` rather than growing a second retry loop.
That works because the loop only touches `messages` and `max_tokens`, and both
keys mean the same thing on the Messages API. Four differences are forced by
the API:

- **The schema is structured output** (`output_config.format` via
  `anthropic.transform_schema`), and Pydantic still validates — the SDK strips
  min/max constraints. A schema the API refuses falls back to a written JSON
  instruction, and is remembered **only if the retry without the format
  succeeds**. A 400 is also what a data-retention refusal looks like, so one
  failure proves nothing about the schema.
- **No `temperature`, no `thinking`.** Current models reject sampling knobs and
  think adaptively. `think=True` selects `ANTHROPIC_REASONING_EFFORT` instead.
  Don't add `thinking: disabled`: it trades tokens for tag leakage in the JSON.
- **The repair turn is a user turn quoting the reply** (`repair=` hook on
  `_validated`), never a replayed assistant turn. The response carries thinking
  blocks, and an assistant turn stripped of them is edited history to
  `claude-fable-5-1`.
- **`stop_reason == "max_tokens"` is checked before emptiness.** Thinking can
  spend the whole budget before any text, which needs a bigger budget, not a
  retry at the same one. Growth stops at 128K (`max_tokens_cap`), the API's hard
  ceiling.

Refusal fallbacks (`fallbacks="default"`) are sent only for catalogue entries
with `fallbacks=True`.

`ANTHROPIC_API_KEY` is required and read only from settings. The SDK's own
lookup would read the environment behind `config.py`'s back, and when it finds
nothing it raises a bare `TypeError` mid-generation. A missing key or package is
an `LlmError` at **call** time (`_sdk`), so the `llm or get_llm()` idiom never
raises. `conftest._offline_claude` drops the key, so a developer `.env` cannot
bill the suite.

Tests: `test_llm_anthropic.py` (fake SDK client injected; middleware, menu,
health and brand-cache key included). The local path stays covered by
`test_llm_backend.py` / `test_llm_cache.py`, unchanged.

## Gotchas

- **Docker dependency skew.** Compose mounts only `./backend/app`, so code edits
  hot-reload but `requirements.txt` additions do **not** reach the container until
  `docker compose build backend`. This burned a whole OCR feature once (silent
  no-op, one INFO line at startup). If a feature acts absent, check the container:
  `docker exec webtree-sitegen-backend python -c "import importlib.util as u; print(u.find_spec('<pkg>'))"`.
- **A ccTLD is matched against the hostname, never the URL string.**
  `locale._bounded` guards its left edge with `(?<![a-z])`, which is right for
  word-shaped needles (`india` must not match `indiana`) and impossible for a
  dotted TLD — every real domain has a letter before the dot, so `.my` failed on
  `kopitiam.com.my` and `kopitiam.my` alike. All 36 ccTLDs were dead and the
  `urls` argument contributed nothing to `detect_market`: a site whose copy did
  not happen to name a city or a phone code got no market cue at all, and its
  stock imagery came back un-localised. `_host_has_cctld` parses the host and
  suffix-matches it. **Do not fold it back into `_bounded`** — and do not match
  the joined URL string either, or `?user.id=3` scores Indonesia and `/a.in?x=1`
  scores India. Tests: `test_locale.CctldFromHostnameTest`.
- **`.env` lives at the repo root**, not `backend/.env`. pydantic's `env_file` is
  CWD-relative, so the manual `cd backend && uvicorn` path does not pick it up —
  run from the repo root or export. `PEXELS_API_KEY` is one of these.
- **Model config is not in this repo's `.env`.** `LLM_BASE_URL` is all the backend
  knows; engine/model/quant/context/keep-alive live in `ai-server/.env`. The model
  id is discovered from `/v1/models` at runtime. Two roles exist: default (content,
  Qwen3-30B-A3B) and reasoning (`REASONING_*` — brand detection, design brain,
  image judge, GLM-Z1-9B). Kill switches: unset `REASONING_MODEL`,
  `REASONING_THINK=false`, `DESIGN_LANGUAGE_ENABLED=false`. A Claude model is
  picked in the UI's model menu, per request — only `ANTHROPIC_API_KEY` and its
  tuning knobs live in `.env`.
- **"AI server unreachable" states WHICH failure.** `llm.endpoint_failure_hint`
  is the one home for the remedy — model discovery, the completion stream and
  `/health/llm` all read it, so the badge and a failed generation say the same
  thing. It classifies by exception **type** (`socket.gaierror` /
  `ConnectionRefusedError` in the `__cause__` chain, then timeout, then status),
  never by message text: the identical DNS failure reads "Name or service not
  known" under glibc and "nodename nor servname provided" under macOS, so a
  string match would be a silent off switch on one of the two. A name that does
  not resolve is the common one and is almost never the AI server's fault — a
  Tailscale/VPN host resolves only while the tunnel is up, and **a container does
  not inherit the host's VPN DNS**, so `LLM_BASE_URL` must name something the
  *container* can resolve. Claude API failures take the same route by **SDK
  exception type** (`_anthropic_failure_hint`), and the badge says "Claude API
  unavailable" instead.
- **Truncation on content-rich sites** needs `LLM_CTX` (ai-server) *and*
  `LLM_CONTEXT_TOKENS` (here) raised together.
- **`.gitignore` `/lib/` must stay anchored.** An unanchored `lib/` once silently
  swallowed `frontend/src/preview/lib/` — files invisible to `git status`. On a
  Vite "failed to resolve import" for a path that should exist, run
  `git check-ignore -v <path>` before assuming it was never written.
- **This repo is sometimes edited by a second live session.** Re-read files before
  editing and reconcile rather than clobber.
- **Never put CMS/live credentials in chat.** Hand over copy-paste commands with
  placeholders; scripts under `backend/scripts/` default to dry-run and require
  `--apply`.

## Docs

[ARCHITECTURE.md](ARCHITECTURE.md) · [CONFIGURATION.md](CONFIGURATION.md) ·
[SECURITY.md](SECURITY.md) (trust model, SSRF guard in `url_guard.py`) ·
[PERFORMANCE.md](PERFORMANCE.md) · [docs/DESIGN_ENGINE.md](docs/DESIGN_ENGINE.md) ·
[SECTION_VISUAL_POLICY_SPEC.md](SECTION_VISUAL_POLICY_SPEC.md) ·
[ai-server/README.md](ai-server/README.md) ·
[frontend/src/preview/README.md](frontend/src/preview/README.md).
Superseded design history is in `docs/archive/`.
