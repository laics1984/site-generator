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
    mlx_model: str = "mlx-community/Qwen3-8B-4bit"
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
    ollama_model_quality: str = "qwen2.5:14b-instruct"
    ollama_timeout_seconds: float = 180.0
    # How long Ollama keeps the model resident after a request. The picker flow
    # fires brand detection then (after the user picks pages) generation; the
    # default 5m can unload the model in between, forcing a cold reload that
    # blows the read timeout. Keeping it warm avoids re-paying the load cost.
    ollama_keep_alive: str = "30m"

    # Context window for the scaffolded content-generation calls (planner.py).
    # 8192 is the safe default for a 7-9B model on 16GB unified memory. Raising
    # to e.g. 12288 lets the batcher pack more pages per call (fewer prefill
    # passes of the fixed prompt) at ~0.5-1GB extra KV cache — check the
    # "Dynamic batching" log line to confirm the batch count actually drops
    # before paying that memory.
    scaffold_num_ctx: int = 8192

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

    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5173",
    ]


settings = Settings()
