"""
How a generated site maps onto the pages an entity already has.

A push is a SYNC, never an append. Every generated page lands on the entity
page that carries the same slug — updated in place, so the page keeps its id,
its URL, its revision history and its per-path insights — a page with no
counterpart is created, and every other page the entity had is archived. Never
deleted: an archived page is one click from restored in the admin, so a wrong
push costs nothing that cannot be got back. Everything that is not a page or
the design — articles, events, categories, tags, contacts, subscribers,
insights, the WhatsApp settings and the article/event template pages — is
never touched, which is what "update the website" means to its owner.

Two rules the plan enforces that the CMS's own API makes easy to get wrong:

- **An archived page still owns its slug.** ``PageSlugService::slugExists`` has
  no status filter, so creating "about" beside an archived "about" is a 422,
  and editing the archived one is a 409. A generated page whose slug matches an
  archived page is therefore RESTORED and updated, never recreated.
- **A template page owns its slug too.** The article/event listing templates
  render at a slug of their own, and a push never rewrites one — so a generated
  page that lands on a template's slug is LEFT TO IT, never created: the CMS
  refuses the slug (``slugExists`` counts every page, templates included), and
  a static copy of a listing the template renders live is not what the site
  wants at that URL anyway.
- **The homepage is matched by the empty slug**, which is how the CMS spells
  it (``createPage`` forces ``slug=''`` for ``isHomepage``), and it is never
  archived: the CMS refuses that outright, and the plan does not ask.

Slugs are settled before the plan is drawn. A generated slug keeps its
hierarchical form (``profile/ashley``) — the URL the source already ranks for —
unless the entity publishes that page flattened (``profile-ashley``, the form
every push wrote before nested slugs existed), in which case the live spelling
wins: renaming a published page is exactly the breakage an update exists to
avoid.

Pure functions: nothing here talks HTTP. ``push_orchestrator`` executes the
plan and ``routers/cms.py``'s ``/plan`` endpoint shows it — the same function,
so the preview the operator confirms is the plan the push runs.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from app.models.builder_schema import (
    BuilderElement,
    BuilderElementContent,
    GeneratedSite,
)
from app.services.platform_routes import is_template_page_path

PageAction = Literal["update", "create", "archive"]

ARCHIVED = "archived"


# --- slugs ----------------------------------------------------------------------


_SLUG_SEP_RE = re.compile(r"[^a-z0-9]+")


def cms_safe_slug(raw: str, *, keep_path: bool = False) -> str:
    """Coerce any string into a CMS-safe slug.

    Lowercase, collapse every run of non-alphanumerics to a single hyphen, trim
    hyphens, cap at the CMS's 160-char limit. "" / junk → "".

    ``keep_path`` preserves ``/`` as a segment separator and sanitizes each
    segment on its own, so "services/web-design" survives as itself instead of
    becoming "services-web-design" — see ``normalize_site_slugs``.
    """
    if keep_path and "/" in (raw or ""):
        segments = [cms_safe_slug(part) for part in raw.split("/")]
        return "/".join(part for part in segments if part)[:160].strip("-/")
    s = _SLUG_SEP_RE.sub("-", (raw or "").strip().lower()).strip("-")
    return s[:160].strip("-")


def slug_spelling(raw: str, existing: Collection[str]) -> str:
    """The CMS slug a generated page publishes at.

    Its hierarchical form, unless the entity already publishes that page under
    the flattened spelling — then the live URL wins. A page the entity has never
    had keeps its path, the same choice a brand-new site gets.
    """
    kept = cms_safe_slug(raw, keep_path=True)
    if kept in existing or "/" not in kept:
        return kept
    flat = cms_safe_slug(raw)
    return flat if flat in existing else kept


def normalize_site_slugs(
    site: GeneratedSite, *, existing_slugs: Collection[str] = ()
) -> dict[str, str]:
    """Normalize every slug to the form the CMS will accept, keeping
    parent_slug, page_tree and all baked nav hrefs consistent.

    ``existing_slugs`` are the pages the destination entity already has; a
    generated page matches one of them under whichever spelling it is
    published at (``slug_spelling``). Empty for a new entity.

    Returns the {old_slug: new_slug} map of slugs that actually changed.
    Mutates ``site`` in place.
    """
    slug_map: dict[str, str] = {}
    used: set[str] = set()
    for page in site.pages:
        old = page.slug or ""
        if page.is_homepage:
            new = ""
        else:
            new = (
                slug_spelling(old, existing_slugs)
                or cms_safe_slug(page.title)
                or "page"
            )
            base, n = new, 2
            while new in used:
                new = f"{base}-{n}"
                n += 1
            used.add(new)
        slug_map[old] = new
        page.slug = new

    # parent_slug references point at a parent's (old) slug — remap them.
    for page in site.pages:
        if page.parent_slug:
            page.parent_slug = (
                slug_map.get(page.parent_slug)
                or slug_spelling(page.parent_slug, existing_slugs)
                or None
            )

    # page_tree mirrors `pages` — keep node slugs in lock-step.
    def _fix_node(node) -> None:
        node.slug = (
            ""
            if node.is_homepage
            else (slug_map.get(node.slug) or slug_spelling(node.slug, existing_slugs))
        )
        for child in node.children:
            _fix_node(child)

    for node in site.page_tree or []:
        _fix_node(node)

    # Rewrite baked anchor hrefs (header/footer/body) that target an old slug.
    href_map = {
        f"/{old}": f"/{new}"
        for old, new in slug_map.items()
        if old and f"/{old}" != f"/{new}"
    }
    if href_map:
        for page in site.pages:
            for el in page.body_schema.elements:
                rewrite_hrefs(el, href_map)
        if site.header_schema:
            rewrite_hrefs(site.header_schema, href_map)
        if site.footer_schema:
            rewrite_hrefs(site.footer_schema, href_map)

    # Report only the slugs that actually changed.
    return {old: new for old, new in slug_map.items() if old != new}


def rewrite_hrefs(node: BuilderElement, href_map: dict[str, str]) -> None:
    """Walk a BuilderElement tree, rewriting internal anchor hrefs in place."""
    content = node.content
    if isinstance(content, BuilderElementContent):
        href = content.href
        if isinstance(href, str) and href:
            key = "/" + href.strip("/") if href != "/" else "/"
            if key in href_map:
                content.href = href_map[key]
    if isinstance(content, list):
        for child in content:
            rewrite_hrefs(child, href_map)


# --- the entity's pages ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ExistingPage:
    """One row of ``GET /entities/{token}/pages`` — what the plan reads."""

    id: str
    slug: str
    title: str
    status: str
    is_homepage: bool = False
    template_for: str | None = None

    @property
    def archived(self) -> bool:
        return self.status == ARCHIVED

    @property
    def is_template(self) -> bool:
        """An article/event detail or listing layout — never a content page."""
        return self.template_for is not None


def existing_pages(rows: Iterable[Mapping[str, Any]]) -> list[ExistingPage]:
    """Parse the CMS page list. A row without an id cannot be acted on."""
    pages: list[ExistingPage] = []
    for row in rows:
        page_id = row.get("id")
        if not page_id:
            continue
        pages.append(
            ExistingPage(
                id=str(page_id),
                slug=str(row.get("slug") or ""),
                title=str(row.get("title") or ""),
                status=str(row.get("status") or "draft"),
                is_homepage=bool(row.get("isHomepage")),
                template_for=row.get("templateFor") or None,
            )
        )
    return pages


# --- the plan -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PageChange:
    action: PageAction
    slug: str
    title: str
    # The entity page acted on — set for update/archive, None for create.
    page_id: str | None = None
    # An archived page brought back before it is updated (see module docstring).
    restore: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "slug": self.slug,
            "title": self.title,
            "restore": self.restore,
        }


@dataclass(frozen=True, slots=True)
class SyncPlan:
    """What a push will do to the entity's page set. Homepage first."""

    changes: tuple[PageChange, ...]
    # Content pages the entity has live (draft or published) before the push.
    existing_count: int
    # templateFor values already on the entity — kept, never rewritten.
    template_pages: tuple[str, ...] = ()
    # Generated pages a template page already serves — left to it, not created.
    template_routes: tuple[str, ...] = ()

    @property
    def first_push(self) -> bool:
        """Nothing to update yet, so the migration extras (articles, events,
        the WhatsApp button) go in too — the push a fresh entity has always
        had. Once a site has pages, an update is pages and design only."""
        return self.existing_count == 0

    def of(self, action: PageAction) -> list[PageChange]:
        return [change for change in self.changes if change.action == action]

    @property
    def landing(self) -> list[PageChange]:
        """The pages the site will have afterwards, in landing order."""
        return [change for change in self.changes if change.action != "archive"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "first_push": self.first_push,
            "existing_page_count": self.existing_count,
            "changes": [change.as_dict() for change in self.changes],
            "template_pages": list(self.template_pages),
            "template_routes": list(self.template_routes),
        }


