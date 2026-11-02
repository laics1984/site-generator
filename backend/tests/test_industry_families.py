"""Industry-family UX: the childcare family and the SaaS family.

Every improvement is gated on the INDUSTRY, so every archetype in the family
inherits it, and every other industry is exactly as it was. Each is evidence
only — nothing here is shown unless the source states it.

* the enquiry form says what the header button asks for (contact_intents);
* fintech is a SaaS archetype with its own directions, offered only to it;
* the SaaS family's footer carries the source's regulatory line and app links;
* childcare earns an enrolment section only from steps the source shows, and
  hands the planner its safety facts verbatim;
* SaaS earns testimonials before its pricing, and a comparison table only
  when the plans can honestly be compared;
* the quality evaluator checks the family's conversion path.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from bs4 import BeautifulSoup

from app.config import settings
from app.models.builder_schema import BuilderElement
from app.models.content_blocks import (
    PricingBlock,
    PricingTier,
    SectionCandidate,
    SourceCard,
    SourceContent,
)
from app.models.industry import PageScaffold
from app.services import contact_intents, creative_directions as cd, design_quality
from app.models.industry_catalog import INDUSTRY_LABELS
from app.services.business_archetype import (
    EARLY_YEARS_ARCHETYPES,
    archetype_from_evidence,
    fits_industry,
)
from app.services.disclosures import from_footer
from app.services.family_evidence import (
    FooterFacts,
    family_signals,
    footer_facts,
    safety_facts,
)
from app.services.nav_extraction import app_store_link_for_href, social_link_for_href
from app.services.page_inference import _augment_sections
from app.services.planner import _build_scaffolded_user_prompt, DetectedBrand
from app.services.section_content import comparison_rows, _pricing_content, _pricing_preference

INDUSTRIES = sorted(INDUSTRY_LABELS)
SAAS_ARCHETYPES = ("saas-product", "developer-platform", "fintech", "it-services", "tech-consultancy")


def _walk(node):
    if isinstance(node, BuilderElement):
        yield node
        if isinstance(node.content, list):
            for child in node.content:
                yield from _walk(child)


# --- 1. the form says what the button asks for ------------------------------------

# The header labels as they were before the intent table existed: pinned so the
# move into contact_intents is proven to change no button anywhere.
_OLD_DIRECTION_LABELS = {
    "visit": "Arrange a Visit", "tour": "Request a Tour", "trial-class": "Request a Trial Class",
    "demo": "Request a Demo", "consult": "Request a Consultation", "project": "Start a Project",
    "assessment": "Request an Assessment", "call": "Talk to Us", "enquire": "Enquire Now",
}
_OLD_INDUSTRY_LABELS = {
    "restaurant": "Request a Table", "agency": "Start a Project", "saas": "Get Started",
    "professional-services": "Request a Consultation", "ecommerce": "Enquire About Products",
    "consultancy": "Request a Consultation", "nonprofit": "Get Involved", "childcare": "Request a Tour",
    "home-services": "Get a Free Quote", "health-wellness": "Book an Appointment",
    "real-estate": "Book a Viewing",
}


@pytest.mark.parametrize("direction", cd.DIRECTIONS, ids=lambda d: d.slug)
def test_no_header_button_changed_its_words(direction) -> None:
    old = next((_OLD_DIRECTION_LABELS[i] for i in direction.cta_strategy if i in _OLD_DIRECTION_LABELS), None)
    assert cd.contact_cta_label(direction) == old


@pytest.mark.parametrize("industry", INDUSTRIES)
def test_no_industry_button_changed_its_words(industry) -> None:
    label = contact_intents.label_for(contact_intents.intent_for(None, industry))
    assert label == _OLD_INDUSTRY_LABELS.get(industry, "Get in touch")


def _family_directions():
    for industry, archetypes in (("childcare", sorted(EARLY_YEARS_ARCHETYPES)), ("saas", SAAS_ARCHETYPES)):
        for archetype in archetypes:
            if not fits_industry(archetype, industry):
                continue
            yield industry, archetype, None
            for direction in cd.directions_for(industry, archetype):
                yield industry, archetype, direction


@pytest.mark.parametrize("industry,archetype,direction", list(_family_directions()),
                         ids=lambda v: getattr(v, "slug", v))
def test_every_family_member_gets_a_form_that_matches_its_button(industry, archetype, direction) -> None:
    saved = settings.creative_directions_enabled
    settings.creative_directions_enabled = True
    try:
        intent = contact_intents.intent_for(direction.cta_strategy if direction else None, industry)
    finally:
        settings.creative_directions_enabled = saved
    form = contact_intents.form_content(intent, industry)
    spec = contact_intents.INTENTS[intent]
    assert form["submitLabel"] == spec.submit
    assert "project brief" not in json.dumps(form).casefold()
    if industry == "childcare":
        assert form["fields"]["phone"]["mode"] == "required"
        assert "child" in form["fields"]["message"]["label"].casefold()


@pytest.mark.parametrize("industry", [i for i in INDUSTRIES if i not in ("childcare", "saas")])
def test_every_other_industry_keeps_the_renderer_default_form(industry) -> None:
    for intent in contact_intents.INTENTS:
        assert contact_intents.form_content(intent, industry) == {}


def test_the_built_form_matches_the_built_button() -> None:
    from scripts.measure_design_diversity import fixture_brand, fixture_plan, offline_generation
    from app.services.schema_builder import plan_to_site

    with offline_generation(schemes=True, history=False):
        site = asyncio.run(plan_to_site(
            fixture_plan("Little Acorns", "childcare"),
            brand=fixture_brand("Little Acorns", "#e11d48", "childcare"),
        ))
    forms = [n for page in site.pages for s in page.body_schema.elements for n in _walk(s) if n.type == "contactForm"]
    assert forms and all(getattr(f.content, "submitLabel", None) == "Request a tour" for f in forms)
    header_links = [n.content.innerText for n in _walk(site.header_schema) if n.type == "link"]
    assert "Request a Tour" in header_links


# --- 2. fintech, inside the SaaS family -------------------------------------------


def test_fintech_is_read_from_the_source_and_lives_only_in_saas() -> None:
    assert archetype_from_evidence("A licensed e-wallet regulated by Bank Negara Malaysia.", "saas") == "fintech"
    assert archetype_from_evidence("Buy now, pay later for online shoppers.", "saas") == "fintech"
    # A SaaS product that merely takes payments is still a SaaS product.
    assert archetype_from_evidence(
        "An invoicing SaaS platform: send invoices and accept payments. Free trial.", "saas"
    ) == "saas-product"
    assert not fits_industry("fintech", "professional-services")


def test_fintech_directions_are_offered_to_fintech_only_on_top_of_the_family() -> None:
    saved = settings.creative_directions_enabled
    settings.creative_directions_enabled = True
    try:
        fintech = {d.slug for d in cd.directions_for("saas", "fintech")}
        product = {d.slug for d in cd.directions_for("saas", "saas-product")}
        unknown = {d.slug for d in cd.directions_for("saas", None)}
    finally:
        settings.creative_directions_enabled = saved
    assert {"trusted-fintech", "consumer-fintech"} <= fintech
    assert not {"trusted-fintech", "consumer-fintech"} & (product | unknown)
    assert product <= fintech  # the family's directions are all still offered


# --- 3. the SaaS family's footer facts --------------------------------------------

_FOOTER_HTML = """<html><body><p>We are licensed by Bank Negara Malaysia.</p>
<a href="https://apps.apple.com/my/app/payku/id123456">Download</a>
<a href="https://play.google.com/store/apps/details?id=my.payku">Get it</a>
<a href="https://play.google.com/store">Store</a>
<footer><p>© 2026 PayKu Sdn Bhd. All rights reserved.</p>
<p>PayKu Sdn Bhd (1234567-X) is approved by Bank Negara Malaysia as an issuer of e-money.
Registered users can log in.</p></footer></body></html>"""


def test_the_disclosure_is_the_footers_own_sentence_verbatim() -> None:
    soup = BeautifulSoup(_FOOTER_HTML, "lxml")
    assert from_footer(soup) == [
        "PayKu Sdn Bhd (1234567-X) is approved by Bank Negara Malaysia as an issuer of e-money."
    ]
    # A verb without an authority, or an authority without a verb, states no licence.
    assert from_footer(BeautifulSoup("<footer>Join 2,000 registered users.</footer>", "lxml")) == []


def test_app_listings_are_not_social_profiles() -> None:
    base = "https://payku.my"
    assert app_store_link_for_href("https://apps.apple.com/my/app/payku/id1", base).label == "App Store"
    assert app_store_link_for_href("https://play.google.com/store/apps/details?id=x", base).label == "Google Play"
    assert app_store_link_for_href("https://play.google.com/store", base) is None
    assert social_link_for_href("https://apps.apple.com/my/app/payku/id1", base) is None


def _source(**kw) -> SourceContent:
    return SourceContent(source_kind="url", source_ref="https://payku.my", title="PayKu", raw_text="x", **kw)


def test_footer_facts_are_the_saas_familys_only() -> None:
    from app.models.content_blocks import NavLink

    source = _source(
        disclosures=["PayKu is approved by Bank Negara Malaysia."],
        app_links=[NavLink(label="App Store", href="https://apps.apple.com/my/app/payku/id1")],
    )
    facts = footer_facts(source, "saas")
    assert facts.disclosure == "PayKu is approved by Bank Negara Malaysia."
    assert facts.app_links == (("App Store", "https://apps.apple.com/my/app/payku/id1"),)
    for industry in INDUSTRIES:
        if industry != "saas":
            assert footer_facts(source, industry) == FooterFacts()


@pytest.mark.parametrize("archetype", ["mega", "cta-banner", "minimal-centered", "editorial"])
def test_every_footer_shows_the_facts_and_nothing_without_them(archetype) -> None:
    from app.models.brand import BrandIdentity
    from app.services.header_footer import build_footer
    from app.services.theme import build_theme

    theme = build_theme("#0f766e", mood="modern", industry="saas")
    brand = BrandIdentity(name="PayKu")
    facts = FooterFacts(
        disclosure="PayKu is approved by Bank Negara Malaysia.",
        app_links=(("App Store", "https://apps.apple.com/my/app/payku/id1"),),
    )
    footer = build_footer(brand, theme, nav_items=[], archetype=archetype,
                          primary_cta=("Request a Demo", "/contact"), facts=facts)
    names = {n.name for n in _walk(footer)}
    assert {"Regulatory disclosure", "App links", "App link"} <= names
    bare = build_footer(brand, theme, nav_items=[], archetype=archetype,
                        primary_cta=("Request a Demo", "/contact"))
    assert not {"Regulatory disclosure", "App links"} & {n.name for n in _walk(bare)}


# --- 4. the childcare family's evidence --------------------------------------------


def _with_sections(*candidates: SectionCandidate, raw_text: str = "x") -> SourceContent:
    return SourceContent(source_kind="url", source_ref="https://acorns.my", title="Acorns",
                         raw_text=raw_text, section_candidates=list(candidates))


def _steps(heading: str) -> SectionCandidate:
    return SectionCandidate(
        heading=heading, level=2, card_kind="steps",
        cards=[SourceCard(title=f"Step {i}", body="b") for i in (1, 2, 3)],
    )


def test_an_enrolment_process_is_earned_only_by_steps_the_source_shows() -> None:
    assert family_signals("childcare", _with_sections(_steps("How to enrol"))) == ["process"]
    # Wording alone asks for nothing a model would have to invent.
    assert family_signals("childcare", _with_sections(raw_text="Enrolment now open for 2027!")) == []
    # Steps under another heading are not an enrolment process.
    assert family_signals("childcare", _with_sections(_steps("Our curriculum"))) == []
    # The same structure means nothing outside the family.
    assert family_signals("agency", _with_sections(_steps("How to enrol"))) == []


def test_the_enrolment_section_joins_every_childcare_homepage_before_the_cta() -> None:
    page = _with_sections(_steps("Admissions process"))
    assert _augment_sections(["hero", "about", "cta"], "home", page, "childcare") == [
        "hero", "about", "process", "cta",
    ]
    assert _augment_sections(["hero", "about", "cta"], "home", page, "restaurant") == ["hero", "about", "cta"]


def test_saas_testimonials_land_before_pricing() -> None:
    page = SourceContent(source_kind="url", source_ref="https://x.io", title="X",
                         raw_text="What our customers say: it saved us hours.")
    sections = ["hero", "features", "process", "pricing", "faq", "cta"]
    assert _augment_sections(sections, "home", page, "saas") == [
        "hero", "features", "process", "testimonials", "pricing", "faq", "cta",
    ]
    assert _augment_sections(sections, "home", page, "agency") == sections


def test_safety_facts_are_the_sources_own_sentences() -> None:
    source = SourceContent(
        source_kind="url", source_ref="https://acorns.my", title="Acorns",
        raw_text=(
            "We love outdoor play. Acorns is registered with the Department of Social Welfare.\n"
            "All teachers hold first-aid certificates. Lunch is cooked fresh daily."
        ),
    )
    assert safety_facts(source, "childcare") == [
        "Acorns is registered with the Department of Social Welfare.",
        "All teachers hold first-aid certificates.",
    ]
    assert safety_facts(source, "saas") == []


def test_safety_facts_reach_only_the_pages_a_parent_checks_them_on() -> None:
    source = SourceContent(source_kind="url", source_ref="https://acorns.my", title="Acorns",
                           raw_text="All teachers hold first-aid certificates.")
    brand = DetectedBrand(site_name="Acorns", industry_category="childcare")
    home = PageScaffold(page_type="home", slug="", title="Home", is_homepage=True, sections=["hero"])
    faq = PageScaffold(page_type="faq", slug="faq", title="FAQ", sections=["faq"])
    assert "stated_safety_facts" in json.loads(_build_scaffolded_user_prompt(source, brand, [home]))
    assert "stated_safety_facts" not in json.loads(_build_scaffolded_user_prompt(source, brand, [faq]))
    other = DetectedBrand(site_name="Acorns", industry_category="saas")
    assert "stated_safety_facts" not in json.loads(_build_scaffolded_user_prompt(source, other, [home]))


# --- 5. pricing that can be compared ---------------------------------------------


def _tier(name: str, *features: str) -> PricingTier:
    return PricingTier(name=name, price="$10/mo", features=list(features))


def test_comparable_plans_make_a_table_and_absence_is_blank_not_a_cross() -> None:
    rows = comparison_rows([
        _tier("Starter", "Unlimited projects", "Email support"),
        _tier("Team", "Unlimited projects", "Email support", "SSO"),
    ])
    assert [r["feature"] for r in rows] == ["Unlimited projects", "Email support", "SSO"]
    assert rows[2]["cells"] == [{"mark": " "}, {"mark": "✓"}]


def test_incremental_or_disjoint_plans_are_never_tabled() -> None:
    assert comparison_rows([
        _tier("Starter", "1 project", "Email support"),
        _tier("Pro", "Everything in Starter, plus", "SSO"),
    ]) == []
    assert comparison_rows([
        _tier("A", "One", "Two"), _tier("B", "Three", "Four"),
    ]) == []


def test_the_table_is_preferred_only_when_it_has_rows() -> None:
    block = PricingBlock(tiers=[
        _tier("Starter", "Projects", "Support", "Exports"),
        _tier("Team", "Projects", "Support", "Exports", "SSO"),
    ])
    assert _pricing_preference(_pricing_content(block), block)[0] == "pricing-compare"
    flat = PricingBlock(tiers=[_tier("A", "One", "Two"), _tier("B", "Three", "Four")])
    assert _pricing_preference(_pricing_content(flat), flat) == ["pricing-tiers"]


@pytest.mark.parametrize("industry,expected", [("saas", "pricing-compare"), ("restaurant", "pricing-tiers")])
def test_the_table_renders_for_the_saas_family_only(industry, expected) -> None:
    from app.services.section_content import select_template

    block = PricingBlock(tiers=[
        _tier("Starter", "Projects", "Support", "Exports"),
        _tier("Team", "Projects", "Support", "Exports", "SSO"),
    ])
    content = _pricing_content(block)
    template = select_template(
        "pricing", content, preferred_ids=_pricing_preference(content, block),
        mood="modern", industry=industry,
    )
    assert template["id"] == expected


# --- 6. the family's conversion path, evaluated -----------------------------------


def test_an_edited_form_that_no_longer_matches_its_button_is_found() -> None:
    from scripts.measure_design_diversity import fixture_brand, fixture_plan, offline_generation
    from app.services.schema_builder import plan_to_site

    with offline_generation(schemes=True, history=False):
        site = asyncio.run(plan_to_site(
            fixture_plan("Little Acorns", "childcare"),
            brand=fixture_brand("Little Acorns", "#e11d48", "childcare"),
        ))
    assert "form-intent-mismatch" not in {f.code for f in design_quality.evaluate(site).findings}
    for page in site.pages:
        for section in page.body_schema.elements:
            for node in _walk(section):
                if node.type == "contactForm":
                    node.content.submitLabel = "Send"  # type: ignore[attr-defined]
    assert "form-intent-mismatch" in {f.code for f in design_quality.evaluate(site).findings}


def test_no_brand_seed_rotates_comparable_plans_back_into_cards() -> None:
    """Comparable plans are the content's shape, like photographs: the brand
    seed's variety rotation must leave the table leading (it did not — "PayKu"
    rendered tier cards in the first visual check)."""
    from app.services.section_content import block_to_section

    block = PricingBlock(tiers=[
        _tier("Starter", "Projects", "Support", "Exports"),
        _tier("Team", "Projects", "Support", "Exports", "SSO"),
    ])
    for seed in ("PayKu", "Acme", "Nimbus", "Ledgerly", "Kopi Pay", "Orbit"):
        template, _content = block_to_section(block, mood="modern", industry="saas", variety_seed=seed)
        assert template["id"] == "pricing-compare", seed
