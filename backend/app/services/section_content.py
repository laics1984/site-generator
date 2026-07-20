"""
Bridge semantic ContentBlocks to section-catalog templates.

Two responsibilities:

  1. **Content mapping** — turn a block (HeroBlock, AboutBlock, …) into a flat
     ``{slot_id: value}`` dict matching a catalog template's slots.
  2. **Template selection** — pick which variant of the section type to use.
     This is the two-stage model: a code-side *feasibility filter* (a template
     is only a candidate if every required slot can be filled from the block)
     narrows the options, then a *preference order* (derived from block hints
     like hero ``layout`` or whether an image exists) chooses among the feasible
     ones. An explicit ``template_id`` (e.g. chosen by the planner LLM) wins when
     it is feasible.

The selection rules themselves live in each catalog entry's description/tags
(read by the LLM); this module enforces only the hard, data-driven constraints
that descriptions cannot guarantee.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Literal
from urllib.parse import quote_plus

from app.models.brand import BrandMood, ColorPalette, ThemeTokens
from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.models.content_blocks import (
    AboutBlock,
    ContactBlock,
    ContentBlock,
    CtaBlock,
    FaqBlock,
    FeaturesBlock,
    GalleryBlock,
    HeroBlock,
    LocationsBlock,
    MenuBlock,
    PricingBlock,
    ProcessBlock,
    ServicesBlock,
    TeamBlock,
    TestimonialsBlock,
    VisualPolicy,
)
from app.services.template_filler import get_template, templates_for_type
from app.services.theme import (
    _adjust_lightness,
    _contrast,
    _hex_to_rgb,
    _relative_luminance,
    _text_for_background,
    band_colors,
)


def _link(label: str | None, href: str | None) -> dict[str, str] | None:
    if not label:
        return None
    return {"innerText": label, "href": href or "#"}


def _image(query: str | None, alt: str | None) -> dict[str, str] | None:
    if not query:
        return None
    return {"query": query, "alt": alt or ""}


def _featured_image(
    query: str | None, bound_url: str | None, alt: str | None
) -> dict[str, str] | None:
    """Image slot value for a block that may carry a ref-bound scraped photo.

    Keeps the value query-shaped even when a photo is bound — the actual pin
    happens in schema_builder's resolver call (pinned_url), which also records
    band/used-URL bookkeeping. The bound URL only matters here for feasibility:
    a block with a real photo but no image_query must still select an
    image-bearing template.
    """
    if query:
        return {"query": query, "alt": alt or ""}
    if bound_url:
        return {"query": alt or "source photo", "alt": alt or ""}
    return None


# --- content mappers (block -> {slot: value}) -----------------------------------


def _hero_content(b: HeroBlock) -> dict[str, Any]:
    return {
        "eyebrow": b.eyebrow,
        "headline": b.headline,
        "body": b.subheadline,
        "primary_cta": {"innerText": b.primary_cta_label, "href": b.primary_cta_href},
        "secondary_cta": _link(b.secondary_cta_label, b.secondary_cta_href),
        "image": _featured_image(b.image_query, b.image_url, b.image_alt or b.headline),
    }


def _about_content(b: AboutBlock) -> dict[str, Any]:
    return {
        "eyebrow": "About",
        "heading": b.heading,
        "subheading": b.body or None,
        "image": _featured_image(b.image_query, b.image_url, b.image_alt or b.heading),
    }


def _item_image(item: Any, alt_fallback: str) -> dict[str, str] | None:
    """Per-card image value: a ref-bound scraped photo fills the slot directly
    (the {src} path in template_filler, same as team-grid member photos);
    unbound items resolve their stock image_query. An item the LLM left
    query-less falls back to its title as the stock search — every card must
    carry an image so the photo-topped grid stays feasible (the resolver's
    industry-context fallback rescues weak title queries)."""
    url = getattr(item, "image_url", None)
    if url:
        return {"src": url, "alt": getattr(item, "image_alt", None) or alt_fallback}
    return _image(getattr(item, "image_query", None) or alt_fallback, alt_fallback)


def _features_content(b: FeaturesBlock) -> dict[str, Any]:
    return {
        "eyebrow": "Features",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [
            {
                "title": i.title,
                "description": i.description,
                "image": _item_image(i, i.title),
            }
            for i in b.items
        ],
    }


def _services_content(b: ServicesBlock) -> dict[str, Any]:
    return {
        "eyebrow": "Services",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [
            {
                "title": i.title,
                "description": i.description,
                "ideal": i.audience,
                "image": _item_image(i, i.title),
            }
            for i in b.items
        ],
    }


def _testimonials_content(b: TestimonialsBlock) -> dict[str, Any]:
    items = [
        {
            "quote": f"“{i.quote}”",
            "attribution": i.author + (f", {i.role}" if i.role else ""),
        }
        for i in b.items
    ]
    first = items[0] if items else {}
    return {
        "eyebrow": "Testimonials",
        "heading": b.heading,
        "subheading": None,
        "items": items,
        # Also expose the first quote as scalars so the single-quote variant
        # (testimonials-single) is feasible without a list.
        "quote": first.get("quote"),
        "attribution": first.get("attribution"),
    }


def _cta_content(b: CtaBlock) -> dict[str, Any]:
    return {
        "eyebrow": None,
        "heading": b.headline,
        "body": b.subheadline,
        "primary_cta": {"innerText": b.cta_label, "href": b.cta_href},
        "secondary_cta": None,
        # A background photo (dark overlay applied by the template) when the LLM
        # supplied an atmospheric query or bound a real photo — else selection
        # falls back to a gradient.
        "image": _featured_image(b.background_query, b.image_url, b.headline),
    }


def _faq_content(b: FaqBlock) -> dict[str, Any]:
    return {
        "eyebrow": "FAQ",
        "heading": b.heading,
        "subheading": None,
        "items": [{"question": i.question, "answer": i.answer} for i in b.items],
    }


def _contact_content(b: ContactBlock) -> dict[str, Any]:
    bullets = [v for v in (
        f"Email: {b.email}" if b.email else None,
        f"Phone: {b.phone}" if b.phone else None,
    ) if v]
    return {
        "eyebrow": "Contact",
        "heading": b.heading,
        "subheading": b.subheading,
        "bullet1": bullets[0] if len(bullets) > 0 else None,
        "bullet2": bullets[1] if len(bullets) > 1 else None,
        "bullet3": None,
    }


def _team_content(b: TeamBlock) -> dict[str, Any]:
    # A source that reuses one photo across members would otherwise repeat the
    # same image down the grid; the second use falls back to a resolver query so
    # each card gets a distinct image.
    used_photo_urls: set[str] = set()

    def member_photo(member: Any) -> dict[str, str] | None:
        url = getattr(member, "photo_url", None)
        if url and url not in used_photo_urls:
            used_photo_urls.add(url)
            return {
                "src": url,
                "alt": member.photo_alt or member.name,
            }
        return _image(member.photo_query, member.name)

    return {
        "eyebrow": "Team",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [
            {
                "photo": member_photo(m),
                "name": m.name,
                "role": m.role,
                "bio": getattr(m, "bio", None) or getattr(m, "description", None),
            }
            for m in b.members
        ],
    }


def _gallery_content(b: GalleryBlock) -> dict[str, Any]:
    return {
        "eyebrow": "Gallery",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [
            {
                # A ref-bound scraped photo fills the slot directly (the {src}
                # path in template_filler, same as team-grid member photos);
                # unbound items resolve their query as before.
                "image": (
                    {"src": i.image_url, "alt": i.title or i.caption or ""}
                    if i.image_url
                    else _image(i.image_query, i.title or i.caption or "")
                )
            }
            for i in b.items
        ],
    }


def _process_content(b: ProcessBlock) -> dict[str, Any]:
    return {
        "eyebrow": "Process",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [
            {"number": f"{idx:02d}", "title": s.title, "description": s.description}
            for idx, s in enumerate(b.steps, start=1)
        ],
    }


def _menu_content(b: MenuBlock) -> dict[str, Any]:
    return {
        "eyebrow": "Menu",
        "heading": b.heading,
        "subheading": b.subheading,
        "categories": [
            {
                "name": c.name,
                "items": [
                    {"name": it.name, "description": it.description, "price": it.price}
                    for it in c.items
                ],
            }
            for c in b.categories
        ],
    }


def _stats_content(b: Any) -> dict[str, Any]:
    return {
        "heading": b.heading,
        "items": [{"value": i.value, "label": i.label} for i in b.items],
    }


def _clients_content(b: Any) -> dict[str, Any]:
    return {
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [{"name": i.name} for i in b.items],
    }


def maps_embed_url(name: str, address: str) -> str:
    """Keyless Google Maps embed URL for a branch (renders via the video/iframe
    element — see the locations catalog template). Name + address together give
    the place search its best chance of pinning the exact business."""
    return f"https://maps.google.com/maps?q={quote_plus(f'{name}, {address}')}&output=embed"


def whatsapp_href(number: str | None) -> str | None:
    """``wa.me`` link for an international-format number; None otherwise.

    wa.me requires the full country-coded number, so a national-format number
    (leading 0, no country code) yields None — callers fall back to ``tel:``,
    which any format satisfies.
    """
    if not number:
        return None
    digits = re.sub(r"\D", "", number)
    if number.strip().startswith("+") and len(digits) >= 9:
        return f"https://wa.me/{digits}"
    if digits.startswith("00") and len(digits) >= 11:
        return f"https://wa.me/{digits[2:]}"
    return None


def _locations_content(b: LocationsBlock) -> dict[str, Any]:
    items = []
    for i in b.items:
        wa = whatsapp_href(i.whatsapp)
        items.append(
            {
                "name": i.name,
                "address": i.address,
                "hours": i.hours,
                "phone_cta": _link(i.phone, f"tel:{re.sub(r'[^0-9+]', '', i.phone)}")
                if i.phone
                else None,
                "whatsapp_cta": _link("WhatsApp us", wa) if wa else None,
                "map": {"src": maps_embed_url(i.name, i.address)},
            }
        )
    return {
        "eyebrow": "Locations",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": items,
    }


def _pricing_content(b: PricingBlock) -> dict[str, Any]:
    return {
        "eyebrow": "Pricing",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [
            {
                "badge": "Most popular" if t.highlighted else None,
                "name": t.name,
                "price": t.price,
                "description": t.description,
                "features": [{"feature": f"✓ {f}"} for f in t.features],
                "cta": {"innerText": t.cta_label, "href": t.cta_href},
            }
            for t in b.tiers
        ],
    }


_MAPPERS: dict[str, Callable[[Any], dict[str, Any]]] = {
    "hero": _hero_content,
    "about": _about_content,
    "features": _features_content,
    "services": _services_content,
    "testimonials": _testimonials_content,
    "cta": _cta_content,
    "faq": _faq_content,
    "contact": _contact_content,
    "team": _team_content,
    "gallery": _gallery_content,
    "process": _process_content,
    "menu": _menu_content,
    "pricing": _pricing_content,
    "locations": _locations_content,
    "stats": _stats_content,
    "clients": _clients_content,
}


# --- preference (best-first template ids per block hints) ------------------------


def _hero_preference(content: dict[str, Any], b: HeroBlock) -> list[str]:
    if content.get("image"):
        if b.layout == "background":
            return [
                "hero-background-bold", "hero-editorial", "hero-modern-split",
                "hero-gradient", "hero-centered-minimal",
            ]
        return [
            "hero-modern-split", "hero-editorial", "hero-background-bold",
            "hero-gradient", "hero-centered-minimal",
        ]
    # No photo: a brand gradient reads richer/on-trend; minimal is the quiet fallback.
    return ["hero-gradient", "hero-minimal", "hero-centered-minimal"]


def _about_preference(content: dict[str, Any], b: AboutBlock) -> list[str]:
    # about-story-split needs metric cards (no generator data) so it's usually
    # infeasible; about-story is the universal no-image fallback.
    if content.get("image"):
        return ["about-image-split", "about-story", "about-story-split"]
    return ["about-story", "about-story-split", "about-image-split"]


def _cta_preference(content: dict[str, Any], b: CtaBlock) -> list[str]:
    # Photo background when an image is available (feasibility enforces it),
    # otherwise the bold gradient banner, then the minimal centered prompt.
    if content.get("image"):
        return ["cta-background", "cta-banner", "cta-minimal"]
    return ["cta-banner", "cta-minimal"]


def _most_items_have_images(content: dict[str, Any]) -> bool:
    """True when the section's cards can lead with photos: 2+ items and every
    item carries an image value (bound scraped photo or stock query)."""
    items = content.get("items") or []
    return len(items) >= 2 and all(i.get("image") for i in items)


def _features_preference(content: dict[str, Any], b: FeaturesBlock) -> list[str]:
    # Photo-topped cards whenever every card has an image to lead with —
    # landing-page practice: show it, don't just say it.
    if _most_items_have_images(content):
        return ["features-image-cards", "features-card-grid", "features-two-col"]
    # Match column count to item count: 3+ -> 3-col grid, 1-2 -> 2-col.
    return ["features-card-grid"] if len(b.items) >= 3 else ["features-two-col", "features-card-grid"]


def _services_preference(content: dict[str, Any], b: ServicesBlock) -> list[str]:
    if _most_items_have_images(content):
        return ["services-image-cards", "services-offer-grid", "services-two-col"]
    return ["services-offer-grid"] if len(b.items) >= 3 else ["services-two-col", "services-offer-grid"]


def _testimonials_preference(content: dict[str, Any], b: TestimonialsBlock) -> list[str]:
    if len(b.items) == 1:
        return ["testimonials-single", "testimonials-quote-grid"]
    return ["testimonials-quote-grid"]


_PREFERENCE: dict[str, Callable[[dict[str, Any], Any], list[str]]] = {
    "hero": _hero_preference,
    "about": _about_preference,
    "cta": _cta_preference,
    "features": _features_preference,
    "services": _services_preference,
    "testimonials": _testimonials_preference,
}


# --- interior-page hero CTA policy ----------------------------------------------
#
# A homepage hero is a *conversion* hero — it keeps its CTA(s). Every interior
# page hero is an *orientation* header: its job is to say where you are, not to
# convert. Whether it carries a button depends on the hero's HEIGHT, which the
# chosen variant encodes:
#
#   * Full-bleed background hero (hero-background-bold) pushes the page's real
#     content below the fold. There a single "scroll cue" CTA that smooth-scrolls
#     down to this page's first content section is genuinely helpful — the content
#     isn't visible yet. It points AT the page's own next section (an in-page
#     anchor), never off-page; the off-page conversion ask lives in the closing
#     `cta` block.
#
#   * Compact heroes (split / gradient / centered-minimal) sit directly above
#     content that is already visible, so a button would only point at what the
#     user can already see. These drop the hero CTA entirely.
#
# The decision is made here, in code, AFTER the variant is chosen — so it
# overrides whatever (often self-referential) CTA the planner LLM emitted.

# Variants whose hero fills the viewport, hiding the content below the fold.
_FULLBLEED_HERO_IDS = frozenset({"hero-background-bold"})

# Scroll-cue label by the kind of section the hero scrolls down to. Framed as an
# invitation to read/see the content, never a generic "Get started".
_SCROLL_CUE_LABEL: dict[str, str] = {
    "testimonials": "Read the reviews",
    "gallery": "View the gallery",
    "menu": "See the menu",
    "team": "Meet the team",
    "services": "Explore services",
    "features": "See what's inside",
    "pricing": "See pricing",
    "process": "How it works",
    "faq": "Read the FAQ",
    "about": "Read more",
    "contact": "Get in touch",
}
_SCROLL_CUE_FALLBACK = "Explore"


def hero_scroll_anchor(target_kind: str) -> str:
    """Stable in-page anchor id for the section a hero scrolls down to."""
    return f"sec-{target_kind}"


def apply_hero_cta_policy(
    content: dict[str, Any],
    template_id: str,
    *,
    is_homepage: bool,
    scroll_target_kind: str | None,
) -> None:
    """Mutate hero ``content`` so interior-page heroes follow the orientation rule.

    Homepage heroes are left untouched (conversion hero). Interior heroes either
    get a single scroll-cue CTA (full-bleed variants, when there is a content
    section to scroll to) or no CTA at all (compact variants). See module notes.
    """
    if is_homepage:
        return
    # Interior heroes carry one action at most — never a secondary button.
    content["secondary_cta"] = None
    is_fullbleed = template_id in _FULLBLEED_HERO_IDS
    if is_fullbleed and scroll_target_kind:
        label = _SCROLL_CUE_LABEL.get(scroll_target_kind, _SCROLL_CUE_FALLBACK)
        content["primary_cta"] = {
            "innerText": label,
            "href": f"#{hero_scroll_anchor(scroll_target_kind)}",
        }
    else:
        content["primary_cta"] = None


# --- feasibility + selection ----------------------------------------------------


def is_feasible(template: dict[str, Any], content: dict[str, Any]) -> bool:
    """True iff every required (non-optional) slot can be filled from ``content``."""
    for slot in template.get("slots", []):
        if slot.get("optional"):
            continue
        value = content.get(slot["id"])
        kind = slot["kind"]
        if kind == "list":
            if not isinstance(value, list) or not value:
                return False
            # A template may require a minimum item count (e.g. bento needs
            # enough tiles to read as a modular grid). Too few → infeasible,
            # so selection falls back to a layout that suits the item count.
            min_items = slot.get("minItems")
            if isinstance(min_items, int) and len(value) < min_items:
                return False
        elif kind == "image":
            if not (isinstance(value, dict) and (value.get("query") or value.get("src"))):
                return False
        elif kind == "link":
            if not (isinstance(value, dict) and (value.get("innerText") or value.get("label"))):
                return False
        elif kind == "video":
            if not (isinstance(value, dict) and value.get("src")):
                return False
        else:  # text
            if not (isinstance(value, str) and value.strip()):
                return False
    return True


# --- mood-aware layout preference ----------------------------------------------
#
# Mood biases WHICH layout variant a section uses, so brands with different moods
# get visibly different structures from the same content. Keyed on `layoutVariant`
# (robust to catalog additions), ordered most→least preferred per mood. This only
# REORDERS feasible candidates — `is_feasible` in select_template remains the hard
# gate, so an infeasible layout (e.g. an image-led hero with no image) is never
# chosen. Single-variant section types (faq, contact, team, …) are unaffected.
_MOOD_LAYOUT_PREFERENCE: dict[BrandMood, list[str]] = {
    "modern": ["bento", "split", "grid", "gradient", "banner"],
    "luxury": ["centered", "editorial", "asymmetric", "narrative", "minimal", "split", "single"],
    # "background" before "split": friendly brands lead photo-forward; split
    # was the reflex that made every friendly-mood page open identically.
    "friendly": ["grid", "steps", "banner", "background", "split"],
    "technical": ["grid", "minimal", "stacked", "centered", "split"],
    "editorial": ["editorial", "asymmetric", "split", "narrative", "single", "background"],
    "playful": ["steps", "bento", "background", "gradient", "banner", "grid"],
}


def mood_preferred_ids(mood: BrandMood | None, section_type: str) -> list[str]:
    """Template ids for `section_type`, ordered by the mood's layout preference.

    Templates whose `layoutVariant` isn't in the mood's list sort last (stable).
    Returns [] for an unknown/None mood, so callers fall back to today's behavior.
    """
    pref = _MOOD_LAYOUT_PREFERENCE.get(mood) if mood else None
    if not pref:
        return []
    rank = {variant: i for i, variant in enumerate(pref)}
    candidates = templates_for_type(section_type)
    ordered = sorted(
        candidates,
        key=lambda t: rank.get(t.get("layoutVariant", ""), len(pref)),
    )
    return [t["id"] for t in ordered]


def mood_allows(template: dict[str, Any], mood: BrandMood | None) -> bool:
    """A template with a ``moods`` list is only offered to those brand moods
    (e.g. playful kindergarten styling never lands on a law firm). Templates
    without the field — the whole pre-existing catalog — are mood-neutral."""
    allowed = template.get("moods")
    if not allowed:
        return True
    return mood in allowed


def select_template(
    section_type: str,
    content: dict[str, Any],
    *,
    preferred_ids: list[str] | None = None,
    explicit_id: str | None = None,
    mood: BrandMood | None = None,
) -> dict[str, Any] | None:
    """Choose a catalog template for a section: feasibility filter, then preference."""
    candidates = [t for t in templates_for_type(section_type) if mood_allows(t, mood)]
    if not candidates:
        return None
    feasible = [t for t in candidates if is_feasible(t, content)]

    if explicit_id:
        chosen = get_template(explicit_id)
        if chosen is not None and chosen in feasible:
            return chosen

    pool = feasible or candidates  # graceful: degrade rather than drop the section
    for pid in preferred_ids or []:
        for template in pool:
            if template["id"] == pid:
                return template
    return pool[0]


_PAGE_BG = "var(--builder-page-background, #ffffff)"
_SURFACE_BG = "var(--builder-color-surface, #f8fafc)"


# --- luminance-band resolution (SECTION_VISUAL_POLICY_SPEC.md §3.3) --------------
#
# Pure, deterministic page-level pass. Given each section's visual intent + its
# resolved photo band, it assigns a luminance band per section. Phase 4 builds
# the inputs from blocks/resolved photos and emits the brand colours; this layer
# is the algorithm only — no BuilderElement, no theme, no I/O — so it is unit-
# testable in isolation.

Band = Literal["light", "dark"]


def _opposite_band(band: Band) -> Band:
    return "light" if band == "dark" else "dark"


@dataclass(frozen=True)
class SectionVisualInput:
    """One section's inputs to the luminance pass.

    Only large background-capable sections that carry a visual_policy
    `participate`; everything else (grids, unscoped sections) is transparent to
    the rhythm and never gets a band.
    """

    participates: bool = False
    # Carries its own featured/content image → ANCHORED: band is fixed opposite
    # the image so the image pops against its container (rule 2).
    anchored: bool = False
    # The featured image's OWN band (anchored sections only). None when no colour
    # hint was available → container defaults light (§8.4).
    image_band: Band | None = None
    # Flexible full-bleed photo → seeds DARK when it has no left neighbour (§3.5).
    is_photo_background: bool = False
    # Escape hatch; wins over all derivation when set.
    band_override: Band | None = None


@dataclass(frozen=True)
class SectionBandPlan:
    """Resolved band for one section. `band is None` for non-participants."""

    band: Band | None = None
    anchored: bool = False
    # True when this participant is forced to the same band as the previous
    # participant (an unavoidable anchor collision) → render a separator so the
    # seam stays visible (§3.3 step 4).
    separator_before: bool = False


def resolve_section_bands(inputs: list[SectionVisualInput]) -> list[SectionBandPlan]:
    """Resolve a per-section luminance-band plan for one page's section list.

    Precedence (§3.3): band_override > anchored (image↔container contrast) >
    strict alternation. Anchored/override bands are fixed points; flexible
    sections alternate around them. A flexible section always flips from its
    left neighbour, so it never collides on the left; only two adjacent *forced*
    sections can land on the same band, and that boundary is flagged for a
    separator (alternation yields to the hard anchor rule).
    """
    plans: list[SectionBandPlan] = [SectionBandPlan() for _ in inputs]
    prev_band: Band | None = None

    for i, s in enumerate(inputs):
        if not s.participates:
            continue

        if s.band_override is not None:
            band: Band = s.band_override
        elif s.anchored:
            band = _opposite_band(s.image_band) if s.image_band else "light"
        elif prev_band is not None:
            band = _opposite_band(prev_band)
        else:
            band = "dark" if s.is_photo_background else "light"

        plans[i] = SectionBandPlan(
            band=band,
            anchored=s.anchored,
            separator_before=(prev_band is not None and band == prev_band),
        )
        prev_band = band

    return plans


# Section kinds large enough to carry a background policy (§4.2).
_POLICY_KINDS = frozenset({"hero", "about", "features", "services", "cta"})


def _default_visual_mode_for(block: ContentBlock) -> str | None:
    """The §5-matrix visual_mode for a block, or None if its kind doesn't
    participate. Deterministic — content/layout drives the choice."""
    kind = block.kind
    if kind == "hero":
        return (
            "photo_background"
            if getattr(block, "layout", None) == "background"
            else "supporting_image"
        )
    if kind == "about":
        return (
            "supporting_image"
            if getattr(block, "image_query", None) or getattr(block, "image_url", None)
            else "plain"
        )
    if kind == "cta":
        return (
            "photo_background"
            if getattr(block, "background_query", None) or getattr(block, "image_url", None)
            else "plain"
        )
    if kind in ("features", "services"):
        return "plain"
    return None


def assign_visual_policies(blocks: list[ContentBlock]) -> None:
    """Set visual_policy on the large background-capable blocks per the §5 matrix.

    Deterministic and idempotent: skips any block that already carries a policy
    (so an explicit/LLM-provided one wins) and any kind that doesn't participate.
    This is the Phase-5 activation step — gated by settings.luminance_rhythm_enabled
    at the call site. Mutates blocks in place. See SECTION_VISUAL_POLICY_SPEC.md §5.
    """
    for block in blocks:
        if block.kind not in _POLICY_KINDS:
            continue
        if getattr(block, "visual_policy", None) is not None:
            continue
        mode = _default_visual_mode_for(block)
        if mode is not None:
            block.visual_policy = VisualPolicy(visual_mode=mode)


def _infer_visual_mode(block: ContentBlock) -> str:
    """Best-effort visual_mode when the planner left it 'auto'."""
    layout = getattr(block, "layout", None)
    if layout == "background":
        return "photo_background"
    if layout == "split":
        return "supporting_image"
    if getattr(block, "background_query", None):  # CTA atmospheric background
        return "photo_background"
    # about/hero supporting image — an LLM query or a ref-bound scraped photo
    if getattr(block, "image_query", None) or getattr(block, "image_url", None):
        return "supporting_image"
    return "plain"


def section_visual_input_for(
    block: ContentBlock, *, image_band: Band | None = None
) -> SectionVisualInput:
    """Derive the luminance-pass input from a block's visual_policy.

    Blocks with no policy don't participate — the legacy apply_section_rhythm
    still handles them (back-compat). `image_band` is the band of the section's
    resolved featured image (captured in plan_to_site, Phase 4b); it only matters
    for anchored sections. None → anchored sections default light per §8.4.
    """
    policy = getattr(block, "visual_policy", None)
    if policy is None:
        return SectionVisualInput(participates=False)

    mode = policy.visual_mode
    if mode == "auto":
        mode = _infer_visual_mode(block)

    override = policy.band_override if policy.band_override != "auto" else None
    anchored = mode == "supporting_image"
    return SectionVisualInput(
        participates=True,
        anchored=anchored,
        image_band=image_band if anchored else None,
        is_photo_background=(mode == "photo_background"),
        band_override=override,
    )


def _is_own_surface(styles: dict[str, Any]) -> bool:
    """Whether an element carries its own (non-transparent) surface — a card or
    solid button whose text keeps its own colour, not the band's font."""
    if styles.get("background"):
        return True
    bg = styles.get("backgroundColor")
    return bg not in (None, "transparent", "rgba(0,0,0,0)")


