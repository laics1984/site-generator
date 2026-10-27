"""A header that cannot hold its pages groups the related ones; one that can is
left exactly as it was (services/nav_groups.py, menu_builder.plan_nav_groups).

Every scenario also asserts the two properties no grouping may break: every
page the menus listed before is still listed (header or footer), and no menu
lists one page twice.
"""

import asyncio
import unittest

from app.models.builder_schema import GeneratedSite, NavGroup, PageNode
from app.models.content_blocks import HeroBlock, PagePlan, SitePlan
from app.services.cms_sync import normalize_site_slugs
from app.services.menu_builder import (
    MAX_PRIMARY_ITEMS,
    build_layout_payload,
    build_menus,
    has_menu_groups,
    plan_nav_groups,
    route_navigation,
)


def _page(slug, title, rank=None, children=(), **kwargs) -> PageNode:
    return PageNode(
        slug=slug, title=title, nav_rank=rank, from_source=True, children=list(children), **kwargs
    )


def _site(*nodes: PageNode) -> list[PageNode]:
    return [PageNode(slug="", title="Home", is_homepage=True, from_source=True), *nodes]


def _flat(*pairs, ranked=False) -> list[PageNode]:
    return _site(*(_page(s, t, i if ranked else None) for i, (s, t) in enumerate(pairs)))


def _menus(tree, industry, language="en"):
    groups = plan_nav_groups(tree, industry=industry, language=language)
    return groups, build_menus(tree, nav_groups=groups)


def _menu(menus, menu_id):
    return next((m for m in menus if m["id"] == menu_id), {"items": []})["items"]


def _shape(items):
    """[(label, href, [child labels])] — what a visitor sees, ids aside."""
    return [(i["label"], i["href"], [c["label"] for c in i.get("children") or []]) for i in items]


def _hrefs(items) -> list[str]:
    out = []
    for item in items:
        if item["href"]:
            out.append(item["href"])
        out.extend(_hrefs(item.get("children") or []))
    return out


KINDERGARTEN = [
    ("about", "About Us"), ("our-story", "Our Story"), ("teachers", "Our Teachers"),
    ("facilities", "Our Facilities"), ("curriculum", "Curriculum"), ("montessori", "Montessori"),
    ("activities", "Activities"), ("gallery", "Gallery"), ("admissions", "Admissions"),
    ("how-to-enrol", "How to Enrol"), ("fees", "Fees"), ("faq", "FAQ"), ("contact", "Contact"),
]

SAAS = [
    ("features", "Features"), ("integrations", "Integrations"), ("security", "Security"),
    ("small-business", "Small Businesses"), ("education", "Education"), ("healthcare", "Healthcare"),
    ("blog", "Blog"), ("case-studies", "Case Studies"), ("documentation", "Documentation"),
    ("faq", "FAQ"), ("pricing", "Pricing"), ("about", "About"), ("careers", "Careers"),
    ("team", "Team"), ("press", "Press"), ("changelog", "Changelog"), ("api", "API"),
    ("partners", "Partners"), ("webinars", "Webinars"), ("contact", "Contact"),
]


class InvariantsMixin:
    def assert_menus_intact(self, tree, menus):
        """Every page the flat menus listed is still listed; no menu repeats one."""
        flat = build_menus(tree)
        before = set(_hrefs(_menu(flat, "menu-primary"))) | set(_hrefs(_menu(flat, "menu-footer")))
        header, footer = _hrefs(_menu(menus, "menu-primary")), _hrefs(_menu(menus, "menu-footer"))
        self.assertEqual(len(header), len(set(header)), "the header lists a page twice")
        self.assertEqual(len(footer), len(set(footer)), "the footer lists a page twice")
        self.assertLessEqual(before, set(header) | set(footer))
        # The footer is complete: every page the flat footer had, it still has.
        self.assertLessEqual(set(_hrefs(_menu(flat, "menu-footer"))), set(footer))


