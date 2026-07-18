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


def test_user_prompt_can_omit_entry_raw_text():
    # Non-first work items drop the entry page's raw_text (pure duplication —
    # their pages carry page_source); brand and pages_requested must survive.
    from app.models.content_blocks import SourceContent
    import json

    source = SourceContent(
        source_kind="url", source_ref="x", title="Acme", raw_text="Entry text here."
    )
    scaffolds = [_scaffold("services", ["hero", "cta"])]
    source_map = {"services": source}

    with_text = json.loads(
        planner._build_scaffolded_user_prompt(source, None, scaffolds, None, source_map)
    )
    trimmed = json.loads(
        planner._build_scaffolded_user_prompt(
            source, None, scaffolds, None, source_map, include_entry_text=False
        )
    )

    assert with_text["source"]["raw_text"] == "Entry text here."
    assert "raw_text" not in trimmed["source"]
    assert trimmed["source"]["title"] == "Acme"
    assert trimmed["pages_requested"][0]["page_source"]  # per-page grounding intact
    assert len(json.dumps(trimmed)) < len(json.dumps(with_text))


def test_measured_fixed_envelope_shrinks_the_budget():
    # A caller-measured envelope replaces the _TOK_BRAND_SOURCE guess; a huge
    # envelope must force smaller batches than the default guess would allow.
    scaffolds = [
        _scaffold("a", ["hero", "cta"]),
        _scaffold("b", ["hero", "cta"]),
        _scaffold("c", ["hero", "cta"]),
    ]
    num_ctx = 8192
    default_batches = planner._build_batches(scaffolds, None, num_ctx)
    # Leave room for barely one page stub beyond the system prompt.
    huge = (
        int(num_ctx * planner._INPUT_SHARE)
        - planner._system_prompt_tokens(frozenset({"hero", "cta"}))
        - planner._TOK_PER_PAGE_STUB
    )
    tight_batches = planner._build_batches(
        scaffolds, None, num_ctx, fixed_input_tokens=huge
    )
    assert len(tight_batches) == 3  # one page per batch under the tight budget
    assert len(tight_batches) > len(default_batches)
    assert [s.slug for b in tight_batches for s in b] == ["a", "b", "c"]


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
