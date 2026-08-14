import unittest

from app.services.source_detect import detect, is_facebook_url


class DetectTest(unittest.TestCase):
    def test_facebook_hosts_route_to_the_facebook_reader(self):
        for url in (
            "https://www.facebook.com/acmecoffee",
            "http://facebook.com/acmecoffee",
            "https://m.facebook.com/acmecoffee",
            "https://mbasic.facebook.com/acmecoffee/about",
            "https://web.facebook.com/acmecoffee",
            "https://fb.com/acmecoffee",
            "https://fb.me/acmecoffee",
            "facebook.com/acmecoffee",  # no scheme, as typed
            "  https://www.facebook.com/acmecoffee  ",
        ):
            with self.subTest(url=url):
                self.assertEqual(detect(url), "facebook")
                self.assertTrue(is_facebook_url(url))

    def test_at_handle_routes_to_facebook(self):
        self.assertEqual(detect("@acmecoffee"), "facebook")
        self.assertTrue(is_facebook_url("@acmecoffee"))

    def test_everything_else_falls_back_to_html(self):
        for url in (
            "https://example.com",
            "https://example.com/about",
            "http://localhost:3000",
            "example.com",
        ):
            with self.subTest(url=url):
                self.assertEqual(detect(url), "html")
                self.assertFalse(is_facebook_url(url))

    def test_near_misses_do_not_route_to_facebook(self):
        """A suffix match without the dot boundary sends real sites to the
        wrong reader, which then fails on markup that was never Facebook's."""
        for url in (
            "https://notfacebook.com",
            "https://myfacebook.com/page",
            "https://facebook.com.evil.example/page",
            "https://example.com/facebook",
            "https://example.com/?ref=facebook.com",
        ):
            with self.subTest(url=url):
                self.assertEqual(detect(url), "html")
                self.assertFalse(is_facebook_url(url))

    def test_bare_word_is_not_assumed_to_be_a_handle(self):
        """Without the @ sigil a bare word is far more likely a mistyped
        domain; guessing sends the user somewhere they didn't ask to go."""
        self.assertEqual(detect("acmecoffee"), "html")

    def test_empty_and_garbage_fall_back_to_html(self):
        for url in ("", "   ", "not a url at all"):
            with self.subTest(url=url):
                self.assertEqual(detect(url), "html")

    def test_host_is_case_insensitive(self):
        self.assertEqual(detect("https://WWW.FaceBook.COM/acme"), "facebook")


if __name__ == "__main__":
    unittest.main()
