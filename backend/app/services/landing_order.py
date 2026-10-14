"""Conservative, evidence-based landing order; no extra model or review step.

Facebook/PDF home and landing recipes can bind explicit text evidence to journey
stages while retaining other selected slots. Other sources keep the strict
one-to-one landing rule. Incomplete evidence, instructional sequences and a
missing action retain the recipe; repeated kinds keep their own source groups.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urlsplit

from app.models.content_blocks import SectionType, SourceContent
from app.models.facebook import FactLabel
from app.models.industry import ConversionSection, PageScaffold
from app.services.landing_source import (
    CONVERSION_SOURCE_KINDS, JOURNEY_ROLES, homepage_source, journey_sections,
    section_role, text_action_links,
)
from app.services.page_inference import source_section_kinds


def _has_action(scaffolds: list[PageScaffold], source: SourceContent) -> bool:
    if any("contact" in page.sections for page in scaffolds if not page.is_legal):
        return True
    for href in source.links:
        try:
            parsed = urlsplit(href)
        except ValueError:
            continue
        if parsed.scheme in {"mailto", "tel"} and parsed.path:
            return True
        if parsed.scheme in {"http", "https"} and parsed.netloc and re.search(
            r"\b(book|booking|contact|enquir[yie]|quote|register|checkout)\b",
            parsed.path.replace("-", " "), re.I,
        ):
            return True
    return False


def _prepare_source_journey(
    scaffold: PageScaffold, source: SourceContent, scaffolds: list[PageScaffold],
) -> tuple[PageScaffold, SourceContent] | None:
    """Adapt only Facebook/PDF home/landing recipes, retaining existing slots.

    Bindings distinguish repeated block kinds and give every journey stage its
    own source evidence. Extra selected sections (gallery, locations, contact,
    etc.) remain, in their relative order, before the closing action.
    """
    if source.source_kind not in CONVERSION_SOURCE_KINDS or scaffold.page_type not in {"home", "landing"}:
        return None
    if scaffold.page_type == "home" and not scaffold.is_homepage:
        return None
    if scaffold.is_homepage:
        source = homepage_source(source)
    evidence = journey_sections(source)
    if not evidence:
        return None
    links = list(dict.fromkeys([*source.links, *text_action_links(source)]))
    if links != source.links:
        source = source.model_copy(update={"links": links})
    if not _has_action(scaffolds, source):
        return None

    body = scaffold.sections[1:-1]
    available = Counter(body)
    used: Counter[SectionType] = Counter()
    source_kinds = source_section_kinds([section for _, section in evidence])
    bindings = []
    for (role, section), source_block_kind in zip(evidence, source_kinds):
        if section.cards:
            preferences = [source_block_kind]
        elif role == 1:
            preferences = [kind for kind in body if kind in {"features", "services"}] or ["about"]
        elif role == 2 and (
            section.heading.casefold() == FactLabel.REVIEWS.casefold()
            or (source.source_kind == "pdf" and re.search(r"\b(testimonials?|reviews?)\b", section.heading, re.I))
        ):
            preferences = ["testimonials"]
        elif role == 3:
            # The closing CTA remains the single primary ask. A source contact
            # section may additionally supply the existing contact-form slot.
            preferences = ["contact"] if "contact" in body else ["about"]
        else:
            preferences = ["about"]
        kind = next((k for k in preferences if available[k] > used[k]), preferences[0])
        used[kind] += 1
        bindings.append(ConversionSection(role=JOURNEY_ROLES[role], kind=kind, source=section, index=0))

    bindings.sort(key=lambda binding: JOURNEY_ROLES.index(binding.role))
    bindings = [
        ConversionSection(role=b.role, kind=b.kind, source=b.source, index=index)
        for index, b in enumerate(bindings, start=1)
    ]
    remaining = []
    for kind in body:
        if used[kind]:
            used[kind] -= 1
        else:
            remaining.append(kind)
    prepared = scaffold.model_copy(update={
        "sections": ["hero", *[b.kind for b in bindings], *remaining, scaffold.sections[-1]],
    })
    prepared._conversion_journey = tuple(bindings)
    return prepared, source


def prepare_landing_pages(
    scaffolds: list[PageScaffold], source_map: dict[str, SourceContent],
) -> tuple[list[PageScaffold], dict[str, SourceContent]]:
    """Reorder only when all stages exist and their relative order improves.

    Source/card objects are not edited. Stable sorting keeps same-role sections
    in their original order. Facebook/PDF homepages can use explicit text evidence
    and retain additional recipe slots. Other sources keep the original strict
    one-to-one landing rule.
    """
    prepared = []
    sources = dict(source_map)
    for scaffold in scaffolds:
        source = sources.get(scaffold.slug)
        can_prepare = (
            source is not None and not scaffold.is_legal and scaffold.locale is None
            and scaffold.translation_of is None and scaffold.record_set is None
            and scaffold.sections[:1] == ["hero"]
            and scaffold.sections[-1:] in (["cta"], ["contact"])
        )
        if can_prepare:
            conversion = _prepare_source_journey(scaffold, source, scaffolds)
            if conversion is not None:
                prepared.append(conversion[0])
                sources[scaffold.slug] = conversion[1]
                continue
        tree = source.section_candidates if source else []
        eligible = (
            scaffold.page_type == "landing" and not scaffold.is_legal
            and not scaffold.locale and not scaffold.record_set
            and 3 <= len(tree) <= 12
            and scaffold.sections[:1] == ["hero"]
            and scaffold.sections[-1:] in (["cta"], ["contact"])
            and scaffold.sections[1:-1] == source_section_kinds(tree)
        )
        roles = [section_role(section) for section in tree] if eligible else []
        if (
            not roles or any(role is None for role in roles)
            or not {0, 1, 2}.issubset(roles)
            or not _has_action(scaffolds, source)
        ):
            prepared.append(scaffold)
            continue
        order = sorted(range(len(tree)), key=lambda index: roles[index])
        if order == list(range(len(tree))):
            prepared.append(scaffold)
            continue
        # Reuse the original kind associated with each group. Recomputing after
        # sorting would change alternating offerings from services to features.
        body = scaffold.sections[1:-1]
        prepared.append(scaffold.model_copy(update={
            "sections": ["hero", *[body[index] for index in order], scaffold.sections[-1]],
        }))
        sources[scaffold.slug] = source.model_copy(update={
            "section_candidates": [tree[index] for index in order],
        })
    return prepared, sources
