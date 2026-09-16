"""Tests for services/site_url.py — one definition of "same site" / "same page"."""

from __future__ import annotations

import unittest

from app.services.site_url import crawl_key, is_same_site, rebase_to_origin, site_host


class SameSiteTest(unittest.TestCase):
    def test_www_alias_is_the_same_site(self):
        self.assertTrue(is_same_site("https://feruni.com/contact-us/", "https://www.feruni.com/"))
        self.assertTrue(is_same_site("http://WWW.Feruni.com/a", "https://feruni.com"))

    def test_other_subdomains_and_hosts_are_not(self):
        self.assertFalse(is_same_site("https://shop.feruni.com/", "https://www.feruni.com/"))
        self.assertFalse(is_same_site("https://elsewhere.test/", "https://www.feruni.com/"))

    def test_site_host_strips_only_a_leading_www(self):
        self.assertEqual(site_host("WWW.Example.com"), "example.com")
        self.assertEqual(site_host("wwwexample.com"), "wwwexample.com")


class CrawlKeyTest(unittest.TestCase):
    def test_alias_slash_and_fragment_share_one_identity(self):
        keys = {
            crawl_key("https://www.feruni.com/contact-us/"),
            crawl_key("https://feruni.com/contact-us"),
            crawl_key("http://feruni.com/contact-us/#map"),
        }
        self.assertEqual(keys, {"feruni.com/contact-us"})

    def test_query_is_part_of_the_identity(self):
        self.assertNotEqual(
            crawl_key("https://site.test/?page_id=1"), crawl_key("https://site.test/?page_id=2")
        )

    def test_root_and_non_web_urls(self):
        self.assertEqual(crawl_key("https://site.test"), "site.test/")
        self.assertIsNone(crawl_key("mailto:a@b.test"))
        self.assertIsNone(crawl_key("/relative/path"))


class RebaseToOriginTest(unittest.TestCase):
    def test_alias_is_requested_on_the_origin_with_its_path_as_linked(self):
        self.assertEqual(
            rebase_to_origin("http://feruni.com/our-collections-new/?x=1#top", "https://www.feruni.com/"),
            "https://www.feruni.com/our-collections-new/?x=1",
        )

    def test_other_sites_only_lose_the_fragment(self):
        self.assertEqual(
            rebase_to_origin("https://elsewhere.test/a#b", "https://www.feruni.com/"),
            "https://elsewhere.test/a",
        )


if __name__ == "__main__":
    unittest.main()
