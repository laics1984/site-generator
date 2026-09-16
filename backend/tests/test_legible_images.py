"""Images whose message is in their pixels: kept out of every slot, placed whole.

watr.org.my is the fixture throughout, as it was the bug: a DuitNow donation
code in the footer of all six pages stretched behind an About headline and
cropped into another About split, an Alpha course poster cropped into a hero,
and a volunteering slide carrying its own link code cropped into another. The
tests follow the path — pixel readings → the slot gate → placement → the
rendered section — plus the two places a code could still leak out: an LLM
image_ref and the page's social card.
"""

import asyncio
import unittest
from unittest import mock

from app.config import settings
from app.models.builder_schema import BuilderElement, BuilderElementContent
from app.models.content_blocks import (
    DETERMINISTIC_SECTION_KINDS,
    ContactBlock,
    CtaBlock,
    GalleryBlock,
    GalleryItem,
    HeroBlock,
    ImageMetadata,
    PagePlan,
    PosterBlock,
    QrBlock,
    ServiceItem,
    ServicesBlock,
    SourceContent,
)
from app.services import text_detection
from app.services.image_match import (
    bears_text,
    hides_legible_content,
    must_show_whole,
    rank_candidates,
)
from app.services.image_refs import bind_image_refs, referenced_images
from app.services.legible_images import inject_legible_images
from app.services.section_content import _poster_content, _qr_content, select_template
from app.services.seo import extract_og_image, scan_code_urls
from app.services.template_filler import fill_template
from app.services.video_embed import is_player_thumbnail

from tests.test_qr_codes import WATR_DUITNOW

QR_URL = "https://watr.org.my/wp-content/uploads/2024/11/WhatsApp-Image-2024-11-01.jpg"
POSTER_URL = "https://watr.org.my/wp-content/uploads/2023/04/Alpha-Social.jpg"
SLIDE_URL = "https://watr.org.my/wp-content/uploads/2023/04/Announcements-Loop-6.jpg"
PHOTO_URL = "https://watr.org.my/wp-content/uploads/2020/02/Galleria_Pic1.jpg"
SLIDE_LINK = "https://qr.page/g/5fcQ2xyDvRX"

SLUGS = ("", "about-us", "empowered-work-life", "explore-life", "contact-us", "getting-involved")


def _img(url: str, **kw) -> ImageMetadata:
    base = dict(url=url, alt="", intent="generic", role="unknown",
                width=1080, height=1080, source_usage="inline")
    base.update(kw)
    return ImageMetadata(**base)


def _code(url: str = QR_URL, payload: str = WATR_DUITNOW, **kw) -> ImageMetadata:
    return _img(url, ocr_has_text=False, qr_payload=payload, **kw)


def _poster(url: str = POSTER_URL, **kw) -> ImageMetadata:
    return _img(url, ocr_has_text=True, **kw)


def _photo(url: str = PHOTO_URL, **kw) -> ImageMetadata:
    return _img(url, ocr_has_text=False, **kw)


def _source_page(slug: str, images: list[ImageMetadata]) -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref=f"https://watr.org.my/{slug}",
        raw_text="",
        url_path=f"/{slug}/" if slug else None,
        # Each crawled page carries its OWN copies, unstamped — as the scraper does.
        image_metadata=[_img(m.url, context_heading=m.context_heading) for m in images],
    )


def _site(pages: dict[str, list[ImageMetadata]]) -> SourceContent:
    entry, *rest = [_source_page(slug, images) for slug, images in pages.items()]
    return entry.model_copy(update={"discovered_pages": rest})


def _plan(slug: str, *, contact: bool = False) -> PagePlan:
    blocks = [HeroBlock(headline="Workplace @theRiver")]
    if contact:
        blocks.append(ContactBlock(email="connect@watr.org.my"))
    blocks.append(CtaBlock(headline="Join us", cta_label="Get involved", cta_href="/getting-involved"))
    return PagePlan(
        page_type="home" if slug == "" else "about",
        slug=slug,
        title=slug or "Home",
        description="",
        is_homepage=slug == "",
        blocks=blocks,
        seo_title="t",
        seo_description="d",
    )


