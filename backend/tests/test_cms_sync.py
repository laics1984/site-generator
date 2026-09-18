"""
How a generated site maps onto the pages an entity already has
(services/cms_sync.py).

Three properties carry the update feature and are pinned here:

- **A push is a sync keyed on slug.** A generated page lands on the entity
  page with the same slug — updated, not recreated — so its id, URL, history
  and per-path insights survive; a page with no counterpart is created; the
  entity's other pages are archived, never deleted. Template pages and the
  homepage are never archived.
- **An archived page still owns its slug** in the CMS, so a match on one is a
  restore-then-update, never a create that would 422.
- **Slugs follow the live site's spelling.** A site published before nested
  slugs existed has `profile-ashley` live; the generator now writes
  `profile/ashley`; the update must land on the live one. A page the site never
  had keeps its path, the same choice a brand-new site gets.
"""

from __future__ import annotations

import unittest

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageNode,
    PageSeo,
)
from app.services.cms_sync import (
    ExistingPage,
    describe_plan,
    existing_pages,
    normalize_site_slugs,
    plan_sync,
    slug_spelling,
)


def _page(slug: str, *, parent: str | None = None, home: bool = False) -> GeneratedPage:
    return GeneratedPage(
        slug=slug,
        title=slug.split("/")[-1].title() if slug else "Home",
        is_homepage=home,
        body_schema=BodySchema(elements=[]),
        seo=PageSeo(),
        parent_slug=parent,
    )


def _site(*slugs: str) -> GeneratedSite:
    return GeneratedSite(
        site_name="Acme",
        pages=[_page("", home=True), *(_page(slug) for slug in slugs)],
        page_tree=[],
    )


def _existing(
    slug: str,
    *,
    page_id: str | None = None,
    status: str = "published",
    template_for: str | None = None,
) -> ExistingPage:
    return ExistingPage(
        id=page_id or f"id-{slug or 'home'}",
        slug=slug,
        title=slug.title() or "Home",
        status=status,
        is_homepage=slug == "",
        template_for=template_for,
    )


class SlugSpellingTest(unittest.TestCase):
    def test_a_new_site_keeps_the_source_path(self):
        # The whole point of the migration: mmta.org.my/profile/ashley still
        # resolves after the switchover, so its search ranking survives.
        self.assertEqual(slug_spelling("profile/ashley", set()), "profile/ashley")

    def test_a_site_published_flat_keeps_its_flat_urls(self):
        # Renaming pages that are already published is the breakage an
        # update exists to avoid.
        self.assertEqual(
            slug_spelling("profile/ashley", {"", "committee", "profile-ashley"}),
            "profile-ashley",
        )

    def test_a_site_published_nested_keeps_its_nested_urls(self):
        self.assertEqual(
            slug_spelling("profile/ashley", {"profile/ashley", "profile-ashley"}),
            "profile/ashley",
        )

    def test_a_page_the_site_never_had_keeps_its_path(self):
        self.assertEqual(
            slug_spelling("services/web-design", {"", "about"}), "services/web-design"
        )

    def test_a_flat_slug_is_never_nested(self):
        self.assertEqual(slug_spelling("about-us", {"about/us"}), "about-us")


def _hierarchical_site() -> GeneratedSite:
    """A migrated site whose slugs are the source's own paths."""
    return GeneratedSite(
        site_name="MMTA",
        pages=[
            _page("", home=True),
            _page("committee"),
            _page("profile/ashley", parent="committee"),
        ],
        page_tree=[
            PageNode(
                slug="committee",
                title="Committee",
                children=[PageNode(slug="profile/ashley", title="Ashley Jinivon")],
            )
        ],
        header_schema=BuilderElement(
            name="Header",
            type="__header",
            content=[
                BuilderElement(
                    name="Nav link",
                    type="link",
                    content=BuilderElementContent(innerText="Ashley", href="/profile/ashley"),
                )
            ],
        ),
    )


class NormalizeSiteSlugsTest(unittest.TestCase):
    def test_a_new_site_keeps_the_source_urls(self):
        site = _hierarchical_site()

        renamed = normalize_site_slugs(site)

        self.assertEqual([p.slug for p in site.pages], ["", "committee", "profile/ashley"])
        self.assertEqual(site.pages[2].parent_slug, "committee")
        self.assertEqual(site.page_tree[0].children[0].slug, "profile/ashley")
        self.assertEqual(renamed, {})

    def test_an_update_lands_on_the_live_spelling_everywhere(self):
        """Pages, parent_slug, page_tree and the baked nav href all move
        together, or the header links to a page that no longer exists."""
        site = _hierarchical_site()

        renamed = normalize_site_slugs(
            site, existing_slugs={"", "committee", "profile-ashley"}
        )

        self.assertEqual([p.slug for p in site.pages], ["", "committee", "profile-ashley"])
        self.assertEqual(site.page_tree[0].children[0].slug, "profile-ashley")
        self.assertEqual(renamed, {"profile/ashley": "profile-ashley"})
        nav_link = site.header_schema.content[0]
        self.assertEqual(nav_link.content.href, "/profile-ashley")

    def test_segments_are_still_sanitized_inside_a_kept_path(self):
        site = _hierarchical_site()
        site.pages[2].slug = "Profile/Kuek Ser Sheen Tse"

        normalize_site_slugs(site)

        self.assertEqual(site.pages[2].slug, "profile/kuek-ser-sheen-tse")

    def test_colliding_slugs_are_suffixed(self):
        site = _site("About Us", "about-us")

        normalize_site_slugs(site)

        self.assertEqual([p.slug for p in site.pages], ["", "about-us", "about-us-2"])