class SmallSitesStayFlatTest(InvariantsMixin, unittest.TestCase):
    """Below the header's capacity nothing changes, byte for byte."""

    def test_a_three_page_site_is_flat(self):
        tree = _flat(("about", "About"), ("contact", "Contact"))
        groups, menus = _menus(tree, "agency")
        self.assertEqual(groups, [])
        self.assertEqual(_shape(_menu(menus, "menu-primary")), [("About", "/about", [])])

    def test_a_six_page_site_with_related_pages_is_still_flat(self):
        # About and Team are related, and a header of four holds them both: one
        # click beats a dropdown.
        tree = _flat(
            ("about", "About"), ("services", "Services"), ("team", "Team"),
            ("faq", "FAQ"), ("contact", "Contact"),
        )
        groups, menus = _menus(tree, "professional-services")
        self.assertEqual(groups, [])
        self.assertEqual(
            _shape(_menu(menus, "menu-primary")), _shape(_menu(build_menus(tree), "menu-primary"))
        )

    def test_an_owner_nav_that_fits_is_verbatim(self):
        tree = _flat(
            ("about", "About"), ("team", "Team"), ("services", "Services"), ("blog", "Blog"),
            ("faq", "FAQ"), ("gallery", "Gallery"), ("careers", "Careers"), ranked=True,
        )
        groups, menus = _menus(tree, "agency")
        self.assertEqual(groups, [])
        self.assertEqual(
            [label for label, _, _ in _shape(_menu(menus, "menu-primary"))],
            ["About", "Team", "Services", "Blog", "FAQ", "Gallery", "Careers"],
        )


class KindergartenTest(InvariantsMixin, unittest.TestCase):
    """The brief's own example: twelve pages, four categories' worth."""

    def setUp(self):
        self.tree = _flat(*KINDERGARTEN)
        self.groups, self.menus = _menus(self.tree, "childcare")

    def test_related_pages_group_under_meaningful_headings(self):
        header = _shape(_menu(self.menus, "menu-primary"))
        self.assertIn(("About Us", "/about", ["Our Story", "Our Teachers", "Our Facilities"]), header)
        self.assertIn(("Programmes", "", ["Curriculum", "Montessori", "Activities"]), header)
        self.assertIn(("Admissions", "/admissions", ["How to Enrol", "Fees", "FAQ"]), header)
        self.assertIn(("Gallery", "/gallery", []), header)
        self.assertLessEqual(len(header), MAX_PRIMARY_ITEMS)
        self.assert_menus_intact(self.tree, self.menus)

    def test_a_heading_that_is_no_page_has_no_href(self):
        programmes = next(g for g in self.groups if g.label == "Programmes")
        self.assertIsNone(programmes.slug)
        item = next(i for i in _menu(self.menus, "menu-primary") if i["label"] == "Programmes")
        self.assertEqual(item["href"], "")

    def test_the_footer_shows_the_same_categories_as_columns(self):
        footer = _shape(_menu(self.menus, "menu-footer"))
        self.assertIn(("Programmes", "", ["Curriculum", "Montessori", "Activities"]), footer)
        # Contact is never filed under a heading: the header's CTA owns it there,
        # and the footer keeps it on its own.
        self.assertIn(("Contact", "/contact", []), footer)
        self.assertTrue(has_menu_groups(self.tree, self.groups))

    def test_no_page_changes_its_url(self):
        for group in self.groups:
            for slug in (group.slug, *group.members):
                if slug is not None:
                    self.assertIn(slug, {s for s, _ in KINDERGARTEN})

    def test_header_and_footer_run_in_one_order_on_a_real_tree(self):
        # _build_page_tree sorts an unranked site's pages by slug, so the footer
        # arrives alphabetical: "admissions" before "curriculum", "contact" in
        # the middle. A category ranks as what it is (Programmes with the
        # services, Admissions where Pricing goes), in both menus.
        tree = _site(*sorted((_page(s, t) for s, t in KINDERGARTEN), key=lambda n: n.slug))
        _, menus = _menus(tree, "childcare")
        header = [label for label, _, _ in _shape(_menu(menus, "menu-primary"))]
        footer = [label for label, _, _ in _shape(_menu(menus, "menu-footer"))]
        self.assertEqual(header, ["Programmes", "Admissions", "About Us", "Gallery"])
        self.assertEqual(footer, [*header, "Contact"])

    def test_a_flat_footer_keeps_the_order_it_had(self):
        tree = _site(*sorted((_page(s, t) for s, t in KINDERGARTEN[:6]), key=lambda n: n.slug))
        self.assertEqual(
            _shape(_menu(_menus(tree, "childcare")[1], "menu-footer")),
            _shape(_menu(build_menus(tree), "menu-footer")),
        )

    def test_a_site_spelling_programs_gets_programs(self):
        tree = _flat(*KINDERGARTEN[:4], ("infant-program", "Infant Program"),
                     ("toddler-program", "Toddler Program"), *KINDERGARTEN[7:])
        groups, _ = _menus(tree, "childcare")
        self.assertIn("Programs", [g.label for g in groups])


