"""Facebook/PDF ingestion reaches conversion planning without losing source facts."""

import json
from collections import Counter

import fitz
import pytest

from app.config import settings
from app.models.content_blocks import AboutBlock, PagePlan, SourceContent, TestimonialsBlock as ReviewsBlock, TestimonialItem as ReviewItem
from app.models.facebook import FacebookPage, FacebookReview
from app.models.industry import PageScaffold
from app.routers.generate import _align_pages_to_scaffolds
from app.services.doc_parser import parse_pdf
from app.services.doc_structure import split_into_pages
from app.services.facebook_authority import enforce_facebook_facts
from app.services.facebook_source import homepage_sections_for, to_source_content
from app.services.landing_order import prepare_landing_pages
from app.services.landing_source import journey_sections, text_action_links
from app.services.planner import _generate_page_section_chunks, _scaffolds_to_prompt_payload, ScaffoldedSitePlan


PROBLEM = "Parents struggle to find reliable afternoon care that fits their working hours."
SOLUTION = "Our afternoon care includes supervised reading, creative activities and outdoor play."
PROOF = "Our child enjoys the reading activities and we appreciate the dependable afternoon care."


def _facebook():
    page = FacebookPage(
        name="Acme Care", canonical_url="https://facebook.com/acmecare",
        category="Childcare", about=f"The problem:\n{PROBLEM}", products=SOLUTION,
        phone="+60 12 345 6789", single_line_address="12 Jalan Sultan, Kuala Lumpur",
        reviews=[FacebookReview(text=PROOF, author="Sam Lee")],
    )
    source = to_source_content(page)
    sections = homepage_sections_for(page, photo_count=4)
    home = PageScaffold(page_type="home", slug="", title="Home", is_homepage=True, sections=sections)
    return page, source, home


def _text_source(kind="pdf", **updates):
    data = dict(
        source_kind=kind, source_ref="brief.pdf", title="Acme Care",
        raw_text=f"Our results\n{PROOF}\nHow we help\n{SOLUTION}\nThe problem\n{PROBLEM}",
        headings=["Our results", "How we help", "The problem"],
        links=["mailto:hello@acme.example"], images=["data:real-photo"],
    )
    data.update(updates)
    return SourceContent(**data)


def _home(**updates):
    data = dict(
        page_type="home", slug="", title="Home", is_homepage=True,
        sections=["hero", "features", "about", "gallery", "cta"],
    )
    data.update(updates)
    return PageScaffold(**data)


def test_real_facebook_mapper_qualifies_with_facts_and_selected_features_intact():
    page, source, home = _facebook()
    before = json.dumps(source.model_dump(), sort_keys=True)
    prepared, sources = prepare_landing_pages([home], {"": source})
    selected = prepared[0]
    assert selected.page_type == "home" and selected.is_homepage and selected.slug == ""
    assert selected.sections == ["hero", "about", "features", "testimonials", "gallery", "locations", "contact", "cta"]
    assert Counter(selected.sections) == Counter(home.sections)
    assert json.dumps(source.model_dump(), sort_keys=True) == before
    assert sources[""].raw_text == source.raw_text
    assert sources[""].images == source.images
    assert sources[""].social_links == source.social_links
    payload = _scaffolds_to_prompt_payload(prepared, source_map=sources)[0]
    assert [binding["role"] for binding in payload["conversion_journey"]] == ["problem", "solution", "proof"]
    assert PROBLEM in payload["conversion_journey"][0]["prose"]
    assert SOLUTION in payload["conversion_journey"][1]["prose"]
    assert PROOF in payload["conversion_journey"][2]["prose"]
    assert "Sam Lee" in payload["conversion_journey"][2]["prose"]
    assert "LANDING:" in payload["copy_guidance"]
    # The final authority still replaces a model-authored review verbatim.
    plan = PagePlan(page_type="home", slug="", title="Home", is_homepage=True, blocks=[
        ReviewsBlock(items=[ReviewItem(quote="Invented review", author="Invented author")]),
    ])
    authoritative = enforce_facebook_facts([plan], page)[0]
    assert authoritative.blocks[0].items[0].quote == PROOF
    assert authoritative.blocks[0].items[0].author == "Sam Lee"


