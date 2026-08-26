"""
The site favicon: what a browser tab and a Google search result show beside the
site's name.

Three things are pinned here, because each was a real hole:

- The declared `<link rel="icon">` is read on EVERY site, not only ones with no
  logo. `extract_logo` consults the icon tier second, so on a site that has a
  real header logo the icon links were parsed and thrown away.
- Every source ends up with an icon. A PDF, a DOCX and a Facebook Page have no
  markup to declare one, so the brand mark is the fallback.
- An `.ico` survives the push. It is the format `rel="icon"` most often points
  at and the one the CMS media store cannot take, so it has to be transcoded.
"""

from __future__ import annotations

import asyncio
import base64
import io
import unittest
from unittest.mock import AsyncMock, patch

from bs4 import BeautifulSoup
from PIL import Image

from app.models.builder_schema import BodySchema, GeneratedPage, GeneratedSite, PageSeo
from app.models.brand import BrandIdentity
from app.services import brand_candidate
from app.services.cms_client import CmsApiError, CmsClient
from app.services.logo_extraction import LogoCandidate, find_favicon
from app.services.push_orchestrator import (
    PushRequest,
    _coerce_to_cms_image,
    _push_favicon,
)
from app.services.push_orchestrator import PushReport

BASE = "https://example.com/"


def _soup(head_html: str, body_html: str = "") -> BeautifulSoup:
    return BeautifulSoup(
        f"<html><head>{head_html}</head><body>{body_html}</body></html>", "lxml"
    )


class FindFaviconTest(unittest.TestCase):
    def test_it_reads_the_declared_icon(self):
        soup = _soup('<link rel="icon" href="/favicon.ico">')

        self.assertEqual(find_favicon(soup, BASE), "https://example.com/favicon.ico")

    def test_it_prefers_the_largest_declared_size(self):
        soup = _soup(
            '<link rel="icon" sizes="16x16" href="/small.png">'
            '<link rel="icon" sizes="192x192" href="/large.png">'
        )

        self.assertEqual(find_favicon(soup, BASE), "https://example.com/large.png")

    def test_it_skips_a_mask_icon(self):
        """A mask-icon is a monochrome silhouette — never a usable favicon."""
        soup = _soup('<link rel="mask-icon" href="/mask.svg" color="#000">')

        self.assertIsNone(find_favicon(soup, BASE))

    def test_it_is_read_even_when_the_page_has_a_real_logo(self):
        """The regression: `extract_logo` only reaches its icon tier when there
        is no real mark, so on a site with a header logo — most sites — the
        icon links were parsed and discarded."""
        soup = _soup(
            '<link rel="icon" href="/favicon.ico">',
            '<header><a href="/"><img src="/assets/logo.svg" alt="Acme"></a></header>',
        )

        self.assertEqual(find_favicon(soup, BASE), "https://example.com/favicon.ico")

    def test_no_icon_is_none(self):
        self.assertIsNone(find_favicon(_soup(""), BASE))


class FaviconDefaultsToTheMarkTest(unittest.TestCase):
    """Every source ends up with an icon. A PDF, a DOCX and a Facebook Page have
    no markup to declare one, and a manually uploaded logo has none either, so
    the mark is the fallback — stated once, on the model."""

    def test_it_defaults_to_the_logo_url(self):
        brand = BrandIdentity(name="Acme", logo_url="https://example.com/logo.svg")

        self.assertEqual(brand.favicon_url, "https://example.com/logo.svg")

    def test_it_defaults_to_an_inline_mark(self):
        """A manually uploaded logo arrives as a data URL and nothing else —
        see routers/brand.py::_brand_payload."""
        brand = BrandIdentity(name="Acme", logo_data_url="data:image/png;base64,aGk=")

        self.assertEqual(brand.favicon_url, "data:image/png;base64,aGk=")

    def test_a_declared_icon_wins(self):
        brand = BrandIdentity(
            name="Acme",
            logo_url="https://example.com/logo.svg",
            favicon_url="https://example.com/favicon.ico",
        )

        self.assertEqual(brand.favicon_url, "https://example.com/favicon.ico")

    def test_an_unrenderable_mark_is_not_used(self):
        """logo_render_ok=False means an og:image — a 1200x630 social card with
        baked-in text, which in a 16px tab is an illegible smear. Reusing that
        verdict rather than inventing a second one."""
        brand = BrandIdentity(
            name="Acme", logo_url="https://example.com/og.png", logo_render_ok=False
        )

        self.assertIsNone(brand.favicon_url)

    def test_no_mark_is_none(self):
        self.assertIsNone(BrandIdentity(name="Acme").favicon_url)


