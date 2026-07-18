# Configuration

All configuration flows through a single source of truth:
[`backend/app/config.py`](backend/app/config.py) — a `pydantic-settings`
`Settings` class. Every value below is set via an environment variable (the
upper-cased field name) or a `.env` file at the repo root. No other module reads
`os.environ` directly.

- **Required:** none. Every value has a working local default. `PEXELS_API_KEY`
  is *recommended* for real photos (falls back to gradient placeholders).
- **Secrets** (`PEXELS_API_KEY`, `REASONING_API_KEY`): keep them in `.env` only
  — `.env` is gitignored and must stay untracked. See [SECURITY.md](SECURITY.md).
- See [`.env.example`](.env.example) for a heavily-commented catalogue.

## LLM backend selection & clients

| Variable | Default | Description |
|---|---|---|
| `LLM_BACKEND` | `ollama` | `ollama` (native `/api/chat`) or `mlx` (OpenAI-compatible `/v1/chat/completions`). |
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama server. In Docker, rewritten to `host.docker.internal` by compose. |
| `OLLAMA_MODEL` | `qwen3.6:35b-a3b` | Model for content + design-brain calls. |
| `OLLAMA_TIMEOUT_SECONDS` | `180` | Per-read (streaming) timeout. |
| `OLLAMA_KEEP_ALIVE` | `30m` | How long Ollama keeps the model resident between calls. |
| `MLX_BASE_URL` | `http://localhost:8080` | OpenAI-compatible server (mlx_lm / llama-server / vLLM). |
| `MLX_MODEL` | `mlx-community/Qwen3.5-2B-OptiQ-4bit` | Must match the id on `/v1/models`. |
| `MLX_TIMEOUT_SECONDS` | `600` | Generous: covers cold time-to-first-token. |
| `MLX_MAX_TOKENS` | `8192` | Output budget (OpenAI servers default too low). |
| `MLX_VISION_BASE_URL` / `MLX_VISION_MODEL` | `None` | Optional MLX vision server. |

## Reasoning role (optional second model)

Routes judgment-heavy calls (brand detection, design brain, image tie-break) to a
bigger/remote model (e.g. GLM on the AI server). Unset `REASONING_MODEL` ⇒ role
disabled (those calls use the default model).

| Variable | Default | Description |
|---|---|---|
| `REASONING_BACKEND` | `None` | `mlx`/`ollama`; `None` inherits `LLM_BACKEND`. |
| `REASONING_BASE_URL` | `None` | `None` ⇒ backend default. |
| `REASONING_MODEL` | `None` | e.g. `glm-z1:9b`; `None` disables the role. |
| `REASONING_API_KEY` | `None` | **Secret.** Sent as `Authorization: Bearer …`. |
| `REASONING_TIMEOUT_SECONDS` | `None` | `None` ⇒ backend default. |
| `REASONING_MAX_TOKENS` | `16384` | Higher — thinking tokens count against it. |
| `REASONING_NUM_CTX` | `None` | Ollama path only. |
| `REASONING_THINK` | `true` | Thinking on by default for this role. |

## LLM tuning, caching & batching

| Variable | Default | Description |
|---|---|---|
| `LLM_DEFAULT_TEMPERATURE` | `0.4` | Fallback when a call site passes none. |
| `LLM_DEFAULT_NUM_CTX` | `4096` | Default context window. |
| `LLM_THINK` | `false` | Thinking off for JSON calls (avoids budget burn). |
| `LLM_CACHE_ENABLED` | `true` | In-process TTL/LRU cache over validated responses. |
| `LLM_CACHE_TTL_SECONDS` | `1800` | Cache TTL. |
| `LLM_CACHE_MAX_ENTRIES` | `64` | Cache size. |
| `SCRAPE_CACHE_TTL_SECONDS` | `1800` | Scrape-preview cache TTL (covers an editing session). |
| `PLAN_TEMPERATURE` | `0.3` | Brand detection / legacy planner. |
| `SCAFFOLD_TEMPERATURE` | `0.25` | Scaffolded content (stay close to source). |
| `SCAFFOLD_NUM_CTX` | `8192` | Context for content-generation calls. |
| `DESIGN_TEMPERATURE` | `0.7` | Design-brain (bolder, enum-constrained). |
| `DESIGN_NUM_CTX` | `8192` | Design-brain context. |
| `JUDGE_TEMPERATURE` | `0.0` | Deterministic judge calls. |
| `JUDGE_NUM_CTX` | `2048` | Judge context. |
| `BRAND_DETECTION_MAX_CHARS` | `4000` | Source chars for brand detection. |
| `MULTIPASS_MAX_CHARS_PER_CALL` | `6000` | Chunk size for oversized pages. |
| `MAX_SECTIONS_PER_BATCH` | `6` | Cap per scaffolded batch. |
| `MAX_PAGES_PER_BATCH` | `4` | Cap per scaffolded batch. |
| `SCAFFOLD_BATCH_CONCURRENCY` | `1` | Raise only for a backend that serves parallel requests. |
| `LEGACY_PROMPT_MAX_CHARS` | `24000` | Cap for the legacy free-form planner. |

