"""What KIND of business this is, inside its industry — the generator's subcategory.

The industry taxonomy is the CMS's (13 slugs, ``models/industry_catalog.py``)
and stays that way: a Montessori casa, a tuition centre and a baby nursery are
all ``childcare`` there, and an IT-support shop, a developer API and an
invoicing app are all ``saas``. For DESIGN they are different businesses with
different audiences and different first questions, so the generator reads an
archetype on top of the industry — never pushed, never shown as a category.

Two producers, one answer (``resolve_archetype``):

* the brand-detection call already reads the source, so it is asked to name an
  archetype in the same reply (no extra call);
* ``archetype_from_evidence`` reads the source text deterministically — the
  fallback when detection is off, fails, or names nothing, and the only reader
  on ``/from-source``.

Matching is whole-word, as ``facebook_source.industry_for`` learned the hard
way ("pub" inside "Public Figure"). An archetype is only ever valid inside its
own industries: a cue in the wrong industry is evidence of nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, get_args

BusinessArchetype = Literal[
    "kindergarten",
    "montessori-preschool",
    "nursery-daycare",
    "enrichment-centre",
    "saas-product",
    "developer-platform",
    "it-services",
    "digital-agency",
    "marketing-agency",
    "tech-consultancy",
]

ARCHETYPES: tuple[str, ...] = get_args(BusinessArchetype)
EARLY_YEARS_ARCHETYPES: frozenset[str] = frozenset(
    {"kindergarten", "montessori-preschool", "nursery-daycare", "enrichment-centre"}
)
TECH_ARCHETYPES: frozenset[str] = frozenset(ARCHETYPES) - EARLY_YEARS_ARCHETYPES


@dataclass(frozen=True)
class ArchetypeSpec:
    slug: str
    label: str
    industries: frozenset[str]
    # (pattern, weight). Patterns are matched whole-word, case-insensitively.
    # A weight-3 cue names the archetype outright ("Montessori"); weight-1 cues
    # only add up.
    cues: tuple[tuple[str, int], ...]
    # One line for the copy planner: what THIS kind of business's visitor needs
    # to find out first. Facts still come only from the source.
    copy_guidance: str


_SPECS: tuple[ArchetypeSpec, ...] = (
    ArchetypeSpec(
        "montessori-preschool", "Montessori preschool", frozenset({"childcare"}),
        (
            (r"montessori", 3), (r"ami[- ]trained", 2), (r"ams", 1),
            (r"prepared environment", 2), (r"practical life", 2), (r"work cycles?", 2),
            (r"casa", 1), (r"nido", 1), (r"sensorial", 2), (r"guides?", 1),
            (r"mixed[- ]age", 1),
        ),
        "explain the method plainly (the prepared environment, mixed ages, the work "
        "cycle) as THIS school practises it, then the communities by age and a visit "
        "to observe; never claim an accreditation the source does not state",
    ),
    ArchetypeSpec(
        "nursery-daycare", "Nursery / daycare", frozenset({"childcare"}),
        (
            (r"nurser(y|ies)", 2), (r"day ?care", 2), (r"cr[eè]che", 2),
            (r"baby care", 2), (r"infant care", 2), (r"infants?", 1), (r"bab(y|ies)", 1),
            (r"toddlers?", 1), (r"nap(s|time)?", 1), (r"extended hours", 1),
            (r"working parents", 1),
        ),
        "lead with care and routine — ages taken, the day's rhythm, carers, hours and "
        "how parents stay informed — then availability; reassurance over curriculum",
    ),
    ArchetypeSpec(
        "enrichment-centre", "Enrichment / tuition centre", frozenset({"childcare", "other"}),
        (
            (r"enrichment", 3), (r"tuition", 3), (r"abacus", 2), (r"mental arithmetic", 2),
            (r"coding for kids", 2), (r"phonics", 1), (r"after[- ]school", 1),
            (r"weekend classes", 2), (r"trial class(es)?", 2), (r"learning centre", 1),
            (r"classes", 1), (r"term(ly)? schedules?", 1),
        ),
        "lead with the classes themselves — subject, age range, schedule, class size — "
        "and the trial or enrolment step; outcomes only as the source states them",
    ),
    ArchetypeSpec(
        "kindergarten", "Kindergarten / preschool", frozenset({"childcare"}),
        (
            (r"kindergartens?", 2), (r"tadika", 3), (r"pre-?schools?", 1),
            (r"pre-?school curriculum", 2), (r"kspk", 2), (r"primary school", 1),
            (r"school readiness", 2), (r"early (learning|childhood)", 1),
            (r"ages? (4|5|four|five)", 1),
        ),
        "lead with what a child learns and how the day runs — programmes by age, "
        "languages, teachers, the learning approach — then fees/visit/enrolment as "
        "the source states them",
    ),
    ArchetypeSpec(
        "developer-platform", "Developer platform", frozenset({"saas"}),
        (
            (r"apis?", 2), (r"sdks?", 3), (r"developers?", 2), (r"open[- ]source", 2),
            (r"cli", 2), (r"webhooks?", 2), (r"documentation", 1), (r"docs", 1),
            (r"github", 2), (r"endpoints?", 1),
        ),
        "lead with what a developer builds and how fast — the core primitive, a real "
        "code or workflow example when the source has one, docs and pricing; no "
        "invented benchmarks",
    ),
    ArchetypeSpec(
        "it-services", "IT services", frozenset({"saas", "consultancy", "professional-services", "other"}),
        (
            (r"managed (it|services)", 3), (r"it support", 3), (r"it services?", 3),
            (r"it solutions?", 2), (r"help ?desk", 2), (r"network(ing)? infrastructure", 2),
            (r"cloud migration", 1), (r"cyber ?security", 1), (r"cctv", 2),
            (r"system integration", 2), (r"servers?", 1), (r"hardware", 1),
            (r"24/7 support", 1), (r"on[- ]site", 1),
        ),
        "lead with the problems handled for the client — services, response and "
        "support terms, industries or business sizes served, how onboarding works — "
        "and an assessment or call; reliability shown through stated facts only",
    ),
    ArchetypeSpec(
        "saas-product", "SaaS product", frozenset({"saas"}),
        (
            (r"saas", 2), (r"platform", 1), (r"software", 1), (r"apps?", 1),
            (r"free trial", 2), (r"\d+-day trial", 2), (r"book a demo", 2), (r"demo", 1),
            (r"subscriptions?", 1), (r"automat(e|ion|ed)", 1), (r"workflows?", 1),
            (r"integrations?", 1), (r"dashboards?", 1),
        ),
        "lead with the outcome the product gives its user, then capabilities, the "
        "workflow, integrations and pricing as supplied; one primary action (trial "
        "or demo, whichever the source offers)",
    ),
    ArchetypeSpec(
        "marketing-agency", "Marketing agency", frozenset({"agency", "other"}),
        (
            (r"digital marketing", 3), (r"seo", 2), (r"social media", 2),
            (r"performance marketing", 3), (r"paid (ads|media|social)", 2),
            (r"content marketing", 2), (r"lead generation", 2), (r"campaigns?", 1),
            (r"marketing agency", 3),
        ),
        "lead with the channels and the client outcomes the source documents, then "
        "process and work; never invent metrics, clients or ROI",
    ),
    ArchetypeSpec(
        "digital-agency", "Digital / design agency", frozenset({"agency", "personal", "other"}),
        (
            (r"design studio", 3), (r"studio", 1), (r"web design", 2),
            (r"web development", 1), (r"brand(ing| identity)", 2), (r"art direction", 2),
            (r"case stud(y|ies)", 2), (r"portfolio", 1), (r"selected work", 2),
            (r"ux", 1), (r"ui", 1), (r"creative agency", 3), (r"digital agency", 3),
        ),
        "lead with the work — real projects first, then capabilities, process and how "
        "to start a project; only documented outcomes, never invented clients",
    ),
    ArchetypeSpec(
        "tech-consultancy", "Technology consultancy", frozenset({"consultancy", "professional-services", "saas", "other"}),
        (
            (r"digital transformation", 3), (r"enterprise architecture", 3),
            (r"technology (consulting|consultancy|advisory)", 3), (r"it consulting", 3),
            (r"data governance", 2), (r"cloud (strategy|adoption)", 2),
            (r"core banking", 1), (r"transformation", 1), (r"advisory", 1), (r"cio", 1),
        ),
        "lead with the decisions and problems the firm helps with, the expertise and "
        "sectors behind it, and how an engagement runs; credibility only from stated "
        "experience",
    ),
)

_BY_SLUG: dict[str, ArchetypeSpec] = {spec.slug: spec for spec in _SPECS}
_COMPILED: dict[str, tuple[tuple[re.Pattern[str], int], ...]] = {
    spec.slug: tuple(
        (re.compile(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", re.IGNORECASE), weight)
        for pattern, weight in spec.cues
    )
    for spec in _SPECS
}
# The least evidence that names an archetype: one decisive cue, or a few
# supporting ones. A lone "platform" or "classes" names nothing.
_MIN_SCORE = 3


def spec_for(archetype: str | None) -> ArchetypeSpec | None:
    return _BY_SLUG.get(archetype or "")


def fits_industry(archetype: str | None, industry: str | None) -> bool:
    spec = spec_for(archetype)
    return spec is not None and (industry or "other") in spec.industries


def archetype_scores(text: str, industry: str | None) -> dict[str, int]:
    """Cue score per archetype valid for this industry (zeros omitted)."""
    scores: dict[str, int] = {}
    for spec in _SPECS:
        if not fits_industry(spec.slug, industry):
            continue
        score = sum(
            weight for pattern, weight in _COMPILED[spec.slug] if pattern.search(text)
        )
        if score:
            scores[spec.slug] = score
    return scores


def archetype_from_evidence(text: str | None, industry: str | None) -> str | None:
    """The archetype the source text names, or None when it names none clearly.

    Highest score wins; a tie goes to the more specific archetype (table order:
    Montessori before kindergarten, developer platform before SaaS product)."""
    if not text:
        return None
    scores = archetype_scores(text, industry)
    if not scores:
        return None
    best = max(scores.values())
    if best < _MIN_SCORE:
        return None
    return next(spec.slug for spec in _SPECS if scores.get(spec.slug) == best)


def heal_archetype_value(value: object) -> str | None:
    """A model's archetype, kept only when it is one of ours."""
    if not isinstance(value, str):
        return None
    slug = value.strip().lower().replace("_", "-").replace(" ", "-")
    return slug if slug in _BY_SLUG else None


def resolve_archetype(
    detected: str | None, *, evidence: str | None, industry: str | None
) -> str | None:
    """One precedence: the detection model's archetype when it fits the
    resolved industry, else what the source text names, else None."""
    if fits_industry(detected, industry):
        return detected
    return archetype_from_evidence(evidence, industry)


def copy_guidance_for(archetype: str | None) -> str | None:
    spec = spec_for(archetype)
    return spec.copy_guidance if spec else None


def prompt_enum() -> str:
    return "|".join(f'"{slug}"' for slug in ARCHETYPES)
