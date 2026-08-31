"""FacebookPage → pipeline input.

The assertions that matter here are the negative ones: a section must not be
requested without its evidence, and raw_text must not contain anything the Page
didn't say — that string is the haystack the grounding net matches against, so
it defines exactly what the model is allowed to claim.
"""

import asyncio
import time
import unittest

from app.config import settings
from app.models.facebook import FacebookHours, FacebookPage, FacebookPost, FacebookReview
from app.services import browser_session
from app.services.facebook_source import (
    FacebookSourceError,
    build_raw_text,
    default_fetchers,
    fetch_facebook_page,
    homepage_sections_for,
    industry_for,
    to_contact_dict,
    saved_session,
    to_image_metadata,
    to_source_content,
)
from app.services.facebook_urls import FacebookRef

REF = FacebookRef(
    handle="acmecoffee", page_id=None, canonical_url="https://www.facebook.com/acmecoffee"
)


def _page(**overrides) -> FacebookPage:
    base = dict(
        name="Acme Coffee Roasters",
        canonical_url="https://www.facebook.com/acmecoffee",
        category="Coffee Shop",
        about="Specialty coffee roasted in-house since 2015 in the heart of the city.",
        phone="+60 3 1234 5678",
        emails=["hello@acmecoffee.example"],
        single_line_address="12 Jalan Sultan, 50000 Kuala Lumpur",
        hours=[FacebookHours(day="Monday", opens="09:00", closes="18:00")],
    )
    base.update(overrides)
    return FacebookPage(**base)


class RawTextTest(unittest.TestCase):
    def test_every_stated_fact_appears_verbatim(self):
        page = _page(
            posts=[FacebookPost(message="New single-origin Ethiopian landed today.")],
            reviews=[FacebookReview(text="Best flat white in KL", author="Sam Lee")],
            website="https://acmecoffee.example",
        )
        text = build_raw_text(page)
        for fragment in (
            "Acme Coffee Roasters",
            "Coffee Shop",
            "Specialty coffee roasted in-house",
            "12 Jalan Sultan, 50000 Kuala Lumpur",
            "+60 3 1234 5678",
            "hello@acmecoffee.example",
            "https://acmecoffee.example",
            "Monday 09:00–18:00",
            "New single-origin Ethiopian landed today.",
            "Best flat white in KL",
            "Sam Lee",
        ):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, text)

    def test_absent_fields_leave_no_empty_labels(self):
        """A bare 'Phone:' line would let the model treat the label itself as
        grounding for a phone number it invented."""
        text = build_raw_text(
            FacebookPage(
                name="Acme", canonical_url="https://www.facebook.com/acme", about="A shop."
            )
        )
        for label in ("Phone:", "Email:", "Address:", "Opening hours:", "Website:"):
            with self.subTest(label=label):
                self.assertNotIn(label, text)

    def test_posts_without_text_contribute_nothing(self):
        page = _page(posts=[FacebookPost(image_url="https://cdn.example/a.jpg")])
        self.assertNotIn("Updates from the Page", build_raw_text(page))


