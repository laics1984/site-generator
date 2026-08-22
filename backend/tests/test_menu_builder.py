import unittest

from app.models.builder_schema import PageNode
from app.services.menu_builder import MAX_PRIMARY_ITEMS, build_menus


def _tree(*nodes: PageNode) -> list[PageNode]:
    return [PageNode(slug="", title="Home", is_homepage=True, from_source=True), *nodes]


def _primary(menus):
    return next(m for m in menus if m["id"] == "menu-primary")


def _footer(menus):
    return next((m for m in menus if m["id"] == "menu-footer"), None)


class NavCuratedPrimaryMenuTest(unittest.TestCase):
    """When the source nav was captured, it is authoritative — verbatim."""

    def test_source_nav_order_is_used_verbatim(self):
        menus = build_menus(
            _tree(
                PageNode(slug="about", title="About", nav_rank=1, from_source=True),
                PageNode(slug="services", title="Services", nav_rank=0, from_source=True),
                PageNode(slug="contact", title="Contact", nav_rank=2, from_source=True),
            )
        )
        # Contact is left out even when the owner ranked it — the header's own
        # "Get in touch" CTA already routes there.
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertEqual(labels, ["Services", "About"])
        footer_labels = {i["label"] for i in _footer(menus)["items"]}
        self.assertIn("Contact", footer_labels)

    def test_home_is_never_a_menu_item(self):
        menus = build_menus(
            _tree(PageNode(slug="about", title="About", nav_rank=0, from_source=True))
        )
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertNotIn("Home", labels)

    def test_pages_outside_the_source_nav_are_not_added(self):
        # The owner curated their header; template-injected Contact stays out.
        menus = build_menus(
            _tree(
                PageNode(slug="get-involved", title="Get Involved", nav_rank=5, from_source=True),
                PageNode(slug="blog", title="Blog", nav_rank=3, from_source=True),
                PageNode(slug="contact", title="Contact"),  # not in source nav
            )
        )
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertEqual(labels, ["Blog", "Get Involved"])
        footer_labels = {i["label"] for i in _footer(menus)["items"]}
        self.assertIn("Contact", footer_labels)

    def test_nav_curated_is_still_capped_with_overflow_in_footer(self):
        nodes = [
            PageNode(slug=f"page-{i}", title=f"Page {i}", nav_rank=i, from_source=True)
            for i in range(10)
        ]
        menus = build_menus(_tree(*nodes))
        primary = _primary(menus)["items"]
        self.assertEqual(len(primary), MAX_PRIMARY_ITEMS)
        self.assertEqual(primary[0]["label"], "Page 0")
        footer_labels = {i["label"] for i in _footer(menus)["items"]}
        self.assertIn("Page 9", footer_labels)

    def test_primary_parent_carries_one_level_of_children(self):
        menus = build_menus(
            _tree(
                PageNode(
                    slug="services",
                    title="Services",
                    nav_rank=0,
                    from_source=True,
                    children=[
                        PageNode(
                            slug="web-design",
                            title="Web Design",
                            # Grandchildren must never reach the dropdown.
                            children=[PageNode(slug="web-design/landing", title="Landing")],
                        )
                    ],
                ),
            )
        )
        services = next(
            i for i in _primary(menus)["items"] if i["label"] == "Services"
        )
        self.assertEqual([c["label"] for c in services["children"]], ["Web Design"])
        self.assertNotIn("children", services["children"][0])

    def test_dropdown_children_are_capped(self):
        menus = build_menus(
            _tree(
                PageNode(
                    slug="services",
                    title="Services",
                    nav_rank=0,
                    from_source=True,
                    children=[
                        PageNode(slug=f"svc-{i}", title=f"Svc {i}") for i in range(12)
                    ],
                ),
            )
        )
        services = next(
            i for i in _primary(menus)["items"] if i["label"] == "Services"
        )
        self.assertEqual(len(services["children"]), 8)


