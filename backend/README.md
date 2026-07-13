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
LLM backend status: `GET /health/llm`. Backend reachability: `GET /health/ollama`, `GET /health/mlx`.

## LLM backends (MLX vs Ollama)

Generation runs on one of two interchangeable backends, chosen explicitly by
`LLM_BACKEND` in `.env` (no auto-detection):

- **`ollama`** (default) — Ollama at `OLLAMA_BASE_URL`.
- **`mlx`** — an MLX server on the Apple-Silicon host (set this in `.env` on a Mac
  running mlx_lm.server).

MLX can't run inside the Linux backend container, so it's an **OpenAI-compatible
server on the Apple-Silicon host** that the app calls over HTTP (same pattern as
Ollama). On the host:

```bash
pip install mlx-lm
mlx_lm.server --model mlx-community/Qwen3-8B-4bit --port 8080
# optional vision pass:
pip install mlx-vlm
mlx_vlm.server --model mlx-community/Qwen2.5-VL-7B-Instruct-4bit --port 8081
```

Then run the app (native or Docker). `GET /health/llm` reports the active backend.

## Configuration

Override defaults via env vars or a `.env` file:

| Variable | Default | Notes |
|---|---|---|
| `LLM_BACKEND` | `ollama` | `mlx` \| `ollama` — set in `.env`; see above |
| `MLX_BASE_URL` | `http://localhost:8080` | mlx_lm.server (OpenAI API). In Docker: `http://host.docker.internal:8080` |
| `MLX_MODEL` | `mlx-community/Qwen3-8B-4bit` | HuggingFace repo id |
| `MLX_MAX_TOKENS` | `8192` | Output budget (OpenAI servers cap low by default) |
| `MLX_VISION_BASE_URL` | unset | mlx_vlm.server URL; unset ⇒ falls back to `MLX_BASE_URL` |
| `MLX_VISION_MODEL` | unset | Opt-in MLX vision model; unset ⇒ vision pass skipped under MLX |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | May point at a remote Ollama (AI server); compose passes a set value through |
| `OLLAMA_MODEL` | `qwen3.5:9b` | Single resident model; fits 16GB M1 with headroom |
| `OLLAMA_MODEL_QUALITY` | `qwen2.5:14b-instruct` | Optional quality mode |
| `OLLAMA_TIMEOUT_SECONDS` | `180` | |
| `REASONING_MODEL` | unset | Second model for the judgment-heavy calls (brand detection, design language + recipe, image judge), e.g. GLM on the AI server. Unset ⇒ role disabled, default model used |
| `REASONING_BACKEND` / `REASONING_BASE_URL` | inherit / unset | `mlx` = any OpenAI-compatible server, `ollama` = Ollama API; base URL of the reasoning server |
| `REASONING_API_KEY` / `REASONING_THINK` | unset / `true` | Bearer auth; thinking mode for the reasoning role (kill switch: `false`) |
| `DESIGN_LANGUAGE_ENABLED` | `true` | LLM picks curated palette + font pairing pre-theme; `false` ⇒ deterministic theming |
| `OLLAMA_VISION_MODEL` | unset | Opt-in: multimodal model (e.g. `qwen2.5vl:7b`, `moondream`) that captions/classifies scraped images for better slot matching + profile verification. Unset ⇒ pass skipped |
| `VISION_MAX_IMAGES` | `12` | Vision annotation cap per generation |
| `PEXELS_API_KEY` | unset | Free key at pexels.com/api — stock photo fallback (Picsum without it) |
| `CMS_API_BASE_URL` | `http://localhost:8000` | webtree CMS API |

## Endpoints (Phase 1)

- `GET /health` — service heartbeat
- `GET /health/llm` — active LLM backend (mlx|ollama) + default model, plus the reasoning role (backend/model/URL/think) when `REASONING_MODEL` is set
- `GET /health/ollama` — Ollama reachability + installed models
- `GET /health/mlx` — MLX server reachability + loaded models
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