class SectionGatingTest(unittest.TestCase):
    """A section that can't be grounded is never requested, so the model is
    never put in the position of padding one."""

    def test_a_bare_page_gets_only_the_unconditional_sections(self):
        sections = homepage_sections_for(
            FacebookPage(name="Acme", canonical_url="https://www.facebook.com/acme")
        )
        self.assertEqual(sections, ["hero", "contact", "cta"])

    def test_about_needs_about_text(self):
        self.assertIn("about", homepage_sections_for(_page()))
        self.assertNotIn("about", homepage_sections_for(_page(about=None)))

    def test_gallery_needs_four_photos(self):
        three = _page(
            posts=[FacebookPost(image_url=f"https://cdn.example/{i}.jpg") for i in range(3)]
        )
        self.assertNotIn("gallery", homepage_sections_for(three))
        four = _page(
            posts=[FacebookPost(image_url=f"https://cdn.example/{i}.jpg") for i in range(4)]
        )
        self.assertIn("gallery", homepage_sections_for(four))

    def test_testimonials_needs_reviews(self):
        self.assertNotIn("testimonials", homepage_sections_for(_page()))
        with_reviews = _page(reviews=[FacebookReview(text="Great", author="Sam")])
        self.assertIn("testimonials", homepage_sections_for(with_reviews))

    def test_locations_needs_an_address(self):
        self.assertIn("locations", homepage_sections_for(_page()))
        self.assertNotIn("locations", homepage_sections_for(_page(single_line_address=None)))

    def test_features_needs_products_or_three_posts(self):
        self.assertNotIn("features", homepage_sections_for(_page()))
        self.assertIn("features", homepage_sections_for(_page(products="Beans, brew kit")))
        posts = [FacebookPost(message=f"Post {i}") for i in range(3)]
        self.assertIn("features", homepage_sections_for(_page(posts=posts)))

    def test_hero_and_cta_always_bracket_the_page(self):
        sections = homepage_sections_for(_page())
        self.assertEqual(sections[0], "hero")
        self.assertEqual(sections[-1], "cta")

    def test_never_exceeds_the_single_call_token_budget(self):
        from app.services.landing_patterns import _MAX_HOMEPAGE_SECTIONS

        rich = _page(
            products="Beans",
            reviews=[FacebookReview(text="Great", author="Sam")],
            posts=[
                FacebookPost(message=f"Post {i}", image_url=f"https://cdn.example/{i}.jpg")
                for i in range(6)
            ],
            cover_photo_url="https://cdn.example/cover.jpg",
        )
        self.assertLessEqual(len(homepage_sections_for(rich)), _MAX_HOMEPAGE_SECTIONS)


class SourceContentTest(unittest.TestCase):
    def test_maps_onto_the_pipeline_shape(self):
        source = to_source_content(_page())
        self.assertEqual(source.source_kind, "facebook")
        self.assertEqual(source.source_ref, "https://www.facebook.com/acmecoffee")
        self.assertEqual(source.title, "Acme Coffee Roasters")
        self.assertEqual(source.subject_name, "Acme Coffee Roasters")
        # The About blurb reaches the planner prompt for free — a field the
        # document path leaves empty.
        self.assertTrue(source.description.startswith("Specialty coffee"))

    def test_one_landing_page_no_discovered_pages(self):
        self.assertEqual(to_source_content(_page()).discovered_pages, [])

    def test_the_page_itself_is_a_social_link(self):
        source = to_source_content(_page(website="https://acmecoffee.example"))
        labels = {link.label for link in source.social_links}
        self.assertEqual(labels, {"Facebook", "Website"})

    def test_headings_only_reflect_present_data(self):
        source = to_source_content(
            _page(
                single_line_address=None,
                hours=[],
                # Long enough to clear the thin-page floor on its own, so this
                # test measures heading gating rather than the floor.
                about=(
                    "Specialty coffee roasted in-house since 2015. We source "
                    "single-origin beans direct from growers and roast in small "
                    "batches every week for cafes across the city."
                ),
            )
        )
        self.assertNotIn("Opening hours", source.headings)
        self.assertNotIn("Where to find us", source.headings)
        self.assertIn("Get in touch", source.headings)

    def test_a_page_with_almost_nothing_is_refused(self):
        """Name + category is ~40 chars and would sail past the document
        path's 80-char floor, producing a site padded out of nothing."""
        with self.assertRaises(FacebookSourceError) as ctx:
            to_source_content(
                FacebookPage(name="Acme", canonical_url="https://www.facebook.com/acme")
            )
        self.assertEqual(ctx.exception.status, 422)
        self.assertIn("token", str(ctx.exception).lower())

    def test_the_floor_is_configurable(self):
        original = settings.facebook_min_raw_text_chars
        settings.facebook_min_raw_text_chars = 1
        try:
            source = to_source_content(
                FacebookPage(name="Acme", canonical_url="https://www.facebook.com/acme")
            )
            self.assertEqual(source.title, "Acme")
        finally:
            settings.facebook_min_raw_text_chars = original


