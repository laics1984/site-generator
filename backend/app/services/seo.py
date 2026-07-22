"""SEO structured data (JSON-LD) and validation helpers.

Produces schema.org JSON-LD dicts per page, consumed by the CMS renderer
to inject ``<script type="application/ld+json">`` into ``<head>``.  The
generator controls the DATA; the renderer controls the injection.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.builder_schema import (
    BuilderElement,
    BuilderElementContent,
    GeneratedSite,
)

_URL_RE = re.compile(r"url\(['\"]?(https?://[^'\")\s]+)['\"]?\)")

_LOCAL_INDUSTRIES = frozenset({
    "restaurant", "childcare", "professional-services",
})


# ---------------------------------------------------------------------------
# Structured data builders
# ---------------------------------------------------------------------------

def build_structured_data(
    *,
    page_slug: str,
    page_title: str,
    page_description: str | None,
    page_type: str,
    is_homepage: bool,
    site_name: str,
    brand_name: str,
    logo_url: str | None,
    industry_category: str | None,
    contact: dict[str, str] | None,
    blocks: list[Any],
    breadcrumb_slugs: list[tuple[str, str]],
) -> list[dict[str, Any]] | None:
    """Return a list of JSON-LD objects for one page, or None if empty."""
    schemas: list[dict[str, Any]] = []

    if is_homepage:
        schemas.append(
            _build_organization(brand_name, logo_url, contact, industry_category)
        )
        schemas.append(_build_website(site_name, page_description))

    if breadcrumb_slugs and len(breadcrumb_slugs) > 1:
        schemas.append(_build_breadcrumb_list(breadcrumb_slugs))

    faq_items = _extract_faq_items(blocks)
    if faq_items:
        schemas.append(_build_faq_page(faq_items))

    return schemas or None


def _build_organization(
    name: str,
    logo_url: str | None,
    contact: dict[str, str] | None,
    industry: str | None,
) -> dict[str, Any]:
    is_local = industry in _LOCAL_INDUSTRIES or (
        contact and contact.get("address")
    )
    org: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "LocalBusiness" if is_local else "Organization",
        "name": name,
    }
    if logo_url:
        org["logo"] = logo_url
    if contact:
        cp: dict[str, Any] = {"@type": "ContactPoint"}
        if contact.get("email"):
            cp["email"] = contact["email"]
        if contact.get("phone"):
            cp["telephone"] = contact["phone"]
            cp["contactType"] = "customer service"
        if len(cp) > 1:
            org["contactPoint"] = cp
        if contact.get("address"):
            org["address"] = {
                "@type": "PostalAddress",
                "streetAddress": contact["address"],
            }
    return org


def _build_website(
    name: str,
    description: str | None,
) -> dict[str, Any]:
    ws: dict[str, Any] = {
        "@context": "https://schema.org",
        "@type": "WebSite",
        "name": name,
    }
    if description:
        ws["description"] = description
    return ws


def _build_breadcrumb_list(
    crumbs: list[tuple[str, str]],
) -> dict[str, Any]:
    items = []
    for i, (slug, label) in enumerate(crumbs, 1):
        href = f"/{slug}" if slug else "/"
        items.append({
            "@type": "ListItem",
            "position": i,
            "name": label,
            "item": href,
        })
    return {
        "@context": "https://schema.org",
        "@type": "BreadcrumbList",
        "itemListElement": items,
    }


def _build_faq_page(
    items: list[tuple[str, str]],
) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "FAQPage",
        "mainEntity": [
            {
                "@type": "Question",
                "name": q,
                "acceptedAnswer": {
                    "@type": "Answer",
                    "text": a,
                },
            }
            for q, a in items
        ],
    }


def _extract_faq_items(blocks: list[Any]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for block in blocks:
        if getattr(block, "kind", None) != "faq":
            continue
        for item in getattr(block, "items", []):
            q = getattr(item, "question", None) or ""
            a = getattr(item, "answer", None) or ""
            if q.strip() and a.strip():
                items.append((q.strip(), a.strip()))
    return items


# ---------------------------------------------------------------------------
# og:image extraction from the rendered element tree
# ---------------------------------------------------------------------------

def extract_og_image(elements: list[BuilderElement]) -> str | None:
    """Best image URL for og:image, preferring the hero's resolved photo."""
    for el in elements:
        if getattr(el, "name", None) == "Hero":
            bg = (el.styles or {}).get("backgroundImage", "")
            if isinstance(bg, str):
                m = _URL_RE.search(bg)
                if m:
                    return m.group(1)
            found = _find_image_src(el)
            if found:
                return found
    for el in elements:
        found = _find_image_src(el)
        if found:
            return found
    return None


def _find_image_src(el: BuilderElement) -> str | None:
    content = el.content
    if isinstance(content, BuilderElementContent) and content.src:
        src = content.src
        if isinstance(src, str) and src.startswith(("http://", "https://")):
            return src
    if isinstance(content, list):
        for child in content:
            if isinstance(child, BuilderElement):
                found = _find_image_src(child)
                if found:
                    return found
    return None


# ---------------------------------------------------------------------------
# Breadcrumb slug chain (for JSON-LD, parallels visual breadcrumbs)
# ---------------------------------------------------------------------------

def breadcrumb_slug_chain(
    slug: str,
    title_map: dict[str, str],
) -> list[tuple[str, str]]:
    """``[(slug, label), ...]`` for BreadcrumbList JSON-LD.

    Always starts with ``("", "Home")``.  Uses ``title_map`` for known pages,
    falls back to humanising the slug segment.
    """
    chain: list[tuple[str, str]] = [("", "Home")]
    segments = slug.split("/") if slug else []
    accumulated: list[str] = []
    for seg in segments:
        accumulated.append(seg)
        joined = "/".join(accumulated)
        label = title_map.get(joined, seg.replace("-", " ").title())
        chain.append((joined, label))
    return chain


# ---------------------------------------------------------------------------
# Duplicate / orphan detection (advisory)
# ---------------------------------------------------------------------------

def detect_duplicate_seo(
    site: GeneratedSite,
) -> list[tuple[str, str, str]]:
    """Return ``(slug_a, slug_b, field)`` for pages sharing an SEO field value."""
    dupes: list[tuple[str, str, str]] = []
    pages = site.pages
    for i, a in enumerate(pages):
        for b in pages[i + 1 :]:
            if a.seo and b.seo:
                if (
                    a.seo.title
                    and b.seo.title
                    and a.seo.title == b.seo.title
                ):
                    dupes.append((a.slug, b.slug, "seo_title"))
                if (
                    a.seo.description
                    and b.seo.description
                    and a.seo.description == b.seo.description
                ):
                    dupes.append((a.slug, b.slug, "seo_description"))
    return dupes


def detect_orphan_pages(site: GeneratedSite) -> list[str]:
    """Return slugs of pages not linked from any other page, header, or footer."""
    all_slugs = {p.slug for p in site.pages if not p.is_homepage}
    linked: set[str] = set()

    def _collect_hrefs(el: BuilderElement) -> None:
        content = el.content
        if isinstance(content, BuilderElementContent):
            href = content.href
            if isinstance(href, str) and href.startswith("/"):
                linked.add(href.lstrip("/"))
        elif isinstance(content, list):
            for child in content:
                if isinstance(child, BuilderElement):
                    _collect_hrefs(child)

    for page in site.pages:
        for el in (page.body_schema.elements if page.body_schema else []):
            _collect_hrefs(el)
    for chrome in (site.header_schema, site.footer_schema):
        if isinstance(chrome, BuilderElement):
            _collect_hrefs(chrome)

    return [s for s in all_slugs if s not in linked]
