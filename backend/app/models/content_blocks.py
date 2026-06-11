"""
Semantic content blocks emitted by the LLM.

We deliberately do NOT ask the LLM to produce BuilderElement trees directly:
- prompt stays small (less drift, less token use, more reliable on 7B models)
- schema_builder.py owns the mapping → deterministic, testable
- model upgrades don't require re-prompting for layout details

The mapping back to BuilderElement section templates is implemented in
services/schema_builder.py and mirrors body-section-templates.ts.

Robustness strategy:
- For fields where the LLM commonly omits or nulls a value, we use
  `@field_validator(..., mode="before")` to substitute a sensible default
  instead of raising. Generation should heal, not fail, on minor LLM drift.
- The `ContentBlock` union is *discriminated* on `kind`, so a malformed hero
  block produces ~2 useful errors instead of 22 union-expansion phantoms.
- Truly critical missing data (e.g. headline) still raises so the retry can
  fix it.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


SectionType = Literal[
    "hero",
    "about",
    "features",
    "services",
    "testimonials",
    "cta",
    "faq",
    "contact",
    "pricing",
    "team",
    "gallery",
    "menu",
    "process",
]


PageType = Literal[
    "home",
    "landing",
    "services",
    "about",
    "contact",
    "testimonials",
    "pricing",
    "team",
    "gallery",
    "menu",
    "work",
    "process",
    "faq",
    "blog",
    "privacy",
    "terms",
    "thank-you",
]


def _default_if_blank(value: object, default: str) -> object:
    """Replace None / empty / whitespace-only strings with `default`."""
    if value is None:
        return default
    if isinstance(value, str) and not value.strip():
        return default
    return value


class VisualPolicy(BaseModel):
    """
    Per-section visual INTENT, consumed by the schema_builder luminance pass.

    The planner sets intent only (what the section wants); the luminance pass
    derives the actual luminance band, background colour, photo filter, and font
    colour. `visual_policy = None` on a block means "no opinion — infer me", which
    falls through to today's backgroundImage heuristic (back-compat).

    Full design + rules: SECTION_VISUAL_POLICY_SPEC.md.
    """

    visual_mode: Literal[
        "photo_background",  # wants a dominant background photo (flexible)
        "supporting_image",  # photo in a column / split → anchored
        "decorative",        # grain / texture, no real photo
        "plain",             # flat colour fill
        "auto",              # engine derives from kind + layout
    ] = "auto"

    image_source_preference: Literal[
        "scraped",   # prefer scraped pool, then fall down the ladder
        "stock",     # prefer Pexels
        "abstract",  # prefer generated/abstract, skip people photos
        "auto",
    ] = "auto"

    # Override of the calm (non-photo) rendering; engine picks when "auto".
    calm_treatment: Literal["plain", "grain", "auto"] = "auto"

    # Escape hatch. Bands are normally derived (anchored from image luminance, or
    # flexible from the page pass). Set to force a band against the rhythm.
    band_override: Literal["light", "dark", "auto"] = "auto"


class HeroBlock(BaseModel):
    kind: Literal["hero"] = "hero"
    eyebrow: str | None = None
    headline: str
    subheadline: str | None = None
    primary_cta_label: str = "Get started"
    primary_cta_href: str = "#contact"
    secondary_cta_label: str | None = None
    secondary_cta_href: str | None = None
    image_alt: str | None = None
    image_query: str | None = Field(
        default=None,
        description=(
            "Short search phrase (2-6 words) describing what the hero image should "
            "depict. E.g. 'artisan coffee beans roasting', 'modern dental clinic'."
        ),
    )
    layout: Literal["split", "background"] = Field(
        default="split",
        description=(
            "split = image on the right alongside text. "
            "background = full-bleed image behind text with dark overlay. "
            "Use 'background' for visual brands (restaurants, fitness, travel, agencies)."
        ),
    )
    visual_policy: VisualPolicy | None = None  # see SECTION_VISUAL_POLICY_SPEC.md

    @field_validator("primary_cta_label", mode="before")
    @classmethod
    def heal_cta_label(cls, v: object) -> object:
        return _default_if_blank(v, "Get started")

    @field_validator("primary_cta_href", mode="before")
    @classmethod
    def heal_cta_href(cls, v: object) -> object:
        return _default_if_blank(v, "#contact")

    @field_validator("layout", mode="before")
    @classmethod
    def heal_layout(cls, v: object) -> object:
        # LLM sometimes omits layout entirely → treat None as "split"
        return _default_if_blank(v, "split")


class FeatureItem(BaseModel):
    title: str
    description: str


class FeaturesBlock(BaseModel):
    kind: Literal["features"] = "features"
    heading: str = "Why choose us"
    subheading: str | None = None
    items: list[FeatureItem] = Field(min_length=1, max_length=6)
    visual_policy: VisualPolicy | None = None  # see SECTION_VISUAL_POLICY_SPEC.md

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Why choose us")


class ServiceItem(BaseModel):
    title: str
    description: str
    cta_label: str | None = None
    cta_href: str | None = None


class ServicesBlock(BaseModel):
    kind: Literal["services"] = "services"
    heading: str = "Our services"
    subheading: str | None = None
    # min_length=1 (not 2): a business with a single real service should keep it
    # rather than be forced to invent a second one to satisfy the schema.
    items: list[ServiceItem] = Field(min_length=1, max_length=8)
    visual_policy: VisualPolicy | None = None  # see SECTION_VISUAL_POLICY_SPEC.md

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Our services")


class TestimonialItem(BaseModel):
    quote: str
    author: str
    role: str | None = None
    avatar_query: str | None = Field(
        default=None,
        description=(
            "Short phrase for the author avatar, e.g. 'smiling professional woman'. "
            "Used to pull a portrait photo. Leave null if a real photo is undesired."
        ),
    )


class TestimonialsBlock(BaseModel):
    kind: Literal["testimonials"] = "testimonials"
    heading: str = "What clients say"
    items: list[TestimonialItem] = Field(min_length=1, max_length=6)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "What clients say")


class AboutBlock(BaseModel):
    kind: Literal["about"] = "about"
    heading: str = "About us"
    body: str = ""
    image_alt: str | None = None
    image_query: str | None = Field(
        default=None,
        description="Short search phrase for the supporting image (2-6 words).",
    )
    visual_policy: VisualPolicy | None = None  # see SECTION_VISUAL_POLICY_SPEC.md

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "About us")

    @field_validator("body", mode="before")
    @classmethod
    def heal_body(cls, v: object) -> object:
        # An empty body is fine — the builder editor will prompt the user to fill it.
        return v if isinstance(v, str) else ""


class FaqItem(BaseModel):
    question: str
    answer: str


class FaqBlock(BaseModel):
    kind: Literal["faq"] = "faq"
    heading: str = "Frequently asked questions"
    items: list[FaqItem] = Field(min_length=1, max_length=20)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Frequently asked questions")


class CtaBlock(BaseModel):
    kind: Literal["cta"] = "cta"
    headline: str = "Ready to get started?"
    subheadline: str | None = None

    @field_validator("headline", mode="before")
    @classmethod
    def heal_headline(cls, v: object) -> object:
        return _default_if_blank(v, "Ready to get started?")
    cta_label: str = "Get started"
    cta_href: str = "#contact"
    background_query: str | None = Field(
        default=None,
        description=(
            "Short search phrase for the background photo behind the CTA (2-6 words). "
            "Choose something atmospheric and on-brand."
        ),
    )
    visual_policy: VisualPolicy | None = None  # see SECTION_VISUAL_POLICY_SPEC.md

    @field_validator("cta_label", mode="before")
    @classmethod
    def heal_cta_label(cls, v: object) -> object:
        return _default_if_blank(v, "Get started")

    @field_validator("cta_href", mode="before")
    @classmethod
    def heal_cta_href(cls, v: object) -> object:
        return _default_if_blank(v, "#contact")


class ContactBlock(BaseModel):
    kind: Literal["contact"] = "contact"
    heading: str = "Get in touch"
    subheading: str | None = None
    email: str | None = None
    phone: str | None = None

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Get in touch")


class PricingTier(BaseModel):
    name: str
    price: str = Field(description="Display string like '$29/mo' or 'Custom'")
    description: str | None = None
    features: list[str] = Field(default_factory=list, max_length=10)
    cta_label: str = "Get started"
    cta_href: str = "#contact"
    highlighted: bool = False  # marks the recommended tier


class PricingBlock(BaseModel):
    kind: Literal["pricing"] = "pricing"
    heading: str = "Pricing"
    subheading: str | None = None
    tiers: list[PricingTier] = Field(min_length=1, max_length=4)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Pricing")


class TeamMember(BaseModel):
    name: str
    role: str
    bio: str | None = Field(
        default=None,
        description="Optional profile bio scraped from the source or written by the LLM.",
    )
    description: str | None = Field(
        default=None,
        description="Deprecated alias for bio; kept for backward compatibility.",
    )
    photo_url: str | None = Field(
        default=None,
        description=(
            "Resolved scraped portrait URL for this real person. Filled by code "
            "after planning; leave null in LLM output."
        ),
    )
    photo_alt: str | None = Field(
        default=None,
        description="Alt text for photo_url, usually the person's name.",
    )
    photo_query: str | None = Field(
        default=None,
        description="Pexels-search phrase for portrait photo, e.g. 'smiling professional woman'.",
    )

    @model_validator(mode="after")
    def sync_description_aliases(self) -> "TeamMember":
        if self.bio is None and self.description is not None:
            self.bio = self.description
        elif self.description is None and self.bio is not None:
            self.description = self.bio
        return self


class TeamBlock(BaseModel):
    kind: Literal["team"] = "team"
    heading: str = "Meet the team"
    subheading: str | None = None
    members: list[TeamMember] = Field(min_length=1, max_length=12)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Meet the team")


class GalleryItem(BaseModel):
    title: str | None = None
    caption: str | None = None
    image_query: str = Field(
        description="Pexels-search phrase for the photo, e.g. 'plated tasting menu close-up'."
    )


class GalleryBlock(BaseModel):
    kind: Literal["gallery"] = "gallery"
    heading: str = "Gallery"
    subheading: str | None = None
    items: list[GalleryItem] = Field(min_length=1, max_length=12)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Gallery")


class MenuItem(BaseModel):
    name: str
    description: str | None = None
    price: str | None = None


class MenuCategory(BaseModel):
    name: str
    items: list[MenuItem] = Field(min_length=1, max_length=20)


class MenuBlock(BaseModel):
    kind: Literal["menu"] = "menu"
    heading: str = "Menu"
    subheading: str | None = None
    categories: list[MenuCategory] = Field(min_length=1, max_length=8)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Menu")


class ProcessStep(BaseModel):
    title: str
    description: str


class ProcessBlock(BaseModel):
    kind: Literal["process"] = "process"
    heading: str = "How we work"
    subheading: str | None = None
    steps: list[ProcessStep] = Field(min_length=1, max_length=6)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "How we work")

    @model_validator(mode="before")
    @classmethod
    def heal_steps(cls, data: object) -> object:
        # Only normalise the shape — the LLM sometimes uses "items" instead of
        # "steps". We deliberately do NOT fabricate steps when none are present:
        # an ungrounded process section is omitted upstream (scaffold_enforcement)
        # rather than filled with invented copy. If a truly empty process block
        # slips through, min_length=1 rejects it and the LLM repair pass retries.
        if not isinstance(data, dict):
            return data
        if not data.get("steps") and data.get("items"):
            data = {**data, "steps": data["items"]}
        return data


class LinkBarLink(BaseModel):
    label: str
    href: str


class LinkBarBlock(BaseModel):
    """Announcement / quick-links strap — a slim accent-colored bar with a short
    label and a row of links (e.g. "Current Releases: Dart Sass · LibSass").

    Never produced by the LLM: it's injected deterministically when the scrape
    finds a one-off link strap in the source page body (nav_extraction.
    find_linkbar_cluster). Kept in the ContentBlock union so it flows through
    PagePlan → schema_builder like any other section.
    """

    kind: Literal["linkbar"] = "linkbar"
    label: str | None = None
    links: list[LinkBarLink] = Field(min_length=1, max_length=6)


# Discriminated union — Pydantic dispatches on the `kind` field, so a malformed
# block produces a focused error list against just that variant instead of all 8.
ContentBlock = Annotated[
    HeroBlock
    | FeaturesBlock
    | ServicesBlock
    | TestimonialsBlock
    | AboutBlock
    | FaqBlock
    | CtaBlock
    | ContactBlock
    | PricingBlock
    | TeamBlock
    | GalleryBlock
    | MenuBlock
    | ProcessBlock
    | LinkBarBlock,
    Field(discriminator="kind"),
]


class PagePlan(BaseModel):
    """The LLM's blueprint for a single page.

    ``parent_slug`` carries hierarchy through the pipeline so the schema_builder
    can wire breadcrumbs and cross-links between parent and child pages.
    """

    page_type: PageType
    slug: str
    title: str
    description: str = ""
    is_homepage: bool = False
    blocks: list[ContentBlock]
    seo_title: str
    seo_description: str
    seo_keywords: list[str] = Field(default_factory=list)
    parent_slug: str | None = None
    nav_rank: int | None = None  # source-nav position from the scaffold; never set by the LLM
    from_source: bool = False    # page evidenced by the source site; never set by the LLM

    @field_validator("description", mode="before")
    @classmethod
    def heal_description(cls, v: object) -> object:
        # Description is nice-to-have for the page card; an empty string is fine.
        return _default_if_blank(v, "")

    @field_validator("seo_title", mode="before")
    @classmethod
    def heal_seo_title(cls, v: object, info: object) -> object:  # noqa: ARG003
        # If missing, leave it for the second-pass retry to fill — but accept
        # an empty string gracefully so other validation can proceed.
        return v if isinstance(v, str) and v.strip() else ""

    @field_validator("seo_description", mode="before")
    @classmethod
    def heal_seo_description(cls, v: object) -> object:
        return v if isinstance(v, str) and v.strip() else ""

    @model_validator(mode="before")
    @classmethod
    def drop_empty_content_blocks(cls, data: object) -> object:
        """The LLM is told to OMIT a section it can't ground with real content,
        but it sometimes emits the block with an empty list instead (e.g.
        ``{"kind":"testimonials","items":[]}``). That trips the block's
        ``min_length=1`` and 502s the whole generation, so we drop such blocks
        here — the intended "omit empty section" outcome — rather than reject the
        plan. Blocks without a content list (hero/cta/about/contact) are untouched.
        """
        if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
            return data
        # kind -> content list field(s) that must be non-empty to keep the block.
        list_fields = {
            "features": ("items",), "services": ("items",), "testimonials": ("items",),
            "faq": ("items",), "gallery": ("items",), "team": ("members",),
            "process": ("steps", "items"), "pricing": ("tiers",), "menu": ("categories",),
        }
        kept = []
        for block in data["blocks"]:
            if isinstance(block, dict):
                fields = list_fields.get(block.get("kind"))
                if fields and not any(block.get(f) for f in fields):
                    continue  # empty-content section — drop instead of failing
            kept.append(block)
        return {**data, "blocks": kept}


BrandMood = Literal[
    "modern",
    "luxury",
    "friendly",
    "technical",
    "editorial",
    "playful",
]


IndustryCategoryLiteral = Literal[
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


class SitePlan(BaseModel):
    """The LLM's blueprint for the entire site."""

    site_name: str
    tagline: str | None = None
    brand_summary: str = ""
    brand_mood: BrandMood = Field(
        default="modern",
        description=(
            "The brand's visual personality. Drives typography pairing, button radius, "
            "and section rhythm. Pick the closest match: "
            "modern=SaaS/fintech/tech; luxury=hospitality/jewellery/real-estate; "
            "friendly=consumer/wellness/lifestyle; technical=engineering/B2B/dev-tools; "
            "editorial=media/agencies/portfolios; playful=entertainment/food/kids."
        ),
    )
    industry_category: IndustryCategoryLiteral = Field(
        default="other",
        description=(
            "The business industry. Drives the suggested page set. Pick the closest: "
            "restaurant, agency, saas, professional-services (legal/dental/medical), "
            "ecommerce, consultancy, nonprofit, personal, or other."
        ),
    )
    primary_color_hint: str | None = Field(
        default=None,
        description="Suggested primary brand color as hex, e.g. '#2563eb'",
    )
    pages: list[PagePlan]

    @field_validator("brand_summary", mode="before")
    @classmethod
    def heal_brand_summary(cls, v: object) -> object:
        return _default_if_blank(v, "")

    @field_validator("brand_mood", mode="before")
    @classmethod
    def heal_mood(cls, v: object) -> object:
        if v is None or (isinstance(v, str) and not v.strip()):
            return "modern"
        return v


