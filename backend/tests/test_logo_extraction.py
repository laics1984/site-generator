import base64
import io
import json
import unittest
from unittest.mock import patch

from bs4 import BeautifulSoup
from PIL import Image

from app.services import scraper
from app.services.logo_extraction import LogoCandidate, extract_logo, is_renderable

BASE = "https://example.com/"


def _soup(body_html: str, head_html: str = "") -> BeautifulSoup:
    return BeautifulSoup(
        f"<html><head>{head_html}</head><body>{body_html}</body></html>", "lxml"
    )


def _evidence(**kwargs) -> str:
    data = {"vw": 1366, "vh": 900, "x": 0, "y": 0, "w": 200, "h": 60}
    data.update(kwargs)
    return json.dumps(data)


# Long enough to clear the interface-glyph floor.
_LOGO_SVG_PATH = "M10 10h180v40H10z" + "l2 2" * 40


class RealMarkBeatsIconTest(unittest.TestCase):
    """The regression this module exists for: the actual logo used to lose to
    an apple-touch-icon, a PWA icon, and an og:image, in that order."""

    def test_header_logo_beats_apple_touch_icon(self):
        soup = _soup(
            '<header><a href="/"><img src="/assets/logo.svg" alt="Acme"></a></header>',
            '<link rel="apple-touch-icon" href="/at.png">',
        )

        logo = extract_logo(soup, BASE)

        self.assertEqual(logo.source, "logo")
        self.assertEqual(logo.url, "https://example.com/assets/logo.svg")

    def test_header_logo_beats_large_pwa_icon(self):
        soup = _soup(
            '<header><img class="logo" src="/assets/logo.png"></header>',
            '<link rel="icon" sizes="512x512" href="/icon-512.png">',
        )

        self.assertEqual(extract_logo(soup, BASE).url, "https://example.com/assets/logo.png")

    def test_header_logo_beats_og_image(self):
        soup = _soup(
            '<header><img class="logo" src="/assets/logo.png"></header>',
            '<meta property="og:image" content="/social-card.jpg">',
        )

        logo = extract_logo(soup, BASE)

        self.assertEqual(logo.source, "logo")
        self.assertEqual(logo.url, "https://example.com/assets/logo.png")


class RealMarkDetectionTest(unittest.TestCase):
    def test_sole_header_image_is_the_logo_even_unnamed(self):
        soup = _soup('<header><img src="/assets/a1b2c3.png" alt=""></header>')

        logo = extract_logo(soup, BASE)

        self.assertEqual(logo.source, "logo")
        self.assertEqual(logo.url, "https://example.com/assets/a1b2c3.png")

    def test_alt_matching_site_name_identifies_the_mark(self):
        soup = _soup(
            "<header>"
            '<img src="/hamburger.png" alt="Menu">'
            '<img src="/x9.png" alt="Acme Dental">'
            "</header>"
        )

        logo = extract_logo(soup, BASE, site_name="Acme Dental")

        self.assertEqual(logo.url, "https://example.com/x9.png")

    def test_srcset_only_logo_resolves(self):
        soup = _soup(
            "<header><img class=\"logo\" "
            'srcset="/logo-400.png 400w, /logo-800.png 800w"></header>'
        )

        self.assertEqual(extract_logo(soup, BASE).url, "https://example.com/logo-800.png")

    def test_logo_named_image_outside_header_still_counts(self):
        soup = _soup('<div class="wrap"><img src="/assets/site-logo.png"></div>')

        logo = extract_logo(soup, BASE)

        self.assertEqual(logo.source, "logo")
        self.assertEqual(logo.url, "https://example.com/assets/site-logo.png")

    def test_rendered_evidence_marks_a_top_of_page_image_as_header(self):
        soup = _soup(
            f'<div><img src="/brandmark.png" data-webtree-evidence=\'{_evidence(y=24)}\'></div>'
        )

        self.assertEqual(extract_logo(soup, BASE).source, "logo")


