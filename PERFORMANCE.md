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
  call (`MAX_SECTIONS_PER_BATCH`, `MAX_PAGES_PER_BATCH`, `SCAFFOLD_NUM_CTX`) to
  amortize the fixed prompt prefill.

### Scraping
- **httpx-first fast path** — `fast_fetch.py` tries a plain GET (~250–600ms) and
  only falls back to Playwright (~3–5s) for JS-shell pages. On a 20-page crawl
  that's ~12s vs ~70s of fetching.
- **CPU-bound parsing off the event loop** — trafilatura/lxml parsing runs in a
  worker thread (`asyncio.to_thread`) so other crawl workers keep fetching.
- **Politeness + concurrency** — `polite.py` gates per-host concurrency and delay;
  crawls run concurrently within those bounds.
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

## Remaining bottlenecks (by impact)

1. **LLM inference time** — inherent and hardware-bound. Levers: a faster/remote
   backend (`LLM_BACKEND=mlx` → the AI server), a smaller model, raising
   `SCAFFOLD_BATCH_CONCURRENCY` **only** on a backend that truly serves parallel
   requests (vLLM/llama-server), and the response cache for regenerations.
2. **Playwright fallback** — a browser render is ~10× an httpx fetch. Sites that
   are pure SPAs pay this per page; nothing to do but cap crawl breadth.
3. **Serial render loop** — `plan_to_site` renders pages sequentially so each
   page's hero sees its parent's context; fine given the LLM is the real cost.
4. **In-process caches are per-process** — a multi-worker deployment wouldn't
   share the LLM/scrape/Pexels caches. Out of scope for the single-process tool;
   would need a shared cache (e.g. Redis) if it ever scales out.
