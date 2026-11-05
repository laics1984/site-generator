"""
A page's text in another language, without planning or designing it again.

A multilingual source site (mmta.org.my ships /bm and /zh copies of every page)
gives us the same page twice. Generating each language independently would pay
the full content + design + imagery cost per language and — worse — let the two
drift: different hero photo, different section rhythm, different template
variants for pages that are meant to be the same page in another language.

So a translated page is never planned. Its counterpart's finished PagePlan is
cloned and only the human-readable strings are swapped. **The model sees
strings, never blocks**: one numbered list of the page's distinct prose strings
(the allowlisted `_TRANSLATABLE_FIELDS`), and it answers with the same list
translated. The swap then happens here, field by field, so image refs, hrefs,
layout hints and every other field are untouched by construction — the model
cannot reach them. Sending whole blocks used to let it blank a field it was
told to keep (`visual_policy: ""` on every block of uniquekindies), and one
such field failed validation for the entire page.

The same string map serves both presentations (docs/multilingual-plan.md §3):

- separate language pages — `build_translated_pages` applies it to a clone;
- same-page bilingual — `add_inline_translations` keeps it on the page as
  `PagePlan.inline_translation`, which `inline_translation` turns into
  lang-tagged lines once the page is built.

Text comes from the owner's *own* page in that language where it covers the
same ground; the model translates ours only where the owner is silent. That
keeps their real Malay/Chinese wording instead of round-tripping it through us.

Failure is never fatal: any error, or a reply whose length doesn't match the
list, leaves the page in its primary language. A page in the wrong language is
a flaw; a failed generation is a broken deliverable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

from pydantic import BaseModel, model_validator
from pydantic.json_schema import SkipJsonSchema

from app.models.content_blocks import (
    InlineTranslation,
    PagePlan,
    SourceContent,
    clamp_seo_description,
    clamp_seo_title,
)
from app.models.industry import PageScaffold
from app.services.llm import LlmClient, LlmError, chat_json_cached, get_llm
from app.services.locale import locale_label

logger = logging.getLogger(__name__)


# Only these carry prose. Everything else — image refs and queries, hrefs,
# person and company names, prices, years, stat values, phone/email, block
# kinds and layout hints — never reaches the model. An allowlist (not a
# blocklist) so a field added to any block model later defaults to "kept"
# rather than silently becoming translatable.
_TRANSLATABLE_FIELDS = frozenset({
    "answer", "audience", "bio", "body", "caption", "cta_label", "description",
    "eyebrow", "heading", "headline", "headline_accent", "hours", "image_alt",
    "label", "photo_alt", "primary_cta_label", "question", "quote", "role",
    "secondary_cta_label", "subheading", "subheadline", "title",
})

# Cap on how much of the owner's own page we show the model. Their words are
# the point, but a long roster page would crowd out the strings.
_MAX_SOURCE_CHARS = 6000

TRANSLATION_PROMPT = """You are localizing one page of a website into another language.

You are given:
  1. `strings` — the page's text, one string per entry, numbered.
  2. `source_text` — the site owner's OWN version of this page in the target
     language, when they have one.

Translate every entry of `strings` into the target language.

Rules:
- Reply with `translations`: an object with exactly the same keys as
  `strings` — the value under key N is the translation of string N.
- Reuse the owner's wording from `source_text` wherever it says the same
  thing. Their phrasing is authoritative — do not re-translate what they have
  already written themselves. Only translate a string `source_text` says
  nothing about.
- Never translate: people's names, organisation names, brand names, email
  addresses, phone numbers, URLs, prices, years, and numeric values. A string
  that is only such a thing is returned unchanged.
- Translate meaning, not words: it should read as if written in the target
  language, in the same register.
- Keep each translation close to its original's length — these strings sit
  in a fixed layout, so a heading that triples in length will break it.

