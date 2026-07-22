"""Tests for SEO structured data generation and audit."""

import unittest

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.services.seo import (
    breadcrumb_slug_chain,
    build_structured_data,
    detect_duplicate_seo,
    detect_orphan_pages,
    extract_og_image,
)
from app.services.ux_audit import audit_seo


# -- helpers ------------------------------------------------------------------

def _el(name, typ="container", children=None, **styles):
    content = children if children is not None else []
    return BuilderElement(name=name, type=typ, styles=styles, content=content)


def _img(name, src="https://example.com/photo.jpg", alt="photo"):
    return BuilderElement(
        name=name, type="image",
        content=BuilderElementContent(src=src, alt=alt),
    )


def _text(name, inner, **styles):
    return BuilderElement(
        name=name, type="text", styles=styles,
        content=BuilderElementContent(innerText=inner),
    )


def _page(slug, *, title="Page", seo=None, elements=None, is_homepage=False):
    return GeneratedPage(
        slug=slug,
        title=title,
        is_homepage=is_homepage,
        body_schema=BodySchema(elements=elements or []),
        seo=seo or PageSeo(),
    )


def _site(pages, header=None, footer=None):
    return GeneratedSite(
        site_name="Test Site",
        pages=pages,
        header_schema=header,
        footer_schema=footer,
    )


def _rules(findings):
    return {f.rule for f in findings}


# -- extract_og_image ---------------------------------------------------------

class OgImageExtractionTest(unittest.TestCase):
    def test_background_hero(self):
        hero = _el(
            "Hero", typ="section",
            backgroundImage="linear-gradient(rgba(0,0,0,.5),rgba(0,0,0,.5)),url('https://img.example.com/hero.jpg')",
        )
        result = extract_og_image([hero])
        self.assertEqual(result, "https://img.example.com/hero.jpg")

    def test_split_hero_image(self):
        hero_img = _img("Hero Image", src="https://img.example.com/split.jpg")
        hero = _el("Hero", typ="section", children=[hero_img])
        result = extract_og_image([hero])
        self.assertEqual(result, "https://img.example.com/split.jpg")

    def test_fallback_to_first_image(self):
        section = _el("About", typ="section", children=[
            _img("About Photo", src="https://img.example.com/about.jpg"),
        ])
        result = extract_og_image([section])
        self.assertEqual(result, "https://img.example.com/about.jpg")

    def test_no_images_returns_none(self):
        section = _el("About", typ="section", children=[
            _text("H1", "Hello"),
        ])
        self.assertIsNone(extract_og_image([section]))

    def test_skips_data_uri(self):
        img = BuilderElement(
            name="Placeholder", type="image",
            content=BuilderElementContent(src="data:image/svg+xml;base64,abc", alt=""),
        )
        self.assertIsNone(extract_og_image([img]))


# -- breadcrumb_slug_chain ----------------------------------------------------

class BreadcrumbChainTest(unittest.TestCase):
    def test_homepage(self):
        chain = breadcrumb_slug_chain("", {})
        self.assertEqual(chain, [("", "Home")])

    def test_single_level(self):
        chain = breadcrumb_slug_chain("about", {"about": "About Us"})
        self.assertEqual(chain, [("", "Home"), ("about", "About Us")])

    def test_nested(self):
        titles = {"services": "Services", "services/web-design": "Web Design"}
        chain = breadcrumb_slug_chain("services/web-design", titles)
        self.assertEqual(chain, [
            ("", "Home"),
            ("services", "Services"),
            ("services/web-design", "Web Design"),
        ])

    def test_unknown_slug_humanized(self):
        chain = breadcrumb_slug_chain("our-team", {})
        self.assertEqual(chain[1], ("our-team", "Our Team"))


# -- build_structured_data ---------------------------------------------------

class _FakeFaqBlock:
    kind = "faq"
    items = []

    def __init__(self, items):
        self.items = items


class _FakeFaqItem:
    def __init__(self, q, a):
        self.question = q
        self.answer = a


class StructuredDataTest(unittest.TestCase):
    def test_homepage_gets_organization_and_website(self):
        result = build_structured_data(
            page_slug="",
            page_title="Home",
            page_description="Welcome",
            page_type="home",
            is_homepage=True,
            site_name="Acme",
            brand_name="Acme Inc",
            logo_url="https://acme.com/logo.png",
            industry_category="saas",
            contact={"email": "hello@acme.com", "phone": "+1234567890"},
            blocks=[],
            breadcrumb_slugs=[("", "Home")],
        )
        self.assertIsNotNone(result)
        types = [s["@type"] for s in result]
        self.assertIn("Organization", types)
        self.assertIn("WebSite", types)

    def test_local_business_for_restaurant(self):
        result = build_structured_data(
            page_slug="",
            page_title="Home",
            page_description=None,
            page_type="home",
            is_homepage=True,
            site_name="Cafe",
            brand_name="Cafe Latte",
            logo_url=None,
            industry_category="restaurant",
            contact={"address": "123 Main St"},
            blocks=[],
            breadcrumb_slugs=[("", "Home")],
        )
        types = [s["@type"] for s in result]
        self.assertIn("LocalBusiness", types)

    def test_subpage_gets_breadcrumb_list(self):
        result = build_structured_data(
            page_slug="services",
            page_title="Services",
            page_description="Our services",
            page_type="services",
            is_homepage=False,
            site_name="Acme",
            brand_name="Acme",
            logo_url=None,
            industry_category=None,
            contact=None,
            blocks=[],
            breadcrumb_slugs=[("", "Home"), ("services", "Services")],
        )
        types = [s["@type"] for s in result]
        self.assertIn("BreadcrumbList", types)
        bc = next(s for s in result if s["@type"] == "BreadcrumbList")
        self.assertEqual(len(bc["itemListElement"]), 2)

    def test_faq_block_produces_faqpage(self):
        faq = _FakeFaqBlock([
            _FakeFaqItem("What is X?", "X is a thing."),
            _FakeFaqItem("How much?", "$100"),
        ])
        result = build_structured_data(
            page_slug="faq",
            page_title="FAQ",
            page_description="Common questions",
            page_type="faq",
            is_homepage=False,
            site_name="Acme",
            brand_name="Acme",
            logo_url=None,
            industry_category=None,
            contact=None,
            blocks=[faq],
            breadcrumb_slugs=[("", "Home"), ("faq", "FAQ")],
        )
        types = [s["@type"] for s in result]
        self.assertIn("FAQPage", types)
        faq_schema = next(s for s in result if s["@type"] == "FAQPage")
        self.assertEqual(len(faq_schema["mainEntity"]), 2)

    def test_no_structured_data_returns_none(self):
        result = build_structured_data(
            page_slug="about",
            page_title="About",
            page_description="About us",
            page_type="about",
            is_homepage=False,
            site_name="Acme",
            brand_name="Acme",
            logo_url=None,
            industry_category=None,
            contact=None,
            blocks=[],
            breadcrumb_slugs=[("", "Home")],
        )
        self.assertIsNone(result)


