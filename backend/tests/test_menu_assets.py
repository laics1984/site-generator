"""Menu evidence across sources, placement, stable refs and existing rendering."""
import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from app.config import settings
from app.models.content_blocks import GalleryBlock, GalleryItem, ImageMetadata, PagePlan, PosterBlock, SourceContent
from app.models.industry import PageScaffold
from app.services.doc_parser import OutlineBlock, ParsedDocument
from app.services.doc_structure import DocImageRef, split_into_pages
from app.services.facebook_source import to_image_metadata
from app.models.facebook import FacebookPage, FacebookPost
from app.services.image_match import hides_legible_content
from app.services.image_refs import bind_image_refs
from app.services.legible_images import inject_legible_images
from app.services.menu_assets import assess_menu_assets, classify_menu_image, inject_menu_assets, menu_review_warnings
from app.services.planner import DetectedBrand, _build_scaffolded_user_prompt, plan_site
from app.services.prompts import RESTAURANT_MENU_CONTENT_PROMPT
from app.services.section_content import _poster_content, select_template
from app.services.source_router import promptable_images
from app.services.template_filler import fill_template
from app.services.text_detection import PixelReading, _candidates, _recognized_text, prefetch_text_flags


def source(images, kind="url", path=None):
    return SourceContent(source_kind=kind, source_ref="https://cafe.test", raw_text="Cafe", url_path=path, image_metadata=images)


def page(slug="", blocks=None):
    return PagePlan(slug=slug, title=slug or "Home", page_type="menu" if slug else "home", is_homepage=not slug, blocks=blocks or [])


def scaffold(slug="menu"):
    return PageScaffold(slug=slug, page_type="menu", title="Menu", sections=["menu", "gallery"])


@pytest.mark.parametrize("kind", ["url", "pdf", "docx", "facebook"])
def test_sources_route_uncropped_menu_to_selected_page(kind):
    meta = ImageMetadata(url="https://cdn.test/menu.jpg", alt="Drinks Menu", role="gallery")
    src, pages = source([meta], kind), [page(), page("menu")]
    assets = assess_menu_assets(src)
    inject_menu_assets(pages, src, assets, [scaffold()])
    assert not pages[0].blocks
    assert isinstance(pages[1].blocks[0], PosterBlock)
    assert pages[1].blocks[0].heading == "Drinks Menu"
    assert hides_legible_content(meta, "inline") and hides_legible_content(meta, "background")


def test_homepage_fallback_is_idempotent_and_not_generic_poster():
    meta = ImageMetadata(url="https://cdn.test/menu.jpg", alt="Menu", role="gallery")
    src, home = source([meta]), page()
    assets = assess_menu_assets(src)
    inject_menu_assets([home], src, assets, [])
    inject_legible_images([home], src, [meta])
    inject_menu_assets([home], src, assets, [])
    assert len(home.blocks) == 1
    assert home.blocks[0].heading == "Menu"


def test_category_ownership_and_sheet_caps():
    metas = [ImageMetadata(url=f"https://cdn.test/{i}.jpg", alt="Lunch Menu") for i in range(14)]
    src = source(metas, path="/menu/lunch/")
    pages = [page(), page("menu"), page("menu/lunch")]
    inject_menu_assets(pages, src, assess_menu_assets(src), [scaffold(), scaffold("menu/lunch")])
    assert not pages[1].blocks
    assert [len(b.items) for b in pages[2].blocks] == [6, 6, 2]


def test_selected_menu_in_later_batch_does_not_duplicate_on_homepage():
    meta = ImageMetadata(url="https://cdn.test/menu.jpg", alt="Menu")
    src, home = source([meta]), page()
    inject_menu_assets([home], src, assess_menu_assets(src), [scaffold()])
    assert not home.blocks


def test_single_priced_food_image_is_menu_content():
    meta = ImageMetadata(url="https://cdn.test/food.jpg", ocr_text="Chicken Rice RM 15", ocr_has_text=True)
    classify_menu_image(meta)
    assert meta.content_purpose == "menu"


@pytest.mark.parametrize("kw", [
    {"alt": "Grilled salmon", "ocr_has_text": False},
    {"caption": "Our dish from the menu", "ocr_has_text": False},
    {"ocr_text": "Ticket RM 50"}, {"ocr_text": "Total RM 50"},
    {"alt": "Menu", "intent": "logo"},
])
def test_food_photos_plain_prices_and_brand_marks_stay_out(kw):
    meta = ImageMetadata(url="https://cdn.test/image.jpg", **kw)
    classify_menu_image(meta)
    assert meta.content_purpose is None


def test_uncaptioned_priced_dishes_identify_image_without_transcribing_prices():
    meta = ImageMetadata(url="https://cdn.test/hash.jpg", ocr_text="Nasi Lemak RM 12\nChicken Rice RM 15", ocr_has_text=True)
    src, home = source([meta]), page()
    inject_menu_assets([home], src, assess_menu_assets(src), [])
    assert home.blocks[0].items[0].image_url == meta.url
    assert "RM" not in home.model_dump_json()


@pytest.mark.parametrize("caption", ["Menu promotion valid until Friday", "Old menu", "New menu version 2"])
def test_promotions_and_versions_are_withheld_and_reported(caption):
    meta = ImageMetadata(url="https://cdn.test/menu.jpg", caption=caption)
    src, home = source([meta]), page()
    assets = assess_menu_assets(src)
    inject_menu_assets([home], src, assets, [])
    inject_legible_images([home], src, [meta])
    assert not home.blocks and menu_review_warnings(assets)
    assert hides_legible_content(meta, "inline")


