"""The business archetype — the generator's subcategory inside a CMS industry."""

from __future__ import annotations

import pytest

from app.services.business_archetype import (
    ARCHETYPES,
    TECH_ARCHETYPES,
    archetype_from_evidence,
    fits_industry,
    heal_archetype_value,
    resolve_archetype,
    spec_for,
)
from app.services.planner import DetectedBrand
from app.services.prompts import DETECT_BRAND_PROMPT


@pytest.mark.parametrize(
    ("text", "industry", "expected"),
    [
        ("An AMI-trained Montessori casa with a prepared environment.", "childcare", "montessori-preschool"),
        ("Tadika in Kajang following the KSPK preschool curriculum.", "childcare", "kindergarten"),
        ("Baby care and a toddler nursery with naps on their own schedule.", "childcare", "nursery-daycare"),
        ("After-school abacus and phonics classes; book a trial class.", "childcare", "enrichment-centre"),
        ("Managed IT support, helpdesk and network infrastructure for SMEs.", "saas", "it-services"),
        ("Our SDK and REST API let developers ship in minutes.", "saas", "developer-platform"),
        ("Accounts payable automation platform — free trial or book a demo.", "saas", "saas-product"),
        ("An independent design studio: brand identity and web design.", "agency", "digital-agency"),
        ("Digital marketing agency for SEO and paid social campaigns.", "agency", "marketing-agency"),
        ("Digital transformation and enterprise architecture for banks.", "consultancy", "tech-consultancy"),
    ],
)
def test_the_source_names_its_archetype(text: str, industry: str, expected: str) -> None:
    assert archetype_from_evidence(text, industry) == expected


def test_an_it_firm_filed_under_saas_is_not_a_saas_product() -> None:
    text = "IT services for Penang businesses: helpdesk, CCTV, cloud migration and 24/7 support."
    assert archetype_from_evidence(text, "saas") == "it-services"


def test_a_weak_cue_names_nothing() -> None:
    assert archetype_from_evidence("We run a platform.", "saas") is None
    assert archetype_from_evidence("Weekly classes.", "childcare") is None


def test_a_cue_in_the_wrong_industry_is_evidence_of_nothing() -> None:
    assert archetype_from_evidence("Montessori-inspired restaurant decor", "restaurant") is None


def test_matching_is_whole_word() -> None:
    # "api" inside "capital", "ui" inside "build": not cues.
    assert archetype_from_evidence("Capital to build a future", "saas") is None


def test_detection_wins_when_it_fits_the_industry() -> None:
    assert resolve_archetype("montessori-preschool", evidence="kindergarten", industry="childcare") == "montessori-preschool"


def test_detection_that_does_not_fit_falls_back_to_the_evidence() -> None:
    assert (
        resolve_archetype("saas-product", evidence="An AMI-trained Montessori casa", industry="childcare")
        == "montessori-preschool"
    )


def test_a_model_slip_heals_to_a_real_slug_or_nothing() -> None:
    assert heal_archetype_value("Montessori Preschool") == "montessori-preschool"
    assert heal_archetype_value("bakery") is None
    assert heal_archetype_value(None) is None
    assert DetectedBrand(site_name="x", archetype="IT_Services").archetype == "it-services"


def test_the_archetype_is_a_design_input_not_copy() -> None:
    brand = DetectedBrand(site_name="x", archetype="kindergarten")
    assert "archetype" not in brand.copy_payload()


def test_the_detection_prompt_offers_every_archetype_and_only_those() -> None:
    for slug in ARCHETYPES:
        assert f'"{slug}"' in DETECT_BRAND_PROMPT
    assert "__ARCHETYPE_ENUM__" not in DETECT_BRAND_PROMPT


def test_every_archetype_has_a_home_industry_and_copy_focus() -> None:
    for slug in ARCHETYPES:
        spec = spec_for(slug)
        assert spec is not None and spec.industries and spec.copy_guidance
        assert any(fits_industry(slug, industry) for industry in spec.industries)
    assert "kindergarten" not in TECH_ARCHETYPES and "it-services" in TECH_ARCHETYPES
