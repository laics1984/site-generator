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

from app.models.content_blocks import NavLink, PageType, SectionType, SourceContent
from app.models.industry import IndustryCategory, PageScaffold
from app.services.industry_templates import get_template
from app.services.landing_patterns import homepage_sections
from app.services.nav_extraction import find_repeated_cluster_keys

logger = logging.getLogger(__name__)


# --- page_type heuristics -------------------------------------------------------


# Each entry: (page_type, slug-substring-tokens). First match wins.
_TYPE_HINTS: list[tuple[PageType, tuple[str, ...]]] = [
    ("contact", ("contact", "get-in-touch", "reach-us")),
    ("pricing", ("pricing", "plans", "subscriptions")),
    (
        "team",
        (
            "team",
            "people",
            "leadership",
            "staff",
            "board",
            "committee",
            "committees",
            "council",
            "governance",
            "trustees",
            "board-members",
        ),
    ),
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


def _without_section(sections: list[SectionType], section: SectionType) -> list[SectionType]:
    return [s for s in sections if s != section]


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


def _title_from_page(
    page: SourceContent, fallback_slug: str, *, site_name: str | None = None
) -> str:
    """Prefer the source's own title, strip brand prefix/suffix, fall back to slug."""
    raw = (page.title or "").strip()
    if raw:
        # Strip leading "Brand: " / "Brand | " / "Brand - " prefixes (e.g.
        # "Sass: Install Sass" → "Install Sass") when we know the brand name.
        if site_name:
            prefix = re.match(
                rf"^\s*{re.escape(site_name.strip())}\s*[:\|\-–—]\s+(.+)$",
                raw,
                flags=re.IGNORECASE,
            )
            if prefix and prefix.group(1).strip():
                raw = prefix.group(1).strip()
        # Strip common " | Brand" / " — Brand" / " - Brand" suffixes
        for sep in (" | ", " — ", " – ", " - "):
            if sep in raw:
                raw = raw.split(sep, 1)[0].strip()
                break
        raw = re.sub(r"\s*[\|\-–—:]+\s*$", "", raw).strip()
        if raw:
            return raw
    if not fallback_slug:
        return "Home"
    last = fallback_slug.rsplit("/", 1)[-1]
    return _humanize(last)


# --- source navigation evidence --------------------------------------------------


# Page types whose pages typically *list* a family of sub-pages. Used to pick
# the parent when a repeated in-body menu strip links a set of sibling pages.
_LISTING_TYPES: set[PageType] = {
    "services", "work", "team", "blog", "menu", "gallery", "pricing", "faq", "process",
}


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", label.lower()).strip("-")
    return slug or "section"


def _explicit_page_type(slug: str) -> PageType | None:
    """Like ``_infer_page_type`` but only when a hint actually matched.

    The inference fallback types every unknown top-level slug as "services",
    which would make every strip target look like a listing page — here we
    need to know the type was *evidenced*, not defaulted.
    """
    haystack = slug.lower()
    for page_type, hints in _TYPE_HINTS:
        for hint in hints:
            if hint == slug.split("/", 1)[0] or hint in haystack:
                return page_type
    return None


def _is_explicit_listing(slug: str) -> bool:
    return "/" not in slug and _explicit_page_type(slug) in _LISTING_TYPES


def _href_to_slug(href: str) -> str | None:
    """Nav href → page slug. ``"/"`` ⇒ ``""`` (home); pure anchors ⇒ None."""
    path = href.split("#", 1)[0]
    if "#" in href and path in ("", "/"):
        return None  # same-page anchor, not a page
    if not path or path == "/":
        return ""
    return path.strip("/").lower()


class _NavEvidence:
    """What the source site's own navigation tells us.

    ``rank``       — top-level slug → 0-based position in the header nav.
    ``parent_of``  — child slug → parent slug, from dropdown nesting.
    ``synth_parents`` — (slug, label, rank) for dropdown parents that have no
                     page of their own (href="#") — we synthesize a hub page.
    """

    def __init__(self) -> None:
        self.rank: dict[str, int] = {}
        self.parent_of: dict[str, str] = {}
        self.synth_parents: list[tuple[str, str, int]] = []
        self.labels: dict[str, str] = {}  # slug → nav label (owner's naming)

    @property
    def nav_slugs(self) -> set[str]:
        return set(self.rank) | set(self.parent_of)


def _gather_nav_evidence(nav_links: list[NavLink]) -> _NavEvidence:
    ev = _NavEvidence()
    position = 0
    for item in nav_links:
        parent_slug = _href_to_slug(item.href) if item.href != "#" else None
        if parent_slug is None and not item.children:
            continue  # anchor-only top-level item — no page behind it
        if parent_slug is None:
            # Unlinked dropdown toggle ("Services ▾" with no /services page):
            # synthesize a hub-page slug from the label so children have a home.
            parent_slug = _slugify(item.label)
            ev.synth_parents.append((parent_slug, item.label, position))
        if parent_slug:  # home ("") keeps its fixed first position
            ev.rank.setdefault(parent_slug, position)
            ev.labels.setdefault(parent_slug, item.label)
            position += 1
        for child in item.children:
            child_slug = _href_to_slug(child.href)
            if child_slug and parent_slug and child_slug != parent_slug:
                ev.parent_of.setdefault(child_slug, parent_slug)
                ev.labels.setdefault(child_slug, child.label)
    return ev


def _page_link_slugs(pages: list[SourceContent]) -> dict[str, set[str]]:
    """slug → set of same-site slugs that page links out to."""
    out: dict[str, set[str]] = {}
    for page in pages:
        slug = _path_to_slug(page.url_path)
        link_slugs: set[str] = set()
        for url in page.links:
            path = urlparse(url).path or "/"
            target = _path_to_slug(path)
            if target:
                link_slugs.add(target)
        out[slug] = link_slugs
    return out


def _subnav_edges(
    source: SourceContent,
    *,
    nav_slugs: set[str],
    known_slugs: set[str],
) -> dict[str, str]:
    """Hierarchy edges from repeated in-body menu strips.

    A cluster counts as *local subnav* (rather than a duplicated primary nav or
    a quick-links row) when it repeats across pages, isn't a subset of the
    header nav, and is self-referencing — at least one page carrying the strip
    is itself one of the strip's targets. That's the signature of a template's
    section menu: every service page shows the same strip of all services.
    """
    repeated = find_repeated_cluster_keys(source)
    if not repeated:
        return {}
    pages = [source, *source.discovered_pages]
    edges: dict[str, str] = {}

    for key in repeated:
        instance = None
        carriers: set[str] = set()
        for page in pages:
            for cluster in page.body_link_clusters:
                if cluster.href_key == key:
                    instance = instance or cluster
                    carriers.add(_path_to_slug(page.url_path))
                    break
        if instance is None:
            continue
        targets = {
            slug
            for slug in (_href_to_slug(link.href) for link in instance.links)
            if slug  # drop anchors and home
        }
        if len(targets) < 2:
            continue
        if targets <= nav_slugs:
            continue  # the template repeats the primary nav in the body — ignore
        if not carriers & targets:
            continue  # not self-referencing — likely a content list, leave it alone

        # Parent, first choice: the single listing-type page among the targets
        # (strips that include their own section landing, e.g. "All Services").
        listing = [t for t in targets if _is_explicit_listing(t)]
        parent: str | None = None
        if len(listing) == 1 and listing[0] in known_slugs:
            parent = listing[0]
        else:
            # Second choice: a unique listing-type page *outside* the strip
            # whose own links cover the strip's targets — the section landing
            # page listing its detail pages.
            covering = [
                slug
                for slug, link_slugs in _page_link_slugs(pages).items()
                if slug not in targets
                and slug in known_slugs
                and _is_explicit_listing(slug)
                and len(targets & link_slugs) >= max(2, len(targets) - 1)
            ]
            if len(covering) == 1:
                parent = covering[0]
        if parent is None:
            continue  # ambiguous — don't guess
        for target in targets:
            if target == parent or "/" in target:
                continue
            if target in nav_slugs:
                continue  # the owner promoted it to the header — respect that
            edges.setdefault(target, parent)
    return edges


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
    evidence = _gather_nav_evidence(source.nav_links)

    if not source.discovered_pages:
        # Thin site or crawl disabled: return the industry template's
        # core + suggested set verbatim. The recipe endpoint will surface
        # optional_pages separately. Source-nav order still applies where
        # template slugs happen to match (about, services, contact...).
        logger.info(
            "No discovered pages — falling back to '%s' industry template", industry
        )
        fallback = [*template.core_pages, *template.suggested_pages]
        # Seed the homepage pattern by site name so same-industry sites vary, even
        # on the no-crawl fallback. Copy (don't mutate) the shared template scaffold.
        fallback = [
            s.model_copy(update={"sections": homepage_sections(industry, seed=site_name)})
            if s.is_homepage
            else s
            for s in fallback
        ]
        for scaffold in fallback:
            scaffold.nav_rank = evidence.rank.get(scaffold.slug)
        return _apply_team_placement(fallback)

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
    scaffolds.append(_home_scaffold(by_slug.get("", source), industry, seed=site_name))
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
        # The owner's nav label is the best name for a page ("Install", not
        # "Sass: Install Sass" from the <title> tag) — prefer it when present.
        nav_label = (evidence.labels.get(slug) or "").strip()
        title = (
            nav_label
            if 0 < len(nav_label) <= 40
            else _title_from_page(page, slug, site_name=site_name)
        )

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
                    from_source=True,
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
                        from_source=True,
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
                    from_source=True,
                )
            )
            seen_slugs.add(slug)

    # 2b. Re-parent flat pages using the source's own navigation structure.
    #     Dropdown nesting in the header nav and self-referencing in-body menu
    #     strips both reveal hierarchy that flat URLs hide (e.g. /web-design
    #     belongs under /services even though the path doesn't say so).
    forced_parent: dict[str, str] = _subnav_edges(
        source, nav_slugs=evidence.nav_slugs, known_slugs=seen_slugs
    )
    forced_parent.update(evidence.parent_of)  # nav nesting beats cluster inference

    # Synthesized hub pages for unlinked dropdown toggles, only when at least
    # one child edge actually points at them.
    for synth_slug, synth_label, _rank in evidence.synth_parents:
        if synth_slug in seen_slugs:
            continue
        if not any(parent == synth_slug for parent in forced_parent.values()):
            continue
        synth_type = _infer_page_type(synth_slug, synth_label)
        scaffolds.append(
            PageScaffold(
                page_type=synth_type,
                slug=synth_slug,
                title=synth_label,
                sections=_sections_for(synth_type, parent_type=None),
                rationale=f"Hub page for the '{synth_label}' dropdown in the source navigation.",
                from_source=True,
            )
        )
        seen_slugs.add(synth_slug)

    # Nav items the bounded crawl never reached are still real pages the owner
    # curated into their header — scaffold them from the nav link alone.
    for nav_slug, _rank in sorted(evidence.rank.items(), key=lambda kv: kv[1]):
        if nav_slug in seen_slugs or "/" in nav_slug:
            continue
        label = evidence.labels.get(nav_slug) or _humanize(nav_slug)
        nav_type = _infer_page_type(nav_slug, label)
        if nav_type in ("privacy", "terms"):
            continue
        scaffolds.append(
            PageScaffold(
                page_type=nav_type,
                slug=nav_slug,
                title=label,
                sections=_sections_for(nav_type, parent_type=None),
                rationale="Linked in the source site's header navigation.",
                from_source=True,
            )
        )
        seen_slugs.add(nav_slug)

    by_scaffold_slug = {s.slug: s for s in scaffolds}
    for child_slug, parent_slug in forced_parent.items():
        child = by_scaffold_slug.get(child_slug)
        parent = by_scaffold_slug.get(parent_slug)
        if child is None or parent is None or child is parent:
            continue
        if child.parent_slug or child.is_homepage or child.is_legal:
            continue  # already nested (deep URL) or not nestable
        if parent.parent_slug or parent_slug in forced_parent:
            continue  # keep the tree one level deep — no chained nesting
        child.parent_slug = parent_slug
        if child.page_type == parent.page_type:
            child.page_type = "landing"
        child.sections = _sections_for(child.page_type, parent_type=parent.page_type)
        child.rationale = (
            child.rationale or f"Grouped under /{parent_slug} by the source navigation."
        )

    # 2c. Source-nav order → nav_rank on top-level scaffolds. menu_builder uses
    #     this to order and cap the primary menu.
    for scaffold in scaffolds:
        if scaffold.parent_slug is None and not scaffold.is_homepage:
            scaffold.nav_rank = evidence.rank.get(scaffold.slug)

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

    return _apply_team_placement(scaffolds)


