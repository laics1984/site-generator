import unittest

from app.services.facebook_urls import FacebookUrlError, parse_ref


class ParseRefTest(unittest.TestCase):
    def test_plain_handle_forms(self):
        for raw in (
            "https://www.facebook.com/acmecoffee",
            "https://facebook.com/acmecoffee/",
            "http://m.facebook.com/acmecoffee",
            "https://mbasic.facebook.com/acmecoffee",
            "facebook.com/acmecoffee",
            "@acmecoffee",
        ):
            with self.subTest(raw=raw):
                ref = parse_ref(raw)
                self.assertEqual(ref.handle, "acmecoffee")
                self.assertIsNone(ref.page_id)
                self.assertEqual(ref.canonical_url, "https://www.facebook.com/acmecoffee")
                self.assertEqual(ref.kind, "page")
                self.assertEqual(ref.node, "acmecoffee")

    def test_tab_suffixes_resolve_to_the_page_itself(self):
        for tab in ("about", "photos", "reviews", "services", "about_contact_and_basic_info"):
            with self.subTest(tab=tab):
                ref = parse_ref(f"https://www.facebook.com/acmecoffee/{tab}")
                self.assertEqual(ref.handle, "acmecoffee")
                self.assertEqual(ref.canonical_url, "https://www.facebook.com/acmecoffee")

    def test_tracking_params_are_stripped(self):
        ref = parse_ref(
            "https://www.facebook.com/acmecoffee?mibextid=LQQJ4d&fbclid=abc123&rdid=xyz"
        )
        self.assertEqual(ref.canonical_url, "https://www.facebook.com/acmecoffee")

    def test_numeric_profile_php_form(self):
        ref = parse_ref("https://www.facebook.com/profile.php?id=100064123456789")
        self.assertIsNone(ref.handle)
        self.assertEqual(ref.page_id, "100064123456789")
        self.assertEqual(
            ref.canonical_url, "https://www.facebook.com/profile.php?id=100064123456789"
        )
        self.assertEqual(ref.node, "100064123456789")

    def test_modern_p_permalink(self):
        ref = parse_ref("https://www.facebook.com/p/Acme-Coffee-Roasters-100064123456789")
        self.assertEqual(ref.page_id, "100064123456789")
        self.assertEqual(ref.kind, "page")

    def test_legacy_pages_vanity_form(self):
        ref = parse_ref("https://www.facebook.com/pages/Acme-Coffee/123456789")
        self.assertEqual(ref.page_id, "123456789")

    def test_numeric_bare_segment_is_a_page_id(self):
        ref = parse_ref("https://www.facebook.com/100064123456789")
        self.assertEqual(ref.page_id, "100064123456789")
        self.assertIsNone(ref.handle)


class WrongShapeTest(unittest.TestCase):
    """A Facebook link is often not a business Page. Generating from one
    produces something confidently wrong, so each shape gets its own message."""

    def test_group_rejected(self):
        with self.assertRaises(FacebookUrlError) as ctx:
            parse_ref("https://www.facebook.com/groups/123456789")
        self.assertIn("group", str(ctx.exception).lower())

    def test_event_rejected(self):
        with self.assertRaises(FacebookUrlError) as ctx:
            parse_ref("https://www.facebook.com/events/123456789")
        self.assertIn("event", str(ctx.exception).lower())

    def test_personal_profile_rejected(self):
        with self.assertRaises(FacebookUrlError) as ctx:
            parse_ref("https://www.facebook.com/people/Jane-Doe/100012345678901")
        self.assertIn("personal", str(ctx.exception).lower())

    def test_post_permalink_rejected(self):
        for raw in (
            "https://www.facebook.com/acmecoffee/posts/pfbid0abc123",
            "https://www.facebook.com/story.php?story_fbid=1&id=2",
            "https://www.facebook.com/watch/?v=123456",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(FacebookUrlError):
                    parse_ref(raw)

    def test_reserved_chrome_rejected(self):
        with self.assertRaises(FacebookUrlError):
            parse_ref("https://www.facebook.com/login")

    def test_non_strict_classifies_instead_of_raising(self):
        ref = parse_ref("https://www.facebook.com/groups/123456789", strict=False)
        self.assertEqual(ref.kind, "group")

    def test_bare_host_rejected(self):
        with self.assertRaises(FacebookUrlError) as ctx:
            parse_ref("https://www.facebook.com/")
        self.assertEqual(ctx.exception.status, 400)

    def test_empty_input_rejected(self):
        with self.assertRaises(FacebookUrlError) as ctx:
            parse_ref("   ")
        self.assertEqual(ctx.exception.status, 400)


if __name__ == "__main__":
    unittest.main()
