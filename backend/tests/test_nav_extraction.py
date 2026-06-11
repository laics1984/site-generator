import unittest

from bs4 import BeautifulSoup

from app.models.content_blocks import SourceContent
from app.services.nav_extraction import (
    extract_body_link_clusters,
    extract_nav_links,
    extract_social_links,
    find_repeated_cluster_keys,
    site_relative_href,
    strip_chrome_lines,
)

BASE = "https://example.com/"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class SiteRelativeHrefTest(unittest.TestCase):
    def test_same_site_absolute_url_becomes_path(self):
        self.assertEqual(
            site_relative_href("https://example.com/services/", BASE), "/services"
        )

    def test_www_prefix_is_ignored_for_host_match(self):
        self.assertEqual(
            site_relative_href("https://www.example.com/about", BASE), "/about"
        )

    def test_external_url_is_dropped(self):
        self.assertIsNone(site_relative_href("https://other.com/page", BASE))

    def test_bare_hash_toggle_is_dropped(self):
        self.assertIsNone(site_relative_href("#", BASE))

    def test_anchor_keeps_fragment(self):
        self.assertEqual(site_relative_href("/#pricing", BASE), "/#pricing")

    def test_mailto_and_tel_are_dropped(self):
        self.assertIsNone(site_relative_href("mailto:a@b.com", BASE))
        self.assertIsNone(site_relative_href("tel:+60123456789", BASE))


class ExtractNavLinksTest(unittest.TestCase):
    def test_nested_dropdown_becomes_children(self):
        html = """
        <header><nav><ul>
          <li><a href="/">Home</a></li>
          <li><a href="/services">Services</a>
            <ul>
              <li><a href="/web-design">Web Design</a></li>
              <li><a href="/seo">SEO</a></li>
            </ul>
          </li>
          <li><a href="/contact">Contact</a></li>
        </ul></nav></header>
        """
        nav = extract_nav_links(_soup(html), BASE)
        self.assertEqual([n.label for n in nav], ["Home", "Services", "Contact"])
        services = nav[1]
        self.assertEqual(services.href, "/services")
        self.assertEqual(
            [(c.label, c.href) for c in services.children],
            [("Web Design", "/web-design"), ("SEO", "/seo")],
        )

    def test_unlinked_dropdown_parent_keeps_children(self):
        html = """
        <header><nav><ul>
          <li><a href="#">Practice Areas</a>
            <ul><li><a href="/family-law">Family Law</a></li>
                <li><a href="/corporate">Corporate</a></li></ul>
          </li>
        </ul></nav></header>
        """
        nav = extract_nav_links(_soup(html), BASE)
        self.assertEqual(len(nav), 1)
        self.assertEqual(nav[0].label, "Practice Areas")
        self.assertEqual(nav[0].href, "#")
        self.assertEqual(len(nav[0].children), 2)

    def test_header_nav_preferred_over_other_navs(self):
        html = """
        <header><nav><ul>
          <li><a href="/">Home</a></li><li><a href="/about">About</a></li>
        </ul></nav></header>
        <div><nav><ul>
          <li><a href="/a">A</a></li><li><a href="/b">B</a></li>
          <li><a href="/c">C</a></li><li><a href="/d">D</a></li>
        </ul></nav></div>
        """
        nav = extract_nav_links(_soup(html), BASE)
        self.assertEqual([n.label for n in nav], ["Home", "About"])

    def test_nav_without_lists_falls_back_to_flat_anchors(self):
        html = """
        <header><nav>
          <a href="/">Home</a><a href="/work">Work</a><a href="/contact">Contact</a>
        </nav></header>
        """
        nav = extract_nav_links(_soup(html), BASE)
        self.assertEqual([n.href for n in nav], ["/", "/work", "/contact"])

    def test_no_nav_returns_empty(self):
        self.assertEqual(extract_nav_links(_soup("<p>hello</p>"), BASE), [])