class ContactDictTest(unittest.TestCase):
    def test_carries_the_three_seo_keys(self):
        self.assertEqual(
            to_contact_dict(_page()),
            {
                "email": "hello@acmecoffee.example",
                "phone": "+60 3 1234 5678",
                "address": "12 Jalan Sultan, 50000 Kuala Lumpur",
            },
        )

    def test_absent_details_are_omitted_not_blanked(self):
        contact = to_contact_dict(_page(phone=None, emails=[], single_line_address=None))
        self.assertEqual(contact, {})


class ImageTest(unittest.TestCase):
    def test_cover_is_the_hero_and_posts_are_gallery(self):
        page = _page(
            cover_photo_url="https://cdn.example/cover.jpg",
            posts=[
                FacebookPost(
                    message="Latte art class this Saturday. Book ahead.",
                    image_url="https://cdn.example/a.jpg",
                )
            ],
        )
        metas = to_image_metadata(page)
        self.assertEqual(metas[0].intent, "hero")
        self.assertEqual(metas[1].role, "gallery")
        self.assertEqual(metas[1].alt, "Latte art class this Saturday")

    def test_the_profile_picture_is_not_in_the_photo_pool(self):
        """It is the brand mark, not content — a header logo repeated in the
        gallery reads as a mistake."""
        page = _page(profile_picture_url="https://cdn.example/pic.jpg")
        self.assertNotIn(
            "https://cdn.example/pic.jpg", [m.url for m in to_image_metadata(page)]
        )

    def test_duplicate_post_photos_appear_once(self):
        page = _page(
            posts=[
                FacebookPost(message="a", image_url="https://cdn.example/x.jpg"),
                FacebookPost(message="b", image_url="https://cdn.example/x.jpg"),
            ]
        )
        self.assertEqual(len(to_image_metadata(page)), 1)


class IndustryTest(unittest.TestCase):
    def test_maps_the_pages_own_category(self):
        cases = [
            ("Coffee Shop", "restaurant"),
            ("Bakery", "restaurant"),
            ("Preschool", "childcare"),
            ("Law Firm", "professional-services"),
            ("Advertising Agency", "agency"),
            ("Clothing Store", "ecommerce"),
            ("Non-Profit Organization", "nonprofit"),
            ("Public Figure", "personal"),
        ]
        for category, expected in cases:
            with self.subTest(category=category):
                self.assertEqual(industry_for(_page(category=category)), expected)

    def test_stems_reach_their_inflections(self):
        for category, expected in (
            ("Consulting Agency", "consultancy"),
            ("Business Consultant", "consultancy"),
            ("Veterinarian", "professional-services"),
            ("Photographer", "agency"),
        ):
            with self.subTest(category=category):
                self.assertEqual(industry_for(_page(category=category)), expected)

    def test_a_needle_does_not_match_inside_a_longer_word(self):
        """'pub' inside 'Public Figure' filed a musician's Page as a
        restaurant. Whole-word matching is the fix; these guard it."""
        for category, expected in (
            ("Public Figure", "personal"),
            ("Publisher", "other"),
            ("Barber Shop", "ecommerce"),  # 'bar' must not make this a pub
        ):
            with self.subTest(category=category):
                self.assertEqual(industry_for(_page(category=category)), expected)

    def test_an_unknown_category_stays_other(self):
        """A wrong industry is worse than none — it steers the whole design."""
        self.assertEqual(industry_for(_page(category="Wormhole Cartography")), "other")

    def test_no_category_at_all_is_other(self):
        self.assertEqual(industry_for(_page(category=None, categories=[])), "other")


class _FakeFetcher:
    def __init__(self, name, result=None, error=None):
        self.name = name
        self._result = result
        self._error = error
        self.calls = 0

    async def fetch(self, ref):
        self.calls += 1
        if self._error:
            raise self._error
        return self._result