class SaasTest(InvariantsMixin, unittest.TestCase):
    def setUp(self):
        self.tree = _flat(*SAAS)
        self.groups, self.menus = _menus(self.tree, "saas")
        self.header = _shape(_menu(self.menus, "menu-primary"))

    def test_twenty_pages_become_a_manageable_header(self):
        self.assertLessEqual(len(self.header), MAX_PRIMARY_ITEMS)
        self.assertIn(("Features", "/features", ["Integrations", "Security", "API"]), self.header)
        self.assertIn(("Solutions", "", ["Small Businesses", "Education", "Healthcare"]), self.header)
        resources = next(children for label, _, children in self.header if label == "Resources")
        self.assertEqual(resources[:4], ["Blog", "Press", "Case Studies", "Documentation"])
        self.assertEqual(resources[-1], "FAQ")
        self.assert_menus_intact(self.tree, self.menus)

    def test_pricing_stays_a_top_level_link(self):
        self.assertIn(("Pricing", "/pricing", []), self.header)

    def test_platform_and_audience_families_are_saas_only(self):
        tree = _flat(
            ("security", "Security Systems"), ("education", "Education"), ("features", "Features"),
            ("cctv", "CCTV"), ("alarms", "Alarms"), ("gates", "Gates"), ("locks", "Locks"),
            ("intercoms", "Intercoms"),
        )
        groups, _ = _menus(tree, "home-services")
        self.assertEqual(groups, [])


class OwnerStructureTest(InvariantsMixin, unittest.TestCase):
    """What the source's own nav decided outranks the vocabulary."""

    def setUp(self):
        self.tree = _site(
            _page("about", "About", 0),
            _page("programmes", "Programmes", 1, [
                _page("programmes/infant", "Infant Group"), _page("programmes/kinder", "Kinder Group"),
            ]),
            _page("activities", "Activities", 2), _page("montessori", "Montessori", 3),
            _page("gallery", "Gallery", 4), _page("blog", "Blog", 5), _page("events", "Events", 6),
            _page("team", "Our Team", 7), _page("career", "Job Vacancy", 8),
            _page("contact", "Contact", 9), _page("seo-article", "Kindergarten in Kepong"),
        )
        self.groups, self.menus = _menus(self.tree, "childcare")
        self.header = _shape(_menu(self.menus, "menu-primary"))

    def test_an_existing_dropdown_keeps_its_children_first_and_in_order(self):
        self.assertIn(
            ("Programmes", "/programmes", ["Infant Group", "Kinder Group", "Activities", "Montessori"]),
            self.header,
        )

    def test_the_owners_order_and_labels_are_kept(self):
        self.assertEqual(
            [label for label, _, _ in self.header], ["About", "Programmes", "Gallery", "Resources"]
        )
        self.assertIn(("About", "/about", ["Our Team", "Job Vacancy"]), self.header)
        self.assert_menus_intact(self.tree, self.menus)

    def test_a_page_the_owner_left_out_of_their_nav_stays_out_of_the_header(self):
        self.assertNotIn("/seo-article", _hrefs(_menu(self.menus, "menu-primary")))
        self.assertIn("/seo-article", _hrefs(_menu(self.menus, "menu-footer")))

    def test_a_page_with_children_is_never_filed_under_another_heading(self):
        tree = _flat(*KINDERGARTEN)
        tree[2] = _page("our-story", "Our Story", children=[_page("our-story/1990", "1990")])
        groups, _ = _menus(tree, "childcare")
        self.assertNotIn("our-story", [s for g in groups for s in g.members])

    def test_hidden_and_translated_pages_join_no_category(self):
        tree = _flat(*KINDERGARTEN)
        tree.append(_page("bm/about", "Tentang Kami", locale="ms", translation_of="about"))
        tree.append(_page("profile-ashley", "Ashley", menu_hidden=True))
        groups, menus = _menus(tree, "childcare")
        members = {s for g in groups for s in g.members}
        self.assertNotIn("bm/about", members)
        self.assertNotIn("/bm/about", _hrefs(_menu(menus, "menu-primary")))


