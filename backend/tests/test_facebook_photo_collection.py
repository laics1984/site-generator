"""The views stage: what a render collects after the About read, and how.

Parsing is driven with inline HTML (`facebook_dom` is pure); navigation with a
fake browser that records every URL it is asked to open.
"""

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

from app.config import settings
from app.models.facebook import FacebookPage, FacebookPhoto, FacebookPost
from app.services import facebook_render as render
from app.services.facebook_dom import load_view, merge_view, read_photos, read_timeline, ViewRead
from app.services.facebook_urls import parse_ref, tab_url


REF = parse_ref("https://www.facebook.com/littleschool")
PHOTO = "https://x.fbcdn.net/v/t39.30808-6/123456789_987654321_n.jpg"


def test_photos_tab_tiles_do_not_need_post_articles():
    html = f'''
      <a href="/photo/?fbid=123"><img src="{PHOTO}"></a>
      <a href="/photo/?fbid=123"><img src="{PHOTO}"></a>
      <a href="/photo/?fbid=456"><img src="https://x.fbcdn.net/images/emoji.png"></a>
      <a href="/photo/?fbid=789"><img src="https://x.fbcdn.net/v/t39.30808-1/avatar.jpg"></a>
      <a href="https://other.test/photo/"><img src="https://x.fbcdn.net/unrelated.jpg"></a>
      <a aria-hidden="true" href="/photo/?fbid=999"><img src="https://x.fbcdn.net/hidden.jpg"></a>
      <a href="/littleschool/photos"><img src="https://x.fbcdn.net/nav.jpg"></a>
    '''
    view = load_view(html, REF.canonical_url)
    assert read_timeline(view).posts == []
    posts = read_photos(view).posts
    assert [p.url for post in posts for p in post.photos] == [PHOTO]
    assert posts[0].message is None


def test_merge_deduplicates_resized_photos_and_keeps_captions(monkeypatch):
    monkeypatch.setattr(settings, "facebook_photo_max_images", 2)
    page = FacebookPage(name="School", canonical_url=REF.canonical_url,
                        profile_picture_url="https://x.fbcdn.net/999999999_logo.jpg")
    merge_view(page, ViewRead(posts=[
        FacebookPost(message="Reading together", photos=[FacebookPhoto(url=PHOTO + "?size=large")]),
        FacebookPost(photos=[FacebookPhoto(url=PHOTO + "?size=small")]),
        FacebookPost(photos=[FacebookPhoto(url=page.profile_picture_url + "?size=small")]),
        FacebookPost(photos=[FacebookPhoto(url="https://x.fbcdn.net/222222222_next.jpg")]),
        FacebookPost(photos=[FacebookPhoto(url="https://x.fbcdn.net/333333333_extra.jpg")]),
    ]))
    assert len(page.post_photo_urls) == 2
    assert page.posts[0].message == "Reading together"


def test_a_full_photo_cap_still_keeps_captions(monkeypatch):
    """Two caps, kept apart: a full Photos tab must not crowd out the words."""
    monkeypatch.setattr(settings, "facebook_photo_max_images", 1)
    page = FacebookPage(name="School", canonical_url=REF.canonical_url)
    merge_view(page, ViewRead(posts=[
        FacebookPost(photos=[FacebookPhoto(url=PHOTO)]),
        FacebookPost(message="Our sports day is on Saturday, and every family is welcome."),
        FacebookPost(message="Our sports day is on Saturday, and every family is welcome."),
    ]))
    assert page.post_photo_urls == [PHOTO]
    assert [post.message for post in page.posts_with_text] == [
        "Our sports day is on Saturday, and every family is welcome."
    ]


def test_merge_fills_gaps_and_never_overwrites():
    page = FacebookPage(name="School", canonical_url=REF.canonical_url,
                        category="Nursery", cover_photo_url="https://x.fbcdn.net/111111111_cover.jpg")
    merge_view(page, ViewRead(categories=["Preschool", "Childcare service"],
                              cover_photo_url="https://x.fbcdn.net/222222222_other.jpg",
                              rating_count=3))
    assert page.category == "Nursery"
    assert page.categories == ["Preschool", "Childcare service"]
    assert page.cover_photo_url.endswith("111111111_cover.jpg")
    assert page.rating_count == 3


