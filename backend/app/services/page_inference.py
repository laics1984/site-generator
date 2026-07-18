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
            # Roster/directory listings ("Find a Music Therapist", practitioner
            # directories). Deliberately conservative: bare "members" would
            # catch membership-info pages.
            "directory",
            "practitioners",
            "find-a",
        ),
    ),
    ("blog", ("blog", "news", "insights", "articles", "press", "media-centre", "newsroom")),
    # Bare "event" is deliberately absent — substring matching would catch
    # e.g. "prevention". Singular /event listings still match via "events" in
    # the page title or the calendar/whats-on tokens.
    ("events", ("events", "calendar", "whats-on", "upcoming-events", "event-list")),
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


# Minimum scraper-extracted profile cards before a page counts as a people
# directory. High enough that a page with a couple of inline author/contact
# cards keeps its own recipe. Imported by routers/generate.py so the roster
# fill uses the same threshold as the classification.
DIRECTORY_MIN_PROFILES = 6


def _looks_like_directory_page(page: SourceContent | None) -> bool:
    """True when the page body is a repeated profile-card roster.

    Evidence comes from the scraper's structured card extraction
    (``SourceContent.profile_candidates``), not from keywords — this catches
    directory pages whose slug says nothing people-like ("find-a-music-
    therapist").
    """
    return page is not None and len(page.profile_candidates or []) >= DIRECTORY_MIN_PROFILES


def _coerce_directory_type(page_type: PageType, page: SourceContent | None) -> PageType:
    """A page whose body is a profile roster is a people directory, whatever
    its slug says — type it ``team`` so its recipe renders the roster instead
    of FAQ-ifying the flattened card text.

    Coerced: the generic fallbacks (services/landing) and contact — sites do
    park their directory at /contact (MMTA's "Find a Music Therapist" lives
    there), and a genuine contact page essentially never carries 6+ profile
    cards. NOT coerced: about (a team grid inside a real about narrative is
    normal — its recipe already includes a team section, so the roster still
    renders), faq, home.
    """
    if page_type in ("services", "landing", "contact") and _looks_like_directory_page(page):
        return "team"
    return page_type


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
    # blog/events carry only a hero here — schema_builder appends the CMS
    # articlesList / eventsList element below it (dynamic, not LLM content).
    "blog":         ["hero"],
    "events":       ["hero"],
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


# Page types whose fixed rhythm always wins — conversion pages (home), pages
# with a dedicated structure (menu, gallery, team, …) and legal boilerplate.
# Everything else may switch to the story rhythm when the source evidences it.
_STORY_INELIGIBLE_TYPES: frozenset[PageType] = frozenset(
    {"home", "contact", "faq", "menu", "pricing", "team", "gallery",
     "testimonials", "privacy", "terms", "thank-you", "blog", "events"}
)

# Minimum distinct heading+photo sections before a page counts as a story page.
_STORY_MIN_SECTIONS = 3


def _story_section_count(page: SourceContent | None) -> int:
    """How many title+paragraph+photo sections the source page evidences.

    A "story page" (e.g. a kindergarten's School Life page) narrates section by
    section, each with its own heading and photo. Signal: content-grade images
    whose context_heading matches one of the page's headings, counted over
    distinct headings. Zero on the fast path when no context was captured —
    detection degrades to the fixed rhythm, never breaks it.
    """
    if page is None:
        return 0
    headings = {h.strip().lower() for h in (page.headings or []) if h.strip()}
    if not headings:
        return 0
    matched: set[str] = set()
    for meta in page.image_metadata or []:
        if meta.role in ("logo", "decoration") or meta.intent == "logo":
            continue
        ch = (meta.context_heading or "").strip().lower()
        if ch and ch in headings:
            matched.add(ch)
    return len(matched)


