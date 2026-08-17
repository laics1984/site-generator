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

from datetime import datetime
from typing import Annotated, Literal, get_args, get_origin

from pydantic import (
    BaseModel,
    Field,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)


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
    "profile",
    "gallery",
    "menu",
    "process",
    "timeline",
    "awards",
    "clients",
    "stats",
    "locations",
    "downloads",
    "video",
    "map",
]


# Kinds re-attached from the source deterministically, never authored by the LLM.
#
# The model is not shown a schema for these (they're absent from
# prompts._SCAFFOLD_BLOCK_SCHEMAS) — but that alone is NOT protection. Absence
# from the schema list only withholds the shape; `planner._scaffold_payload`
# still puts the kind NAME into `required_sections` in the user prompt, and
# `PagePlan.salvage_page_content` validates each block against the whole
# `ContentBlock` union. A model that guesses the shape produces a structurally
# valid block that `align_page_to_scaffold` then matches to the slot, and
# nothing errors. `downloads` already reaches `required_sections` this way, via
# `page_inference._CARD_KIND_SECTIONS`.
#
# What these kinds carry is not prose but FACTS with external referents — a
# download href, a video id, a pinned map coordinate. An invented one is not
# vague, it is *wrong*, and it looks authoritative: a hallucinated 11-character
# YouTube id is a stranger's video embedded on a client's site, and a guessed
# map pin sends their customers to the wrong street. So it is enforced on both
# sides —
# the model is never invited (planner), and its output for these kinds is
# discarded even if it volunteers one (scaffold_enforcement).
DETERMINISTIC_SECTION_KINDS: frozenset[str] = frozenset(
    {"downloads", "linkbar", "video", "map"}
)


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
    "events",
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


# A button is a promise of one action, so its label is a verb phrase, not a
# sentence. Long labels are almost always a source NAV link that leaked into the
# slot ("Learn More About Our Committee Members" under a "Join Our Effort"
# headline) — the button then sends the reader somewhere the headline never
# offered. Trim at the first function word that still leaves a usable verb
# phrase, so "Learn More About Our Committee Members" → "Learn More" and any
# already-tight label ("Join Our Community", 18 chars) is untouched.
_CTA_LABEL_MAX_CHARS = 24
_CTA_LABEL_MAX_WORDS = 4
_CTA_LABEL_STOP_WORDS = frozenset({
    "about", "our", "the", "your", "their", "with", "for", "from", "of", "on",
    "in", "at", "by", "and", "or", "to", "into", "all",
})


def _heal_cta_label(value: object, default: str) -> object:
    """Blank → `default`; an over-long label → its leading verb phrase.

    Cuts at the first function word from position 2 on (so a two-word verb
    phrase always survives), then drops any function word left dangling at the
    end — a button reading "Register For" is worse than one reading "Register".
    """
    healed = _default_if_blank(value, default)
    if not isinstance(healed, str) or len(healed) <= _CTA_LABEL_MAX_CHARS:
        return healed
    words = healed.split()
    cut = next(
        (i for i, w in enumerate(words) if i >= 2 and w.lower() in _CTA_LABEL_STOP_WORDS),
        min(len(words), _CTA_LABEL_MAX_WORDS),
    )
    kept = words[:cut]
    while len(kept) > 1 and kept[-1].lower() in _CTA_LABEL_STOP_WORDS:
        kept.pop()
    return " ".join(kept)