Reply ONLY with valid JSON matching the schema."""


class TranslatedStrings(BaseModel):
    """What the model returns: `strings`, translated.

    Asked for as an object keyed by the same numbers as `strings` — the number
    pins each translation to its string, so a reply that skips some is still
    right about the rest (`numbers` records which it answered). A bare
    numbered object without the wrapper is the same answer. A plain list is
    accepted too, but only a COMPLETE one can be trusted: a short list could
    have slipped by one anywhere.

    `translations` is required: a reply in some other shape must fail
    validation, so the LLM layer's repair turn runs, rather than pass as an
    answer that translated nothing."""

    translations: list[str]
    numbers: SkipJsonSchema[list[int] | None] = None

    @model_validator(mode="before")
    @classmethod
    def _numbered(cls, data: object) -> object:
        if isinstance(data, dict) and "translations" not in data and data \
                and all(str(key).strip().isdigit() for key in data):
            data = {"translations": data}
        if isinstance(data, dict) and isinstance(data.get("translations"), dict):
            try:
                numbered = {int(key): str(value) for key, value in data["translations"].items()}
            except (TypeError, ValueError):
                return data  # not numbered: let validation say so
            order = sorted(numbered)
            return {**data, "translations": [numbered[n] for n in order], "numbers": order}
        return data

    def answers(self, count: int) -> dict[int, str]:
        """Index → translation for each of `count` strings this reply answers."""
        if self.numbers is not None:
            return {n - 1: text for n, text in zip(self.numbers, self.translations) if 1 <= n <= count}
        return dict(enumerate(self.translations)) if len(self.translations) == count else {}


# --- strings in, strings out -----------------------------------------------------------


def _prose_strings(value: Any, out: dict[str, None]) -> None:
    """Every distinct non-empty allowlisted string in a block tree, in order."""
    if isinstance(value, list):
        for item in value:
            _prose_strings(item, out)
        return
    if not isinstance(value, BaseModel):
        return
    for name in type(value).model_fields:
        child = getattr(value, name, None)
        if isinstance(child, (list, BaseModel)):
            _prose_strings(child, out)
        elif name in _TRANSLATABLE_FIELDS and isinstance(child, str) and child.strip():
            out.setdefault(child.strip(), None)


def page_strings(page: PagePlan) -> list[str]:
    """The page's distinct prose strings — what one translation call covers.

    The page title and SEO texts ride along: they are the page's words too,
    and in another language they are what a search result shows."""
    found: dict[str, None] = {}
    for text in (page.title, page.seo_title, page.seo_description):
        if text and text.strip():
            found.setdefault(text.strip(), None)
    _prose_strings(page.blocks, found)
    return list(found)


def _apply_map(value: Any, mapping: dict[str, str]) -> None:
    """Swap every allowlisted string `mapping` translates, in place."""
    if isinstance(value, list):
        for item in value:
            _apply_map(item, mapping)
        return
    if not isinstance(value, BaseModel):
        return
    for name in type(value).model_fields:
        child = getattr(value, name, None)
        if isinstance(child, (list, BaseModel)):
            _apply_map(child, mapping)
        elif name in _TRANSLATABLE_FIELDS and isinstance(child, str):
            translated = mapping.get(child.strip())
            if translated:
                setattr(value, name, translated)


def _source_excerpt(source: SourceContent | None) -> str:
    if source is None:
        return ""
    parts = [source.title or "", *source.headings, source.raw_text or ""]
    return "\n".join(p for p in parts if p).strip()[:_MAX_SOURCE_CHARS]


async def translate_strings(
    strings: list[str],
    locale: str,
    source: SourceContent | None,
    *,
    client: LlmClient,
    label: str = "",
    keep_unchanged: bool = False,
) -> dict[str, str] | None:
    """`strings` → their translations in `locale`, as far as the model answered;
    None when nothing came back at all. `keep_unchanged` keeps a string the
    model answered with itself, for a caller that must tell "answered: stays
    as is" from "not answered".

    Every numbered answer is kept. What a reply skipped is asked for again
    once, on its own and past the response cache (which still holds the short
    reply), and whatever is still missing stays in the primary language for
    the caller to report. A string the model returned unchanged (a name, a
    brand) is left out of the map: it is not a translation, and the page
    already shows it."""
    if not strings:
        return {}
    excerpt = _source_excerpt(source)
    language = f"{locale_label(locale)} (code: {locale})"

    def prompt(subset: list[str]) -> str:
        return json.dumps(
            {
                "target_language": language,
                "strings": {str(i + 1): text for i, text in enumerate(subset)},
                "source_text": excerpt or "(none — translate the strings)",
            },
            ensure_ascii=False,
        )

    got: dict[int, str] = {}
    try:
        result = await chat_json_cached(
            client, system_prompt=TRANSLATION_PROMPT, user_prompt=prompt(strings),
            schema=TranslatedStrings,
        )
        got = result.answers(len(strings))
    except (LlmError, asyncio.TimeoutError) as exc:
        logger.warning("Translation of %s into %s failed (%s)", label, locale, exc)
    gaps = [i for i in range(len(strings)) if i not in got]
    if gaps:
        logger.warning("Translation of %s into %s answered %d of %d strings — asking again for the rest",
                       label, locale, len(strings) - len(gaps), len(strings))
        try:
            again = await client.chat_json(
                system_prompt=TRANSLATION_PROMPT, user_prompt=prompt([strings[i] for i in gaps]),
                schema=TranslatedStrings,
            )
            got.update({gaps[j]: text for j, text in again.answers(len(gaps)).items()})
        except (LlmError, asyncio.TimeoutError) as exc:
            logger.warning("Translation of %s into %s failed again (%s)", label, locale, exc)
        if left := len(strings) - len(got):
            logger.warning("Translation of %s into %s left %d of %d strings in the primary language",
                           label, locale, left, len(strings))
    if not got:
        return None
    return {
        strings[i]: new.strip()
        for i, new in got.items()
        if new and new.strip() and (keep_unchanged or new.strip() != strings[i])
    }


def is_untranslatable(text: str) -> bool:
    """A string that is only a URL, an email, a phone number or a number —
    the prompt's "returned unchanged" list, decided without asking."""
    from app.services.contact_text import EMAIL_RE, PHONE_RE

    value = text.strip()
    return bool(
        re.fullmatch(r"(?:https?://|www\.)\S+", value)
        or EMAIL_RE.fullmatch(value)
        or PHONE_RE.fullmatch(value)
        or re.fullmatch(r"[\d\s.,:%+\-/()]+", value)
    )


