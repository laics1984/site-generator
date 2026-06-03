"""
Infer the page tree of a generated site from a crawled SourceContent.

Inputs:  the primary SourceContent (with discovered_pages from the crawler) +
         the detected industry, so we can fall back to the industry template
         when the crawl is empty (small / no-crawl sources).

Output:  a flat list of PageScaffolds carrying parent_slug, ready for the
         page picker (which renders them as a tree) and the generator.

The inference is pure: URL paths + page titles → page_type + parent_slug.
No LLM is involved. The LLM still writes copy; this module only decides
which pages exist and how they nest.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlparse

from app.models.content_blocks import PageType, SectionType, SourceContent
from app.models.industry import IndustryCategory, PageScaffold
from app.services.industry_templates import get_template

logger = logging.getLogger(__name__)


# --- page_type heuristics -------------------------------------------------------


# Each entry: (page_type, slug-substring-tokens). First match wins.
_TYPE_HINTS: list[tuple[PageType, tuple[str, ...]]] = [
    ("contact", ("contact", "get-in-touch", "reach-us")),
    ("pricing", ("pricing", "plans", "subscriptions")),
    ("team", ("team", "people", "leadership", "staff", "board")),
    ("blog", ("blog", "news", "insights", "articles", "press", "media-centre", "newsroom")),
    ("faq", ("faq", "faqs", "help", "support-faq", "questions")),
    ("work", ("work", "portfolio", "case-studies", "projects", "clients", "case-study")),
    ("menu", ("menu", "food-menu", "drinks-menu")),
    ("gallery", ("gallery", "photos", "images", "galleries")),
    ("testimonials", ("testimonials", "reviews", "stories")),
    ("process", ("process", "how-we-work", "approach", "methodology")),
    ("services", ("services", "what-we-do", "solutions", "products", "offerings", "expertise")),
    ("about", ("about", "company", "who-we-are", "our-story", "story")),
    ("privacy", ("privacy", "privacy-policy", "data-protection")),
    ("terms", ("terms", "terms-of-service", "tos", "legal", "conditions")),
    ("thank-you", ("thank-you", "thanks", "success")),
]


def _slug_tokens(slug: str) -> set[str]:
    """Return the slug split on / and - into matchable tokens."""
    return {
        tok.strip().lower()
        for part in slug.split("/")
        for tok in re.split(r"[-_]", part)
        if tok.strip()
    }


def _infer_page_type(slug: str, title: str = "") -> PageType:
    """Best-effort match of a URL slug + title to one of the PageType literals.

    Falls back to ``"services"`` for unknown top-level pages and ``"landing"``
    for unknown sub-pages (which signals a detail page in the scaffold layer).
    """
    if not slug:
        return "home"
    haystack = f"{slug} {title}".lower()
    for page_type, hints in _TYPE_HINTS:
        for hint in hints:
            # Match against full token, slash-delimited segment, or substring
            if hint == slug.split("/", 1)[0]:
                return page_type
            if hint in haystack:
                return page_type
    # No match — depth decides between landing (sub) and services (top).
    return "landing" if "/" in slug else "services"


# --- sections per inferred page_type --------------------------------------------


# Section rhythms for inferred top-level pages. Mirrors industry_templates.py
# but lives here so the inference module is self-contained.
_TOP_SECTIONS: dict[PageType, list[SectionType]] = {
    "home":         ["hero", "features", "testimonials", "cta"],
    "about":        ["hero", "about", "team", "cta"],
    "contact":      ["hero", "contact", "faq"],
    "services":     ["hero", "services", "process", "testimonials", "cta"],
    "pricing":      ["hero", "pricing", "features", "faq", "cta"],
    "team":         ["hero", "team", "cta"],
    "work":         ["hero", "gallery", "testimonials", "cta"],
    "blog":         ["hero", "features"],
    "faq":          ["hero", "faq", "cta"],
    "menu":         ["hero", "menu", "gallery", "cta"],
    "gallery":      ["hero", "gallery", "cta"],
    "testimonials": ["hero", "testimonials", "cta"],
    "process":      ["hero", "process", "faq", "cta"],
    "thank-you":    ["hero", "cta"],
    "landing":      ["hero", "features", "process", "testimonials", "cta"],
    # privacy / terms are handled by legal_pages.py — no LLM sections needed.
    "privacy":      [],
    "terms":        [],
}

# Sub-pages get a tighter, detail-focused rhythm. Reused across page_types.
_SUBPAGE_SECTIONS: list[SectionType] = ["hero", "features", "process", "testimonials", "cta"]
# Sub-pages of work/case-studies show the project visually, not as a services list.
_WORK_SUBPAGE_SECTIONS: list[SectionType] = ["hero", "about", "gallery", "testimonials", "cta"]
# Sub-pages of team show a single person's bio + linked services/work.
_TEAM_SUBPAGE_SECTIONS: list[SectionType] = ["hero", "about", "testimonials", "cta"]


def _sections_for(page_type: PageType, parent_type: PageType | None) -> list[SectionType]:
    if parent_type is None:
        return list(_TOP_SECTIONS.get(page_type, _TOP_SECTIONS["services"]))
    if parent_type == "work":
        return list(_WORK_SUBPAGE_SECTIONS)
    if parent_type == "team":
        return list(_TEAM_SUBPAGE_SECTIONS)
    return list(_SUBPAGE_SECTIONS)


# --- URL → slug helpers ---------------------------------------------------------


def _path_to_slug(url_path: str | None) -> str:
    """Normalize ``"/services/web-design/"`` → ``"services/web-design"``.

    The empty string ⇒ homepage.
    """
    if not url_path or url_path == "/":
        return ""
    return url_path.strip("/").lower()


def _humanize(slug_part: str) -> str:
    """``"web-design"`` → ``"Web Design"``."""
    return " ".join(w.capitalize() for w in re.split(r"[-_]", slug_part) if w)


def _title_from_page(page: SourceContent, fallback_slug: str) -> str:
    """Prefer the source's own title, strip site-name suffix, fall back to slug."""
    raw = (page.title or "").strip()
    if raw:
        # Strip common " | Brand" / " — Brand" / " - Brand" suffixes
        for sep in (" | ", " — ", " – ", " - "):
            if sep in raw:
                raw = raw.split(sep, 1)[0].strip()
                break
        if raw:
            return raw
    if not fallback_slug:
        return "Home"
    last = fallback_slug.rsplit("/", 1)[-1]
    return _humanize(last)