def _heal_image_ref(value: object) -> int | None:
    """Coerce an LLM-emitted image_ref onto a non-negative int, else None.

    The ref indexes source_router.promptable_images for the page; a junk value
    (string, float, negative, invented object) must never fail the plan — it
    just falls back to the block's image_query stock search.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _coerce_literal(value: object, allowed: frozenset[str], default: str) -> str:
    """Coerce an LLM-emitted enum-ish value onto its Literal set.

    Known values pass through (case/whitespace-normalised); anything else —
    None, an invented adjective, the wrong type — degrades to the field's safe
    default instead of failing the whole plan (same policy as
    HeroBlock.heal_layout).
    """
    if isinstance(value, str):
        token = value.strip().lower()
        if token in allowed:
            return token
    return default


def heal_brand_mood_value(value: object) -> str | None:
    """Coerce an LLM brand_mood onto the BrandMood Literal; None when missing or
    invalid, so callers can fall back to an industry-derived default rather than a
    blanket 'modern' (see industry_default_mood)."""
    healed = _coerce_literal(value, frozenset(get_args(BrandMood)), "")
    return healed or None


def heal_industry_value(value: object) -> str:
    """Coerce an LLM industry_category onto its Literal, default 'other'."""
    return _coerce_literal(value, frozenset(get_args(IndustryCategoryLiteral)), "other")


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

    @field_validator(
        "visual_mode",
        "image_source_preference",
        "calm_treatment",
        "band_override",
        mode="before",
    )
    @classmethod
    def heal_policy_enums(cls, v: object, info: ValidationInfo) -> object:
        # These are intent enums the LLM occasionally embellishes ("soft",
        # "muted", "scraped_only"). An invented adjective must never fail the
        # whole plan: every field's safe default is "auto" (no opinion — the
        # engine derives the treatment), so unknown values degrade to that.
        allowed = frozenset(get_args(cls.model_fields[info.field_name].annotation))
        return _coerce_literal(v, allowed, "auto")


class HeroBlock(BaseModel):
    kind: Literal["hero"] = "hero"
    eyebrow: str | None = None
    headline: str
    headline_accent: str | None = Field(
        default=None,
        description=(
            "Optional 1-4 word phrase copied verbatim from the END of headline; "
            "the layout renders it as a highlighted line in the brand accent "
            "colour. Ignored (safe no-op) when it isn't the headline's tail."
        ),
    )
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
    image_ref: int | None = Field(
        default=None,
        description=(
            "Index into the page's real scraped photos (page_source.images in "
            "the prompt). Resolved to image_url by services/image_refs.py."
        ),
    )
    image_url: str | None = Field(
        default=None,
        description=(
            "Resolved scraped photo URL. Filled by code from image_ref after "
            "planning; leave null in LLM output."
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
        return _heal_cta_label(v, "Get started")

    @field_validator("primary_cta_href", mode="before")
    @classmethod
    def heal_cta_href(cls, v: object) -> object:
        return _default_if_blank(v, "#contact")

    @field_validator("image_ref", mode="before")
    @classmethod
    def heal_image_ref(cls, v: object) -> object:
        return _heal_image_ref(v)

    @field_validator("layout", mode="before")
    @classmethod
    def heal_layout(cls, v: object) -> object:
        # The LLM sometimes omits layout, and sometimes invents adjectives
        # ("compact", "minimal", "full-bleed") despite the schema. A layout
        # word must never fail the whole plan: known full-bleed synonyms map
        # to "background", anything else degrades to "split".
        if isinstance(v, str):
            token = v.strip().lower()
            if token in ("split", "background"):
                return token
            if token in ("full-bleed", "fullbleed", "full_bleed", "overlay", "photo", "image", "immersive"):
                return "background"
        return "split"


class FeatureItem(BaseModel):
    title: str
    description: str
    image_query: str | None = Field(
        default=None,
        description="Short stock-search phrase for the card photo (2-6 words).",
    )
    image_ref: int | None = Field(
        default=None,
        description="Index into the page's real scraped photos (page_source.images).",
    )
    image_url: str | None = Field(
        default=None,
        description=(
            "Resolved scraped photo URL. Filled by code from image_ref after "
            "planning; leave null in LLM output."
        ),
    )
    image_alt: str | None = None

    @field_validator("image_ref", mode="before")
    @classmethod
    def heal_image_ref(cls, v: object) -> object:
        return _heal_image_ref(v)


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
    audience: str | None = Field(
        default=None,
        description=(
            "Who this service/program is for, as a short badge — e.g. 'Ages 2-4', "
            "'For startups'. Only when the source states it."
        ),
    )
    cta_label: str | None = None
    cta_href: str | None = None
    image_query: str | None = Field(
        default=None,
        description="Short stock-search phrase for the card photo (2-6 words).",
    )
    image_ref: int | None = Field(
        default=None,
        description="Index into the page's real scraped photos (page_source.images).",
    )
    image_url: str | None = Field(
        default=None,
        description=(
            "Resolved scraped photo URL. Filled by code from image_ref after "
            "planning; leave null in LLM output."
        ),
    )
    image_alt: str | None = None

    @field_validator("image_ref", mode="before")
    @classmethod
    def heal_image_ref(cls, v: object) -> object:
        return _heal_image_ref(v)


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
    image_ref: int | None = Field(
        default=None,
        description="Index into the page's real scraped photos (page_source.images).",
    )
    image_url: str | None = Field(
        default=None,
        description=(
            "Resolved scraped photo URL. Filled by code from image_ref after "
            "planning; leave null in LLM output."
        ),
    )
    visual_policy: VisualPolicy | None = None  # see SECTION_VISUAL_POLICY_SPEC.md

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "About us")

    @field_validator("image_ref", mode="before")
    @classmethod
    def heal_image_ref(cls, v: object) -> object:
        return _heal_image_ref(v)

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
    items: list[FaqItem] = Field(min_length=1, max_length=50)

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
    image_ref: int | None = Field(
        default=None,
        description="Index into the page's real scraped photos (page_source.images).",
    )
    image_url: str | None = Field(
        default=None,
        description=(
            "Resolved scraped background photo URL. Filled by code from "
            "image_ref after planning; leave null in LLM output."
        ),
    )
    visual_policy: VisualPolicy | None = None  # see SECTION_VISUAL_POLICY_SPEC.md

    @field_validator("cta_label", mode="before")
    @classmethod
    def heal_cta_label(cls, v: object) -> object:
        return _heal_cta_label(v, "Get started")

    @field_validator("image_ref", mode="before")
    @classmethod
    def heal_image_ref(cls, v: object) -> object:
        return _heal_image_ref(v)

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
    profile_href: str | None = Field(
        default=None,
        description=(
            "Site-relative link to this person's own page, when the source's "
            "roster card carried one and that page was generated. Filled by code "
            "after planning; leave null in LLM output."
        ),
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
    # Cap matches the scraper's profile_candidates cap (24) so a scraped
    # directory roster (e.g. a 17-practitioner listing) fits without trimming.
    members: list[TeamMember] = Field(min_length=1, max_length=24)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Meet the team")


class ProfileContact(BaseModel):
    """One way to reach the person a profile block is about.

    Its own model rather than a ``NavLink``: these are the person's contact
    affordances as their page states them (an email, a phone number, a
    professional profile), not site navigation — and the union below is built
    before ``NavLink`` exists.
    """

    label: str
    href: str


class ProfileBlock(BaseModel):
    """One person, on the page that is about them.

    A team block introduces people to each other; this introduces ONE person to
    the reader. It exists because a roster grid rendering a single card gets
    everything slightly wrong — the portrait is thumbnail-sized on a page that
    is entirely about that face, the name is restated under a hero that already
    said it, and there is nowhere for the contact details a directory carries.

    The bio lives here rather than in a separate about section: a profile page
    tells the story next to the face, and both came from the same source text
    anyway (see routers/generate._profile_page_member).
    """

    kind: Literal["profile"] = "profile"
    name: str
    role: str = ""
    credentials: str | None = Field(
        default=None,
        description=(
            "Qualifications line under the name, e.g. 'MT-BC, Saint "
            "Mary-of-the-Woods College'. Only what the source states."
        ),
    )
    bio: str | None = None
    photo_url: str | None = Field(
        default=None,
        description=(
            "Resolved scraped portrait of this real person. Filled by code "
            "after planning; leave null in LLM output."
        ),
    )
    photo_alt: str | None = None
    photo_query: str | None = Field(
        default=None,
        description=(
            "Ignored for real people — a stranger's stock face under a real "
            "name is a misattribution, so an unmatched profile renders a "
            "monogram instead (same rule as team cards)."
        ),
    )
    contacts: list[ProfileContact] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "The person's own contact affordances as the source gives them: "
            "an email link, a phone number, a professional profile."
        ),
    )

    @field_validator("role", mode="before")
    @classmethod
    def blank_role(cls, v: object) -> object:
        return "" if v is None else v


class GalleryItem(BaseModel):
    title: str | None = None
    caption: str | None = None
    image_query: str = Field(
        description="Pexels-search phrase for the photo, e.g. 'plated tasting menu close-up'."
    )
    image_ref: int | None = Field(
        default=None,
        description="Index into the page's real scraped photos (page_source.images).",
    )
    image_url: str | None = Field(
        default=None,
        description=(
            "Resolved scraped photo URL. Filled by code from image_ref after "
            "planning; leave null in LLM output."
        ),
    )

    @field_validator("image_ref", mode="before")
    @classmethod
    def heal_image_ref(cls, v: object) -> object:
        return _heal_image_ref(v)


class GalleryBlock(BaseModel):
    kind: Literal["gallery"] = "gallery"
    heading: str = "Gallery"
    subheading: str | None = None
    # A real scraped photo gallery routinely runs past a dozen shots, and the
    # tiles are click-to-enlarge (see BuilderElement.lightbox), so a longer set
    # is browsable rather than just a taller grid. Still capped — every unique
    # image is one media upload at push time.
    items: list[GalleryItem] = Field(min_length=1, max_length=24)

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


class DownloadLink(BaseModel):
    label: str
    href: str


class DownloadItem(BaseModel):
    """One document, as its source card presented it: a title, an optional
    thumbnail, and one-or-more file links (e.g. per-language variants of the
    same brochure). Mirrors DocumentCardCandidate 1:1 — the block-model
    counterpart built once hrefs are resolved to absolute URLs."""

    title: str | None = None
    image_url: str | None = None
    links: list[DownloadLink] = Field(min_length=1, max_length=6)


class DownloadsBlock(BaseModel):
    """One or more downloadable-document cards on a page (e.g. a resource
    library, or a single card with per-language file links).

    Never produced by the LLM: injected deterministically from the source
    page's scraped document cards (scraper._extract_document_cards →
    SourceContent.document_cards), same non-LLM pattern as LinkBarBlock. Every
    document card found on a page becomes one item of a SINGLE block for that
    page — never split across multiple blocks — so a document's title/image
    stay grouped with its own download links.
    """

    kind: Literal["downloads"] = "downloads"
    heading: str | None = None
    items: list[DownloadItem] = Field(min_length=1, max_length=12)


class VideoItem(BaseModel):
    """One embedded player, as the source page attached it.

    ``embed_url`` is already canonical (services.video_embed.parse_video_src), so
    nothing downstream re-parses it. There is deliberately no ``image_query``
    field — every other media item has one so an unfilled slot can fall through
    to stock photography, but there is no stock equivalent of a specific video.
    A tile with no embed is not a tile.
    """

    embed_url: str
    title: str | None = None
    thumbnail_url: str | None = None


class VideoBlock(BaseModel):
    """Videos the source page embedded, replayed verbatim.

    NEVER produced by the LLM — see DETERMINISTIC_SECTION_KINDS. A video id is
    an opaque 11-character string with an external referent: invent one and you
    have not written vague copy, you have embedded a stranger's video on a
    client's site, captioned as if it were theirs. Injected from
    SourceContent.video_embeds by routers.generate._inject_videos, the same
    non-LLM pattern as DownloadsBlock and LinkBarBlock.

    ``heading`` is a non-blank str rather than ``str | None`` on purpose: it
    fills a REQUIRED text slot in the catalog templates, and
    ``section_content.is_feasible`` rejects a required slot whose value is None
    — which makes select_template return None and schema_builder raise for an
    unregistered block kind. The healer below is the same guard GalleryBlock uses.
    """

    kind: Literal["video"] = "video"
    heading: str = "Videos"
    subheading: str | None = None
    # 24, matching GalleryBlock rather than the 12 of downloads/awards: a video
    # index is a gallery of players, and brightkids' carries 14. Capping lower
    # silently drops real videos, which is the failure this block exists to fix.
    # Cheap to allow — the renderer ships click-to-load facades, and a video is
    # hotlinked rather than uploaded to the tenant's media library.
    items: list[VideoItem] = Field(min_length=1, max_length=24)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Videos")


class MapItem(BaseModel):
    """One embedded map, as the source page framed it.

    ``embed_url`` is already canonical (services.map_embed.parse_map_src) and,
    like VideoItem, has no ``image_query`` sibling: there is no stock stand-in
    for a specific place. A pin nobody supplied is not a pin.
    """

    embed_url: str
    title: str | None = None


class MapBlock(BaseModel):
    """Maps the source page embedded, replayed verbatim.

    NEVER produced by the LLM — see DETERMINISTIC_SECTION_KINDS. A map URL
    encodes a coordinate: invent one and you have not written vague copy, you
    have published the wrong address for a real business and sent its customers
    somewhere else. Injected from SourceContent.map_embeds by
    routers.generate._inject_maps, the same non-LLM pattern as VideoBlock.

    Distinct from LocationsBlock on purpose, even though both end up rendering a
    Google Map. ``locations`` is LLM-authored prose (branch names, hours, phone)
    whose map is SYNTHESIZED from the address it wrote — a search for a string.
    This is the map the site owner themself pinned, which is the more precise
    artefact and the only one available on a page that states no address at all.
    ``_inject_maps`` yields to a page that already has a locations block rather
    than shipping two maps.

    ``heading`` is a non-blank str rather than ``str | None`` for the reason
    spelled out on VideoBlock: it fills a REQUIRED text slot in the catalog
    templates, and a None there makes select_template return None and
    schema_builder raise for an unregistered block kind.
    """

    kind: Literal["map"] = "map"
    heading: str = "Find us"
    subheading: str | None = None
    items: list[MapItem] = Field(min_length=1, max_length=8)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Find us")


class TimelineItem(BaseModel):
    year: str
    title: str
    description: str | None = None


class TimelineBlock(BaseModel):
    """Company history / milestones — only when the source evidences real dates."""

    kind: Literal["timeline"] = "timeline"
    heading: str = "Our story"
    subheading: str | None = None
    items: list[TimelineItem] = Field(min_length=1, max_length=10)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Our story")


class AwardItem(BaseModel):
    title: str
    issuer: str | None = None
    year: str | None = None


class AwardsBlock(BaseModel):
    """Real awards / certifications / recognitions — never invented."""

    kind: Literal["awards"] = "awards"
    heading: str = "Awards & recognition"
    subheading: str | None = None
    items: list[AwardItem] = Field(min_length=1, max_length=12)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Awards & recognition")


class ClientItem(BaseModel):
    name: str
    logo_query: str | None = Field(
        default=None,
        description="Short phrase for a logo/brand-mark lookup, e.g. 'Acme Corp logo'.",
    )


class ClientsBlock(BaseModel):
    """Logo wall of real clients/customers/partners named in the source."""

    kind: Literal["clients"] = "clients"
    heading: str = "Trusted by"
    subheading: str | None = None
    items: list[ClientItem] = Field(min_length=2, max_length=20)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Trusted by")


class StatItem(BaseModel):
    value: str
    label: str


class StatsBlock(BaseModel):
    """Real, source-grounded numbers (years in business, projects delivered, etc)."""

    kind: Literal["stats"] = "stats"
    heading: str | None = None
    items: list[StatItem] = Field(min_length=1, max_length=6)


class LocationItem(BaseModel):
    name: str
    address: str = Field(
        description="Full street address as stated in the source; drives the embedded map."
    )
    phone: str | None = None
    whatsapp: str | None = Field(
        default=None,
        description=(
            "WhatsApp number in international format (e.g. +60123456789) when the "
            "source gives one. Leave null rather than guessing a country code."
        ),
    )
    hours: str | None = Field(
        default=None,
        description='Operating hours as stated, e.g. "Mon-Fri 8:00am-6:00pm".',
    )


class LocationsBlock(BaseModel):
    """Physical branches/outlets with real addresses named in the source."""

    kind: Literal["locations"] = "locations"
    heading: str = "Visit us"
    subheading: str | None = None
    items: list[LocationItem] = Field(min_length=1, max_length=6)

    @field_validator("heading", mode="before")
    @classmethod
    def heal_heading(cls, v: object) -> object:
        return _default_if_blank(v, "Visit us")


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
    | ProfileBlock
    | GalleryBlock
    | MenuBlock
    | ProcessBlock
    | LinkBarBlock
    | TimelineBlock
    | AwardsBlock
    | ClientsBlock
    | StatsBlock
    | LocationsBlock
    | DownloadsBlock
    | VideoBlock
    | MapBlock,
    Field(discriminator="kind"),
]


def _required_list_fields() -> dict[str, tuple[str, int]]:
    """kind -> (list_field, min_length) for every block whose content list must be
    non-empty, derived from the block models themselves.

    Derived (not hand-maintained) on purpose: the previous static map silently
    missed awards/clients/stats/timeline/linkbar when those block types were added,
    which 502'd generation whenever the LLM emitted one with an empty list. Each
    block has exactly one min_length list field, so a single entry per kind suffices.
    """
    union = get_args(ContentBlock)[0]  # unwrap Annotated[Union[...], Field(...)]
    out: dict[str, tuple[str, int]] = {}
    for member in get_args(union):
        kind = member.model_fields["kind"].default
        for fname, finfo in member.model_fields.items():
            if get_origin(finfo.annotation) is list:
                min_len = next(
                    (getattr(m, "min_length", None) for m in finfo.metadata
                     if getattr(m, "min_length", None)),
                    None,
                )
                if min_len:
                    out[kind] = (fname, min_len)
                    break
    return out


_REQUIRED_LIST_FIELDS = _required_list_fields()

# Validates one block dict on its own so a single malformed block can be dropped
# without failing the whole page (see PagePlan.salvage_page_content).
_CONTENT_BLOCK_ADAPTER = TypeAdapter(ContentBlock)


def _block_is_valid(block: object) -> bool:
    """True when ``block`` parses as a ContentBlock on its own (healers applied).

    An already-constructed block model (blocks assembled in code, e.g. the
    multipass merge, or built directly in tests) is valid by construction — only
    raw dicts from the LLM's JSON get re-validated so a malformed one is dropped.
    """
    if isinstance(block, BaseModel):
        return True
    if not isinstance(block, dict) or "kind" not in block:
        return False
    try:
        _CONTENT_BLOCK_ADAPTER.validate_python(block)
        return True
    except ValidationError:
        return False


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
    menu_hidden: bool = False    # reached from a listing, not a menu; never set by the LLM
    # Carried through from the scaffold; never set by the LLM. A page with
    # `locale` set is a translation of `translation_of` — same blocks, same
    # images, same templates, text in another language.
    locale: str | None = None
    translation_of: str | None = None

    @field_validator("page_type", mode="before")
    @classmethod
    def heal_page_type(cls, v: object) -> object:
        # Scaffolds hand the LLM a page_type to echo verbatim, but it sometimes
        # rewrites it ("home page", "landing-page"). An unknown page_type must
        # never fail the plan — degrade to the generic "landing". is_homepage
        # carries homepage-ness separately, so nothing is lost.
        return _coerce_literal(v, frozenset(get_args(PageType)), "landing")

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
    def salvage_page_content(cls, data: object) -> object:
        """Keep as much of a drifting LLM's page as possible, so a technicality
        never blanks out a page's real content.

        The old behaviour dropped a whole page on ANY validation error, and the
        page was then re-synthesised as an empty structural shell — which meant a
        page whose only fault was one malformed block (or a missing SEO string)
        lost ALL its source-grounded sections and rendered as just a hero + CTA.
        Instead we salvage, in order:

        1. **Misplaced blocks** — when ``blocks`` is missing, adopt the section
           list the model put under ``sections``/``content`` (a common drift);
           a list of plain section-name strings is NOT mistaken for blocks.
        2. **Per-block salvage** — keep every block that parses on its own and
           drop only the ones that don't (a single truncated/empty-list block no
           longer fails the page). This also subsumes the old empty-list drop.
        3. **Missing SEO** — default ``seo_title``/``seo_description`` from the
           page's real title/description (before-validators can't fire on absent
           keys, so an omitted SEO field would otherwise fail the whole page).

        A page with NO salvageable blocks still validates (empty ``blocks``); the
        scaffold-alignment pass then pads its structural sections. Truly broken
        top-level shapes (``pages`` not a list, unparseable JSON) are still left
        to the caller's retry path.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)

        blocks = data.get("blocks")
        if not isinstance(blocks, list):
            for alias in ("sections", "content", "page_blocks"):
                candidate = data.get(alias)
                if isinstance(candidate, list) and any(
                    isinstance(b, dict) and "kind" in b for b in candidate
                ):
                    blocks = candidate
                    break
            else:
                blocks = []

        data["blocks"] = [b for b in blocks if _block_is_valid(b)]

        title = data.get("title") or data.get("slug") or ""
        if not (isinstance(data.get("seo_title"), str) and data["seo_title"].strip()):
            data["seo_title"] = str(title)
        if not (
            isinstance(data.get("seo_description"), str)
            and data["seo_description"].strip()
        ):
            data["seo_description"] = str(data.get("description") or title)
        return data


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
    "childcare",
    "personal",
    "other",
]


