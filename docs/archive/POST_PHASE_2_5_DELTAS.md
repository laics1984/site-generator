# Post-Phase-2.5 Deltas — Read Before Phase 3

Significant work landed in the codebase *after* the Phase 2.5 polish wrapped.
This document is the inventory + invariants list so the next phase (Phase 3:
PDF/DOCX upload) doesn't accidentally roll any of it back.

If you touch any file listed here, **read this first.**

---

## Big additions

### A. Bounded same-domain crawler — multi-page source

The scraper now follows same-domain links from the entry URL and returns
a tree of pages, not just one.

**New / changed files**

- [`services/scraper.py`](backend/app/services/scraper.py)
  - `scrape_url(url, *, respect_robots, crawl, crawl_max_pages, crawl_max_depth)` — new crawl knobs (defaults: `crawl=True`, `max_pages=12`, `max_depth=2`)
  - Pulled the single-page renderer out into `_goto_and_render(context, url, ...)` so one browser context serves the entry + the BFS crawl
  - New `_crawl_extra_pages()` — BFS over same-domain links, batches of 3 in parallel, honours `robots.txt` per-link
  - `_parse_rendered_html(html, final_url, *, require_text)` — shared parser used for entry and sub-pages
  - `_is_crawlable_link()` + `_normalize_crawl_url()` + `_NON_PAGE_EXTENSIONS` + `_SKIP_PATH_HINTS` — link filtering
- [`routers/scrape.py`](backend/app/routers/scrape.py)
  - `ScrapePreviewRequest` gained `crawl`, `crawl_max_pages`, `crawl_max_depth`
  - Cache key includes all crawl params so off vs. on are independent cache entries
  - Response now includes `discovered_count`

**Invariants for Phase 3**

- Document parser must produce `SourceContent` in the same shape (it already does — `discovered_pages` defaults to `[]`)
- Document parser doesn't crawl — it just emits the single source. No code path here breaks
- Anything that consumes `SourceContent` must continue tolerating `discovered_pages = []` (the page-inference module already does)

---

### B. Page-tree inference (no LLM)

A pure-Python module that turns a crawled `SourceContent` into a tree of
`PageScaffold`s. The LLM never decides what pages exist — only what copy goes
into each.

**New file**

- [`services/page_inference.py`](backend/app/services/page_inference.py)
  - `infer_page_scaffolds(source, *, industry, site_name)` — main entry. Returns a flat list of `PageScaffold`s with `parent_slug` set on children
  - `optional_pool_for(industry, inferred)` — pages from the industry template that the inference didn't cover; surfaced as unchecked options
  - `_TYPE_HINTS` — slug-substring heuristics mapping URLs → `PageType`
  - `_TOP_SECTIONS`, `_SUBPAGE_SECTIONS`, `_WORK_SUBPAGE_SECTIONS`, `_TEAM_SUBPAGE_SECTIONS` — section rhythms per page type
  - Falls back to the industry template when the crawl returned nothing — small / no-crawl sources still get a sensible default

**Invariants for Phase 3**

- The doc-upload preview endpoint should still call `infer_page_scaffolds()` — it works fine with `discovered_pages=[]` (falls back to the industry template)
- `PageScaffold.parent_slug` and `PageScaffold.source_url` are now part of the public API; keep them around when building scaffolds from documents

---

### C. Schema changes — hierarchy is now a first-class concept

**Files changed**

- [`models/content_blocks.py`](backend/app/models/content_blocks.py)
  - `SourceContent` gained `url_path: str | None` and `discovered_pages: list[SourceContent]`
  - `PagePlan` gained `parent_slug: str | None`
- [`models/industry.py`](backend/app/models/industry.py)
  - `PageScaffold` gained `parent_slug: str | None` and `source_url: str | None`
  - `PageRecipeResponse` gained `inferred_pages: list[PageScaffold]`
