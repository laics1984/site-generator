"""
Routes the platform publishes that are not a site's own pages.

A site hosted on the CMS serves its article/event TEMPLATE pages at their own
slugs (`/article-template`, …) and lists them in its sitemap, so a crawl of a
site this tool generated — the update flow's normal input — found
"Article template" as a page, typed it `services`, and the model wrote copy for
a blank. Pushed back, that page collides with the real template's slug.

The template contract lives here and nowhere else: the push creates these
pages when a site's list elements need them (`push_orchestrator`), the crawler
refuses to read them back (`scraper._is_crawlable_link`). Titles and
descriptions mirror the builder's TEMPLATE_DEFAULTS
(builder/src/lib/page-management.ts) so a template created by either side is
indistinguishable from the other's.
"""

from __future__ import annotations

from typing import Any

# The body the CMS gives a page the moment it creates one —
# App\Support\PageBuilderDefaults::bodySchema(), mirrored here because ADOPTING
# a page as a template has to leave it exactly as a freshly created one would
# be. An article renders from its template's bodySchema (webtree-public
# ContentDetail.vue), so a template still carrying a page's old marketing
# sections would publish every article AS those sections.
# test_platform_routes.BlankTemplateBodyTest parses the PHP and fails on drift.
BLANK_TEMPLATE_BODY: dict[str, Any] = {
    "elements": [
        {
            "id": "__body",
            "type": "__body",
            "name": "Body",
            "styles": {"minHeight": "10px", "height": "800px"},
            "classes": "w-full",
            "content": [],
        }
    ]
}

TEMPLATE_PAGE_DEFAULTS: dict[str, tuple[str, str, str]] = {
    "article": (
        "Article Template",
        "Default layout used to render every published article.",
        "article-template",
    ),
    "event": (
        "Event Template",
        "Default layout used to render every published event.",
        "event-template",
    ),
    "articleListing": (
        "Article Listing Template",
        "Default layout used to render article index, category, and tag listing pages.",
        "article-listing-template",
    ),
    "eventListing": (
        "Event Listing Template",
        "Default layout used to render the event index page.",
        "event-listing-template",
    ),
}

TEMPLATE_PAGE_SLUGS: frozenset[str] = frozenset(
    slug for _title, _description, slug in TEMPLATE_PAGE_DEFAULTS.values()
)


def is_template_page_path(path: str) -> bool:
    """Whether a URL path is one of the platform's template pages."""
    return path.strip("/").lower() in TEMPLATE_PAGE_SLUGS
