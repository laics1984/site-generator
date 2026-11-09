from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.builder_schema import BodySchema, BuilderElement, GeneratedPage, GeneratedSite, PageSeo
from app.routers import preview
from app.services import design_review


def site_with_heading():
    heading = BuilderElement(
        id="headline", name="Heading", type="text", htmlTag="h1",
        styles={"width": "640px"}, content={"innerText": "Welcome to Orchard Nursery"},
    )
    page = GeneratedPage(
        slug="", title="Home", is_homepage=True, seo=PageSeo(title="Orchard Nursery"),
        body_schema=BodySchema(elements=[heading]),
    )
    return GeneratedSite(
        site_name="Orchard Nursery", pages=[page],
        header_schema=BuilderElement(name="Header", type="__header"),
        footer_schema=BuilderElement(name="Footer", type="__footer"),
    )


class Renderer:
    def __init__(self, *, introduce_regression=False):
        self.introduce_regression = introduce_regression

    async def measure(self, site, page, layout, width, *, screenshot=False):
        heading = page.body_schema.elements[0]
        edited = bool(heading.responsiveStyles)
        overflowing = width == 375 and not edited
        return {
            "nodes": [{
                "id": heading.id, "overflow": overflowing, "clippedHeading": False,
                "foreground": None, "background": None, "fontSize": 42,
                "paddingTop": 0, "paddingBottom": 0, "inFrame": True,
            }],
            "headings": [heading.content.innerText],
            "horizontalOverflow": overflowing,
            "brokenImages": int(edited and self.introduce_regression),
            **({"image": "jpeg"} if screenshot else {}),
        }


@pytest.mark.asyncio
@pytest.mark.parametrize("introduce_regression", [False, True])
async def test_only_keeps_browser_verified_styles(introduce_regression):
    site = site_with_heading()
    initial = site.model_dump()
    model = AsyncMock()
    model.chat_json.return_value = design_review.ModelReview(
        suggestions=[design_review.ReviewSuggestion(slug="", message="Clarify the primary action")],
        spacing=[design_review.SpacingChoice(slug="", node="invented", width=375)],
    )
    result = await design_review.review(site, {}, Renderer(introduce_regression=introduce_regression), model)

    assert result.status == "complete"
    assert model.chat_json.call_args.kwargs["images"] == ["jpeg", "jpeg"]
    assert result.suggestions[0].message == "Clarify the primary action"
    assert site.model_dump() == initial
    if introduce_regression:
        assert not result.pages
        assert not result.changes
    else:
        heading = result.pages[0].body_schema.elements[0]
        assert heading.responsiveStyles.mobile == {"maxWidth": "100%"}
        assert heading.content.innerText == "Welcome to Orchard Nursery"


@pytest.mark.asyncio
async def test_browser_repairs_still_work_when_vision_is_unavailable():
    model = AsyncMock()
    model.chat_json.side_effect = design_review.LlmError("image inputs unavailable")
    result = await design_review.review(site_with_heading(), {}, Renderer(), model)
    assert result.status == "partial"
    assert result.changes


def test_preview_endpoint_runs_review_without_writing_to_cms(monkeypatch):
    monkeypatch.setattr(settings, "design_review_enabled", True)
    reviewer = AsyncMock(return_value=design_review.DesignReview(status="complete"))

    @asynccontextmanager
    async def browser(url):
        yield Renderer()

    monkeypatch.setattr(preview, "review_browser", browser)
    monkeypatch.setattr(design_review, "review", reviewer)

    response = TestClient(app).post("/api/preview/design-review", json=site_with_heading().model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json() == {"status": "complete", "pages": [], "changes": [], "suggestions": []}
    reviewer.assert_awaited_once()
