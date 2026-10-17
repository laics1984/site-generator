"""The design benchmark holds its baseline (scripts/design_benchmark.py).

`tests/fixtures/design_bench/baseline.json` is the committed result of the
offline benchmark: eight representative businesses (four early-years, four
technology/agency, plus a gentle nursery), each under alternative logo
colours. A change that lowers a site's design-quality score, adds a
high-severity finding, collapses the direction spread or makes same-industry
sites more alike fails here, with the numbers to look at.

Regenerate the baseline deliberately, after reviewing screenshots:

    cd backend && .venv/bin/python scripts/design_benchmark.py --out tests/fixtures/design_bench/baseline.json
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.config import settings
from app.models.brand import AxisReading, BrandPersonality
from app.services.design_direction import ContentProfile, resolve_direction

_BASELINE = Path(__file__).parent / "fixtures" / "design_bench" / "baseline.json"
_SCORE_TOLERANCE = 4
_SIMILARITY_TOLERANCE = 0.05


@pytest.fixture(scope="module")
def result():
    from scripts.design_benchmark import run

    saved = (settings.creative_directions_enabled, settings.design_quality_enabled)
    settings.creative_directions_enabled = True
    settings.design_quality_enabled = True
    try:
        return asyncio.run(run(variants=True, dump=None))
    finally:
        settings.creative_directions_enabled, settings.design_quality_enabled = saved


@pytest.fixture(scope="module")
def baseline():
    return json.loads(_BASELINE.read_text())


def test_no_site_scores_below_its_baseline(result, baseline) -> None:
    before = {s["key"]: s["quality"]["score"] for s in baseline["sites"]}
    drops = {
        s["key"]: (before[s["key"]], s["quality"]["score"])
        for s in result["sites"]
        if s["key"] in before and s["quality"]["score"] < before[s["key"]] - _SCORE_TOLERANCE
    }
    assert not drops, f"design-quality regressions (baseline, now): {drops}"


def test_no_high_severity_design_finding(result) -> None:
    from app.services.design_quality import _DIMENSION  # noqa: F401 — vocabulary sanity

    # Only accessibility findings the fixtures cannot avoid (fixture gallery
    # items carry no alt text) may be high; nothing about the design itself.
    allowed = {"alt-text"}
    for site in result["sites"]:
        findings = set(site["quality"]["findings"])
        high_design = findings & {
            "hero-without-action", "gradient-heavy", "purple-blue-gradient",
            "font-outside-theme", "repeated-template", "shape-crops-portrait",
        }
        assert not high_design - allowed, (site["key"], findings)


def test_every_business_gets_its_own_direction(result, baseline) -> None:
    primaries = [s for s in result["sites"] if s["variant"] == 0]
    assert all(s["direction"] for s in primaries)
    assert result["distinct"]["directions"] >= baseline["distinct"]["directions"]


def test_a_logo_colour_does_not_change_what_the_business_is(result) -> None:
    """The direction answers to the business, not the logo: every logo variant
    of one business lands on one direction."""
    by_business: dict[str, set[str]] = {}
    for site in result["sites"]:
        by_business.setdefault(site["base"], set()).add(site["direction"])
    assert all(len(directions) == 1 for directions in by_business.values()), by_business


def test_same_industry_sites_are_no_more_alike(result, baseline) -> None:
    for industry, now in result["similarity_by_industry"].items():
        then = baseline["similarity_by_industry"].get(industry)
        if then:
            assert now["max"] <= then["max"] + _SIMILARITY_TOLERANCE, (industry, then, now)


def test_the_design_decision_costs_no_extra_model_call() -> None:
    """Directions change what the ONE concept call is shown, not how many
    calls there are."""
    calls: list[str] = []

    class _Counting:
        async def chat_json(self, *, system_prompt, user_prompt, schema, temperature=None, **_):
            calls.append(user_prompt)
            return schema.model_validate({"concept": 1, "hero_height": None})

    saved = (settings.creative_directions_enabled, settings.design_schemes_enabled, settings.design_language_enabled)
    settings.creative_directions_enabled = settings.design_schemes_enabled = settings.design_language_enabled = True
    try:
        direction = asyncio.run(
            resolve_direction(
                brand_name="Maple & Moss",
                mood="luxury",
                industry="childcare",
                logo_hex="#3f6212",
                personality=BrandPersonality(**{
                    a: AxisReading(value=v, evidence="q")
                    for a, v in {"playful_sophisticated": 2, "calm_energetic": -2, "minimal_expressive": -2}.items()
                }),
                content=ContentProfile(photos=6, hero_photos=6, prose_chars=4000),
                archetype="montessori-preschool",
                llm=_Counting(),
            )
        )
    finally:
        settings.creative_directions_enabled, settings.design_schemes_enabled, settings.design_language_enabled = saved
    assert len(calls) == 1
    # The model's pick (concept 1) is honoured and is a direction.
    assert direction.picked_by == "model" and direction.creative is not None
    assert "Kind of business: Montessori preschool" in calls[0]