# --- separate language pages ------------------------------------------------------------


def url_prefix_of(slug: str, translation_of: str | None) -> str:
    """The language directory a translated page lives under, from its own slug.

    ``bm/committee`` translating ``committee`` → ``bm``. Read off the slug, not
    the locale: the locale is BCP 47 (``ms``) while the URL keeps whatever
    spelling the site uses.
    """
    slug = slug.strip("/")
    if translation_of:
        return slug[: -len(translation_of)].strip("/") if slug.endswith(translation_of) else slug.split("/", 1)[0]
    return slug


def localize_href(href: str, prefix: str, translated_slugs: set[str]) -> str:
    """Point an internal link at the same page in this language, when it exists.

    A translated page linking to ``/committee`` should reach ``/bm/committee``
    — but only if that page was built in THIS language. Otherwise the reader is
    better off on the primary-language page than on a 404.
    """
    if not prefix or not href.startswith("/") or href.startswith("//"):
        return href
    path, _, fragment = href.partition("#")
    slug = path.strip("/")
    if slug.startswith(f"{prefix}/") or slug == prefix:
        return href  # already localized
    if slug not in translated_slugs:
        return href
    localized = f"/{prefix}/{slug}".rstrip("/") if slug else f"/{prefix}"
    return f"{localized}#{fragment}" if fragment else localized


