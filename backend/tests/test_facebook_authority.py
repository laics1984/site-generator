"""The Page overrules the model on its own facts.

Every test here is the same shape: hand the pass a block the LLM plausibly
could have written, and assert the Page's value survives — or that the block is
gone when the Page never stated one. A fluent invention that no rule catches is
exactly what this pass exists to stop.
"""

import unittest

from app.models.content_blocks import (
    ContactBlock,
    HeroBlock,
    LocationItem,
    LocationsBlock,
    PagePlan,
    StatItem,
    StatsBlock,
    TestimonialItem,
    TestimonialsBlock,
)
from app.models.facebook import FacebookHours, FacebookPage, FacebookReview
from app.services.facebook_authority import enforce_facebook_facts


def _page(**overrides) -> FacebookPage:
    base = dict(
        name="Acme Coffee Roasters",
        canonical_url="https://www.facebook.com/acmecoffee",
        phone="+60 3 1234 5678",
        emails=["hello@acmecoffee.example"],
        single_line_address="12 Jalan Sultan, 50000 Kuala Lumpur",
        hours=[FacebookHours(day="Monday", opens="09:00", closes="18:00")],
    )
    base.update(overrides)
    return FacebookPage(**base)


def _plan(*blocks) -> list[PagePlan]:
    return [
        PagePlan(
            page_type="home",
            slug="",
            title="Home",
            description="",
            is_homepage=True,
            blocks=list(blocks),
            seo_title="Home",
            seo_description="",
        )
    ]


def _blocks(pages):
    return pages[0].blocks


class ContactTest(unittest.TestCase):
    def test_the_pages_details_replace_the_models(self):
        invented = ContactBlock(email="info@acme.com", phone="03-9999 0000")
        out = _blocks(enforce_facebook_facts(_plan(invented), _page()))
        self.assertEqual(out[0].email, "hello@acmecoffee.example")
        self.assertEqual(out[0].phone, "+60 3 1234 5678")

    def test_details_the_page_never_stated_are_nulled_not_left_standing(self):
        """The whole point: a plausible invention survives the grounding net
        precisely because it looks real."""
        invented = ContactBlock(email="info@acme.com", phone="03-9999 0000")
        page = _page(phone=None, emails=[])
        out = _blocks(enforce_facebook_facts(_plan(invented), page))
        self.assertIsNone(out[0].email)
        self.assertIsNone(out[0].phone)

    def test_the_block_itself_survives(self):
        """Contact is a structural section — it stays, just emptied of claims."""
        out = _blocks(
            enforce_facebook_facts(_plan(ContactBlock()), _page(phone=None, emails=[]))
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].kind, "contact")


class LocationsTest(unittest.TestCase):
    def test_rebuilt_from_the_page(self):
        invented = LocationsBlock(
            items=[LocationItem(name="Acme", address="1 Made Up Road", hours="Daily 8-8")]
        )
        out = _blocks(enforce_facebook_facts(_plan(invented), _page()))
        item = out[0].items[0]
        self.assertEqual(item.address, "12 Jalan Sultan, 50000 Kuala Lumpur")
        self.assertEqual(item.hours, "Monday 09:00–18:00")
        self.assertEqual(item.phone, "+60 3 1234 5678")
        self.assertEqual(item.name, "Acme Coffee Roasters")

    def test_dropped_entirely_without_an_address(self):
        invented = LocationsBlock(items=[LocationItem(name="Acme", address="1 Made Up Road")])
        out = _blocks(
            enforce_facebook_facts(_plan(invented), _page(single_line_address=None))
        )
        self.assertEqual(out, [])

    def test_whatsapp_is_never_inferred_from_a_phone_number(self):
        """Guessing the country code is exactly the kind of plausible
        fabrication this pass exists to stop."""
        invented = LocationsBlock(
            items=[LocationItem(name="Acme", address="x", whatsapp="+60123456789")]
        )
        out = _blocks(enforce_facebook_facts(_plan(invented), _page()))
        self.assertIsNone(out[0].items[0].whatsapp)

    def test_hours_are_omitted_when_the_page_lists_none(self):
        invented = LocationsBlock(
            items=[LocationItem(name="Acme", address="x", hours="Open 24/7")]
        )
        out = _blocks(enforce_facebook_facts(_plan(invented), _page(hours=[])))
        self.assertIsNone(out[0].items[0].hours)