class HeuristicPrimaryMenuTest(unittest.TestCase):
    """No nav evidence — fall back to type weights; Contact always excluded."""

    def test_unranked_pages_fall_back_to_type_weight(self):
        menus = build_menus(
            _tree(
                PageNode(slug="testimonials", title="Testimonials", from_source=True),
                PageNode(slug="services", title="Services", from_source=True),
                PageNode(slug="about", title="About", from_source=True),
            )
        )
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertEqual(labels, ["Services", "About", "Testimonials"])

    def test_contact_from_source_is_excluded_from_primary(self):
        # Even a source-evidenced Contact page stays out of the header — the
        # "Get in touch" CTA already routes there. It still gets a footer column.
        menus = build_menus(
            _tree(
                PageNode(slug="services", title="Services", from_source=True),
                PageNode(slug="contact", title="Contact", from_source=True),
                PageNode(slug="about", title="About", from_source=True),
            )
        )
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertNotIn("Contact", labels)
        footer_labels = {i["label"] for i in _footer(menus)["items"]}
        self.assertIn("Contact", footer_labels)

    def test_template_injected_contact_is_also_hidden(self):
        # Source evidence exists (crawled pages) but no contact page among it —
        # the template-injected Contact stays out of the header too.
        menus = build_menus(
            _tree(
                PageNode(slug="services", title="Services", from_source=True),
                PageNode(slug="about", title="About", from_source=True),
                PageNode(slug="contact", title="Contact"),  # template-injected
            )
        )
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertNotIn("Contact", labels)
        footer_labels = {i["label"] for i in _footer(menus)["items"]}
        self.assertIn("Contact", footer_labels)

    def test_contact_excluded_when_there_is_no_source_evidence_at_all(self):
        # Doc uploads / thin crawls: no evidence either way — Contact still
        # stays out of the header, the CTA covers it.
        menus = build_menus(
            [
                PageNode(slug="", title="Home", is_homepage=True),
                PageNode(slug="services", title="Services"),
                PageNode(slug="contact", title="Contact"),
            ]
        )
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertNotIn("Contact", labels)

    def test_heuristic_primary_is_capped(self):
        nodes = [
            PageNode(slug=f"page-{i}", title=f"Page {i}", from_source=True)
            for i in range(10)
        ]
        menus = build_menus(_tree(*nodes))
        self.assertLessEqual(len(_primary(menus)["items"]), MAX_PRIMARY_ITEMS)

    def test_leaf_primary_items_have_no_children_key(self):
        menus = build_menus(
            _tree(PageNode(slug="about", title="About", from_source=True))
        )
        about = next(i for i in _primary(menus)["items"] if i["label"] == "About")
        self.assertNotIn("children", about)

    def test_social_links_become_menu_social_with_new_tab_targets(self):
        menus = build_menus(
            _tree(PageNode(slug="about", title="About", from_source=True)),
            social_links=[
                ("Facebook", "https://facebook.com/acme"),
                ("Instagram", "https://instagram.com/acme"),
            ],
        )
        social = next(m for m in menus if m["id"] == "menu-social")
        self.assertEqual(social["purpose"], "social")
        self.assertEqual(
            [(i["label"], i["href"], i["target"]) for i in social["items"]],
            [
                ("Facebook", "https://facebook.com/acme", "_blank"),
                ("Instagram", "https://instagram.com/acme", "_blank"),
            ],
        )

    def test_no_social_links_means_no_social_menu(self):
        menus = build_menus(
            _tree(PageNode(slug="about", title="About", from_source=True))
        )
        self.assertIsNone(next((m for m in menus if m["id"] == "menu-social"), None))

    def test_legal_pages_stay_out_of_primary(self):
        menus = build_menus(
            _tree(
                PageNode(slug="privacy", title="Privacy"),
                PageNode(slug="terms", title="Terms"),
            ),
            legal_pages=[("Privacy", "/privacy"), ("Terms", "/terms")],
        )
        self.assertEqual(_primary(menus)["items"], [])
        legal = next(m for m in menus if m["id"] == "menu-legal")
        self.assertEqual(len(legal["items"]), 2)


if __name__ == "__main__":
    unittest.main()


