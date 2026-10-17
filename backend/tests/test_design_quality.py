"""The design-quality evaluator: findings, targeted correction, the build hook."""

from __future__ import annotations

import asyncio

from app.config import settings
from app.models.builder_schema import (
    BuilderElement,
    BuilderElementContent,
    BuilderElementMotion,
    GeneratedPage,
    BodySchema,
    GeneratedSite,
    PageSeo,
    SectionDivider,
    SectionDividerEdge,
)
from app.models.design_manifest import DesignManifest, DirectionSpec
from app.services import design_quality
from app.services.style_tokens import IMAGE_SHAPES


def _text(name: str, text: str, **styles) -> BuilderElement:
    return BuilderElement(name=name, type="text", styles=styles, content=BuilderElementContent(innerText=text))


def _link(href: str = "/contact") -> BuilderElement:
    return BuilderElement(name="CTA", type="link", content=BuilderElementContent(innerText="Visit", href=href))


def _section(name: str, *children: BuilderElement, **styles) -> BuilderElement:
    return BuilderElement(name=name, type="container", styles=styles, content=list(children))


def _site(sections: list[BuilderElement], *, anti: list[str] | None = None) -> GeneratedSite:
    manifest = DesignManifest(
        seed="t",
        direction=DirectionSpec(slug="gentle-nurturing", label="Gentle", anti_patterns=anti or []),
    )
    return GeneratedSite(
        site_name="T",
        pages=[GeneratedPage(
            slug="", title="Home", is_homepage=True,
            body_schema=BodySchema(elements=sections), seo=PageSeo(title="T", description="d"),
        )],
        design_manifest=manifest.model_dump(),
    )


def _codes(report) -> set[str]:
    return {f.code for f in report.findings if not f.corrected}


def test_a_hero_with_no_action_is_found() -> None:
    site = _site([_section("Hero - Split", _text("Heading", "Hello")), _section("CTA - Banner", _link())])
    assert "hero-without-action" in _codes(design_quality.evaluate(site))


def test_more_than_the_gradient_budget_is_found() -> None:
    grad = {"background": "linear-gradient(135deg, #0ea5e9, #22c55e)"}
    site = _site([
        _section("Hero - Split", _link()),
        _section("A", _text("Heading", "a"), **grad),
        _section("B", _text("Heading", "b"), **grad),
        _section("C", _text("Heading", "c"), **grad),
    ])
    assert "gradient-heavy" in _codes(design_quality.evaluate(site))


def test_a_scrim_over_a_photo_is_not_a_gradient_band() -> None:
    scrim = {"backgroundImage": "linear-gradient(#0008,#0008), url(https://x/p.jpg)"}
    site = _site([_section("Hero - Split", _link())] + [_section(f"S{i}", **scrim) for i in range(4)])
    assert "gradient-heavy" not in _codes(design_quality.evaluate(site))


def test_a_purple_blue_gradient_is_found_unless_the_brand_is_purple_blue() -> None:
    grad = {"background": "linear-gradient(135deg, #4f46e5, #9333ea)"}
    site = _site([_section("Hero - Split", _link(), **grad)])
    assert "purple-blue-gradient" in _codes(design_quality.evaluate(site))


def test_direction_anti_patterns_are_checked_only_for_that_direction() -> None:
    wave = SectionDivider(bottom=SectionDividerEdge(shape="wave", color="#ffffff"))
    section = _section("About - Split", _text("Heading", "x"))
    section.divider = wave
    with_rule = _site([_section("Hero - Split", _link()), section], anti=["wave-dividers"])
    without = _site([_section("Hero - Split", _link()), section.model_copy(deep=True)])
    assert "wave-dividers" in _codes(design_quality.evaluate(with_rule))
    assert "wave-dividers" not in _codes(design_quality.evaluate(without))