def _localize_block_hrefs(
    value: Any, prefix: str, translated_slugs: set[str]
) -> None:
    """Walk a block tree rewriting internal hrefs to their localized twin."""
    if isinstance(value, list):
        for item in value:
            _localize_block_hrefs(item, prefix, translated_slugs)
        return
    if not isinstance(value, BaseModel):
        return
    for name in type(value).model_fields:
        child = getattr(value, name, None)
        if isinstance(child, str) and name.endswith("href"):
            setattr(value, name, localize_href(child, prefix, translated_slugs))
        elif isinstance(child, (list, BaseModel)):
            _localize_block_hrefs(child, prefix, translated_slugs)


def _clone_plan(canonical: PagePlan, scaffold: PageScaffold) -> PagePlan:
    """A deep copy of the counterpart carrying the translation's identity."""
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
    clone.inline_translation = None
    return clone


async def _translate_one(
    clone: PagePlan,
    canonical: PagePlan,
    source: SourceContent | None,
    *,
    client: LlmClient,
    owned: dict[str, str] | None = None,
) -> PagePlan:
    """Fill one clone's prose. Returns it in the primary language on any failure.

    `owned` is the owner's own wording in this language (their bilingual
    pairs): used as is, never sent to the model."""
    locale = clone.locale or ""
    owned = owned or {}
    # Q3's badge: the owner's own page in this language, or ours translated.
    clone.translation_status = "source" if _source_excerpt(source) else "ai"
    asked = [text for text in page_strings(canonical) if text not in owned]
    mapping = {
        **(await translate_strings(asked, locale, source, client=client, label=f"/{clone.slug}") or {}),
        **owned,  # the owner's own words win
    }
    if not mapping:
        return clone
    # Kept: plan_to_site builds the page from its counterpart's finished tree
    # with this map, so it differs from that page in words only.
    clone.text_map = mapping
    _apply_map(clone.blocks, mapping)
    if title := mapping.get(canonical.title.strip()):
        clone.title = title
    # Direct assignment skips PagePlan's validators, so the SERP budgets are
    # applied here too — a translation is as likely to overrun them as the
    # canonical page was, and often likelier (German, Malay).
    if seo_title := mapping.get(canonical.seo_title.strip()):
        clone.seo_title = clamp_seo_title(seo_title)
    if seo_description := mapping.get(canonical.seo_description.strip()):
        clone.seo_description = clamp_seo_description(seo_description)
    return clone


async def build_translated_pages(
    pages: list[PagePlan],
    scaffolds: list[PageScaffold],
    sources: dict[str, SourceContent],
    *,
    primary: str | None = None,
    client: LlmClient | None = None,
) -> list[PagePlan]:
    """Clone each translation scaffold from the page it translates.

    ``pages`` are the finished primary-language PagePlans (images already
    bound, so the clones inherit the exact same photos). ``sources`` maps a
    translated page's slug to its crawled SourceContent, supplying the owner's
    own wording.

    A string the owner wrote in both languages ("Character & Behaviour
    Development | 品格与行为培养") is split first, given the ``primary``
    language: the primary page keeps its side, the translation takes the
    other word for word, and no model is asked for what the owner wrote.

    Scaffolds whose counterpart wasn't generated are skipped — there is nothing
    to clone.
    """
    wanted = [s for s in scaffolds if s.locale and s.translation_of is not None]
    if not wanted:
        return []

    by_slug = {p.slug: p for p in pages}
    owned: dict[tuple[str, str], dict[str, str]] = {}
    if primary:
        for s in wanted:
            if (canonical := by_slug.get(s.translation_of or "")) is not None:
                split_owner_pairs(canonical.blocks, primary, s.locale or "",
                                  owned.setdefault((s.locale or "", canonical.slug), {}))
    # Per language: a Chinese page links to a page's Chinese twin, never to a
    # Malay one that happens to exist.
    translated_slugs: dict[str, set[str]] = {}
    for s in wanted:
        if s.translation_of in by_slug:
            translated_slugs.setdefault(s.locale or "", set()).add(s.translation_of)

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
            _translate_one(clone, canonical, source, client=llm,
                           owned=owned.get((clone.locale or "", canonical.slug), {}))
            for clone, canonical, source in clones
        ),
        return_exceptions=True,
    )

    out: list[PagePlan] = []
    for (clone, _canonical, _source), result in zip(clones, results):
        if isinstance(result, BaseException):
            logger.warning(
                "Translation of /%s crashed (%s) — keeping the primary language",
                clone.slug, result,
            )
            page = clone
        else:
            page = result
        # Last, on the final blocks: a reader who followed a Malay link should
        # land on the Malay version of the next page where one exists.
        _localize_block_hrefs(
            page.blocks,
            url_prefix_of(page.slug, page.translation_of),
            translated_slugs.get(page.locale or "", set()),
        )
        out.append(page)
    logger.info("Built %d translated page(s) by cloning", len(out))
    return out