class TestimonialsTest(unittest.TestCase):
    def test_replaced_wholesale_with_real_recommendations(self):
        invented = TestimonialsBlock(
            items=[TestimonialItem(quote="Amazing service!", author="Jane Doe")]
        )
        page = _page(
            reviews=[
                FacebookReview(text="Best flat white in KL", author="Sam Lee"),
                FacebookReview(text="Lovely staff", author="Aina R."),
            ]
        )
        out = _blocks(enforce_facebook_facts(_plan(invented), page))
        self.assertEqual([i.quote for i in out[0].items], ["Best flat white in KL", "Lovely staff"])
        self.assertEqual([i.author for i in out[0].items], ["Sam Lee", "Aina R."])

    def test_no_stock_portrait_is_attached_to_a_real_named_reviewer(self):
        """A generated face on a real person is the same class of error as a
        generated quote."""
        invented = TestimonialsBlock(
            items=[
                TestimonialItem(
                    quote="x", author="Jane Doe", avatar_query="smiling professional woman"
                )
            ]
        )
        page = _page(reviews=[FacebookReview(text="Great coffee", author="Sam Lee")])
        out = _blocks(enforce_facebook_facts(_plan(invented), page))
        self.assertIsNone(out[0].items[0].avatar_query)
        self.assertIsNone(out[0].items[0].role)

    def test_dropped_when_the_page_has_no_reviews(self):
        invented = TestimonialsBlock(
            items=[TestimonialItem(quote="Amazing service!", author="Jane Doe")]
        )
        self.assertEqual(_blocks(enforce_facebook_facts(_plan(invented), _page())), [])

    def test_capped_at_the_blocks_own_limit(self):
        page = _page(
            reviews=[FacebookReview(text=f"Review {i}", author=f"P{i}") for i in range(10)]
        )
        invented = TestimonialsBlock(items=[TestimonialItem(quote="x", author="y")])
        out = _blocks(enforce_facebook_facts(_plan(invented), page))
        self.assertEqual(len(out[0].items), 6)


class StatsTest(unittest.TestCase):
    def test_built_only_from_counts_facebook_reports(self):
        invented = StatsBlock(items=[StatItem(value="200+", label="Projects delivered")])
        page = _page(fan_count=1234, rating_count=56, overall_star_rating=4.8)
        out = _blocks(enforce_facebook_facts(_plan(invented), page))
        values = [i.value for i in out[0].items]
        self.assertIn("1,234", values)
        self.assertIn("4.8", values)
        self.assertNotIn("200+", values)

    def test_dropped_when_there_are_no_real_numbers(self):
        invented = StatsBlock(items=[StatItem(value="15 years", label="In business")])
        self.assertEqual(_blocks(enforce_facebook_facts(_plan(invented), _page())), [])

    def test_founded_date_counts_when_the_page_states_one(self):
        invented = StatsBlock(items=[StatItem(value="x", label="y")])
        out = _blocks(enforce_facebook_facts(_plan(invented), _page(founded="2015")))
        self.assertEqual(out[0].items[0].value, "2015")


class UntouchedBlocksTest(unittest.TestCase):
    def test_copy_blocks_are_left_to_the_model_and_the_grounding_net(self):
        hero = HeroBlock(headline="Coffee, roasted here", subheadline="Since 2015")
        out = _blocks(enforce_facebook_facts(_plan(hero), _page()))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].headline, "Coffee, roasted here")

    def test_block_order_is_preserved(self):
        pages = enforce_facebook_facts(
            _plan(
                HeroBlock(headline="A"),
                ContactBlock(),
                LocationsBlock(items=[LocationItem(name="x", address="y")]),
            ),
            _page(),
        )
        self.assertEqual([b.kind for b in _blocks(pages)], ["hero", "contact", "locations"])

    def test_every_page_is_processed_not_just_the_homepage(self):
        pages = enforce_facebook_facts(
            [
                *_plan(ContactBlock(email="wrong@x.com")),
                PagePlan(
                    page_type="contact",
                    slug="contact",
                    title="Contact",
                    description="",
                    is_homepage=False,
                    blocks=[ContactBlock(email="alsowrong@x.com")],
                    seo_title="Contact",
                    seo_description="",
                ),
            ],
            _page(),
        )
        for page in pages:
            self.assertEqual(page.blocks[0].email, "hello@acmecoffee.example")


if __name__ == "__main__":
    unittest.main()
