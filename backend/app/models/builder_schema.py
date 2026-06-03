"""
Pydantic models mirroring the webtree builder's BuilderElement schema.

Source of truth: webtree/builder/src/lib/site-navigation.ts
Keep these types in lock-step with that file. Any drift will produce
schemas the builder cannot open for editing.
"""

from __future__ import annotations

from typing import Any, Literal, Union
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


EditorBtns = Literal[
    "menu",
    "text",
    "container",
    "section",
    "contactForm",
    "paymentForm",
    "link",
    "2Col",
    "3Col",
    "video",
    "__body",
    "__header",
    "__footer",
    "image",
    "articlesList",
    "eventsList",
    "articleTitle",
    "articleBody",
    "articleImage",
    "articleExcerpt",
    "articleDate",
    "articleAuthor",
    "articleCategory",
    "articleTag",
    "archiveTitle",
    "archiveDescription",
    "eventTitle",
    "eventBody",
    "eventImage",
    "eventExcerpt",
    "eventDate",
    "eventLocation",
    "cmsArchiveHeader",
]


LinkTarget = Literal["_self", "_blank"]

# Menu element fields — mirror MenuSlot / MenuVariant / MenuColorMode in
# webtree/builder/src/lib/site-navigation.ts. A menu element renders shared
# navigation; the builder resolves its items via `slot` (or `menuId`) against
# the entity's `menus[]`.
MenuSlot = Literal["primary", "utility", "footer", "legal", "social"]
MenuVariant = Literal[
    "header-inline",
    "utility-inline",
    "footer-columns",
    "footer-legal",
    "social-inline",
    "vertical-list",
]
MenuColorMode = Literal["auto", "manual"]


class BuilderElementContent(BaseModel):
    """Leaf-level content for non-container elements (text, link, image, menu, etc.)."""

    model_config = ConfigDict(extra="allow")

    href: str | None = None
    innerText: str | None = None
    src: str | None = None
    alt: str | None = None
    width: str | None = None
    height: str | None = None
    caption: str | None = None
    target: LinkTarget | None = None
    rel: str | None = None
    ariaLabel: str | None = None
    # Menu element fields (type == "menu").
    menuId: str | None = None
    slot: MenuSlot | None = None
    variant: MenuVariant | None = None
    menuLabel: str | None = None
    colorMode: MenuColorMode | None = None


class ResponsiveStyles(BaseModel):
    model_config = ConfigDict(extra="allow")

    mobile: dict[str, Any] | None = None
    tablet: dict[str, Any] | None = None


class BuilderElement(BaseModel):
    """
    Mirrors the BuilderElement type in
    webtree/builder/src/lib/site-navigation.ts:30.

    `content` is either a recursive list of BuilderElements (containers,
    sections, columns) or a BuilderElementContent (leaf nodes).
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    type: EditorBtns
    styles: dict[str, Any] = Field(default_factory=dict)
    content: Union[list["BuilderElement"], BuilderElementContent] = Field(
        default_factory=list
    )
    classes: str | None = None
    visible: bool | None = None
    responsiveStyles: ResponsiveStyles | None = None


BuilderElement.model_rebuild()


class BodySchema(BaseModel):
    """Top-level body schema as expected by the builder's page payload."""

    elements: list[BuilderElement] = Field(default_factory=list)


class PageSeo(BaseModel):
    title: str | None = None
    description: str | None = None
    keywords: list[str] | None = None
    ogTitle: str | None = None
    ogDescription: str | None = None
    ogImage: str | None = None
    twitterCard: str | None = None
    structuredData: dict[str, Any] | list[dict[str, Any]] | None = None
    noindex: bool = False


class GeneratedPage(BaseModel):
    """Internal representation of a generated page before pushing to CMS."""

    slug: str
    title: str
    description: str | None = None
    is_homepage: bool = False
    body_schema: BodySchema
    seo: PageSeo
    parent_slug: str | None = None  # set on sub-pages for breadcrumbs + nav grouping


class PageNode(BaseModel):
    """Hierarchy node exposed alongside the flat ``pages`` list.

    The builder uses this to render multi-level navigation and to know which
    pages are siblings under a shared parent. Always present even for flat
    sites — small sites get a one-level-deep tree with no children.
    """

    slug: str
    title: str
    is_homepage: bool = False
    children: list["PageNode"] = Field(default_factory=list)


PageNode.model_rebuild()


class GeneratedSite(BaseModel):
    """A full site = a set of pages + global metadata + theme + header/footer."""

    site_name: str
    tagline: str | None = None
    primary_color: str | None = None
    secondary_color: str | None = None
    pages: list[GeneratedPage]
    page_tree: list[PageNode] = Field(
        default_factory=list,
        description=(
            "Top-level pages with nested children. Mirrors the ``pages`` list "
            "but preserves parent/child structure for the builder's navigation."
        ),
    )
    media_credits: list[str] = Field(
        default_factory=list,
        description=(
            "Attribution lines for third-party media (e.g. Pexels). Surface these "
            "in the footer to comply with provider attribution requirements."
        ),
    )
    # Theme + chrome. These let the webtree builder display the site as designed.
    theme: Any | None = Field(default=None, description="ThemeTokens (full design system).")
    builder_styles: dict[str, Any] | None = Field(
        default=None,
        description=(
            "BuilderStyles payload matching webtree/builder/src/lib/builder-styles.ts. "
            "Push this to PUT /pages/{id}/layout so the builder applies the theme."
        ),
    )
    google_fonts: list[str] = Field(
        default_factory=list,
        description="Google Fonts CSV identifiers to load in <link rel='stylesheet'>.",
    )
    brand: Any | None = Field(default=None, description="BrandIdentity used during generation.")
    header_schema: BuilderElement | None = None
    footer_schema: BuilderElement | None = None
