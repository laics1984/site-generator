from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Which LLM backend serves chat_json calls. Set via LLM_BACKEND in .env:
    # `mlx` on an Apple-Silicon host running mlx_lm.server, `ollama` otherwise
    # (Windows, or a Mac running `ollama serve`). See services/llm.resolve_llm_backend.
    llm_backend: Literal["mlx", "ollama"] = "ollama"

    # MLX backend: an OpenAI-compatible server (mlx_lm.server) running natively on
    # the Apple-Silicon host — Docker can't run MLX, so the container reaches it
    # over host.docker.internal (see docker-compose.yml). MLX uses HuggingFace repo
    # ids, not Ollama tags.
    mlx_base_url: str = "http://localhost:8080"
    mlx_model: str = "mlx-community/Qwen3-4B-4bit"
    # Generous because this is a per-read (streaming) timeout: once tokens flow
    # each one resets the clock, so it only bites on cold time-to-first-token —
    # which on a memory-constrained Mac can run 1-3 min while the OS pages the
    # model back into unified memory. Too low ⇒ ReadTimeout 502s mid-generation.
    mlx_timeout_seconds: float = 600.0
    # OpenAI servers default to a small max_tokens that would truncate a multi-
    # section generation mid-JSON; set a generous output budget. (Ollama has no
    # equivalent cap — num_predict defaults to unlimited.)
    mlx_max_tokens: int = 8192
    # Opt-in MLX vision server (mlx_vlm.server). Unset ⇒ the vision pass falls back
    # to Ollama / is skipped, exactly as with ollama_vision_model.
    mlx_vision_base_url: str | None = None
    mlx_vision_model: str | None = None

    ollama_base_url: str = "http://localhost:11434"
    # Single resident model for both content and design-brain calls — picked to
    # fit comfortably in 16GB unified memory (M1) with headroom, so the two
    # passes never fight over which model is loaded. A generation newer than
    # qwen2.5 at the same footprint.
    ollama_model: str = "qwen3.5:9b"
    ollama_timeout_seconds: float = 180.0
    # How long Ollama keeps the model resident after a request. The picker flow
    # fires brand detection then (after the user picks pages) generation; the
    # default 5m can unload the model in between, forcing a cold reload that
    # blows the read timeout. Keeping it warm avoids re-paying the load cost.
    ollama_keep_alive: str = "30m"

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
    # truncates JSON) well before the context window is actually full. A larger
    # model can raise these.
    max_sections_per_batch: int = 6
    max_pages_per_batch: int = 4

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

    # Transparent header floating over full-bleed heroes, solidifying to the
    # header's real chrome after `header_scroll_reveal_offset` px of scroll
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


settings = Settings()