def _recolor_text_for_dark(
    node: BuilderElement, color: str, *, inside_surface: bool = False
) -> None:
    """Recolour a dark-band section's text (and inline links) to `color`,
    skipping any subtree under its own surface (cards / solid buttons keep
    their designed colours)."""
    content = node.content
    if not isinstance(content, list):
        return
    for child in content:
        cs = child.styles or {}
        child_surface = inside_surface or _is_own_surface(cs)
        if child.type in ("text", "link") and not child_surface:
            child.styles = {**cs, "color": color}
        _recolor_text_for_dark(child, color, inside_surface=child_surface)


def apply_luminance_rhythm(
    sections: list[BuilderElement],
    inputs: list[SectionVisualInput],
    palette: ColorPalette,
) -> list[SectionBandPlan]:
    """Emit brand band colours + a contrasting font onto participating sections.

    Resolves the band plan, then for each participating section sets
    `backgroundColor` to the band's brand colour, recolours child text on dark
    bands (protecting cards/solid buttons), and applies a within-band luminance
    step + hairline border on forced anchor collisions (§3.3 step 4). Returns the
    plans so the caller can run the legacy rhythm over the non-participants.
    Mutates `sections` in place. See SECTION_VISUAL_POLICY_SPEC.md §6/§7.
    """
    plans = resolve_section_bands(inputs)
    for section, plan in zip(sections, plans):
        if plan.band is None:
            continue
        bg, fg = band_colors(palette, plan.band)
        if plan.separator_before:
            bg = _adjust_lightness(bg, 0.07 if plan.band == "dark" else -0.05)
        styles = dict(section.styles or {})
        styles["backgroundColor"] = bg
        if plan.separator_before:
            styles["borderTop"] = f"1px solid {_adjust_lightness(bg, 0.12)}"
        section.styles = styles
        if plan.band == "dark":
            _recolor_text_for_dark(section, fg)
    return plans