# -- detect_duplicate_seo ----------------------------------------------------

class DuplicateSeoTest(unittest.TestCase):
    def test_duplicate_titles_detected(self):
        pages = [
            _page("a", seo=PageSeo(title="Same Title")),
            _page("b", seo=PageSeo(title="Same Title")),
        ]
        dupes = detect_duplicate_seo(_site(pages))
        self.assertTrue(any(f == "seo_title" for _, _, f in dupes))

    def test_unique_titles_clean(self):
        pages = [
            _page("a", seo=PageSeo(title="Title A")),
            _page("b", seo=PageSeo(title="Title B")),
        ]
        dupes = detect_duplicate_seo(_site(pages))
        self.assertEqual(dupes, [])


# -- detect_orphan_pages -----------------------------------------------------

class OrphanPageTest(unittest.TestCase):
    def test_linked_page_not_orphan(self):
        link = BuilderElement(
            name="Nav link", type="link",
            content=BuilderElementContent(innerText="About", href="/about"),
        )
        pages = [
            _page("", is_homepage=True, elements=[link]),
            _page("about"),
        ]
        orphans = detect_orphan_pages(_site(pages))
        self.assertNotIn("about", orphans)

    def test_unlinked_page_is_orphan(self):
        pages = [
            _page("", is_homepage=True),
            _page("hidden"),
        ]
        orphans = detect_orphan_pages(_site(pages))
        self.assertIn("hidden", orphans)


# -- audit_seo ---------------------------------------------------------------

class SeoAuditTest(unittest.TestCase):
    def test_short_title_flagged(self):
        pages = [_page("", seo=PageSeo(title="Hi"))]
        findings = audit_seo(_site(pages))
        self.assertIn("seo-title-length", _rules(findings))

    def test_long_title_flagged(self):
        pages = [_page("", seo=PageSeo(title="A" * 70))]
        findings = audit_seo(_site(pages))
        self.assertIn("seo-title-length", _rules(findings))

    def test_good_title_clean(self):
        pages = [_page("", seo=PageSeo(title="A" * 55))]
        findings = audit_seo(_site(pages))
        self.assertNotIn("seo-title-length", _rules(findings))

    def test_duplicate_titles_flagged(self):
        pages = [
            _page("a", seo=PageSeo(title="Same Title Here For Both")),
            _page("b", seo=PageSeo(title="Same Title Here For Both")),
        ]
        findings = audit_seo(_site(pages))
        self.assertIn("seo-title-unique", _rules(findings))

    def test_missing_og_image_flagged(self):
        pages = [_page("", seo=PageSeo(title="Test", ogImage=None))]
        findings = audit_seo(_site(pages))
        self.assertIn("og-image-missing", _rules(findings))

    def test_og_image_present_clean(self):
        pages = [_page("", seo=PageSeo(title="Test", ogImage="https://x.com/img.jpg"))]
        findings = audit_seo(_site(pages))
        self.assertNotIn("og-image-missing", _rules(findings))

    def test_cta_missing_flagged(self):
        section = _el("About", children=[_text("H1", "About Us")])
        pages = [_page("", elements=[section])]
        findings = audit_seo(_site(pages))
        self.assertIn("cta-missing", _rules(findings))

    def test_cta_present_clean(self):
        cta = _el("CTA", typ="section", children=[_text("H2", "Get Started")])
        pages = [_page("", elements=[cta])]
        findings = audit_seo(_site(pages))
        self.assertNotIn("cta-missing", _rules(findings))

    def test_heading_hierarchy_no_h1_flagged(self):
        section = _el("Content", children=[_text("H2", "Subtitle")])
        pages = [_page("about", elements=[section])]
        findings = audit_seo(_site(pages))
        self.assertIn("heading-hierarchy", _rules(findings))

    def test_heading_hierarchy_one_h1_clean(self):
        section = _el("Content", children=[_text("H1", "Title")])
        pages = [_page("about", elements=[section])]
        findings = audit_seo(_site(pages))
        self.assertNotIn("heading-hierarchy", _rules(findings))

    def test_orphan_page_flagged(self):
        pages = [
            _page("", is_homepage=True),
            _page("orphan-page"),
        ]
        findings = audit_seo(_site(pages))
        self.assertIn("orphan-page", _rules(findings))