def _story_sections(story_count: int) -> list[SectionType]:
    """hero + one image+text (about) section per source section + closing cta.

    Each about renders as an image+text split whose photo the planner binds to
    the source section's real image (image_ref) — the fidelity-preserving
    alternative to flattening every source section into text-only card grids.
    """
    n = min(story_count, _MAX_PAGE_SECTIONS - 2)
    return ["hero", *(["about"] * n), "cta"]


def _content_photo_count(page: SourceContent | None) -> int:
    """Content-grade photos on the page (logos/decorations excluded)."""
    if page is None:
        return 0
    return sum(
        1
        for meta in page.image_metadata or []
        if meta.role not in ("logo", "decoration") and meta.intent != "logo"
    )


# Page types where photo-backed about splits are never woven in: pages whose
# structure is the content (contact form, FAQ list, menu, pricing table,
# gallery/team grids already photo-led) and legal boilerplate.
_PHOTO_SECTION_INELIGIBLE_TYPES: frozenset[PageType] = frozenset(
    {"contact", "faq", "menu", "pricing", "gallery", "team",
     "privacy", "terms", "thank-you", "blog", "events"}
)

# Cap on image+text about splits woven into a fixed rhythm (full story pages
# derive their count from the source instead).
_MAX_PHOTO_SECTIONS = 2


def _weave_photo_sections(
    sections: list[SectionType], page_type: PageType, page: SourceContent | None
) -> list[SectionType]:
    """Weave image+text `about` splits into a page's fixed rhythm.

    Landing-page practice: a content page communicates better when text blocks
    are broken up by at least one image+text moment, so every eligible page
    gets ONE about split as a baseline — its photo comes from the source when
    one was matched, else the LLM's stock image_query (Pexels). Image-rich
    pages (heading-matched photos, or 4+ loose content photos) get up to
    _MAX_PHOTO_SECTIONS splits bound to their real photos via image_ref. Full
    story pages replace their rhythm outright instead (see _story_sections).
    Cards stay for genuinely list-like content — they just stop monopolising
    the page.
    """
    if page_type in _PHOTO_SECTION_INELIGIBLE_TYPES:
        return sections
    matched = _story_section_count(page)
    if matched <= 0 and _content_photo_count(page) >= 4:
        # Image-rich page whose photos didn't match headings (slideshow, loose
        # gallery) — the LLM binds the best-fitting photo via image_ref or
        # falls back to its image_query.
        matched = 1
    # Baseline: at least one image+text section per content page, stock-backed
    # when the source is text-only.
    want = max(min(matched, _MAX_PHOTO_SECTIONS), 1)
    add = min(
        max(0, want - sections.count("about")),
        max(0, _MAX_PAGE_SECTIONS - len(sections)),
    )
    if add <= 0:
        return sections
    extras: list[SectionType] = ["about"] * add
    if "cta" in sections:
        idx = sections.index("cta")
        return [*sections[:idx], *extras, *sections[idx:]]
    return [*sections, *extras]


def _sections_for(
    page_type: PageType,
    parent_type: PageType | None,
    page: SourceContent | None = None,
) -> list[SectionType]:
    # A detected profile directory renders its roster whatever its nesting —
    # the team grid IS the page content, so the top-level team recipe wins
    # even for sub-pages (which would otherwise get the generic detail rhythm).
    if page_type == "team" and _looks_like_directory_page(page):
        return _augment_sections(list(_TOP_SECTIONS["team"]), page_type, page)
    # Story pages override the fixed rhythms: the source itself dictates the
    # section list (a photo-and-heading section sequence), not the page type.
    if page_type not in _STORY_INELIGIBLE_TYPES:
        story_count = _story_section_count(page)
        if story_count >= _STORY_MIN_SECTIONS:
            return _augment_sections(_story_sections(story_count), page_type, page)
    if parent_type is None:
        base = list(_TOP_SECTIONS.get(page_type, _TOP_SECTIONS["services"]))
    elif parent_type == "work":
        base = list(_WORK_SUBPAGE_SECTIONS)
    elif parent_type == "team":
        base = list(_TEAM_SUBPAGE_SECTIONS)
    else:
        base = list(_SUBPAGE_SECTIONS)
    # Below the story threshold, an image-rich page still gets its matched
    # photos as image+text splits woven into the fixed rhythm.
    base = _weave_photo_sections(base, page_type, page)
    return _augment_sections(base, page_type, page)