def _apply_team_placement(scaffolds: list[PageScaffold]) -> list[PageScaffold]:
    """Place people content according to IA: Team page wins, otherwise About.

    Template-suggested Team pages are not source evidence. By default the roster
    belongs under About; a separate Team page is kept only when the crawl/nav/doc
    actually surfaced one (``from_source=True``). When a real Team page exists,
    remove the full team section from About to avoid duplicate rosters.
    """
    has_source_team_page = any(
        s.page_type == "team"
        and not s.is_legal
        and s.parent_slug is None
        and s.from_source
        for s in scaffolds
    )

    placed: list[PageScaffold] = []
    for scaffold in scaffolds:
        if (
            scaffold.page_type == "team"
            and scaffold.parent_slug is None
            and not scaffold.is_legal
            and not scaffold.from_source
        ):
            continue
        if has_source_team_page and scaffold.page_type == "about":
            scaffold = scaffold.model_copy(
                update={"sections": _without_section(scaffold.sections, "team")}
            )
        placed.append(scaffold)
    return placed


def _home_scaffold(
    home_source: SourceContent | None,
    industry: IndustryCategory | None = None,
    seed: str | None = None,
) -> PageScaffold:
    """Standard homepage scaffold — reuses the crawl's title if it has one."""
    title = "Home"
    if home_source and home_source.title:
        title = "Home"  # always literal "Home" in the nav; real headline comes from the LLM
    # Industry-fit landing pattern; `seed` (the brand/site name) varies the choice
    # among equally-fitting patterns so same-industry sites aren't identical. Falls
    # back to the standard order when industry is unknown or unmatched.
    sections = (
        homepage_sections(industry, seed=seed)
        if industry is not None
        else ["hero", "features", "testimonials", "cta"]
    )
    return PageScaffold(
        page_type="home",
        slug="",
        title=title,
        is_homepage=True,
        sections=sections,  # type: ignore[arg-type]
        rationale="Always present — first impression, value proposition, CTA.",
        # Only called on the crawl path, where the entry page is real source
        # evidence. Template-fallback homes come from the template verbatim.
        from_source=True,
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


def core_pages_not_in_inferred(
    core: list[PageScaffold], inferred: list[PageScaffold]
) -> list[PageScaffold]:
    """Keep template core pages only when the crawl didn't already cover them.

    For non-legal pages we treat an inferred page of the same semantic type as
    covering that core page, even when the source slug differs, e.g.
    ``about-us`` should suppress template ``about``.
    """
    inferred_slugs = {s.slug for s in inferred}
    inferred_types = {s.page_type for s in inferred if not s.is_legal}
    return [
        c for c in core
        if c.is_legal or (c.slug not in inferred_slugs and c.page_type not in inferred_types)
    ]
