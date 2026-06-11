import unittest

from bs4 import BeautifulSoup

from app.models.content_blocks import (
    HeroBlock,
    LinkCluster,
    NavLink,
    PagePlan,
    SourceContent,
)
from app.routers.generate import _inject_linkbar
from app.services.nav_extraction import (
    extract_body_link_clusters,
    find_linkbar_cluster,
    strip_linkbar_lines,
)

BASE = "https://example.com/"

STRAP_HTML = """
<main>
  <p class="release-banner">Current Releases:
    <a href="/dart-sass">Dart Sass 1.100.0</a>
    <a href="/libsass">LibSass</a>
    <a href="/ruby-sass">Ruby Sass</a>
  </p>
  <p>Sass is the most mature, stable, and powerful CSS extension language.</p>
</main>
"""


def _source(clusters, raw_text="", discovered=None) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref="https://example.com",
        raw_text=raw_text,
        body_link_clusters=clusters,
        discovered_pages=discovered or [],
    )


class LinkbarDetectionTest(unittest.TestCase):
    def test_strap_with_context_label_is_detected(self):
        soup = BeautifulSoup(STRAP_HTML, "lxml")
        clusters = extract_body_link_clusters(soup, BASE)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].context_label, "Current Releases")

        found = find_linkbar_cluster(_source(clusters))
        self.assertIsNotNone(found)
        self.assertEqual(len(found.links), 3)

    def test_real_world_strap_with_external_link_and_emoji(self):
        # Mirrors sass-lang.com's release banner: the version number is a
        # separate EXTERNAL anchor (counts toward the link ratio, excluded
        # from the cluster) and coffin emojis decorate dead implementations
        # (must not pollute the context label).
        html = """
        <main><div class="alert"><ul>
          <li>Current Releases:</li>
          <li><a href="/dart-sass">Dart Sass</a>
              <a href="https://github.com/sass/dart-sass/releases/tag/1.100.0">1.100.0</a></li>
          <li><a href="/libsass">LibSass</a> <span>⚰</span></li>
          <li><a href="/ruby-sass">Ruby Sass</a> <span>⚰</span></li>
          <li><a href="/implementation">Implementation Guide</a></li>
        </ul></div></main>
        """
        clusters = extract_body_link_clusters(BeautifulSoup(html, "lxml"), BASE)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0].context_label, "Current Releases")
        self.assertEqual(
            [link.href for link in clusters[0].links],
            ["/dart-sass", "/libsass", "/ruby-sass", "/implementation"],
        )
        self.assertIsNotNone(find_linkbar_cluster(_source(clusters)))

    def test_repeated_chrome_cluster_is_not_a_linkbar(self):
        soup = BeautifulSoup(STRAP_HTML, "lxml")
        clusters = extract_body_link_clusters(soup, BASE)
        sub = SourceContent(
            source_kind="url",
            source_ref="https://example.com/dart-sass",
            raw_text="",
            url_path="/dart-sass",
            body_link_clusters=list(clusters),
        )
        source = _source(clusters, discovered=[sub])
        self.assertIsNone(find_linkbar_cluster(source))

    def test_large_unlabeled_link_list_is_not_a_linkbar(self):
        cluster = LinkCluster(
            links=[NavLink(label=f"Post {i}", href=f"/post-{i}") for i in range(6)],
            href_key="aa",
            context_label="",
        )
        self.assertIsNone(find_linkbar_cluster(_source([cluster])))

    def test_strip_removes_strap_line_but_keeps_prose_and_short_lines(self):
        soup = BeautifulSoup(STRAP_HTML, "lxml")
        clusters = extract_body_link_clusters(soup, BASE)
        raw = "\n".join(
            [
                "Current Releases: Dart Sass 1.100.0 LibSass Ruby Sass",
                "Sass is the most mature, stable, and powerful CSS extension language.",
                "Menu",
            ]
        )
        source = _source(clusters, raw_text=raw)
        strip_linkbar_lines(source, clusters[0])
        lines = source.raw_text.split("\n")
        self.assertNotIn(
            "Current Releases: Dart Sass 1.100.0 LibSass Ruby Sass", lines
        )
        self.assertIn(
            "Sass is the most mature, stable, and powerful CSS extension language.",
            lines,
        )
        self.assertIn("Menu", lines)


def _page(slug: str, *, is_homepage: bool = False, blocks=None) -> PagePlan:
    return PagePlan(
        page_type="home" if is_homepage else "landing",
        slug=slug,
        title=slug or "Home",
        is_homepage=is_homepage,
        blocks=blocks if blocks is not None else [],
        seo_title="t",
        seo_description="d",
    )


def _cluster(*links: tuple[str, str], label: str = "Current Releases") -> LinkCluster:
    return LinkCluster(
        links=[NavLink(label=lbl, href=href) for lbl, href in links],
        href_key="bb",
        context_label=label,
    )


class LinkbarInjectionTest(unittest.TestCase):
    def test_injected_after_hero_with_resolvable_links_only(self):
        home = _page("", is_homepage=True, blocks=[HeroBlock(headline="Hi")])
        pages = [home, _page("dart-sass"), _page("libsass")]
        _inject_linkbar(
            pages,
            _cluster(
                ("Dart Sass", "/dart-sass"),
                ("LibSass", "/libsass"),
                ("Ghost", "/not-generated"),
            ),
        )
        self.assertEqual(home.blocks[1].kind, "linkbar")
        self.assertEqual(home.blocks[1].label, "Current Releases")
        self.assertEqual(
            [link.href for link in home.blocks[1].links],
            ["/dart-sass", "/libsass"],
        )

    def test_skipped_when_fewer_than_two_links_resolve(self):
        home = _page("", is_homepage=True, blocks=[HeroBlock(headline="Hi")])
        pages = [home, _page("dart-sass")]
        _inject_linkbar(
            pages,
            _cluster(("Dart Sass", "/dart-sass"), ("Ghost", "/nope")),
        )
        self.assertEqual(len(home.blocks), 1)

    def test_homepage_anchor_links_resolve(self):
        home = _page("", is_homepage=True, blocks=[HeroBlock(headline="Hi")])
        _inject_linkbar(
            [home],
            _cluster(("Pricing", "/#pricing"), ("FAQ", "/#faq")),
        )
        self.assertEqual(home.blocks[1].kind, "linkbar")


if __name__ == "__main__":
    unittest.main()
