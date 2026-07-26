# Webtree Site Generator

AI-powered website generator that produces pages compatible with the webtree
builder schema. Runs entirely on a locally served model (no cloud LLM calls).

- **Option 1 — Scrape a URL**: pull content from an existing site, rewrite for
  conversion, optimize SEO, add CTAs, and emit a multi-page site.
- **Option 2 — Upload a document**: turn a PDF or Word file into a structured site.
- Output matches the `BuilderElement` schema 1:1 — every generated page opens
  natively in the webtree builder for further editing.

---

## How to start the app

> **Where the model runs:** not in this stack. `ai-server/` is a separate Compose
> project that owns the model — engine, weights, GPU offload, everything. The app
> talks to it over one URL (`LLM_BASE_URL`) and reads the model id from
> `/v1/models`, so swapping models is one line in `ai-server/.env` with no app
> restart. It can run on this machine or on a different box over Tailscale.

### Step 0 — One-time setup: start the AI server

```bash
cd site-generator/ai-server
cp .env.example .env     # LLM_ENGINE=ollama and a model tag are already set
./ai.sh up               # first run downloads the weights
./ai.sh status
```

Pick the engine in `ai-server/.env`: `ollama` (default — easiest model swapping),
`llamacpp` (fastest for a big MoE on a small GPU), `mlx` (Apple Silicon, runs as
a host process), or `external` (something already running elsewhere). GPU
prerequisites, the model swap table, tuning and the Tailscale setup are all in
[ai-server/README.md](ai-server/README.md).

### Step 0.5 — Get a Pexels API key (free, instant)

For real, topical photos in generated sites:

1. Sign up at https://www.pexels.com/api/ (no credit card)
2. Copy your API key
3. Put it in `.env` as `PEXELS_API_KEY=...`

The generator still works without a key — it falls back to Picsum random placeholders.
The status badge in the header shows whether Pexels is active.

---

### Path A — Docker Compose (recommended)

**Prereqs:** Docker Desktop running, plus Step 0 above.

```bash
cd site-generator

# (optional) copy env defaults so you can tweak model / timeout
cp .env.example .env

# Build + start both services
docker compose up --build
```

Wait for these lines:
- `webtree-sitegen-backend  | Uvicorn running on http://0.0.0.0:8001`
- `webtree-sitegen-frontend | Local:   http://localhost:5174/`

Then open **http://localhost:5174**. The header should say "Ollama OK · N models installed". Paste some page text into the form and click **Generate website**.

**Common commands:**
```bash
docker compose logs -f backend          # tail backend logs
docker compose logs -f frontend         # tail frontend logs
docker compose restart backend          # restart after edits to requirements.txt
docker compose down                     # stop everything
docker compose down -v                  # stop + wipe node_modules volume
```

Code edits to `backend/app/**` or `frontend/src/**` hot-reload automatically. Dependency changes (requirements.txt / package.json) require a rebuild: `docker compose up --build`.

---

### Path B — Manual (no Docker)

**Prereqs:** Python 3.11+, Node 20+, Step 0 above.

```bash
# 1. Backend
cd site-generator/backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8001
```

In a second terminal:
```bash
# 2. Frontend
cd site-generator/frontend
npm install
npm run dev
```

Open **http://localhost:5174**.

---

### Verify everything is wired up

```bash
# backend health
curl http://localhost:8001/health
# → {"status":"ok"}

# backend → AI server reachability (lists what it advertises)
curl http://localhost:8001/health/llm
# → {"status":"ok","models":["qwen3.6:35b-a3b"],"model":"qwen3.6:35b-a3b", …}
```

If it returns `"unreachable"`, the AI server isn't up: `./ai-server/ai.sh status`.
If `models` is empty, no model is loaded yet: `./ai-server/ai.sh pull`.

---

## Troubleshooting