class NoForcedGroupingTest(unittest.TestCase):
    def test_unrelated_pages_are_left_alone_and_capped_as_before(self):
        tree = _flat(
            ("info-cards", "Info Cards"), ("membership", "Membership"), ("volunteer", "Volunteer"),
            ("shop", "Shop"), ("donate", "Donate"), ("map", "Find Us"), ("radio", "Radio"),
            ("gala", "Gala"), ("hall", "Hall Hire"),
        )
        groups, menus = _menus(tree, "nonprofit")
        self.assertEqual(groups, [])
        self.assertEqual(_menu(menus, "menu-primary"), _menu(menus, "menu-primary")[:MAX_PRIMARY_ITEMS])
        self.assertEqual(
            _shape(_menu(menus, "menu-primary")), _shape(_menu(build_menus(tree), "menu-primary"))
        )

    def test_one_page_is_no_category(self):
        tree = _flat(
            ("blog", "Blog"), ("info-cards", "Info Cards"), ("membership", "Membership"),
            ("volunteer", "Volunteer"), ("shop", "Shop"), ("donate", "Donate"),
            ("map", "Find Us"), ("radio", "Radio"),
        )
        groups, _ = _menus(tree, "nonprofit")
        self.assertEqual(groups, [])

    def test_a_head_with_one_member_is_a_category(self):
        tree = _flat(
            ("about", "About"), ("team", "Team"), ("info-cards", "Info Cards"),
            ("membership", "Membership"), ("volunteer", "Volunteer"), ("shop", "Shop"),
            ("donate", "Donate"), ("radio", "Radio"),
        )
        groups, _ = _menus(tree, "nonprofit")
        self.assertEqual(
            groups, [NavGroup(label="About", slug="about", members=["team"], kind="about")]
        )


class LanguageTest(unittest.TestCase):
    MALAY = [
        ("about", "Kenali Kami"), ("team", "Guru Kami"), ("facilities", "Kemudahan"),
        ("curriculum", "Kurikulum"), ("activities", "Aktiviti"), ("montessori", "Montessori"),
        ("gallery", "Galeri"), ("fees", "Yuran"), ("faq", "Soalan Lazim"),
    ]

    def test_a_heading_is_never_written_in_english_over_another_language(self):
        groups, _ = _menus(_flat(*self.MALAY), "childcare", language="ms")
        self.assertTrue(groups)
        self.assertTrue(all(g.slug is not None for g in groups))
        # The heading is the owner's own page title, in the site's language.
        self.assertEqual(groups[0].label, "Kenali Kami")

    def test_an_english_site_may_write_one(self):
        groups, _ = _menus(_flat(*self.MALAY), "childcare", language="en-GB")
        self.assertIn(None, [g.slug for g in groups])


class RoundTripTest(unittest.TestCase):
    """A generated site is re-crawled on update. Its header is read back as a
    flat run of links in document order (a heading that is no page is a button,
    not a link), every one ranked — and the same categories must come back."""

    def test_the_published_header_regroups_to_itself(self):
        tree = _flat(*KINDERGARTEN)
        _, menus = _menus(tree, "childcare")
        header = _menu(menus, "menu-primary")
        read_back = [
            (item["href"].lstrip("/"), item["label"])
            for top in header
            for item in ([top] if top["href"] else []) + (top.get("children") or [])
        ]
        crawled = _site(
            *(_page(slug, label, rank) for rank, (slug, label) in enumerate(read_back)),
            _page("contact", "Contact"),
        )
        _, again = _menus(crawled, "childcare")
        self.assertEqual(_shape(_menu(again, "menu-primary")), _shape(header))


class DegradeTest(unittest.TestCase):
    """Stored categories never invent: what they name must still be there."""

    def tree(self):
        return _flat(("about", "About"), ("team", "Team"), ("blog", "Blog"), ("faq", "FAQ"))

    def test_a_missing_head_leaves_its_members_standing_alone(self):
        menus = build_menus(
            self.tree(), nav_groups=[NavGroup(label="Company", slug="gone", members=["team"])]
        )
        self.assertIn(("Team", "/team", []), _shape(_menu(menus, "menu-primary")))

    def test_a_heading_left_with_one_member_is_no_heading(self):
        menus = build_menus(
            self.tree(), nav_groups=[NavGroup(label="Resources", members=["blog", "gone"])]
        )
        self.assertNotIn("Resources", [i["label"] for i in _menu(menus, "menu-primary")])

    def test_a_page_named_by_two_categories_is_listed_once(self):
        menus = build_menus(self.tree(), nav_groups=[
            NavGroup(label="About", slug="about", members=["team", "faq"]),
            NavGroup(label="Resources", members=["blog", "faq"]),
        ])
        for menu_id in ("menu-primary", "menu-footer"):
            hrefs = _hrefs(_menu(menus, menu_id))
            self.assertEqual(len(hrefs), len(set(hrefs)))