class LanguageSwitcherTest(unittest.TestCase):
    """Translated pages belong in the switcher, not in the content menus."""

    @staticmethod
    def _multilingual() -> list[PageNode]:
        return _tree(
            PageNode(slug="committee", title="Committee", nav_rank=0, from_source=True),
            PageNode(
                slug="bm",
                title="Home (Bahasa Malaysia)",
                locale="bm",
                translation_of="",
                from_source=True,
            ),
            PageNode(
                slug="bm/committee",
                title="Committee (Bahasa Malaysia)",
                locale="bm",
                translation_of="committee",
                from_source=True,
            ),
            PageNode(
                slug="zh",
                title="Home (中文)",
                locale="zh",
                translation_of="",
                from_source=True,
            ),
        )

    def _utility(self, menus):
        return next((m for m in menus if m["id"] == "menu-utility"), None)

    def test_translations_stay_out_of_the_primary_menu(self):
        menus = build_menus(self._multilingual())
        labels = [i["label"] for i in _primary(menus)["items"]]

        self.assertEqual(labels, ["Committee"])

    def test_translations_stay_out_of_the_footer(self):
        menus = build_menus(self._multilingual())
        footer = _footer(menus)
        labels = [i["label"] for i in (footer["items"] if footer else [])]

        self.assertNotIn("Committee (Bahasa Malaysia)", labels)
        self.assertNotIn("Home (中文)", labels)

    def test_switcher_lists_one_entry_per_language(self):
        menus = build_menus(self._multilingual())
        utility = self._utility(menus)

        self.assertIsNotNone(utility)
        self.assertEqual(
            [(i["label"], i["href"]) for i in utility["items"]],
            [("English", "/"), ("Bahasa Malaysia", "/bm"), ("中文", "/zh")],
        )

    def test_switcher_points_at_the_language_home_not_an_inner_page(self):
        # /bm/committee comes first in the tree; the switcher must still land
        # the reader on the Malay homepage.
        tree = _tree(
            PageNode(
                slug="bm/committee",
                title="Committee (BM)",
                locale="bm",
                translation_of="committee",
            ),
            PageNode(slug="bm", title="Home (BM)", locale="bm", translation_of=""),
        )
        utility = self._utility(build_menus(tree))

        self.assertEqual([i["href"] for i in utility["items"]], ["/", "/bm"])

    def test_a_language_with_only_inner_pages_still_gets_an_entry(self):
        tree = _tree(
            PageNode(
                slug="bm/committee",
                title="Committee (BM)",
                locale="bm",
                translation_of="committee",
            ),
        )
        utility = self._utility(build_menus(tree))

        self.assertEqual([i["href"] for i in utility["items"]], ["/", "/bm/committee"])

    def test_a_monolingual_site_gets_no_switcher(self):
        menus = build_menus(
            _tree(PageNode(slug="committee", title="Committee", from_source=True))
        )

        self.assertIsNone(self._utility(menus))


class ListingReachedPagesTest(unittest.TestCase):
    """Pages the source reaches from a grid, not from a menu.

    MMTA's nine committee members each have a page, linked from the committee
    grid. They belong in the tree — breadcrumbs are real — but the source has
    no nine-item Committee dropdown and no nine-row footer column, so neither
    should the generated site.
    """

    def _committee(self, *, hidden: bool) -> PageNode:
        return PageNode(
            slug="committee",
            title="Committee",
            nav_rank=0,
            from_source=True,
            children=[
                PageNode(
                    slug=f"profile/{slug}",
                    title=name,
                    from_source=True,
                    menu_hidden=hidden,
                )
                for name, slug in (("Ashley Jinivon", "ashley"), ("Sandra Cheah", "sandra"))
            ],
        )

    def test_hidden_children_are_in_neither_menu(self):
        menus = build_menus(_tree(self._committee(hidden=True)))

        committee = next(
            i for i in _primary(menus)["items"] if i["label"] == "Committee"
        )
        self.assertIsNone(committee.get("children"))

        footer_committee = next(
            i for i in _footer(menus)["items"] if i["label"] == "Committee"
        )
        self.assertIsNone(footer_committee.get("children"))

    def test_ordinary_children_still_nest(self):
        # The carve-out is opt-in: a normal section keeps its dropdown.
        menus = build_menus(_tree(self._committee(hidden=False)))

        committee = next(
            i for i in _primary(menus)["items"] if i["label"] == "Committee"
        )
        self.assertEqual(
            [c["label"] for c in committee["children"]],
            ["Ashley Jinivon", "Sandra Cheah"],
        )

    def test_a_hidden_page_left_at_top_level_stays_reachable(self):
        # Its roster wasn't generated, so the footer is the only way in.
        orphan = PageNode(
            slug="profile/ashley", title="Ashley Jinivon", from_source=True, menu_hidden=True
        )

        menus = build_menus(_tree(orphan))

        self.assertIn(
            "Ashley Jinivon", [i["label"] for i in _footer(menus)["items"]]
        )