- [`models/builder_schema.py`](backend/app/models/builder_schema.py)
  - `GeneratedPage` gained `parent_slug: str | None`
  - New `PageNode` class — recursive `{slug, title, is_homepage, children}`
  - `GeneratedSite` gained `page_tree: list[PageNode]`

**Invariants for Phase 3**

- Any new code creating `PageScaffold` / `PagePlan` / `GeneratedPage` must preserve `parent_slug` when applicable (the doc parser's output is always flat → `parent_slug=None` everywhere, which is correct)
- The frontend already consumes `page_tree` and `inferred_pages` — do not remove these from response payloads

---

### D. Breadcrumbs + parent-aware nav rendering

**Files changed**

- [`services/schema_builder.py`](backend/app/services/schema_builder.py)
  - `RenderContext` extended with `current_page_slug`, `current_parent_slug`, `children_by_parent`, `page_title_by_slug`
  - New `_build_breadcrumb(page_plan, ctx)` — prepended above the hero on sub-pages
  - `plan_to_site()` now computes `children_by_parent` and `page_title_by_slug` up front, passes them in `RenderContext`, then calls `_build_page_tree(pages)` and threads `page_tree` into `build_header` and `build_footer`
  - New `_build_page_tree(pages: list[GeneratedPage]) -> list[PageNode]`
- [`services/header_footer.py`](backend/app/services/header_footer.py)
  - `build_header` and `build_footer` both accept a `page_tree` parameter
  - Header renders a hover-dropdown when a top-level page has children
  - Footer's company column reads from `page_tree`; `extra_legal_nav` parameter adds Privacy/Terms to the legal row
  - `_top_level_nav_entries(page_tree)` — flatten roots → `(label, href)` for the legacy nav-items contract

**Invariants for Phase 3**

- `build_header` / `build_footer` signatures must keep `page_tree=None` as a graceful fallback (existing tests rely on this)
- Doc-upload flow produces flat pages → `_build_page_tree()` returns a single-level tree → header renders without dropdowns (no breakage)

---

### E. Parent-aware LLM batching

**File changed**

- [`services/planner.py`](backend/app/services/planner.py)
  - `_SCAFFOLD_BATCH_SIZE = 3`, `_SCAFFOLD_NUM_CTX = 4096`
  - `_scaffold_depth(s)`, `_hero_summary(page)` helpers
  - `plan_site_with_scaffolds()` now batches: sorts scaffolds by depth, runs 3-at-a-time, harvests parent hero headlines into `parent_context`, feeds them into child batches so detail pages echo the parent's voice
  - System prompt updated to reference `parent_slug` + `parent_context`
  - `_build_scaffolded_user_prompt` accepts `parent_context: dict[str, dict] | None`
  - **Critical**: `parent_slug` is *stitched back onto every PagePlan from its source scaffold* after the LLM returns. The LLM can't reliably round-trip it; we re-attach it server-side
- [`services/llm.py`](backend/app/services/llm.py)
  - `chat_json` accepts `num_ctx: int = 4096` (was implicit default — Ollama's 2048 was too small)
  - Repair-retry prompt now explicitly mentions "every page object MUST have a 'blocks' array"
  - Improved `_post_chat` error string includes exception type

**Invariants for Phase 3**

- All new LLM calls should pass `num_ctx` explicitly (don't go back to the Ollama default of 2048)
- The `parent_slug` stitch-back step in `plan_site_with_scaffolds` is load-bearing — do not remove it

---

### F. Frontend hierarchy + crawl UI

**Files changed**

- [`frontend/src/lib/types.ts`](frontend/src/lib/types.ts)
  - `SourceContent.url_path`, `SourceContent.discovered_pages`
  - `PageScaffold.parent_slug`, `PageScaffold.source_url`
  - `GeneratedPage.parent_slug`
  - New `PageNode`; `GeneratedSite.page_tree`
  - `PageRecipeResponse.inferred_pages`
  - `ScrapePreview.discovered_count`
- [`frontend/src/lib/api.ts`](frontend/src/lib/api.ts)
  - `scrapeUrlPreview(url, opts)` — `opts` now `{ crawl, crawlMaxPages, crawlMaxDepth }`
  - Maps camelCase opts → snake_case payload at the boundary
- [`frontend/src/components/SourcePanel.tsx`](frontend/src/components/SourcePanel.tsx)
  - URL mode has a "Discover sub-pages from the site" checkbox (default on)
  - Button label reflects state: "Crawling…" vs "Fetching…" vs "Fetch site"
  - `onScrape` signature: `(url: string, opts: { crawl: boolean })`
- [`frontend/src/components/ScrapePreview.tsx`](frontend/src/components/ScrapePreview.tsx)
  - "Scrape OK" banner shows `discovered_count`
  - New "Sub-pages found" `<details>` listing url_paths + titles
- [`frontend/src/components/PagePicker.tsx`](frontend/src/components/PagePicker.tsx)
  - Renders `inferred_pages` as a tree via `buildTree()` + `<TreeRow>`
  - `selectedPages` semantics extended: ticking a parent ticks its subtree; unticking unticks the subtree (`toggleSubtree`)
  - "Discovered N sections with M sub-pages from the source" banner under the industry chip
  - Industry-change merge logic preserves user toggles across the inferred tree, not just the flat template

**Invariants for Phase 3**

- `SourcePanel`'s `onScrape` callback contract — `(url, opts)` — must stay. The App passes opts through; do not strip them
- `scrapeUrlPreview` is the only function on the API client that takes URL-mode options. The doc upload will need a parallel `uploadDocumentPreview(file, opts?)` and should keep similar shape
- `PagePicker` already handles the empty-crawl case (`inferred_pages=[]` → falls back to `template.core_pages` + `template.suggested_pages`). Doc uploads will naturally produce empty crawls — no changes needed in the picker

---

## Other smaller deltas

| Change | Where | Why |
|---|---|---|
| `BROWSER_USER_AGENT` realistic Chrome string + `_BROWSER_HEADERS` + `_STEALTH_INIT_SCRIPT` | `scraper.py` | Beat WAF 403s (Cloudflare/Akamai); kept from earlier polish |
| Friendly 401/403/429 error messages with `status=` carried through | `scraper.py` | The picker's error UI relies on the message strings |
| LLM `num_ctx` parameter | `llm.py` | Larger payloads were getting truncated |
| `_align_pages_to_scaffolds` now forces `parent_slug` from scaffold onto the matched page (and onto synthesised pages) | `routers/generate.py` | Hierarchy enforcement |

---

## Phase 3 invariants — summary

Things Phase 3 **must not** break:

1. **`SourceContent` shape** — `url_path: str \| None`, `discovered_pages: list[SourceContent]` exist on every instance. Document parser sets both to `None` / `[]`
2. **`PageScaffold.parent_slug` / `source_url`** — preserved end-to-end
3. **`PageRecipeResponse.inferred_pages`** — populated even for documents (via `infer_page_scaffolds` falling back to industry template)
4. **`GeneratedSite.page_tree`** — emitted from every `plan_to_site` call regardless of source
5. **`plan_site_with_scaffolds` parent_slug stitch-back** — load-bearing, don't remove
6. **LLM `num_ctx` propagation** — every chat_json call passes `num_ctx` explicitly
7. **Header / Footer `page_tree`-aware rendering** — must keep working with `page_tree=None` fallback for any legacy callers
8. **Frontend types** — `SourceContent`, `PageScaffold`, `GeneratedPage`, `GeneratedSite`, `PageRecipeResponse`, `ScrapePreview` all carry the new fields. Doc-upload responses should populate them (mostly with zeros/nulls) to keep types stable

When Phase 3 lands, the doc parser produces a `SourceContent` with `source_kind: 'pdf' | 'docx'`, `discovered_pages: []`, `url_path: None`. Everything downstream — recipe inference, page picker, LLM scaffolded planning, schema builder, header / footer — works with no changes. **That's the goal: Phase 3 only adds a new source extractor, nothing else.**
