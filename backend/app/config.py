from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- LLM connection ------------------------------------------------------
    # The ONLY thing the backend knows about models: where to reach one. Which
    # engine (Ollama / llama.cpp / MLX / a remote box), which model, quantization,
    # context length, keep-alive and GPU offload all live in ai-server/.env —
    # see ai-server/README.md. Every engine serves the same OpenAI-compatible
    # /v1/chat/completions + /v1/models, so nothing here changes when the engine
    # does, and swapping models needs no edit on this side at all.
    #
    # Server ROOT — no /v1 suffix; the client appends the OpenAI paths itself.
    # In-container this points at host.docker.internal (the ai-server publishes
    # its port on the host); set it to a tailnet name/IP for a remote AI box.
    llm_base_url: str = Field(
        "http://host.docker.internal:11434",
        validation_alias=AliasChoices("LLM_BASE_URL", "MLX_BASE_URL", "OLLAMA_BASE_URL"),
    )
    # Normally UNSET: the model id is read from /v1/models on first use (see
    # services/llm.resolve_model), which is what makes a model swap in
    # ai-server/.env invisible here. Set it only when one server advertises
    # several models and you need to pin which one this role uses.
    llm_model: str | None = Field(
        None,
        validation_alias=AliasChoices("LLM_MODEL", "MLX_MODEL", "OLLAMA_MODEL"),
    )
    # Sent as "Authorization: Bearer …". Ollama has no auth; llama-server does
    # (--api-key), and a tailnet endpoint should use it — see ai-server/README §D.
    llm_api_key: str | None = None
    # Generous because this is a per-READ (streaming) timeout: once tokens flow
    # each one resets the clock, so it only bites on cold time-to-first-token,
    # which for a 24GB model loading from disk can run minutes. Too low ⇒
    # ReadTimeout 502s mid-generation.
    llm_timeout_seconds: float = Field(
        600.0,
        validation_alias=AliasChoices("LLM_TIMEOUT_SECONDS", "MLX_TIMEOUT_SECONDS"),
    )
    # OpenAI servers default to a small max_tokens that would truncate a multi-
    # section generation mid-JSON, so set a generous output budget. This is a
    # per-REQUEST cap (unlike the server's context window) and is doubled on
    # demand by llm._boost_budget after a genuine truncation.
    llm_max_tokens: int = Field(
        16384,
        validation_alias=AliasChoices("LLM_MAX_TOKENS", "MLX_MAX_TOKENS"),
    )
    # Sampling knob sent per request, so it stays here rather than moving to the
    # ai-server. mlx_lm.server defaults it to 0.0 (disabled) — unlike Ollama and
    # llama.cpp, whose repeat_penalty already defaults to 1.1. Without it a small
    # model can fall into a degenerate loop (re-emitting the same nested block)
    # that never produces valid JSON and just burns the whole max_tokens budget;
    # the doubled-budget truncation retry can't fix that, it only lets the loop
    # run longer. 0.0 restores the server's own default (off).
    llm_repetition_penalty: float = Field(
        1.1,
        validation_alias=AliasChoices("LLM_REPETITION_PENALTY", "MLX_REPETITION_PENALTY"),
    )

    # --- Reasoning role: a second, bigger model for the judgment-heavy calls ---
    # Routes brand detection (planner.detect_brand), the design-brain passes
    # (design recipe + design language) and the image tie-break judge
    # (image_match._llm_pick_best) to a different endpoint — typically a bigger
    # model on the AI server — while the default one keeps bulk content
    # generation. Unset REASONING_BASE_URL *and* REASONING_MODEL ⇒ role disabled:
    # those calls use get_llm() unchanged. This is application ROUTING (which
    # role talks to which endpoint), which is why it survives here while engine
    # selection does not.
    reasoning_base_url: str | None = None  # None → the default endpoint
    reasoning_model: str | None = None  # None → auto-discovered from that endpoint
    reasoning_api_key: str | None = None  # sent as "Authorization: Bearer …" when set
    reasoning_timeout_seconds: float | None = None  # None → llm_timeout_seconds
    # Output budget for the reasoning calls. Same value as llm_max_tokens, but
    # kept separate because thinking tokens count against the completion budget
    # on OpenAI-compatible servers — so this role burns budget before emitting
    # any JSON, and may need to diverge.
    #
    # NB raising this past LLM_CTX does nothing: the server's context window
    # covers prompt + completion together, so it is the real ceiling. A genuine
    # truncation needs a bigger LLM_CTX (ai-server/.env, mirrored by
    # LLM_CONTEXT_TOKENS) or smaller batches — not a bigger max_tokens.
    reasoning_max_tokens: int = 16384
    # Thinking ON by default for this role: the reasoning calls are small
    # prompts with small JSON outputs, where a thinking pass buys better
    # judgment. REASONING_THINK=false is the kill switch if a model/server
    # combo misbehaves (e.g. thinking output breaking JSON mode).
    reasoning_think: bool = True

    @field_validator(
        "llm_model",
        "llm_api_key",
        "reasoning_base_url",
        "reasoning_model",
        "reasoning_api_key",
        "cms_remote_api_base_url",
        "cms_remote_admin_base_url",
        mode="before",
    )
    @classmethod
    def _empty_str_is_none(cls, v: object) -> object:
        """`FOO=` (empty) in .env means unset, not empty-string — which matters
        for llm_model especially, where empty must fall through to /v1/models
        auto-discovery rather than being sent as a blank model id."""
        if isinstance(v, str) and not v.strip():
            return None
        return v

    # --- LLM tuning: everything you'd retune when swapping model variants ---
    # All of these are env-overridable (upper-cased field name), so moving to a
    # bigger/smaller or thinking/non-thinking model is a .env change, not a code
    # change. Defaults are tuned for a 4-9B instruct model on 16GB unified memory.

    # Client-level fallbacks used whenever a call site doesn't pass its own value.
    llm_default_temperature: float = 0.4
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
    # TTL for the crawl-job result cache (routers/scrape.py + crawl_jobs). 5
    # minutes routinely expired while the user was still in the page picker,
    # forcing a full re-scrape on regeneration; 30 minutes covers a whole
    # editing session.
    scrape_cache_ttl_seconds: int = 1800

    # Seed the crawl frontier from the site's own sitemap, behind every link the
    # entry page shows. The BFS alone only reaches pages some crawled page links
    # to, so a page linked only from beyond the budget (or from nowhere) was
    # invisible even when the sitemap listed it. Costs 1-3s of plain HTTP per
    # crawl; off → links-only discovery, exactly as before.
    crawl_seed_from_sitemap: bool = True

    # Brand detection + the legacy free-form planner: faithful rewrite — keep it
    # close to the source, not creative.
    plan_temperature: float = 0.3
    # Scaffolded content generation: low temperature keeps the rewrite close to
    # the scraped source text.
    scaffold_temperature: float = 0.25
    # Deterministic judge calls (image tie-break in image_match.py, vision
    # annotation in image_vision.py): tiny prompts, want reproducible picks.
    judge_temperature: float = 0.0

    # The server's context window, as far as the BATCHER is concerned.
    #
    # This is no longer sent to the model — the OpenAI wire has no per-request
    # num_ctx, so the real context size is a server setting (LLM_CTX in
    # ai-server/.env → OLLAMA_CONTEXT_LENGTH / llama.cpp -c). What stays here is
    # the backend's *view* of it: planner._plan_batches divides this into input
    # and output budgets to decide how many pages/sections fit in one call
    # (see the "Dynamic batching" log line).
    #
    # KEEP IT IN STEP WITH ai-server/.env LLM_CTX. Too high ⇒ the batcher packs
    # calls the server will truncate; too low ⇒ needlessly many small batches
    # and worse cross-page coherence.
    llm_context_tokens: int = Field(
        16384,
        validation_alias=AliasChoices("LLM_CONTEXT_TOKENS", "SCAFFOLD_NUM_CTX"),
    )

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
    #
    # At 6 this was a *per-page* cap in disguise: an inferred rhythm is 3-6
    # sections, so nearly every batch sealed at one page while the token budgets
    # sat two-thirds empty ("Batch sealed — pages=1 sections=6 est_input=4162"
    # against input_budget=15084). 10 is the ceiling the costing note below
    # names — past it the model thins each block — and it lets 6+3 / 5+5 pages
    # share a call. It also raises _needs_section_chunking's threshold, so a
    # 7-section page stops being split into extra calls that each re-send the
    # whole page excerpt and the ~3k-token system prompt.
    max_sections_per_batch: int = 10
    max_pages_per_batch: int = 4
    # How many scaffold batches may be in flight at once. 1 is strictly serial.
    # Raise it only when the LLM backend genuinely serves parallel requests —
    # for Ollama that means OLLAMA_NUM_PARALLEL >= this value, or the requests
    # simply queue on one slot. Mind the VRAM: Ollama sizes its KV cache as
    # num_ctx * num_parallel, so a second slot on a model that already spills to
    # CPU evicts more weights and runs SLOWER. Lower OLLAMA_CONTEXT_LENGTH (and
    # llm_context_tokens with it) to keep the product flat.
    #
    # Work items are pulled by a sliding-window worker pool, not lockstep depth
    # groups (see planner._run_worklist), so one slow page no longer idles the
    # other slots.
    scaffold_batch_concurrency: int = 2

    # Char cap on the raw source text sent to the LEGACY free-form planner
    # (planner._build_user_prompt, the /from-source path). The old hardcoded
    # 12 000 silently dropped everything past the first few sections of a rich
    # page; the scaffolded path chunks instead of truncating, so this only
    # bounds the legacy single-call prompt.
    legacy_prompt_max_chars: int = 24000

    # Ask the LLM to work out a PASTE's page structure (services/paste_structure.py)
    # instead of reading it from line shape. A pasted brief has no reliable
    # structural markers — unlike a crawl (URLs) or a Word doc (heading styles) —
    # so the line-shape heuristic mistakes layout labels for page titles. Off ⇒
    # that heuristic is the whole reader, which is also the fallback whenever the
    # LLM is unreachable or the paste is bigger than the cap below.
    paste_llm_structure_enabled: bool = True
    # Char cap on the line-numbered paste sent to that call. Past it we fall back
    # to the heuristic rather than truncating: half a structure is worse than a
    # consistent one, because the dropped tail silently loses its pages.
    paste_structure_max_chars: int = 24000

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
    # Off switch for design schemes (services/design_schemes.py): the style-pack
    # layer that gives each (mood, industry) cell several distinct visual
    # languages instead of one. Disabling is a safe no-op — every scheme field
    # defers to the per-mood tables that were the sole authority before it
    # (MOOD_SPECS, _MOOD_LAYOUT_PREFERENCE, _DIVIDER_SHAPE_BY_MOOD, the
    # hero-director specs), so output is byte-identical to the pre-scheme
    # generator. Ships off; flipped on once verified against real sources.
    design_schemes_enabled: bool = False

    # Full-bleed photo/abstract background hero on EVERY page (not just the
    # homepage), so the transparent floating header engages site-wide. Imagery
    # per page: bound/scraped photo → stock photo → colour-matched abstract;
    # a page where nothing genuine resolves still degrades to a compact hero
    # with a solid header (readability wins). Off → legacy per-mood interior
    # hero rotation (compact splits/centered).
    hero_fullbleed_all_pages: bool = True

    # Anchor a full-bleed hero's copy to one side (left / bottom-left) instead of
    # centring it, with the scrim and focal crop following that edge — the
    # editorial composition from services/hero_director.hero_composition.
    #
    # Off by default: centred copy is what reads as deliberate on this
    # generator's output. An anchored column only works when the photograph has
    # a genuinely open side to give it, and across arbitrary scraped and stock
    # imagery that is the exception, not the rule — so the anchor more often
    # lands copy over a busy half of the frame than beside a clean one.
    # On → homepage leads left and interiors rotate left / bottom-left / centre.
    hero_anchored_copy: bool = False

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

    # Vision pass over scraped images (services/image_vision.py). Opt-in: set to
    # a multimodal model served by the ai-server (e.g. "qwen2.5vl:7b") to
    # caption/classify scraped images for better slot matching and profile
    # verification. Unset ⇒ the pass is skipped entirely. Images are sent as
    # OpenAI `image_url` data URIs, which every engine here accepts.
    llm_vision_model: str | None = Field(
        None,
        validation_alias=AliasChoices(
            "LLM_VISION_MODEL", "MLX_VISION_MODEL", "OLLAMA_VISION_MODEL"
        ),
    )
    # Only needed when the vision model is served by a DIFFERENT endpoint than
    # the text model. None ⇒ same endpoint as llm_base_url.
    llm_vision_base_url: str | None = Field(
        None,
        validation_alias=AliasChoices("LLM_VISION_BASE_URL", "MLX_VISION_BASE_URL"),
    )
    vision_max_images: int = 12  # annotation cap per generation
    vision_image_max_bytes: int = 4_000_000  # skip downloads larger than this
    vision_fetch_timeout_seconds: float = 8.0

    # Pixel sampling for full-bleed photo slots (services/image_sampling.py):
    # reads a scraped photo's dominant colour and focal point so the hero scrim
    # adapts and the crop frames the subject. Off ⇒ scraped photos keep the
    # metadata-only path (blind mid-cast, centred crop), exactly as before the
    # pass existed. The backend test suite turns this off — it is the only thing
    # in ImageResolver that touches the network.
    # OCR text detection (services/text_detection.py): flags scraped images that
    # carry their own headline/tagline/price list so they never fill a slot we
    # draw OUR headline over. Requires rapidocr-onnxruntime; the pass no-ops
    # cleanly when the wheel is absent, so turning this off is also how you run
    # without that dependency installed.
    #
    # Runs on SOURCE images only (never stock) and rides the existing prefetch
    # window alongside the content LLM, so it is ~free in wall time: measured
    # ~630ms/image, i.e. ~7s for the default cap, against an LLM pass that owns
    # the GPU meanwhile. Do NOT raise the cap far — it is CPU-bound and
    # single-batch (thread pools measured SLOWER: onnxruntime already uses every
    # core per inference).
    ocr_text_detection_enabled: bool = True
    ocr_max_images: int = 12  # screening cap per generation
    ocr_input_px: int = 512  # matches the vision thumbnail, so downloads are shared
    ocr_fetch_concurrency: int = 3
    # How many text-bearing candidates a single background slot may reject
    # before giving up and falling through to stock. Each rejection costs a
    # download plus an inference, so this bounds the worst case (a source whose
    # every image is a promo graphic) instead of screening the whole pool.
    ocr_verify_budget: int = 4

    photo_sampling_enabled: bool = True
    # Deliberately tighter than the vision fetch: a hero's dressing is an
    # enhancement, never worth stalling a build for. On timeout the photo just
    # keeps the old defaults.
    photo_sample_timeout_seconds: float = 4.0

    cms_api_base_url: str = "http://localhost:8000"

    # The webtree admin suite (a separate app on its own origin — the CMS API
    # above is not it). Set this and a successful push returns an `admin_url`
    # the frontend turns into an "Open in webtree admin" link. Left unset, the
    # frontend simply omits the link rather than guessing a host.
    admin_app_base_url: str | None = None

    # --- A second CMS you can pick per push ---------------------------------
    # The two settings above define the DEFAULT push target. Set these to point
    # a locally-run generator at a live CMS: the publish drawer then grows a
    # target picker and each push chooses where it lands, with no restart and
    # no .env edit between sites. Left unset there is exactly one target and
    # the UI is byte-identical to before — the same restraint as
    # admin_app_base_url. Two flat scalars rather than a nested targets map,
    # mirroring the reasoning_* precedent for "a second endpoint with its own
    # settings"; services/cms_targets.py is the only reader, so a third target
    # is a change there and nowhere else.
    #
    # Must be the ADMIN API origin: the CMS's routes/api.php can serve admin
    # and public routes on separate hosts (ADMIN_API_DOMAIN +
    # ALLOW_LEGACY_SHARED_API_HOST), and the push only ever calls admin routes.
    cms_remote_api_base_url: str | None = None
    cms_remote_admin_base_url: str | None = None

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

    # --- SEO ----------------------------------------------------------------
    # Master switch for SEO enrichment (og:image, twitterCard, structuredData,
    # canonical). Disabling is a safe no-op — pages get title/description only.
    seo_enabled: bool = True
    # Master switch for the advisory SEO audit pass (title length, heading
    # hierarchy, CTA, duplicates, orphan pages). Logged only, never blocks.
    seo_audit_enabled: bool = True
    # Structured data (JSON-LD) generation: Organization/LocalBusiness on
    # homepage, BreadcrumbList on sub-pages, FAQPage on FAQ blocks.
    seo_structured_data_enabled: bool = True
    # SEO title length bounds (chars). The LLM targets 50-60; the audit flags
    # titles outside these bounds.
    seo_title_min_length: int = 30
    seo_title_max_length: int = 65
    # SEO meta description length bounds (chars).
    seo_description_min_length: int = 100
    seo_description_max_length: int = 170

    # --- Deployment posture -------------------------------------------------
    # WHERE this generator runs — not where a push lands (that is a CmsTarget,
    # services/cms_targets.py; the two are deliberately separate concerns).
    #
    # "local" is the tool as designed and documented in SECURITY.md: one user,
    # one machine, no auth, bound to localhost. "hosted" asserts the opposite,
    # and app/deployment/guards.py refuses to start on the settings that are
    # only safe under "local". Every check is inert while this is "local", so
    # the default is a true no-op.
    deployment: Literal["local", "hosted"] = "local"
    # How requests are authenticated under "hosted". There is no auth in this
    # app by design, so "none" is refused at startup under "hosted" — the point
    # is that an unauthenticated public deploy cannot happen by omission.
    # "proxy" is an explicit attestation that an authenticating reverse proxy
    # sits in front, which is the arrangement SECURITY.md already recommends.
    deployment_auth: Literal["none", "proxy"] = "none"

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

    # --- Facebook -----------------------------------------------------------
    # Reading a Facebook business Page (About, contacts, hours, posts, profile
    # mark) as a generation source. Routed to automatically when the pasted link
    # is a Facebook URL — see services/source_detect.py.
    facebook_graph_base_url: str = "https://graph.facebook.com"
    facebook_graph_version: str = "v21.0"
    # Optional default Page access token. The Graph path is the sanctioned one
    # and yields structured hours/emails/posts; without it the reader falls back
    # to rendering the public Page, which is best-effort. A per-request token
    # (never persisted) overrides this.
    facebook_access_token: str | None = None
    facebook_timeout_seconds: float = 15.0
    facebook_max_posts: int = 25
    facebook_max_reviews: int = 12
    # Floor below which a Page has too little to build from. Higher than the
    # document path's 80 because Facebook ALWAYS yields a name plus a category
    # (~40 chars), which would sail past 80 and produce a padded site.
    facebook_min_raw_text_chars: int = 200
    # Public-page render when no token is available. Fragile by nature (Facebook
    # changes its markup without notice); set false to require a token.
    facebook_render_fallback_enabled: bool = True
    # Ask the vision judge whether the profile picture is a real mark or a
    # photograph, and demote it to palette-only when it's a photo. No-op unless
    # llm_vision_model is configured.
    facebook_logo_vision_check: bool = True


settings = Settings()