class SourceContent(BaseModel):
    """Normalized input passed to the LLM, regardless of source (URL or doc).

    For crawled sites, ``discovered_pages`` carries the structure-bearing
    output of the bounded crawler: one entry per same-domain page found (the
    primary page itself stays the top-level instance — discovered_pages lists
    the *additional* pages). ``url_path`` is set on crawled sub-page instances
    only.
    """

    source_kind: Literal["url", "pdf", "docx"]
    source_ref: str
    title: str | None = None
    description: str | None = None
    raw_text: str
    headings: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    url_path: str | None = Field(
        default=None,
        description=(
            "Relative path on the origin (e.g. '/services/web-design'). Set only on "
            "entries inside discovered_pages — the primary page leaves this null."
        ),
    )
    discovered_pages: list["SourceContent"] = Field(
        default_factory=list,
        description=(
            "Additional same-domain pages discovered by the crawler. Each entry "
            "carries its own raw_text/headings/links. The crawler caps the count "
            "and depth; the planner uses these to infer sub-page structure."
        ),
    )
    image_metadata: list["ImageMetadata"] = Field(
        default_factory=list,
        description=(
            "Per-image metadata parallel to `images`: alt text, intent guess, "
            "dimensions. Used by services/image_match.py to score scraped images "
            "against the LLM's image_query phrases. `images` stays a flat URL list "
            "for frontend thumbnail rendering; image_metadata is the rich version."
        ),
    )
    profile_candidates: list["ProfileCandidate"] = Field(
        default_factory=list,
        description=(
            "Likely team/committee profile cards extracted from the source. "
            "Additive and optional so older payloads that only provide text/images "
            "continue to validate unchanged."
        ),
    )
    nav_links: list["NavLink"] = Field(
        default_factory=list,
        description=(
            "The source page's header navigation as an ordered tree: top-level "
            "items in display order, dropdown items nested as children. This is "
            "the site owner's own curation — page_inference uses membership, "
            "order, and nesting as hierarchy + priority evidence."
        ),
    )
    body_link_clusters: list["LinkCluster"] = Field(
        default_factory=list,
        description=(
            "Link-dense blocks found inside the page body (outside header/nav/"
            "footer). Template menu strips that live in the content container "
            "land here; clusters repeated across sibling pages are classified "
            "as chrome (local subnav / duplicated nav) rather than content."
        ),
    )
    social_links: list["NavLink"] = Field(
        default_factory=list,
        description=(
            "Social profile links found anywhere on the page (label = platform "
            "name, href = full profile URL). One per platform, share/intent "
            "URLs excluded. Feeds the generated site's menu-social."
        ),
    )


