"""Carry explicit structured offer identities to cards and unambiguous detail pages.

No text scraping, name guessing, or additional model calls. A detail page must
have an explicit internal card target. CMS owns ID creation and tenant validation.
"""
from urllib.parse import urlsplit

from app.models.builder_schema import BuilderElement, GeneratedSite


def _nodes(elements):
    for element in elements:
        yield element
        if isinstance(element.content, list):
            yield from _nodes(element.content)


def link_product_detail_pages(site: GeneratedSite) -> None:
    pages = {"/" + page.slug.strip("/"): page for page in site.pages if not page.is_homepage}
    candidates = {}
    for page in site.pages:
        for element in _nodes(page.body_schema.elements):
            ref = element.catalogue
            if not ref or ref.scope != "block" or not ref.detailPath:
                continue
            url = urlsplit(ref.detailPath)
            if url.scheme or url.netloc or url.fragment or not url.path.startswith("/"):
                continue
            path = "/" + url.path.strip("/")
            if path not in pages:
                continue
            # A shared detail target (e.g. /contact) must not become a product page.
            candidates.setdefault(path, {}).setdefault((ref.kind, ref.name), []).append(ref)
    for path, identities in candidates.items():
        page = pages[path]
        if len(identities) != 1 or not page.body_schema.elements:
            continue
        references = next(iter(identities.values()))
        reference = references[0]
        # Only an explicitly named offer page qualifies, never contact/listing pages.
        if page.title.strip().casefold() != (reference.name or "").strip().casefold():
            continue
        root = next((node for node in page.body_schema.elements if node.type == "__body"), None)
        if root is None:
            root = BuilderElement(id="__body", type="__body", name="Body", content=page.body_schema.elements)
            page.body_schema.elements = [root]
        # Only verified single-offer detail targets are stable source identities.
        for ref in references:
            ref.sourceKey = path
        root.catalogue = reference.model_copy(update={"scope": "page", "sourceKey": path})
