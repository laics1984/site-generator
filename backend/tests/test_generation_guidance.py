"""Page-specific copy survives normal and chunked generation in existing calls."""

import json
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.models.content_blocks import AboutBlock, HeroBlock, PagePlan, SourceContent
from app.models.industry import PageScaffold
from app.services import planner
from app.services.generation_guidance import guidance_for_page
from app.services.prompts import HERO_COPY_GUIDANCE, PAGE_COPY_GUIDANCE, WEBSITE_COPY_GUIDANCE


def _source(text="Real business information."):
    return SourceContent(source_kind="paste", source_ref="brief", raw_text=text)


def _scaffold(page_type="about", sections=None):
    return PageScaffold(
        page_type=page_type, slug="page", title="Page",
        sections=sections or ["hero", "about", "cta"],
    )


def test_only_applicable_guidance_is_sent_and_existing_schema_survives():
    payload = json.loads(planner._build_scaffolded_user_prompt(
        _source(), None, [_scaffold("services")],
    ))
    page = payload["pages_requested"][0]
    assert PAGE_COPY_GUIDANCE["services"] in page["copy_guidance"]
    assert HERO_COPY_GUIDANCE in page["copy_guidance"]
    assert PAGE_COPY_GUIDANCE["about"] not in page["copy_guidance"]
    assert page["required_sections"] == ["hero", "about", "cta"]
    assert "copy_guidance" not in PagePlan.model_fields
    assert not guidance_for_page("gallery", ("gallery",))


async def test_legacy_reuses_shared_rules_without_an_extra_call():
    client = AsyncMock()
    await planner.plan_site(_source(), client)
    client.chat_json.assert_awaited_once()
    prompt = client.chat_json.call_args.kwargs["system_prompt"]
    assert prompt.count(WEBSITE_COPY_GUIDANCE) == 1
    for directive in PAGE_COPY_GUIDANCE.values():
        assert prompt.count(directive) == 1


class RecordingClient:
    def __init__(self):
        self.payloads = []

    async def chat_json(self, **kwargs):
        payload = json.loads(kwargs["user_prompt"])
        self.payloads.append(payload)
        pages = []
        for page in payload["pages_requested"]:
            blocks = [HeroBlock(headline="Real business information")]
            if "about" in page["required_sections"]:
                blocks.append(AboutBlock(heading="A real story", body="Supported content."))
            pages.append(PagePlan(
                page_type=page["page_type"], slug=page["slug"],
                title=page["title"], blocks=blocks,
            ))
        return planner.ScaffoldedSitePlan(site_name="Business", pages=pages)


@pytest.mark.parametrize("mode", ["normal", "text_chunks", "section_chunks"])
async def test_all_work_paths_receive_the_same_page_intent(monkeypatch, mode):
    text = "Supported business information.\n" * (25 if mode == "text_chunks" else 1)
    sections = ["hero", "about", "services", "features", "cta"]
    monkeypatch.setattr(settings, "multipass_max_chars_per_call", 240 if mode == "text_chunks" else 4000)
    monkeypatch.setattr(settings, "max_sections_per_batch", 3 if mode == "section_chunks" else 10)
    source, scaffold, client = _source(text), _scaffold(sections=sections), RecordingClient()
    await planner.plan_site_with_scaffolds(source, None, [scaffold], client)
    assert len(client.payloads) == 1 if mode == "normal" else len(client.payloads) > 1
    for payload in client.payloads:
        assert PAGE_COPY_GUIDANCE["about"] in payload["pages_requested"][0]["copy_guidance"]


def test_serialized_guidance_and_long_link_evidence_are_budgeted():
    scaffold = _scaffold("landing", ["hero", "cta"])
    source = _source()
    source.links = ["https://example.test/contact?context=" + "x" * 1000] * 15
    source_map = {scaffold.slug: source}
    actual = planner._estimate_tokens(json.dumps(
        planner._scaffolds_to_prompt_payload([scaffold], source_map=source_map),
        ensure_ascii=False,
    ))
    budget = planner._system_prompt_tokens(frozenset(scaffold.sections)) + actual + 40
    context = int((budget + planner._TOK_BRAND_SOURCE) / planner._INPUT_SHARE) + 1
    batches = planner._build_batches([scaffold, scaffold], source_map, context)
    assert len(batches) == 2  # A flat page-stub estimate would incorrectly combine them.
