# Configuration

All configuration flows through a single source of truth:
[`backend/app/config.py`](backend/app/config.py) — a `pydantic-settings`
`Settings` class. Every value below is set via an environment variable (the
upper-cased field name) or a `.env` file at the repo root. No other module reads
`os.environ` directly.

- **Required:** none. Every value has a working local default. `PEXELS_API_KEY`
  is *recommended* for real photos (falls back to gradient placeholders).
- **Secrets** (`PEXELS_API_KEY`, `LLM_API_KEY`, `REASONING_API_KEY`): keep them in `.env` only
  — `.env` is gitignored and must stay untracked. See [SECURITY.md](SECURITY.md).
- See [`.env.example`](.env.example) for a heavily-commented catalogue.

## LLM connection

Model **management** is not configured here. Which engine runs (Ollama /
llama.cpp / MLX / a remote box), which model it loads, its quantization, context
length, keep-alive and GPU offload all live in `ai-server/.env` — see
[ai-server/README.md](ai-server/README.md). The backend holds a URL, because
every engine serves the same OpenAI-compatible API.

| Variable | Default | Description |
|---|---|---|
| `LLM_BASE_URL` | `http://host.docker.internal:11434` | The AI server. Server **root**, no `/v1` suffix. A tailnet address for a remote box works unchanged. |
| `LLM_MODEL` | `None` | Normally unset — the id is read from `/v1/models` at runtime, so a model swap needs no change here and no restart. Set it only to pin one when a server advertises several. |
| `LLM_API_KEY` | `None` | **Secret.** Sent as `Authorization: Bearer …`. Ollama has no auth (use Tailscale ACLs); llama-server has `--api-key`. |
| `LLM_TIMEOUT_SECONDS` | `600` | Per-read (between-token) timeout; covers cold time-to-first-token. |
| `LLM_MAX_TOKENS` | `16384` | Output budget, doubled automatically on a genuine truncation. |
| `LLM_REPETITION_PENALTY` | `1.1` | Sampling knob. `mlx_lm.server` defaults it to 0.0 unlike Ollama/llama.cpp; `0.0` restores the server default. |
| `LLM_VISION_MODEL` | `None` | Opt-in vision pass. Unset ⇒ skipped entirely. |
| `LLM_VISION_BASE_URL` | `None` | Only when the vision model is on a **different** endpoint. |

Legacy names still resolve for one release: `MLX_BASE_URL` / `OLLAMA_BASE_URL` →
`LLM_BASE_URL`, `MLX_MODEL` / `OLLAMA_MODEL` → `LLM_MODEL`,
`MLX_TIMEOUT_SECONDS` → `LLM_TIMEOUT_SECONDS`, `MLX_MAX_TOKENS` →
`LLM_MAX_TOKENS`, `SCAFFOLD_NUM_CTX` → `LLM_CONTEXT_TOKENS`. **`LLM_BACKEND` is
gone** — there is no engine to select on this side.

## Reasoning role (optional second endpoint)

Routes judgment-heavy calls (brand detection, design brain, image tie-break) to a
different endpoint while the default one keeps bulk content generation. This is
the one piece of model routing that stays in the backend: which *role* talks to
which endpoint is an application decision, not a serving one. Unset both
`REASONING_BASE_URL` and `REASONING_MODEL` ⇒ role disabled.

| Variable | Default | Description |
|---|---|---|
| `REASONING_BASE_URL` | `None` | A second endpoint. Alone, it is enough — the model id is discovered. |
| `REASONING_MODEL` | `None` | Only needed to pin one of several models. |
| `REASONING_API_KEY` | `None` | **Secret.** Sent as `Authorization: Bearer …`. |
| `REASONING_TIMEOUT_SECONDS` | `None` | `None` ⇒ `LLM_TIMEOUT_SECONDS`. |
| `REASONING_MAX_TOKENS` | `16384` | Higher — thinking tokens count against it. |
| `REASONING_THINK` | `true` | Thinking on by default for this role. |

## LLM tuning, caching & batching

| Variable | Default | Description |
|---|---|---|
| `LLM_DEFAULT_TEMPERATURE` | `0.4` | Fallback when a call site passes none. |
| `LLM_CONTEXT_TOKENS` | `16384` | The server's context window **as far as the batcher is concerned** — not sent to the model (the OpenAI wire has no per-request `num_ctx`). Keep in step with `LLM_CTX` in `ai-server/.env`. |
| `LLM_THINK` | `false` | Thinking off for JSON calls (avoids budget burn). |
| `LLM_CACHE_ENABLED` | `true` | In-process TTL/LRU cache over validated responses. |
| `LLM_CACHE_TTL_SECONDS` | `1800` | Cache TTL. |
| `LLM_CACHE_MAX_ENTRIES` | `64` | Cache size. |
| `SCRAPE_CACHE_TTL_SECONDS` | `1800` | Scrape-preview cache TTL (covers an editing session). |
| `PLAN_TEMPERATURE` | `0.3` | Brand detection / legacy planner. |
| `SCAFFOLD_TEMPERATURE` | `0.25` | Scaffolded content (stay close to source). |
| `DESIGN_TEMPERATURE` | `0.7` | Design-brain (bolder, enum-constrained). |
| `JUDGE_TEMPERATURE` | `0.0` | Deterministic judge calls. |
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
| `DESIGN_ENGINE_ENABLED` | `true` | Design director: varies header/footer chrome archetypes per brand (off → legacy classic/mega chrome). |
| `DIVERSITY_ENGINE_ENABLED` | `true` | SQLite usage history steering consecutive sites away from repeating chrome picks (off → pure seeded rotation). |
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

