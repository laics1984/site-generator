"""
Data-URI decoding at push time.

Every non-network image this repo generates is an inline SVG data URI: the
monogram avatar for a named team member with no photo, the on-brand gradient
placeholder that is `ImageResolver.resolve`'s last resort, and the section
icons. All three are PERCENT-ENCODED, not base64 — and `_decode_data_url`'s
pattern used to accept `;base64` as the only parameter, so it either failed to
match at all (`;utf8`) or matched and then fed URL-encoded text to `b64decode`.

Either way the src became a `_ResolveSkip`, joined `failed`, and
`_strip_invalid_images` deleted the element. The preview renders the pre-push
tree, so this was invisible there and showed up only as missing avatars and
icons on the published site.
"""

from __future__ import annotations

import asyncio
import base64
import unittest
from unittest.mock import AsyncMock

from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.services.icons import icon_data_url
from app.services.media import _placeholder_photo, monogram_avatar_url
from app.services.push_orchestrator import (
    PushRequest,
    _decode_data_url,
    _ResolveSkip,
    _resolve_to_bytes,
    _upload_media,
)


class DataUrlDecodeTest(unittest.TestCase):
    def test_monogram_avatar_decodes_to_svg(self) -> None:
        """`data:image/svg+xml;utf8,…` — the form that matched nothing at all."""
        src = monogram_avatar_url("Jane Smith")
        self.assertTrue(src.startswith("data:image/svg+xml;utf8,"))

        data, content_type, filename = _decode_data_url(src)

        self.assertEqual(content_type, "image/svg+xml")
        self.assertEqual(filename, "upload.svg")
        self.assertTrue(data.startswith(b"<svg"))
        # The initials survive the round trip — a real avatar, not bytes that
        # merely happen to decode.
        self.assertIn(b">JS<", data)

    def test_gradient_placeholder_decodes_to_svg(self) -> None:
        """The resolver's last resort uses the same `;utf8` form."""
        photo = _placeholder_photo("clinic reception", "landscape", "Reception")
        self.assertTrue(photo.url.startswith("data:image/svg+xml;utf8,"))

        data, content_type, filename = _decode_data_url(photo.url)

        self.assertEqual(content_type, "image/svg+xml")
        self.assertEqual(filename, "upload.svg")
        self.assertTrue(data.startswith(b"<svg"))

    def test_icon_decodes_to_svg(self) -> None:
        """`data:image/svg+xml,…` — matched the old pattern, then died in b64decode."""
        src = icon_data_url("check", "#0f172a")
        assert src is not None
        self.assertTrue(src.startswith("data:image/svg+xml,"))

        data, content_type, filename = _decode_data_url(src)

        self.assertEqual(content_type, "image/svg+xml")
        self.assertEqual(filename, "upload.svg")
        self.assertTrue(data.startswith(b"<svg"))
        self.assertIn(b"<path", data)

    def test_base64_svg_still_decodes(self) -> None:
        """logo_extraction / style_tokens emit `;base64` — that branch must not move."""
        svg = b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>"
        src = "data:image/svg+xml;base64," + base64.b64encode(svg).decode()

        data, content_type, filename = _decode_data_url(src)

        self.assertEqual(data, svg)
        self.assertEqual(content_type, "image/svg+xml")
        self.assertEqual(filename, "upload.svg")

    def test_base64_png_still_decodes(self) -> None:
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        src = "data:image/png;base64," + base64.b64encode(raw).decode()

        data, content_type, filename = _decode_data_url(src)

        self.assertEqual(data, raw)
        self.assertEqual(content_type, "image/png")
        self.assertEqual(filename, "upload.png")

    def test_charset_parameter_before_base64_is_not_mistaken_for_text(self) -> None:
        """`;base64` as the last of several parameters is still base64."""
        svg = b"<svg xmlns='http://www.w3.org/2000/svg'/>"
        src = (
            "data:image/svg+xml;charset=utf-8;base64,"
            + base64.b64encode(svg).decode()
        )

        data, _ct, _fn = _decode_data_url(src)

        self.assertEqual(data, svg)

    def test_plus_in_percent_encoded_payload_is_literal(self) -> None:
        """unquote, not unquote_plus: a `+` in SVG markup is a plus, not a space."""
        src = "data:image/svg+xml;utf8,%3Csvg%3EA%2BB%3C/svg%3E"

        data, _ct, _fn = _decode_data_url(src)

        self.assertEqual(data, b"<svg>A+B</svg>")

    def test_malformed_data_url_still_skips(self) -> None:
        with self.assertRaises(_ResolveSkip):
            _decode_data_url("data:image/png")  # no comma at all


def _site_with_image(src: str) -> GeneratedSite:
    image = BuilderElement(
        id="img-1",
        name="Member photo",
        type="image",
        styles={},
        content=BuilderElementContent(src=src, alt="Jane Smith"),
    )
    home = GeneratedPage(
        slug="",
        title="Home",
        is_homepage=True,
        body_schema=BodySchema(elements=[image]),
        seo=PageSeo(),
    )
    return GeneratedSite(site_name="Test Site", pages=[home], page_tree=[])


class PushFetchIsGuardedTest(unittest.TestCase):
    """Push time is a fetch boundary too (SECURITY.md §2).

    Every src reaching `_resolve_to_bytes` came from outside — a scraped page's
    markup, or markup the user pasted straight in, which makes it directly
    attacker-chosen rather than requiring a site they control to be scraped
    first. A refusal must degrade to `_ResolveSkip` so `_strip_invalid_images`
    drops that one element, exactly as it would for an unreachable host, rather
    than failing the whole push.
    """

    def test_private_host_is_skipped_not_fetched(self) -> None:
        for src in (
            "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
            "http://127.0.0.1:8001/health",
            "http://host.docker.internal/api/pages",
        ):
            with self.subTest(src=src):
                with self.assertRaises(_ResolveSkip):
                    asyncio.run(_resolve_to_bytes(src))

    def test_data_urls_bypass_the_guard(self) -> None:
        # A data URI is bytes we already hold — there is no host to resolve.
        data, content_type, _ = asyncio.run(
            _resolve_to_bytes(monogram_avatar_url("Jane Smith"))
        )
        self.assertEqual(content_type, "image/svg+xml")
        self.assertTrue(data)


class MonogramSurvivesUploadTest(unittest.TestCase):
    def test_monogram_is_rehosted_not_stripped(self) -> None:
        """End to end: the src is rewritten to a CMS URL and never reaches `failed`.

        `_strip_invalid_images` deletes whatever comes back in `failed`, so an
        empty failed set is the assertion that the avatar survives to the
        builder as an editable image element.
        """
        src = monogram_avatar_url("Jane Smith")
        req = PushRequest(
            site=_site_with_image(src),
            cms_email="user@example.com",
            cms_password="secret",
            entity_token="entity-token",
        )
        client = AsyncMock()
        client.upload_media.return_value = "https://cms.example.com/storage/mono.svg"

        rewrites, failed = asyncio.run(_upload_media(client, req))

        self.assertEqual(failed, set())
        self.assertEqual(rewrites.get(src), "https://cms.example.com/storage/mono.svg")

        # And it was uploaded as a real SVG, not as garbage bytes.
        _args, kwargs = client.upload_media.call_args
        self.assertTrue(kwargs["file_bytes"].startswith(b"<svg"))
        self.assertEqual(kwargs["content_type"], "image/svg+xml")


if __name__ == "__main__":
    unittest.main()
