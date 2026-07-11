"""Tests for the tailored scaffold system prompt + token-aware batching.

The system prompt used to be budgeted at a flat 700 tokens while actually
measuring ~2 700 — batches could overflow num_ctx, truncate the JSON output,
and burn a repair-retry LLM call. The prompt is now measured per batch and
tailored to the batch's section kinds.
"""

from __future__ import annotations

from app.config import settings
from app.models.industry import PageScaffold
from app.services import planner


def _scaffold(slug: str, sections: list[str], *, page_type: str = "landing") -> PageScaffold:
    return PageScaffold(
        page_type=page_type,  # type: ignore[arg-type]
        slug=slug,
        title=slug.replace("-", " ").title() or "Home",
        sections=sections,  # type: ignore[arg-type]
    )


def test_tailored_prompt_contains_only_requested_and_always_kinds():
    prompt = planner._scaffold_system_prompt(frozenset({"hero", "features"}))

    assert 'kind:"features"' in prompt
    # Always-keep kinds ride along even when not requested.
    for kind in ("hero", "about", "cta", "contact"):
        assert f'kind:"{kind}"' in prompt
    # Unrequested schemas are dropped.
    for kind in ("menu", "pricing", "timeline", "awards", "team"):
        assert f'kind:"{kind}"' not in prompt


def test_full_prompt_includes_every_kind_and_is_larger():
    full = planner._scaffold_system_prompt()
    tailored = planner._scaffold_system_prompt(frozenset({"hero", "cta"}))

    for kind in planner._SCAFFOLD_BLOCK_SCHEMAS:
        assert f'kind:"{kind}"' in full
    assert len(tailored) < len(full)


def test_system_prompt_token_estimate_reflects_measured_size():
    # The old constant said 700; the real full prompt is ~4x that. Guard against
    # the estimate regressing to a wishful constant.
    est = planner._system_prompt_tokens(None)
    assert est == len(planner._scaffold_system_prompt()) // planner._CHARS_PER_TOKEN
    assert est > 2000


def test_batches_fit_the_input_budget():
    scaffolds = [
        _scaffold("", ["hero", "features", "cta"], page_type="home"),
        _scaffold("services", ["hero", "services", "cta"]),
        _scaffold("about", ["hero", "about", "team"]),
        _scaffold("contact", ["hero", "contact", "faq"]),
        _scaffold("gallery", ["hero", "gallery"]),
    ]
    num_ctx = 8192
    batches = planner._build_batches(scaffolds, None, num_ctx)

    # Every scaffold placed exactly once, order preserved.
    flat = [s.slug for b in batches for s in b]
    assert flat == [s.slug for s in scaffolds]

    input_budget = int(num_ctx * planner._INPUT_SHARE) - planner._TOK_BRAND_SOURCE
    for batch in batches:
        kinds = planner._batch_kinds(batch)
        est_input = planner._system_prompt_tokens(kinds) + sum(
            planner._TOK_PER_PAGE_STUB for _ in batch
        )
        assert est_input <= input_budget
        assert sum(len(s.sections) for s in batch) <= settings.max_sections_per_batch
