"""A creative direction is chosen on the business's own evidence.

Root cause this pins (docs/design-intelligence-report.md): after the
design-identity work the look of a site was chosen by a seeded rotation over
generic schemes, and the industry's design knowledge no longer reached the
decision. Now directions are scored on personality, archetype, the business's
own words and its material, the seed only breaks ties, and the model choosing
the concept is told what each direction is FOR."""

from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.models.brand import AxisReading, BrandPersonality
from app.services import creative_directions as cd
from app.services.design_direction import (
    ContentProfile,
    _concept_prompt,
    resolve_direction,
    score_directions,
)
from app.services.theme import resolve_color_scheme


@pytest.fixture(autouse=True)
def _directions_and_schemes_on():
    saved = (settings.creative_directions_enabled, settings.design_schemes_enabled)
    settings.creative_directions_enabled = True
    settings.design_schemes_enabled = True
    yield
    settings.creative_directions_enabled, settings.design_schemes_enabled = saved


def _personality(**axes: int) -> BrandPersonality:
    return BrandPersonality(**{a: AxisReading(value=v, evidence="quoted") for a, v in axes.items()})


def _direction(name: str, industry: str, *, archetype: str | None, personality=None,
               mood="friendly", context: str = "", photos: int = 6, logo="#16a34a"):
    return asyncio.run(
        resolve_direction(
            brand_name=name,
            mood=mood,
            industry=industry,
            logo_hex=logo,
            personality=personality,
            business_context=context,
            content=ContentProfile(photos=photos, hero_photos=photos, prose_chars=4000),
            use_model=False,
            archetype=archetype,
        )
    )


def test_two_kindergartens_with_different_evidence_get_different_directions() -> None:
    playful = _direction(
        "Rainbow Yard", "childcare", archetype="kindergarten",
        personality=_personality(playful_sophisticated=-2, calm_energetic=2, minimal_expressive=2),
        context="Fun, hands-on projects and exciting adventures every day.", mood="playful",
    )
    academic = _direction(
        "Seri Mutiara", "childcare", archetype="kindergarten",
        personality=_personality(organic_structured=1, traditional_contemporary=-1, minimal_expressive=-1),
        context="KSPK curriculum in three languages for primary school readiness.",
    )
    assert playful.creative.slug == "expressive-contemporary"
    assert academic.creative.slug == "contemporary-academic"


def test_a_premium_montessori_lands_on_a_calm_direction() -> None:
    result = _direction(
        "Maple & Moss", "childcare", archetype="montessori-preschool", mood="luxury",
        personality=_personality(playful_sophisticated=2, minimal_expressive=-2, calm_energetic=-2, premium_accessible=-2),
        context="A premium Montessori casa with a calm prepared environment.",
    )
    assert result.creative.slug in ("contemporary-minimalist", "organic-editorial")


def test_a_gentle_nursery_gets_the_soft_pastel_direction_and_a_lively_daycare_does_not() -> None:
    gentle = _direction(
        "Little Nest", "childcare", archetype="nursery-daycare",
        personality=_personality(calm_energetic=-2, organic_structured=-2, playful_sophisticated=-1),
        context="A small home-like nursery with gentle routines and unhurried naps.",
    )
    lively = _direction(
        "Kinderloop", "childcare", archetype="nursery-daycare", mood="playful",
        personality=_personality(traditional_contemporary=2, calm_energetic=1, minimal_expressive=1),
        context="A modern daycare for working parents with app updates.",
    )
    assert gentle.creative.slug == "gentle-nurturing"
    assert gentle.theme.design_scheme in ("soft-nursery", "soft-warm")
    assert lively.creative.slug != "gentle-nurturing"


def test_a_developer_platform_is_developer_centric_and_dark() -> None:
    result = _direction(
        "Hookline", "saas", archetype="developer-platform", mood="technical",
        personality=_personality(organic_structured=2, traditional_contemporary=2, minimal_expressive=-1),
        context="An API and SDK for developers; open source CLI and docs.", photos=0,
    )
    assert result.creative.slug == "developer-centric"
    assert result.theme.color_scheme == "dark"