def test_duplicate_bytes_reserved_across_gallery_and_resolver_aliases():
    first = ImageMetadata(url="https://cdn.test/menu.jpg", alt="Menu", content_hash="same")
    alias = ImageMetadata(url="https://cdn.test/alias.jpg", content_hash="same")
    src = source([first, alias])
    home = page(blocks=[GalleryBlock(items=[GalleryItem(image_query="menu", image_url=alias.url)])])
    inject_menu_assets([home], src, assess_menu_assets(src), [])
    assert len(home.blocks) == 1 and len(home.blocks[0].items) == 1
    assert hides_legible_content(alias, "inline")


def test_late_ocr_does_not_shift_numbered_image_refs():
    menu, food = ImageMetadata(url="https://cdn.test/one.jpg"), ImageMetadata(url="https://cdn.test/two.jpg")
    src = source([menu, food])
    before = [m.url for m in promptable_images(src)]
    menu.ocr_text = "Menu\nLatte RM 12\nCoffee RM 10"
    assess_menu_assets(src)
    assert [m.url for m in promptable_images(src)] == before
    home = page(blocks=[GalleryBlock(items=[GalleryItem(image_query="coffee", image_ref=1)])])
    bind_image_refs([home], {"": src})
    assert home.blocks[0].items[0].image_url == food.url


def test_late_text_free_reading_clears_caption_only_reservation():
    meta = ImageMetadata(url="https://cdn.test/food.jpg", caption="From our menu")
    src = source([meta])
    assert assess_menu_assets(src)
    meta.ocr_has_text = False
    assert not assess_menu_assets(src)
    assert meta.content_purpose is None
    assert not hides_legible_content(meta, "inline")


def test_actual_document_extraction_and_facebook_caption_paths():
    parsed = ParsedDocument(source_kind="docx", source_ref="menu.docx", title="Cafe", raw_text="Cafe\nDrinks Menu", headings=[],
                            outline=[OutlineBlock(1, "Cafe"), OutlineBlock(1, "Drinks Menu")])
    src = split_into_pages(parsed, images=[DocImageRef(url="data:menu", width=900, height=1200, anchor=2)])
    assert assess_menu_assets(src)[0].content_purpose == "menu"
    fb = FacebookPage(name="Cafe", canonical_url="https://facebook.com/cafe", category="Restaurant",
                      posts=[FacebookPost(message="Drinks Menu", photos=[{"url": "https://cdn.test/menu.jpg"}])])
    metadata = to_image_metadata(fb)
    assert metadata[0].caption == "Drinks Menu"
    assert assess_menu_assets(source(metadata, "facebook"))[0].content_purpose == "menu"


def test_prompt_is_restaurant_only_and_legacy_reuses_constant():
    for industry in ("restaurant", "childcare", "other"):
        payload = json.loads(_build_scaffolded_user_prompt(source([]), DetectedBrand(site_name="Cafe", industry_category=industry), [scaffold()]))
        assert (payload.get("industry_menu_direction") == RESTAURANT_MENU_CONTENT_PROMPT) == (industry == "restaurant")
    client = AsyncMock()
    asyncio.run(plan_site(source([]), llm=client))
    assert client.chat_json.call_args.kwargs["system_prompt"].count(RESTAURANT_MENU_CONTENT_PROMPT) == 1


def test_gallery_ocr_is_bounded_and_preserves_evidence():
    metas = [ImageMetadata(url=f"https://cdn.test/{i}.jpg", role="gallery") for i in range(4)]
    assert not _candidates(metas, 2)
    assert len(_candidates(metas, 2, include_gallery=True)) == 2
    async def screen(urls, prefetched):
        return {url: PixelReading(True, None, text="Menu\nCoffee RM 10") for url in urls}
    with patch.object(settings, "ocr_text_detection_enabled", True), patch("app.services.text_detection._screen", side_effect=screen):
        asyncio.run(prefetch_text_flags(metas, max_images=2, include_gallery=True))
    assert metas[0].ocr_text.startswith("Menu") and not metas[2].ocr_text


def test_ocr_groups_separate_dish_price_boxes_into_rows():
    boxes = [([[0,0],[80,0],[80,20],[0,20]], "Coffee", .99),
             ([[200,0],[250,0],[250,20],[200,20]], "RM 10", .99),
             ([[0,30],[80,30],[80,50],[0,50]], "Latte", .99),
             ([[200,30],[250,30],[250,50],[200,50]], "RM 12", .99)]
    assert _recognized_text(boxes) == "Coffee RM 10\nLatte RM 12"


async def test_existing_template_renders_whole_with_lightbox_and_no_stock():
    block = PosterBlock(heading="Menu", items=[{"image_url": "https://cdn.test/menu.jpg", "alt": "Menu"}])
    content = _poster_content(block)
    resolve = AsyncMock(side_effect=AssertionError("Menu must not query stock"))
    rendered = await fill_template(select_template("poster", content), content, resolve_image=resolve)
    def walk(node):
        yield node
        for child in node.content if isinstance(node.content, list) else []:
            yield from walk(child)
    nodes = list(walk(rendered))
    assert any(n.lightbox for n in nodes)
    image = next(n for n in nodes if n.type == "image")
    assert image.styles["objectFit"] == "contain" and "h-auto" in image.classes
    assert image.content.src == block.items[0].image_url
    resolve.assert_not_called()