def _kinds(page: PagePlan) -> list[str]:
    return [block.kind for block in page.blocks]


def _walk(el):
    yield el
    if isinstance(el.content, list):
        for child in el.content:
            yield from _walk(child)


async def _no_stock(query: str):
    raise AssertionError(f"a legible image section asked for stock: {query!r}")


def _render(kind: str, content: dict) -> BuilderElement:
    return asyncio.run(fill_template(select_template(kind, content), content, resolve_image=_no_stock))


# --- 1. which images must be shown whole ----------------------------------------


class LegibilityTest(unittest.TestCase):
    def test_a_code_must_be_shown_whole_though_ocr_saw_little_text(self):
        """watr's code measured 3.6% text coverage — its caption line only — so
        the text flag alone passed it as a photograph."""
        code = _code()
        self.assertFalse(code.ocr_has_text)
        self.assertTrue(must_show_whole(code))

    def test_words_that_are_the_content_must_be_shown_whole(self):
        self.assertTrue(must_show_whole(_poster()))
        self.assertTrue(must_show_whole(_img(PHOTO_URL, vision_kind="banner")))

    def test_a_legible_sign_bars_only_the_background(self):
        """Any readable words collide with a headline; only words that ARE the
        picture are lost to a crop."""
        shopfront = _photo(vision_has_text=True)
        self.assertTrue(bears_text(shopfront))
        self.assertFalse(must_show_whole(shopfront))
        self.assertTrue(hides_legible_content(shopfront, "background"))
        self.assertFalse(hides_legible_content(shopfront, "inline"))

    def test_every_slot_usage_refuses_a_code_or_a_poster(self):
        for usage in ("background", "inline", "any"):
            for image in (_code(), _poster()):
                with self.subTest(usage=usage, url=image.url):
                    self.assertTrue(hides_legible_content(image, usage))

    def test_the_ranker_never_crops_a_poster_into_a_featured_slot(self):
        """The Alpha poster was the /about-us editorial hero, cropped to 3:4."""
        poster = _poster(intent="hero")
        photo = _photo(intent="hero")
        result = rank_candidates("alpha course", "hero", [poster, photo], slot_usage="inline")
        self.assertIs(result.chosen, photo)


class PixelReadingTest(unittest.TestCase):
    def test_one_decode_stamps_both_readings(self):
        """The code is read off the frame OCR already decoded — no second fetch."""
        frame = object()
        reading = text_detection.PixelReading(has_text=False, qr_payload=WATR_DUITNOW)
        pool = [_img(QR_URL)]
        with mock.patch.object(settings, "ocr_text_detection_enabled", True), \
             mock.patch.object(text_detection, "_engine", return_value=object()), \
             mock.patch.object(text_detection, "_payloads", return_value=[(QR_URL, frame)]), \
             mock.patch.object(text_detection, "read_pixels", return_value=reading) as read, \
             mock.patch.dict(text_detection._TEXT_CACHE, clear=True):
            asyncio.run(text_detection.verify_many(pool))
        read.assert_called_once_with(frame)
        self.assertFalse(pool[0].ocr_has_text)
        self.assertEqual(pool[0].qr_payload, WATR_DUITNOW)


# --- 2. placement -----------------------------------------------------------------


