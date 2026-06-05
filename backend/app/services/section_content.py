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

from typing import Any, Callable

from app.models.brand import BrandMood
from app.models.builder_schema import BuilderElement
from app.models.content_blocks import (
    AboutBlock,
    ContactBlock,
    ContentBlock,
    CtaBlock,
    FaqBlock,
    FeaturesBlock,
    GalleryBlock,
    HeroBlock,
    MenuBlock,
    PricingBlock,
    ProcessBlock,
    ServicesBlock,
    TeamBlock,
    TestimonialsBlock,
)
from app.services.template_filler import get_template, templates_for_type


def _link(label: str | None, href: str | None) -> dict[str, str] | None:
    if not label:
        return None
    return {"innerText": label, "href": href or "#"}


def _image(query: str | None, alt: str | None) -> dict[str, str] | None:
    if not query:
        return None
    return {"query": query, "alt": alt or ""}


# --- content mappers (block -> {slot: value}) -----------------------------------


def _hero_content(b: HeroBlock) -> dict[str, Any]:
    return {
        "eyebrow": b.eyebrow,
        "headline": b.headline,
        "body": b.subheadline,
        "primary_cta": {"innerText": b.primary_cta_label, "href": b.primary_cta_href},
        "secondary_cta": _link(b.secondary_cta_label, b.secondary_cta_href),
        "image": _image(b.image_query, b.image_alt or b.headline),
    }


def _about_content(b: AboutBlock) -> dict[str, Any]:
    return {
        "eyebrow": "About",
        "heading": b.heading,
        "subheading": b.body or None,
        "image": _image(b.image_query, b.image_alt or b.heading),
    }


def _features_content(b: FeaturesBlock) -> dict[str, Any]:
    return {
        "eyebrow": "Features",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [{"title": i.title, "description": i.description} for i in b.items],
    }


def _services_content(b: ServicesBlock) -> dict[str, Any]:
    return {
        "eyebrow": "Services",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [
            {"title": i.title, "description": i.description, "ideal": None}
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
        # supplied an atmospheric query — else selection falls back to a gradient.
        "image": _image(b.background_query, b.headline),
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
    return {
        "eyebrow": "Team",
        "heading": b.heading,
        "subheading": b.subheading,
        "items": [
            {
                "photo": _image(m.photo_query, m.name),
                "name": m.name,
                "role": m.role,
                "bio": m.bio,
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
            {"image": _image(i.image_query, i.title or i.caption or "")}
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
}


# --- preference (best-first template ids per block hints) ------------------------


def _hero_preference(content: dict[str, Any], b: HeroBlock) -> list[str]:
    if content.get("image"):
        if b.layout == "background":
            return ["hero-background-bold", "hero-modern-split", "hero-gradient", "hero-centered-minimal"]
        return ["hero-modern-split", "hero-background-bold", "hero-gradient", "hero-centered-minimal"]
    # No photo: a brand gradient reads richer/on-trend; minimal is the quiet fallback.
    return ["hero-gradient", "hero-centered-minimal"]


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


def _features_preference(content: dict[str, Any], b: FeaturesBlock) -> list[str]:
    # Match column count to item count: 3+ -> 3-col grid, 1-2 -> 2-col.
    return ["features-card-grid"] if len(b.items) >= 3 else ["features-two-col", "features-card-grid"]


def _services_preference(content: dict[str, Any], b: ServicesBlock) -> list[str]:
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
    "luxury": ["centered", "editorial", "narrative", "minimal", "split", "single"],
    "friendly": ["split", "grid", "banner", "background"],
    "technical": ["grid", "minimal", "stacked", "centered", "split"],
    "editorial": ["editorial", "split", "narrative", "single", "background"],
    "playful": ["bento", "background", "gradient", "banner", "grid"],
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


def select_template(
    section_type: str,
    content: dict[str, Any],
    *,
    preferred_ids: list[str] | None = None,
    explicit_id: str | None = None,
) -> dict[str, Any] | None:
    """Choose a catalog template for a section: feasibility filter, then preference."""
    candidates = templates_for_type(section_type)
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
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Map a block to (template, content). Returns None if the kind is unsupported.

    Layout precedence (all gated by is_feasible in select_template):
    explicit_id → mood preference → content preference (_PREFERENCE) → pool[0].
    """
    kind = block.kind
    mapper = _MAPPERS.get(kind)
    if mapper is None:
        return None
    content = mapper(block)
    pref_fn = _PREFERENCE.get(kind)
    content_pref = pref_fn(content, block) if pref_fn else []
    # Mood leads the layout choice; content preference is the fallback/tiebreaker.
    preferred = mood_preferred_ids(mood, kind) + list(content_pref or [])
    template = select_template(
        kind, content, preferred_ids=preferred, explicit_id=explicit_id
    )
    if template is None:
        return None
    return template, content
