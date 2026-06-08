from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b-instruct"
    ollama_model_quality: str = "qwen2.5:14b-instruct"
    ollama_timeout_seconds: float = 180.0

    cms_api_base_url: str = "http://localhost:8000"

    # Luminance-band section rhythm (SECTION_VISUAL_POLICY_SPEC.md). Off by
    # default: when enabled, the planner assigns a visual_policy per the §5 matrix
    # and the schema_builder luminance pass emits brand band colours / contrasting
    # font / separators. Flip on to validate the new look before making it default.
    luminance_rhythm_enabled: bool = False

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