# --- content-signal detection (timeline / awards / clients / stats) -------------
#
# Unlike _TOP_SECTIONS (pure URL/nav structure), these sections are only ever
# added when the page's own text actually evidences them — a restaurant's
# /about page doesn't get an awards section just because it's "about", but it
# does if the text says "Awarded Best Bakery 2022". The LLM still omits the
# section if it later finds the signal was a false positive (same fidelity
# rule as testimonials/faq/pricing/etc in scaffold_enforcement.py).

_SIGNAL_PATTERNS: dict[SectionType, re.Pattern[str]] = {
    "timeline": re.compile(
        r"\b(our history|our journey|founded in|established in|since (19|20)\d{2}"
        r"|milestones?|over the years)\b",
        re.IGNORECASE,
    ),
    "awards": re.compile(
        r"\b(award[- ]?winning|awards?|certified|certifications?|recogni[sz]ed"
        r"|winner of|accredited)\b",
        re.IGNORECASE,
    ),
    "clients": re.compile(
        r"\b(our clients|trusted by|clients include|customers include"
        r"|partners include|as seen in|some of our clients)\b",
        re.IGNORECASE,
    ),
    "stats": re.compile(
        r"(\d+%|\d+\+\b|over \d+|years of experience)",
        re.IGNORECASE,
    ),
    "locations": re.compile(
        r"\b(our (branch|location|outlet|centre|center|campus)e?s"
        r"|branch(es)? (in|at)|visit us (at|in)|find us (at|in)|both branches)\b",
        re.IGNORECASE,
    ),
}

# page_type → which signal kinds are even eligible there. Keeps the new
# sections off pages where they'd be noise (contact, faq, menu, pricing...).
_SIGNAL_ELIGIBLE_TYPES: dict[SectionType, frozenset[PageType]] = {
    "timeline": frozenset({"home", "about"}),
    "awards": frozenset({"home", "about", "services"}),
    "clients": frozenset({"home", "work"}),
    "stats": frozenset({"home", "about"}),
    "locations": frozenset({"home", "about", "contact"}),
}

_MAX_EXTRA_SECTIONS = 2  # quality cap — don't let a page balloon past its rhythm

# Hard ceiling on a single page's section count. A rich page is fine — the
# generator splits any page above the per-call cap into several light section
# groups (see planner._generate_page_section_chunks), so page richness is
# decoupled from per-call weight. This ceiling just stops truly runaway pages;
# evidenced extras are trimmed to fit, and core sections always survive.
_MAX_PAGE_SECTIONS = 9


def _detect_content_signals(page: SourceContent) -> list[SectionType]:
    """Detected kinds, in `_SIGNAL_PATTERNS` order — deterministic when the cap bites."""
    haystack = " ".join([page.raw_text or "", " ".join(page.headings or [])])
    return [kind for kind, pattern in _SIGNAL_PATTERNS.items() if pattern.search(haystack)]


