"""The pure Page readers, over synthetic markup in the shapes measured live on
logged-out Pages. Names and numbers are invented; the structure is Facebook's."""

import json
import unittest

from app.models.facebook import FacebookPage, FacebookPhoto, FacebookPost
from app.services.facebook_dom import (
    _scan_labelled,
    apply_originals,
    load_view,
    parse_public_html,
    read_photos,
    read_reviews,
    read_timeline,
)
from app.services.facebook_urls import parse_ref
from app.services.image_urls import is_cropped_photo_url, photo_identity

REF = parse_ref("https://www.facebook.com/sunnycatering")
FILE = "613920967_122160356984859713_5280887874409076164_n.jpg"
CROP = f"https://scontent.x.fbcdn.net/v/t39.30808-6/{FILE}?stp=c0.233.1294.1294a_dst-jpg_tt6&oh=a"
ORIGINAL = f"https://scontent.x.fbcdn.net/v/t39.30808-6/{FILE}?stp=dst-jpg_tt6&oh=b"


def _payload(*nodes: dict) -> str:
    """Facebook's inline data: nested `require` arrays around the real nodes."""
    return f'<script type="application/json">{json.dumps({"require": [[{"__bbox": list(nodes)}]]})}</script>'


def _page(body: str, head: str = "") -> str:
    return (
        '<html><head><meta property="og:title" content="Sunny Catering">'
        f"{head}</head><body><div role=\"main\">{body}</div></body></html>"
    )


class PhotoIdentityTest(unittest.TestCase):
    def test_a_crop_and_its_original_are_one_picture(self):
        self.assertEqual(photo_identity(CROP), photo_identity(ORIGINAL))
        self.assertEqual(photo_identity(CROP), f"fbcdn:{FILE}")

    def test_only_a_crop_token_marks_a_crop(self):
        self.assertTrue(is_cropped_photo_url(CROP))
        self.assertFalse(is_cropped_photo_url(ORIGINAL))
        scaled = ORIGINAL.replace("dst-jpg_tt6", "dst-jpg_s320x320_tt6")
        self.assertFalse(is_cropped_photo_url(scaled))
        self.assertFalse(is_cropped_photo_url("https://example.com/c0.1.2.3a.jpg"))


class InlinePayloadTest(unittest.TestCase):
    """The DOM paints a square crop and a truncated caption; the inline data
    the page was painted from carries the original and the whole text."""

    def test_a_tile_crop_is_swapped_for_the_payload_original(self):
        html = _page(
            f'<a href="/photo/?fbid=1"><img src="{CROP}"></a>',
            _payload({"image": {"uri": ORIGINAL, "width": 2048, "height": 1536}},
                     {"image": {"uri": CROP, "width": 1294, "height": 1294}}),
        )
        read = read_photos(load_view(html, REF.canonical_url))
        page = FacebookPage(name="Sunny", canonical_url=REF.canonical_url, posts=read.posts)
        apply_originals(page, read.originals)
        photo = page.posts[0].photos[0]
        self.assertEqual((photo.url, photo.width, photo.height), (ORIGINAL, 2048, 1536))

    def test_a_photo_no_payload_names_stays_as_read(self):
        page = FacebookPage(name="Sunny", canonical_url=REF.canonical_url,
                            posts=[FacebookPost(photos=[FacebookPhoto(url=CROP)])])
        apply_originals(page, {})
        self.assertEqual(page.posts[0].photos[0].url, CROP)

    def test_a_truncated_caption_reads_whole_from_the_payload(self):
        full = "Little lanterns, big smiles! 🏮\nCelebrating Mid-Autumn with every family. 中秋节快乐 #festival"
        html = _page(
            '<div role="article"><div data-ad-preview="message">Little lanterns, big smiles! '
            'Celebrating Mid-Autumn with every family. 中… <div role="button">See more</div></div></div>',
            _payload({"message": {"text": full}}),
        )
        posts = read_timeline(load_view(html, REF.canonical_url)).posts
        self.assertEqual([post.message for post in posts], [full])

    def test_a_text_only_post_is_kept_for_its_words(self):
        html = _page('<div role="article"><div data-ad-preview="message">'
                     "Enrolment for next year opens on Monday.</div></div>")
        posts = read_timeline(load_view(html, REF.canonical_url)).posts
        self.assertEqual(posts[0].message, "Enrolment for next year opens on Monday.")
        self.assertEqual(posts[0].photos, [])

    def test_a_cover_is_read_by_facebooks_own_attribute(self):
        html = _page(f'<img data-imgperflogname="profileCoverPhoto" src="{ORIGINAL}">')
        self.assertEqual(read_timeline(load_view(html)).cover_photo_url, ORIGINAL)

    def test_malformed_inline_data_is_ignored(self):
        html = _page('<script type="application/json">{not json</script><p>Hi</p>')
        self.assertEqual(read_timeline(load_view(html)).originals, {})