class PlacementTest(unittest.TestCase):
    def _watr(self):
        code = _code()
        poster = _poster(context_heading="Explore Life")
        slide = _poster(SLIDE_URL, qr_payload=SLIDE_LINK, context_heading="Explore Life")
        pages = {slug: [code] for slug in SLUGS}
        pages["explore-life"] = [poster, slide, code]
        return _site(pages), [code, poster, slide]

    def test_a_sitewide_code_is_placed_once_on_the_homepage(self):
        source, pool = self._watr()
        plans = [_plan(slug) for slug in SLUGS]
        inject_legible_images(plans, source, pool)
        with_codes = [plan.slug for plan in plans if "qr" in _kinds(plan)]
        self.assertEqual(with_codes, [""])

    def test_a_code_sits_with_the_pages_asks(self):
        source, pool = self._watr()
        home, contact_home = _plan(""), _plan("", contact=True)
        inject_legible_images([home], source, pool)
        inject_legible_images([contact_home], source, pool)
        self.assertEqual(_kinds(home), ["hero", "qr", "cta"])
        self.assertEqual(_kinds(contact_home), ["hero", "contact", "qr", "cta"])

    def test_the_code_section_says_what_scanning_does(self):
        source, pool = self._watr()
        home = _plan("")
        inject_legible_images([home], source, pool)
        block = next(b for b in home.blocks if isinstance(b, QrBlock))
        self.assertEqual(block.heading, "Give with DuitNow")
        self.assertEqual(block.items[0].image_url, QR_URL)
        self.assertIn("SIBKL WATR-QR", block.items[0].description)

    def test_posters_follow_the_hero_under_their_own_heading(self):
        source, pool = self._watr()
        page = _plan("explore-life")
        inject_legible_images([page], source, pool)
        self.assertEqual(_kinds(page), ["hero", "poster", "cta"])
        block = page.blocks[1]
        self.assertIsInstance(block, PosterBlock)
        self.assertEqual(block.heading, "Explore Life")
        self.assertEqual([i.image_url for i in block.items], [POSTER_URL, SLIDE_URL])

    def test_a_code_inside_a_poster_is_the_posters_tap_through(self):
        """A phone cannot scan its own screen, so the slide's link is a button."""
        source, pool = self._watr()
        page = _plan("explore-life")
        inject_legible_images([page], source, pool)
        slide = page.blocks[1].items[1]
        self.assertEqual(slide.action_href, SLIDE_LINK)
        self.assertNotIn("qr", _kinds(page))

    def test_readings_are_looked_up_in_the_pool_by_url(self):
        """A page's own metadata copies are never stamped; only the pool is."""
        source, pool = self._watr()
        self.assertIsNone(source.image_metadata[0].qr_payload)
        home = _plan("")
        inject_legible_images([home], source, pool)
        self.assertIn("qr", _kinds(home))

    def test_only_legible_images_are_placed(self):
        source = _site({"": [_photo()]})
        home = _plan("")
        inject_legible_images([home], source, [_photo()])
        self.assertEqual(_kinds(home), ["hero", "cta"])

    def test_images_that_belong_elsewhere_are_not_placed(self):
        cases = {
            "a video's still": _poster("https://i.ytimg.com/vi/TapO6jbeq3c/hqdefault.jpg"),
            "a gallery cell": _poster(role="gallery"),
            "a brand mark": _poster(role="logo"),
            "a portrait": _poster(role="portrait"),
        }
        for label, image in cases.items():
            with self.subTest(label):
                home = _plan("")
                inject_legible_images([home], _site({"": [image]}), [image])
                self.assertEqual(_kinds(home), ["hero", "cta"])

    def test_a_transparent_code_the_graphic_screen_called_a_logo_is_still_placed(self):
        code = _code(role="logo")
        home = _plan("")
        inject_legible_images([home], _site({"": [code]}), [code])
        self.assertIn("qr", _kinds(home))

    def test_an_image_already_on_the_page_is_not_shown_twice(self):
        poster = _poster()
        home = _plan("")
        home.blocks.insert(1, GalleryBlock(
            heading="Gallery", items=[GalleryItem(image_query="x", image_url=POSTER_URL)]
        ))
        inject_legible_images([home], _site({"": [poster]}), [poster])
        self.assertEqual(_kinds(home), ["hero", "gallery", "cta"])

    def test_several_different_codes_share_a_neutral_heading(self):
        donate = _code()
        chat = _code("https://x/wa.png", payload="https://wa.me/60123456789")
        home = _plan("")
        inject_legible_images([home], _site({"": [donate, chat]}), [donate, chat])
        block = next(b for b in home.blocks if isinstance(b, QrBlock))
        self.assertEqual(block.heading, "Scan with your phone")
        self.assertEqual(len(block.items), 2)

    def test_the_model_is_never_asked_for_either(self):
        self.assertTrue({"qr", "poster"} <= DETERMINISTIC_SECTION_KINDS)


# --- 3. rendering -------------------------------------------------------------------


