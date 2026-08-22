"""The Page is the authority on its own facts — not the model.

Prompt-level fidelity rules and `scaffold_enforcement`'s grounding net do most
of the work, but both are probabilistic in the end: the net matches a claim
against `raw_text`, and a fluent near-miss ("Mon-Fri 9-6" when the Page says
"Monday 09:00-18:00") can slip through looking grounded.

For a Facebook source we can do better than "probably right", because we hold
the structured values. So after the model has written and the net has run, this
pass overwrites every fact-bearing field with what the Page actually said, and
**drops what the Page never said** — an LLM guess at a phone number never gets
to stand simply because no rule caught it.

Same precedent as the profile refill in `routers/generate.py`, where a scraped
card is treated as "the authority on this person's portrait AND contacts".
"""

from __future__ import annotations

import logging

from app.models.content_blocks import (
    ContactBlock,
    LocationItem,
    LocationsBlock,
    PagePlan,
    StatItem,
    StatsBlock,
    TestimonialItem,
    TestimonialsBlock,
)
from app.models.facebook import FacebookPage

logger = logging.getLogger(__name__)


def _contact(block: ContactBlock, page: FacebookPage) -> ContactBlock:
    """Page values win; absent Page values null the model's guess."""
    return block.model_copy(
        update={"email": page.primary_email, "phone": page.phone}
    )


def _locations(block: LocationsBlock, page: FacebookPage) -> LocationsBlock | None:
    """One real branch, or nothing. The Page states one address at most."""
    address = page.address_line
    if not address:
        return None
    return block.model_copy(
        update={
            "items": [
                LocationItem(
                    name=page.name,
                    address=address,
                    phone=page.phone,
                    # WhatsApp is never inferred from a phone number — the
                    # country code guess is exactly the kind of plausible
                    # fabrication this pass exists to stop.
                    whatsapp=None,
                    hours=page.hours_text,
                )
            ]
        }
    )


def _testimonials(
    block: TestimonialsBlock, page: FacebookPage
) -> TestimonialsBlock | None:
    """Real recommendations, verbatim and attributed, or nothing.

    `avatar_query=None` is deliberate: pulling a stock portrait for a real named
    reviewer attaches a fabricated face to a real person, which is the same
    class of error as a fabricated quote.
    """
    if not page.reviews:
        return None
    items = [
        TestimonialItem(quote=r.text, author=r.author, role=None, avatar_query=None)
        for r in page.reviews[:6]  # TestimonialsBlock caps at 6
    ]
    return block.model_copy(update={"items": items})


def _stats(block: StatsBlock, page: FacebookPage) -> StatsBlock | None:
    """Only the counts Facebook actually reports."""
    items: list[StatItem] = []
    if page.fan_count is not None:
        items.append(StatItem(value=f"{page.fan_count:,}", label="Followers on Facebook"))
    if page.overall_star_rating is not None and page.rating_count is not None:
        items.append(
            StatItem(
                value=f"{page.overall_star_rating:.1f}",
                label=f"Average rating from {page.rating_count:,} reviews",
            )
        )
    if page.founded:
        items.append(StatItem(value=page.founded, label="Established"))
    if not items:
        return None
    return block.model_copy(update={"items": items[:6]})


def enforce_facebook_facts(
    pages: list[PagePlan], page: FacebookPage
) -> list[PagePlan]:
    """Rewrite every fact-bearing block from the Page's own values.

    Runs last, after `align_page_to_scaffold` has finished, so nothing
    downstream can reintroduce a model-authored fact.
    """
    out: list[PagePlan] = []
    for plan in pages:
        blocks = []
        changed: list[str] = []
        for block in plan.blocks:
            kind = getattr(block, "kind", None)
            replacement: object | None = block

            if kind == "contact" and isinstance(block, ContactBlock):
                replacement = _contact(block, page)
                if (block.email, block.phone) != (
                    replacement.email,
                    replacement.phone,
                ):
                    changed.append("contact")
            elif kind == "locations" and isinstance(block, LocationsBlock):
                replacement = _locations(block, page)
                changed.append("locations" if replacement else "locations(dropped)")
            elif kind == "testimonials" and isinstance(block, TestimonialsBlock):
                replacement = _testimonials(block, page)
                changed.append(
                    "testimonials" if replacement else "testimonials(dropped)"
                )
            elif kind == "stats" and isinstance(block, StatsBlock):
                replacement = _stats(block, page)
                changed.append("stats" if replacement else "stats(dropped)")

            if replacement is not None:
                blocks.append(replacement)

        if changed:
            logger.info(
                "Facebook authority: /%s — %s taken from the Page",
                plan.slug or "", ", ".join(changed),
            )
        out.append(plan.model_copy(update={"blocks": blocks}))
    return out
