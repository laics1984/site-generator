"""Childcare art direction reaches planning without changing its JSON contract."""

import asyncio
import json
from unittest.mock import AsyncMock

from app.models.content_blocks import FeaturesBlock, SourceContent
from app.models.industry import PageScaffold
from app.services.planner import (
    DetectedBrand,
    _build_scaffolded_user_prompt,
    plan_site,
)
from app.services.prompts import CHILDCARE_VISUAL_PROMPT, TECH_VISUAL_PROMPT
from app.services.section_content import _features_preference


def _source():
    return SourceContent(
        source_kind="url", source_ref="https://school.test",
        title="Little School", raw_text="Children learn through play.",
    )


def test_visual_brief_is_injected_for_its_industries_only():
    """`visual_direction` carries the industry layer's imagery brief: the
    childcare brief for childcare, the technology brief for saas/agency (or a
    technology archetype filed elsewhere), nothing for anyone else."""
    scaffolds = [PageScaffold(
        page_type="home", slug="", title="Home", sections=["hero"],
    )]
    for industry in ("childcare", "saas", "agency", "restaurant", "other", None):
        brand = DetectedBrand(site_name="Little School", industry_category=industry)
        payload = json.loads(_build_scaffolded_user_prompt(_source(), brand, scaffolds))
        if industry == "childcare":
            assert payload["visual_direction"] == CHILDCARE_VISUAL_PROMPT
            assert brand.brand_mood == "friendly"
        elif industry in ("saas", "agency"):
            assert payload["visual_direction"] == TECH_VISUAL_PROMPT
        else:
            assert "visual_direction" not in payload
    it_firm = DetectedBrand(site_name="Kinetic", industry_category="consultancy", archetype="it-services")
    payload = json.loads(_build_scaffolded_user_prompt(_source(), it_firm, scaffolds))
    assert payload["visual_direction"] == TECH_VISUAL_PROMPT
    assert payload["business_focus"]
    payload = json.loads(_build_scaffolded_user_prompt(_source(), None, scaffolds))
    assert "visual_direction" not in payload


def test_the_chosen_direction_reaches_the_planner_once():
    scaffolds = [PageScaffold(page_type="home", slug="", title="Home", sections=["hero"])]
    context = {"look": "Gentle nurturing — soft", "imagery": "tender moments", "hero": "split"}
    brand = DetectedBrand(site_name="Little Nest", industry_category="childcare", design_context=context)
    raw = _build_scaffolded_user_prompt(_source(), brand, scaffolds)
    assert json.loads(raw)["design_direction"] == context
    assert raw.count("Gentle nurturing") == 1
    # A computed field: never in the schema the detection model is sent.
    assert "design_context" not in DetectedBrand.model_json_schema()["properties"]


def test_no_prompt_forbids_pastel_outright():
    """Pastel and rainbow are a direction's choice now (gentle-nurturing,
    expressive-contemporary), so no prompt may ban them for an industry."""
    from app.services.design_direction import PALETTE_PROMPT

    for prompt in (CHILDCARE_VISUAL_PROMPT, PALETTE_PROMPT):
        lowered = prompt.lower()
        assert "rainbow for childcare" not in lowered
        assert "never impose a pastel" not in lowered
        assert "never default to pastel" not in lowered


def test_legacy_planning_reuses_brief_in_one_call():
    client = AsyncMock()
    result = asyncio.run(plan_site(_source(), llm=client))
    assert result is client.chat_json.return_value
    client.chat_json.assert_awaited_once()
    prompt = client.chat_json.call_args.kwargs["system_prompt"]
    assert "ONLY when industry_category is childcare" in prompt
    assert prompt.count(CHILDCARE_VISUAL_PROMPT) == 1


def test_benefit_queries_can_be_omitted_without_forcing_photo_cards():
    block = FeaturesBlock.model_validate({
        "kind": "features", "heading": "Why choose us",
        "items": [
            {"title": "Care", "description": "A caring approach"},
            {"title": "Play", "description": "Learning through play"},
            {"title": "Curiosity", "description": "Hands-on discovery"},
        ],
    })
    assert all(item.image_query is None for item in block.items)
    assert "features-image-cards" not in _features_preference({}, block)