class RenderTest(unittest.TestCase):
    def test_images_are_framed_whole(self):
        blocks = {
            "qr": _qr_content(QrBlock(items=[{"image_url": QR_URL, "title": "Give with DuitNow",
                                               "description": "Scan to give."}])),
            "poster": _poster_content(PosterBlock(items=[{"image_url": POSTER_URL, "alt": "Alpha"}])),
        }
        for kind, content in blocks.items():
            with self.subTest(kind=kind):
                images = [el for el in _walk(_render(kind, content)) if el.type == "image"]
                self.assertEqual(len(images), 1)
                self.assertEqual(images[0].styles["objectFit"], "contain")
                self.assertIn("h-auto", images[0].classes.split())
                self.assertNotIn("height", images[0].styles)

    def test_a_lone_code_does_not_repeat_its_title_on_the_card(self):
        block = QrBlock(heading="Give with DuitNow",
                        items=[{"image_url": QR_URL, "title": "Give with DuitNow",
                                "description": "Scan to give."}])
        texts = [el.content.innerText for el in _walk(_render("qr", _qr_content(block)))
                 if el.type == "text"]
        self.assertEqual(texts.count("Give with DuitNow"), 1)

    def test_a_link_code_renders_its_button_and_a_payment_code_none(self):
        link = QrBlock(items=[{"image_url": QR_URL, "title": "Chat on WhatsApp",
                               "description": "Scan to chat.", "action_label": "Open WhatsApp",
                               "action_href": "https://wa.me/60123456789"}])
        pay = QrBlock(items=[{"image_url": QR_URL, "title": "Give with DuitNow",
                              "description": "Scan to give."}])
        links = [el for el in _walk(_render("qr", _qr_content(link))) if el.type == "link"]
        self.assertEqual([el.content.href for el in links], ["https://wa.me/60123456789"])
        self.assertFalse([el for el in _walk(_render("qr", _qr_content(pay))) if el.type == "link"])


# --- 4. the two other ways out ------------------------------------------------------


class ImageRefGateTest(unittest.TestCase):
    def test_a_poster_bound_to_a_card_is_dropped(self):
        """A bound card photo goes straight into its slot, past the resolver."""
        poster, photo = _poster(), _photo()
        source = _site({"": [poster, photo]})
        source = source.model_copy(update={"image_metadata": [poster, photo]})
        page = _plan("")
        page.blocks.insert(1, ServicesBlock(items=[
            ServiceItem(title="Alpha", description="d", image_ref=0),
            ServiceItem(title="Galleria", description="d", image_ref=1),
        ]))
        bound = bind_image_refs([page], {"": source})
        self.assertEqual(bound, {PHOTO_URL})
        self.assertIsNone(page.blocks[1].items[0].image_url)

    def test_referenced_images_lists_only_valid_refs(self):
        poster, photo = _poster(), _photo()
        source = _site({"": []}).model_copy(update={"image_metadata": [poster, photo]})
        page = _plan("")
        page.blocks.insert(1, ServicesBlock(items=[
            ServiceItem(title="a", description="d", image_ref=1),
            ServiceItem(title="b", description="d", image_ref=9),
        ]))
        self.assertEqual(referenced_images([page], {"": source}), [photo])


class SocialCardTest(unittest.TestCase):
    def test_a_code_is_never_the_pages_og_image(self):
        code_node = BuilderElement(id="c", name="QR Code", type="image", styles={},
                                   content=BuilderElementContent(src=QR_URL))
        section = BuilderElement(id="s", name="QR - Cards", type="container", styles={},
                                 content=[code_node])
        blocks = [QrBlock(items=[{"image_url": QR_URL, "title": "t", "description": "d"}])]
        self.assertEqual(extract_og_image([section]), QR_URL)  # the hazard
        self.assertIsNone(extract_og_image([section], exclude=scan_code_urls(blocks)))


class PlayerThumbnailTest(unittest.TestCase):
    def test_provider_stills_are_recognised_by_host(self):
        self.assertTrue(is_player_thumbnail("https://i.ytimg.com/vi/abc/hqdefault.jpg"))
        self.assertTrue(is_player_thumbnail("https://i.vimeocdn.com/video/1.jpg"))
        self.assertFalse(is_player_thumbnail(POSTER_URL))


if __name__ == "__main__":
    unittest.main()
