"""
Which top-level pages belong under one navigation category — a menu decision.

``menu_builder.plan_nav_groups`` asks this only when the header cannot hold its
pages as they are (more than ``MAX_PRIMARY_ITEMS``); below that a site keeps the
flat nav it always had, so page COUNT decides whether the nav becomes
hierarchical and the pages' RELATIONSHIPS decide what it becomes. A category is
never a page and never a URL: its members keep their slugs and their place in
the page tree, so breadcrumbs, canonicals and the sitemap are untouched.

What a page is comes from its own words — slug and title, read whole-token with
page_inference's matcher — against a small vocabulary of the categories a
visitor expects (About, the offering, Admissions, Work, Resources). A page the
vocabulary does not recognise stays a top-level link: ambiguity costs a
category, never a misfiled page. That is the same bet the page-type hints make,
and like them this vocabulary is English; a site whose pages are named in
another language simply keeps the flat nav it had.

Three rules keep a category honest:

* **A page that already has menu children is the owner's (or the URL's)
  structure.** It is never split up and never filed under another heading; it
  may HEAD its own category ("Programmes" with its programmes, plus a
  standalone "Activities"), its own children first and in their order.
* **A heading that is a page is that page** — the anchor ("About" heading Team
  and Facilities keeps its own link and its own title, in the site's language).
  Only where no page heads a category is a heading written, and then only in
  English (``_writes_headings``) and only over two or more pages: a one-page
  category is a detour, not a category.
* **The first family to claim a page keeps it.** Families are tried in a fixed
  order, so a childcare FAQ files under Admissions when there is one and under
  Resources otherwise, and no page is ever listed twice.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.builder_schema import NavGroup, PageNode


@dataclass(frozen=True)
class _Family:
    key: str
    # A page titled (or slugged) exactly one of these heads the category; "our"
    # is dropped first, so "Our Services" heads it as "Services" does.
    hubs: tuple[str, ...]
    # A page whose slug or title carries one of these belongs to it. The order
    # is editorial: where the menu has no owner's order to keep, members are
    # listed by the first hint they match ("Our Story" before "Our Teachers").
    hints: tuple[str, ...]
    # The heading when no page heads it — by industry, then the default.
    labels: tuple[tuple[str, str], ...] = ()
    label: str = ""
    # Industries the family exists for; empty = every industry.
    industries: frozenset[str] = frozenset()
    # The page type whose header weight the category takes when no owner ranked
    # the nav (menu_builder._TYPE_NAV_WEIGHT): Admissions is a step towards
    # signing up, so it sits where Pricing would, after what is on offer.
    ranks_as: str = "services"

    def label_for(self, industry: str | None) -> str:
        return dict(self.labels).get(industry or "", self.label)


# In claim order: a page joins the FIRST of these that forms around it.
_FAMILIES: tuple[_Family, ...] = (
    _Family(
        "about",
        hubs=("about", "about us", "about me", "who we are", "company"),
        hints=(
            "story", "history", "mission", "values", "philosophy", "founder",
            "founders", "team", "people", "staff", "teachers", "educators",
            "leadership", "committee", "board", "facilities", "campus", "premises",
            "testimonials", "testimonial", "reviews", "why us", "why choose us",
            "partners", "awards", "accreditation", "accreditations", "careers",
            "career", "jobs", "job vacancy", "vacancies", "join us",
        ),
        label="About",
        ranks_as="about",
    ),
    # A software product's own surface. SaaS only: "security" or "features"
    # on a locksmith's or a venue's site is not a platform.
    _Family(
        "platform",
        hubs=("platform", "product", "features"),
        hints=("features", "platform", "integrations", "integration", "security", "api", "how it works"),
        label="Platform",
        industries=frozenset({"saas"}),
    ),
    # Who a software product is for. SaaS only: "Education" on a school's site
    # is the school.
    _Family(
        "audiences",
        hubs=("solutions", "industries", "use cases"),
        hints=(
            "small business", "small businesses", "smb", "startups", "startup",
            "enterprise", "enterprises", "agencies", "nonprofits", "nonprofit",
            "education", "schools", "healthcare", "retail", "ecommerce",
            "finance", "financial services", "government", "manufacturing",
            "hospitality", "real estate", "logistics", "use case", "use cases",
        ),
        label="Solutions",
        industries=frozenset({"saas"}),
    ),
    _Family(
        "offerings",
        hubs=(
            "services", "programmes", "programs", "products", "solutions",
            "classes", "courses", "treatments", "therapies", "what we do",
        ),
        hints=(
            "curriculum", "programme", "programmes", "program", "programs",
            "montessori", "playgroup", "nursery", "daycare", "infant care",
            "after school", "enrichment", "activities", "class", "classes",
            "course", "courses", "lessons", "workshops", "holiday camp", "camps",
            "our day", "daily routine", "services", "products", "solutions",
            "treatments", "therapy", "therapies", "packages",
        ),
        labels=(("childcare", "Programmes"),),
        label="Services",
    ),
    # How a parent gets a child in: a school's journey, not a shop's checkout —
    # elsewhere "fees" and "pricing" stay the top-level pages they are.
    _Family(
        "admissions",
        hubs=("admissions", "admission", "enrolment", "enrollment", "enrol", "enroll", "registration"),
        hints=(
            "admissions", "admission", "enrolment", "enrollment", "enrol", "enroll",
            "registration", "register", "apply", "open day", "open house",
            "school tour", "book a tour", "fees", "fee", "tuition", "pricing",
            "faq", "faqs",
        ),
        label="Admissions",
        industries=frozenset({"childcare"}),
        ranks_as="pricing",
    ),
    # A portfolio, where the business sells what it made. "work" alone is no
    # hint: "How We Work" is a process page. A SaaS site's case studies are
    # reading, and file under Resources.
    _Family(
        "work",
        hubs=("work", "portfolio", "projects", "case studies"),
        hints=("portfolio", "projects", "case studies", "case study", "clients"),
        label="Work",
        ranks_as="work",
        industries=frozenset({
            "agency", "consultancy", "personal", "professional-services",
            "home-services", "real-estate", "other",
        }),
    ),
    _Family(
        "resources",
        hubs=("resources", "resource centre", "resource center", "learn"),
        hints=(
            "blog", "news", "articles", "insights", "journal", "press", "newsroom",
            "events", "case studies", "case study", "customers", "customer stories",
            "success stories", "guides", "guide", "documentation", "docs",
            "help centre", "help center", "knowledge base", "tutorials", "webinars",
            "ebooks", "podcast", "downloads", "changelog", "release notes",
            "roadmap", "faq", "faqs",
        ),
        label="Resources",
        ranks_as="blog",
    ),
)

_BY_KEY = {family.key: family for family in _FAMILIES}


def ranks_as(kind: str | None) -> str | None:
    """The page type a category of ``kind`` ranks as in a header nobody ranked
    (None for a category this vocabulary no longer has)."""
    family = _BY_KEY.get(kind or "")
    return family.ranks_as if family else None


def _tokens(text: str) -> list[str]:
    # Local import: page_inference imports nothing from the menu modules, so this
    # stays cycle-free while reusing the one whole-token matcher.
    from app.services.page_inference import _match_tokens

    return _match_tokens(text)


def _hub_key(text: str) -> str:
    tokens = _tokens(text)
    if tokens[:1] == ["our"]:
        tokens = tokens[1:]
    return " ".join(tokens)


def _heads(family: _Family, node: PageNode) -> bool:
    # The slug counts as much as the title: "About Our School" at /about heads
    # About, and so does an owner's "Kenali Kami" there.
    keys = {_hub_key(node.title), _hub_key(node.slug.rsplit("/", 1)[-1])}
    return not keys.isdisjoint(family.hubs)


def _rank(family: _Family, node: PageNode) -> int | None:
    """Where ``node`` sits in ``family`` — 0 for a page that could head it, then
    its first hint's place — or None when the family does not claim it."""
    from app.services.page_inference import _hint_matches

    if _heads(family, node):
        return 0
    tokens = _tokens(f"{node.slug} {node.title}")
    return next(
        (index for index, hint in enumerate(family.hints, 1) if _hint_matches(hint, tokens)),
        None,
    )


