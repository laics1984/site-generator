"""
Industry-aware page scaffolding.

`PageScaffold` is the recipe: which page (slug + title) + which section types
in what order. The LLM writes the *copy* for those sections; the structure is
ours, so every site has the page rhythm a designer would lay out.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, PrivateAttr

from app.models.content_blocks import (
    IndustryCategoryLiteral,
    MultilingualPlan,
    PageType,
    SectionCandidate,
    SectionType,
)


# One spelling: the literal lives beside the rest of the vocabulary in
# content_blocks; this name is what the industry modules import.
IndustryCategory = IndustryCategoryLiteral


@dataclass(frozen=True)
class ConversionSection:
    """Private planning evidence bound to one occurrence in a page's recipe."""

    role: Literal["problem", "solution", "proof", "action"]
    kind: SectionType
    source: SectionCandidate
    index: int


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
    # A page the reader is meant to reach from a listing, not from a menu — a
    # committee member's own page, linked from the grid that names them. Set
    # when a roster's link, not a URL segment, chose this page's parent
    # (page_inference.roster_detail_links); menu_builder keeps these out of the
    # header dropdown and the footer column, as the source site does.
    menu_hidden: bool = False
    # Translated mirrors (/bm/committee): the language directory this page lives
    # under, and the slug it is a translation of ("" for the homepage). Both
    # None on a source-language page. A translated page is never planned from
    # scratch — it clones its counterpart's design and swaps the text.
    locale: str | None = None
    translation_of: str | None = None
    # One of a set of look-alike detail pages built from the same source
    # template (a catalogue's /product/* pages): the slug of the set's
    # exemplar, on every member. A record page is never planned by the content
    # LLM — its layout is chosen once per set and each page is filled verbatim
    # from its own source (services/record_pages.py).
    record_set: str | None = None
    # Prepared only for Facebook/PDF conversion flows. PrivateAttr survives
    # internal model_copy calls but never changes the picker/API/site schemas.
    _conversion_journey: tuple[ConversionSection, ...] = PrivateAttr(default=())


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
    theme_preview: dict | None = Field(
        default=None,
        description=(
            "Industry-aware preview theme (BuilderStyles shape) so the picker can "
            "show the tailored typography/colours before generation. The fonts here "
            "match what /generate/with-pages will produce (both derive from industry "
            "+ site name + mood)."
        ),
    )
    google_fonts: list[str] = Field(
        default_factory=list,
        description="Google Font families to preload for the preview theme.",
    )
    multilingual: MultilingualPlan | None = Field(
        default=None,
        description=(
            "How the site will present the source's languages, and why "
            "(multilingual_strategy.decide). inferred_pages already holds every "
            "page the plan publishes at its own URLs."
        ),
    )
    available_locales: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Languages a user may add (Q4): [{code, label}] from locale.LOCALES, "
            "so the picker never keeps a second list."
        ),
    )
