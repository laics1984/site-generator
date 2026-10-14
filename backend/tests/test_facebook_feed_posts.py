"""The Page's own posts, read whole from the data its views already carry.

Measured on a real childcare Page (a capture replayed by
`scripts/measure_facebook_read.py`): the About page the reader loads first
carries ten of the Page's recent posts as Story objects in its reels collection
(`aggregated_fb_shorts`) — words, date, id, video and the frame Facebook shows
for it. The reader kept one of them, found by the timeline's painted article,
and that one with no date, no id and no picture. These tests pin the shapes that
capture showed, stripped to the keys the reader reads.
"""

from __future__ import annotations

import json

from app.config import settings
from app.models.content_blocks import SourceContent
from app.models.facebook import FacebookPage, FacebookPhoto, FacebookPost
from app.services.facebook_dom import (
    ViewRead,
    finish_views,
    keep_larger_originals,
    load_view,
    merge_view,
    read_photos,
    read_reviews,
    read_timeline,
)
from app.services.facebook_source import to_image_metadata
from app.services.facebook_urls import parse_ref
from app.services.source_router import image_prompt_payload

REF = parse_ref("https://www.facebook.com/thecorelearningcentre")
FRAME = "https://scontent.fkul10-2.fna.fbcdn.net/v/t51.82787-10/825322674_18195969934385966_63_n.jpg?stp=dst-jpg_tt6"
PHOTO = "https://scontent.fkul10-2.fna.fbcdn.net/v/t51.82787-15/839594216_18196958218385966_74_n.jpg?stp=dst-jpg"
CAPTION = "Float or Sink? Let's find out! Our 3-6-year-olds explored float and sink through hands-on experiments."


def _story(post_id="1698901405569947", message=CAPTION, created=1790485375, attachments=None, **extra):
    story = {
        "__typename": "Story", "id": "UzpfS", "post_id": post_id, "creation_time": created,
        "actors": [{"__typename": "User", "id": "100063503116317", "name": "The CORE Learning Centre"}],
        "attachments": attachments if attachments is not None else [_reel()],
        "comet_sections": {"content": {"story": {"message": {"text": message}}}},
    }
    story.update(extra)
    return story


def _reel(video_id="2346135962825439"):
    return {"media": {
        "__typename": "Video", "id": video_id, "width": 1080, "height": 1920,
        "playable_duration_in_ms": 98933,
        "permalink_url": f"https://www.facebook.com/reel/{video_id}/",
        "preferred_thumbnail": {"image": {"uri": FRAME}},
    }}


def _photo(uri=PHOTO, alt="May be an image of child, gelato and text"):
    return {"__typename": "Photo", "id": "1709992811127473", "accessibility_caption": alt,
            "viewer_image": {"uri": uri, "width": 1675, "height": 2000}}


def _page_html(data: dict, body: str = "") -> str:
    return f'<html><body><div role="main">{body}</div><script type="application/json">{json.dumps(data)}</script></body></html>'


def _reels_collection(*stories):
    """The About page's reels, where the capture found them."""
    edges = [{"profile_reel_node": {"node": story}} for story in stories]
    return {"data": {"timeline_nav_app_sections": {"edges": [{"node": {"all_collections": {"edges": [
        {"node": {"style_renderer": {"collection": {"aggregated_fb_shorts": {"edges": edges}}}}}
    ]}}}]}}}


def _timeline_feed(*stories):
    return {"data": {"user": {"timeline_list_feed_units": {"edges": [{"node": s} for s in stories]}}}}


def _page() -> FacebookPage:
    return FacebookPage(name="The CORE Learning Centre", canonical_url=REF.canonical_url, category="Childcare service")


def test_a_reel_in_the_about_pages_collection_is_a_whole_post():
    posts = read_timeline(load_view(_page_html(_reels_collection(_story())), REF.canonical_url)).posts
    assert len(posts) == 1
    post = posts[0]
    assert post.message == CAPTION
    assert post.post_id == "1698901405569947"
    assert post.created_time.startswith("2026-09-27")
    assert post.permalink == "https://www.facebook.com/reel/2346135962825439/"
    assert [(v.video_id, v.thumbnail_url, v.width, v.height) for v in post.videos] == [
        ("2346135962825439", FRAME, 1080, 1920)
    ]
    assert [p.url for p in post.pictures] == [FRAME], "the frame illustrates the caption"


def test_a_photo_post_carries_facebooks_description_of_each_photo():
    story = _story(attachments=[{"styles": {"attachment": {"all_subattachments": {"nodes": [
        {"media": _photo()},
    ]}}}}])
    post = read_timeline(load_view(_page_html(_timeline_feed(story)), REF.canonical_url)).posts[0]
    assert [(p.url, p.width, p.height, p.auto_alt) for p in post.photos] == [
        (PHOTO, 1675, 2000, "May be an image of child, gelato and text")
    ]