def test_rainbow_titles_count_hues_not_light_and_dark_ink() -> None:
    inked = [_section(f"S{i}", _text("Heading", "t", color=c)) for i, c in enumerate(["#f8fafc", "#0f172a", "#f8fafc"])]
    rainbow = [_section(f"S{i}", _text("Heading", "t", color=c)) for i, c in enumerate(["#dc2626", "#16a34a", "#2563eb"])]
    hero = _section("Hero - Split", _link())
    assert "rainbow-titles" not in _codes(design_quality.evaluate(_site([hero, *inked], anti=["rainbow-titles"])))
    assert "rainbow-titles" in _codes(design_quality.evaluate(_site([hero.model_copy(deep=True), *rainbow], anti=["rainbow-titles"])))


def test_correction_is_one_bounded_round_and_records_what_it_fixed() -> None:
    hero = _section("Hero - Split", _link())
    hero.motion = BuilderElementMotion(preset="aurora")
    site = _site([hero, _section("CTA - Banner", _link())], anti=["ambient-motion"])
    first = design_quality.evaluate_and_correct(site)
    assert first.corrections == 1
    assert any(f.code == "ambient-motion" and f.corrected for f in first.findings)
    assert "ambient-motion" not in _codes(first)
    assert hero.motion is None
    # Idempotent: a second pass has nothing left to correct.
    assert design_quality.evaluate_and_correct(site).corrections == 0


def test_an_organic_mask_on_a_portrait_falls_back_to_a_rectangle() -> None:
    photo = BuilderElement(
        name="Blob Photo", type="image", styles={"borderRadius": IMAGE_SHAPES["blob"]},
        content=BuilderElementContent(src="https://x/p.jpg", alt="p", width="800", height="1200"),
    )
    site = _site([_section("Hero - Playful Split", photo, _link())])
    report = design_quality.evaluate_and_correct(site)
    assert report.corrections == 1
    assert photo.styles["borderRadius"] == "28px"


def test_the_score_charges_a_finding_once_per_page() -> None:
    site = _site([_section("Hero - Split", _text("Heading", "x"))])
    report = design_quality.evaluate(site)
    assert 0 <= report.score <= 100


def test_a_broken_check_never_breaks_the_verdict(monkeypatch) -> None:
    def boom(_ctx):
        raise RuntimeError("bad tree")
        yield  # pragma: no cover

    monkeypatch.setattr(design_quality, "_CHECKS", (boom,))
    assert design_quality.evaluate(_site([])).score == 100


def test_the_build_records_quality_on_the_manifest() -> None:
    from scripts.measure_design_diversity import fixture_brand, fixture_plan, offline_generation
    from app.services.schema_builder import plan_to_site

    saved = settings.design_quality_enabled
    settings.design_quality_enabled = True
    try:
        with offline_generation(schemes=True, history=False):
            site = asyncio.run(plan_to_site(
                fixture_plan("Little Acorns", "childcare"),
                brand=fixture_brand("Little Acorns", "#e11d48", "childcare"),
            ))
    finally:
        settings.design_quality_enabled = saved
    quality = site.design_manifest["quality"]
    assert 0 <= quality["score"] <= 100
    from app.models.design_manifest import css_safe

    assert site.builder_styles["designManifest"]["quality"] == css_safe(quality)


def test_the_builder_styles_manifest_cannot_break_the_site_root_style() -> None:
    """Renderers flatten BuilderStyles (manifest included) into CSS custom
    properties on the site root; a server-rendered style attribute with an
    unbalanced quote swallows every later declaration — the colour and button
    tokens among them. The carried copy holds no CSS-significant character."""
    from app.models.design_manifest import builder_styles_manifest

    manifest = DesignManifest(
        seed="St. Mary's Kindergarten",
        direction=DirectionSpec(slug="creative-studio", label="Studio", imagery='the studio\'s own work; "uncropped" {x}'),
    )
    flat: list[str] = []

    def walk(value) -> None:
        if isinstance(value, dict):
            for v in value.values():
                walk(v)
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, str):
            flat.append(value)

    walk(builder_styles_manifest(manifest))
    assert flat
    assert not any(c in s for s in flat for c in "'\";{}\\")
    assert "St. Mary’s Kindergarten" in flat