# --- same-page bilingual -------------------------------------------------------------


def split_owner_pairs(value: Any, primary: str, second: str, found: dict[str, str]) -> None:
    """Split a string the owner wrote in both languages, in place.

    The planner is told to write in one language, but it does copy a source
    heading like "Character & Behaviour Development | 品格与行为培养" verbatim.
    On a same-page bilingual site that IS the owner's pair: the primary side
    stays in the field, the other becomes its second line word for word
    (`found`), and no model is asked to translate what the owner wrote.
    """
    from app.services.language_analysis import split_bilingual

    if isinstance(value, list):
        for item in value:
            split_owner_pairs(item, primary, second, found)
        return
    if not isinstance(value, BaseModel):
        return
    for name in type(value).model_fields:
        child = getattr(value, name, None)
        if isinstance(child, (list, BaseModel)):
            split_owner_pairs(child, primary, second, found)
        elif name in _TRANSLATABLE_FIELDS and isinstance(child, str):
            pair = split_bilingual(child, primary, second)
            if pair is not None:
                setattr(value, name, pair[0])
                found.setdefault(pair[0], pair[1])


async def add_inline_translations(
    pages: list[PagePlan],
    locale: str,
    sources: dict[str, SourceContent],
    *,
    primary: str,
    client: LlmClient | None = None,
) -> None:
    """Same-page bilingual (mode B): give each page its strings in `locale`.

    First the owner's own pairs (`split_owner_pairs`), verbatim. Then, for the
    strings left, the same translation a separate-language page gets — the
    owner's wording from `sources[page.slug]` (the page itself, which carries
    both languages) where it covers the string, ours translated where it is
    silent. The map stays on the primary page as `PagePlan.inline_translation`;
    `inline_translation.apply_inline_translation` adds the lines once the page
    is built. A failed call keeps whatever the owner's pairs already gave.
    """
    targets = [p for p in pages if not p.locale]
    if not targets:
        return
    owned: list[dict[str, str]] = []
    for page in targets:
        found: dict[str, str] = {}
        split_owner_pairs(page.blocks, primary, locale, found)
        owned.append(found)
    llm = client or get_llm()
    maps = await asyncio.gather(
        *(
            translate_strings(
                [text for text in page_strings(page) if text not in found],
                locale, sources.get(page.slug), client=llm, label=f"/{page.slug}",
            )
            for page, found in zip(targets, owned)
        ),
        return_exceptions=True,
    )
    for page, found, mapping in zip(targets, owned, maps):
        if isinstance(mapping, BaseException):
            logger.warning("Inline translation of /%s crashed (%s)", page.slug, mapping)
            mapping = None
        text = {**(mapping or {}), **found}  # the owner's own words win
        if text:
            page.inline_translation = InlineTranslation(lang=locale, text=text)
    logger.info("Inline %s strings added to %d page(s)", locale, sum(
        1 for page in targets if page.inline_translation
    ))


# --- separate language pages: the finished site ----------------------------------------