def _plan_page(slug, title, **kwargs) -> PagePlan:
    return PagePlan(
        page_type="home" if not slug else "services", slug=slug, title=title,
        blocks=[HeroBlock(headline=title, image_query=None)],
        seo_title=title, seo_description=title, **kwargs,
    )


class GenerationTest(unittest.TestCase):
    """Decided once at generation, stored on the site, read by every menu."""

    @classmethod
    def setUpClass(cls):
        from app.services.schema_builder import plan_to_site

        plan = SitePlan(
            site_name="Little Acorns", brand_mood="friendly", industry_category="childcare",
            pages=[
                _plan_page("", "Home", is_homepage=True),
                *(_plan_page(slug, title) for slug, title in KINDERGARTEN),
            ],
        )
        cls.site = asyncio.run(plan_to_site(plan, footer_override="mega"))

    def test_the_site_carries_its_categories(self):
        self.assertIn("Programmes", [g.label for g in self.site.nav_groups])

    def test_the_footer_keeps_the_grid_its_columns_need(self):
        self.assertEqual(self.site.design_manifest["footer_archetype"], "mega")
        self.assertTrue(has_menu_groups(self.site.page_tree, self.site.nav_groups))

    def test_the_layout_payload_reads_them_after_a_round_trip(self):
        site = GeneratedSite.model_validate_json(self.site.model_dump_json())
        header = _shape(_menu(build_layout_payload(site).menus, "menu-primary"))
        self.assertIn(("Programmes", "", ["Curriculum", "Montessori", "Activities"]), header)

    def test_a_written_heading_is_translated_like_every_other_menu_label(self):
        # Secondary languages translate menu labels by text (translations
        # .menu_labels → menu_label_translations); the heading must be among them.
        from app.services.translations import menu_labels

        self.assertIn("Programmes", menu_labels(self.site))

    def test_a_renamed_slug_carries_its_category_with_it(self):
        site = GeneratedSite.model_validate_json(self.site.model_dump_json())
        site.pages[next(i for i, p in enumerate(site.pages) if p.slug == "how-to-enrol")].slug = "How To Enrol!"
        for node in site.page_tree:
            if node.slug == "how-to-enrol":
                node.slug = "How To Enrol!"
        for group in site.nav_groups:
            group.members = ["How To Enrol!" if s == "how-to-enrol" else s for s in group.members]
        normalize_site_slugs(site)
        admissions = next(g for g in site.nav_groups if g.slug == "admissions")
        self.assertIn("how-to-enrol", admissions.members)
        header = _shape(_menu(build_layout_payload(site).menus, "menu-primary"))
        self.assertIn(("Admissions", "/admissions", ["How to Enrol", "Fees", "FAQ"]), header)


class RoutePlanTest(unittest.TestCase):
    """A two-page preview's frozen route plan groups exactly as a full build."""

    def test_the_route_plan_folds_the_same_categories(self):
        tree = _flat(*KINDERGARTEN)
        links = route_navigation(tree, industry="childcare", language="en")
        self.assertEqual(links[0].href, "/")  # the route plan keeps Home, as it always has
        programmes = next(link for link in links if link.label == "Programmes")
        self.assertEqual(programmes.href, "")
        self.assertEqual([c.label for c in programmes.children], ["Curriculum", "Montessori", "Activities"])
        self.assertIn("/contact", [link.href for link in links])

    def test_a_route_plan_that_fits_is_unchanged(self):
        tree = _flat(("about", "About"), ("team", "Team"), ("contact", "Contact"))
        links = route_navigation(tree, industry="agency", language="en")
        self.assertEqual([link.href for link in links], ["/", "/about", "/team", "/contact"])


if __name__ == "__main__":
    unittest.main()