def test_someone_elses_story_is_never_the_pages_post():
    review = {"data": {"user": {"ratings_list_feed_units": {"reviews_feed": {"edges": [
        {"node": _story(post_id="2095273190928575", message="Lovely teachers, my son loves it here.")}
    ]}}}}}
    view = load_view(_page_html(review), REF.canonical_url)
    assert read_timeline(view).posts == []
    assert read_reviews(view).posts == []


def test_a_reshare_keeps_the_pages_words_not_the_original_posters():
    shared = _story(post_id="999", message="Someone else's announcement")
    story = _story(message="We loved this — have a look!", attached_story=shared, attachments=[])
    posts = read_timeline(load_view(_page_html(_timeline_feed(story)), REF.canonical_url)).posts
    assert [post.message for post in posts] == ["We loved this — have a look!"]


def test_the_painted_article_and_the_data_are_one_post():
    article = f'<div role="article"><div data-ad-preview="message">{CAPTION}</div></div>'
    view = load_view(_page_html(_timeline_feed(_story()), article), REF.canonical_url)
    page = _page()
    merge_view(page, read_timeline(view))
    assert len(page.posts) == 1
    assert page.posts[0].post_id == "1698901405569947"


def test_a_caption_less_tile_takes_its_storys_words():
    """A tile read first and the story of the same photo read later are one post."""
    page = _page()
    merge_view(page, ViewRead(posts=[FacebookPost(photos=[FacebookPhoto(url=PHOTO)])]))
    merge_view(page, ViewRead(posts=[
        FacebookPost(message=CAPTION, post_id="42", created_time="2026-09-27T00:00:00+00:00",
                     photos=[FacebookPhoto(url=PHOTO)]),
    ]))
    assert len(page.posts) == 1
    assert (page.posts[0].message, page.posts[0].post_id) == (CAPTION, "42")
    assert [p.url for p in page.posts[0].photos] == [PHOTO]


def test_two_captioned_posts_sharing_a_photo_stay_two_posts():
    page = _page()
    merge_view(page, ViewRead(posts=[
        FacebookPost(message="Sports day was a blast", photos=[FacebookPhoto(url=PHOTO)]),
        FacebookPost(message="Throwback to sports day", photos=[FacebookPhoto(url=PHOTO)]),
    ]))
    assert [post.message for post in page.posts] == ["Sports day was a blast", "Throwback to sports day"]
    assert [len(post.photos) for post in page.posts] == [1, 0]


def test_a_frame_counts_against_the_picture_cap_but_the_video_survives(monkeypatch):
    monkeypatch.setattr(settings, "facebook_photo_max_images", 0)
    page = _page()
    merge_view(page, read_timeline(load_view(_page_html(_reels_collection(_story())), REF.canonical_url)))
    assert page.posts[0].videos[0].thumbnail_url is None
    assert page.post_photo_urls == []


def test_the_photos_tab_carries_facebooks_description_to_the_tile():
    tile = f'<a href="/photo/?fbid=1709992811127473"><img src="{PHOTO}&small=1"></a>'
    view = load_view(_page_html({"data": {"node": _photo()}}, tile), REF.canonical_url)
    read = read_photos(view)
    page = _page()
    merge_view(page, read)
    finish_views(page, dict(read.originals))
    assert page.posts[0].photos[0].auto_alt == "May be an image of child, gelato and text"


def test_the_larger_rendition_keeps_the_description_a_smaller_one_carried():
    held = {"fbcdn:x": FacebookPhoto(url="https://a.fbcdn.net/111111111_x.jpg", width=10, height=10, auto_alt="cake")}
    keep_larger_originals(held, {"fbcdn:x": FacebookPhoto(url="https://a.fbcdn.net/111111111_x.jpg?big", width=99, height=99)})
    assert (held["fbcdn:x"].width, held["fbcdn:x"].auto_alt) == (99, "cake")


def test_frames_reach_the_photo_pool_with_their_caption_and_description():
    page = _page().model_copy(update={"category": "Cafe"})
    page.posts = [
        FacebookPost(message=CAPTION, videos=read_timeline(load_view(
            _page_html(_reels_collection(_story())), REF.canonical_url)).posts[0].videos),
        FacebookPost(photos=[FacebookPhoto(url=PHOTO, auto_alt="May be an image of dessert")]),
    ]
    metadata = {meta.url: meta for meta in to_image_metadata(page)}
    assert metadata[FRAME].caption == CAPTION
    assert metadata[PHOTO].platform_alt == "May be an image of dessert"
    source = SourceContent(source_kind="facebook", source_ref=REF.canonical_url, raw_text="x",
                           image_metadata=list(metadata.values()))
    entries = {entry["ref"]: entry for entry in image_prompt_payload(source)}
    assert any(entry.get("platform_alt") == "May be an image of dessert" for entry in entries.values())
