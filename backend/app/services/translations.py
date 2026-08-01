"""
Build a translated page by cloning its source-language counterpart.

A multilingual source site (mmta.org.my ships /bm and /zh copies of every page)
gives us the same page twice. Generating each language independently would pay
the full content + design + imagery cost per language and — worse — let the two
drift: different hero photo, different section rhythm, different template
variants for pages that are meant to be the same page in another language.

So a translated page is never planned. It is *cloned*: the finished PagePlan of
its counterpart, deep-copied, with only the human-readable strings replaced.
Everything that decides how the page looks — image refs and URLs, layout hints,
section order, block kinds — is copied verbatim, and re-copied defensively after
the model answers (see ``_TRANSLATABLE_FIELDS``). The design is identical by
construction rather than by instruction.

Text comes from the owner's *own* translated page where it covers the same
ground; the model falls back to translating our copy only for slots the source
doesn't speak to (a CTA label we wrote, say). That keeps the owner's real
Malay/Chinese wording instead of round-tripping their English through us.

Failure is never fatal: any error, or a shape that doesn't match the original,
leaves the clone with its counterpart's text. A page in the wrong language is a
flaw; a failed generation is a broken deliverable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, Field

from app.models.content_blocks import ContentBlock, PagePlan, SourceContent
from app.models.industry import PageScaffold
from app.services.llm import LlmClient, LlmError, get_llm
from app.services.locale import locale_label

logger = logging.getLogger(__name__)


# Only these carry prose. Everything else — image refs and queries, hrefs,
# person and company names, prices, years, stat values, phone/email, block
# kinds and layout hints — is restored from the counterpart after the model
# answers. An allowlist (not a blocklist) so a field added to any block model
# later defaults to "preserved" rather than silently becoming translatable.
_TRANSLATABLE_FIELDS = frozenset({
    "answer", "audience", "bio", "body", "caption", "cta_label", "description",
    "eyebrow", "heading", "headline", "headline_accent", "hours", "image_alt",
    "label", "photo_alt", "primary_cta_label", "question", "quote", "role",
    "secondary_cta_label", "subheading", "subheadline", "title",
})

# Cap on how much of the translated source page we show the model. The page's
# own words are the point, but a long roster page would crowd out the blocks.
_MAX_SOURCE_CHARS = 6000

TRANSLATION_PROMPT = """You are localizing one page of a website that already exists in another language.

You are given:
  1. `blocks` — the finished page content, as JSON.
  2. `source_text` — the site owner's OWN version of this page in the target language.

Rewrite every human-readable string in `blocks` into the target language.

Rules:
- Reuse the owner's wording from `source_text` wherever it covers the same
  ground. Their phrasing is authoritative — do not re-translate what they have
  already written themselves. Only translate the existing string when
  `source_text` says nothing about that slot.
- Return the SAME JSON structure: same number of blocks, same order, same
  "kind" on every block, same number of items in every list.
- Never translate: people's names, organisation names, brand names, email
  addresses, phone numbers, URLs, prices, years, and numeric stat values.
- Never change any field that is not human-readable prose (image references,
  layout hints, links).
- Keep the register and length close to the original — these strings sit in a
  fixed layout, so a heading that triples in length will break it.

Reply ONLY with valid JSON matching the schema."""


class TranslatedPageContent(BaseModel):
    """What the model returns: the page's prose, in the target language."""

    title: str = ""
    seo_title: str = ""
    seo_description: str = ""
    blocks: list[ContentBlock] = Field(default_factory=list)


def _localize_href(href: str, locale: str, translated_slugs: set[str]) -> str:
    """Point an internal link at the same page in this locale, when it exists.

    A translated page linking to ``/committee`` should reach ``/bm/committee``
    — but only if we actually built that translation. Otherwise the reader is
    better off on the source-language page than on a 404.
    """
    if not href.startswith("/") or href.startswith("//"):
        return href
    path, _, fragment = href.partition("#")
    slug = path.strip("/")
    if slug.startswith(f"{locale}/") or slug == locale:
        return href  # already localized
    if slug not in translated_slugs:
        return href
    localized = f"/{locale}/{slug}".rstrip("/") if slug else f"/{locale}"
    return f"{localized}#{fragment}" if fragment else localized


def _restore_untranslatable(
    original: BaseModel, translated: BaseModel
) -> BaseModel:
    """Rebuild ``translated`` with every non-prose field taken from ``original``.

    The prompt asks the model to leave those alone; this makes it true. Any
    structural mismatch (different block kind, different item count) falls back
    to the original wholesale — a section in the wrong language beats a section
    whose photo or link the model rewrote.
    """
    if type(original) is not type(translated):
        return original

    updates: dict[str, Any] = {}
    for name in type(original).model_fields:
        old = getattr(original, name, None)
        new = getattr(translated, name, None)

        if isinstance(old, BaseModel):
            updates[name] = (
                _restore_untranslatable(old, new) if isinstance(new, BaseModel) else old
            )
        elif isinstance(old, list) and old and isinstance(old[0], BaseModel):
            if not isinstance(new, list) or len(new) != len(old):
                updates[name] = old  # the model added/dropped items
            else:
                updates[name] = [
                    _restore_untranslatable(o, n) for o, n in zip(old, new)
                ]
        elif name in _TRANSLATABLE_FIELDS:
            # Take the translation only when it's a non-empty string; an empty
            # slot would render as a blank heading.
            updates[name] = new if isinstance(new, str) and new.strip() else old
        else:
            updates[name] = old

    return type(original).model_validate(updates)


