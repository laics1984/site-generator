"""Rendering keeps the new mobile and conversion rules concrete."""

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.builder_schema import BodySchema, BuilderElement, BuilderElementContent, GeneratedPage, GeneratedSite, PageSeo, ResponsiveStyles
from app.models.content_blocks import ContactBlock, CtaBlock, HeroBlock, PagePlan, SitePlan
from app.services.header_footer import build_footer
from app.services.schema_builder import apply_mobile_usability, plan_to_site
from app.services.theme import build_theme
from app.services.ux_audit import audit_site


def _leaves(node):
    if isinstance(node.content, list):
        for child in node.content:
            yield from _leaves(child)
    else:
        yield node


def test_mobile_button_overrides_preserve_tablet_rules_and_larger_targets():
    button = BuilderElement(
        name="CTA", type="link", content=BuilderElementContent(innerText="Request information", href="/contact"),
        styles={"display": "inline-flex", "minHeight": "32px", "width": "420px"},
        responsiveStyles=ResponsiveStyles(mobile={"fontSize": "16px"}, tablet={"minHeight": "60px"}),
    )
    plain = BuilderElement(name="Nav", type="link", content=BuilderElementContent(innerText="About", href="/about"))
    site = GeneratedSite(site_name="Business", pages=[GeneratedPage(
        slug="", title="Home", body_schema=BodySchema(elements=[button]), seo=PageSeo(),
    )])
    assert {f.rule for f in audit_site(site)} >= {"mobile-touch-target", "mobile-fixed-width"}
    apply_mobile_usability([button, plain])
    assert button.responsiveStyles.mobile["minHeight"] == "44px"
    assert button.responsiveStyles.mobile["whiteSpace"] == "normal"
    assert button.responsiveStyles.mobile["fontSize"] == "16px"
    assert button.responsiveStyles.tablet == {"minHeight": "60px"}
    assert plain.responsiveStyles is None
    assert not {f.rule for f in audit_site(site)} & {"mobile-touch-target", "mobile-fixed-width"}
    button.responsiveStyles.mobile["minHeight"] = "64px"
    apply_mobile_usability([button])
    assert button.responsiveStyles.mobile["minHeight"] == "64px"


async def test_landing_hero_has_real_action_and_enquiry_fallback_has_honest_label(monkeypatch):
    monkeypatch.setattr(settings, "design_brain_enabled", False)
    page = PagePlan(page_type="landing", slug="offer", title="Offer", blocks=[
        HeroBlock(headline="A real service for your business", primary_cta_label="Book a call", primary_cta_href="/book-now"),
        CtaBlock(headline="Discuss your needs", cta_label="Buy now", cta_href="/checkout"),
    ])
    contact = PagePlan(page_type="contact", slug="contact", title="Contact", blocks=[ContactBlock()])
    result = await plan_to_site(SitePlan(site_name="Business", pages=[page, contact]), brand=BrandIdentity(name="Business"))
    hero_links = [el.content for el in _leaves(result.pages[0].body_schema.elements[0]) if el.type == "link"]
    assert any(link.href == "/contact" and link.innerText == "Request a call" for link in hero_links)
    links = [el.content for root in result.pages[0].body_schema.elements for el in _leaves(root) if el.type == "link"]
    assert any(link.href == "/contact" and link.innerText == "Enquire" for link in links)
    assert all(link.href not in {"/book-now", "/checkout"} for link in links)


def test_footer_banner_reuses_real_closing_copy_without_changing_action():
    footer = build_footer(
        BrandIdentity(name="Business", tagline="Original tagline"), build_theme("#2563eb"),
        nav_items=[], archetype="cta-banner", primary_cta=("Enquire", "/contact"),
        cta_headline="Discuss the right service for your business",
    )
    leaves = list(_leaves(footer))
    assert any(el.content.innerText == "Discuss the right service for your business" for el in leaves)
    assert any(el.type == "link" and el.content.href == "/contact" for el in leaves)
