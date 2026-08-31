"""Public-page parsing. Pure over inline HTML — never opens a browser."""

import unittest

from app.services.facebook_render import (
    FacebookRenderError,
    _candidate_urls,
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
    """Where we LANDED is the wall signal — not what the markup mentions.

    Facebook bundles its login dialog into every page it serves, so a markup
    denylist (`login_form`, `/login/?next=`) matched every readable Page as
    well as every wall. That check condemned 100% of public reads for as long
    as it existed; `test_a_page_that_merely_bundles_the_login_dialog...` is the
    regression pin.
    """

    def test_a_redirect_to_the_login_screen_is_a_wall(self):
        for final_url in (
            "https://www.facebook.com/login/?next=%2Facmecoffee",
            "https://www.facebook.com/checkpoint/1234",
            "https://www.facebook.com/recover/initiate",
        ):
            with self.subTest(final_url=final_url):
                # Even a body that reads like a real Page loses to the URL.
                self.assertTrue(
                    is_login_wall(_page_html("<div>Acme Coffee</div>"), final_url=final_url)
                )

    def test_the_retired_mbasic_host_lands_on_login(self):
        """mbasic used to serve plain HTML; it now 302s to the login screen,
        which is why it is no longer in the candidate ladder."""
        self.assertTrue(
            is_login_wall(
                "<html><body>Log in to Facebook</body></html>",
                final_url="https://www.facebook.com/login/?next=https%3A%2F%2Fmbasic.facebook.com%2FNASA%2F",
            )
        )

    def test_a_page_that_merely_bundles_the_login_dialog_is_not_a_wall(self):
        """THE regression. Every one of these strings is on a Facebook Page
        that renders perfectly — they are the login dialog every page ships,
        not a refusal to serve this one."""
        html = _page_html(
            '<div>Acme Coffee Roasters</div><div>28M followers</div>'
            '<form id="login_form"><input name="pass" /></form>'
            '<a href="/login/?next=%2Facmecoffee">Log in</a>'
            '<a href="#">Forgotten account?</a>'
        )
        self.assertFalse(is_login_wall(html, final_url="https://www.facebook.com/acmecoffee/about"))
        # …and it parses, rather than raising.
        self.assertEqual(
            parse_public_html(html, REF, final_url="https://www.facebook.com/acmecoffee/about").name,
            "Acme Coffee Roasters",
        )

    def test_a_refusal_stated_in_visible_text_is_a_wall(self):
        for body in (
            "You must log in to continue",
            "This content isn\u2019t available right now".replace("\u2019", "\u0027"),
            "This page isn\u0027t available",
        ):
            with self.subTest(body=body):
                self.assertTrue(is_login_wall(f"<html><body>{body}</body></html>"))

    def test_a_refusal_buried_in_SCRIPT_text_is_not_a_wall(self):
        """Facebook ships its whole string table inline. A phrase sitting in a
        <script> is vocabulary, not a message to the reader."""
        html = _page_html(
            '<script>var s = {err: "You must log in to continue"};</script>'
            "<div>Acme Coffee</div>"
        )
        self.assertFalse(is_login_wall(html, final_url="https://www.facebook.com/acmecoffee"))

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
        # The message must name both ways out, since either fixes it.
        message = str(ctx.exception).lower()
        self.assertIn("token", message)
        self.assertIn("sign in", message)


class CandidateLadderTest(unittest.TestCase):
    def test_no_mbasic_rung(self):
        """It 302s to /login now, so it cost a full render to learn nothing."""
        for candidate in _candidate_urls(REF):
            self.assertNotIn("mbasic", candidate)

    def test_the_about_tab_comes_first(self):
        self.assertEqual(
            _candidate_urls(REF),
            [
                "https://www.facebook.com/acmecoffee/about",
                "https://www.facebook.com/acmecoffee",
            ],
        )

    def test_a_numeric_id_addresses_its_about_tab_with_sk(self):
        """profile.php has no path segments — `/about` would 404."""
        ref = FacebookRef(
            handle=None, page_id="123", canonical_url="https://www.facebook.com/profile.php?id=123"
        )
        self.assertEqual(
            _candidate_urls(ref),
            [
                "https://www.facebook.com/profile.php?id=123&sk=about",
                "https://www.facebook.com/profile.php?id=123",
            ],
        )


class SignedInTest(unittest.TestCase):
    def test_a_signed_in_read_says_so(self):
        """The UI explains what's missing from what ran — so which render ran
        has to survive into the result."""
        html = _page_html("<div>Acme</div>")
        self.assertEqual(parse_public_html(html, REF).fetched_via, "render")
        self.assertEqual(
            parse_public_html(html, REF, signed_in=True).fetched_via, "render_session"
        )

    def test_both_render_paths_stay_partial(self):
        """Signing in reveals the About panel; it never reveals structured
        posts or recommendations, so the token offer must remain."""
        page = parse_public_html(_page_html("<div>Acme</div>"), REF, signed_in=True)
        self.assertTrue(page.partial)
        self.assertIn("posts", page.missing_fields)
        self.assertIn("reviews", page.missing_fields)


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

    def test_a_navigation_tab_is_not_a_value(self):
        """The tab strip reads `Posts / About / Photos / More`. Scanning
        forward from "About" landed on "Photos", which became the business's
        own description on every Page ever read this way."""
        found = _scan_labelled(["Posts", "About", "Photos", "More"])
        self.assertNotIn("about", found)
        self.assertEqual(found, {})

    def test_the_about_prose_comes_from_the_og_blurb_not_the_tab(self):
        html = _page_html(
            "<div>Posts</div><div>About</div><div>Photos</div><div>More</div>",
            og='<meta property="og:description" content="Acme Coffee. 12 likes. Roasted in-house daily." />',
        )
        page = parse_public_html(html, REF)
        self.assertIn("Roasted in-house", page.about)
        self.assertNotEqual(page.about, "Photos")

    def test_a_value_labelled_AFTER_itself_is_still_read(self):
        """Facebook's About panel puts contact rows value-first:
        `public-inquiries@hq.nasa.gov` then `Email address`. A forward-only
        scan called those unreadable while they sat in plain sight."""
        found = _scan_labelled(
            ["Contact info", "hello@acme.example", "Email address", "Websites and social links"]
        )
        self.assertEqual(found["email"], "hello@acme.example")

    def test_the_forward_pass_wins_over_the_backward_one(self):
        """The backward pass may only fill a gap, never move an answer."""
        found = _scan_labelled(["wrong@acme.example", "Email", "right@acme.example"])
        self.assertEqual(found["email"], "right@acme.example")

    def test_an_unusable_value_does_not_block_the_real_one(self):
        """"Email address" sitting above a section heading used to fill the
        slot with the heading, so the address further down was never reached."""
        found = _scan_labelled(
            [
                "Email address",
                "Websites and social links",
                "Contact info",
                "hello@acme.example",
                "Email address",
            ]
        )
        self.assertEqual(found["email"], "hello@acme.example")

    def test_a_shapeless_phone_is_never_offered(self):
        found = _scan_labelled(["Phone", "ask us in store"])
        self.assertNotIn("phone", found)


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
