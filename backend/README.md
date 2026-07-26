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
LLM status: `GET /health/llm` (`/health/ollama` and `/health/mlx` are aliases).

## Where the model comes from

The backend does not manage models. It talks to **one URL** (`LLM_BASE_URL`) and
reads the model id from `/v1/models` at runtime, so it has no notion of which
engine is behind it — Ollama, llama.cpp's llama-server, `mlx_lm.server`,
LM Studio and vLLM all serve the same OpenAI-compatible API.

Everything else — engine, model, quantization, context length, keep-alive, GPU
offload, Tailscale identity — lives in `ai-server/`, an independent Compose
project with its own lifecycle:

```bash
./ai-server/ai.sh up       # start whatever ai-server/.env selects
./ai-server/ai.sh status
```

Swapping the model is one line in `ai-server/.env` and needs no change here and
no restart. See [ai-server/README.md](../ai-server/README.md).

## Configuration

Override defaults via env vars or a `.env` file:

| Variable | Default | Notes |
|---|---|---|
| `LLM_BASE_URL` | `http://host.docker.internal:11434` | The AI server. Server **root**, no `/v1` suffix |
| `LLM_MODEL` | unset | Normally unset — discovered from `/v1/models`. Set only to pin one of several |
| `LLM_API_KEY` | unset | Bearer auth. Ollama has none (use Tailscale ACLs); llama-server has `--api-key` |
| `LLM_MAX_TOKENS` | `16384` | Output budget (OpenAI servers cap low by default) |
| `LLM_REPETITION_PENALTY` | `1.1` | `mlx_lm.server` defaults this to 0.0 (off); without it a small model can loop re-emitting the same JSON fragment until it burns the whole token budget. Matches Ollama's default; `0` restores the server default |
| `LLM_CONTEXT_TOKENS` | `16384` | The batcher's view of the server's context window — **not** sent to the model. Keep in step with `LLM_CTX` in `ai-server/.env` |
| `LLM_VISION_MODEL` | unset | Opt-in multimodal model that captions/classifies scraped images for better slot matching. Unset ⇒ pass skipped |
| `LLM_VISION_BASE_URL` | unset | Only when the vision model is on a different endpoint |
| `REASONING_BASE_URL` | unset | Second endpoint for the judgment-heavy calls (brand detection, design language + recipe, image judge). Unset ⇒ role disabled |
| `REASONING_MODEL` | unset | Only needed to pin one of several models on that endpoint |
| `REASONING_API_KEY` / `REASONING_THINK` | unset / `true` | Bearer auth; thinking mode for the reasoning role (kill switch: `false`) |
| `DESIGN_LANGUAGE_ENABLED` | `true` | LLM picks curated palette + font pairing pre-theme; `false` ⇒ deterministic theming |
| `VISION_MAX_IMAGES` | `12` | Vision annotation cap per generation |
| `PEXELS_API_KEY` | unset | Free key at pexels.com/api — stock photo fallback (Picsum without it) |
| `CMS_API_BASE_URL` | `http://localhost:8000` | webtree CMS API |

## Endpoints (Phase 1)

- `GET /health` — service heartbeat
- `GET /health/llm` — AI server reachability + the models it advertises, plus the reasoning role when configured. `/health/ollama` and `/health/mlx` are aliases of it
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
planner.py + LLM   (MLX or Ollama → SitePlan JSON)
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
