"""A post is a post wherever its permalink puts it.

Measured on mykiddyland.com's crawl: eleven WordPress posts at root permalinks
(/kindergarten-selangor/, …) were scaffolded as top-level `services` pages —
one as a `team` directory — and none was migrated, because only posts nested
under a blog/events listing were. Their markup says what they are
(`declared_entry`, services/entry_markup.py); page inference and the article
migration now read that one answer (`page_inference.collection_entries`).
"""

import asyncio
import unittest
from unittest import mock

from app.models.content_blocks import NavLink, SourceContent
from app.services import content_collections
from app.services.page_inference import collection_entries, infer_page_scaffolds

ORIGIN = "https://kiddy.example"
POSTS = ("kindergarten-selangor", "5-things-not-to-do-when-your-kids-are-being-disrespectful")


def _page(path: str, title: str, *, declared: str | None = None) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref=f"{ORIGIN}{path}",
        title=title,
        raw_text=f"{title}. Copy that belongs to this page and no other.",
        url_path=path,
        declared_entry=declared,
    )


def _site(*, declared: bool = True, blog_listing: bool = True, nav_post: bool = False) -> SourceContent:
    kind = "article" if declared else None
    nav = [
        NavLink(label="Home", href="/"),
        NavLink(label="About", href="/about/"),
        NavLink(label="Programmes", href="/programmes/"),
        NavLink(label="Contact", href="/contact/"),
    ]
    if blog_listing:
        nav.append(NavLink(label="Blog", href="/blog/"))
    if nav_post:
        nav.append(NavLink(label="Selangor", href=f"/{POSTS[0]}/"))
    pages = [
        _page("/about/", "More Info About Kiddy"),
        _page("/programmes/", "Programmes"),
        _page("/programmes/infantgroup/", "Infant Group"),
        _page("/contact/", "Contact"),
        _page(f"/{POSTS[0]}/", "Kindergarten in Selangor", declared=kind),
        _page(f"/{POSTS[1]}/", "5 Things Not to Do", declared=kind),
        # WordPress files a post's attachment page under the post.
        _page(f"/{POSTS[0]}/classroom-photo/", "classroom-photo"),
    ]
    if blog_listing:
        pages.append(_page("/blog/", "Blog"))
    return SourceContent(
        source_kind="url",
        source_ref=ORIGIN,
        raw_text="Home page text.",
        nav_links=nav,
        discovered_pages=pages,
    )


def _slugs(source: SourceContent) -> set[str]:
    return {s.slug for s in infer_page_scaffolds(source, industry="childcare")}


class RootPermalinkPostTest(unittest.TestCase):
    def test_declared_posts_are_not_scaffolded_and_real_pages_are(self):
        slugs = _slugs(_site())

        self.assertTrue({"about", "programmes", "programmes/infantgroup", "contact", "blog"} <= slugs)
        for post in POSTS:
            self.assertNotIn(post, slugs)
        self.assertNotIn(f"{POSTS[0]}/classroom-photo", slugs)

    def test_without_a_declaration_the_same_pages_stay_pages(self):
        slugs = _slugs(_site(declared=False))

        for post in POSTS:
            self.assertIn(post, slugs)

    def test_the_migration_fetches_exactly_what_the_page_set_dropped(self):
        source = _site()

        self.assertEqual(set(collection_entries(source)), set(POSTS))
        self.assertEqual(
            content_collections._declared_entry_urls(source),
            {"blog": [f"{ORIGIN}/{post}/" for post in POSTS], "events": []},
        )

    def test_a_post_the_owner_put_in_their_menu_is_a_page(self):
        source = _site(nav_post=True)

        self.assertIn(POSTS[0], _slugs(source))
        self.assertNotIn(POSTS[0], collection_entries(source))

    def test_posts_with_no_listing_crawled_get_one(self):
        scaffolds = infer_page_scaffolds(_site(blog_listing=False), industry="childcare")
        blog = next(s for s in scaffolds if s.slug == "blog")

        self.assertEqual(blog.page_type, "blog")
        self.assertIsNone(blog.parent_slug)

    def test_a_declared_event_is_listed_under_events(self):
        source = SourceContent(
            source_kind="url",
            source_ref=ORIGIN,
            raw_text="Home page text.",
            discovered_pages=[
                _page("/about/", "About"),
                _page("/open-day-2026/", "Open Day 2026", declared="event"),
            ],
        )
        scaffolds = {s.slug: s for s in infer_page_scaffolds(source, industry="childcare")}

        self.assertNotIn("open-day-2026", scaffolds)
        self.assertEqual(scaffolds["events"].page_type, "events")
        self.assertEqual(
            content_collections._declared_entry_urls(source)["events"],
            [f"{ORIGIN}/open-day-2026/"],
        )

    def test_a_translated_mirror_of_a_post_is_not_a_second_entry(self):
        source = _site()
        source.discovered_pages.append(_page(f"/zh/{POSTS[0]}/", "雪兰莪幼儿园", declared="article"))

        self.assertNotIn(f"zh/{POSTS[0]}", collection_entries(source))
        self.assertNotIn(f"zh/{POSTS[0]}", _slugs(source))


def _post_html(title: str) -> str:
    return (
        f'<html><head><meta property="og:title" content="{title}"></head>'
        '<body class="single single-post postid-9"><article>'
        f"<h1>{title}</h1><p>A post long enough to be a real entry rather than a stub: "
        "what to pack on the first day, and how to say goodbye at the gate.</p>"
        "</article></body></html>"
    )


class MigrationTest(unittest.TestCase):
    def _extract(self, source: SourceContent, listing_links: list[str]) -> tuple[list[str], list[str]]:
        fetched: list[str] = []

        async def fetch(url: str, _budget: list[int]) -> str:
            fetched.append(url)
            return _post_html(url.rstrip("/").rsplit("/", 1)[-1])

        for page in source.discovered_pages:
            if page.url_path == "/blog/":
                page.links = listing_links
        with mock.patch.object(content_collections, "_fetch_html", fetch):
            cols = asyncio.run(content_collections.extract_collections(source))
        return fetched, [a.slug for a in cols.articles]

    def test_root_posts_are_migrated_with_no_listing_at_all(self):
        fetched, slugs = self._extract(_site(blog_listing=False), [])

        self.assertEqual(sorted(slugs), sorted(POSTS))
        self.assertEqual(len(fetched), 2)

    def test_a_post_the_listing_also_reaches_is_fetched_once(self):
        # A dated permalink the listing finds itself, which the crawl also read.
        source = _site()
        dated = _page("/2024/05/sports-day/", "Sports Day", declared="article")
        source.discovered_pages.append(dated)

        fetched, slugs = self._extract(source, [f"{ORIGIN}/2024/05/sports-day"])

        self.assertEqual(sorted(fetched), sorted({*fetched}))
        self.assertEqual(sorted(slugs), sorted([*POSTS, "sports-day"]))


if __name__ == "__main__":
    unittest.main()