# Industry → default brand mood, used when the LLM gives no usable mood so the
# fallback reflects the industry instead of a blanket "modern". Grounded in the
# ui-ux-pro-max typography/style reasoning (MIT) and brand.py's mood taxonomy:
# modern=SaaS/professional; friendly=consumer/hospitality/cause; editorial=
# agency/portfolio; technical=B2B/expertise. (luxury/playful stay LLM-driven —
# no IndustryCategory maps cleanly to them.)
INDUSTRY_MOOD: dict[str, str] = {
    "restaurant": "friendly",
    "agency": "editorial",
    "saas": "modern",
    "professional-services": "modern",
    "ecommerce": "friendly",
    "consultancy": "technical",
    "nonprofit": "friendly",
    # childcare reads friendly, not playful: the audience is parents, so the
    # design must build trust and warmth first — joyful without being childish.
    "childcare": "friendly",
    "personal": "editorial",
    "other": "modern",
}


def industry_default_mood(industry: object) -> str:
    """The default brand mood for an industry (→ 'modern' when unknown)."""
    return INDUSTRY_MOOD.get(str(industry or "").strip().lower(), "modern")


# Industries whose design brief pins the visual language: the detected mood is
# advisory only. Explicit user choices still win — callers insert this between
# the explicit overrides and the LLM-detected fallback. Childcare is friendly
# by brief ("joyful without being childish"): a detected `playful` would swap
# in loud type/dramatic shadows aimed at entertainment brands, not parents.
INDUSTRY_LOCKED_MOOD: dict[str, str] = {"childcare": "friendly"}