def _clone_plan(canonical: PagePlan, scaffold: PageScaffold) -> PagePlan:
    """A deep copy of the counterpart carrying the translation's identity.

    Links are localized later, once the blocks are final — translating replaces
    the block list wholesale, so rewriting hrefs here would be undone.
    """
    clone = canonical.model_copy(deep=True)
    clone.slug = scaffold.slug
    clone.title = scaffold.title
    clone.description = scaffold.description or canonical.description
    clone.is_homepage = False  # the localized home is a page, not THE homepage
    clone.parent_slug = scaffold.parent_slug
    clone.nav_rank = None
    clone.from_source = True
    clone.locale = scaffold.locale
    clone.translation_of = scaffold.translation_of
    return clone


def _localize_block_hrefs(
    value: Any, locale: str, translated_slugs: set[str]
) -> None:
    """Walk a block tree rewriting internal hrefs to their localized twin."""
    if isinstance(value, list):
        for item in value:
            _localize_block_hrefs(item, locale, translated_slugs)
        return
    if not isinstance(value, BaseModel):
        return
    for name in type(value).model_fields:
        child = getattr(value, name, None)
        if isinstance(child, str) and name.endswith("href"):
            setattr(value, name, _localize_href(child, locale, translated_slugs))
        elif isinstance(child, (list, BaseModel)):
            _localize_block_hrefs(child, locale, translated_slugs)


def _source_excerpt(source: SourceContent | None) -> str:
    if source is None:
        return ""
    parts = [source.title or "", *source.headings, source.raw_text or ""]
    return "\n".join(p for p in parts if p).strip()[:_MAX_SOURCE_CHARS]


async def _translate_one(
    clone: PagePlan,
    canonical: PagePlan,
    source: SourceContent | None,
    *,
    client: LlmClient,
) -> PagePlan:
    """Fill one clone's prose. Returns it untouched on any failure."""
    locale = clone.locale or ""
    excerpt = _source_excerpt(source)
    payload = TranslatedPageContent(
        title=canonical.title,
        seo_title=canonical.seo_title,
        seo_description=canonical.seo_description,
        blocks=canonical.blocks,
    )
    user_prompt = (
        f"Target language: {locale_label(locale)} (code: {locale})\n\n"
        f"blocks:\n{payload.model_dump_json(indent=1)}\n\n"
        f"source_text (the owner's own {locale_label(locale)} version of this page):\n"
        f"{excerpt or '(none available — translate the strings above)'}"
    )

    try:
        result = await client.chat_json(
            system_prompt=TRANSLATION_PROMPT,
            user_prompt=user_prompt,
            schema=TranslatedPageContent,
        )
    except (LlmError, asyncio.TimeoutError) as exc:
        logger.warning(
            "Translation of /%s into %s failed (%s) — keeping source-language copy",
            clone.slug, locale, exc,
        )
        return clone

    if len(result.blocks) != len(canonical.blocks):
        logger.warning(
            "Translation of /%s into %s returned %d blocks for %d — keeping "
            "source-language copy",
            clone.slug, locale, len(result.blocks), len(canonical.blocks),
        )
        return clone

    clone.blocks = [
        _restore_untranslatable(original, translated)  # type: ignore[misc]
        for original, translated in zip(canonical.blocks, result.blocks)
    ]
    if result.title.strip():
        clone.title = result.title.strip()
    if result.seo_title.strip():
        clone.seo_title = result.seo_title.strip()
    if result.seo_description.strip():
        clone.seo_description = result.seo_description.strip()
    return clone


async def build_translated_pages(
    pages: list[PagePlan],
    scaffolds: list[PageScaffold],
    sources: dict[str, SourceContent],
    *,
    client: LlmClient | None = None,
) -> list[PagePlan]:
    """Clone each translation scaffold from the page it translates.

    ``pages`` are the finished source-language PagePlans (images already bound,
    so the clones inherit the exact same photos). ``sources`` maps a translated
    page's slug to its crawled SourceContent, supplying the owner's own wording.

    Scaffolds whose counterpart wasn't generated are skipped — there is nothing
    to clone.
    """
    wanted = [s for s in scaffolds if s.locale and s.translation_of is not None]
    if not wanted:
        return []

    by_slug = {p.slug: p for p in pages}
    translated_slugs = {
        s.translation_of for s in wanted if s.translation_of in by_slug
    }

    clones: list[tuple[PagePlan, PagePlan, SourceContent | None]] = []
    for scaffold in wanted:
        canonical = by_slug.get(scaffold.translation_of or "")
        if canonical is None:
            logger.info(
                "Skipping /%s — its counterpart /%s wasn't generated",
                scaffold.slug, scaffold.translation_of,
            )
            continue
        clone = _clone_plan(canonical, scaffold)
        clones.append((clone, canonical, sources.get(scaffold.slug)))

    if not clones:
        return []

    llm = client or get_llm()
    results = await asyncio.gather(
        *(
            _translate_one(clone, canonical, source, client=llm)
            for clone, canonical, source in clones
        ),
        return_exceptions=True,
    )

    out: list[PagePlan] = []
    for (clone, _canonical, _source), result in zip(clones, results):
        if isinstance(result, BaseException):
            logger.warning(
                "Translation of /%s crashed (%s) — keeping source-language copy",
                clone.slug, result,
            )
            page = clone
        else:
            page = result
        # Last, on the final blocks: a reader who followed a Malay link should
        # land on the Malay version of the next page where one exists.
        _localize_block_hrefs(page.blocks, page.locale or "", translated_slugs)
        out.append(page)
    logger.info("Built %d translated page(s) by cloning", len(out))
    return out
