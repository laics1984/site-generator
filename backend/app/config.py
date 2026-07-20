from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Which LLM backend serves chat_json calls. Set via LLM_BACKEND in .env:
    # `mlx` for any OpenAI-compatible /v1/chat/completions server (mlx_lm.server
    # on the Mac host, or llama-server on the remote RTX AI box — see ai-server/),
    # `ollama` for Ollama's native /api/chat. See services/llm.resolve_llm_backend.
    llm_backend: Literal["mlx", "ollama"] = "ollama"

    # "mlx" backend = OpenAI-compatible server. Local default is mlx_lm.server on
    # the Apple-Silicon host (Docker can't run MLX, so the container reaches it
    # over host.docker.internal — see docker-compose.yml); point MLX_BASE_URL at a
    # Tailscale IP to use the remote AI server instead. mlx_model must match the
    # id the server exposes on /v1/models: a HuggingFace repo id for mlx_lm.server,
    # the --alias for llama-server.
    mlx_base_url: str = "http://localhost:8080"
    mlx_model: str = "mlx-community/Qwen3.5-2B-OptiQ-4bit"
    # Generous because this is a per-read (streaming) timeout: once tokens flow
    # each one resets the clock, so it only bites on cold time-to-first-token —
    # which on a memory-constrained Mac can run 1-3 min while the OS pages the
    # model back into unified memory. Too low ⇒ ReadTimeout 502s mid-generation.
    mlx_timeout_seconds: float = 600.0
    # OpenAI servers default to a small max_tokens that would truncate a multi-
    # section generation mid-JSON; set a generous output budget. (Ollama has no
    # equivalent cap — num_predict defaults to unlimited.)
    # 16 384 (raised from 8 192): content-rich sites were hitting the old cap
    # mid-batch and burning an extra retry; 16 384 keeps most generations in one
    # shot while remaining well under the model's 32 768-token context window.
    mlx_max_tokens: int = 16384
    # mlx_lm.server defaults this to 0.0 (disabled) — unlike Ollama, whose
    # repeat_penalty already defaults to 1.1. Without it, a small model can fall
    # into a degenerate loop (e.g. re-emitting the same nested block over and
    # over) that never produces valid JSON and just burns the whole max_tokens
    # budget as a wall of repeated text — the doubled-budget truncation retry
    # (see llm._boost_budget) can't fix that, it only lets the loop run longer
    # before failing again. 1.1 matches Ollama's default. 0.0 restores the
    # server's own default (off).
    mlx_repetition_penalty: float = 1.1
    # Opt-in MLX vision server (mlx_vlm.server). Unset ⇒ the vision pass falls back
    # to Ollama / is skipped, exactly as with ollama_vision_model.
    mlx_vision_base_url: str | None = None
    mlx_vision_model: str | None = None

    ollama_base_url: str = "http://localhost:11434"
    # Single resident model for both content and design-brain calls — picked to
    # fit comfortably in 16GB unified memory (M1) with headroom, so the two
    # passes never fight over which model is loaded. A generation newer than
    # qwen2.5 at the same footprint.
    ollama_model: str = "qwen3.6:35b-a3b" #"qwen3.5:4b|qwen3.6:35b-q4_K_M|qwen3.6:35b-a3b"
    ollama_timeout_seconds: float = 180.0
    # How long Ollama keeps the model resident after a request. The picker flow
    # fires brand detection then (after the user picks pages) generation; the
    # default 5m can unload the model in between, forcing a cold reload that
    # blows the read timeout. Keeping it warm avoids re-paying the load cost.
    ollama_keep_alive: str = "30m"

    # --- Reasoning role: a second, bigger model for the judgment-heavy calls ---
    # Routes brand detection (planner.detect_brand), the design-brain passes
    # (design recipe + design language) and the image tie-break judge
    # (image_match._llm_pick_best) to a remote model — typically GLM on the AI
    # server — while the local default model keeps the bulk content generation.
    # REASONING_MODEL unset ⇒ role disabled: those calls use get_llm() unchanged.
    reasoning_backend: Literal["mlx", "ollama"] | None = None  # None → inherit llm_backend. "mlx" speaks OpenAI-compatible — also use it for vLLM/sglang/llama.cpp servers.
    reasoning_base_url: str | None = None  # None → the chosen backend's default base URL
    reasoning_model: str | None = None  # e.g. "glm-z1:9b"; None → role disabled
    reasoning_api_key: str | None = None  # sent as "Authorization: Bearer …" when set
    reasoning_timeout_seconds: float | None = None  # None → backend default (mlx 600s / ollama 180s)
    # OpenAI-path output budget. Higher than mlx_max_tokens because thinking
    # tokens count against the completion budget on OpenAI-compatible servers.
    reasoning_max_tokens: int = 16384
    # Ollama-path only, and only for calls that pass no num_ctx (detect_brand).
    # The design/judge calls pass DESIGN_NUM_CTX/JUDGE_NUM_CTX explicitly —
    # raise those in .env for a bigger reasoning model.
    reasoning_num_ctx: int | None = None
    # Thinking ON by default for this role: the reasoning calls are small
    # prompts with small JSON outputs, where a thinking pass buys better
    # judgment. REASONING_THINK=false is the kill switch if a model/server
    # combo misbehaves (e.g. thinking output breaking JSON mode).
    reasoning_think: bool = True

    @field_validator(
        "reasoning_backend",
        "reasoning_base_url",
        "reasoning_model",
        "reasoning_api_key",
        mode="before",
    )
    @classmethod
    def _reasoning_empty_str_is_none(cls, v: object) -> object:
        """`REASONING_X=` (empty) in .env means unset, not empty-string — also
        keeps an empty REASONING_BACKEND from failing Literal validation."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # --- LLM tuning: everything you'd retune when swapping model variants ---
    # All of these are env-overridable (upper-cased field name), so moving to a
    # bigger/smaller or thinking/non-thinking model is a .env change, not a code
    # change. Defaults are tuned for a 4-9B instruct model on 16GB unified memory.

    # Client-level fallbacks used whenever a call site doesn't pass its own value.
    llm_default_temperature: float = 0.4
    # Ollama's own default of 2048 is too small for most generation tasks.
    llm_default_num_ctx: int = 4096
    # Hybrid-thinking models (Qwen3/3.5) can emit a `<think>` preamble in a
    # separate channel that burns the token budget before any JSON `content` is
    # produced, so thinking is off by default for the JSON calls. Non-thinking
    # models ignore the field harmlessly. Set LLM_THINK=true for a model that
    # produces better JSON with its reasoning channel enabled.
    llm_think: bool = False

    # --- regeneration caches -------------------------------------------------
    # In-process TTL cache over validated LLM responses (llm.chat_json_cached).
    # Only the deterministic-ish expensive calls opt in (scaffolded content
    # batches, the image tie-break judge) — re-generating an unchanged site
    # within the TTL reuses their results instead of re-paying minutes of GPU
    # time, while the temp-0.7 design passes stay fresh so the look can still
    # vary run to run. Any input change (source text, page selection, prompts,
    # sampling knobs) is a different key and generates fresh.
    llm_cache_enabled: bool = True
    llm_cache_ttl_seconds: int = 1800
    llm_cache_max_entries: int = 64
    # TTL for the scrape-preview cache (routers/scrape.py). 5 minutes routinely
    # expired while the user was still in the page picker, forcing a full
    # re-scrape on regeneration; 30 minutes covers a whole editing session.
    scrape_cache_ttl_seconds: int = 1800

    # Brand detection + the legacy free-form planner: faithful rewrite — keep it
    # close to the source, not creative.
    plan_temperature: float = 0.3
    # Scaffolded content generation: low temperature keeps the rewrite close to
    # the scraped source text.
    scaffold_temperature: float = 0.25
    # Design-brain pass context: its whole-site prompt repeats each kind's option
    # list per page, so give it more room than the default to avoid truncation.
    # (Its temperature is design_temperature below.)
    design_num_ctx: int = 8192
    # Deterministic judge calls (image tie-break in image_match.py, vision
    # annotation in image_vision.py): tiny prompts, want reproducible picks.
    judge_temperature: float = 0.0
    judge_num_ctx: int = 2048

    # Context window for the scaffolded content-generation calls (planner.py).
    # 8192 is the safe default for a 7-9B model on 16GB unified memory. Raising
    # to e.g. 12288 lets the batcher pack more pages per call (fewer prefill
    # passes of the fixed prompt) at ~0.5-1GB extra KV cache — check the
    # "Dynamic batching" log line to confirm the batch count actually drops
    # before paying that memory.
    scaffold_num_ctx: int = 8192

    # Prompt sizing / batching caps, all keyed to the model's usable context.
    # Brand detection only needs enough source to name the business and pick an
    # industry/mood — not the full content-generation budget. This is the FIRST
    # LLM call in the flow, so it also eats any cold model-load; a leaner prompt
    # keeps time-to-first-token (prefill) inside the read timeout. 4k chars fits
    # comfortably inside the default 4096 num_ctx.
    brand_detection_max_chars: int = 4000
    # Chunk size (chars of page text per call) for oversized-page multipass
    # generation; sized so a chunk plus the scaffold prompt fits scaffold_num_ctx.
    multipass_max_chars_per_call: int = 6000
    # Hard caps on how much one scaffolded batch may ask a single call to emit,
    # regardless of the token math — a small model degrades (drops sections,
    # truncates JSON) well before the context window is actually full. With the
    # slimmed system prompt these caps (not tokens) are usually the binding
    # constraint on batch size, so on a larger model raising them here is the
    # lever that genuinely cuts the number of content calls.
    max_sections_per_batch: int = 6
    max_pages_per_batch: int = 4
    # How many scaffold batches may be in flight at once. 1 (default) preserves
    # strictly serial generation — correct for a single local GPU model, where
    # parallel requests just queue and slow each other down. Raise it only when
    # the LLM backend genuinely serves parallel requests (vLLM/llama-server on a
    # big card, a hosted API); batches are grouped by page depth either way so
    # child pages still see their parent's hero context.
    scaffold_batch_concurrency: int = 1

    # Char cap on the raw source text sent to the LEGACY free-form planner
    # (planner._build_user_prompt, the /from-source path). The old hardcoded
    # 12 000 silently dropped everything past the first few sections of a rich
    # page; the scaffolded path chunks instead of truncating, so this only
    # bounds the legacy single-call prompt.
    legacy_prompt_max_chars: int = 24000

    # Temperature for the design-brain pass (services/design_brain.py), which
    # picks per-section template variety/drama. Deliberately higher than the
    # 0.3 content/fidelity calls — bolder, less repetitive choices are exactly
    # what this call is for, and its output is constrained to a feasibility-
    # checked enum (template ids), so a less predictable model can't break a
    # page, only pick a less expected (but still valid) layout.
    design_temperature: float = 0.7
    # Off switch for the design-brain pass (services/design_brain.py) without a
    # code change — e.g. if Ollama is unavailable in an environment. Disabling
    # it is always a safe no-op: generation falls back to the deterministic
    # mood-ordered template selection that ran before this pass existed.
    design_brain_enabled: bool = True
    # Off switch for the design-language pass (services/design_brain.py): the
    # LLM picking a curated palette + font pairing before theme construction.
    # Disabling is a safe no-op — build_theme falls back to the deterministic
    # industry/hue/seed pickers that ran before this pass existed.
    design_language_enabled: bool = True
    # Off switch for the design director (services/design_director.py): the
    # manifest pass that varies header/footer chrome archetypes per brand.
    # Disabling is a safe no-op — every site gets the legacy "classic" header
    # and "mega" footer, exactly the pre-manifest output.
    design_engine_enabled: bool = True
    # Off switch for the diversity engine (services/diversity.py): the SQLite
    # usage history that steers consecutive generations away from repeating
    # the same chrome picks. Disabling is a safe no-op — archetype selection
    # falls back to the purely seeded (per-brand idempotent) rotation.
    diversity_engine_enabled: bool = True

    # Full-bleed photo/abstract background hero on EVERY page (not just the
    # homepage), so the transparent floating header engages site-wide. Imagery
    # per page: bound/scraped photo → stock photo → colour-matched abstract;
    # a page where nothing genuine resolves still degrades to a compact hero
    # with a solid header (readability wins). Off → legacy per-mood interior
    # hero rotation (compact splits/centered).
    hero_fullbleed_all_pages: bool = True

    # Minimum long-edge (px) a SCRAPED image must have to fill a full-bleed hero
    # background. Heroes stretch their photo edge-to-edge (background-size:
    # cover), so a small source image visibly softens/pixelates when upscaled.
    # A scraped candidate whose *known* dimensions fall below this is skipped for
    # the hero-background slot so Pexels supplies a crisp full-size photo instead
    # (unknown dimensions still pass — CSS-background URLs often omit size).
    hero_min_background_dim: int = 1200

    # Minimum width/height aspect ratio a SCRAPED image with KNOWN dimensions
    # must have to fill a full-bleed hero background. Full-bleed heroes are
    # wide bands; a square-ish or portrait-orientation source photo (typically
    # a headshot or grid cell) stretched behind the hero text reads as a
    # scrape failure. CSS-background sources are exempt — the source itself
    # composed them full-bleed. Unknown dimensions still pass.
    hero_bg_min_aspect: float = 1.2

    # Minimum long-edge (px) a SCRAPED image must have to fill a NON-hero
    # full-bleed section background (also rendered background-size: cover, so a
    # small source image softens when upscaled). Lower than the hero minimum
    # because section bands are shorter, but still guards against stretching a
    # tiny figure image across a full-width band. Unknown dimensions still pass.
    # 800 sits between the hero minimum (1200) and genuinely small source images:
    # a 600px-wide photo still visibly softens across a full-width band, so it
    # defers to a crisp Pexels shot (see tests/test_media.py section-bg cases).
    section_min_background_dim: int = 800

    # Transparent header floating over full-bleed heroes, solidifying to the
    # header's real chrome after `header_scroll_reveal_offset` px of scrollF
    # (2026 trend look). Fires when the HOMEPAGE hero directive is full-bleed;
    # interior pages opt in per page via the `headerOverlaySafe` marker their
    # hero section carries (webtree-public gates the transparent phase on it).
    # Renderer support verified: PublicSiteShell honours `behavior.overlay` +
    # `behavior.scrollRevealOffset`. This flag is the kill switch.
    header_overlay_enabled: bool = True
    # Pixels scrolled before the floating header gains its background. The
    # renderer clamps to [0, 600] and defaults to 80 when the field is absent.
    header_scroll_reveal_offset: int = 80

    # Header shrinks (logo + row padding) on scroll — big by default, compacting
    # to `header_shrink_amount` percent once scrolled past the shrink offset
    # (which reuses `header_scroll_reveal_offset`). Applies to ALL generated
    # headers; on overlay/hero pages the renderer fires it with the background
    # reveal at one scroll moment. This flag is the kill switch.
    header_shrink_enabled: bool = True
    # Percent of original size the header shrinks to (renderer clamps 50-100).
    header_shrink_amount: int = 80

    # Vision pass over scraped images (services/image_vision.py). Opt-in: set
    # to a multimodal Ollama model (e.g. "qwen2.5vl:7b" or "moondream") to
    # caption/classify scraped images for better slot matching and profile
    # verification. Unset ⇒ the pass is skipped entirely.
    ollama_vision_model: str | None = None
    vision_max_images: int = 12  # annotation cap per generation
    vision_image_max_bytes: int = 4_000_000  # skip downloads larger than this
    vision_fetch_timeout_seconds: float = 8.0

    cms_api_base_url: str = "http://localhost:8000"

    # SQLite file for durable crawl-job state (services/db.py). Inside the
    # container this lives on the mounted data volume.
    sitegen_db_path: str = "/app/data/sitegen.db"

    # Luminance-band section rhythm (SECTION_VISUAL_POLICY_SPEC.md). When enabled,
    # the planner assigns a visual_policy per the §5 matrix and the schema_builder
    # luminance pass emits brand band colours / contrasting font / separators —
    # replacing the flatter legacy page-bg/surface-tint alternation with anchored
    # light/dark bands keyed to each section's imagery.
    luminance_rhythm_enabled: bool = True

    # Pexels API: free key at https://www.pexels.com/api/
    pexels_api_key: str | None = None
    pexels_base_url: str = "https://api.pexels.com/v1"
    pexels_timeout_seconds: float = 10.0
    pexels_cache_size: int = 256

    # Content migration: when the source site has a blog / events listing,
    # crawl the post/event detail pages and push them as real CMS
    # article/event entries (services/content_collections.py). The cap bounds
    # generation time — each entry costs a page fetch + an image upload.
    content_migration_enabled: bool = True
    content_migration_max_entries: int = 12

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
    ]

    # --- Security -----------------------------------------------------------
    # SSRF guard: the scrape/fetch layer accepts arbitrary user- and page-
    # supplied URLs. By default it refuses any URL that resolves to a non-public
    # address (loopback / private / link-local / cloud-metadata / the Docker
    # host gateway), so a caller can't drive the backend into internal services.
    # Set true ONLY for local development when you deliberately want to scrape a
    # localhost / LAN target on your own machine. See services/url_guard.py.
    scrape_allow_private_hosts: bool = False

    # --- HTTP client --------------------------------------------------------
    # Single source of truth for the browser-like User-Agent used by the httpx
    # fast-fetch path AND the Playwright/robots fetches (previously duplicated
    # string constants kept in sync by comment).
    http_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    )
    # Network read/connect timeouts (seconds) for the non-LLM HTTP calls. These
    # were hardcoded at their call sites; the defaults preserve prior behaviour.
    fast_fetch_timeout_seconds: float = 8.0
    robots_fetch_timeout_seconds: float = 10.0
    playwright_goto_timeout_ms: int = 15000
    cms_timeout_seconds: float = 30.0
    cms_media_upload_timeout_seconds: float = 120.0


settings = Settings()
