"""Public-page parsing. Pure over inline HTML — never opens a browser."""

import unittest

from app.services.facebook_render import (
    FacebookRenderError,
    _scan_hours,
    _scan_labelled,
    _split_og_description,
    _text_lines,
    is_login_wall,
    parse_public_html,
)
from app.services.facebook_urls import FacebookRef

REF = FacebookRef(
    handle="acmecoffee", page_id=None, canonical_url="https://www.facebook.com/acmecoffee"
)


def _page_html(body: str = "", *, og: str = "") -> str:
    return f"""
    <html><head>
      <meta property="og:title" content="Acme Coffee Roasters" />
      <meta property="og:url" content="https://www.facebook.com/acmecoffee" />
      <meta property="og:image" content="https://cdn.example/pic.jpg" />
      {og}
    </head><body>{body}</body></html>
    """


class LoginWallTest(unittest.TestCase):
    def test_detects_the_common_wall_markers(self):
        for html in (
            "<html><body>You must log in to continue</body></html>",
            '<html><body><form id="login_form"></form></body></html>',
            '<html><body><a href="/login/?next=%2Facme">Log in</a></body></html>',
            "<html><body>This content isn't available right now</body></html>",
        ):
            with self.subTest(html=html[:40]):
                self.assertTrue(is_login_wall(html))

    def test_a_real_page_is_not_a_wall(self):
        self.assertFalse(is_login_wall(_page_html("<div>Acme Coffee</div>")))

    def test_empty_html_counts_as_a_wall(self):
        self.assertTrue(is_login_wall(""))

    def test_a_wall_raises_rather_than_returning_a_thin_page(self):
        """Returning a Page carrying only a name would send a half-invented
        site downstream — the failure has to be loud."""
        with self.assertRaises(FacebookRenderError) as ctx:
            parse_public_html("<html><body>You must log in to continue</body></html>", REF)
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("token", str(ctx.exception).lower())


class OgDescriptionTest(unittest.TestCase):
    def test_engagement_counts_are_split_off_the_blurb(self):
        raw = (
            "Acme Coffee, Kuala Lumpur. 1,234 likes · 56 talking about this · "
            "78 were here. Specialty coffee roasted in-house."
        )
        blurb, fans = _split_og_description(raw)
        self.assertEqual(fans, 1234)
        self.assertIn("Specialty coffee roasted in-house", blurb)
        self.assertNotIn("likes", blurb)
        self.assertNotIn("talking about this", blurb)

    def test_no_counts_leaves_the_text_alone(self):
        blurb, fans = _split_og_description("Just a plain description.")
        self.assertEqual(blurb, "Just a plain description.")
        self.assertIsNone(fans)

    def test_missing_description_is_none(self):
        self.assertEqual(_split_og_description(None), (None, None))


class LabelScanTest(unittest.TestCase):
    def test_labelled_lines_become_fields(self):
        html = _page_html(
            "<div>Address</div><div>12 Jalan Sultan, Kuala Lumpur</div>"
            "<div>Phone</div><div>+60 3 1234 5678</div>"
            "<div>Email</div><div>hello@acme.example</div>"
        )
        found = _scan_labelled(_text_lines(__import__("bs4").BeautifulSoup(html, "lxml")))
        self.assertEqual(found["single_line_address"], "12 Jalan Sultan, Kuala Lumpur")
        self.assertEqual(found["phone"], "+60 3 1234 5678")
        self.assertEqual(found["email"], "hello@acme.example")

    def test_a_label_with_no_value_does_not_swallow_the_next_label(self):
        html = _page_html("<div>Phone</div><div>Email</div><div>hello@acme.example</div>")
        found = _scan_labelled(_text_lines(__import__("bs4").BeautifulSoup(html, "lxml")))
        self.assertNotIn("phone", found)
        self.assertEqual(found["email"], "hello@acme.example")


class HoursScanTest(unittest.TestCase):
    def test_day_ranges_are_read_off_visible_text(self):
        lines = ["Monday 9:00 AM - 6:00 PM", "Tuesday 9:00 AM – 6:00 PM", "noise"]
        hours = _scan_hours(lines)
        self.assertEqual([h.day for h in hours], ["Monday", "Tuesday"])
        self.assertEqual(hours[0].opens, "9:00 AM")

    def test_duplicate_days_are_not_repeated(self):
        self.assertEqual(len(_scan_hours(["Monday 9-5", "Monday 9-5"])), 1)


class ParsePublicHtmlTest(unittest.TestCase):
    def test_a_full_page_maps_across_and_is_always_partial(self):
        html = _page_html(
            "<div>Address</div><div>12 Jalan Sultan</div>"
            "<div>Monday 9:00 AM - 6:00 PM</div>",
            og=(
                '<meta property="og:description" content="Acme Coffee. 1,234 likes. '
                'Specialty coffee roasted in-house." />'
            ),
        )
        page = parse_public_html(html, REF)
        self.assertEqual(page.name, "Acme Coffee Roasters")
        self.assertEqual(page.profile_picture_url, "https://cdn.example/pic.jpg")
        self.assertEqual(page.single_line_address, "12 Jalan Sultan")
        self.assertEqual(page.fan_count, 1234)
        self.assertEqual(len(page.hours), 1)
        self.assertEqual(page.fetched_via, "render")
        # A public render never sees emails, posts or reviews, so the UI must
        # always be able to offer the token.
        self.assertTrue(page.partial)
        self.assertIn("posts", page.missing_fields)
        self.assertIn("reviews", page.missing_fields)

    def test_a_phone_shaped_value_is_required_before_it_becomes_a_phone(self):
        html = _page_html("<div>Phone</div><div>ask us in store</div>")
        page = parse_public_html(html, REF)
        self.assertIsNone(page.phone)

    def test_json_ld_fills_what_the_text_scan_missed(self):
        html = _page_html(
            '<script type="application/ld+json">'
            '{"@type":"Restaurant","telephone":"+60312345678",'
            '"address":{"streetAddress":"12 Jalan Sultan","addressLocality":"KL"}}'
            "</script>"
        )
        page = parse_public_html(html, REF)
        self.assertEqual(page.phone, "+60312345678")
        self.assertEqual(page.single_line_address, "12 Jalan Sultan, KL")

    def test_malformed_json_ld_is_ignored_not_fatal(self):
        html = _page_html('<script type="application/ld+json">{not json}</script>')
        page = parse_public_html(html, REF)
        self.assertEqual(page.name, "Acme Coffee Roasters")

    def test_no_identity_at_all_raises(self):
        with self.assertRaises(FacebookRenderError):
            parse_public_html(
                "<html><head></head><body>nothing</body></html>",
                FacebookRef(handle=None, page_id=None, canonical_url="x"),
            )


if __name__ == "__main__":
    unittest.main()