class FetchChainTest(unittest.TestCase):
    def test_first_success_wins_and_later_fetchers_are_not_called(self):
        primary = _FakeFetcher("graph", result=_page())
        fallback = _FakeFetcher("render", result=_page(name="Wrong"))
        page = asyncio.run(fetch_facebook_page(REF, fetchers=[primary, fallback]))
        self.assertEqual(page.name, "Acme Coffee Roasters")
        self.assertEqual(fallback.calls, 0)

    def test_falls_through_to_the_next_fetcher(self):
        primary = _FakeFetcher("graph", error=RuntimeError("no token"))
        fallback = _FakeFetcher("render", result=_page())
        page = asyncio.run(fetch_facebook_page(REF, fetchers=[primary, fallback]))
        self.assertEqual(page.name, "Acme Coffee Roasters")
        self.assertEqual(fallback.calls, 1)

    def test_all_failing_raises_the_last_error(self):
        with self.assertRaises(FacebookSourceError) as ctx:
            asyncio.run(
                fetch_facebook_page(
                    REF,
                    fetchers=[
                        _FakeFetcher("graph", error=RuntimeError("a")),
                        _FakeFetcher("render", error=RuntimeError("b")),
                    ],
                )
            )
        self.assertIn("b", str(ctx.exception))

    def test_an_empty_chain_fails_loudly_rather_than_silently(self):
        """conftest leaves the chain empty offline — this must be an error the
        caller sees, not a mysterious empty result."""
        with self.assertRaises(FacebookSourceError) as ctx:
            asyncio.run(fetch_facebook_page(REF, fetchers=[]))
        self.assertEqual(ctx.exception.status, 422)

    def test_the_default_chain_is_empty_under_the_offline_fixture(self):
        from app.services.facebook_source import default_fetchers

        self.assertEqual(default_fetchers(None), [])


class SessionChainTest(unittest.TestCase):
    """The saved session has to reach `facebook_render.fetch_page`, and be read
    fresh — connecting one happens BETWEEN reads, so a cached chain would
    ignore it."""

    def setUp(self):
        self._fallback = settings.facebook_render_fallback_enabled
        self._enabled = settings.facebook_session_enabled
        settings.facebook_render_fallback_enabled = True
        settings.facebook_session_enabled = True

    def tearDown(self):
        settings.facebook_render_fallback_enabled = self._fallback
        settings.facebook_session_enabled = self._enabled
        browser_session.clear("facebook")

    def _connect(self):
        browser_session.save(
            "facebook",
            {
                "cookies": [
                    {"name": "c_user", "value": "1", "domain": ".facebook.com",
                     "path": "/", "expires": time.time() + 86400},
                    {"name": "xs", "value": "2", "domain": ".facebook.com",
                     "path": "/", "expires": time.time() + 86400},
                ],
                "origins": [],
            },
        )

    def test_no_session_means_an_anonymous_render(self):
        (fetcher,) = default_fetchers(None)
        self.assertEqual(fetcher.name, "render")
        self.assertIsNone(fetcher._storage_state)

    def test_a_connected_session_rides_the_render_fetcher(self):
        self._connect()
        (fetcher,) = default_fetchers(None)
        self.assertIsNotNone(fetcher._storage_state)
        names = {c["name"] for c in fetcher._storage_state["cookies"]}
        self.assertEqual(names, {"c_user", "xs"})

    def test_the_session_is_read_fresh_on_every_chain_build(self):
        self.assertIsNone(default_fetchers(None)[0]._storage_state)
        self._connect()
        self.assertIsNotNone(default_fetchers(None)[0]._storage_state)
        browser_session.clear("facebook")
        self.assertIsNone(default_fetchers(None)[0]._storage_state)

    def test_the_kill_switch_is_a_true_no_op(self):
        self._connect()
        settings.facebook_session_enabled = False
        self.assertIsNone(saved_session())
        self.assertIsNone(default_fetchers(None)[0]._storage_state)

    def test_a_token_still_outranks_a_session(self):
        """Graph reads structured posts and recommendations no render can."""
        self._connect()
        chain = default_fetchers("TOKEN")
        self.assertEqual([f.name for f in chain], ["graph", "render"])


if __name__ == "__main__":
    unittest.main()
