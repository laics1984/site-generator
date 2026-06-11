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
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertEqual(labels, ["Services", "About", "Contact"])

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
    """No nav evidence — fall back to type weights, Contact gated on source."""

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

    def test_contact_from_source_is_included_last(self):
        menus = build_menus(
            _tree(
                PageNode(slug="services", title="Services", from_source=True),
                PageNode(slug="contact", title="Contact", from_source=True),
                PageNode(slug="about", title="About", from_source=True),
            )
        )
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertEqual(labels[-1], "Contact")

    def test_template_injected_contact_is_hidden_when_source_lacks_one(self):
        # Source evidence exists (crawled pages) but no contact page among it —
        # the template-injected Contact stays out of the header.
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

    def test_contact_shown_when_there_is_no_source_evidence_at_all(self):
        # Doc uploads / thin crawls: no evidence either way — convention wins.
        menus = build_menus(
            [
                PageNode(slug="", title="Home", is_homepage=True),
                PageNode(slug="services", title="Services"),
                PageNode(slug="contact", title="Contact"),
            ]
        )
        labels = [i["label"] for i in _primary(menus)["items"]]
        self.assertEqual(labels[-1], "Contact")

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
