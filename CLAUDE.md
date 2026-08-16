# CLAUDE.md — Webtree Site Generator

Turns a scraped URL or an uploaded PDF/DOCX into a multi-page site whose JSON
matches the webtree **builder** `BuilderElement` schema 1:1, then pushes it to
the webtree CMS. All LLM calls go to a **locally/self-hosted** OpenAI-compatible
server — never a cloud LLM.

## Where this repo sits

Four sibling repos under `~/Documents/Projects/webtree/`:

| Repo | Stack | Role |
|---|---|---|
| **site-generator** (here) | FastAPI + React | AI generation/scraping only. Its SQLite is ephemeral crawl state, **not** the app datastore. |
| `webtree-cms-api` | Laravel + MySQL | The real backend. Multi-tenant by `entity_id`; public routes are host-based/tokenless, admin routes are JWT + `entity_api_token`. |
| `webtree-public` | Nuxt | Renders the published sites. **Never run `nuxt build` there** — `.nuxt`/`.output` are tracked and served; `npm test` (vitest) only. |
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
URL / document / FB Page → scraper.py | doc_parser.py | facebook_source.py → SourceContent
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

## Adding a new section block

Catalog entry in the builder + sync, then wire the generator:
`SectionType` literal + block model + `ContentBlock` union in `content_blocks.py`
→ mapper in `section_content._MAPPERS` → `planner._SCAFFOLD_BLOCK_SCHEMAS`
→ `page_inference._SIGNAL_PATTERNS` + `_SIGNAL_ELIGIBLE_TYPES`. Hero variants
must **also** land in `hero_director._MOOD_SPECS` rotations or they're never
picked. New variants of an existing type need only the catalog entry.

Catalog rules: no `display:grid` (use 2Col/3Col/flex) — the one sanctioned
exception is a `$bento` fan-out container, which `check_catalog_contract.py`
exempts by node name. `$bento` has full parity: `_bento_spans` in
`template_filler.py` and `bentoSpans` in `builder/src/lib/section-catalog.ts`
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
Graph API when a token exists → public Playwright render. Graph fields are
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
`conftest._offline_facebook` nulls the token and disables the render fallback, so
the default chain is empty and no test can reach Facebook.

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
pins that. Remember `webtree-public` serves committed `.nuxt`/`.output`, so a
renderer edit there is inert without a build you must not run locally.

Tests: `test_video_embeds.py`, `test_map_embeds.py` (deliberately parallel — they
share a DOM walk and an injection spine); `builder/src/lib/{embed-kind,section-catalog}.test.mjs`
(`node --test`); `webtree-public/lib/videoEmbed.test.ts` (`npm test`).

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
  (sets `htmlTag="h1"`); heroes carry it elsewhere. Zero or multiple is an audit
  failure.
- Titles must be unique across the site and within the length bounds in
  `ux_audit.py` — duplicate titles/descriptions and missing `ogImage` are flagged
  there. `detect_duplicate_seo` / `detect_orphan_pages` back it.
- **The audit is advisory: it logs and never blocks generation.** If you make it
  blocking you will fail real sites — raise the generator's output quality
  instead.
- `push_orchestrator.py` honours `page.seo.noindex` at push time.

Tests: `test_seo.py`, `test_ux_audit.py`.

## Gotchas

- **Docker dependency skew.** Compose mounts only `./backend/app`, so code edits
  hot-reload but `requirements.txt` additions do **not** reach the container until
  `docker compose build backend`. This burned a whole OCR feature once (silent
  no-op, one INFO line at startup). If a feature acts absent, check the container:
  `docker exec webtree-sitegen-backend python -c "import importlib.util as u; print(u.find_spec('<pkg>'))"`.
- **`.env` lives at the repo root**, not `backend/.env`. pydantic's `env_file` is
  CWD-relative, so the manual `cd backend && uvicorn` path does not pick it up —
  run from the repo root or export. `PEXELS_API_KEY` is one of these.
- **Model config is not in this repo's `.env`.** `LLM_BASE_URL` is all the backend
  knows; engine/model/quant/context/keep-alive live in `ai-server/.env`. The model
  id is discovered from `/v1/models` at runtime. Two roles exist: default (content,
  Qwen3-30B-A3B) and reasoning (`REASONING_*` — brand detection, design brain,
  image judge, GLM-Z1-9B). Kill switches: unset `REASONING_MODEL`,
  `REASONING_THINK=false`, `DESIGN_LANGUAGE_ENABLED=false`.
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
