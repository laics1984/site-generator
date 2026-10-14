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
from app.services.prompts import CHILDCARE_VISUAL_PROMPT
from app.services.section_content import _features_preference


def _source():
    return SourceContent(
        source_kind="url", source_ref="https://school.test",
        title="Little School", raw_text="Children learn through play.",
    )


def test_visual_brief_is_injected_only_for_childcare():
    scaffolds = [PageScaffold(
        page_type="home", slug="", title="Home", sections=["hero"],
    )]
    for industry in ("childcare", "restaurant", "other", None):
        brand = DetectedBrand(site_name="Little School", industry_category=industry)
        payload = json.loads(_build_scaffolded_user_prompt(_source(), brand, scaffolds))
        if industry == "childcare":
            assert payload["industry_visual_direction"] == CHILDCARE_VISUAL_PROMPT
            assert brand.brand_mood == "friendly"
        else:
            assert "industry_visual_direction" not in payload
    payload = json.loads(_build_scaffolded_user_prompt(_source(), None, scaffolds))
    assert "industry_visual_direction" not in payload


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
