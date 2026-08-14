"""Graph payload → FacebookPage. Pure mapping; no network anywhere."""

import unittest

from app.services.facebook_graph import _parse_hours, _parse_reviews, build_page
from app.services.facebook_urls import FacebookRef

REF = FacebookRef(
    handle="acmecoffee", page_id=None, canonical_url="https://www.facebook.com/acmecoffee"
)

CORE = {
    "id": "100064123456789",
    "name": "Acme Coffee Roasters",
    "username": "acmecoffee",
    "link": "https://www.facebook.com/acmecoffee",
    "category": "Coffee Shop",
    "category_list": [{"id": "1", "name": "Coffee Shop"}, {"id": "2", "name": "Cafe"}],
    "about": "Specialty coffee roasted in-house.",
    "description": "We have been roasting since 2015.",
    "website": "https://acmecoffee.example",
    "picture": {"data": {"url": "https://cdn.example/pic.jpg", "is_silhouette": False}},
    "cover": {"source": "https://cdn.example/cover.jpg"},
}

CONTACT = {
    "phone": "+60 3 1234 5678",
    "emails": ["hello@acmecoffee.example"],
    "single_line_address": "12 Jalan Sultan, 50000 Kuala Lumpur",
    "location": {"street": "12 Jalan Sultan", "city": "Kuala Lumpur", "country": "Malaysia"},
    "hours": {"mon_1_open": "09:00", "mon_1_close": "18:00"},
}


class BuildPageTest(unittest.TestCase):
    def test_core_fields_map_across(self):
        page = build_page(REF, CORE, {}, [])
        self.assertEqual(page.name, "Acme Coffee Roasters")
        self.assertEqual(page.page_id, "100064123456789")
        self.assertEqual(page.username, "acmecoffee")
        self.assertEqual(page.category, "Coffee Shop")
        self.assertEqual(page.categories, ["Coffee Shop", "Cafe"])
        self.assertEqual(page.about, "Specialty coffee roasted in-house.")
        self.assertEqual(page.profile_picture_url, "https://cdn.example/pic.jpg")
        self.assertEqual(page.cover_photo_url, "https://cdn.example/cover.jpg")
        self.assertEqual(page.fetched_via, "graph")

    def test_contact_group_maps_and_derives_address_and_hours(self):
        page = build_page(REF, CORE, {"contact": CONTACT}, [])
        self.assertEqual(page.phone, "+60 3 1234 5678")
        self.assertEqual(page.emails, ["hello@acmecoffee.example"])
        self.assertEqual(page.address_line, "12 Jalan Sultan, 50000 Kuala Lumpur")
        self.assertEqual(page.hours_text, "Monday 09:00–18:00")

    def test_address_falls_back_to_location_parts(self):
        contact = {k: v for k, v in CONTACT.items() if k != "single_line_address"}
        page = build_page(REF, CORE, {"contact": contact}, [])
        self.assertEqual(page.address_line, "12 Jalan Sultan, Kuala Lumpur, Malaysia")

    def test_a_denied_optional_group_degrades_instead_of_failing(self):
        """A token without pages_read_engagement must still yield a Page —
        the whole reason the fields are requested in groups."""
        page = build_page(REF, CORE, {}, ["contact", "posts"])
        self.assertEqual(page.name, "Acme Coffee Roasters")
        self.assertTrue(page.partial)
        self.assertEqual(page.missing_fields, ["contact", "posts"])
        self.assertIsNone(page.phone)
        self.assertEqual(page.hours, [])

    def test_no_missing_groups_is_not_partial(self):
        page = build_page(REF, CORE, {"contact": CONTACT}, [])
        self.assertFalse(page.partial)

    def test_silhouette_avatar_is_not_a_brand_mark(self):
        """Facebook's grey default head must never end up in the site header."""
        core = {
            **CORE,
            "picture": {"data": {"url": "https://cdn.example/sil.jpg", "is_silhouette": True}},
        }
        page = build_page(REF, core, {}, [])
        self.assertIsNone(page.profile_picture_url)

    def test_blank_strings_become_none_not_empty_facts(self):
        core = {**CORE, "about": "   ", "website": ""}
        page = build_page(REF, core, {}, [])
        self.assertIsNone(page.about)
        self.assertIsNone(page.website)

    def test_posts_without_text_or_photo_are_dropped(self):
        posts = {"data": [{"created_time": "2026-01-01"}, {"message": "Open today"}]}
        page = build_page(REF, CORE, {"posts": posts}, [])
        self.assertEqual(len(page.posts), 1)
        self.assertEqual(page.posts[0].message, "Open today")


class HoursTest(unittest.TestCase):
    def test_days_come_back_in_week_order(self):
        raw = {
            "fri_1_open": "09:00", "fri_1_close": "17:00",
            "mon_1_open": "08:00", "mon_1_close": "18:00",
        }
        self.assertEqual([h.day for h in _parse_hours(raw)], ["Monday", "Friday"])

    def test_split_shifts_collapse_to_first_open_and_last_close(self):
        """A lunch break the Page states as two ranges must not become an
        invented 'closed 13:00-14:00' line on the site."""
        raw = {
            "mon_1_open": "09:00", "mon_1_close": "13:00",
            "mon_2_open": "14:00", "mon_2_close": "18:00",
        }
        hours = _parse_hours(raw)
        self.assertEqual(len(hours), 1)
        self.assertEqual((hours[0].opens, hours[0].closes), ("09:00", "18:00"))

    def test_half_specified_day_is_dropped(self):
        self.assertEqual(_parse_hours({"mon_1_open": "09:00"}), [])

    def test_garbage_input_is_survivable(self):
        self.assertEqual(_parse_hours(None), [])
        self.assertEqual(_parse_hours({"nonsense": "x"}), [])


class ReviewsTest(unittest.TestCase):
    def test_review_needs_both_text_and_a_named_reviewer(self):
        """An unattributed quote reads as invented; a name with no words says
        nothing. Either half missing means we drop the item."""
        raw = {
            "data": [
                {"review_text": "Great coffee", "reviewer": {"name": "Sam Lee"}, "rating": 5},
                {"review_text": "No name here", "reviewer": {}},
                {"reviewer": {"name": "Silent Sam"}},
            ]
        }
        reviews = _parse_reviews(raw, 10)
        self.assertEqual(len(reviews), 1)
        self.assertEqual(reviews[0].author, "Sam Lee")
        self.assertEqual(reviews[0].rating, 5.0)

    def test_limit_is_honoured(self):
        raw = {
            "data": [
                {"review_text": f"Review {i}", "reviewer": {"name": f"P{i}"}}
                for i in range(10)
            ]
        }
        self.assertEqual(len(_parse_reviews(raw, 3)), 3)


class TokenRedactionTest(unittest.TestCase):
    def test_token_never_appears_in_an_error_string(self):
        from app.services.facebook_graph import _redact

        message = "Bad request for access_token=SECRET123 on node"
        self.assertNotIn("SECRET123", _redact(message, "SECRET123"))
        self.assertIn("***", _redact(message, "SECRET123"))


if __name__ == "__main__":
    unittest.main()