# --- main entry -----------------------------------------------------------------


_CORE_SLUGS = {"", "about", "contact"}


def infer_page_scaffolds(
    source: SourceContent,
    *,
    industry: IndustryCategory,
    site_name: str | None = None,
) -> list[PageScaffold]:
    """Build the inferred scaffold tree.

    Empty or near-empty crawl → industry-template fallback (small sites still
    get the standard core + suggested pages).

    Non-empty crawl → derive scaffolds from the discovered URL structure,
    merging in core pages the source doesn't expose (about/contact often live
    in the footer; we add them so the generated site is complete).
    """
    template = get_template(industry)

    if not source.discovered_pages:
        # Thin site or crawl disabled: return the industry template's
        # core + suggested set verbatim. The recipe endpoint will surface
        # optional_pages separately.
        logger.info(
            "No discovered pages — falling back to '%s' industry template", industry
        )
        return [*template.core_pages, *template.suggested_pages]

    # Build a map of slug → SourceContent for every discovered page (and the
    # primary, which represents the homepage).
    by_slug: dict[str, SourceContent] = {"": source}
    for page in source.discovered_pages:
        slug = _path_to_slug(page.url_path)
        if slug and slug not in by_slug:
            by_slug[slug] = page

    scaffolds: list[PageScaffold] = []
    seen_slugs: set[str] = set()

    # 1. Always start with home
    scaffolds.append(_home_scaffold(by_slug.get("", source)))
    seen_slugs.add("")

    # 2. Walk slugs in path-depth order so parents always exist before children
    sorted_slugs = sorted(
        (s for s in by_slug if s),
        key=lambda s: (s.count("/"), s),
    )

    for slug in sorted_slugs:
        if slug in seen_slugs:
            continue
        page = by_slug[slug]
        segments = slug.split("/")
        title = _title_from_page(page, slug)

        if len(segments) == 1:
            # Top-level page
            page_type = _infer_page_type(slug, title)
            if page_type in ("privacy", "terms"):
                # Legal — we generate from boilerplate, not the crawl.
                continue
            scaffolds.append(
                PageScaffold(
                    page_type=page_type,
                    slug=slug,
                    title=title,
                    sections=_sections_for(page_type, parent_type=None),
                    rationale=f"Discovered at /{slug} in the source site.",
                    source_url=page.source_ref,
                )
            )
            seen_slugs.add(slug)
        else:
            # Sub-page: ensure its parent scaffold exists
            parent_slug = "/".join(segments[:-1])
            if parent_slug not in seen_slugs:
                # Parent wasn't in the crawl — synthesize it from the top segment.
                parent_title = _humanize(segments[0])
                parent_type = _infer_page_type(parent_slug, parent_title)
                if parent_type in ("privacy", "terms"):
                    continue
                scaffolds.append(
                    PageScaffold(
                        page_type=parent_type,
                        slug=parent_slug,
                        title=parent_title,
                        sections=_sections_for(parent_type, parent_type=None),
                        rationale=f"Parent section inferred from /{slug}.",
                    )
                )
                seen_slugs.add(parent_slug)

            parent_scaffold = next(
                (s for s in scaffolds if s.slug == parent_slug), None
            )
            parent_type = parent_scaffold.page_type if parent_scaffold else None
            sub_type = _infer_page_type(slug, title)
            # Sub-pages typically aren't another listing of the parent — coerce
            # services/x → landing detail unless title looks like a real category.
            if sub_type == parent_type:
                sub_type = "landing"
            scaffolds.append(
                PageScaffold(
                    page_type=sub_type,
                    slug=slug,
                    title=title,
                    sections=_sections_for(sub_type, parent_type=parent_type),
                    rationale=f"Sub-page of /{parent_slug} discovered in the source.",
                    parent_slug=parent_slug,
                    source_url=page.source_ref,
                )
            )
            seen_slugs.add(slug)

    # 3. Ensure About / Contact are present — they often live in the footer
    #    rather than the nav, so the crawler misses them on small sites.
    have_types = {s.page_type for s in scaffolds}
    for fallback in template.core_pages:
        if fallback.is_homepage:
            continue
        if fallback.is_legal:
            continue
        if fallback.page_type not in have_types and fallback.slug not in seen_slugs:
            scaffolds.append(fallback)
            seen_slugs.add(fallback.slug)
            have_types.add(fallback.page_type)

    # 4. Legal pages always come from the template (boilerplate).
    for legal in template.core_pages:
        if legal.is_legal and legal.slug not in seen_slugs:
            scaffolds.append(legal)
            seen_slugs.add(legal.slug)

    return scaffolds


def _home_scaffold(home_source: SourceContent | None) -> PageScaffold:
    """Standard homepage scaffold — reuses the crawl's title if it has one."""
    title = "Home"
    if home_source and home_source.title:
        title = "Home"  # always literal "Home" in the nav; real headline comes from the LLM
    return PageScaffold(
        page_type="home",
        slug="",
        title=title,
        is_homepage=True,
        sections=["hero", "features", "testimonials", "cta"],
        rationale="Always present — first impression, value proposition, CTA.",
    )


# --- optional pool for the picker -----------------------------------------------


def optional_pool_for(
    industry: IndustryCategory, inferred: list[PageScaffold]
) -> list[PageScaffold]:
    """Pages from the industry template the inference didn't already include.

    Surfaced as unchecked options in the picker so the user can opt into pages
    the source site didn't have.
    """
    template = get_template(industry)
    seen_types = {s.page_type for s in inferred}
    pool: list[PageScaffold] = []
    candidates = [*template.suggested_pages, *template.optional_pages]
    for c in candidates:
        if c.page_type in seen_types:
            continue
        if c.is_legal:
            continue
        pool.append(c)
    return pool