| Symptom | Cause / Fix |
|---|---|
| Header says "AI server unreachable" | The AI server isn't up. `./ai-server/ai.sh status`, then `./ai-server/ai.sh up`. |
| `host.docker.internal: name does not resolve` | You're on Linux. The compose file already maps this to `host-gateway`, so it should work — restart compose: `docker compose down && docker compose up`. |
| Frontend won't hot-reload inside Docker | Polling is enabled in `vite.config.ts` but file events from macOS bind mounts can still be slow. As a last resort, `docker compose restart frontend`. |
| "advertises no models on /v1/models" | Nothing loaded yet. `./ai-server/ai.sh pull` (or `up`, which pulls when missing). |
| Generation truncates on content-rich sites | Raise `LLM_CTX` in `ai-server/.env` **and** `LLM_CONTEXT_TOKENS` in `.env` together — the first sizes the server, the second sizes the batcher. |
| Generation takes >60s on first request | Cold model load is normal — weights page into VRAM on first call. Subsequent calls are fast (`LLM_KEEP_ALIVE` in `ai-server/.env` keeps it resident). |
| Backend port already in use | Another service is on 8001. Either stop it, or change `ports` in `docker-compose.yml`. |

---

## What's built

All five original phases are implemented.

| Phase | Status | Scope |
|---|---|---|
| **1** | ✅ done | Backend + frontend scaffold, Pydantic schema mirroring `BuilderElement`, Ollama/MLX client, semantic-blocks → BuilderElement mapper, paste-input pipeline, Docker compose |
| **2** | ✅ done | Playwright URL scraping (httpx fast-path + fallback), same-domain crawl, extracted-content preview |
| **3** | ✅ done | PDF/DOCX upload + parser (`doc_parser.py`, PyMuPDF) |
| **4** | ✅ done | CMS API push (create page → media → menus → header/footer → styles → publish) |
| **5** | ✅ done | Iframe visual preview + per-page layout |

## Project docs

| Doc | What's in it |
|---|---|
| [ARCHITECTURE.md](ARCHITECTURE.md) | Services, generation pipeline, config flow, data layer |
| [CONFIGURATION.md](CONFIGURATION.md) | Every environment variable, default, and description |
| [SECURITY.md](SECURITY.md) | Trust model, SSRF guard, risks + fixes, remaining recommendations |
| [PERFORMANCE.md](PERFORMANCE.md) | Optimizations in place and remaining bottlenecks |

**Running tests:** `backend/scripts/run-tests.sh` (runs the suite inside the
container — no local Python needed). Design history lives in `docs/archive/`.

---

## Architecture

```
URL or Document
   │
   ▼
extractor          (Playwright / pdfminer / python-docx)
   │
   ▼
SourceContent      normalized text + headings + images
   │
   ▼
planner.py + LLM   Ollama qwen2.5 → SitePlan JSON
   │                (semantic content blocks: hero/features/cta/…)
   ▼
schema_builder.py  deterministic mapping → BuilderElement tree
   │                (mirrors webtree/builder body-section-templates.ts)
   ▼
GeneratedSite      pages match webtree builder schema 1:1
   │
   ▼
cms_client.py      POST to webtree CMS API (Phase 4)
```

**Two-stage LLM design.** We don't ask the LLM for `BuilderElement` JSON directly:
- 7B models drift on long, deeply-nested schemas. Semantic blocks
  (`hero`, `features`, `cta`, …) are small and stable.
- The deterministic mapper in `services/schema_builder.py` owns styles, layout,
  and responsive variants — they stay consistent across model upgrades.
- Easier to test: blocks are plain Pydantic models, mapper is pure functions.

## Schema compatibility

The Pydantic `BuilderElement` in
[backend/app/models/builder_schema.py](backend/app/models/builder_schema.py)
mirrors the TS type in `webtree/builder/src/lib/site-navigation.ts`. **Any drift
between these two files will break editor compatibility.** When the builder
schema changes, update both files together.