def industry_locked_mood(industry: object) -> str | None:
    """The mood an industry's design brief locks in, or None when the industry
    leaves mood to detection."""
    return INDUSTRY_LOCKED_MOOD.get(str(industry or "").strip().lower())


class SitePlan(BaseModel):
    """The LLM's blueprint for the entire site."""

    site_name: str
    tagline: str | None = None
    brand_summary: str = ""
    brand_mood: BrandMood | None = Field(
        default=None,
        description=(
            "The brand's visual personality. Drives typography pairing, button radius, "
            "and section rhythm. Pick the closest match: "
            "modern=SaaS/fintech/tech; luxury=hospitality/jewellery/real-estate; "
            "friendly=consumer/wellness/lifestyle/childcare; technical=engineering/B2B/dev-tools; "
            "editorial=media/agencies/portfolios; playful=entertainment/food/gaming."
        ),
    )
    industry_category: IndustryCategoryLiteral = Field(
        default="other",
        description=(
            "The business industry. Drives the suggested page set. Pick the closest: "
            "restaurant, agency, saas, professional-services (legal/dental/medical), "
            "ecommerce, consultancy, nonprofit, childcare (kindergarten/preschool/"
            "daycare/early learning), personal, or other."
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
        # Coerces invented moods ("minimalist", "bold") as well as None/blank.
        return heal_brand_mood_value(v)

    @field_validator("industry_category", mode="before")
    @classmethod
    def heal_industry(cls, v: object) -> object:
        return heal_industry_value(v)

    @model_validator(mode="after")
    def _mood_from_industry(self) -> "SitePlan":
        # No usable mood from the LLM → derive one from the industry instead of a
        # blanket "modern". An explicit, valid mood is always kept.
        if self.brand_mood is None:
            self.brand_mood = industry_default_mood(self.industry_category)  # type: ignore[assignment]
        return self


class SourceContent(BaseModel):
    """Normalized input passed to the LLM, regardless of source (URL or doc).

    For crawled sites, ``discovered_pages`` carries the structure-bearing
    output of the bounded crawler: one entry per same-domain page found (the
    primary page itself stays the top-level instance — discovered_pages lists
    the *additional* pages). ``url_path`` is set on crawled sub-page instances
    only.
    """

    # Mirrored by `SourceKind` in frontend/src/lib/types.ts — change both together.
    source_kind: Literal["url", "pdf", "docx", "facebook"]
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
    section_candidates: list["SectionCandidate"] = Field(
        default_factory=list,
        description=(
            "The page's own section tree, recovered from heading rank by "
            "services/section_extraction.py. The planner is grounded on THIS "
            "rather than re-deriving boundaries from flat text, so one source "
            "section maps to one output section and a card never migrates "
            "between them. Additive and optional: a page whose markup carries "
            "no usable headings yields [] and the flat raw_text path applies."
        ),
    )
    document_cards: list["DocumentCardCandidate"] = Field(
        default_factory=list,
        description=(
            "Likely downloadable-document cards extracted from THIS page (title + "
            "optional thumbnail + one-or-more document links, e.g. a brochure "
            "offered in several languages). Scoped per-page like image_metadata, "
            "not just the primary page — routers.generate._inject_downloads groups "
            "every card found on a page into one DownloadsBlock for that page."
        ),
    )
    video_embeds: list["VideoEmbed"] = Field(
        default_factory=list,
        description=(
            "YouTube/Vimeo players THIS page embedded, canonicalized at "
            "extraction (scraper._extract_videos). Whitelist-only: every other "
            "iframe on a page is a tracker, a chat widget or a social plugin. "
            "Scoped per-page like image_metadata and document_cards — "
            "routers.generate._inject_videos groups every embed found on a page "
            "into one VideoBlock for that page."
        ),
    )
    map_embeds: list["MapEmbed"] = Field(
        default_factory=list,
        description=(
            "Maps THIS page framed (Google Maps, OpenStreetMap), canonicalized "
            "at extraction (scraper._extract_embeds). Same whitelist discipline "
            "as video_embeds, and scoped per-page the same way — "
            "routers.generate._inject_maps groups every map found on a page "
            "into one MapBlock for that page."
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
    subject_name: str | None = Field(
        default=None,
        description=(
            "The person the page's BODY leads with: the first designated name "
            "element (a heading, or an element classed name/member-name) below "
            "the chrome whose text reads as a person's name. Evidence that the "
            "page is that person's own, for detail pages whose <title> is a "
            "shared template and whose h1 is a section banner."
        ),
    )


class ImageMetadata(BaseModel):
    """Per-image hints from the extractor — used by the resolver's scorer."""

    url: str
    alt: str = ""
    intent: Literal["hero", "about", "logo", "generic"] = "generic"
    # Visual role measured from rendered geometry (services/image_evidence.py).
    # 'unknown' on the httpx fast path and for doc-upload images, where no
    # render evidence exists — the matcher then relies on intent alone.
    role: Literal[
        "hero", "background", "content", "gallery", "portrait", "logo", "decoration", "unknown"
    ] = "unknown"
    width: int | None = None
    height: int | None = None
    # How the source site used the image: 'css_background' = CSS background-image,
    # 'inline' = <img>/og:image. 'unknown' for doc-upload and legacy bare-URL
    # wrapping. Backgrounds must stay backgrounds: the matcher keeps
    # css_background images out of side/featured slots (slot_usage='inline') and
    # pins them for full-bleed slots (slot_usage='background').
    source_usage: Literal["inline", "css_background", "unknown"] = "unknown"
    # Nearest preceding heading on the source page — ties the image to the
    # section it illustrated. Surfaced in the planner prompt so the LLM can
    # bind real photos to sections (image_ref), and used as a secondary
    # lexical signal by the matcher.
    context_heading: str = ""
    # <figcaption> text when the image sat inside a <figure>.
    caption: str = ""
    # Vision-pass annotations (services/image_vision.py). All None until the
    # opt-in pass runs. vision_caption feeds the matcher's lexical scoring so
    # alt-less, hash-named images can still be ranked against slot queries.
    vision_caption: str | None = None
    vision_kind: Literal[
        "photo", "logo", "banner", "screenshot", "graphic", "map", "other"
    ] | None = None
    vision_people: int | None = None  # visible people count
    vision_portrait: bool | None = None  # single face/head-and-shoulders subject
    # Readable words burned into the pixels (headline, tagline, price list, a
    # legible sign). Bars the image from full-bleed BACKGROUND slots, where our
    # own headline would be laid over words that are already there; it stays
    # eligible for featured/inline slots, which draw nothing on top.
    vision_has_text: bool | None = None
    # Same judgement from the OCR pass (services/text_detection.py), which is
    # independent of the vision model and runs off the critical path. None until
    # that pass runs / when it is disabled.
    ocr_has_text: bool | None = None
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
    email: str | None = None
    phone: str | None = None
    social_links: list[tuple[str, str]] = Field(
        default_factory=list,
        description=(
            "(label, href) social profile links found on this person's own card "
            "or page, e.g. ('LinkedIn', 'https://linkedin.com/in/...'). Distinct "
            "from the site-wide SourceContent.social_links: these are scoped to "
            "this one person, not the header/footer."
        ),
    )
    profile_url: str | None = Field(
        default=None,
        description=(
            "The page this card LINKS to — the person's own detail page, when the "
            "roster gives them one. The source site's own answer to two questions "
            "code would otherwise have to guess: which page a detail page belongs "
            "under, and where a rendered team card should point. Null when the card "
            "links nowhere (a directory whose cards carry only an email)."
        ),
    )
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# What a card GROUP is. Deliberately coarse: these are the distinctions the
# source markup can actually support. "offerings" covers services, programmes,
# rooms and features alike — whether a card carries a photo is already visible
# in `image_urls`, and splitting on that alone produced a "facilities" category
# that meant nothing more than "has an image".
SourceCardKind = Literal[
    "people",
    "offerings",
    "steps",
    "documents",
    "gallery",
    "prose",
]


class SourceCard(BaseModel):
    """One card inside a source section's repeated group.

    Deliberately kind-agnostic: the same shape carries a person, a programme, a
    room and a downloadable brochure. What it IS is the enclosing
    ``SectionCandidate.card_kind``, decided over the whole group — a card in
    isolation cannot be classified, which is the mistake that turned Glorykids'
    facility grid into a team roster.
    """

    # May be "": a badge/logo tile (an award, an accreditation, a partner mark)
    # is a picture and nothing else. What the group IS still resolves, because
    # `card_kind` is decided over the whole group — a rack of untitled pictures
    # is exactly the `gallery` signature.
    title: str = ""
    body: str = ""
    image_url: str | None = None
    image_alt: str = ""
    link: str | None = None
    meta: list[str] = Field(
        default_factory=list,
        description=(
            "Short label/value lines the card carries beside its body — "
            "'Full Programme : 8:30 am - 3:00 pm', a price, an age badge. Kept "
            "separate from `body` so a mapper can render them as badges rather "
            "than prose, and so they never read as a person's job title."
        ),
    )


class SectionCandidate(BaseModel):
    """One section of the SOURCE page, as the source's own markup declares it.

    The unit the source page is actually built from, recovered from heading
    rank: a heading owns everything until the next heading of same-or-higher
    rank, so an h3 card title nests inside the h2 section above it. This is the
    structure that ``headings`` (a flat, level-less, deduped list) and
    ``raw_text`` (newline-joined lines) each destroy — leaving the LLM to
    rebuild a tree from a list, which is why six sections arrived as one.

    Advisory, never binding: the planner is told to preserve these boundaries,
    but a page whose markup carries no usable headings simply yields none and
    the flat text path still applies.
    """

    heading: str
    level: int = Field(ge=1, le=6)
    subheading: str = ""
    prose: str = ""
    cards: list[SourceCard] = Field(default_factory=list)
    card_kind: SourceCardKind = "prose"
    image_urls: list[str] = Field(default_factory=list)


class DocumentCardLink(BaseModel):
    """One file link on a document card, as scraped (href still page-relative
    — resolved to absolute by the injector, mirroring how ProfileCandidate's
    photo_url is resolved downstream of extraction)."""

    label: str
    href: str


class DocumentCardCandidate(BaseModel):
    """A likely downloadable-document card extracted near a cluster of
    document-file links (scraper._extract_document_cards).

    Mirrors ProfileCandidate's shape: a distinctive anchor (there, a portrait;
    here, a document-extension href) plus nearby title/thumbnail text found by
    walking up to the smallest enclosing card. No site-specific markup is
    assumed — this must work on any site's resource/brochure listing, not just
    one with a particular class-name convention.
    """

    title: str | None = None
    image_url: str | None = None
    links: list[DocumentCardLink] = Field(min_length=1)


class VideoEmbed(BaseModel):
    """One playable video the SOURCE page embedded (scraper._extract_videos).

    Whitelist-only (YouTube/Vimeo). Every other iframe on a real page is an ad,
    a tracker, a chat widget or a social plugin — brightkids' homepage carries
    four iframes, of which three are Google Tag Manager and a Facebook like-box.

    ``embed_url`` is already the canonical form the renderer wants, so nothing
    downstream re-parses a raw src. ``title`` is the source's own caption, left
    EMPTY rather than guessed: an embed with no heading near it gets no title,
    because a wrong caption is worse than none. ``context_heading`` is the
    nearest preceding heading and groups several embeds under one section.
    """

    provider: Literal["youtube", "vimeo"]
    video_id: str
    embed_url: str
    thumbnail_url: str | None = None
    title: str = ""
    context_heading: str = ""


class MapEmbed(BaseModel):
    """One map the SOURCE page framed (scraper._extract_embeds).

    Sibling of VideoEmbed, same whitelist discipline and same caption rules:
    ``title`` is the source's own label for this map — the DOM caption if it has
    one, else the place the URL itself names — and is left EMPTY rather than
    guessed. ``context_heading`` groups several pins under one section, which is
    what turns a three-branch page into one "Our centres" map wall.

    There is no ``thumbnail_url``: a map has no poster frame, so the renderer
    always frames it directly instead of showing a click-to-load facade.
    """

    provider: Literal["google", "osm"]
    embed_url: str
    title: str = ""
    context_heading: str = ""


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


# --- source blog/event entries (content migration) -------------------------------
#
# Extracted verbatim from the source site's post/event detail pages by
# services/content_collections.py — no LLM involvement. Carried on
# GeneratedSite so the frontend's generate → push round-trip preserves them;
# the push orchestrator turns each into a real CMS article/event entry.


class ArticleEntry(BaseModel):
    title: str
    slug: str
    excerpt: str
    body_html: str
    published_at: datetime | None = None
    image_url: str | None = None
    source_url: str


class EventEntry(BaseModel):
    title: str
    slug: str
    excerpt: str
    body_html: str
    start: datetime | None = None
    end: datetime | None = None
    location: str | None = None
    image_url: str | None = None
    source_url: str


class ContentCollections(BaseModel):
    articles: list[ArticleEntry] = Field(default_factory=list)
    events: list[EventEntry] = Field(default_factory=list)

    @property
    def has_articles(self) -> bool:
        return bool(self.articles)

    @property
    def has_events(self) -> bool:
        return bool(self.events)