class LogoWallTest(unittest.TestCase):
    def test_partner_logo_wall_is_not_the_brand(self):
        tiles = "".join(
            f'<img src="/logos/partner{i}.png" '
            f"data-webtree-evidence='{_evidence(y=1400, grid=6)}'>"
            for i in range(6)
        )
        soup = _soup(
            f'<section>{tiles}</section>', '<link rel="icon" sizes="192x192" href="/i.png">'
        )

        logo = extract_logo(soup, BASE)

        self.assertEqual(logo.source, "icon")

    def test_grid_membership_alone_disqualifies_a_logo_named_tile(self):
        soup = _soup(
            '<section><img src="/assets/acme-logo.png" '
            f"data-webtree-evidence='{_evidence(y=1400, grid=4)}'></section>"
        )

        self.assertIsNone(extract_logo(soup, BASE))

    def test_repeated_marks_sharing_a_class_are_a_wall_without_evidence(self):
        """The fast-fetch path has no grid stamp and these URLs aren't under
        /logos/ — bbc.co.uk lists 'BBC Scotland logo', 'BBC ALBA logo' etc. as a
        strip of sibling images in one class."""
        tiles = "".join(
            f'<img class="Image" src="/ace/standard/{i}.png" alt="BBC {i} logo">'
            for i in range(4)
        )
        soup = _soup(
            f"<section>{tiles}</section>",
            '<link rel="icon" sizes="512x512" href="/touch-512.png">',
        )

        self.assertEqual(extract_logo(soup, BASE).source, "icon")

    def test_two_logo_named_images_are_not_a_wall(self):
        soup = _soup(
            '<div><img class="Image" src="/site-logo.png">'
            '<img class="Image" src="/logo-alt.png"></div>'
        )

        self.assertEqual(extract_logo(soup, BASE).url, "https://example.com/site-logo.png")

    def test_prose_alt_mentioning_a_logo_is_not_the_brand(self):
        """A news photo alt'd '…a handbag with the Prada logo on it…' used to
        win tier 1c on a bare substring match."""
        soup = _soup(
            '<article><img src="/ace/standard/c655.jpg" alt="A woman with long '
            "dark blond hair holding a black handbag with the Prada logo on it "
            'which is fake. She is standing in a living room."></article>',
            '<link rel="icon" sizes="512x512" href="/touch-512.png">',
        )

        self.assertEqual(extract_logo(soup, BASE).source, "icon")

    def test_short_label_alt_still_identifies_a_mark(self):
        soup = _soup('<div><img src="/a1b2.png" alt="Acme logo"></div>')

        self.assertEqual(extract_logo(soup, BASE).source, "logo")


class InlineSvgTest(unittest.TestCase):
    def test_inline_svg_in_home_link_becomes_a_data_url(self):
        soup = _soup(
            '<header><a href="/"><svg viewBox="0 0 200 60">'
            f'<path d="{_LOGO_SVG_PATH}" fill="#ff6600"/>'
            "</svg></a></header>"
        )

        logo = extract_logo(soup, BASE)

        self.assertEqual(logo.source, "logo")
        self.assertIsNone(logo.url)
        self.assertTrue(logo.data_url.startswith("data:image/svg+xml;base64,"))
        decoded = base64.b64decode(logo.data_url.split(",", 1)[1]).decode()
        self.assertIn("http://www.w3.org/2000/svg", decoded)  # namespace re-added
        self.assertIn("#ff6600", decoded)

    def test_hamburger_svg_is_not_a_logo(self):
        soup = _soup(
            '<header><button class="menu-toggle"><svg viewBox="0 0 24 24">'
            '<path d="M3 6h18M3 12h18M3 18h18"/>'
            "</svg></button></header>",
            '<link rel="icon" sizes="192x192" href="/i.png">',
        )

        self.assertEqual(extract_logo(soup, BASE).source, "icon")

    def test_class_named_svg_logo_is_picked_without_a_home_link(self):
        soup = _soup(
            '<header><span><svg class="site-logo" viewBox="0 0 200 60">'
            f'<path d="{_LOGO_SVG_PATH}"/>'
            "</svg></span></header>"
        )

        self.assertEqual(extract_logo(soup, BASE).source, "logo")


class IconFallbackTest(unittest.TestCase):
    def test_largest_declared_icon_wins_not_the_first(self):
        soup = _soup(
            "",
            '<link rel="apple-touch-icon" sizes="57x57" href="/at57.png">'
            '<link rel="apple-touch-icon" sizes="180x180" href="/at180.png">',
        )

        logo = extract_logo(soup, BASE)

        self.assertEqual(logo.source, "icon")
        self.assertEqual(logo.url, "https://example.com/at180.png")

    def test_multi_size_attribute_uses_its_largest_value(self):
        soup = _soup(
            "",
            '<link rel="icon" sizes="16x16 32x32 48x48" href="/multi.ico">'
            '<link rel="icon" sizes="24x24" href="/small.png">',
        )

        self.assertEqual(extract_logo(soup, BASE).url, "https://example.com/multi.ico")

    def test_undeclared_apple_touch_icon_outranks_a_bare_favicon(self):
        soup = _soup(
            "",
            '<link rel="icon" href="/favicon.ico">'
            '<link rel="apple-touch-icon" href="/at.png">',
        )

        self.assertEqual(extract_logo(soup, BASE).url, "https://example.com/at.png")

    def test_mask_icon_is_never_used(self):
        soup = _soup("", '<link rel="mask-icon" href="/mask.svg" color="#000">')

        self.assertIsNone(extract_logo(soup, BASE))

    def test_bare_favicon_is_still_a_last_resort(self):
        soup = _soup("", '<link rel="shortcut icon" href="/favicon.ico">')

        logo = extract_logo(soup, BASE)

        self.assertEqual(logo.source, "icon")
        self.assertEqual(logo.url, "https://example.com/favicon.ico")