def _augment_sections(
    sections: list[SectionType], page_type: PageType, page: SourceContent | None
) -> list[SectionType]:
    if page is None:
        return sections
    detected = _detect_content_signals(page)
    extras = [
        kind
        for kind in detected
        if page_type in _SIGNAL_ELIGIBLE_TYPES.get(kind, frozenset())
        and kind not in sections
    ][:_MAX_EXTRA_SECTIONS]
    # Keep the page within the single-call ceiling — trim extras to the room left
    # (core sections always win). Prevents an evidence-rich page (e.g. a homepage
    # that already runs long) from ballooning into an over-large, failure-prone
    # single LLM call.
    extras = extras[: max(0, _MAX_PAGE_SECTIONS - len(sections))]
    if not extras:
        return sections
    if "cta" in sections:
        idx = sections.index("cta")
        return [*sections[:idx], *extras, *sections[idx:]]
    return [*sections, *extras]


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
    "services", "work", "team", "blog", "events", "menu", "gallery", "pricing",
    "faq", "process",
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
            s.model_copy(
                update={
                    "sections": _augment_sections(
                        homepage_sections(industry, seed=site_name), "home", source
                    )
                }
            )
            if s.is_homepage
            else s
            for s in fallback
        ]
        # The scraped entry page itself may be a profile directory ("find a
        # therapist" scraped as a single page). Surface it as a source-evidenced
        # team page so the roster renders instead of feeding template recipes.
        if _looks_like_directory_page(source):
            dir_slug = _path_to_slug(source.url_path) or "team"
            directory_scaffold = PageScaffold(
                page_type="team",
                slug=dir_slug,
                title=_title_from_page(source, dir_slug, site_name=site_name),
                sections=list(_TOP_SECTIONS["team"]),
                rationale="Profile directory detected on the scraped page.",
                source_url=source.source_ref,
                from_source=True,
            )
            existing_idx = next(
                (i for i, s in enumerate(fallback) if s.slug == dir_slug), None
            )
            if existing_idx is not None:
                fallback[existing_idx] = directory_scaffold
            else:
                fallback.append(directory_scaffold)
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
            page_type = _coerce_directory_type(_infer_page_type(slug, title), page)
            if page_type in ("privacy", "terms"):
                # Legal — we generate from boilerplate, not the crawl.
                continue
            scaffolds.append(
                PageScaffold(
                    page_type=page_type,
                    slug=slug,
                    title=title,
                    sections=_sections_for(page_type, parent_type=None, page=page),
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
            if parent_type in ("blog", "events"):
                # Post / event detail pages aren't scaffolded as static pages —
                # they're migrated into CMS article/event entries and rendered
                # through the builder's detail templates (content_collections).
                seen_slugs.add(slug)
                continue
            sub_type = _infer_page_type(slug, title)
            # Sub-pages typically aren't another listing of the parent — coerce
            # services/x → landing detail unless title looks like a real category.
            if sub_type == parent_type:
                sub_type = "landing"
            sub_type = _coerce_directory_type(sub_type, page)
            scaffolds.append(
                PageScaffold(
                    page_type=sub_type,
                    slug=slug,
                    title=title,
                    sections=_sections_for(sub_type, parent_type=parent_type, page=page),
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
        child.sections = _sections_for(
            child.page_type, parent_type=parent.page_type, page=by_slug.get(child_slug)
        )
        child.rationale = (
            child.rationale or f"Grouped under /{parent_slug} by the source navigation."
        )

    # 2c. Source-nav order → nav_rank on top-level scaffolds. menu_builder uses
    #     this to order and cap the primary menu.
    for scaffold in scaffolds:
        if scaffold.parent_slug is None and not scaffold.is_homepage:
            scaffold.nav_rank = evidence.rank.get(scaffold.slug)

    # 2d. The entry page itself is a profile directory (the user scraped the
    #     directory URL directly) and no scaffold surfaced a team section —
    #     weave the roster into the homepage so the profiles aren't dropped.
    if _looks_like_directory_page(source) and not any(
        "team" in s.sections for s in scaffolds
    ):
        home = next((s for s in scaffolds if s.is_homepage), None)
        if home is not None:
            if "cta" in home.sections:
                home.sections.insert(home.sections.index("cta"), "team")
            else:
                home.sections.append("team")

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
    # An image-rich homepage gets image+text about splits woven in, same as
    # interior pages — the landing pattern stays, cards stop monopolising it.
    sections = _weave_photo_sections(sections, "home", home_source)
    sections = _augment_sections(sections, "home", home_source)
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