def _pdf_bytes():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((40, 35), "Welcome", fontsize=24)
    for y, heading, body in (
        (65, "Our results", PROOF), (180, "How we help", SOLUTION),
        (295, "The problem", PROBLEM),
    ):
        page.insert_text((40, y), heading, fontsize=16)
        page.insert_textbox(fitz.Rect(40, y + 12, 210, y + 105), body, fontsize=11)
    page.insert_text((40, 435), "Email: hello@acme.example", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


def test_real_pdf_parser_and_splitter_reach_home_conversion_without_a_section_tree():
    parsed = parse_pdf(_pdf_bytes(), "brief.pdf")
    source = split_into_pages(parsed)
    before = json.dumps(source.model_dump(), sort_keys=True)
    assert source.section_candidates == []
    prepared, sources = prepare_landing_pages([_home()], {"": source})
    home = prepared[0]
    assert [binding.role for binding in home._conversion_journey] == ["problem", "solution", "proof"]
    assert home.sections == ["hero", "about", "features", "about", "gallery", "cta"]
    assert source.raw_text in sources[""].raw_text
    assert sources[""].discovered_pages == source.discovered_pages
    assert json.dumps(source.model_dump(), sort_keys=True) == before
    assert "mailto:hello@acme.example" in sources[""].links
    assert home._conversion_journey[0].source.heading == "The problem"
    # Evidence is private; neither picker serialization nor schema grows.
    assert "_conversion_journey" not in home.model_dump()
    assert "_conversion_journey" not in home.model_json_schema()["properties"]


@pytest.mark.parametrize("kind", ["url", "docx", "paste"])
@pytest.mark.parametrize("page_type", ["home", "landing"])
def test_the_extension_does_not_apply_to_other_source_kinds(kind, page_type):
    source = _text_source(kind)
    home = _home(page_type=page_type, is_homepage=page_type == "home")
    prepared, sources = prepare_landing_pages([home], {"": source})
    assert prepared[0] is home
    assert sources[""] is source
    assert "conversion_journey" not in _scaffolds_to_prompt_payload(prepared, source_map=sources)[0]


@pytest.mark.parametrize("kind", ["facebook", "pdf"])
def test_unstructured_dedicated_landing_pages_also_qualify(kind):
    source = _text_source(kind)
    landing = _home(page_type="landing", slug="offer", is_homepage=False)
    prepared, sources = prepare_landing_pages([landing], {"offer": source})
    assert prepared[0].slug == "offer" and prepared[0].page_type == "landing"
    assert not prepared[0].is_homepage
    assert [b.role for b in prepared[0]._conversion_journey] == ["problem", "solution", "proof"]
    assert sources["offer"].raw_text == source.raw_text


@pytest.mark.parametrize("kind", ["facebook", "pdf"])
@pytest.mark.parametrize("reason", ["no_problem", "no_proof", "thin_proof", "ambiguous", "steps", "no_action", "services", "record", "translation", "legal"])
def test_incomplete_or_protected_pages_keep_the_selected_recipe(kind, reason):
    source, home = _text_source(kind), _home()
    if reason == "no_problem":
        source.raw_text = source.raw_text.replace(f"The problem\n{PROBLEM}", "About us\nWe care for families.")
        source.headings[-1] = "About us"
    elif reason == "no_proof":
        source.raw_text = source.raw_text.replace(f"Our results\n{PROOF}", "About us\nWe care for families.")
        source.headings[0] = "About us"
    elif reason == "thin_proof":
        source.raw_text = source.raw_text.replace(PROOF, "Great")
    elif reason == "ambiguous":
        source.raw_text = source.raw_text.replace("The problem", "Problems and solutions")
        source.headings[-1] = "Problems and solutions"
    elif reason == "steps":
        source.raw_text += "\nStep 1\nKeep this instructional sequence in its original order."
    elif reason == "no_action":
        source.links = []
    elif reason == "services":
        home.page_type, home.is_homepage = "services", False
    elif reason == "record":
        home.record_set = ""
    elif reason == "translation":
        home.locale = "bm"
    elif reason == "legal":
        home.is_legal = True
    prepared, sources = prepare_landing_pages([home], {"": source})
    assert prepared[0] is home
    assert sources[""] is source


def test_pdf_home_can_summarize_discovered_pages_without_removing_them():
    source = _text_source(raw_text=f"The problem\n{PROBLEM}", headings=["The problem"], links=[])
    solution = _text_source(title="Solutions", raw_text=SOLUTION, headings=["Solutions"], url_path="/solutions", links=[])
    proof = _text_source(title="Our results", raw_text=PROOF, headings=["Our results"], url_path="/results", links=[])
    source.discovered_pages = [solution, proof]
    contact = PageScaffold(page_type="contact", slug="contact", title="Contact", sections=["hero", "contact"])
    before = json.dumps(source.model_dump(), sort_keys=True)
    prepared, sources = prepare_landing_pages([_home(), contact], {"": source, "contact": source})
    assert [binding.role for binding in prepared[0]._conversion_journey] == ["problem", "solution", "proof"]
    assert SOLUTION in sources[""].raw_text and PROOF in sources[""].raw_text
    assert sources[""].discovered_pages == [solution, proof]
    assert sources["contact"] is source
    assert json.dumps(source.model_dump(), sort_keys=True) == before


def test_preparation_is_idempotent_and_same_role_sections_keep_their_relative_order():
    source = _text_source(raw_text=f"Our results\n{PROOF}\nHow we help\n{SOLUTION}\nMore benefits\n{SOLUTION}\nThe problem\n{PROBLEM}", headings=[])
    prepared, sources = prepare_landing_pages([_home()], {"": source})
    again, again_sources = prepare_landing_pages(prepared, sources)
    assert [b.source.heading for b in again[0]._conversion_journey] == ["The problem", "How we help", "More benefits", "Our results"]
    assert again == prepared and again_sources == sources


def test_repeated_about_stages_keep_their_evidence_through_alignment():
    source = _text_source()
    home = _home(sections=["hero", "about", "gallery", "cta"])
    prepared, sources = prepare_landing_pages([home], {"": source})
    payload = _scaffolds_to_prompt_payload(prepared, source_map=sources)[0]
    plan = PagePlan(page_type="home", slug="", title="Home", is_homepage=True, blocks=[
        AboutBlock(heading=b["heading"], body=b["prose"])
        for b in payload["conversion_journey"]
    ])
    aligned = _align_pages_to_scaffolds([plan], prepared, source_map=sources)[0]
    assert [b.heading for b in aligned.blocks if b.kind == "about"] == ["The problem", "How we help", "Our results"]


@pytest.mark.asyncio
async def test_section_multipass_rebases_bindings_for_repeated_about_stages(monkeypatch):
    source = _text_source()
    home = _home(sections=["hero", "about", "gallery", "cta"])
    prepared, sources = prepare_landing_pages([home], {"": source})
    monkeypatch.setattr(settings, "max_sections_per_batch", 2)
    calls = []

    async def fake_chat(client, *, user_prompt, **kwargs):
        payload = json.loads(user_prompt)["pages_requested"][0]
        calls.append(payload)
        return ScaffoldedSitePlan(site_name="Acme", pages=[PagePlan(
            page_type="home", slug="", title="Home", is_homepage=True,
            blocks=[AboutBlock(heading=b["heading"], body=b["prose"]) for b in payload.get("conversion_journey", [])],
        )])

    monkeypatch.setattr("app.services.planner.chat_json_cached", fake_chat)
    plan, _ = await _generate_page_section_chunks(source, None, prepared[0], {}, sources, object())
    bindings = [binding for call in calls for binding in call.get("conversion_journey", [])]
    assert [binding["heading"] for binding in bindings] == ["The problem", "How we help", "Our results"]
    assert all(binding["section_index"] == 1 for binding in bindings)
    assert [b.heading for b in plan.blocks if b.kind == "about"] == ["The problem", "How we help", "Our results"]


def test_body_mentions_are_not_used_to_invent_missing_journey_headings():
    source = _text_source(raw_text=f"About us\nWe solve problems for busy families. {SOLUTION}\nOur results\n{PROOF}", headings=["About us", "Our results"])
    assert journey_sections(source) == []


def test_wrapped_body_lines_cannot_be_mistaken_for_a_missing_problem_heading():
    source = _text_source(
        raw_text=f"Our solution\n{SOLUTION}\nWe address complex problems\nwith care designed around the working hours of busy families.\nOur results\n{PROOF}",
        headings=["Our solution", "Our results"],
    )
    assert journey_sections(source) == []


def test_contact_metadata_cannot_pad_out_thin_proof():
    source = _text_source(raw_text=f"The problem\n{PROBLEM}\nHow we help\n{SOLUTION}\nOur results\nGreat\nEmail: an-extremely-long-contact-address@acme.example", headings=[])
    assert journey_sections(source) == []


def test_action_extraction_stops_after_fifteen_unique_destinations(monkeypatch):
    from types import SimpleNamespace

    def bounded_matches(text):
        for index in range(15):
            yield SimpleNamespace(group=lambda _, index=index: f"contact{index}@acme.example")
        raise AssertionError("The bounded extractor must not collect the rest of a large document")

    monkeypatch.setattr("app.services.landing_source._EMAIL", SimpleNamespace(finditer=bounded_matches))
    assert len(text_action_links(_text_source())) == 15
