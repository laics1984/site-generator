import unittest

from app.models.content_blocks import LinkCluster, NavLink, SourceContent
from app.services.page_inference import infer_page_scaffolds


def _page(path: str, title: str, text: str = "Some page text.") -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref=f"https://example.com{path}",
        title=title,
        raw_text=text,
        url_path=path,
    )


def _source(nav_links=None, discovered=None, clusters=None) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref="https://example.com",
        title="Acme",
        raw_text="Homepage text for Acme with plenty of words.",
        nav_links=nav_links or [],
        discovered_pages=discovered or [],
        body_link_clusters=clusters or [],
    )


class NavNestingTest(unittest.TestCase):
    def test_dropdown_children_nest_under_parent_despite_flat_urls(self):
        source = _source(
            nav_links=[
                NavLink(label="Home", href="/"),
                NavLink(
                    label="Services",
                    href="/services",
                    children=[
                        NavLink(label="Web Design", href="/web-design"),
                        NavLink(label="SEO", href="/seo"),
                    ],
                ),
                NavLink(label="Contact", href="/contact"),
            ],
            discovered=[
                _page("/services", "Services"),
                _page("/web-design", "Web Design"),
                _page("/seo", "SEO"),
                _page("/contact", "Contact"),
            ],
        )
        scaffolds = infer_page_scaffolds(source, industry="agency")
        web = next(s for s in scaffolds if s.slug == "web-design")
        seo = next(s for s in scaffolds if s.slug == "seo")
        self.assertEqual(web.parent_slug, "services")
        self.assertEqual(seo.parent_slug, "services")

    def test_unlinked_dropdown_parent_synthesizes_hub_page(self):
        source = _source(
            nav_links=[
                NavLink(
                    label="Practice Areas",
                    href="#",
                    children=[
                        NavLink(label="Family Law", href="/family-law"),
                        NavLink(label="Corporate", href="/corporate"),
                    ],
                ),
            ],
            discovered=[
                _page("/family-law", "Family Law"),
                _page("/corporate", "Corporate"),
            ],
        )
        scaffolds = infer_page_scaffolds(source, industry="professional-services")
        hub = next((s for s in scaffolds if s.slug == "practice-areas"), None)
        self.assertIsNotNone(hub)
        family = next(s for s in scaffolds if s.slug == "family-law")
        self.assertEqual(family.parent_slug, "practice-areas")

    def test_nav_rank_follows_source_nav_order(self):
        source = _source(
            nav_links=[
                NavLink(label="Home", href="/"),
                NavLink(label="Menu", href="/menu"),
                NavLink(label="About", href="/about"),
            ],
            discovered=[
                _page("/about", "About"),
                _page("/menu", "Menu"),
            ],
        )
        scaffolds = infer_page_scaffolds(source, industry="restaurant")
        menu = next(s for s in scaffolds if s.slug == "menu")
        about = next(s for s in scaffolds if s.slug == "about")
        self.assertEqual(menu.nav_rank, 0)
        self.assertEqual(about.nav_rank, 1)

    def test_nav_page_missed_by_crawl_is_scaffolded(self):
        source = _source(
            nav_links=[
                NavLink(label="Home", href="/"),
                NavLink(label="Gallery", href="/gallery"),
            ],
            discovered=[_page("/about", "About")],
        )
        scaffolds = infer_page_scaffolds(source, industry="other")
        gallery = next((s for s in scaffolds if s.slug == "gallery"), None)
        self.assertIsNotNone(gallery)
        self.assertEqual(gallery.page_type, "gallery")
        self.assertEqual(gallery.nav_rank, 0)

    def test_anchor_only_nav_items_do_not_become_pages(self):
        source = _source(
            nav_links=[
                NavLink(label="Pricing", href="/#pricing"),
                NavLink(label="About", href="/about"),
            ],
            discovered=[_page("/about", "About")],
        )
        scaffolds = infer_page_scaffolds(source, industry="saas")
        self.assertIsNone(next((s for s in scaffolds if s.slug == "pricing"), None))

    def test_nav_label_beats_page_title_for_scaffold_title(self):
        # Sass-style sites prefix every <title> with the brand ("Sass: Install
        # Sass") while the nav says just "Install" — the nav label must win.
        source = _source(
            nav_links=[
                NavLink(label="Install", href="/install"),
                NavLink(label="Learn Sass", href="/guide"),
            ],
            discovered=[
                _page("/install", "Sass: Install Sass"),
                _page("/guide", "Sass: Sass Basics"),
            ],
        )
        scaffolds = infer_page_scaffolds(
            source, industry="other", site_name="Sass"
        )
        install = next(s for s in scaffolds if s.slug == "install")
        guide = next(s for s in scaffolds if s.slug == "guide")
        self.assertEqual(install.title, "Install")
        self.assertEqual(guide.title, "Learn Sass")

    def test_brand_prefix_stripped_from_titles_of_non_nav_pages(self):
        source = _source(
            discovered=[_page("/playground", "Sass: Playground")],
        )
        scaffolds = infer_page_scaffolds(
            source, industry="other", site_name="Sass"
        )
        playground = next(s for s in scaffolds if s.slug == "playground")
        self.assertEqual(playground.title, "Playground")

    def test_template_fallback_still_gets_nav_ranks(self):
        source = _source(
            nav_links=[
                NavLink(label="About", href="/about"),
                NavLink(label="Contact", href="/contact"),
            ],
        )
        scaffolds = infer_page_scaffolds(source, industry="other")
        about = next((s for s in scaffolds if s.slug == "about"), None)
        self.assertIsNotNone(about)
        self.assertEqual(about.nav_rank, 0)