class ImageMetadata(BaseModel):
    """Per-image hints from the extractor — used by the resolver's scorer."""

    url: str
    alt: str = ""
    intent: Literal["hero", "about", "logo", "generic"] = "generic"
    width: int | None = None
    height: int | None = None
    # Luminance-band inputs for the schema_builder pass (SECTION_VISUAL_POLICY_SPEC.md
    # §4.3). Dominant colour comes free from Pexels avg_color or a generated base —
    # NO pixel download. luminance/band stay None until set by media.py.
    dominant_color: str | None = None  # hex, e.g. Pexels avg_color
    luminance: float | None = None  # 0.0 (black)..1.0 (white) from dominant_color
    band: Literal["light", "dark"] | None = None  # luminance thresholded


class ProfileCandidate(BaseModel):
    """A likely real person profile extracted near a source portrait."""

    name: str
    role: str | None = None
    bio: str | None = None
    photo_url: str | None = None
    photo_alt: str | None = None
    source_url: str | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class NavLink(BaseModel):
    """One navigation item from the source site's header nav.

    ``href`` is kept as the raw same-site path (e.g. ``/services/web-design``
    or ``/#pricing``) so inference can map it onto discovered pages or anchors.
    ``children`` carries dropdown/submenu items, one level is typical but the
    extractor preserves whatever nesting the markup has.
    """

    label: str
    href: str
    children: list["NavLink"] = Field(default_factory=list)


class LinkCluster(BaseModel):
    """A link-dense block found in the page body during extraction.

    ``links`` is the ordered (label, href) list; ``href_key`` is a stable hash
    of the sorted href set so repeated template strips can be matched across
    pages without comparing labels (which may vary in active-state markup).
    """

    links: list[NavLink] = Field(default_factory=list)
    href_key: str = ""
    context_label: str = Field(
        default="",
        description=(
            "The block's non-link text (e.g. 'Current Releases:'), used as the "
            "label when the cluster is promoted to a linkbar section."
        ),
    )


NavLink.model_rebuild()
SourceContent.model_rebuild()
