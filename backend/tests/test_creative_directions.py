"""The creative-direction library: vocabulary and reachability.

A direction written in the wrong vocabulary is a silent off switch (the
`profile-centered` lesson in CLAUDE.md): a scheme slug that does not exist, a
landing pattern nobody defines, an anti-pattern the evaluator never checks, an
image shape the shape pass cannot draw. Each is pinned here."""

from __future__ import annotations

from typing import get_args

import pytest

from app.config import settings
from app.models.industry_catalog import INDUSTRY_LABELS
from app.services import creative_directions as cd
from app.services.business_archetype import ARCHETYPES
from app.services.design_direction import direction_realisers
from app.services.design_quality import IMPLEMENTED_ANTI_PATTERNS
from app.services.design_schemes import SCHEMES_BY_SLUG, HeroLead
from app.services.landing_patterns import PATTERN_NAMES
from app.services.style_tokens import IMAGE_SHAPES


@pytest.fixture(autouse=True)
def _directions_on():
    original = settings.creative_directions_enabled
    settings.creative_directions_enabled = True
    yield
    settings.creative_directions_enabled = original


@pytest.mark.parametrize("direction", cd.DIRECTIONS, ids=lambda d: d.slug)
class TestVocabulary:
    def test_gates_are_real_industries_and_archetypes(self, direction) -> None:
        assert direction.industries and direction.industries <= set(INDUSTRY_LABELS)
        assert direction.archetypes and direction.archetypes <= set(ARCHETYPES)

    def test_realised_by_at_least_two_real_schemes(self, direction) -> None:
        assert len(direction.schemes) >= 2
        assert set(direction.schemes) <= set(SCHEMES_BY_SLUG)

    def test_every_field_is_in_a_vocabulary_something_reads(self, direction) -> None:
        assert direction.image_shape in IMAGE_SHAPES
        assert set(direction.hero_leads) <= set(get_args(HeroLead))
        assert set(direction.section_emphasis) <= PATTERN_NAMES
        assert set(direction.anti_patterns) <= IMPLEMENTED_ANTI_PATTERNS
        assert set(direction.anti_patterns) <= set(get_args(cd.AntiPattern))
        assert direction.cta_strategy and cd.contact_cta_label(direction)

    def test_briefs_fit_their_token_budget(self, direction) -> None:
        assert len(direction.brief) <= 300
        assert len(direction.imagery) <= 400

    def test_realisable_for_every_industry_it_claims(self, direction) -> None:
        for industry in direction.industries:
            for mood in ("friendly", "modern", "luxury", "playful", "technical", "editorial"):
                assert direction_realisers(direction, mood, industry), (industry, mood)


@pytest.mark.parametrize(
    "archetype",
    ["kindergarten", "montessori-preschool", "nursery-daycare", "enrichment-centre",
     "saas-product", "it-services", "digital-agency", "tech-consultancy"],
)
def test_each_benchmark_archetype_has_at_least_three_directions(archetype: str) -> None:
    home = {
        "kindergarten": "childcare", "montessori-preschool": "childcare",
        "nursery-daycare": "childcare", "enrichment-centre": "childcare",
        "saas-product": "saas", "it-services": "saas",
        "digital-agency": "agency", "tech-consultancy": "consultancy",
    }[archetype]
    assert len(cd.directions_for(home, archetype)) >= 3


def test_childcare_has_a_calm_pastel_direction_and_a_loud_one() -> None:
    slugs = {d.slug for d in cd.directions_for("childcare", None)}
    assert {"gentle-nurturing", "expressive-contemporary"} <= slugs
    gentle = cd.by_slug("gentle-nurturing")
    assert "pastel" in gentle.palette_voice and "rainbow-titles" in gentle.anti_patterns


def test_an_archetype_carries_its_directions_across_industries() -> None:
    # An IT firm filed as `consultancy` still gets technology directions.
    slugs = {d.slug for d in cd.directions_for("consultancy", "it-services")}
    assert "human-centered-tech" in slugs
    # …but a management consultancy with no tech archetype gets none.
    assert cd.directions_for("consultancy", None) == []


def test_industries_without_directions_get_none() -> None:
    for industry in ("restaurant", "real-estate", "health-wellness", "other"):
        assert cd.directions_for(industry, None) == []


def test_the_flag_off_is_no_directions_at_all() -> None:
    settings.creative_directions_enabled = False
    assert cd.directions_for("childcare", "kindergarten") == []
    assert cd.archetype_for_source(None, object(), "childcare") is None


def test_a_header_cta_never_promises_a_destination_it_does_not_have() -> None:
    # The header button always routes to the contact page: no WhatsApp chat,
    # no self-serve trial behind it.
    for direction in cd.DIRECTIONS:
        label = cd.contact_cta_label(direction)
        assert "WhatsApp" not in label and "Free Trial" not in label


def test_planner_context_is_small_and_names_a_real_hero_layout() -> None:
    for direction in cd.DIRECTIONS:
        context = cd.planner_context(direction)
        assert context["hero"] in ("split", "background")
        assert sum(len(v) for v in context.values()) < 900