def _writes_headings(language: str | None) -> bool:
    """Whether a heading no page names may be written: the vocabulary is
    English, and an English word in a Malay or Chinese header is a wrong word.
    No plan (a site from before the language plan) reads as English, as every
    other generated label does."""
    return language is None or language.split("-", 1)[0].lower() == "en"


def _label(family: _Family, industry: str | None, members: list[PageNode]) -> str:
    label = family.label_for(industry)
    # The site's own spelling: "Programs" beside "Infant Program", not "Programmes".
    if label == "Programmes" and any(
        token in ("program", "programs")
        for node in members for token in _tokens(f"{node.slug} {node.title}")
    ):
        return "Programs"
    return label


def group_pages(
    pages: list[PageNode],
    *,
    industry: str | None,
    language: str | None,
    has_children: frozenset[str] = frozenset(),
    owner_order: bool = False,
) -> list[NavGroup]:
    """The categories over ``pages`` (top-level, in menu order), each at the
    place of its head — or, with none, of its first member. ``has_children``
    names the pages that already carry menu children: never members, only
    possible heads. ``owner_order`` (the source's own nav ranked these pages)
    keeps members in the order given; otherwise they are listed editorially,
    by the family's hint order.

    Every group returned is formed: a head and at least one member, or two
    members under a written heading.
    """
    families = [f for f in _FAMILIES if not f.industries or (industry or "") in f.industries]
    headings = _writes_headings(language)
    position = {node.slug: index for index, node in enumerate(pages)}

    # Heads first, so a page that heads one category is never taken as a member
    # of an earlier one ("Careers" files under About; "About" heads it).
    heads: dict[str, PageNode] = {}
    taken: set[str] = set()
    for family in families:
        head = next((n for n in pages if n.slug not in taken and _heads(family, n)), None)
        if head is not None:
            heads[family.key] = head
            taken.add(head.slug)

    groups: list[tuple[int, NavGroup]] = []
    for family in families:
        head = heads.get(family.key)
        ranked = [
            (rank, position[n.slug], n)
            for n in pages
            if n.slug not in taken and n.slug not in has_children
            and (rank := _rank(family, n)) is not None
        ]
        if not ranked or (head is None and not (headings and len(ranked) >= 2)):
            continue  # a head alone is just its own link; one page is no category
        if not owner_order:
            ranked.sort(key=lambda entry: entry[:2])
        members = [n for _, _, n in ranked]
        taken.update(n.slug for n in members)
        group = NavGroup(
            label=head.title if head else _label(family, industry, members),
            slug=head.slug if head else None,
            members=[n.slug for n in members],
            kind=family.key,
        )
        place = position[(head or members[0]).slug]
        groups.append((place, group))
    return [group for _, group in sorted(groups, key=lambda pair: pair[0])]