## Design & layout feature flags

| Variable | Default | Description |
|---|---|---|
| `DESIGN_BRAIN_ENABLED` | `true` | LLM per-section template variety (safe no-op off). |
| `DESIGN_LANGUAGE_ENABLED` | `true` | LLM palette/font pairing pass. |
| `HERO_FULLBLEED_ALL_PAGES` | `true` | Full-bleed hero on every page. |
| `HERO_MIN_BACKGROUND_DIM` | `1200` | Min long-edge (px) for a scraped hero background. |
| `HERO_BG_MIN_ASPECT` | `1.2` | Min width/height for a scraped hero background. |
| `SECTION_MIN_BACKGROUND_DIM` | `800` | Min long-edge (px) for a scraped section background. |
| `HEADER_OVERLAY_ENABLED` | `true` | Transparent header over full-bleed heroes. |
| `HEADER_SCROLL_REVEAL_OFFSET` | `80` | Px scrolled before the header solidifies. |
| `HEADER_SHRINK_ENABLED` | `true` | Header shrinks on scroll. |
| `HEADER_SHRINK_AMOUNT` | `80` | Percent of original size when shrunk. |
| `LUMINANCE_RHYTHM_ENABLED` | `true` | Light/dark section-band rhythm. |

## Vision, imagery & content migration

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_VISION_MODEL` | `None` | Multimodal model to caption/classify scraped images. |
| `VISION_MAX_IMAGES` | `12` | Annotation cap per generation. |
| `VISION_IMAGE_MAX_BYTES` | `4000000` | Skip larger downloads. |
| `VISION_FETCH_TIMEOUT_SECONDS` | `8.0` | Image fetch timeout. |
| `PEXELS_API_KEY` | `None` | **Secret.** Free key for topical stock photos. |
| `PEXELS_BASE_URL` | `https://api.pexels.com/v1` | Pexels API base. |
| `PEXELS_TIMEOUT_SECONDS` | `10.0` | Pexels request timeout. |
| `PEXELS_CACHE_SIZE` | `256` | Per-query result cache size. |
| `CONTENT_MIGRATION_ENABLED` | `true` | Migrate blog/event listings as CMS entries. |
| `CONTENT_MIGRATION_MAX_ENTRIES` | `12` | Cap on migrated entries. |

## Network, security & infrastructure

| Variable | Default | Description |
|---|---|---|
| `SCRAPE_ALLOW_PRIVATE_HOSTS` | `false` | **Security.** `true` disables the SSRF guard for localhost/LAN scraping (dev only). See [SECURITY.md](SECURITY.md). |
| `HTTP_USER_AGENT` | *(Chrome UA)* | Shared UA for httpx + Playwright fetches. |
| `FAST_FETCH_TIMEOUT_SECONDS` | `8.0` | httpx fast-path timeout. |
| `ROBOTS_FETCH_TIMEOUT_SECONDS` | `10.0` | robots/sitemap/logo fetch timeout. |
| `PLAYWRIGHT_GOTO_TIMEOUT_MS` | `15000` | Playwright navigation timeout. |
| `CMS_API_BASE_URL` | `http://localhost:8000` | webtree CMS base. Compose rewrites to host. |
| `CMS_TIMEOUT_SECONDS` | `30.0` | CMS API timeout. |
| `CMS_MEDIA_UPLOAD_TIMEOUT_SECONDS` | `120.0` | CMS media-upload timeout. |
| `SITEGEN_DB_PATH` | `/app/data/sitegen.db` | SQLite file for crawl-job state. |
| `CORS_ORIGINS` | `localhost:5173/5174, 127.0.0.1:5173` | Allowed browser origins. |
