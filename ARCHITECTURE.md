# Architecture

The Webtree Site Generator turns an existing website (scraped) or an uploaded
document into a multi-page site that matches the webtree builder's
`BuilderElement` schema 1:1, then optionally pushes it to the webtree CMS. It
runs entirely against a **local** LLM (Ollama or an OpenAI-compatible MLX/vLLM
server) — no cloud LLM calls.

## Services & layout

```
site-generator/
├── backend/            FastAPI app (Python 3.11, async)
│   ├── app/
│   │   ├── config.py           single pydantic-settings source of truth
│   │   ├── main.py             app wiring + lifespan (startup/shutdown)
│   │   ├── models/             Pydantic: builder_schema, content_blocks, brand, industry
│   │   ├── routers/            health, brand, scrape, document, pages, generate, cms, preview
│   │   └── services/           domain logic (~60 modules — see below)
│   ├── tests/                  pytest suite (659 tests) + conftest.py
│   ├── requirements.txt        runtime deps
│   └── requirements-dev.txt    test-only deps (pytest, pytest-asyncio)
├── frontend/           React 18 + Vite + Tailwind (same-origin via Vite proxy)
├── ai-server/          optional remote GPU LLM host (llama.cpp over Tailscale)
└── docker-compose.yml  backend + frontend dev services
```

## Generation pipeline

```
URL or Document
   │  scraper.py (httpx fast-path → Playwright fallback) / doc_parser.py
   ▼
SourceContent            normalized text + headings + image metadata
   │  planner.py + LLM    brand detection → scaffolded content blocks
   ▼
SitePlan (ContentBlocks) semantic blocks: hero / features / cta / team / …
   │  schema_builder.py   deterministic map → BuilderElement tree
   │    + style_tokens.py (pure CSS/style helpers)
   │    + section_content.py / hero_director.py / theme.py / design_brain.py
   ▼
GeneratedSite            pages match the webtree builder schema 1:1
   │  push_orchestrator.py + cms_client.py
   ▼
webtree CMS              pages, media, menus, header/footer, styles
```

**Two-stage LLM design.** The LLM produces small, stable *semantic blocks*, not
deeply-nested `BuilderElement` JSON (7–9B models drift on long schemas). The
deterministic mapper in `schema_builder.py` owns all styles/layout, so the look
stays consistent across model swaps and is unit-testable as pure functions.

### Notable service groups
- **Scraping:** `scraper.py`, `fast_fetch.py` (httpx-first), `sitemap.py`,
  `polite.py`, `nav_extraction.py`, `crawl_orchestrator.py` + `crawl_jobs.py`
  (durable, cancellable background crawls), `url_guard.py` (SSRF guard).
- **Rendering:** `schema_builder.py` (tree assembly) + `style_tokens.py` (pure
  style helpers), `section_content.py`, `hero_director.py`, `theme.py`,
  `template_filler.py`, `header_footer.py`, `menu_builder.py`.
- **Imagery:** `media.py` (resolver), `image_match/evidence/refs/styling/vision`,
  `pexels.py` (stock fallback).
- **LLM:** `llm.py` (Ollama + MLX clients behind one protocol, validate-or-repair
  retry, response cache), `design_brain.py`, `planner.py`.
- **Design engine:** `design_director.py` (composes the `DesignManifest` —
  chrome archetypes + decision log), `diversity.py` (SQLite usage history that
  steers consecutive sites apart), `header_footer.py` (5 header + 4 footer
  archetypes). See [docs/DESIGN_ENGINE.md](docs/DESIGN_ENGINE.md).
- **CMS push:** `push_orchestrator.py`, `cms_client.py`, `content_collections.py`.

## Configuration flow

Everything configurable lives in **`backend/app/config.py`** as one
`pydantic-settings` `Settings` class, instantiated once as `settings`. Values
load from environment variables (upper-cased field name) or `.env`; no module
reads `os.environ` directly. See [CONFIGURATION.md](CONFIGURATION.md).

- Frontend reads **no** `import.meta.env` at runtime — it talks to the backend
  over relative paths that Vite proxies (`BACKEND_URL`, build-time only).
- Container networking rewrites (`OLLAMA_BASE_URL` etc. → `host.docker.internal`)
  live in `docker-compose.yml`, layered over `.env`.

## Data layer

`services/db.py` — raw SQLite via `aiosqlite` (WAL, `foreign_keys=ON`), used
**only** for durable crawl-job state (`crawl_jobs`, `crawl_pages`). The
generation pipeline itself is stateless. SQLite is a deliberate single-tenant
choice; the DSN is swappable if the tool ever goes multi-process.

## Running & testing

- **Run:** `docker compose up --build` → frontend on `:5174`, backend on `:8001`.
  Ollama runs on the host (`:11434`); see [README.md](README.md).
- **Test:** `backend/scripts/run-tests.sh` (copies the suite + pytest into the
  running container and runs it — the image ships only `app/`). No local Python
  toolchain is required.
