"""The push respects the site's storage plan: what will not fit is skipped
before it is sent, a refusal from the CMS is not retried and closes the
budget, and the report names what was left out — while text and layout still
land."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.services.cms_client import CmsApiError
from app.services.push_orchestrator import (
    MediaUpload,
    _describe_media,
    _media_warning,
    _StorageBudget,
    _store_bytes,
)

MB = 1024 * 1024


def _usage(available: int | None, image_limit: int | None = 10 * MB, limit: int | None = 5 * 1024 * MB) -> dict:
    return {
        "storage": {"limit_bytes": limit, "available_bytes": available},
        "file_limits": {"image": image_limit, "video": 100 * MB, "document": 20 * MB},
    }


def _client(*, existing: str | None = None, upload=None) -> MagicMock:
    client = MagicMock()
    client.lookup_media = AsyncMock(return_value=existing)
    client.upload_media = upload or AsyncMock(return_value="https://cms/new.jpg")
    return client


def _store(client, budget, size: int, *, content_type: str = "image/jpeg", src: str = "https://src/a.jpg"):
    return asyncio.run(
        _store_bytes(
            client,
            "tok",
            file_bytes=b"x" * size,
            filename="a.jpg",
            content_type=content_type,
            budget=budget,
            source=src,
        )
    )


def test_a_budget_reads_the_plan_and_says_why_a_file_would_not_fit():
    budget = _StorageBudget.from_usage(_usage(available=50 * MB))

    assert budget.refusal(5 * MB, "image/jpeg") is None
    assert budget.refusal(11 * MB, "image/png") == "file_too_large"
    assert budget.refusal(15 * MB, "application/pdf") is None
    assert budget.refusal(60 * MB, "video/mp4") == "storage_full"
    assert _StorageBudget.from_usage(_usage(available=0)).full is True
    # Unlimited (Fowable demo sites) or an API without quotas: no pre-check.
    assert _StorageBudget.from_usage(_usage(available=None, image_limit=None, limit=None)).refusal(10**10, "image/jpeg") is None
    assert _StorageBudget.from_usage(None).refusal(10**10, "image/jpeg") is None


def test_a_file_that_will_not_fit_is_skipped_without_being_sent():
    client = _client()
    budget = _StorageBudget.from_usage(_usage(available=2 * MB))

    assert _store(client, budget, 3 * MB) is None

    client.upload_media.assert_not_awaited()
    assert budget.skipped == [{"url": "https://src/a.jpg", "reason": "storage_full"}]


def test_bytes_the_library_already_holds_cost_nothing():
    client = _client(existing="https://cms/old.jpg")
    budget = _StorageBudget.from_usage(_usage(available=0))

    assert _store(client, budget, 3 * MB) == ("https://cms/old.jpg", True)
    assert budget.skipped == []


def test_space_used_by_one_upload_is_not_offered_to_the_next():
    client = _client()
    budget = _StorageBudget.from_usage(_usage(available=5 * MB))

    assert _store(client, budget, 4 * MB, src="https://src/1.jpg") == ("https://cms/new.jpg", False)
    assert _store(client, budget, 4 * MB, src="https://src/2.jpg") is None
    assert budget.skipped == [{"url": "https://src/2.jpg", "reason": "storage_full"}]


def test_a_refusal_from_the_cms_is_not_retried_and_closes_the_budget():
    refused = CmsApiError(
        403,
        "Media upload failed",
        response_body={"error": {"code": "STORAGE_QUOTA_EXCEEDED", "message": "Your storage is full."}},
    )
    client = _client(upload=AsyncMock(side_effect=refused))
    budget = _StorageBudget()

    assert _store(client, budget, MB, src="https://src/1.jpg") is None
    assert _store(client, budget, MB, src="https://src/2.jpg") is None

    assert client.upload_media.await_count == 1
    assert budget.full is True
    assert [item["url"] for item in budget.skipped] == ["https://src/1.jpg", "https://src/2.jpg"]


def test_a_gateway_hiccup_is_still_retried_once():
    client = _client(upload=AsyncMock(side_effect=[CmsApiError(503, "busy"), "https://cms/ok.jpg"]))

    assert _store(client, _StorageBudget(), MB) == ("https://cms/ok.jpg", False)
    assert client.upload_media.await_count == 2


def test_the_report_names_what_the_plan_left_out_and_what_to_do():
    media = MediaUpload(
        rewrites={"https://src/1.jpg": "https://cms/1.jpg"},
        failed={"https://src/2.jpg", "https://src/3.jpg"},
        skipped=(
            {"url": "https://src/2.jpg", "reason": "storage_full"},
            {"url": "https://src/3.jpg", "reason": "file_too_large"},
        ),
    )

    assert "2 not stored (storage plan)" in _describe_media(media)
    warning = _media_warning(media)
    assert warning.startswith("2 file(s) were not added")
    assert "1 because the site's storage is full" in warning
    assert "1 because they are larger than the plan allows per file" in warning
    assert "pages were pushed without them" in warning


def test_the_cms_error_code_is_read_from_its_error_shape():
    assert CmsApiError(422, "x", response_body={"error": {"code": "FILE_TOO_LARGE"}}).code == "FILE_TOO_LARGE"
    assert CmsApiError(500, "x", response_body={"t": "f", "errors": "nope"}).code is None
    assert CmsApiError(503, "x").code is None