def localized_body(
    base: list, overlay: dict | None, slug: str, translation_of: str | None, translated_slugs: set[str]
) -> list:
    """A translated page's body: its counterpart's tree with the overlay applied
    and internal links following the reader into this language where the
    target page exists — what the CMS will serve for this URL."""
    from app.services import locale_overlay

    prefix = url_prefix_of(slug, translation_of)
    return locale_overlay.apply(base, overlay, lambda href: localize_href(href, prefix, translated_slugs))


def localized_layout(site, locale: str):
    """The menus, header and footer the CMS will serve on this language's pages:
    the layout overlay on the shared header/footer, every menu label in the
    language, and internal links following the reader into it where the page
    exists. The preview draws a translated page with it, and the push's menu
    overlay is the same `menu_overlay` call, so the two can't disagree."""
    from app.services import locale_overlay
    from app.services.menu_builder import build_layout_payload

    plan = site.multilingual
    translated = [p for p in site.pages if p.locale == locale and p.translation_of is not None]
    prefix = (plan.prefixes.get(locale) if plan else None) or (
        url_prefix_of(translated[0].slug, translated[0].translation_of) if translated else ""
    )
    slugs = {p.translation_of for p in translated}

    def localize(href: str) -> str:
        return localize_href(href, prefix, slugs)

    overlay = site.layout_overlays.get(locale) or {}
    chrome = site.model_copy(update={
        "header_schema": _applied(site.header_schema, overlay, localize),
        "footer_schema": _applied(site.footer_schema, overlay, localize),
    })
    payload = build_layout_payload(chrome)
    menus = locale_overlay.menu_overlay(
        payload.menus, {p.translation_of: p.title for p in translated},
        site.menu_label_translations.get(locale) or {},
    )
    return payload._replace(menus=locale_overlay.apply_menus(payload.menus, {"menus": menus}, localize))


def _applied(element, overlay: dict, localize):
    from app.services import locale_overlay

    if element is None:
        return None
    applied = locale_overlay.apply([element], overlay, localize)
    return applied[0] if applied else element


def menu_labels(site) -> list[str]:
    """Every label the site's menus show, minus the language menu (each item
    already in its own language) and social links (platform names)."""
    from app.services.menu_builder import build_layout_payload

    found: dict[str, None] = {}

    def visit(items: list[dict]) -> None:
        for item in items or []:
            if isinstance(item.get("label"), str) and item["label"].strip():
                found.setdefault(item["label"].strip(), None)
            visit(item.get("children") or [])

    for menu in build_layout_payload(site).menus:
        if menu.get("purpose") not in ("language", "social"):
            visit(menu.get("items") or [])
    return list(found)


def _translated_slugs(site) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for page in site.pages:
        if page.locale and page.translation_of is not None:
            out.setdefault(page.locale, set()).add(page.translation_of)
    return out


async def _complete_overlay(
    elements: list, overlay: dict, locale: str, source: SourceContent | None,
    *, client: LlmClient, label: str, origin: str,
) -> bool:
    """Translate what `overlay` still leaves in the primary language, in place.

    A string the model returns unchanged (a name, a brand, "UNIQUE KINDIES") IS
    its translation and is recorded as covered, so the CMS's completeness test
    agrees the page is finished; so is a string that is only a contact detail
    or a number, without asking. A string the model did NOT answer stays a
    gap, for the owner and the push's report. Returns whether anything is
    still missing."""
    from app.services import locale_overlay

    gaps = locale_overlay.missing(elements, overlay)
    if not gaps:
        return False
    strings: dict[str, None] = {}
    for element in gaps:
        for field in locale_overlay.TEXT_FIELDS:
            value = getattr(element.content, field, None)
            if locale_overlay.needs_translation(value) and not is_untranslatable(value):
                strings.setdefault(value.strip(), None)
    mapping = await translate_strings(
        list(strings), locale, source, client=client, label=label, keep_unchanged=True,
    ) or {}
    for element in gaps:
        patch: dict[str, str] = {}
        for field in locale_overlay.TEXT_FIELDS:
            value = getattr(element.content, field, None)
            if not locale_overlay.needs_translation(value):
                continue  # a file's name is in no language
            if is_untranslatable(value):
                patch[field] = value.strip()
            elif (translated := mapping.get(value.strip())) is not None:
                patch[field] = translated
        if not patch:
            continue
        overlay.setdefault("elements", {})[element.id] = {**overlay.get("elements", {}).get(element.id, {}), **patch}
        if (meta := locale_overlay.source_meta(element, origin)) is not None:
            overlay.setdefault("meta", {})[element.id] = meta
    return bool(locale_overlay.missing(elements, overlay))