def test_public_main_photos_survive_outer_modal_accessibility_wrapper():
    html = f'''<div aria-hidden="true"><main role="main">
      <a href="/photo.php?fbid=123"><img src="{PHOTO}"></a>
      <div aria-hidden="true"><a href="/photo/?fbid=456">
        <img src="https://x.fbcdn.net/hidden.jpg"></a></div>
    </main></div><div role="dialog">Log in</div>'''
    posts = read_photos(load_view(html, REF.canonical_url)).posts
    assert [photo.url for post in posts for photo in post.photos] == [PHOTO]


def _fake_browser(monkeypatch, responses):
    visited = []
    monkeypatch.setattr(settings, "facebook_render_fallback_enabled", True)

    @asynccontextmanager
    async def context(**kwargs):
        yield object()

    @asynccontextmanager
    async def page(context, url, **kwargs):
        visited.append(url)
        response = responses.get(url, RuntimeError("not served"))
        if isinstance(response, Exception):
            raise response
        view = AsyncMock()
        view.url = url
        view.content.return_value = response
        yield view

    monkeypatch.setattr(render, "browser_context", context)
    monkeypatch.setattr(render, "rendered_page", page)
    monkeypatch.setattr(settings, "facebook_view_scroll_steps", 0)
    return visited


def test_successful_about_read_still_collects_views_without_overwriting_facts(monkeypatch):
    root = REF.canonical_url
    about = '<meta property="og:title" content="Little School"><div>Phone</div><div>03-1234 5678</div>'
    visited = _fake_browser(monkeypatch, {
        root + "/about": about,
        root: '<meta property="og:title" content="Wrong timeline title"><div>Phone</div><div>09-9999 9999</div>',
        root + "/photos": f'<a href="/photo/?fbid=123"><img src="{PHOTO}"></a>',
    })
    page = asyncio.run(render.fetch_page(REF))
    assert visited == [root + "/about", root, root + "/photos", root + "/reviews"]
    assert page.name == "Little School"
    assert page.phone == "03-1234 5678"
    assert page.post_photo_urls == [PHOTO]
    assert page.partial is True
    assert "posts" not in page.missing_fields


def test_a_ladder_that_fell_back_to_the_timeline_does_not_render_it_twice(monkeypatch):
    root = REF.canonical_url
    visited = _fake_browser(monkeypatch, {
        root + "/about": RuntimeError("unavailable"),
        root: '<meta property="og:title" content="Little School">',
    })
    asyncio.run(render.fetch_page(REF))
    assert visited.count(root) == 1


def test_view_failures_preserve_the_successful_about_read(monkeypatch):
    root = REF.canonical_url
    _fake_browser(monkeypatch, {
        root + "/about": '<meta property="og:title" content="Little School">',
        root: RuntimeError("unavailable"),
    })
    page = asyncio.run(render.fetch_page(REF))
    assert page.name == "Little School"
    assert page.posts == []
    assert "posts" in page.missing_fields


def test_the_first_wall_stops_the_stage(monkeypatch):
    """Repeated anonymous views are what make Facebook start refusing."""
    root = REF.canonical_url
    visited = _fake_browser(monkeypatch, {
        root + "/about": '<meta property="og:title" content="Little School">',
        root: '<div>You must log in to continue</div>',
        root + "/photos": f'<a href="/photo/?fbid=123"><img src="{PHOTO}"></a>',
    })
    page = asyncio.run(render.fetch_page(REF))
    assert visited == [root + "/about", root]
    assert page.name == "Little School"


def test_the_time_budget_preserves_the_about_facts(monkeypatch):
    root = REF.canonical_url
    _fake_browser(monkeypatch, {root + "/about": '<meta property="og:title" content="School">'})
    original = render.rendered_page

    @asynccontextmanager
    async def slow(context, url, **kwargs):
        if url == root + "/about":
            async with original(context, url, **kwargs) as page:
                yield page
        else:
            await asyncio.sleep(10)
            yield AsyncMock()

    monkeypatch.setattr(render, "rendered_page", slow)
    monkeypatch.setattr(settings, "facebook_views_timeout_seconds", 0.01)
    page = asyncio.run(render.fetch_page(REF))
    assert page.name == "School"


def test_numeric_page_tab_urls():
    ref = parse_ref("https://www.facebook.com/profile.php?id=123456789")
    assert tab_url(ref, "photos").endswith("id=123456789&sk=photos")
    assert render._candidate_urls(ref)[0].endswith("id=123456789&sk=about")
