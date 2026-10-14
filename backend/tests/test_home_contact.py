"""A ticked Contact page takes the contact form off the homepage.

A Facebook Page's homepage always carries a `contact` section, drawn before the
user picks pages, with a template Contact page offered beside it. Ticking that
page must not ship the same form twice, one click apart.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.models.content_blocks import SourceContent
from app.models.industry import PageScaffold
from app.routers.generate import GenerateWithPagesRequest, generate_with_pages
from app.services.industry_templates import get_template
from app.services.page_inference import defer_home_contact

_HOME_SECTIONS = ["hero", "about", "gallery", "contact", "cta"]


def _home(**overrides) -> PageScaffold:
    fields = dict(page_type="home", slug="", title="Home", is_homepage=True, sections=_HOME_SECTIONS)
    fields.update(overrides)
    return PageScaffold(**fields)


def _template_contact() -> PageScaffold:
    return next(p for p in get_template("restaurant").core_pages if p.page_type == "contact")


def _about() -> PageScaffold:
    return PageScaffold(page_type="about", slug="about", title="About", sections=["hero", "about", "cta"])


class DeferHomeContactTest(unittest.TestCase):
    def test_a_selected_contact_page_takes_the_form_off_the_homepage(self):
        about, contact = _about(), _template_contact()
        home, *rest = defer_home_contact([_home(), about, contact])
        self.assertEqual(home.sections, ["hero", "about", "gallery", "cta"])
        self.assertEqual(rest, [about, contact], "every other page, the form's new home included, is untouched")

    def test_the_selection_it_was_given_is_never_mutated(self):
        selected = [_home(), _template_contact()]
        defer_home_contact(selected)
        self.assertEqual(selected[0].sections, _HOME_SECTIONS)

    def test_without_a_contact_page_the_homepage_keeps_its_form(self):
        selected = [_home(), _about()]
        self.assertEqual(defer_home_contact(selected), selected)

    def test_a_contact_page_with_no_form_leaves_the_homepage_the_only_one(self):
        """A nested contact page gets the generic detail rhythm, which has no
        `contact` section — removing the homepage's would leave no form at all."""
        detail = PageScaffold(
            page_type="contact", slug="about/contact", title="Contact",
            parent_slug="about", sections=["hero", "about", "cta"],
        )
        home, _ = defer_home_contact([_home(), detail])
        self.assertIn("contact", home.sections)

    def test_a_translated_contact_page_serves_another_reader(self):
        translated = _template_contact().model_copy(
            update={"slug": "bm/contact", "locale": "bm", "translation_of": "contact"}
        )
        home, _ = defer_home_contact([_home(), translated])
        self.assertIn("contact", home.sections)


class GenerationAppliesItTest(unittest.TestCase):
    """The handler asks the whole planned set, so a two-page preview of Home +
    About still knows Contact is coming — and saves the plan it built from."""

    def _selected_after_narrowing(self, **request) -> list[PageScaffold]:
        payload = GenerateWithPagesRequest(
            source=SourceContent(source_kind="facebook", source_ref="https://www.facebook.com/acme", raw_text="Acme"),
            selected_pages=[_home(), _about(), _template_contact()],
            **request,
        )

        class Stop(Exception):
            pass

        # The first step after the page set is settled; nothing past it runs.
        with patch("app.routers.generate.keep_photo_links_alive", new=AsyncMock(side_effect=Stop)):
            with self.assertRaises(Stop):
                asyncio.run(generate_with_pages(payload))
        return payload.selected_pages

    def test_full_generation_builds_the_homepage_without_the_form(self):
        home = next(s for s in self._selected_after_narrowing() if s.is_homepage)
        self.assertNotIn("contact", home.sections)

    def test_a_preview_without_the_contact_page_still_defers_to_it(self):
        selected = self._selected_after_narrowing(
            generation_mode="two_page_preview", preview_second_slug="about"
        )
        self.assertEqual([s.slug for s in selected], ["", "about"])
        self.assertNotIn("contact", selected[0].sections)


if __name__ == "__main__":
    unittest.main()