def test_saas_is_no_longer_dark_by_industry() -> None:
    """The industry lean made every SaaS site dark; a direction decides now."""
    product = cd.by_slug("product-led-minimal")
    assert resolve_color_scheme(None, None, None, industry="saas") == "dark"  # no direction: as before
    assert resolve_color_scheme(None, None, None, industry="saas", direction=product) == "light"
    # An explicit choice still beats any lean.
    assert resolve_color_scheme("dark", None, None, industry="saas", direction=product) == "dark"


def test_an_it_firm_is_not_designed_as_a_saas_product() -> None:
    result = _direction(
        "Kinetic Systems", "saas", archetype="it-services", mood="technical",
        personality=_personality(organic_structured=2, calm_energetic=-1, premium_accessible=1),
        context="Managed IT for local SMEs: helpdesk support you can trust.",
    )
    assert result.creative.slug not in ("b2b-saas", "product-led-minimal", "developer-centric")


def test_strong_evidence_beats_the_seed() -> None:
    """The same evidence under any brand name lands on the same direction."""
    personality = _personality(calm_energetic=-2, organic_structured=-2, playful_sophisticated=-1)
    picks = {
        _direction(name, "childcare", archetype="nursery-daycare", personality=personality,
                   context="gentle, home-like, unhurried naps").creative.slug
        for name in ("A", "Bee", "Cee House", "Delta Nursery", "Echo")
    }
    assert picks == {"gentle-nurturing"}


def test_with_no_evidence_the_seed_breaks_ties() -> None:
    directions = cd.directions_for("childcare", None)
    a = score_directions(directions, seed="Alpha", personality=None, archetype=None, evidence="", photos=None, recent=set())
    b = score_directions(directions, seed="Zulu Kids", personality=None, archetype=None, evidence="", photos=None, recent=set())
    assert {row[1] for row in a} == {row[1] for row in b}  # identical scores…
    assert [row[0].slug for row in a] != [row[0].slug for row in b]  # …order by seed only


def test_a_photo_carried_direction_is_held_back_without_photographs() -> None:
    documentary = cd.by_slug("documentary-story")
    assert cd.content_fit(documentary, 0) < cd.content_fit(documentary, 12)


def test_the_concept_prompt_names_each_direction_once() -> None:
    result = _direction(
        "Seri Mutiara", "childcare", archetype="kindergarten",
        personality=_personality(organic_structured=1, traditional_contemporary=-1),
        context="curriculum and primary school readiness",
    )
    prompt = _concept_prompt(
        list(result.concepts), brand_name="Seri Mutiara", mood="friendly", industry="childcare",
        logo_hex="#16a34a", personality=None, positioning=None, include_palette_context=False,
        archetype="kindergarten", audience="parents", business_context="curriculum",
    )
    assert "Kind of business: Kindergarten / preschool" in prompt
    for concept in result.concepts:
        assert prompt.count(concept.direction.label) == 1
    # Distinct directions, not three shades of one.
    assert len({c.direction.slug for c in result.concepts}) == len(result.concepts)


def test_the_theme_carries_the_direction_but_the_wire_does_not() -> None:
    result = _direction("Maple", "childcare", archetype="montessori-preschool")
    assert result.theme.creative_direction == result.creative.slug
    assert result.theme.business_archetype == "montessori-preschool"
    styles = result.theme.to_builder_styles()
    assert "creative_direction" not in str(styles) and "creativeDirection" not in str(styles)


def test_flag_off_is_the_scheme_path_unchanged() -> None:
    settings.creative_directions_enabled = False
    off = _direction("Maple", "childcare", archetype="montessori-preschool")
    assert off.creative is None
    assert all(c.direction is None for c in off.concepts)
