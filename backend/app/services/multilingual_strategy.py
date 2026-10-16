"""How a site presents its languages — one answer, asked everywhere.

docs/multilingual-plan.md §2. `decide` turns the source's `LanguageProfile`
(what the source IS) and the user's `MultilingualChoice` (what they asked for)
into a `MultilingualPlan`: one website in one of three modes.

- `single` — one language, the one the source is written in (never "English
  by default": a Chinese-only source is a Chinese site).
- `bilingual` — the source's own presentation kept: both languages on the same
  page, the second as a lang-tagged line under each string
  (`inline_translation`).
- `separate` — each language at its own URLs (`/ms/about`), every page in
  every language, cloned from the primary page (`translations`).

Auto rules, in order (each states its evidence in `reason`):

1. Translated mirrors at their own URLs → separate (Rule 2).
2. Two substantial languages on the same pages, paired or by section →
   bilingual (Rules 1 and 4: keep what the owner chose).
3. Anything else → single in the primary (Rule 3: incidental text adds
   nothing).

The user's mode always wins (Rule 5), and their added languages join the
detected ones (Q4). Thin evidence is surfaced, never acted on silently
(Rule 6): `uncertain` marks it, and the preview says so. Pure — no I/O.
"""

from __future__ import annotations

from app.models.content_blocks import (
    LanguageProfile,
    MultilingualChoice,
    MultilingualPlan,
    SourceContent,
)
from app.services.locale import (
    LOCALES,
    canonical_locale,
    locale_label,
    locale_segment,
    url_prefix,
)

# A secondary language read with less confidence than this is reported as
# uncertain. Script-identified languages read 1.0; py3langid's Malay on
# mmta's /bm read 0.91 and a single misread Title-Case label 0.4.
MIN_CONFIDENCE = 0.6

_RECOMMEND = {
    "parallel_urls": "separate",
    "inline_bilingual": "bilingual",
    "mixed_sections": "bilingual",
}


def _same(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return a == b or (a.startswith("zh") and b.startswith("zh"))


def _label(code: str) -> str:
    return locale_label(code)


def _source_prefixes(profile: LanguageProfile) -> dict[str, str]:
    """code → the directory the source itself used for that language (`ms` → `bm`).

    Kept so an existing site's indexed URLs survive (plan §4)."""
    found: dict[str, str] = {}
    for page in profile.pages:
        if page.url_locale and (segment := locale_segment(page.path)):
            found.setdefault(page.url_locale, segment)
    return found


def decide(
    profile: LanguageProfile | None, choice: MultilingualChoice | None = None
) -> MultilingualPlan:
    choice = choice or MultilingualChoice()
    profile = profile or LanguageProfile()
    primary = canonical_locale(profile.primary) or "en"

    detected = [
        share for share in profile.languages
        if share.substantial and not _same(share.code, primary)
    ]
    secondary = [share.code for share in detected]
    added: list[str] = []
    for raw in choice.locales:
        code = canonical_locale(raw)
        if code and not _same(code, primary) and not any(_same(code, c) for c in secondary + added):
            added.append(code)

    recommended = _RECOMMEND.get(profile.pattern, "single") if secondary else "single"
    confidence = min((share.confidence for share in detected), default=1.0)
    uncertain = recommended != "single" and confidence < MIN_CONFIDENCE

    if choice.mode == "auto":
        mode = recommended
        # An added language needs somewhere to go: a single-language source
        # with one asked for becomes separate pages (the SEO-preferred shape).
        if mode == "single" and added:
            mode = "separate"
        chosen_by = "auto"
    else:
        mode = choice.mode
        chosen_by = "override"

    languages = [*secondary, *added] if mode != "single" else []
    if mode != "single" and not languages:
        return MultilingualPlan(
            mode="single", primary=primary, chosen_by=chosen_by, confidence=confidence,
            reason=(
                f"{_label(primary)} only: the source has no second language. "
                "Add one to build it in two."
            ),
        )

    dropped: list[str] = []
    if mode == "bilingual" and len(languages) > 1:
        # Two languages share a page; a third would make it a wall of text.
        languages, dropped = languages[:1], languages[1:]

    prefixes: dict[str, str] = {}
    if mode == "separate":
        own = _source_prefixes(profile)
        everything = [primary, *languages]
        prefixes = {code: own.get(code) or url_prefix(code, everything) for code in languages}

    return MultilingualPlan(
        mode=mode,  # type: ignore[arg-type]
        primary=primary,
        secondary=languages,
        prefixes=prefixes,
        reason=_reason(mode, primary, languages, added, dropped, profile, chosen_by, uncertain),
        confidence=round(confidence, 2),
        chosen_by=chosen_by,  # type: ignore[arg-type]
        uncertain=uncertain,
    )


def _reason(
    mode: str, primary: str, languages: list[str], added: list[str], dropped: list[str],
    profile: LanguageProfile, chosen_by: str, uncertain: bool,
) -> str:
    names = ", ".join(_label(code) for code in languages)
    if mode == "single":
        why = {
            "incidental": " A little text in another language isn't enough for a second version.",
        }.get(profile.pattern, "")
        text = f"{_label(primary)} only.{why}"
    elif mode == "separate":
        text = (
            f"Separate pages per language: {_label(primary)} at the root, {names} "
            "under their own addresses, so each can be found in search in its own language."
        )
    else:
        text = (
            f"{_label(primary)} and {names} on the same pages, the way the source "
            "presents them."
        )
    if added:
        text += f" {', '.join(_label(c) for c in added)} added at your request — translated from the {_label(primary)} copy."
    if dropped:
        text += f" {', '.join(_label(c) for c in dropped)} left out: one page shows two languages at most."
    if chosen_by == "override":
        text += " (Your choice.)"
    if uncertain:
        text += " The second language was hard to read with confidence — check the preview."
    return text


def with_plan(source: SourceContent, choice: MultilingualChoice | None) -> SourceContent:
    """`source` carrying its decided plan — the one answer every later pass reads.

    The profile rides on the source from the preview; a source from before P1
    (or built outside the readers) is analysed here, once.
    """
    profile = source.language_profile
    if profile is None:
        from app.services.language_analysis import with_language_profile

        profile = with_language_profile(source).language_profile
    return source.model_copy(
        update={"language_profile": profile, "multilingual": decide(profile, choice)}
    )


def addable_locales() -> list[dict[str, str]]:
    """Every language a user may add, for the picker. Plain `zh` is left out:
    it means "Chinese, script unknown", which is a reading, never a choice."""
    return [
        {"code": code, "label": locale.label}
        for code, locale in LOCALES.items() if code != "zh"
    ]