class OgImageTest(unittest.TestCase):
    def test_og_image_is_the_last_tier(self):
        soup = _soup("", '<meta property="og:image" content="/card.jpg">')

        logo = extract_logo(soup, BASE)

        self.assertEqual(logo.source, "og-image")
        self.assertEqual(logo.url, "https://example.com/card.jpg")

    def test_nothing_brandish_yields_none(self):
        soup = _soup("<main><p>Hello</p><img src='/photo.jpg' alt='A room'></main>")

        self.assertIsNone(extract_logo(soup, BASE))


class RenderGateTest(unittest.TestCase):
    def test_real_logo_always_renders(self):
        self.assertTrue(is_renderable("logo", size=(24, 24)))

    def test_og_image_never_renders(self):
        self.assertFalse(is_renderable("og-image", size=(1200, 630)))

    def test_small_icon_is_rejected(self):
        self.assertFalse(is_renderable("icon", size=(32, 32)))

    def test_large_icon_is_accepted(self):
        self.assertTrue(is_renderable("icon", size=(192, 192)))

    def test_vector_icon_skips_the_size_test(self):
        self.assertTrue(is_renderable("icon", size=None, is_vector=True))

    def test_banner_shaped_icon_is_rejected(self):
        self.assertFalse(is_renderable("icon", size=(1200, 100)))

    def test_unknown_source_is_rejected(self):
        self.assertFalse(is_renderable(None, size=(400, 120)))


def _png(size: tuple[int, int], color=(255, 102, 0, 255)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class _FakeClient:
    def __init__(self, content: bytes):
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, _url):
        return _FakeResponse(self._content)


class BrandCandidateGateTest(unittest.IsolatedAsyncioTestCase):
    """The gate runs on DECODED pixels, so a `sizes="192x192"` claim on a 32px
    file cannot smuggle a favicon into the header."""

    async def _build(self, candidate, image_bytes):
        with (
            patch.object(scraper, "is_public_url", return_value=True),
            patch.object(
                scraper.httpx, "AsyncClient", lambda **kw: _FakeClient(image_bytes)
            ),
        ):
            return await scraper._build_brand_candidate("Acme", candidate)

    async def test_small_icon_seeds_the_palette_but_is_not_renderable(self):
        brand = await self._build(
            LogoCandidate(source="icon", url="https://example.com/favicon.ico"),
            _png((32, 32)),
        )

        self.assertEqual(brand.logo_source, "icon")
        self.assertFalse(brand.logo_render_ok)
        self.assertEqual(brand.extracted_palette, ["#ff6600"])

    async def test_real_logo_is_renderable(self):
        brand = await self._build(
            LogoCandidate(source="logo", url="https://example.com/logo.png"),
            _png((400, 120)),
        )

        self.assertEqual(brand.logo_source, "logo")
        self.assertTrue(brand.logo_render_ok)

    async def test_og_image_is_palette_only_however_large(self):
        brand = await self._build(
            LogoCandidate(source="og-image", url="https://example.com/card.jpg"),
            _png((1200, 630)),
        )

        self.assertFalse(brand.logo_render_ok)
        self.assertEqual(brand.extracted_palette, ["#ff6600"])

    async def test_inline_svg_needs_no_fetch_and_renders(self):
        svg = (
            b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 200 60">'
            b'<path d="M0 0h10v10H0z" fill="#ff6600"/></svg>'
        )
        candidate = LogoCandidate(
            source="logo",
            data_url="data:image/svg+xml;base64," + base64.b64encode(svg).decode(),
        )

        with patch.object(
            scraper, "is_public_url", side_effect=AssertionError("should not fetch")
        ):
            brand = await scraper._build_brand_candidate("Acme", candidate)

        self.assertTrue(brand.logo_render_ok)
        self.assertIsNone(brand.logo_url)
        self.assertEqual(brand.extracted_palette, ["#ff6600"])


class BrandCandidateResilienceTest(unittest.IsolatedAsyncioTestCase):
    """A dead logo used to discard the scraped site name along with it."""

    async def test_no_logo_still_keeps_the_site_name(self):
        brand = await scraper._build_brand_candidate("Acme", None)

        self.assertEqual(brand.name, "Acme")
        self.assertIsNone(brand.logo_url)
        self.assertIsNone(brand.logo_source)

    async def test_unfetchable_logo_still_keeps_the_site_name(self):
        with patch.object(scraper, "is_public_url", return_value=False):
            brand = await scraper._build_brand_candidate(
                "Acme", LogoCandidate(source="logo", url="http://127.0.0.1/logo.png")
            )

        self.assertEqual(brand.name, "Acme")
        self.assertIsNone(brand.logo_url)

    async def test_nothing_at_all_is_still_none(self):
        self.assertIsNone(await scraper._build_brand_candidate(None, None))


if __name__ == "__main__":
    unittest.main()
