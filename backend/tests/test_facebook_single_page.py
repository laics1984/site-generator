"""The single-page scaffold path.

A source carrying one page's worth of grounded facts must not be handed the
industry template's four or five pages — the fidelity net would strip them back
to almost nothing and ship a site of empty rooms.
"""

import unittest

from app.models.content_blocks import SourceContent
from app.services.page_inference import infer_page_scaffolds


def _source(**overrides) -> SourceContent:
    base = dict(
        source_kind="facebook",
        source_ref="https://www.facebook.com/acmecoffee",
        title="Acme Coffee Roasters",
        raw_text="Acme Coffee Roasters — Coffee Shop\nAbout: Specialty coffee roasted in-house.",
    )
    base.update(overrides)
    return SourceContent(**base)


class SinglePageTest(unittest.TestCase):
    def test_returns_the_homepage_plus_legal_pages_only(self):
        scaffolds = infer_page_scaffolds(
            _source(), industry="restaurant", site_name="Acme", single_page=True
        )
        self.assertTrue(scaffolds[0].is_homepage)
        self.assertTrue(all(s.is_legal for s in scaffolds[1:]))

    def test_without_the_flag_the_template_still_fans_out(self):
        """The default path is untouched — a thin website should still get the
        industry template's core + suggested pages."""
        scaffolds = infer_page_scaffolds(
            _source(source_kind="url"), industry="restaurant", site_name="Acme"
        )
        self.assertGreater(len([s for s in scaffolds if not s.is_legal]), 1)

    def test_an_explicit_section_list_beats_the_industry_pattern(self):
        sections = ["hero", "about", "gallery", "contact", "cta"]
        scaffolds = infer_page_scaffolds(
            _source(),
            industry="restaurant",
            site_name="Acme",
            single_page=True,
            homepage_sections_override=sections,
        )
        self.assertEqual(scaffolds[0].sections, sections)

    def test_no_override_falls_back_to_the_industry_pattern(self):
        scaffolds = infer_page_scaffolds(
            _source(), industry="restaurant", site_name="Acme", single_page=True
        )
        home = scaffolds[0]
        self.assertEqual(home.sections[0], "hero")
        self.assertEqual(home.sections[-1], "cta")

    def test_the_homepage_slug_stays_the_site_root(self):
        scaffolds = infer_page_scaffolds(
            _source(), industry="other", site_name="Acme", single_page=True
        )
        self.assertEqual(scaffolds[0].slug, "")


if __name__ == "__main__":
    unittest.main()


def _photo(index: int, **kwargs):
    from app.models.content_blocks import ImageMetadata

    fields = dict(url=f"https://cdn.example/{index}.jpg", role="content", width=1600, height=1200)
    fields.update(kwargs)
    return ImageMetadata(**fields)


class EvidencePagesTest(unittest.TestCase):
    """Material the source has, beyond one homepage's text, earns a page — and
    each gate asks the predicate its filler uses, so an earned page is never
    empty."""

    def _slugs(self, source, industry="other"):
        scaffolds = infer_page_scaffolds(source, industry=industry, site_name="Acme", single_page=True)
        return [s.slug for s in scaffolds if not s.is_homepage and not s.is_legal]

    def test_a_full_photo_set_earns_a_gallery_page(self):
        from app.services.page_inference import GALLERY_PAGE_MIN_PHOTOS

        full = [_photo(i) for i in range(GALLERY_PAGE_MIN_PHOTOS)]
        self.assertEqual(self._slugs(_source(image_metadata=full)), ["gallery"])
        self.assertEqual(self._slugs(_source(image_metadata=full[:-1])), [])

    def test_words_in_pixels_and_marks_never_count_toward_one(self):
        from app.services.page_inference import GALLERY_PAGE_MIN_PHOTOS

        photos = [_photo(i) for i in range(GALLERY_PAGE_MIN_PHOTOS - 1)]
        photos += [_photo(98, ocr_has_text=True), _photo(99, role="logo")]
        self.assertEqual(self._slugs(_source(image_metadata=photos)), [])

    def test_a_restaurants_menu_sheet_earns_a_menu_page_of_sheets_alone(self):
        sheet = _photo(1, caption="Our menu", ocr_text="Menu\nChicken chop RM18", ocr_has_text=True)
        scaffolds = infer_page_scaffolds(
            _source(image_metadata=[sheet]), industry="restaurant", site_name="Acme", single_page=True
        )
        menu = next(s for s in scaffolds if s.slug == "menu")
        self.assertEqual(menu.sections, ["hero", "cta"])

    def test_no_menu_page_where_menus_are_not_replayed(self):
        sheet = _photo(1, caption="Our menu", ocr_text="Menu\nChicken chop RM18", ocr_has_text=True)
        self.assertEqual(self._slugs(_source(image_metadata=[sheet]), industry="agency"), [])


class RecipeOffersTemplatePagesTest(unittest.TestCase):
    """A template About or Contact page would only restate a single-page
    source's one homepage of text, so the picker offers them unticked."""

    def _recipe(self, single_page: bool) -> dict:
        from fastapi.testclient import TestClient

        from app.main import app

        response = TestClient(app).post("/api/pages/recipe", json={
            "source": _source().model_dump(mode="json"),
            "industry_override": "restaurant",
            "single_page": single_page,
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_single_page_core_pages_are_offered_not_preticked(self):
        recipe = self._recipe(single_page=True)
        self.assertTrue(all(page["is_legal"] for page in recipe["template"]["core_pages"]))
        offered = {page["slug"] for page in recipe["template"]["optional_pages"]}
        self.assertTrue({"about", "contact"} <= offered)

    def test_without_the_flag_about_is_still_a_page_of_the_site(self):
        recipe = self._recipe(single_page=False)
        self.assertIn("about", {page["slug"] for page in recipe["inferred_pages"]})