class IntroFactsTest(unittest.TestCase):
    def _read(self, body: str):
        return read_timeline(load_view(_page(body), REF.canonical_url))

    def test_the_intro_line_names_every_category(self):
        read = self._read("<strong>Page</strong><span>· Food and drink · Caterer · Snack van</span>")
        self.assertEqual(read.categories, ["Food and drink", "Caterer", "Snack van"])

    def test_service_options_are_facebooks_vocabulary_verbatim(self):
        read = self._read("<span>Takeaway · Roadside collection · In-store collection</span>")
        self.assertEqual(read.service_options, ["Takeaway", "Roadside collection", "In-store collection"])

    def test_a_dot_separated_line_of_anything_else_is_not_service_options(self):
        read = self._read("<span>Takeaway · Sunday roast</span><span>Delivery</span>")
        self.assertEqual(read.service_options, [])

    def test_ratings_are_read_rated_or_not(self):
        self.assertEqual(self._read("<span>Not yet rated (3 reviews)</span>").rating_count, 3)
        read = self._read("<span>Rating · 4.8 (1,204 reviews)</span>")
        self.assertEqual((read.overall_star_rating, read.rating_count), (4.8, 1204))

    def test_outbound_profiles_are_unwrapped_and_posts_ignored(self):
        wrap = "https://l.facebook.com/l.php?u={}&h=AT0"
        read = self._read(
            f'<a href="{wrap.format("https%3A%2F%2Fwww.instagram.com%2Fsunny%3Ffbclid%3Dx")}">sunny</a>'
            f'<a href="{wrap.format("http%3A%2F%2Fsunny.example%2F")}">sunny.example</a>'
            f'<div role="article"><a href="{wrap.format("https%3A%2F%2Fwww.tiktok.com%2F%40other")}">x</a></div>'
        )
        self.assertEqual(read.social_profiles, ["https://www.instagram.com/sunny"])


class TruncatedAboutTest(unittest.TestCase):
    """og:description clips the bio; the Intro card shows it whole."""

    OG = ('<meta property="og:description" content="Sunny Catering, Shah Alam. 40 likes. '
          'Established 1999. We cook fresh food for weddings and preparing them...">')

    def test_the_intro_completes_a_clipped_blurb(self):
        full = "Established 1999. We cook fresh food for weddings and preparing them for every guest."
        page = parse_public_html(_page(f"<span>Intro</span><span>{full}</span>", self.OG), REF)
        self.assertEqual(page.about, full)

    def test_an_unrelated_intro_line_never_replaces_it(self):
        page = parse_public_html(_page("<span>Intro</span><span>Page</span>", self.OG), REF)
        self.assertTrue(page.about.endswith("preparing them..."))


class LabelLayoutTest(unittest.TestCase):
    """The About panel writes contact rows value-first; reading such a run
    forward paired every label with the NEXT row's value, so a measured
    Page's street address became its service area."""

    ABOUT = [
        "Contact info",
        "1, Jalan Setia 12, Shah Alam, 40170",
        "Address",
        "Shah Alam, Selangor, Malaysia",
        "Service area",
        "03-3358 0000",
        "Mobile",
        "hello@sunny.example",
        "Email address",
        "Websites and social links",
        "http://www.sunny.example/",
        "Website",
        "sunny_catering",
        "Instagram",
        "Basic info",
        "Closed now",
        "Hours",
        "Price range · ££",
        "Price",
        "Reels",
    ]

    def test_a_value_first_run_is_read_backward(self):
        found = _scan_labelled(self.ABOUT)
        self.assertEqual(found["single_line_address"], "1, Jalan Setia 12, Shah Alam, 40170")
        self.assertEqual(found["service_area"], "Shah Alam, Selangor, Malaysia")
        self.assertEqual(found["phone"], "03-3358 0000")
        self.assertEqual(found["email"], "hello@sunny.example")
        self.assertEqual(found["website"], "http://www.sunny.example/")
        self.assertEqual(found["price_range"], "££")

    def test_a_label_first_run_is_still_read_forward(self):
        found = _scan_labelled(["Address", "12 Jalan Sultan", "Phone", "+60 3 1234 5678"])
        self.assertEqual(found["single_line_address"], "12 Jalan Sultan")
        self.assertEqual(found["phone"], "+60 3 1234 5678")

    def test_the_page_carries_the_new_facts(self):
        body = "".join(f"<div>{line}</div>" for line in self.ABOUT)
        page = parse_public_html(_page(body), REF)
        self.assertEqual(page.service_area, "Shah Alam, Selangor, Malaysia")
        self.assertEqual(page.price_range, "££")


class RecommendationsTest(unittest.TestCase):
    def _article(self, author: str, verdict: str) -> str:
        return (
            f'<div role="article"><h2><b><a href="/person">{author}</a></b> {verdict} '
            '<b><a href="/sunnycatering">Sunny Catering</a></b>.</h2>'
            "<span>4 November 2025</span><span>·</span><span>All reactions:</span></div>"
        )

    def test_a_recommendation_quotes_the_reviewers_own_words(self):
        html = _page(
            self._article("Aina Rahman", "recommends") + self._article("Ben Ong", "doesn't recommend"),
            _payload(
                {"actors": [{"name": "Aina Rahman"}], "message": {"text": "Lovely food, always on time."}},
                {"actors": [{"name": "Ben Ong"}], "message": {"text": "Late twice."}},
            ),
        )
        reviews = read_reviews(load_view(html, REF.canonical_url)).reviews
        self.assertEqual([(r.author, r.text) for r in reviews],
                         [("Aina Rahman", "Lovely food, always on time.")])

    def test_a_recommendation_with_no_words_has_nothing_to_quote(self):
        html = _page(self._article("Aina Rahman", "recommends"))
        self.assertEqual(read_reviews(load_view(html, REF.canonical_url)).reviews, [])


class MissingFieldsTest(unittest.TestCase):
    def test_a_page_that_states_zero_reviews_misses_none(self):
        page = parse_public_html(_page("<span>Not yet rated (0 reviews)</span>"), REF)
        self.assertEqual(page.rating_count, 0)
        self.assertNotIn("reviews", page.missing_fields)
        self.assertIn("posts", page.missing_fields)
        self.assertTrue(page.partial)


if __name__ == "__main__":
    unittest.main()