class PlanSyncTest(unittest.TestCase):
    def test_pages_match_by_slug_and_the_rest_are_created(self):
        plan = plan_sync(_site("about", "contact"), [_existing(""), _existing("about")])

        self.assertEqual(
            [(c.action, c.slug, c.page_id) for c in plan.changes],
            [
                ("update", "", "id-home"),
                ("update", "about", "id-about"),
                ("create", "contact", None),
            ],
        )
        self.assertEqual(plan.existing_count, 2)
        self.assertFalse(plan.first_push)

    def test_an_archived_match_is_restored_not_recreated(self):
        """The CMS keeps an archived page's slug reserved, so a create would
        422 and an edit would 409 — restoring is the only path."""
        plan = plan_sync(_site("team"), [_existing(""), _existing("team", status="archived")])

        team = plan.of("update")[1]
        self.assertEqual((team.slug, team.page_id, team.restore), ("team", "id-team", True))
        self.assertEqual(plan.of("create"), [])
        # An archived page is not a live one.
        self.assertEqual(plan.existing_count, 1)

    def test_pages_the_new_site_lacks_are_archived(self):
        plan = plan_sync(
            _site("about"),
            [_existing(""), _existing("about"), _existing("careers"), _existing("news", status="draft")],
        )

        self.assertEqual(
            [(c.slug, c.page_id) for c in plan.of("archive")],
            [("careers", "id-careers"), ("news", "id-news")],
        )

    def test_the_homepage_templates_and_already_archived_pages_are_never_archived(self):
        existing = [
            _existing(""),
            _existing("old", status="archived"),
            _existing("article-template", template_for="article"),
            _existing("event-listing-template", template_for="eventListing"),
        ]

        plan = plan_sync(_site("about"), existing)

        self.assertEqual(plan.of("archive"), [])
        self.assertEqual(plan.template_pages, ("article", "eventListing"))
        # A template page never matches a content page either.
        self.assertEqual([c.slug for c in plan.of("update")], [""])

    def test_a_site_with_only_template_pages_is_a_first_push(self):
        """The builder creates template pages on first open, so an entity can
        hold them and still never have had a content page."""
        plan = plan_sync(
            _site("about"), [_existing("article-template", template_for="article")]
        )

        self.assertTrue(plan.first_push)
        self.assertEqual([c.action for c in plan.changes], ["create", "create"])

    def test_the_homepage_lands_first(self):
        site = GeneratedSite(
            site_name="Acme", pages=[_page("zzz"), _page("aaa"), _page("", home=True)], page_tree=[]
        )

        plan = plan_sync(site, [])

        self.assertEqual([c.slug for c in plan.landing], ["", "aaa", "zzz"])

    def test_as_dict_is_the_wire_shape(self):
        plan = plan_sync(_site("about"), [_existing(""), _existing("careers")])

        self.assertEqual(
            plan.as_dict(),
            {
                "first_push": False,
                "existing_page_count": 2,
                "changes": [
                    {"action": "update", "slug": "", "title": "Home", "restore": False},
                    {"action": "create", "slug": "about", "title": "About", "restore": False},
                    {"action": "archive", "slug": "careers", "title": "Careers", "restore": False},
                ],
                "template_pages": [],
                "template_routes": [],
            },
        )

    def test_a_page_whose_slug_a_template_page_serves_is_left_to_it(self):
        """The CMS counts template pages when it checks a slug, so creating a
        page beside one is a 422 — and a static copy of a listing the template
        renders live is not what belongs at that URL anyway."""
        plan = plan_sync(
            _site("articles"),
            [_existing(""), _existing("articles", template_for="articleListing")],
        )

        self.assertEqual([change.action for change in plan.changes], ["update"])
        self.assertEqual(plan.template_routes, ("articles",))
        self.assertEqual(plan.of("archive"), [])
        self.assertIn("1 left to a template page", describe_plan(plan))
        self.assertEqual(plan.as_dict()["template_routes"], ["articles"])

    def test_describe_plan_reads_as_one_line(self):
        plan = plan_sync(_site("about"), [_existing(""), _existing("careers")])
        self.assertEqual(
            describe_plan(plan), "Site has 2 pages · 1 to update, 1 to create, 1 to archive"
        )
        self.assertEqual(
            describe_plan(plan_sync(_site(), [])),
            "Site has no pages yet — first push · 1 to create",
        )


class ExistingPagesTest(unittest.TestCase):
    def test_rows_are_read_and_the_idless_dropped(self):
        rows = [
            {"id": 7, "slug": "about", "title": "About", "status": "published"},
            {"id": "t", "slug": "article-template", "title": "T", "status": "draft", "templateFor": "article"},
            {"slug": "ghost", "title": "No id"},
            {"id": "h", "slug": "", "title": "Home", "status": "archived", "isHomepage": True},
        ]

        pages = existing_pages(rows)

        self.assertEqual([p.id for p in pages], ["7", "t", "h"])
        self.assertTrue(pages[1].is_template)
        self.assertFalse(pages[0].is_template)
        self.assertTrue(pages[2].archived)
        self.assertTrue(pages[2].is_homepage)


if __name__ == "__main__":
    unittest.main()