## Facebook Page reading

A Facebook URL pasted into the normal link field is routed to the Facebook
reader rather than the crawler (`services/source_detect.py`). Graph API first
when a token is available, a public-page render otherwise.

| Variable | Default | Description |
|---|---|---|
| `FACEBOOK_ACCESS_TOKEN` | `None` | **Secret.** Page access token. Unlocks emails, structured hours, posts and recommendations, and avoids the login wall. A per-request token from the UI overrides it and is held in memory only — never written to the jobs table or logged. |
| `FACEBOOK_GRAPH_BASE_URL` | `https://graph.facebook.com` | Graph API host. |
| `FACEBOOK_GRAPH_VERSION` | `v21.0` | Graph API version. |
| `FACEBOOK_TIMEOUT_SECONDS` | `15.0` | Graph request / public render timeout. |
| `FACEBOOK_MAX_POSTS` | `25` | Posts read for grounding text and photos. |
| `FACEBOOK_MAX_REVIEWS` | `12` | Recommendations read as testimonials. |
| `FACEBOOK_MIN_RAW_TEXT_CHARS` | `200` | Below this, a Page is refused (422) rather than padded into a site. Higher than the document path's 80 because a Page always yields a name plus a category. |
| `FACEBOOK_RENDER_FALLBACK_ENABLED` | `true` | `false` requires a token instead of rendering the public Page. |
| `FACEBOOK_LOGO_VISION_CHECK` | `true` | Ask the vision judge whether the profile picture is a mark or a photograph; a photograph is demoted to palette-only so the header falls back to the wordmark. No-op unless `OLLAMA_VISION_MODEL` is set. |

## Network, security & infrastructure

| Variable | Default | Description |
|---|---|---|
| `DEPLOYMENT` | `local` | **Where this generator runs** (not where a push lands — that's `CMS_REMOTE_*`). `hosted` makes `app/deployment/guards.py` refuse to start on settings that are only safe on a laptop. Inert under `local`, so the default is a true no-op. |
| `DEPLOYMENT_AUTH` | `none` | This app has no authentication. Under `DEPLOYMENT=hosted`, `none` is **refused at startup** — set `proxy` to attest that an authenticating reverse proxy fronts it. |
| `SCRAPE_ALLOW_PRIVATE_HOSTS` | `false` | **Security.** `true` disables the SSRF guard for localhost/LAN scraping (dev only). Refused under `DEPLOYMENT=hosted`. See [SECURITY.md](SECURITY.md). |
| `HTTP_USER_AGENT` | *(Chrome UA)* | Shared UA for httpx + Playwright fetches. |
| `FAST_FETCH_TIMEOUT_SECONDS` | `8.0` | httpx fast-path timeout. |
| `ROBOTS_FETCH_TIMEOUT_SECONDS` | `10.0` | robots/sitemap/logo fetch timeout. |
| `PLAYWRIGHT_GOTO_TIMEOUT_MS` | `15000` | Playwright navigation timeout. |
| `CMS_API_BASE_URL` | `http://localhost:8000` | webtree CMS base — the **default** push target. Compose rewrites to host. |
| `CMS_REMOTE_API_BASE_URL` | `None` | A **second** CMS, chosen per push from a picker in the publish drawer, so a local generator can push into a live CMS without a restart. Must be the *admin API* origin. Unset ⇒ one target and no picker. |
| `CMS_REMOTE_ADMIN_BASE_URL` | `None` | Admin-suite origin for that second CMS. Drives its "Open in webtree admin" link, so a remote push is never followed by a `localhost` one. |
| `ADMIN_APP_BASE_URL` | `None` | webtree admin suite base (a different app from the CMS API). Set it and `POST /api/cms/push` returns an `admin_url` the UI renders as an "Open in webtree admin" link after a successful push. Unset ⇒ no link. |
| `CMS_TIMEOUT_SECONDS` | `30.0` | CMS API timeout. |
| `CMS_MEDIA_UPLOAD_TIMEOUT_SECONDS` | `120.0` | CMS media-upload timeout. |
| `SITEGEN_DB_PATH` | `/app/data/sitegen.db` | SQLite file for crawl-job state. |
| `CORS_ORIGINS` | `localhost:5173/5174, 127.0.0.1:5173` | Allowed browser origins. |
