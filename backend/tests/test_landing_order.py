"""Source-backed landing order is selected before writing and never loses groups."""

import json

import pytest

from app.models.content_blocks import AboutBlock, PagePlan, SectionCandidate, SourceCard, SourceContent
from app.models.industry import PageScaffold
from app.routers.generate import _align_pages_to_scaffolds
from app.services.landing_order import prepare_landing_pages
from app.services.page_inference import source_section_kinds
from app.services.planner import _scaffolds_to_prompt_payload


def _section(heading, *, cards=False):
    body = "This is substantive source information about " + heading + "."
    return SectionCandidate(
        heading=heading, level=2, prose="" if cards else body,
        cards=[SourceCard(title=heading, body=body, meta=["Original qualifier"])] if cards else [],
        card_kind="offerings" if cards else "prose",
    )


def _fixture():
    tree = [_section("Customer testimonials", cards=True), _section("Our solution", cards=True), _section("The problem")]
    source = SourceContent(
        source_kind="url", source_ref="https://business.test/offer",
        raw_text="\n".join([s.heading + " " + s.prose for s in tree]),
        section_candidates=tree, images=["https://business.test/real.jpg"],
    )
    landing = PageScaffold(
        page_type="landing", slug="offer", title="Offer", from_source=True,
        sections=["hero", *source_section_kinds(tree), "cta"],
    )
    contact = PageScaffold(page_type="contact", slug="contact", title="Contact", sections=["hero", "contact"])
    return source, landing, contact


def test_complete_journey_moves_groups_and_kinds_together_without_mutating_source():
    source, landing, contact = _fixture()
    before = json.dumps(source.model_dump(), sort_keys=True)
    scaffolds, sources = prepare_landing_pages([landing, contact], {"offer": source})
    prepared = scaffolds[0]
    assert prepared.sections == ["hero", "about", "features", "services", "cta"]
    assert [s.heading for s in sources["offer"].section_candidates] == ["The problem", "Our solution", "Customer testimonials"]
    assert sources["offer"].section_candidates[1].cards == source.section_candidates[1].cards
    assert sources["offer"].images == source.images
    assert sources["offer"].raw_text == source.raw_text
    assert json.dumps(source.model_dump(), sort_keys=True) == before
    assert landing.sections == ["hero", "services", "features", "about", "cta"]
    payload = _scaffolds_to_prompt_payload([prepared], source_map=sources)[0]
    assert payload["required_sections"] == prepared.sections
    assert payload["page_source"]["sections"][0]["heading"] == "The problem"


@pytest.mark.parametrize("reason", ["non_landing", "no_action", "thin_proof", "unknown_section", "steps", "different_recipe", "record", "translation"])
def test_ambiguous_or_incomplete_pages_keep_the_original_order(reason):
    source, landing, contact = _fixture()
    selected = [landing, contact]
    if reason == "non_landing":
        landing.page_type = "services"
    elif reason == "no_action":
        selected = [landing]
    elif reason == "thin_proof":
        source.section_candidates[0].cards[0].body = ""
    elif reason == "unknown_section":
        source.section_candidates[0].heading = "Other information"
    elif reason == "steps":
        source.section_candidates[1].card_kind = "steps"
    elif reason == "different_recipe":
        landing.sections = ["hero", "features", "cta"]
    elif reason == "record":
        landing.record_set = "offer"
    elif reason == "translation":
        landing.locale = "bm"
    scaffolds, sources = prepare_landing_pages(selected, {"offer": source})
    assert scaffolds[0] is landing
    assert sources["offer"] is source


def test_already_good_order_is_retained_and_repeated_roles_are_stable():
    source, landing, contact = _fixture()
    source.section_candidates = [
        _section("Our solution"), _section("More benefits"),
        _section("The problem"), _section("Customer testimonials"),
    ]
    landing.sections = ["hero", *source_section_kinds(source.section_candidates), "cta"]
    prepared, sources = prepare_landing_pages([landing, contact], {"offer": source})
    assert [s.heading for s in sources["offer"].section_candidates] == [
        "The problem", "Our solution", "More benefits", "Customer testimonials",
    ]
    again, again_sources = prepare_landing_pages(prepared, sources)
    assert again == prepared
    assert again_sources == sources


def test_repeated_about_blocks_keep_their_selected_order_after_alignment():
    source, landing, contact = _fixture()
    source.section_candidates = [_section("Our results"), _section("Our solution"), _section("The problem")]
    landing.sections = ["hero", *source_section_kinds(source.section_candidates), "cta"]
    prepared, sources = prepare_landing_pages([landing, contact], {"offer": source})
    payload = _scaffolds_to_prompt_payload([prepared[0]], source_map=sources)[0]
    page = PagePlan(page_type="landing", slug="offer", title="Offer", blocks=[
        AboutBlock(heading=s["heading"], body=s["prose"])
        for s in payload["page_source"]["sections"]
    ])
    aligned = _align_pages_to_scaffolds([page], [prepared[0]], brand_name="Business", source_map=sources)
    assert [b.heading for b in aligned[0].blocks if b.kind == "about"] == [
        "The problem", "Our solution", "Our results",
    ]
