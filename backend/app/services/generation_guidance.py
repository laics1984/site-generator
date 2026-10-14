"""Select existing-call copy guidance; no I/O, model calls or public fields."""

from __future__ import annotations

from app.services.prompts import CONVERSION_JOURNEY_GUIDANCE, HERO_COPY_GUIDANCE, PAGE_COPY_GUIDANCE


def guidance_for_page(
    page_type: str, section_kinds: tuple[str, ...], *, conversion_journey: bool = False,
) -> str:
    """Only the instructions relevant to this page, also used for chunked pages."""
    parts = []
    if "hero" in section_kinds:
        parts.append(HERO_COPY_GUIDANCE)
    page_guidance = PAGE_COPY_GUIDANCE.get("landing" if conversion_journey else page_type)
    if page_guidance:
        parts.append(page_guidance)
    if conversion_journey:
        parts.append(CONVERSION_JOURNEY_GUIDANCE)
    return "\n".join(parts)


def legacy_page_guidance() -> str:
    """Free-form planning does not know its page types until the existing call."""
    return "\n".join(
        [HERO_COPY_GUIDANCE]
        + [f"For {page_type} pages: {text}" for page_type, text in PAGE_COPY_GUIDANCE.items()]
    )
