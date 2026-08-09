# Webtree Site Generator — Pipeline Deep Dive

From the moment **Fetch site** is clicked to a finished `GeneratedSite` (and the optional CMS push).
Every phase: what runs, why it exists, and the non-obvious logic ("secret sauce") that makes it work.

Line references are `path:line` against the repo root.

For a from-zero-knowledge version of the same material, see
[HOW_IT_WORKS_EXPLAINED.md](HOW_IT_WORKS_EXPLAINED.md).

---

## Phase 0 — Frontend state machine

`frontend/src/App.tsx` is a single stateful wizard. The important state:

| State | Meaning |
|---|---|
| `scrapeResult` | raw `ScrapePreview` from the crawl (source_content + brand_candidate + unvisited frontier) |
| `confirmedSource` | the `SourceContent` **after** the user edited the extracted text |
| `selectedPages` | `PageScaffold[]` ticked in the picker |
| `detectedBrand` | cached LLM brand detection, passed forward to skip a second call |
| `lastGenerate` | the exact payload of the last generation, so Regenerate is free |

There are two generation entry points:
- `handlePagesConfirm` → `POST /api/generate/with-pages` — the real path.
- `handleGenerateFreeform` → `POST /api/generate/from-source` — legacy paste path, no page picker.

**Design point:** the wizard is never torn down after generation (`setFormOpen(false)` only collapses it),
so Adjust/Regenerate cost zero re-scraping.

---

## Phase 1 — Sitemap probe

`handleScrape` (App.tsx:167) → `POST /api/scrape/probe` → `services/sitemap.probe_sitemap`.

**Purpose:** learn the site's true size in 1–3 seconds of plain HTTP, *before* paying 30–90s for
Playwright, so the UI can ask "Quick (20 pages) or Full?" instead of silently truncating.

**Logic** (`sitemap.py`):
1. `GET /robots.txt`, regex out `Sitemap:` directives — author-curated, so preferred.
2. Fall back to `/sitemap.xml`, `/sitemap_index.xml`, `/sitemap.xml.gz`.
3. `_read_sitemap_recursive` handles both shapes: `<urlset>` (leaf → collect `<loc>`) and
   `<sitemapindex>` (→ recurse, **capped at depth 2 and 8 sub-sitemaps**).
4. gzip transparently decompressed; body capped at 5 MB; results de-duped preserving order; 500 URLs max.

**Secret sauce:** every failure path returns `has_sitemap=False` rather than raising. The frontend
treats a probe failure as "proceed with the default cap" (App.tsx:189-192). A probe can never block a scrape.

**Branching:**
- `has_sitemap && total_urls > 20` → `ScopeChoice` modal, scrape deferred.
- otherwise → straight to `runScrape` with cap 20.
- crawl checkbox off → probe skipped entirely, `maxPages: 0`.

---

## Phase 2 — Crawl job lifecycle

`runScrape` (App.tsx:199) does **not** call the synchronous `/api/scrape/preview`. It uses a job model:

```
POST /api/scrape/start    → { job_id }        (returns instantly)
GET  /api/scrape/jobs/id  ×N  (1s poll, 10min ceiling)
DELETE /api/scrape/jobs/id    (best-effort cleanup)
```

- `routers/scrape.py:184` — SSRF guard runs **first**, then `crawl_jobs.create()` writes a row to SQLite,
  then `asyncio.create_task(run_crawl_job(id))` and `mgr.register_task()`.
- `services/crawl_orchestrator.py:23` is the job-aware wrapper. Its whole job is to
  (a) hand `scrape_url` an `on_progress` callback that writes progress to SQLite,
  (b) hand it an `is_cancelled` closure the crawl checks *between pages*, and
  (c) **never raise into `create_task`** — every exception path records `job.error` and returns.
- `_result_to_payload` (crawl_orchestrator.py:99) deliberately mirrors the `/preview` response shape
  byte-for-byte so the frontend's `ScrapePreview` type hydrates from either endpoint unchanged.

**Also still live:** `POST /api/scrape/preview` with a 30-minute in-process cache keyed on
`(respect_robots, crawl, max_pages, max_depth, url)` — `scrape.py:29`. Absorbs double-clicks and
back-button traffic. GC'd lazily on write (`_gc_cache`).
⚠️ Its frontend caller (`api.ts:116 scrapeUrlPreview`) has **zero call sites** — this endpoint and its
cache are effectively dead. See improvement #10.

---

## Phase 3 — Scrape and crawl (`services/scraper.py`, 2964 lines)

### 3.1 Guards

**`url_guard.assert_public_url`** (url_guard.py:63) — the SSRF choke point:
- scheme must be http/https
- explicit hostname denylist: `host.docker.internal`, `gateway.docker.internal`, `metadata.google.internal`
- `getaddrinfo` on the event loop's resolver (never blocks), then **every** returned IP must fail
  `is_private / is_loopback / is_link_local / is_reserved / is_multicast / is_unspecified`
- `settings.scrape_allow_private_hosts` is the local-dev escape hatch

It runs at *every* fetch boundary: the router, `scrape_url`, `_goto_and_render`, `try_fast_fetch`
(both before **and after** redirects — fast_fetch.py:96 and :116), sitemap, image_vision.
The documented residual risk is a mid-chain redirect that never returns.

**`_robots_allows`** (scraper.py:133) — `RobotFileParser` with a 10-minute per-host cache. Any HTTP
failure is treated as permissive (`rp = None` → allow).

### 3.2 Fetch strategy — httpx first, Chromium second

`services/fast_fetch.py` exists because ~80% of real marketing sites are static or SSR'd.

- Plain `httpx.get` with a realistic Chrome UA and `Accept-Encoding: gzip, deflate`
  (**br deliberately omitted** — httpx has no Brotli decoder by default and would return unparseable bytes).
- Substance heuristic: run `trafilatura` on the response; **≥500 chars → accept**, else it's a JS shell
  → `FastFetchSkipped` and Playwright takes over.
- ~250–600ms vs Playwright's 3–5s. On a 20-page crawl that's ~12s vs ~70s.

Playwright path (`_goto_and_render`, scraper.py:319):
- One `browser` + one `context` shared by the entry render **and** the entire crawl.
- `context.route("**/*", _route_block_heavy)` aborts `media`/`font`/`websocket` requests.
- `add_init_script(_STEALTH_INIT_SCRIPT)` + `--disable-blink-features=AutomationControlled`.
- Status handling is user-facing, not generic: 403 → "the site has bot-detection, paste the content
  instead"; 401 → "requires auth"; 429 → "wait a minute". These messages surface directly in the UI.
- `wait_for_load_state("networkidle", 3000)` wrapped in try/except — a page that never idles still renders.
- `_autoscroll` — 12 × 1200px with a 150ms settle, **bails early when `scrollHeight` stops growing**,
  then scrolls back to top. Without it, IntersectionObserver-mounted copy never enters `page.content()`.

### 3.3 The render-evidence stamp — the single cleverest piece in the scraper

`_stamp_render_evidence` (scraper.py:233) runs JS in the live page and writes measured geometry into
DOM attributes, so the *static* BeautifulSoup parse downstream can see what the browser saw.