async def complete_translations(
    site, sources: dict[str, SourceContent], *, client: LlmClient | None = None
) -> list[str]:
    """Finish every translated page, and the header and footer per language.

    `plan_to_site` derives each translation from its page's string map, which
    only knows the strings the PLANNER wrote; a section label or a footer line
    is text the page shows that no map covered. One call per page (and one per
    language for the layout) translates exactly those, so a page is complete
    enough for the CMS to publish it. Returns the slugs (and "layout:<locale>") still incomplete,
    for the generation warnings.
    """
    from app.services import locale_overlay

    translated = [p for p in site.pages if p.locale and p.overlay is not None]
    if not translated:
        return []
    llm = client or get_llm()
    by_slug = {p.slug: p for p in site.pages if not p.locale}
    slugs = _translated_slugs(site)

    async def page_job(page) -> str | None:
        base = by_slug.get(page.translation_of or "")
        if base is None:
            return None
        incomplete = await _complete_overlay(
            base.body_schema.elements, page.overlay, page.locale, sources.get(page.slug),
            client=llm, label=f"/{page.slug}", origin=page.translation_status or "ai",
        )
        page.body_schema.elements = localized_body(
            base.body_schema.elements, page.overlay, page.slug, page.translation_of,
            slugs.get(page.locale, set()),
        )
        return page.slug if incomplete else None

    async def layout_job(locale: str) -> str | None:
        chrome = [c for c in (site.header_schema, site.footer_schema) if c is not None]
        # What the pages already say, first: a header CTA reading "Enrol now"
        # is the same words wherever they appear.
        known: dict[str, str] = {}
        for page in translated:
            if page.locale == locale and page.translation_of in by_slug:
                known.update(locale_overlay.as_text_map(
                    by_slug[page.translation_of].body_schema.elements, page.overlay
                ))
        overlay = locale_overlay.derive(chrome, known, origin="ai")
        incomplete = await _complete_overlay(
            chrome, overlay, locale, None, client=llm, label=f"layout:{locale}", origin="ai",
        )
        site.layout_overlays[locale] = overlay
        # Menu labels by text: the items' ids are minted again by every layout
        # build, so the push and the preview key them at the time (menu_overlay).
        # A failed call leaves them in the primary language — the CMS's
        # completeness test doesn't count menus, so nothing waits on them.
        labels = menu_labels(site) if len(chrome) == 2 else []
        rest = [label for label in labels if label not in known]
        fresh = await translate_strings(rest, locale, None, client=llm, label=f"menus:{locale}") if rest else {}
        site.menu_label_translations[locale] = {
            **{label: known[label] for label in labels if label in known}, **(fresh or {}),
        }
        return f"layout:{locale}" if incomplete else None

    locales = sorted({p.locale for p in translated})
    results = await asyncio.gather(
        *(page_job(p) for p in translated), *(layout_job(code) for code in locales),
        return_exceptions=True,
    )
    left = []
    for result in results:
        if isinstance(result, BaseException):
            logger.warning("Completing a translation crashed (%s)", result)
        elif result:
            left.append(result)
    logger.info("Translations completed: %d page(s), %d language layout(s); %d still incomplete",
                len(translated), len(locales), len(left))
    return left
