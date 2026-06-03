# Site Generator — Backend

FastAPI service that generates webtree builder-compatible page schemas
from a source (URL or document) using a local Ollama model.

## Setup

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8001
```

Server runs on `http://localhost:8001`. Health check: `GET /health`.
Ollama reachability: `GET /health/ollama`.

## Configuration

Override defaults via env vars or a `.env` file:

| Variable | Default | Notes |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | |
| `OLLAMA_MODEL` | `qwen2.5:7b-instruct` | Recommended for M1 16GB |
| `OLLAMA_MODEL_QUALITY` | `qwen2.5:14b-instruct` | Optional quality mode |
| `OLLAMA_TIMEOUT_SECONDS` | `180` | |
| `CMS_API_BASE_URL` | `http://localhost:8000` | webtree CMS API |

## Endpoints (Phase 1)

- `GET /health` — service heartbeat
- `GET /health/ollama` — Ollama reachability + installed models
- `POST /api/generate/from-source` — accepts `SourceContent`, returns `GeneratedSite`
- `POST /api/generate/plan-only` — debug: returns raw `SitePlan`

Phases 2/3 will add `/api/generate/from-url` and `/api/generate/from-document`.
Phase 4 adds the CMS push endpoint.

## Architecture

```
URL or Document
   │
   ▼
extractor          (Playwright / pdfminer / python-docx) — Phase 2/3
   │
   ▼
SourceContent      (normalized text + headings + images)
   │
   ▼
planner.py + LLM   (Ollama qwen2.5 → SitePlan JSON)
   │
   ▼
schema_builder.py  (deterministic: ContentBlock → BuilderElement tree)
   │
   ▼
GeneratedSite      (matches webtree BuilderElement schema)
   │
   ▼
cms_client.py      (POST to CMS API) — Phase 4
```

The LLM produces **semantic blocks** (hero, features, cta, …), not raw
BuilderElement trees. The mapping to BuilderElement is deterministic and
lives in `services/schema_builder.py`, mirroring
`webtree/builder/src/lib/body-section-templates.ts`.
