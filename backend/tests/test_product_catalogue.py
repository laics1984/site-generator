import unittest
from app.models.builder_schema import BodySchema, BuilderElement, CatalogueReference, GeneratedPage, GeneratedSite, PageSeo
from app.models.content_blocks import ServiceItem, ServicesBlock
from app.services.cms_sync import normalize_site_slugs
from app.services.product_catalogue import link_product_detail_pages
from app.services.section_content import _services_content
from app.services.template_filler import fill_template


def page(slug, title, elements):
    return GeneratedPage(slug=slug, title=title, body_schema=BodySchema(elements=elements), seo=PageSeo())

def card(name, path=None, kind="service"):
    return BuilderElement(name=name, type="container", content=[], catalogue=CatalogueReference(name=name, kind=kind, detailPath=path))

class ProductCatalogueTest(unittest.IsolatedAsyncioTestCase):
    async def test_structured_services_attach_identity_to_card_not_heading(self):
        content = _services_content(ServicesBlock(items=[ServiceItem(title="Aircon", description="Clean it", cta_href="/aircon")]))
        async def no_image(query):
            raise AssertionError("No image or model lookup expected")
        template = {"tree": {"name": "Grid", "type": "container", "styles": {}, "$repeat": "items", "content": [{"name": "Card", "type": "container", "styles": {}, "content": [{"name": "Title", "type": "text", "styles": {}, "$slot": "title"}]}]}}
        root = await fill_template(template, content, resolve_image=no_image)
        offer = root.content[0]
        self.assertEqual(offer.catalogue.name, "Aircon")
        self.assertIsNone(offer.catalogue.sourceKey)
        self.assertIsNone(offer.content[0].catalogue)
        self.assertEqual(offer.content[0].content.innerText, "Aircon")

    def test_named_detail_target_shares_source_identity_and_survives_serialization(self):
        offer = card("Aircon", "/services/aircon")
        site = GeneratedSite(site_name="Acme", pages=[page("services", "Services", [offer]), page("services/aircon", "Aircon", [BuilderElement(name="Copy", type="text", content={"innerText": "Original"})])])
        normalize_site_slugs(site, existing_slugs={"services-aircon"})
        link_product_detail_pages(site)
        restored = GeneratedSite.model_validate(site.model_dump(mode="json"))
        self.assertEqual(restored.pages[0].body_schema.elements[0].catalogue.sourceKey, "/services-aircon")
        root = restored.pages[1].body_schema.elements[0]
        self.assertEqual(root.type, "__body")
        self.assertEqual(root.catalogue.scope, "page")
        self.assertEqual(root.catalogue.sourceKey, "/services-aircon")
        self.assertEqual(root.content[0].content.innerText, "Original")

    def test_shared_contact_targets_and_ambiguous_offers_never_merge(self):
        a, b = card("Aircon", "/contact"), card("Plumbing", "/contact")
        site = GeneratedSite(site_name="Acme", pages=[page("services", "Services", [a, b]), page("contact", "Contact", [BuilderElement(name="Contact", type="section", content=[])])])
        link_product_detail_pages(site)
        self.assertIsNone(a.catalogue.sourceKey)
        self.assertIsNone(b.catalogue.sourceKey)
        self.assertIsNone(site.pages[1].body_schema.elements[0].catalogue)

    def test_external_and_fragment_targets_never_mark_detail_pages(self):
        for href in ["https://other.test/aircon", "/aircon#contact", "//other.test/aircon"]:
            offer = card("Aircon", href)
            detail = page("aircon", "Aircon", [BuilderElement(name="Section", type="section", content=[])])
            link_product_detail_pages(GeneratedSite(site_name="Acme", pages=[page("services", "Services", [offer]), detail]))
            self.assertIsNone(detail.body_schema.elements[0].catalogue)