class ExtractBodyClustersTest(unittest.TestCase):
    def test_menu_strip_in_body_is_detected(self):
        html = """
        <header><nav><ul><li><a href="/">Home</a></li></ul></nav></header>
        <main>
          <ul class="service-strip">
            <li><a href="/web-design">Web Design</a></li>
            <li><a href="/seo">SEO</a></li>
            <li><a href="/branding">Branding</a></li>
          </ul>
          <p>Long-form copy about the business that goes on and on.</p>
        </main>
        """
        clusters = extract_body_link_clusters(_soup(html), BASE)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(
            [link.href for link in clusters[0].links],
            ["/web-design", "/seo", "/branding"],
        )
        self.assertTrue(clusters[0].href_key)

    def test_header_nav_is_not_a_body_cluster(self):
        html = """
        <header><nav><ul>
          <li><a href="/a">A</a></li><li><a href="/b">B</a></li>
          <li><a href="/c">C</a></li>
        </ul></nav></header>
        <main><p>Just prose here.</p></main>
        """
        self.assertEqual(extract_body_link_clusters(_soup(html), BASE), [])

    def test_prose_with_inline_links_is_not_a_cluster(self):
        html = """
        <main><div>
          We provide <a href="/web-design">web design</a> plus a wide range of
          other digital services including <a href="/seo">seo</a> and
          <a href="/branding">branding</a>, with years of experience and many
          happy clients across the region who keep coming back to us.
        </div></main>
        """
        self.assertEqual(extract_body_link_clusters(_soup(html), BASE), [])


def _page(path: str | None, raw_text: str, clusters) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref=f"https://example.com{path or ''}",
        raw_text=raw_text,
        url_path=path,
        body_link_clusters=clusters,
    )


class CrossPageClassificationTest(unittest.TestCase):
    def _source_with_repeated_strip(self) -> SourceContent:
        html = """
        <main><ul>
          <li><a href="/web-design">Web Design</a></li>
          <li><a href="/seo">SEO</a></li>
          <li><a href="/branding">Branding</a></li>
        </ul></main>
        """
        clusters = extract_body_link_clusters(_soup(html), BASE)
        primary = _page(None, "Welcome to Acme\nWeb Design\nGreat copy.", clusters)
        primary.discovered_pages = [
            _page("/web-design", "Web Design\nWe build websites.", list(clusters)),
            _page("/seo", "SEO\nWe rank you.", list(clusters)),
        ]
        return primary

    def test_repeated_cluster_is_chrome(self):
        source = self._source_with_repeated_strip()
        keys = find_repeated_cluster_keys(source)
        self.assertEqual(len(keys), 1)

    def test_one_off_cluster_is_not_chrome(self):
        html = """
        <main><ul>
          <li><a href="/web-design">Web Design</a></li>
          <li><a href="/seo">SEO</a></li>
          <li><a href="/branding">Branding</a></li>
        </ul></main>
        """
        clusters = extract_body_link_clusters(_soup(html), BASE)
        primary = _page(None, "Welcome.", clusters)
        primary.discovered_pages = [_page("/about", "About us.", [])]
        self.assertEqual(find_repeated_cluster_keys(primary), set())

    def test_strip_chrome_lines_removes_label_lines_only(self):
        source = self._source_with_repeated_strip()
        strip_chrome_lines(source)
        # Whole-line label matches are gone...
        self.assertNotIn("Web Design", source.raw_text.split("\n"))
        sub = source.discovered_pages[0]
        self.assertNotIn("Web Design", sub.raw_text.split("\n"))
        # ...but prose lines mentioning the label survive.
        self.assertIn("We build websites.", sub.raw_text)
        self.assertIn("Great copy.", source.raw_text)


class ExtractSocialLinksTest(unittest.TestCase):
    def test_profile_links_found_one_per_platform(self):
        html = """
        <footer>
          <a href="https://www.facebook.com/acme">Facebook</a>
          <a href="https://instagram.com/acme"><svg></svg></a>
          <a href="https://www.facebook.com/acme-duplicate">FB again</a>
          <a href="https://example.com/about">About</a>
        </footer>
        """
        links = extract_social_links(_soup(html), BASE)
        self.assertEqual(
            [(l.label, l.href) for l in links],
            [
                ("Facebook", "https://www.facebook.com/acme"),
                ("Instagram", "https://instagram.com/acme"),
            ],
        )

    def test_share_links_and_bare_homepages_are_excluded(self):
        html = """
        <div>
          <a href="https://www.facebook.com/sharer/sharer.php?u=x">Share</a>
          <a href="https://twitter.com/intent/tweet?text=x">Tweet</a>
          <a href="https://instagram.com/">Instagram</a>
        </div>
        """
        self.assertEqual(extract_social_links(_soup(html), BASE), [])

    def test_no_social_links_returns_empty(self):
        self.assertEqual(extract_social_links(_soup("<p>hi</p>"), BASE), [])


if __name__ == "__main__":
    unittest.main()
