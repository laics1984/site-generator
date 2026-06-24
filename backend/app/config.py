from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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