def _strip_cluster() -> LinkCluster:
    return LinkCluster(
        links=[
            NavLink(label="Web Design", href="/web-design"),
            NavLink(label="SEO", href="/seo"),
            NavLink(label="Branding", href="/branding"),
        ],
        href_key="cafebabe",
    )


class BodyClusterInferenceTest(unittest.TestCase):
    def test_self_referencing_repeated_strip_nests_under_listing_page(self):
        cluster = _strip_cluster()
        web = _page("/web-design", "Web Design")
        web.body_link_clusters = [cluster]
        seo = _page("/seo", "SEO")
        seo.body_link_clusters = [cluster]
        services = _page("/services", "Services")
        services.links = [
            "https://example.com/web-design",
            "https://example.com/seo",
            "https://example.com/branding",
        ]
        source = _source(
            nav_links=[NavLink(label="Services", href="/services")],
            discovered=[
                services,
                web,
                seo,
                _page("/branding", "Branding"),
            ],
        )
        scaffolds = infer_page_scaffolds(source, industry="agency")
        for slug in ("web-design", "seo", "branding"):
            child = next(s for s in scaffolds if s.slug == slug)
            self.assertEqual(child.parent_slug, "services", f"{slug} should nest")

    def test_strip_duplicating_header_nav_is_ignored(self):
        cluster = LinkCluster(
            links=[
                NavLink(label="About", href="/about"),
                NavLink(label="Team", href="/team"),
                NavLink(label="Contact", href="/contact"),
            ],
            href_key="deadbeef",
        )
        about = _page("/about", "About")
        about.body_link_clusters = [cluster]
        team = _page("/team", "Team")
        team.body_link_clusters = [cluster]
        source = _source(
            nav_links=[
                NavLink(label="About", href="/about"),
                NavLink(label="Team", href="/team"),
                NavLink(label="Contact", href="/contact"),
            ],
            discovered=[about, team, _page("/contact", "Contact")],
        )
        scaffolds = infer_page_scaffolds(source, industry="other")
        for slug in ("about", "team", "contact"):
            page = next(s for s in scaffolds if s.slug == slug)
            self.assertIsNone(page.parent_slug, f"{slug} should stay top-level")


if __name__ == "__main__":
    unittest.main()


class NavPlacementTest(unittest.TestCase):
    """Every page the source nav names records WHERE the owner's header shows
    it — read off the nav, never the URL. The tree keeps its URL hierarchy."""

    def _scaffolds(self):
        source = _source(
            nav_links=[
                NavLink(label="Home", href="/"),
                NavLink(
                    label="About Us",
                    href="/about-us",
                    children=[
                        NavLink(label="Our Story", href="/about-us/our-story"),
                        NavLink(label="Our Teachers", href="/team"),
                        NavLink(label="History", href="/company/history"),
                    ],
                ),
                NavLink(label="Admissions", href="/enrol/apply-now"),
                NavLink(label="Gallery", href="/gallery"),
            ],
            discovered=[
                _page(path, path)
                for path in (
                    "/about-us", "/about-us/our-story", "/team",
                    "/company/history", "/enrol/apply-now", "/gallery",
                )
            ],
        )
        return {s.slug: s for s in infer_page_scaffolds(source, industry="childcare")}

    def test_a_top_level_item_at_a_nested_url_keeps_its_header_place(self):
        admissions = self._scaffolds()["enrol/apply-now"]
        self.assertEqual(admissions.parent_slug, "enrol")  # the tree is the URL's
        self.assertEqual((admissions.nav_rank, admissions.nav_parent), (1, None))

    def test_a_dropdown_page_is_placed_in_its_dropdown_whatever_its_url(self):
        scaffolds = self._scaffolds()
        history = scaffolds["company/history"]
        self.assertEqual(history.parent_slug, "company")
        self.assertEqual((history.nav_rank, history.nav_parent), (2, "about-us"))

    def test_dropdown_positions_follow_the_owner_not_the_alphabet(self):
        scaffolds = self._scaffolds()
        self.assertEqual(
            [(s, scaffolds[s].nav_rank) for s in ("about-us/our-story", "team", "company/history")],
            [("about-us/our-story", 0), ("team", 1), ("company/history", 2)],
        )
        self.assertTrue(all(scaffolds[s].nav_parent == "about-us"
                            for s in ("about-us/our-story", "team", "company/history")))

    def test_pages_outside_the_nav_carry_no_placement(self):
        company = self._scaffolds()["company"]  # inferred from a URL, not in the nav
        self.assertEqual((company.nav_rank, company.nav_parent), (None, None))