import re as _re

_RGBA = _re.compile(
    r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)"
)
_HEX6 = _re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_VAR_TOKEN = _re.compile(r"var\(\s*(--builder-[a-z-]+)")


def _token_hex(token: str, theme: ThemeTokens) -> str | None:
    """Resolve a `--builder-*` custom property to its concrete hex for the active
    scheme. Mirrors ThemeTokens.to_builder_styles(), so the contrast we compute
    here is exactly what the renderer paints."""
    p = theme.palette
    return {
        "--builder-color-primary": p.primary,
        "--builder-color-secondary": p.secondary,
        "--builder-color-accent": p.accent,
        "--builder-color-text": p.text,
        "--builder-color-background": p.background,
        "--builder-color-surface": p.surface,
        "--builder-page-background": theme.page.background,
    }.get(token)


def _expand_hex(h: str) -> str:
    h = h.strip()
    if len(h) == 4:  # #abc → #aabbcc
        return "#" + "".join(c * 2 for c in h[1:])
    return h


def _to_hex(rgb: tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % rgb


def _parse_color(
    value: object, theme: ThemeTokens
) -> tuple[tuple[int, int, int], float] | None:
    """Resolve a CSS colour (hex / rgb(a) / `var(--builder-*)`) to ((r,g,b), alpha).

    Returns None for gradients, url()s, `transparent`, keywords, or anything we
    can't read as a flat colour — callers treat those as "not a solid surface"."""
    if not isinstance(value, str):
        return None
    v = value.strip()
    if not v or "gradient(" in v or "url(" in v:
        return None
    if v.lower() in ("transparent", "none", "currentcolor", "inherit"):
        return None
    m = _VAR_TOKEN.search(v)
    if m:
        hexv = _token_hex(m.group(1), theme)
        return (_hex_to_rgb(_expand_hex(hexv)), 1.0) if hexv else None
    if _HEX6.match(v):
        return _hex_to_rgb(_expand_hex(v)), 1.0
    mm = _RGBA.search(v)
    if mm:
        a = float(mm.group(4)) if mm.group(4) is not None else 1.0
        rgb = (int(float(mm.group(1))), int(float(mm.group(2))), int(float(mm.group(3))))
        return rgb, a
    return None


def _composite(
    fg: tuple[int, int, int], alpha: float, bg: tuple[int, int, int]
) -> tuple[int, int, int]:
    """Alpha-composite a semi-transparent colour over an opaque background."""
    return tuple(round(alpha * f + (1 - alpha) * b) for f, b in zip(fg, bg))  # type: ignore[return-value]


def _has_opaque_gradient(styles: dict[str, Any]) -> bool:
    """True when the node paints a fully opaque CSS gradient fill — its text ink
    was designed against the gradient, which _parse_color cannot read.

    Decorative overlays (mesh gradients fading to `transparent`, rgba stops
    with alpha < 1) do NOT count: they let the node's real backgroundColor show
    through, so contrast must still be judged against that colour."""
    for key in ("backgroundImage", "background"):
        v = styles.get(key)
        if not isinstance(v, str) or "gradient(" not in v or "url(" in v:
            continue
        if "transparent" in v:
            continue
        if any(
            m.group(4) is not None and float(m.group(4)) < 0.999
            for m in _RGBA.finditer(v)
        ):
            continue
        return True
    return False


def _has_real_photo(styles: dict[str, Any]) -> bool:
    """True if a background carries a genuine photo (a non-`data:` url). Decorative
    gradients and `url(data:…svg…)` grain are transparent overlays, not photos."""
    for key in ("backgroundImage", "background"):
        v = styles.get(key)
        if isinstance(v, str):
            for m in _re.finditer(r"url\(\s*['\"]?([^'\")]+)", v):
                if not m.group(1).strip().lower().startswith("data:"):
                    return True
    return False


_WHATSAPP_HREF = re.compile(r"(?:^whatsapp:|//wa\.me/|//api\.whatsapp\.com/)", re.IGNORECASE)

# Official WhatsApp glyph (simple-icons path), white fill, inlined as a data URI
# so the button needs no asset pipeline. Rendered as a backgroundImage on the
# link's left edge; paddingLeft clears it.
_WHATSAPP_ICON = (
    "url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
    "viewBox='0 0 24 24' fill='white'%3E%3Cpath d='M17.472 14.382c-.297-.149-1.758"
    "-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173."
    "199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1."
    "653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198"
    "-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.24"
    "2-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-."
    "272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 "
    "3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-."
    "085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-"
    ".57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982."
    "998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.8"
    "84 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.43"
    "7 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335"
    ".157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 "
    "0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3."
    "48-8.413z'/%3E%3C/svg%3E\")"
)

# WhatsApp's brand green; fixed on purpose (recognisability over theme harmony —
# users tap the green pill because they know exactly what it does).
_WHATSAPP_GREEN = "#25D366"


def style_whatsapp_links(elements: list[BuilderElement]) -> int:
    """First-class WhatsApp buttons: any body link whose href opens a WhatsApp
    chat (wa.me / api.whatsapp.com / whatsapp:) is restyled as the recognisable
    green pill with the WhatsApp glyph, regardless of which template or legacy
    builder produced it. Icon-only links (no visible label) are left alone.
    Returns the number of links restyled. Run AFTER enforce_text_contrast so the
    white-on-green ink is never retinted."""
    styled = 0

    def walk(el: BuilderElement) -> None:
        nonlocal styled
        if isinstance(el.content, list):
            for child in el.content:
                walk(child)
            return
        content = el.content
        href = getattr(content, "href", None) or ""
        label = (getattr(content, "innerText", None) or "").strip()
        if el.type == "link" and len(label) >= 2 and _WHATSAPP_HREF.search(href):
            el.styles = {
                **(el.styles or {}),
                "display": "inline-flex",
                "alignItems": "center",
                "justifyContent": "center",
                "width": "fit-content",
                "backgroundColor": _WHATSAPP_GREEN,
                "color": "#ffffff",
                "backgroundImage": _WHATSAPP_ICON,
                "backgroundRepeat": "no-repeat",
                "backgroundPosition": "18px center",
                "backgroundSize": "18px 18px",
                "paddingTop": "12px",
                "paddingBottom": "12px",
                "paddingLeft": "46px",
                "paddingRight": "24px",
                "borderRadius": "999px",
                "border": "none",
                "fontWeight": 700,
                "textDecoration": "none",
            }
            styled += 1

    for el in elements:
        walk(el)
    return styled


def enforce_text_contrast(elements: list[BuilderElement], theme: ThemeTokens) -> int:
    """Scheme-agnostic contrast safety net. Retargets any text whose colour fails
    contrast against its *resolved* band background to that band's correct
    foreground (white on a dark band, dark ink on a light band).

    Catalogue sections hard-code a single palette token for text — often
    `secondary`, which is dark in *every* scheme — so it vanishes whenever the
    section lands on a same-luminance band: dark-on-dark (dark scheme, or a dark
    CTA band in a light scheme) or light-on-light. Because the replacement is
    chosen from the band's luminance, not the global scheme, this works in both
    schemes. It only flips text that is on the *wrong* side of its background
    (the flip must strictly improve contrast and differ from the current colour),
    so photo overlays, cards that paint their own surface, and brand-colour text
    that is merely low-contrast-but-correct are left untouched. Mutates in place;
    returns the count changed."""
    page_rgb = _hex_to_rgb(_expand_hex(theme.page.background))
    # Brand-hued text (primary/accent eyebrows, links) is intentional even when it
    # lands a little under AA on a band — never recolour it. The vanishing-text bug
    # is always `secondary`/slate, never a brand colour.
    brand_rgb = {
        _hex_to_rgb(_expand_hex(theme.palette.primary)),
        _hex_to_rgb(_expand_hex(theme.palette.accent)),
    }
    changed = 0

    def walk(node: BuilderElement, bg_rgb: tuple[int, int, int], photo: bool) -> None:
        nonlocal changed
        styles = node.styles if isinstance(node.styles, dict) else {}
        # A genuine photo or an opaque gradient fill owns its subtree's text:
        # the template designed that ink against its own backdrop (scrim/photo
        # overlay or gradient tones we can't parse into a flat colour).
        # Crucially this holds even when the SAME node also carries a
        # backgroundColor — the photo/gradient paints over it, so that colour
        # must not be treated as the effective surface (it used to flip the
        # catalog's white hero headings to dark ink under dark gradients and
        # photos). Semi-transparent decorative gradients don't count — see
        # _has_opaque_gradient.
        covered = _has_real_photo(styles) or _has_opaque_gradient(styles)
        if covered:
            photo = True
        # A solid / semi-transparent own background updates the effective surface
        # and (painting over whatever is beneath) clears the photo-owned flag —
        # unless this very node is the one carrying the photo/gradient.
        own = _parse_color(styles.get("backgroundColor"), theme) or _parse_color(
            styles.get("background"), theme
        )
        if own is not None:
            (r, g, b), a = own
            bg_rgb = (r, g, b) if a >= 0.999 else _composite((r, g, b), a, bg_rgb)
            if not covered:
                photo = False

        content = node.content
        if isinstance(content, BuilderElementContent):
            if node.type == "text" and not photo:
                fg = _parse_color(styles.get("color"), theme)
                if fg is not None and fg[0] not in brand_rgb:
                    fg_hex, bg_hex = _to_hex(fg[0]), _to_hex(bg_rgb)
                    # Only "wrong-side" text — the same luminance tone as its band —
                    # is the vanishing-text bug (dark-on-dark / light-on-light). Text
                    # on the correct side that is merely muted (a soft caption, a
                    # low-contrast-but-legible accent) is left as designed.
                    same_side = (_relative_luminance(fg_hex) < 0.5) == (
                        _relative_luminance(bg_hex) < 0.5
                    )
                    safe = _text_for_background(bg_hex)
                    if (
                        same_side
                        and _contrast(fg_hex, bg_hex) < 4.5
                        and safe.lower() != fg_hex.lower()
                    ):
                        new_styles = dict(styles)
                        new_styles["color"] = safe
                        if fg[1] < 1.0 and "opacity" not in new_styles:
                            new_styles["opacity"] = f"{round(fg[1] * 100)}%"
                        node.styles = new_styles
                        changed += 1
        elif isinstance(content, list):
            for child in content:
                if isinstance(child, BuilderElement):
                    walk(child, bg_rgb, photo)

    for el in elements:
        walk(el, page_rgb, False)
    return changed


# The design brief's cheerful pastel set (cream, sky, mint, peach, lavender,
# butter). All are very light (Tailwind 50/100-class tints), so the theme's dark
# ink keeps a wide WCAG margin on every one — a childcare page can rotate through
# them band-to-band and stay legible. Ordered so adjacent bands contrast in hue.
_CHILDCARE_PASTELS: tuple[str, ...] = (
    "#FFF7ED",  # cream
    "#E0F2FE",  # sky blue
    "#DCFCE7",  # mint green
    "#FFEDD5",  # peach
    "#EDE9FE",  # lavender
    "#FEF9C3",  # butter yellow
)


# Vivid, cheerful section-title inks for childcare — one per section so the
# page's headings read multi-coloured instead of one dark theme hue. All are
# Tailwind-700-class: saturated but dark enough to clear WCAG AA (≥4.5:1) on
# every pastel band above, so they stay legible whichever pastel they land on.
_CHILDCARE_HEADING_INKS: tuple[str, ...] = (
    "#BE123C",  # rose
    "#6D28D9",  # violet
    "#1D4ED8",  # blue
    "#0F766E",  # teal
    "#BE185D",  # pink
    "#047857",  # emerald
)


def _first_named_text(el: BuilderElement, name: str) -> BuilderElement | None:
    """The first descendant text node named `name` (depth-first), or None."""
    if el.type == "text" and (el.name or "") == name:
        return el
    content = el.content
    if isinstance(content, list):
        for child in content:
            found = _first_named_text(child, name)
            if found is not None:
                return found
    return None


def apply_childcare_heading_colors(sections: list[BuilderElement]) -> None:
    """Give each pastel section's TITLE its own vivid colour (rose/violet/blue/
    teal/pink/emerald), so the page's headings are multi-coloured rather than one
    dark theme hue — the cheerful direction, extended past the hero.

    Only the section-level title (the node named "Heading") is recoloured; card
    titles and body copy stay dark for readability. Heroes (their own treatment)
    and photo/gradient sections are skipped. Run AFTER apply_childcare_pastel_
    rhythm so its dark-ink recolour doesn't overwrite these. Mutates in place."""
    i = 0
    for section in sections:
        if (section.name or "").startswith("Hero"):
            continue
        styles = section.styles or {}
        if styles.get("backgroundImage") or styles.get("background"):
            continue
        heading = _first_named_text(section, "Heading")
        if heading is None:
            continue
        heading.styles = {
            **(heading.styles or {}),
            "color": _CHILDCARE_HEADING_INKS[i % len(_CHILDCARE_HEADING_INKS)],
        }
        i += 1


def apply_childcare_pastel_rhythm(sections: list[BuilderElement], ink: str) -> None:
    """Recolour a childcare page's flat-background sections through the rotating
    pastel set, so the page reads cheerful and multi-coloured instead of one hue
    plus a dark band (the brief's "bright, layered" direction).

    Only sections whose background is a FLAT colour are recoloured; anything
    painting a real photo, gradient, or texture (the photo hero, the photo CTA,
    an interior washed-split hero) is left untouched so those moments survive.
    Every recoloured band gets the theme's dark `ink` — all pastels are light, so
    this also flips any previously-light text (e.g. a former dark band) back to
    legible dark and drops the dark-band hairline. Mutates in place.

    Run AFTER the luminance/legacy rhythm (it overrides their flat colours) and
    BEFORE apply_section_dividers (so seam fills read the final pastel colours)."""
    i = 0
    for section in sections:
        styles = dict(section.styles or {})
        # A real photo/gradient/texture fill → leave it as the designed moment.
        if styles.get("backgroundImage") or styles.get("background"):
            continue
        # The announcement strap is a designed brand-primary bar with matching
        # ink — repainting it pastel while its ink stays light made it vanish.
        if (section.name or "") == "Linkbar":
            continue
        styles["backgroundColor"] = _CHILDCARE_PASTELS[i % len(_CHILDCARE_PASTELS)]
        styles.pop("borderTop", None)  # a former dark-band hairline reads wrong on pastel
        section.styles = styles
        i += 1
        # Pastels are light → force dark ink (undoes any dark-band recolour).
        _recolor_text_for_dark(section, ink)


def _split_columns_row(section: BuilderElement) -> BuilderElement | None:
    """The two-column row of an about split section, or None.

    Both split variants (about-image-split / about-editorial-split) render as a
    section container with a direct 2Col child holding [copy, image]; the
    no-image about-story has no 2Col, so it never matches.
    """
    content = section.content
    if not isinstance(content, list):
        return None
    for child in content:
        if (
            child.type == "2Col"
            and isinstance(child.content, list)
            and len(child.content) == 2
        ):
            return child
    return None


def apply_about_zigzag(sections: list[BuilderElement]) -> None:
    """Alternate the image side of a page's about split sections.

    Story pages render one image+text split per source section; the template
    always puts the copy column first, so without this every row reads
    text-left/image-right. Reversing the 2Col children on every second split
    zigzags the photos left/right — structural, so it needs no new template
    or CSS support in the builder. Mutates in place.
    """
    i = 0
    for section in sections:
        if not (section.name or "").startswith("About"):
            continue
        row = _split_columns_row(section)
        if row is None:
            continue
        if i % 2 == 1:
            row.content = list(reversed(row.content))  # type: ignore[arg-type]
        i += 1


def apply_section_rhythm(sections: list[BuilderElement]) -> None:
    """Alternate plain sections between the page background and a surface tint for
    visual rhythm (color-blocking). Sections that already carry their own fill — a
    gradient, a background photo, or any non-page background colour (dark CTA,
    surface-tinted testimonial) — are left untouched and don't advance the toggle,
    so the rhythm reads cleanly around them. Mutates in place."""
    surface_next = False
    for section in sections:
        styles = dict(section.styles or {})
        has_own_fill = bool(
            styles.get("background")
            or styles.get("backgroundImage")
            or (styles.get("backgroundColor") not in (None, _PAGE_BG))
        )
        if has_own_fill:
            continue
        if surface_next:
            styles["backgroundColor"] = _SURFACE_BG
            section.styles = styles
        surface_next = not surface_next


def block_to_section(
    block: ContentBlock,
    *,
    explicit_id: str | None = None,
    mood: BrandMood | None = None,
    is_homepage: bool = True,
    hero_scroll_target_kind: str | None = None,
    variety_seed: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Map a block to (template, content). Returns None if the kind is unsupported.

    Layout precedence (all gated by is_feasible in select_template):
    explicit_id → content preference (_PREFERENCE) → mood preference → pool[0].

    ``variety_seed`` (the brand name, threaded from plan_to_site) rotates the
    MOOD-preference tail per brand so two same-mood sites stop converging on
    one identical layout when the LLM design brain is off or fails — the exact
    convergence design_brain.py documents. Content preference and explicit ids
    are never rotated: imagery use and the LLM's deliberate picks still lead.
    None (the default, and every direct test call) keeps the legacy order.

    For a hero, ``is_homepage`` / ``hero_scroll_target_kind`` drive the interior-
    page CTA policy (see ``apply_hero_cta_policy``); the default (homepage hero)
    leaves the CTA untouched.
    """
    kind = block.kind
    mapper = _MAPPERS.get(kind)
    if mapper is None:
        return None
    content = mapper(block)
    # Photo-topped card grids are a hard site policy, not a stylistic choice:
    # when every card carries an image, the image variant wins even over the
    # design-brain's explicit pick (which draws from ALL variants and would
    # otherwise happily re-select the text-only grid).
    if kind in ("features", "services") and _most_items_have_images(content):
        explicit_id = f"{kind}-image-cards"
        # Friendly/playful brands (childcare et al) get the badge-carrying
        # program cards instead — same photo-topped policy, warmer framing.
        if kind == "services" and mood in ("friendly", "playful"):
            explicit_id = "services-programs-age"
    pref_fn = _PREFERENCE.get(kind)
    content_pref = pref_fn(content, block) if pref_fn else []
    # Content leads the layout choice so available imagery is actually used.
    # Mood remains a fallback/tiebreaker among still-feasible variants.
    preferred = list(content_pref or []) + mood_preferred_ids(mood, kind)
    # Imagery-led preference is a HARD signal (show the photo, don't say it).
    # Text-only sections have no such anchor — their preference head is just
    # a sensible default, and "always the default" is exactly the per-mood
    # convergence design_brain.py documents. With a variety_seed (brand name,
    # threaded from plan_to_site), rotate the candidate head within the top 3
    # so different brands lead with different (still feasible, still
    # mood-gated) variants; one brand stays idempotent, and every direct call
    # without a seed keeps the legacy order.
    has_image_signal = bool(content.get("image")) or _most_items_have_images(content)
    if variety_seed and not has_image_signal:
        deduped: list[str] = []
        for pid in preferred:
            if pid not in deduped:
                deduped.append(pid)
        if len(deduped) > 1:
            from app.services.diversity import seeded_index

            offset = seeded_index(
                variety_seed, f"template:{kind}", min(3, len(deduped))
            )
            preferred = deduped[offset:] + deduped[:offset]
    template = select_template(
        kind, content, preferred_ids=preferred, explicit_id=explicit_id, mood=mood
    )
    if template is None:
        return None
    # The CTA policy runs AFTER selection so it can key off the chosen variant.
    if kind == "hero":
        apply_hero_cta_policy(
            content,
            template["id"],
            is_homepage=is_homepage,
            scroll_target_kind=hero_scroll_target_kind,
        )
    return template, content
