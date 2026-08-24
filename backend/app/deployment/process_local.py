"""
State that assumes this process is the only one.

**Nothing here is moved out of its home module.** Each entry below is a cache or
registry that lives with the code that uses it, where it belongs — a response
cache is part of how `llm.py` does its job, not part of how this app is
deployed. What was missing was a single place that says *which* of them exist,
and what actually goes wrong when a second worker appears. That list is the
only thing this file owns.

`test_deployment_boundary.py` walks the AST of every module under `app/` and
fails if it finds runtime state that is in neither table here. So the inventory
cannot quietly go stale, which is the usual fate of a hand-maintained list — the
same drift-test idiom the section catalog and the renderer-pinned name sets use.

The discriminator it applies: **a cache starts empty, a lookup table starts
full.** An empty dict/list/set literal at module scope is runtime state; a
populated one is data. `NOT_PROCESS_LOCAL` records the handful of things that
look like state to that rule and aren't, with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass

# Breaks the site vs. merely wastes work. Only the first kind blocks a hosted
# deploy (see guards.py) — a cold cache in a second worker costs time, not
# correctness, and is a perfectly reasonable thing to ship with.
CORRECTNESS = "correctness"
EFFICIENCY = "efficiency"


@dataclass(frozen=True, slots=True)
class ProcessLocal:
    module: str
    attribute: str
    holds: str
    at_scale: str
    severity: str


PROCESS_LOCAL: tuple[ProcessLocal, ...] = (
    ProcessLocal(
        module="app.services.facebook_orchestrator",
        attribute="_TOKENS",
        holds="job_id → Facebook access token, for jobs in flight",
        at_scale=(
            "A job's follow-up request can land on a worker that never saw the "
            "token, so the read fails as though no token was supplied — and the "
            "token is deliberately never persisted, so there is nowhere to look "
            "it up. Needs a shared store, or session affinity per job."
        ),
        severity=CORRECTNESS,
    ),
    ProcessLocal(
        module="app.services.polite",
        attribute="_registry",
        holds="host → crawl politeness state (delay, last-hit time)",
        at_scale=(
            "Per-host rate limiting is enforced per process, so N workers crawl "
            "a site N times as fast as its robots.txt allows. The politeness "
            "contract is with the remote host, so it has to be shared."
        ),
        severity=CORRECTNESS,
    ),
    ProcessLocal(
        module="app.services.llm",
        attribute="_RESPONSE_CACHE",
        holds="validated LLM responses (TTL/LRU)",
        at_scale="Cache miss on another worker — a re-generation costs GPU time again.",
        severity=EFFICIENCY,
    ),
    ProcessLocal(
        module="app.services.llm",
        attribute="_MODEL_CACHE",
        holds="discovered model id per endpoint (60s TTL)",
        at_scale="Each worker re-probes /v1/models once a minute.",
        severity=EFFICIENCY,
    ),
    ProcessLocal(
        module="app.services.planner",
        attribute="_DETECT_BRAND_CACHE",
        holds="brand detection results (TTL)",
        at_scale="Cache miss on another worker — one extra LLM call.",
        severity=EFFICIENCY,
    ),
    ProcessLocal(
        module="app.services.pexels",
        attribute="_CACHE",
        holds="stock photo results by query",
        at_scale="Cache miss on another worker — one extra Pexels call against your quota.",
        severity=EFFICIENCY,
    ),
    ProcessLocal(
        module="app.services.pexels",
        attribute="_CACHE_ORDER",
        holds="LRU eviction order for _CACHE",
        at_scale="Follows _CACHE.",
        severity=EFFICIENCY,
    ),
    ProcessLocal(
        module="app.services.pexels",
        attribute="get_pexels_client",
        holds="@lru_cache'd client singleton — captures the API key at first call",
        at_scale=(
            "One client per worker, which is fine. Listed because the captured "
            "key is why tests must clear it (see conftest)."
        ),
        severity=EFFICIENCY,
    ),
    ProcessLocal(
        module="app.services.image_vision",
        attribute="_ANNOTATION_CACHE",
        holds="vision annotations by image URL (bounded FIFO)",
        at_scale="Cache miss on another worker — the image is re-annotated.",
        severity=EFFICIENCY,
    ),
    ProcessLocal(
        module="app.services.text_detection",
        attribute="_TEXT_CACHE",
        holds="url → has_text OCR results (bounded FIFO)",
        at_scale="Cache miss on another worker — the image is re-OCR'd.",
        severity=EFFICIENCY,
    ),
    ProcessLocal(
        module="app.services.scraper",
        attribute="_ROBOTS_CACHE",
        holds="parsed robots.txt per origin (TTL)",
        at_scale=(
            "Each worker fetches robots.txt separately. Harmless on its own, but "
            "it compounds the polite._registry problem above."
        ),
        severity=EFFICIENCY,
    ),
    ProcessLocal(
        module="app.services.sitemap",
        attribute="_CACHE",
        holds="sitemap probe results per origin (TTL)",
        at_scale="Each worker re-probes. One extra fetch per host per worker.",
        severity=EFFICIENCY,
    ),
)


# Module-level empties that the "starts empty ⇒ it's a cache" rule flags but
# which hold no runtime state. Each needs a reason, so the list can't be used to
# wave things through.
NOT_PROCESS_LOCAL: tuple[tuple[str, str, str], ...] = (
    (
        "app.services.theme",
        "_INDUSTRY_BACKGROUND_STRATEGY",
        "A lookup table that is currently empty (every industry was moved to an "
        "explicit rhythm — see the comment above it). Read-only: only ever .get().",
    ),
    (
        "app.services.template_filler",
        "load_catalog",
        "Pure memo of a file read. Deterministic, so per-worker duplication is "
        "just a few KB.",
    ),
    (
        "app.services.template_filler",
        "catalog_by_id",
        "Pure memo derived from load_catalog(). Same reasoning.",
    ),
    (
        "app.services.planner",
        "_scaffold_system_prompt",
        "Pure memo of prompt-string assembly. No I/O, no captured config.",
    ),
)


def blocking_at_scale() -> tuple[ProcessLocal, ...]:
    """The entries that are wrong — not merely slower — with >1 process."""
    return tuple(item for item in PROCESS_LOCAL if item.severity == CORRECTNESS)
