"""
Industry-aware page scaffolding.

`PageScaffold` is the recipe: which page (slug + title) + which section types
in what order. The LLM writes the *copy* for those sections; the structure is
ours, so every site has the page rhythm a designer would lay out.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.models.content_blocks import PageType, SectionType


IndustryCategory = Literal[
    "restaurant",
    "agency",
    "saas",
    "professional-services",
    "ecommerce",
    "consultancy",
    "nonprofit",
    "personal",
    "other",
]


class PageScaffold(BaseModel):
    """A single page's recipe: identity + section order.

    Hierarchy: a non-null ``parent_slug`` makes this a sub-page nested under
    another scaffold. The ``slug`` itself remains the full path (e.g.
    ``"services/web-design"`` for a child of ``"services"``).
    """

    page_type: PageType
    slug: str
    title: str
    sections: list[SectionType]
    description: str = ""
    is_homepage: bool = False
    is_legal: bool = False  # Privacy / Terms — generated from boilerplate, no LLM
    rationale: str = ""     # one-line why-this-page, shown in the UI picker
    parent_slug: str | None = None  # set on sub-pages, references the parent's slug
    source_url: str | None = None   # URL the sub-page was discovered/crawled at
    nav_rank: int | None = None     # source-nav position (0-based); None ⇒ not in the source header nav
    from_source: bool = False       # page evidenced by the source (crawled / nav / strip) vs template-injected


class IndustryTemplate(BaseModel):
    """A complete page set proposal for one industry."""

    industry: IndustryCategory
    label: str
    description: str
    core_pages: list[PageScaffold]       # always-on (with legal)
    suggested_pages: list[PageScaffold]  # pre-checked in the UI, removable
    optional_pages: list[PageScaffold] = Field(default_factory=list)  # unchecked


class PageRecipeResponse(BaseModel):
    """API payload returned by /api/pages/recipe.

    For hierarchical sites the picker uses ``inferred_pages`` (a tree built from
    the crawl) as the source of truth for what's pre-checked; ``template`` stays
    around for the optional pool the user can opt into. Older callers that
    only know about ``template`` keep working.
    """

    industry: IndustryCategory
    template: IndustryTemplate
    inferred_pages: list[PageScaffold] = Field(
        default_factory=list,
        description=(
            "Tree of pages inferred from the crawled source (or empty if no crawl). "
            "Pre-checked in the UI; combined with template.core_pages + legal."
        ),
    )
    all_industries: list[dict] = Field(default_factory=list)  # for the dropdown
    detected_brand: dict | None = Field(
        default=None,
        description=(
            "The DetectedBrand from the LLM call we made for industry detection. "
            "Frontend should pass this back into /generate/with-pages so the backend "
            "doesn't have to re-run the same LLM call."
        ),
    )
