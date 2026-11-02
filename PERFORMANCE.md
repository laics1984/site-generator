# Performance

The dominant cost in this tool is **local LLM inference** (seconds-to-minutes per
generation on consumer hardware), not the web layer. The performance design is
therefore about *avoiding and reusing* LLM/network work, keeping the model warm,
and not blocking the event loop. This document lists what's in place and where
the real bottlenecks remain.

## Optimizations in place

### LLM layer
- **Shared keep-alive HTTP client** — `llm.py` reuses one `httpx.AsyncClient`
  across all calls (closed on shutdown via the lifespan handler), avoiding
  per-call connection setup.
- **Model stays resident** — `OLLAMA_KEEP_ALIVE=30m` keeps weights in memory
  between the brand-detection and generation passes, avoiding a cold reload that
  would otherwise blow the read timeout.
- **Streaming reads** — both backends stream, so the httpx timeout is
  per-token-gap, not whole-generation; it only bites on cold time-to-first-token.
- **Validate-or-repair** — a schema-invalid response is fed back once for repair
  instead of re-running the whole call.
- **Response cache** — `chat_json_cached` (TTL/LRU) reuses the expensive,
  deterministic-ish calls (scaffolded content batches, the image judge) so
  regenerating an unchanged site within the TTL skips minutes of GPU time. The
  temp-0.7 design passes stay uncached so the look can still vary.
- **Dynamic batching** — content generation packs multiple pages/sections per
  call (`MAX_SECTIONS_PER_BATCH`, `MAX_PAGES_PER_BATCH`, `LLM_CONTEXT_TOKENS`) to
  amortize the fixed prompt prefill.
- **Record pages** — look-alike detail pages (a catalogue's products) cost one
  small layout call per template set instead of a content batch every few pages;
  each page is filled deterministically (`record_pages.py`).

### Scraping
- **httpx-first fast path** — `fast_fetch.py` tries a plain GET (~250–600ms) and
  only falls back to Playwright (~3–5s) for JS-shell pages. On a 20-page crawl
  that's ~12s vs ~70s of fetching.
- **CPU-bound parsing off the event loop** — trafilatura/lxml parsing runs in a
  worker thread (`asyncio.to_thread`) so other crawl workers keep fetching.
- **Politeness + concurrency** — `polite.py` gates per-host concurrency and delay;
  crawls run concurrently within those bounds. A bot-challenged page is never
  retried through Playwright (headless is challenged too).
- **No redirect hops** — crawl URLs keep their trailing slash and are requested
  on the entry's origin (`site_url.rebase_to_origin`), so neither a WordPress
  slash redirect nor a `www` alias redirect costs a round trip per page.
- **Scrape-preview cache** — absorbs double-clicks / regeneration re-POSTs for a
  whole editing session (`SCRAPE_CACHE_TTL_SECONDS`).
- **Stock pre-warming** — `media.prewarm_stock` concurrently warms the Pexels
  query cache before the serial render loop needs it.

### Web / data layer
- **Async everywhere** — FastAPI + httpx + aiosqlite; no sync blocking in request
  paths.
- **SQLite tuned** — WAL journal + `synchronous=NORMAL` + indexes on the
  crawl-job/page hot paths (`db.py`). Used only for durable crawl state; the
  generation path is stateless.
- **Frontend** — same-origin Vite proxy (no CORS preflight in dev), lean deps
  (React + `clsx`, no router/state library).

## Changes from this pass

- Removed two **unused runtime dependencies** (`pdfminer.six`, `colorthief`) —
  smaller image, faster build, less to audit. PyMuPDF handles PDFs.
- Network timeouts and the browser User-Agent were centralized into config (no
  behavior change; the shared UA removes a duplicated constant).
- The `style_tokens.py` extraction is neutral at runtime (pure relocation).

## Same site, faster, fewer requests (Oct 2026)

Every change below obeys three rules, each pinned by a test: **the design is
unchanged** (same prompts, same deterministic passes, same output), **the
request footprint only shrinks** (per host: fewer requests, lower peak
concurrency, no new Pexels API calls), and **throttling never leaks into the
design** (a deadline covers the transfer, never the wait for a slot).

- **One download per image** — `image_vision._fetch_image_bytes`, the fetcher
  every pixel pass shares, keeps finished downloads (`IMAGE_BYTES_CACHE_MB`,
  TTL) and joins the one in progress. Measured on a 20-photo source: 50 → 20
  requests to the business's server, peak concurrency 9 → 3.
- **The source's host is capped without a crawl** —
  `asset_fetch.gate_source_hosts`, concurrency only, outside polite's
  registry (a no-delay entry there would later pace a real crawl at zero delay).
- **One keep-alive client** for file downloads (`asset_fetch.asset_client`).
- **Content calls are scheduled by dependency** — the slot is held per call, so
  a chunked page's calls go out together, and a child waits only for its own
  parent. Prompt ledger old vs new: identical over concurrency 1/2/3 and eight
  completion orders; with a fixed 200 ms per call, the fixture's content pass
  went 2.02 s → 1.62 s at the default concurrency of 2 (serial unchanged).
- **Identical in-flight LLM calls are joined** (`chat_json_cached`).
- **Bio condense calls run together** (one per team block).
- **Stock bytes are warmed** for likely full-bleed slots
  (`ImageResolver.prewarm_background_bytes`), from Pexels results already
  cached — CDN only, never the source's own server.
- **The SSRF guard caches its DNS verdict** per host for 60 s.
- `stage` timers now cover the image screens, vision annotation, bio condense,
  bound-image verification and `plan_to_site`.

Considered and rejected because they would move the design or the footprint:
moving the design-brain recipe earlier (it reads the final page list and runs
uncached), starting the graphic/OCR screens before the design direction (it
reads the `role`/`ocr_has_text` they write), starting collections earlier (it
would raise peak concurrency on the business's host), batching the stock OCR
screen (more CPU in the common case), and any cap, budget or concurrency
default change.

## Remaining bottlenecks (by impact)

1. **LLM inference time** — inherent and hardware-bound. Levers: a faster/remote
   endpoint (`LLM_BASE_URL` → a bigger AI server), a smaller model, raising
   `SCAFFOLD_BATCH_CONCURRENCY` **only** on a backend that truly serves parallel
   requests (vLLM/llama-server), and the response cache for regenerations.
2. **Playwright fallback** — a browser render is ~10× an httpx fetch. Sites that
   are pure SPAs pay this per page; nothing to do but cap crawl breadth.
3. **Serial render loop** — `plan_to_site` renders pages sequentially so each
   page's hero sees its parent's context; fine given the LLM is the real cost.
4. **In-process caches are per-process** — a multi-worker deployment wouldn't
   share the LLM/scrape/Pexels caches. Out of scope for the single-process tool;
   would need a shared cache (e.g. Redis) if it ever scales out.