def plan_sync(site: GeneratedSite, existing: Iterable[ExistingPage]) -> SyncPlan:
    """Match the site's pages against the entity's by slug.

    Call after ``normalize_site_slugs`` — the match is on the slug a page will
    be published at, not the slug the generator first wrote.
    """
    existing = list(existing)
    content = {page.slug: page for page in existing if not page.is_template}
    templates = {page.slug: page for page in existing if page.is_template}

    changes: list[PageChange] = []
    template_routes: list[str] = []
    for page in sorted(site.pages, key=lambda p: (not p.is_homepage, p.slug)):
        match = content.get(page.slug)
        if match is not None:
            changes.append(
                PageChange(
                    "update",
                    page.slug,
                    page.title,
                    page_id=match.id,
                    restore=match.archived,
                )
            )
        elif page.slug in templates:
            # The template page already renders this URL and a push never
            # rewrites one, so there is nothing to do here. Creating the page
            # would only be refused: the slug is taken (see module docstring).
            template_routes.append(page.slug)
        else:
            changes.append(PageChange("create", page.slug, page.title))

    wanted = {page.slug for page in site.pages}
    for page in existing:
        if (
            page.is_template
            or page.archived
            or page.is_homepage
            or page.slug == ""
            or page.slug in wanted
            # A page sitting on one of the platform's template slugs is not
            # content the site dropped: it is the template route, read back as
            # a page before the crawler learned to skip it. The template step
            # adopts it (push_orchestrator._adopt_as_template), so archiving it
            # here would only mean restoring it again minutes later.
            or is_template_page_path(page.slug)
        ):
            continue
        changes.append(PageChange("archive", page.slug, page.title, page_id=page.id))

    return SyncPlan(
        changes=tuple(changes),
        existing_count=sum(1 for page in content.values() if not page.archived),
        template_pages=tuple(
            sorted({page.template_for for page in existing if page.template_for})
        ),
        template_routes=tuple(template_routes),
    )


def describe_plan(plan: SyncPlan) -> str:
    """One line for the push report: what the entity had, what the push does."""
    parts = [
        f"{len(plan.of(action))} to {action}"
        for action in ("update", "create", "archive")
        if plan.of(action)
    ]
    if plan.template_routes:
        parts.append(f"{len(plan.template_routes)} left to a template page")
    counts = ", ".join(parts)
    had = (
        "no pages yet — first push"
        if plan.first_push
        else f"{plan.existing_count} page{'s' if plan.existing_count != 1 else ''}"
    )
    return f"Site has {had} · {counts}" if counts else f"Site has {had}"