Every `<img>` gets `data-webtree-evidence`:
```json
{"nw":naturalWidth,"nh":naturalHeight,"x":..,"y":..,"w":..,"h":..,"vw":..,"vh":..,"grid":N}
```
Every element ≥200×120px with a CSS background gets `data-webtree-bg-image` (resolved URL) plus
`data-webtree-bg-evidence` including `text`: **the length of text rendered inside it**.

`gridCount()` is the key trick: walk up to 4 ancestors, and at each level count sibling cells that hold
exactly one image of *similar area* (0.4×–2.5× mine). **≥3 similar siblings ⇒ this image is one tile of a grid.**

`services/image_evidence.py` turns that into a role, cheap disqualifiers first:

| Rule | Result |
|---|---|
| `< 96×72` rendered | `decoration` |
| aspect ≥ 4.5 and height < 140 | `decoration` (banner strips, marquee logos) |
| background element with ≥24 chars of text over it | `background` |
| starts in top 60% of viewport AND (≥18% viewport coverage OR ≥80% width & ≥35% height) | `hero` |
| grid_count ≥ 3, square-ish (0.6–1.6) and ≤420px wide | `portrait` |
| grid_count ≥ 3 otherwise | `gallery` |
| area ≥ 160×120 | `content` |
| else | `decoration` |

**Why this matters:** it is the whole defence against the classic scrape failure — a committee member's
headshot blown up as the hero background, or a logo cropped into a feature card. Roles propagate all the
way into `ImageMetadata.role` and are honoured by `image_match`, `promptable_images` and the LLM prompt.

The httpx fast path leaves **no** stamps → `parse_evidence` returns `None` → the scraper falls back to
legacy DOM-order heuristics. Behaviour degrades, never breaks. Two fast-path compensations exist
(scraper.py:2281-2291): filename-based logo detection, and "3+ profile cards ⇒ their photos are portraits".

### 3.4 Parsing one page — `_parse_rendered_html` (scraper.py:2241)

Runs via `asyncio.to_thread` (trafilatura + lxml are CPU-bound and would stall the other crawl workers).

**Body text — `_extract_body_text` (scraper.py:2092).** Two extractors *merged*, not chosen between:
- `trafilatura.extract(favor_recall=True, include_tables=True)` — high precision, but on marketing pages
  it drops whole `<section>` blocks it deems boilerplate, and it drops headings.
- `_structural_text` — walks every block-level tag on a decomposed copy, dedupes by lowercased text.
  High recall, some nav noise.

Structural is the **spine** (document order, headings included); trafilatura blocks not already present are
appended. Neither side's content is lost. Flat `get_text` only if both come back empty.

Entry page under 80 chars ⇒ `ScrapeError` 422 with a user-facing explanation (SPA loading state, paywall, auth).

**Images — `_extract_images` (scraper.py:932).** Sources: `<img>` tags, `og:image`/`twitter:image`
(inserted at index 0 as `intent="hero"` — curated and usually high quality), and CSS background URLs
(inline styles at "level 1", `<style>` block CSS text at "level 2"). Filters: icon-name denylist
(`tracking`, `pixel`, `spacer`, `1x1`, `loader`…), `<200px` or `<120px` declared/measured, and any
evidence-classified `decoration`. Measured natural size beats declared attributes. Capped at 30.
`_promote_hero_by_evidence` assigns `hero` after *all* candidates (including CSS backgrounds) are measured
— so DOM order never decides the hero.

`_upgrade_source_image_url` (scraper.py:574) is a targeted fix for Wix: Wix bakes transforms into the URL
path (`/v1/fill/w_119,h_79,...,blur_2/`), so a scraped `src` is often a **blurred 119px placeholder**.
The media id encodes original dims (`_d_{W}_{H}`), so the rewrite restores a crisp variant capped at 2560px
(the untransformed original can be 20 MB+ and blow the CMS upload cap).

**Profile cards — `_extract_profile_candidates` (scraper.py:1616).** Structural inference, no LLM:
1. Start from an `<img>` that survives icon/logo/size gates and has portrait aspect (0.5–1.6).
2. `_nearest_profile_container` walks up looking for a hinted container (`team|member|profile|staff|
   committee|board|trustee|…`) — stopping at `nav/footer/header/aside/form` and Divi-style `*footer*` divs.
3. If the walk finds nothing or a nameless container, `_row_text_sibling_for_profile` tries **sideways** —
   page-builder layouts put the portrait and the copy in *sibling columns*, which the upward walk can't see.
   (The comment notes this omission once cost a whole team grid its photos.)
4. `_extract_profile_name` with a hard denylist: `_GENERIC_PROFILE_NAMES` (exact: "our team", "good food",
   "latest events"…) plus `_NON_NAME_LEAD_TOKENS` (a real name never starts with `our/the/meet/why/getting/
   building/good/latest/…`). `_NAME_PARTICLES` whitelists `bin/binti/van/der/de/al/…` so
   "Siti binti Rahman" and "Jan van der Berg" survive the "every token capitalised" rule.
5. Role: ≤8 words, rejected if it opens with a `_PROSE_LEAD_TOKENS` pronoun ("He leads our…" is a bio, not a title).
6. Contacts scraped from the card's own anchors (mailto/tel/social), and `_profile_card_link` records
   *where this person's own page lives* — this becomes the site's roster→detail index later.
7. Fallback: zero cards → `_page_subject_profile` (this page is about *one* person), but only with
   **measured** portrait aspect, since a card's structure earns the benefit of the doubt and a loose photo doesn't.

**Document cards — `_extract_document_cards` (scraper.py:1815).** Same structural idea, anchored on a
`.pdf/.docx/...` href instead of a portrait. Then `_strip_document_card_lines` **removes their text from
`raw_text`** — claiming the content before the LLM ever sees it, so the model can't narrate the same titles
into an invented, disconnected prose section.

**Navigation — `services/nav_extraction.py`.** Three extractors:
- `extract_nav_links` — scores `<nav>`/`role=navigation` candidates by `(in_header, top-level item count)`,
  parses `<li>` structure into a nested `NavLink` tree (dropdown parents that are `<button>`/`<span>` toggles
  with no href are captured by label). This is the **site owner's own curation** and becomes hierarchy evidence.
- `extract_body_link_clusters` — link-dense blocks *inside* the content container (header/nav/footer removed
  from a copy first). Qualifies at 3–15 same-site links whose combined anchor text is ≥60% of the block's text.
  Smallest container wins via a `consumed` anchor set. `cluster_href_key` = sha1 of the sorted href set — a
  stable identity that can be compared **across pages**.
- `extract_social_links` — first occurrence per platform, max 6, with share/intent URLs excluded
  (`/sharer`, `/intent`, `/plugins/`) and bare platform homepages rejected.

### 3.5 The BFS crawl — `_crawl_extra_pages` (scraper.py:2435)

**Three queues, not one.** This is where the crawl's quality lives:

| Queue | Contents | Drained |
|---|---|---|
| `priority` | roster/profile-card links (a committee page's links to its own members) | first |
| `frontier` | ordinary discovered links | second |
| `deferred` | locale mirrors (`/bm/about` when `/about` is known) | only when nothing else is queued **or in flight** |

Rationale, straight from the code: a plain FIFO lets a roster's member links get crowded out by nav/footer
links when a site has more pages than the budget — and those member pages are exactly what a Team block
needs. Mirrors are real pages, so they stay queued, but a bilingual site must not spend its whole budget
saying everything twice. The tier is carried in the queue tuple so it drives **result order** too, not just
fetch order — a translation of `/about` must never outrank `/about` downstream.

`mirrors_unlocked` only flips when nothing untranslated is queued *or in flight*, so a translation can never
steal a worker slot from a page no other language covers.

Also here:
- `_normalize_crawl_url` — strip fragment, normalise trailing slash, lowercase host.
- `_is_crawlable_link` — same host, not in `_NON_PAGE_EXTENSIONS` (documents sourced from
  `nav_extraction.DOCUMENT_EXTENSIONS` so the two stay in lockstep), not in `_SKIP_PATH_HINTS`
  (`/wp-admin`, `/cart`, `/login`, `/tag/`, `/author/`, `/page/`, `/search`…).
- Queue cap at `max_pages * 3` so a link-farm page can't blow memory.
- **Sliding-window worker pool**, not lockstep batches: with batches of 3, one slow Playwright fallback
  stalled two finished slots per round. A `pw_sem` semaphore keeps Chromium tab pressure at the old level.
- Per-host politeness gates every fetch (`services/polite.py`): a semaphore (4 concurrent), a min-delay
  (200ms or robots `Crawl-delay`, whichever is larger, protected by a lock so two slots don't both think
  they were "most recent"), and a **circuit breaker** that opens after 5 consecutive failures and stops the crawl.
- httpx statuses in `RETRIABLE_STATUS_CODES` (429/502/503/504) record a failure and **do not** fall through
  to Playwright — same host, same problem.
- Progress is emitted per page and cancellation is checked between pages (`_should_stop`).

Whatever the frontier still holds when the cap hits is returned as `unvisited_urls` → powers
"Crawl N more" (`POST /api/scrape/extend`, which resumes without re-rendering the entry).

### 3.6 Post-crawl: template chrome purge

Only once the whole page set is known can you tell a *section subnav* from *page content*.
`strip_chrome_lines` (nav_extraction.py:488): a cluster `href_key` seen on ≥2 pages is template chrome →
drop `raw_text` lines that **exactly** match one of its link labels (whole-line only, so prose that merely
mentions a label survives). Run again idempotently in `/api/pages/recipe` for sources assembled outside
`scrape_url` (extend-crawl merges, doc uploads).

### 3.7 Brand candidate

`_build_brand_candidate` (scraper.py:2188): fetch the logo URL (guarded by `is_public_url`), then
`extract_palette_from_image_bytes` in a thread (PIL decode + quantize is CPU-bound). Returns
`BrandIdentity` with `extracted_palette` and `logo_is_light`. Any failure returns `None` — a site with no
detectable logo still generates.

---

## Phase 4 — Preview and confirm

`ScrapePreview` shows the extracted text editable. `handleScrapeConfirm` (App.tsx:388) merges the edit into
`confirmedSource`. **No LLM has run yet** — this is the whole reason the scrape/generate split exists
(`routers/scrape.py` docstring: "so the frontend can show a confirmation step before spending an LLM call").

Because `detect_brand_cached` fingerprints on the full `raw_text` (planner.py:161), **editing the text here
automatically invalidates the brand-detection cache.** That's deliberate.

---

## Phase 5 — Page recipe (`routers/pages.py`) — LLM call #1

### 5.1 Brand detection

`planner.detect_brand` → the **reasoning** model (GLM), `DETECT_BRAND_PROMPT`, capped at
`settings.brand_detection_max_chars` (large PDFs were causing `ReadTimeout` 502s on this first, cold call).

`DetectedBrand` (planner.py:103) is defensive by design:
- `heal_mood` / `heal_industry` validators coerce invented values back to the enum — "a wrong adjective must
  never fail brand detection".
- `_mood_from_industry` model validator: no usable mood → derive from industry rather than blanket "modern".

`detect_brand_cached` — 5-min TTL, sha256 fingerprint over `(source_kind, source_ref, title, raw_text)`,
light GC above 64 entries. The recipe endpoint and the generate endpoint both want this; the frontend also
passes `detected_brand` forward explicitly so the second call is skipped even on a cache miss.

### 5.2 Page inference — **no LLM**, `services/page_inference.py` (1319 lines)

Pure function: URL paths + titles + nav structure → a `PageScaffold` tree.

**`_infer_page_type`** — ordered `_TYPE_HINTS` table, first match wins, matching on full slug segment or
substring. Unmatched: `landing` if the slug has a `/`, else `services`.

**Section rhythms** — `_TOP_SECTIONS` maps page_type → section list (`home` → hero/features/testimonials/cta,
`team` → hero/team/cta, `blog` → hero only because schema_builder appends a CMS `articlesList` element).
Sub-pages get tighter rhythms: `_SUBPAGE_SECTIONS`, `_WORK_SUBPAGE_SECTIONS` (gallery-led),
`_TEAM_SUBPAGE_SECTIONS`, and `_PROFILE_PAGE_SECTIONS` = `[hero, profile, cta]`.

Four content-driven overrides layer on top of the fixed rhythm:

1. **Directory detection** — `_looks_like_directory_page`: ≥6 `profile_candidates` (`DIRECTORY_MIN_PROFILES`).
   `_coerce_directory_type` re-types `services`/`landing`/**`contact`** as `team` (sites really do park a
   "Find a Music Therapist" directory at /contact), but never `about`/`faq`/`home`.
2. **Story pages** — `_story_section_count`: count *distinct headings* that have a content-grade image whose
   `context_heading` matches. ≥3 ⇒ the source narrates section-by-section, so replace the fixed rhythm with
   `[hero, about×n, cta]` — each `about` renders as an image+text split bound to that section's real photo.
   Zero on the fast path (no context captured), so detection degrades to the fixed rhythm.
3. **Photo weaving** — `_weave_photo_sections`: every eligible page gets **at least one** image+text `about`
   split as a baseline (landing-page practice: text blocks need breaking up), up to 2 when the page is
   image-rich (heading-matched photos, or ≥4 loose content photos).
4. **Content signals** — `_SIGNAL_PATTERNS` regexes for `timeline` ("founded in", "since 19xx", "milestones"),
   `awards`, `clients`, `stats` (`\d+%`, `over \d+`, "years of experience"), `locations`. Gated by
   `_SIGNAL_ELIGIBLE_TYPES` (a restaurant's /about only gets awards if the text *says* it won one), capped at
   2 extras, trimmed to fit `_MAX_PAGE_SECTIONS = 9`, and inserted **before** the closing `cta`.

**Hierarchy inference** — three independent sources of truth, in precedence order:
- `roster_detail_links` — read straight off `_profile_card_link`. A page a roster links to belongs to that
  roster, **not** to a `/profile` section invented from its URL. Requires the roster to have
  `ROSTER_MIN_PROFILES = 2` cards, because a *lone* card on a person's page links **back** to the roster,
  and reading that as a roster edge would file the committee under Ashley Jinivon.
- `evidence.parent_of` — header dropdown nesting.
- `_subnav_edges` — repeated in-body menu strips. A cluster is *local subnav* (not a duplicated primary nav,
  not a quick-links row) only when it repeats across pages, is **not** a subset of the header nav, and is
  **self-referencing** (a page carrying the strip is itself one of its targets). That's the signature of a
  template section menu. The parent is then either the single listing-type page among the targets, or the
  unique listing-type page *outside* the strip whose own links cover its targets. Ambiguous ⇒ don't guess.

Nav nesting beats cluster inference (`forced_parent.update(evidence.parent_of)`); nesting is capped at one
level deep; a child re-parented to a same-type parent is coerced to `landing`.

**Template-title disambiguation** — `_ambiguous_page_labels`: a `<title>` or heading carried by ≥2 pages
names the *template*, not the page (MMTA's nine committee-member pages are all "About MMTA"). Those pages fall
back to `_distinct_page_label`: exactly one profile card on the page ⇒ that person's name; else the first
heading not shared with siblings. The owner's own **nav label** beats the `<title>` when ≤40 chars.

**Translations** — `translation_pairing`: a mirror only counts when its untranslated counterpart is also in
the crawl (so `/it/support` on a site with no `/support` stays an ordinary page). A bare `/bm` translates the
homepage, but only for codes that aren't ordinary English words (`AMBIGUOUS_LOCALE_SEGMENTS`). Mirrors are
held out of the whole structural walk and attached at the end with `locale` + `translation_of`.

**Team placement IA** — `_apply_team_placement`: a template-suggested Team page is not evidence. The roster
belongs under About by default; a separate Team page survives only when `from_source=True`. Whether the
**homepage** repeats the roster is deliberately deferred to `generate._apply_homepage_team_policy`, because
it depends on portrait/vision gating that hasn't happened yet.

**Founders weave** — `_weave_founders_into_home`: if 2–`FOUNDERS_BAND_MAX` people carry a founder-ish role
anywhere in the crawl, the homepage gets a `team` section. More than that is a leadership page, not founders,
so it returns `[]`.

### 5.3 Recipe response

`build_theme` runs here too so the picker can show the real fonts/colours up front, using the same inputs
generation will use (`palette_mode="auto"`, no logo palette yet). `detected_brand` is handed back for reuse.

---

## Phase 6 — `POST /api/generate/with-pages` orchestration (`routers/generate.py:1152`)

Order matters throughout. Annotated:

```
a. split scaffolds        content / translation / legal
b. brand+mood+industry    override precedence
c. design language        ── LLM #2 (reasoning model)
d. build_theme
e. claim linkbar          strip its text BEFORE planning
f. kick off prefetch+OCR  ── background, overlaps the GPU-bound content pass
g. content generation     ── LLM #3 (the expensive one)
h. align + repair passes
i. await prefetch, vision annotate, roster/team fill
j. bind image_refs
k. clone translations
l. plan_to_site           ── LLM #4 inside (design brain), + all rendering
m. append legal pages
```

**(b) Override precedence** (generate.py:1197): `payload.industry == "other"` is treated as *unset* — it's
the frontend's catch-all when the user never opened the dropdown, so a detected `childcare` must win, or the
industry's light-only design brief is lost (the comment cites a kindergarten rendering dark). Mood:
explicit override → brand.mood → `industry_locked_mood(industry)` → detected.

**(e) Linkbar claim before planning.** `find_linkbar_cluster` finds a *one-off* (non-chrome) strap of 2–6
links on the entry page with a context label ("Current Releases:"), rejecting breadcrumbs
(`_looks_like_breadcrumb`: announcement straps virtually never link home). `strip_linkbar_lines` then drops
lines that are essentially *made of* those labels (≥2 matched, <5 alphanumerics remaining). Re-injected as a
real linkbar section after alignment. Same principle as document cards: **claim content before the model
sees it, or it gets narrated twice.**

**(f) Overlap strategy.** `prefetch_image_pool` (network) and `_screen_source_images_for_text` (OCR, CPU) are
launched as tasks and awaited *after* the content LLM. Vision **judging** deliberately stays after generation:
two models on one 16GB GPU would thrash weight swaps. This is the one place where the local-GPU constraint
visibly shapes the architecture.

---

## Phase 7 — Content generation (`services/planner.py`) — LLM call #3

### 7.1 Prompt (`services/prompts.py`)

`_SCAFFOLD_PROMPT_HEAD` is the product spec in prose. Key sections:

- **FIDELITY RULES override everything else.** An explicit never-fabricate list (testimonials, stats, prices,
  awards, team names, addresses, FAQ specifics, menu items, founding dates) plus a placeholder-name ban, plus:
  *if the source can't ground a requested section, OMIT it — a shorter honest page beats a padded one.*
  Exceptions: hero/about/contact/cta, which assert nothing.
- **REAL PHOTOS** — the page's own images are offered as `{ref, alt, role, near}` and the model binds
  `image_ref`. Two hard bans, both derived from the render-evidence roles: never bind a `background`-role
  photo to a content section, never bind a `portrait` to anything but a team member or testimonial author
  ("the single most common way a generated page looks wrong").
- **DESIGN INTENT** — headlines as editorial pull-quotes, 6–12 words, *varied across pages*; every hero gets
  an `eyebrow`; homepage hero layout leans background for visual/brand-led industries, split for credibility-led.
- **Image query policy** — content sections default to *people doing the thing*, with an explicit exception
  for when the subject genuinely is a **thing** (menu dish, product, portfolio piece, room). Bare mood words
  banned. Atmospheric phrasing reserved for `background_query`. **Negative words banned outright**
  ("stressed", "empty", "alone", "sad") — a downbeat stock photo is a brand risk regardless of industry.
- Write in the source's language.

`_SCAFFOLD_BLOCK_SCHEMAS` is one line per block kind. The system prompt is **assembled per batch** from only
the kinds that batch requests, plus `_SCAFFOLD_ALWAYS_KINDS = {hero, about, cta, contact}`
(`_scaffold_system_prompt`, `lru_cache(64)`). The 2026-07 rewrite removed triplicated rules for ~35% fewer
prefill tokens per call.

Note the FAQ line is the one place curation is **forbidden**: "transcribe EVERY genuine Q&A… do not pick a
representative subset". And `profile` is explicitly *one* person, with contacts in a `contacts` array rather
than smuggled into the bio.

### 7.2 Source routing (`services/source_router.py`)

`match_scaffolds_to_pages` — the fix for "the LLM is writing /services copy from the homepage's text":
1. homepage → entry source
2. exact slug match against `discovered_pages`
3. trailing-segment match (`services/web-design` → `/web-design`)
4. `_PAGE_TYPE_KEYWORDS` match against path+title+first 3 headings
5. entry source fallback — so a value **always** exists

Special case: FAQ content is often split across `/faq` **and** `/support`, so every keyword-matching page's
text is combined (`_combine_sources` merges `raw_text` + `headings` only; identity fields stay the primary's).

`promptable_images` is the deterministic photo list the prompt shows: excludes `logo`/`decoration`/**`portrait`**
roles and sub-200px images, capped at `MAX_PROMPT_IMAGES = 12`. **Its ordering must stay a pure function of
`image_metadata`**, because `services/image_refs.py` recomputes the exact same list to resolve the model's
`ref` integers back to URLs after parsing.

`split_raw_text` is **heading-aware**: when the current chunk is ≥half full and the next line is a known
heading, seal the chunk there — so a source section (heading + paragraphs + photo context) stays whole inside
one chunk rather than being cut mid-section. Greedy block packing is the fallback.

### 7.3 Batching (`_build_batches`, planner.py:399)

Four limits, any of which seals the current batch:
1. input tokens — `_system_prompt_tokens(batch kinds) + Σ page_input` vs `num_ctx × 0.48 − fixed`
2. output tokens — `Σ sections × 230` vs `num_ctx × 0.52`
3. section density — `settings.max_sections_per_batch = 6` (a quality cap: past ~10 sections the model thins
   each block out)
4. absolute page cap — `settings.max_pages_per_batch = 4`

Per-page input is estimated from the page's **actual** text length (capped at
`multipass_max_chars_per_call = 6000`) plus `len(promptable_images) × 30`, so tiny pages pack several per
batch instead of all costing a flat estimate. The **fixed** envelope is *measured once* by building the real
prompt with an empty page list (planner.py:1010) rather than trusting the 600-token guess — an unusually long
brand summary would otherwise overflow. The system prompt is measured per candidate batch, because adding a
page with a new section kind *grows the prompt*.

### 7.4 Work-list, chunking and merging

`plan_site_with_scaffolds` builds an ordered work-list of three item kinds:
- `batch` — a run of small pages
- `multipass` — one page whose text exceeds the per-call budget → generated across chunks
- `section_multipass` — one page with more sections than `max_sections_per_batch` → generated in section
  groups, each **anchored by the hero** (repeated on purpose: the merge keeps only the first, but every
  group needs the page's thesis so its body sections stay coherent)

Scaffolds are stable-sorted by `(depth, original index)`, so parents generate before children and their hero
headlines flow down as `parent_context` (`_hero_summary`). `include_entry_text` is true **only for item 0** —
later items ground each page in its own source, so the entry text there was pure duplication.

`_merge_page_plans` is where multipass results come back together:
- kinds the scaffold requests **once** → `_merge_blocks_of_kind`: list-bearing kinds union their items across
  chunks (so a capped FAQ is drawn from the *whole* page, not just its first chunk); singletons take chunk 0
  (top of page → best hero/about).
- kinds requested **multiple** times (story pages' repeated `about`) → `_keep_repeated_blocks` keeps them side
  by side, deduped by normalised heading.
- dedupe keys are per-kind (`_LIST_BLOCK_SPECS`), with **composite** (AND) semantics only for `timeline` —
  two milestones can share the title "Expansion" in different years.
- caps are read from the pydantic field metadata (`_field_max_len`) so merge caps never drift from the models.
- `timeline` is sorted chronologically **before** the cap truncates, else a multipass page keeps chunk-arrival
  order instead of the earliest real history.
- final block order follows `scaffold.sections` occurrence-by-occurrence, extras appended.

### 7.5 LLM client hardening (`services/llm.py`)

`chat_json` sits behind four layers of resilience:
1. `_post_nonempty` — an empty stream (cold model load, or a turn spent entirely on reasoning tokens) is
   retried once.
2. `TruncatedLlmResponse` → `_retry_with_growing_budget`: **double `max_tokens` and retry**, up to 131 072.
   Explicitly *not* a repair retry — the repair prompt is longer than the original and would truncate again.
   Only the output budget can grow; a prompt that overflows context needs a smaller batch, not a retry.
3. `ValidationError` → one repair retry with the error list fed back, using the *boosted* payload.
4. `ScaffoldedSitePlan.drop_invalid_pages` — a page broken past salvage is dropped so the rest of the plan
   parses; `_align_pages_to_scaffolds` re-materialises it from its scaffold anyway.

`chat_json_cached` — opt-in TTL+LRU over **validated** results, keyed on the *resolved* model id (so swapping
the model on the ai-server invalidates entries). Stores and returns **deep copies**, because every caller
mutates results in place. Test fakes bypass it entirely so call-counting fixtures keep working.

---

## Phase 8 — Post-LLM enforcement (`services/scaffold_enforcement.py`)

`align_page_to_scaffold` is the contract enforcer: **the scaffold owns order and the allowed set; the LLM owns
the words and which content sections it can honestly fill.**

For each required kind in order: take the LLM's first matching block (sanitized), else inject a
`_STRUCTURAL_FALLBACK_KINDS` default, else **omit**. Extras are dropped. A totally empty page keeps a
title-based hero. Only the **first** occurrence of a structural kind gets a default — a story page requesting
five `about`s and getting three means the model couldn't ground five, so the extras are omitted, not blanked.

**The fabrication net.** `is_grounded_in_source` (scaffold_enforcement.py:298) checks each fact-bearing item
against the page's own scraped text: exact normalised substring, or (for ≥15 chars) a longest contiguous
`difflib` match ≥60% of the needle. Tolerant of whitespace/smart-quote noise, **intolerant of paraphrase** —
because the prompt says quotes must be preserved verbatim. Applied to `testimonials` (quote *or* author),
`awards` (title or issuer), `clients` (name), `stats` (value or label), `timeline` (year or title). Zero
survivors ⇒ the whole block is dropped.

`_sanitize_team_block` is asymmetric on purpose: **a bad name means it isn't a person → drop the entry; a bad
role or bio is untrustworthy text attached to a real person → blank the field** and let the card render with
what it can stand behind. `looks_like_team_member_name` requires 2–7 tokens, all capitalised (particles
allowed), no `@`/`http`, no `_NON_PERSON_NAME_TOKENS` ("services", "awards", "portfolio", "web", …), no
`_NON_NAME_LEAD_TOKENS`. Note `model_copy` bypasses validators, so the deprecated `description` alias has to
be written by hand or the dirty text resurrects through `_team_content`'s `bio or description`.

`_backfill_hero_image_query` fixes a silent failure: `HeroBlock` has no healing validator for `image_query`,
and a blank one means the resolver never queries Pexels — the hero degrades to a flat gradient. Page-type-aware
defaults are used (`contact` → "welcoming modern office reception").

Back in `generate.py`, further deterministic passes run in a deliberate order:
- `_strip_profile_faq_items` — FAQ items manufactured out of profile listings
- `_ensure_hub_child_links` — every child page reachable from its parent's *body*, not just the footer
- `_inject_linkbar`, `_inject_downloads`
- `_enrich_plan_profile_photos` → `_ensure_scraped_team_blocks` → `_drop_hollow_team_pages` →
  `_prune_dead_profile_links` (last, so a member's link is checked against pages the site actually ships)
- `bind_image_refs` — `image_ref` integers → real URLs, recomputed against the same `promptable_images` list
- translations cloned **here**, after image binding, so a mirror inherits the exact photos its counterpart
  ended up with rather than the ones it was planned with

---

## Phase 9 — `plan_to_site` (`services/schema_builder.py:4068`) — SitePlan → BuilderElement

Everything below is deterministic. This is the only module that owns styling.

### 9.1 Setup

1. **`compose_design_manifest`** (`services/design_director.py`) — chrome archetypes. Four-tier selection:
   explicit override (confidence 1.0) → industry pin (`childcare`, `nonprofit`; confidence 0.9) → mood fit
   list → seeded rotation → **diversity nudge** (confidence 0.6 when it moved the pick). Every decision is
   recorded with a rationale string. An LLM is *deliberately* not in this loop: chrome archetypes are a small
   closed vocabulary where a fit table beats a 7–9B model's judgement.

   `"classic"` is in every fit list — "never wrong, merely never *interesting*", so it anchors the rotation.

2. **`services/diversity.py`** — SQLite history of design choices. `recent_choices(area, site_key)` returns
   *this site's* last pick (a regeneration should explore) plus the last few global picks (a batch shouldn't
   converge). `pick_diverse` starts from a seeded index (md5, not `hash()`, so it survives interpreter
   restarts) and walks forward past avoided candidates — **if everything is avoided, the seeded pick stands:
   fit beats novelty at the margin.** Every path fails open to "no history".

3. **`ImageResolver`** constructed once per site, holding the used-URL set. URLs already bound by
   `bind_image_refs` are pre-marked used so free ranking can't steal them.

4. **Two warm-ups in parallel** (`asyncio.gather`, schema_builder.py:4218):
   - `resolver.prewarm_stock(_harvest_image_slots(plan))` — expands every slot into its full stock-query
     chain, de-dupes by `(query, orientation)`, and fetches concurrently into the shared per-query cache.
     Explicitly **output-identical**: selection, dedup and rotation still run in the serial render loop.
   - `generate_site_design_recipe` — **LLM #4**. One call for the whole site, keyed by
     `(page_index, section_index)` so one page's "section 0" can't land on another's. Heroes are excluded
     (the hero director owns them). Only sections with ≥2 mood-allowed templates are even offered.

### 9.2 Hero art direction (`services/hero_director.py`)

Two orthogonal axes, both seeded and idempotent:
- `plan_site_heroes` → `HeroDirective(template_id, layout, wants_wash, pin_source_background)`
- `plan_site_compositions` → `HeroComposition(anchor)`

Both call `_inherit_from_parent`: a `menu_hidden` profile page reached from a roster copies its parent's pick
wholesale — it reads as a continuation of that roster, not a new place. Both nudge consecutive picks apart
(if the seeded pick equals the previous page's, step one forward in the rotation) — per-page seeding alone
clusters, and three consecutive identical heroes is exactly the problem this exists to solve.

`_MOOD_SPECS` / `_INDUSTRY_SPECS` encode per-mood hero languages (nonprofit: immersive homepage, editorial
storytelling, oversized-type minimal for transactional pages; childcare: **deliberately excludes the gradient
hero** because its hardcoded white text goes illegible over childcare's pastel primary).

⚠️ **With the current defaults** (`hero_fullbleed_all_pages=True`, `hero_anchored_copy=False`) both rotations
short-circuit: every page of every site gets `hero-background-bold`, centre-composed. See the improvements
section.

`_apply_hero_photo_policy` resolves the photo **up front** so the gradient-vs-photo choice can key off the
resolver's real source, and `_has_composable_subject` forces `center` when the photo is an abstract wash,
gradient or placeholder — an anchor only earns its keep when there's a *subject* to make room for.

### 9.3 Contact href resolution

Every `#contact` placeholder is rewritten **before** rendering (schema_builder.py:4304), per locale:
a Contact page that renders a form beats one that only lists details; with no Contact page at all it falls
back to `/slug#contact` on the page carrying a `contact` section. A site with no contact affordance gets
**no header CTA button** rather than a dead one.

### 9.4 Per-block rendering

`block_to_element` → `block_to_section` (maps `ContentBlock` → catalog content dict via
`section_content._MAPPERS`) → `select_template` → `fill_template`.

**`is_feasible`** (section_content.py:623) — every non-optional slot must be fillable: lists need items
(and ≥`minItems` where declared, e.g. bento), images need `query` or `src`, links need a label, text must be
non-blank. This is the hard gate.

**`select_template`** — mood-gated candidates (`mood_allows`: a template with a `moods` list is only offered
to those moods, so playful kindergarten styling never lands on a law firm) → feasibility filter → the design
brain's `explicit_id` **only if it's in the feasible set** → mood preference order → first candidate.
`pool = feasible or candidates` means the section degrades rather than disappearing.

**`fill_template`** (`services/template_filler.py`) is the Python mirror of the builder's TS
`materializeTemplate`. Directive vocabulary: `$slot`, `$repeat`, `$bento`, `$gridFit`, `$content`, `$styleSlot`.

Notable bits:
- `$gridFit` prefers 3 columns but **drops to 2 when `count % 3 == 1`** — a single orphan card beside a big gap
  reads as broken (4 → 2×2, 7 → 2+2+2+1).
- `$bento` stamps varied grid spans (`_bento_spans`: large lead tile, periodic wide tile, standard halves) on a
  6-column `auto-flow: dense` grid. **Python-only — the TS engine has no `$bento` branch** (known parity gap).
- `$styleSlot` on `backgroundImage` with a theme hands off to `image_styling.photo_background`, which builds
  the full layered treatment (grain, edge fade, copy scrim, vignette, brand cast) whose darkness **adapts to
  the photo's measured average luminance**, and overrides the catalog's `background-size: cover` because the
  grain layer tiles at fixed size while every other layer covers.
- Empty containers are **pruned** — a card whose text slots were all blank would otherwise render as a stray
  placeholder box; the prune cascades up through bare wrappers but spares anything with its own background.
- `{"monogram": name}` — a named person with no portrait gets a locally-built monogram in brand colours,
  never a stock photo of a stranger.

### 9.5 Image resolution (`services/media.py` + `image_match.py`)

`ImageResolver.resolve(query, intent, prefer, slot_usage, pinned_url, allow_portrait)` — **always succeeds**.

Order:
1. **Pinned** (`image_ref`-bound) wins outright — *unless* it fails a fitness screen. A background slot
   additionally runs the pin through OCR (`verify_one`) on demand, because "an LLM `image_ref` binds by topic
   and cannot see the picture, so a promo banner captioned 'our restaurant' is exactly the kind of pin that
   arrives here and must not be honoured full-bleed."
2. **Scraped pool**, ranked by `image_match.rank_candidates`, then `_reject_text_backgrounds`.
3. **Pexels** via `_search_pexels`.
4. **On-brand gradient placeholder** — no network, no random stock photo, with a per-seed nonce so two
   placeholders on a page don't render identically.

**Ranking (`image_match.py`)** — three signals, `[0,1]`:
- lexical 0–0.6: token overlap between the slot query and `alt ∪ URL path tokens ∪ vision_caption ∪
  context_heading ∪ caption`. The vision caption is what rescues authentic photos with hashed CDN filenames
  and empty alt text; the context heading does the same job for free on the render path.
- intent 0–0.2 (exact match 0.2, `generic` 0.1)
- size 0–0.2 (negative below 200px)

Bands: **≥0.62** confident (skip the judge), **0.30–0.62** ambiguous, **<0.30** fall through to Pexels.
The LLM judge is a *tiebreaker*, not an evaluator: it only fires in the ambiguous band **and** when ≥2
candidates are within 0.10 of the top — typically 0–2 calls per site.

`slot_usage` is the other axis: `inline` keeps source CSS backgrounds out of featured slots; `background`
pins them first *and* enforces a minimum long edge (`hero_min_background_dim` / `section_min_background_dim`)
because a small source image visibly softens under `background-size: cover`.
`allow_portrait` is set only by slots that are *about* a person or replay the source's own photos.

**Pexels chain (`_search_pexels`)** — market/place/industry-cued query chain, each fetching a batch of ~15:
- drop candidates with `_has_negative_vibe` alt text (falling back to the unfiltered batch if that empties it)
- rank by `_stock_relevance` against the slot query
- `_first_text_free` walks the ranked list past photographs *of* text under an OCR budget
  (`ocr_verify_budget = 4`) — rejecting a candidate costs a step down the *same* batch, not another round-trip
- accept only if the winner has real relevance to **either** the slot query or the chain candidate that was
  searched (the industry fallback entries use deliberately different wording, so checking only the slot query
  would wrongly reject an on-target hit). The chain's last entry is broad and always accepted, so it never
  falls all the way to a placeholder.

### 9.6 The styling pass chain

Fifteen mutation passes over `elements`, in an order the comments justify individually
(schema_builder.py:4420-4478):

| # | Pass | Why here |
|---|---|---|
| 1 | `apply_about_zigzag` | alternate image sides so photos zigzag |
| 2 | `apply_luminance_rhythm` | resolve per-section light/dark bands + recolour dark-band text |
| 3 | `apply_section_rhythm` | legacy alternation for sections the luminance pass didn't fill |
| 4 | `modernize_sections` | fluid type, card depth/glass, atmospheric surfaces; **steers texture away from sections that will border a shaped divider** |
| 5 | `cap_gradient_textures` | keep the first pure gradient/mesh/grain, flatten the rest — after (4) creates them, before (7) reads final colours |
| 6 | `enforce_fill_contrast` | resolve `var(--builder-color-*)` gradient fills to concrete AA-checked hexes |
| 7 | `polish_inset_panels` | needs (6) to have turned tokens into real hexes to judge fill darkness |
| 8 | childcare pastel rhythm + heading colours | overrides (3)/(4) flat colours, before dividers read final pastels |
| 9 | `apply_section_dividers` | the picture section carries the edge, fill = the flat neighbour's exact colour; a boundary with pictures on both sides gets **no** divider |
| 10 | `retune_photo_edge_fades` | the photo's bottom dissolve was written against the theme bg before the page existed — re-point it at the real neighbour or drop it |
| 11 | `apply_heading_alignment` | reads the final band each section landed on |
| 12 | `apply_heading_levels` | first section title → `<h1>`, later → `<h2>` (purely structural) |
| 13 | `apply_motion` | scroll/backdrop motion, last |

**`resolve_section_bands`** (section_content.py:782) is the pure algorithm underneath (2):
precedence `band_override > anchored > strict alternation`. An **anchored** section (one carrying its own
featured image) is fixed to the *opposite* band of its image so the image pops. Flexible sections alternate
around those fixed points. A flexible section always flips from its left neighbour, so only two adjacent
*forced* sections can collide — and that boundary is flagged `separator_before`, which
`apply_luminance_rhythm` renders as a within-band luminance step plus a hairline border.

A **panelled** section (content in one inset self-filled card) is forced light *at the input stage* rather
than repainted afterwards, so alternation and the separator rule stay honest — neighbours flip around the
forced band instead of colliding with it unannounced.

### 9.7 Chrome, contrast, audit

- `header_overlay` is keyed off the homepage's **actually rendered** first section carrying
  `headerOverlaySafe`, not the pre-render directive — a hero that fell back to a light layout keeps the solid
  header with dark ink instead of unreadable white-on-light. Rule: white nav ⇒ dark hero.
- `build_header` / `build_footer` from `manifest.header_archetype` / `footer_archetype`. `menu_builder`
  deliberately leaves Contact out of the primary nav because the header CTA covers that action; primary nav
  is capped at `MAX_PRIMARY_ITEMS = 7` and dropdowns at 8, ordered by source `nav_rank` with
  `_TYPE_NAV_WEIGHT` as fallback. `menu_hidden` children (roster members) are excluded from menus but keep
  their place in the tree, so breadcrumbs still read Home > Committee > Person.
- `enforce_text_contrast` over every page **and** the header/footer — catalogue sections hard-code a single
  dark text token that vanishes on a same-luminance band.
- `style_whatsapp_links` after the contrast pass (which must not retint white-on-green).
- The manifest rides into the CMS inside `builderStyles.designManifest` — flexible JSON, no migration, and
  both renderers ignore it.
- `record_manifest_choices` feeds the diversity history for the *next* site.
- `ux_audit.audit_site` + `audit_seo` run inside a bare `try/except` and are **logged only**. Rules:
  alt-text, aria-label, colour-contrast (≥4.5:1, resolving `var()` against the pushed `builder_styles`),
  readable-font-size (≥12px), image-dimensions, SEO title/description length + uniqueness, heading hierarchy,
  cta-missing, og-image-missing, orphan-page.

---

## Phase 10 — Push (`services/push_orchestrator.py`)

Ten ordered steps, each recorded as a `PushStep` in a `PushReport` the UI renders as a progress table:

1. `auth` — JWT
2. `create_entity` (optional) — captures `entity_api_token`
3. `guard` — greenfield-only unless `force_overwrite`
4. `normalize_slugs` — **greenfield keeps hierarchical paths** (`/profile/ashley`) so a migrated site
   publishes at the URLs it already ranks for; a non-empty entity is a re-push over a live site, so slugs
   flatten (renaming published pages is exactly the SEO damage this prevents). This runs *after* the guard,
   because only the guard's page list can tell the two apart.
5. `media` — collect unique srcs, upload, build a rewrite map. Individual failures are non-fatal: the image
   is **stripped** so the published site never renders a broken reference pointing back at the source.
6. `create_pages` — homepage **first and alone** (so `isHomepage=true` is deterministic), rest concurrently
   under a semaphore
7. `read_layout_version` → `save_layout` — menus + header/footer in one PUT on the homepage
8. `save_drafts` — `bodySchema` per page, concurrent
9. `builder_styles` via the launch-code bridge — **non-fatal**, but it mints a new layout version, so
   `layout_version_id` must be refreshed or publish fails with `LAYOUT_VERSION_CONFLICT`
10. `publish` (optional), then `_push_content_types` for article/event collections

---

## Improvements — ranked

### Tier 1 — correctness bugs

**1. `_infer_page_type` substring order misclassifies `/how-we-work`.**
`page_inference.py:41` lists `work` (index 7) *before* `process` (index 11), and the match is a bare
substring against `f"{slug} {title}"`. So `/how-we-work` → `work` (a portfolio/gallery rhythm) instead of
`process`. Same class of bug: `/framework`, `/teamwork`, `/network` → `work`; `/helpful-resources` → `faq`
(the `help` hint). Fix: require a whole-token match (`_slug_tokens` already exists and is unused by this
function) or move the multi-word hints (`how-we-work`, `case-studies`) ahead of the single-word ones.

**2. The politeness circuit breaker never closes.**
`polite.py:93` sets `_circuit_open = True` and nothing ever resets it except `reset_politeness()`. The
registry is **process-wide and keyed by host**, so one crawl that hits five consecutive failures poisons that
host for the lifetime of the backend process — every later crawl of the same site stops immediately with
"politeness circuit open". Fix: half-open after a cooldown (e.g. reset when `now - last_failure > 60s`), or
scope the instance to the crawl job.

**3. The sitemap probe's URLs are discarded.**
`probe_sitemap` returns up to 500 author-curated URLs, and `routers/scrape.py:272` ships them to the frontend
— which uses only `total_urls` to size the cap. The BFS then rediscovers the site from scratch through
`links[:50]` per page. Seeding the frontier from the sitemap would find pages the link graph hides, and would
make the "Full" scope choice actually mean "the pages the sitemap listed".

**4. `_extract_links` caps at 50 per page** (`scraper.py:2170`) — *before* `_is_crawlable_link` filters
anything. On a link-dense homepage (mega-menu + footer sitemap), 50 absolute URLs are often exhausted by
chrome, so genuine content links never enter the frontier. Filter first, then cap; or raise the cap for the
entry page specifically.

### Tier 2 — robustness

**5. One failed batch kills the whole generation.** `generate.py:1291` turns any `LlmError` from
`plan_site_with_scaffolds` into a 502. With a local single-GPU server, a cold-load timeout on batch 4 of 6
throws away five successful batches. Since `_align_pages_to_scaffolds` can already synthesise a page from its
scaffold, per-item failure should degrade to structural defaults for that batch and surface a warning in the
response, not abort. This is probably the single highest-value change in the list.

**6. All process-local state breaks under multiple workers.** `_CACHE` (scrape), `_DETECT_BRAND_CACHE`,
`_RESPONSE_CACHE` (llm), `_ROBOTS_CACHE`, `_registry` (politeness) and the crawl job's `asyncio.Task` all
live in one process. Jobs are durable in SQLite but their *tasks* are not: a backend restart leaves rows
stuck in `running` forever with no reaper. Either document single-worker as a hard requirement, or move the
caches to SQLite and add a startup sweep that fails orphaned `running` jobs.

**7. Grounding checks are O(items × page_text) with `difflib`.**
`_longest_match_len` runs `SequenceMatcher(autojunk=False).find_longest_match` over the **entire normalised
page** per item, and it's called twice per testimonial. On a 30k-char page with 20 items that's real wall
time in a synchronous path. The haystack is already normalised once — the remaining win is to gate the fuzzy
path behind a cheap n-gram prefilter, or cap the haystack to the chunk the item plausibly came from.

**8. `_CHARS_PER_TOKEN = 4` under-counts non-English text.** The whole batching budget rests on this constant
(planner.py:388), and this generator is explicitly built for multilingual Malaysian sites (`bm` locale
handling everywhere). Chinese/Japanese text runs closer to 1–1.5 chars/token, so a `/zh` page's estimate can
be off by 3×, which silently overflows `num_ctx` and burns a truncation retry. Cheap fix: detect CJK
character ratio in `excerpt_for_prompt` and scale the divisor.

### Tier 3 — design engine

**9. Two hero-variety systems are inert by default.** `hero_fullbleed_all_pages=True` makes
`plan_site_heroes` return `hero-background-bold` for **every page**, and `hero_anchored_copy=False` makes
`plan_site_compositions` return `center` for **every page**. Both defaults have reasoned comments, so this is
a decision, not an accident — but the consequence is that `_MOOD_SPECS`, `_INDUSTRY_SPECS`, `_NONPROFIT_SPEC`,
`_CHILDCARE_SPEC` and `_ANCHOR_ROTATION` (~150 lines, plus `test_hero_director.py`) never execute in
production, while the design-brain prompt still says heroes are "pre-assigned by the generator's own per-page
art direction". Either re-enable a narrowed rotation (e.g. keep full-bleed but vary height/overlay per page)
or delete the dead specs so the next reader doesn't debug code that can't run.

**10. `plan_to_site` is a 600-line function with 15 order-dependent mutation passes.** Every ordering
constraint is real and documented in a comment, which is exactly what makes it fragile — the constraints live
in prose, not in code. Making the chain an explicit list of `(name, fn)` pairs would let you assert the order
in a test, log per-pass timings, and let a future pass declare `after=("modernize_sections",)` instead of
relying on line position.

**11. The UX/SEO audit is invisible.** It runs on every generation and logs one INFO line. The frontend never
sees it. Attaching `findings` to `GeneratedSite` and rendering them in the preview panel would turn a
developer log into an operator-facing quality gate — still advisory, per the hard rule that it must never
block, but actually actionable.

### Tier 4 — performance and ergonomics

**12. Image resolution is fully serial across the render.** `for page → for block → await resolve` — the
Pexels prewarm hides the search latency, but `_sampled_fields` still downloads and samples pixels one slot at
a time. Resolving a page's slots concurrently (the used-set would need a lock, or a two-phase
resolve-then-assign) is the remaining wall-clock win after content generation.

**13. Polling instead of streaming.** The crawl UI polls `/jobs/{id}` every second for up to 10 minutes.
The job already writes structured progress; an SSE endpoint would cut request volume and let you stream the
generation phases (`stage()` already instruments them) rather than showing an indeterminate spinner during the
60–120s generation.

**14. `stage()` timings never leave the log.** The design manifest already rides into the response as an
audit record. Adding the per-stage timings alongside it would let you answer "why was this site slow?" from
the UI instead of `docker logs`.

**15. Documented residual SSRF.** `assert_public_url` runs before and after redirects in `fast_fetch`, and
per-page in the crawl — but a redirect chain that never returns is still unguarded (and Playwright's `goto`
follows redirects internally without a per-hop check). Low severity given the trust model, but worth an
`httpx` event-hook that validates each hop.

**16. `/api/scrape/preview` is dead code holding the only cache.** It was the original synchronous
implementation; the job model replaced it. Its frontend caller `scrapeUrlPreview` (api.ts:116) has zero call
sites, so `settings.scrape_cache_ttl_seconds` and the 30-minute dedupe cache are unreachable — while
`/api/scrape/start` creates a fresh `uuid4()` job unconditionally, so clicking "Fetch site" twice on the same
URL re-renders the whole crawl. Fix: delete the endpoint + the dead frontend function, and add a
recent-terminal-job lookup on `(entry_url, options)` in `crawl_jobs.create`.
