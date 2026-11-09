from unittest.mock import AsyncMock

import pytest

from app.services import scout_review


@pytest.mark.asyncio
@pytest.mark.parametrize("quote,allowed", [
    ("Centre One is in Petaling Jaya.", True),
    ("Centre One's address is Petaling Jaya.", False),
])
async def test_scout_review_keeps_only_corrections_quoted_from_source(monkeypatch, quote, allowed):
    model = AsyncMock()
    model.chat_json.return_value = scout_review.ScoutReview(
        explanation="The source spells out the town.",
        correction=scout_review.Correction(field="city", value="Petaling Jaya", quote=quote),
    )
    monkeypatch.setattr(scout_review, "get_llm", lambda: model)
    payload = scout_review.ScoutReviewRequest(
        stage="scout_source", blockage="The town does not match.", next_step="Review the public address.",
        recorded={"city": "PJ", "name": "Centre One"}, review_fields=["city"],
        excerpts=["Centre One is in Petaling Jaya."],
    )

    result = await scout_review.review(payload)

    assert bool(result.correction) == allowed
    model.chat_json.assert_awaited_once()