class DeclaredIconSurvivesADegradedReadTest(unittest.TestCase):
    """A logo that cannot be fetched says nothing about whether the page
    declared an icon, so the icon must survive the name-only degraded path."""

    def test_it_survives_a_logo_that_cannot_be_fetched(self):
        logo = LogoCandidate(source="logo", url="https://example.com/gone.svg")

        with patch.object(brand_candidate, "is_public_url", AsyncMock(return_value=False)):
            brand = asyncio.run(
                brand_candidate.build_brand_candidate(
                    "Acme", logo, favicon_url="https://example.com/favicon.ico"
                )
            )

        self.assertIsNone(brand.logo_url)
        self.assertEqual(brand.favicon_url, "https://example.com/favicon.ico")

    def test_an_unfetchable_mark_does_not_become_the_icon(self):
        """Nothing is known about a mark we could not even read — falling back
        to it would ship a favicon we have never seen."""
        logo = LogoCandidate(source="logo", url="https://example.com/gone.svg")

        with patch.object(brand_candidate, "is_public_url", AsyncMock(return_value=False)):
            brand = asyncio.run(brand_candidate.build_brand_candidate("Acme", logo))

        self.assertIsNone(brand.favicon_url)


class IcoSurvivesTheCoercionTest(unittest.TestCase):
    """`.ico` is what `rel="icon"` usually points at, and the one format the CMS
    media store will not take."""

    def _ico_bytes(self) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGBA", (32, 32), (10, 20, 30, 255)).save(buffer, format="ICO")
        return buffer.getvalue()

    def test_an_ico_is_transcoded_to_png(self):
        coerced = _coerce_to_cms_image(self._ico_bytes(), "image/x-icon", "favicon.ico")

        self.assertIsNotNone(coerced)
        _, mime, filename = coerced
        self.assertEqual(mime, "image/png")
        self.assertTrue(filename.endswith(".png"))


def _site(favicon_url: str | None) -> GeneratedSite:
    site = GeneratedSite(
        site_name="Test Site",
        pages=[
            GeneratedPage(
                slug="",
                title="Home",
                is_homepage=True,
                body_schema=BodySchema(elements=[]),
                seo=PageSeo(),
            )
        ],
        page_tree=[],
        builder_styles={},
    )
    site.brand = {"favicon_url": favicon_url} if favicon_url else None
    return site


class PushFaviconStepTest(unittest.TestCase):
    """The step is never fatal: the pages are already in by the time it runs,
    and an icon is something the owner can set in Site settings."""

    def _run(self, site, *, push_favicon=True, set_favicon=None):
        req = PushRequest(
            site=site,
            cms_email="user@example.com",
            cms_password="secret",
            entity_token="entity-token",
            push_favicon=push_favicon,
        )
        report = PushReport()
        client = CmsClient(base_url="https://cms.example.test")
        with patch.object(
            CmsClient,
            "set_entity_favicon",
            new=set_favicon or AsyncMock(return_value="https://cms/storage/favicons/a.png"),
        ):
            asyncio.run(_push_favicon(client, req, report))
        return next(step for step in report.steps if step.name == "favicon")

    def test_a_data_url_icon_is_uploaded(self):
        png = io.BytesIO()
        Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(png, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()

        step = self._run(_site(data_url))

        self.assertTrue(step.ok)
        self.assertEqual(step.data.get("favicon_url"), "https://cms/storage/favicons/a.png")

    def test_it_is_skipped_when_the_source_declared_none(self):
        step = self._run(_site(None))

        self.assertTrue(step.ok)
        self.assertIn("declared none", step.detail)

    def test_it_is_skipped_on_request(self):
        step = self._run(_site("data:image/png;base64,aGk="), push_favicon=False)

        self.assertTrue(step.ok)
        self.assertIn("Skipped", step.detail)

    def test_a_cms_failure_is_recorded_but_not_raised(self):
        png = io.BytesIO()
        Image.new("RGBA", (64, 64), (1, 2, 3, 255)).save(png, format="PNG")
        data_url = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode()

        step = self._run(
            _site(data_url),
            set_favicon=AsyncMock(side_effect=CmsApiError(500, "boom")),
        )

        self.assertFalse(step.ok)
        self.assertIn("boom", step.error)

    def test_undecodable_bytes_are_skipped_rather_than_failing(self):
        """A format the CMS cannot store is decoded here first, so garbage is
        caught before it becomes a request. Garbage labelled as a format the
        CMS *can* store passes through — validating it twice is the CMS's job,
        and a rejection there lands as a failed step either way."""
        step = self._run(_site("data:image/x-icon;base64,bm90LWFuLWltYWdl"))

        self.assertTrue(step.ok)
        self.assertIn("unreadable", step.detail)


if __name__ == "__main__":
    unittest.main()
