"""Signed Facebook photo links die; a site is never built or pushed on dead ones.

Pins the three generation rules in `services/photo_url_expiry.py` — renew by
photo identity, leave out what is dead with a warning, name the deadline of
what is left — the URL helpers they stand on, the selective supply cut in
`source_images.without_images`, and the push preflight that refuses a dead link
instead of letting `_strip_invalid_images` delete it.
"""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.models.brand import BrandIdentity
from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.models.content_blocks import ImageMetadata, SectionCandidate, SourceCard, SourceContent
from app.services.cms_client import CmsClient
from app.services.image_urls import (
    is_expired_url,
    photo_identity,
    renew_signed_urls,
    signed_url_expiry,
    signed_urls_in,
)
from app.services.photo_url_expiry import keep_photo_links_alive, links_due
from app.services.push_orchestrator import PushRequest, push_site
from app.services.source_images import without_images

NOW = 1_791_650_000.0  # 2026-10-10, the measured read
DAY = 86_400


def fb(photo: str, *, expires: float, rendition: str = "dst-jpg_s960x960") -> str:
    """A Facebook CDN URL shaped like a real one: the filename is the picture,
    the signed query (with its hex deadline) one rendition of it."""
    return (
        f"https://scontent.fkul10-2.fna.fbcdn.net/v/t39.30808-6/{photo}_1234567890123_n.jpg"
        f"?stp={rendition}&_nc_cat=1&oh=00_abc&oe={int(expires):X}"
    )


def facebook_source(*urls: str, kind: str = "facebook") -> SourceContent:
    return SourceContent(
        source_kind=kind,  # type: ignore[arg-type]
        source_ref="https://www.facebook.com/thecorelearningcentre/",
        raw_text="The CORE Learning Centre",
        images=list(urls),
        image_metadata=[ImageMetadata(url=url, caption="Leaf bugs") for url in urls],
        section_candidates=[
            SectionCandidate(
                heading="Activities", level=2, card_kind="gallery",
                image_urls=list(urls),
                cards=[SourceCard(title="Leaf bugs", image_url=url) for url in urls],
            )
        ],
    )


class SignedUrlTest(unittest.TestCase):
    def test_the_deadline_is_read_from_the_signature(self):
        url = fb("483914123", expires=NOW + 5 * DAY)
        self.assertEqual(signed_url_expiry(url), NOW + 5 * DAY)
        self.assertFalse(is_expired_url(url, at=NOW))
        self.assertTrue(is_expired_url(url, at=NOW + 6 * DAY))

    def test_a_link_inside_the_grace_counts_as_dead(self):
        # A push downloads for minutes; a link dying mid-push is dead already.
        url = fb("483914123", expires=NOW + 60)
        self.assertTrue(is_expired_url(url, at=NOW))

    def test_urls_without_a_deadline_never_expire(self):
        for url in (
            "https://images.pexels.com/photos/1/pexels-photo-1.jpeg",
            "https://scontent.fbcdn.net/v/t39/483914123_1_n.jpg",  # unsigned
            "https://scontent.fbcdn.net/v/t39/483914123_1_n.jpg?oe=zzz",  # unreadable
            "https://example.com/photo.jpg?oe=6712ABCD",  # not Facebook's CDN
        ):
            self.assertIsNone(signed_url_expiry(url), url)
            self.assertFalse(is_expired_url(url, at=NOW + 365 * DAY))

    def test_links_are_found_inside_css_layers(self):
        url = fb("483914123", expires=NOW)
        css = f"linear-gradient(rgba(0,0,0,.4), rgba(0,0,0,.4)), url('{url}')"
        self.assertEqual(signed_urls_in(css), [url])
        self.assertEqual(signed_urls_in("no photos here"), [])

    def test_renewal_swaps_whole_urls_by_picture(self):
        old = fb("483914123", expires=NOW)
        new = fb("483914123", expires=NOW + 5 * DAY, rendition="dst-jpg_s2048x2048")
        other = fb("555555555", expires=NOW)
        css = f"url('{old}'), url(\"{other}\")"
        renewed = renew_signed_urls(css, {photo_identity(new): new})
        self.assertEqual(renewed, f"url('{new}'), url(\"{other}\")")

    def test_a_url_that_prefixes_another_is_never_corrupted(self):
        # The failure mode of a per-key str.replace (see CLAUDE.md, "A URL is
        # rewritten whole, or it is corrupted").
        short = fb("483914123", expires=NOW)
        longer = short + "&extra=1"
        fresh = fb("483914123", expires=NOW + 5 * DAY)
        self.assertEqual(renew_signed_urls(longer, {photo_identity(fresh): fresh}), fresh)


class WithoutImagesTest(unittest.TestCase):
    def test_only_the_named_photos_leave_every_field(self):
        keep, drop = fb("111111111", expires=NOW), fb("222222222", expires=NOW)
        page = facebook_source(keep, drop)
        source = facebook_source(keep, drop).model_copy(update={"discovered_pages": [page]})
        cut = without_images(source, {drop})
        for candidate in (cut, cut.discovered_pages[0]):
            self.assertEqual(candidate.images, [keep])
            self.assertEqual([m.url for m in candidate.image_metadata], [keep])
            section = candidate.section_candidates[0]
            self.assertEqual(section.image_urls, [keep])
            self.assertEqual([c.image_url for c in section.cards], [keep, None])
            # The card stays: its title is still the source's words.
            self.assertEqual(len(section.cards), 2)
        self.assertEqual(source.images, [keep, drop], "the original is never mutated")

    def test_nothing_to_cut_returns_the_source_itself(self):
        source = facebook_source(fb("111111111", expires=NOW))
        self.assertIs(without_images(source, set()), source)


def _run(source, brand=None, *, fresh=None, failing=None, now=NOW):
    calls = []

    async def fresh_links(src):
        calls.append(src.source_ref)
        if failing:
            raise failing
        return fresh or {}

    result = asyncio.run(keep_photo_links_alive(source, brand, now=now, fresh_links=fresh_links))
    return result, calls


class KeepPhotoLinksAliveTest(unittest.TestCase):
    def test_a_fresh_read_is_not_read_again(self):
        source = facebook_source(fb("111111111", expires=NOW + 5 * DAY))
        brand = BrandIdentity(name="CORE")
        result, calls = _run(source, brand)
        self.assertEqual(calls, [])
        self.assertIs(result.source, source)
        self.assertIs(result.brand, brand)
        self.assertEqual(result.warnings, [])

    def test_due_links_are_renewed_by_picture_everywhere(self):
        photo, mark = fb("111111111", expires=NOW + 3600), fb("999999999", expires=NOW + 3600)
        source = facebook_source(photo)
        brand = BrandIdentity(name="CORE", logo_url=mark)
        new_photo = fb("111111111", expires=NOW + 5 * DAY)
        new_mark = fb("999999999", expires=NOW + 5 * DAY)
        result, calls = _run(
            source, brand,
            fresh={photo_identity(new_photo): new_photo, photo_identity(new_mark): new_mark},
        )
        self.assertEqual(calls, [source.source_ref])
        self.assertEqual(result.source.images, [new_photo])
        self.assertEqual(result.source.image_metadata[0].url, new_photo)
        self.assertEqual(result.source.image_metadata[0].caption, "Leaf bugs")
        self.assertEqual(result.source.section_candidates[0].cards[0].image_url, new_photo)
        self.assertEqual(result.brand.logo_url, new_mark)
        self.assertEqual(result.brand.favicon_url, new_mark)
        self.assertEqual(result.warnings, [])
        self.assertEqual(links_due(result.source, result.brand, before=NOW + DAY), {})

    def test_a_photo_gone_from_the_page_is_left_out_and_counted(self):
        kept, gone = fb("111111111", expires=NOW - 60), fb("222222222", expires=NOW - 60)
        renewed = fb("111111111", expires=NOW + 5 * DAY)
        result, _ = _run(facebook_source(kept, gone), fresh={photo_identity(renewed): renewed})
        self.assertEqual(result.source.images, [renewed])
        self.assertEqual([m.url for m in result.source.image_metadata], [renewed])
        self.assertEqual(len(result.warnings), 1)
        self.assertIn("1 photo from Facebook has expired", result.warnings[0])

    def test_a_failed_read_withdraws_dead_links_and_says_why(self):
        dead = fb("111111111", expires=NOW - 60)
        mark = fb("999999999", expires=NOW - 60)
        brand = BrandIdentity(name="CORE", logo_url=mark, extracted_palette=["#123456"])
        result, _ = _run(facebook_source(dead), brand, failing=RuntimeError("login wall"))
        self.assertEqual(result.source.images, [])
        self.assertEqual(result.source.image_metadata, [])
        self.assertIsNone(result.brand.logo_url)
        self.assertIsNone(result.brand.favicon_url)
        self.assertFalse(result.brand.logo_render_ok, "the header falls back to the wordmark")
        self.assertEqual(result.brand.extracted_palette, ["#123456"], "the palette stays")
        self.assertEqual(len(result.warnings), 2)
        self.assertIn("login wall", result.warnings[0])
        self.assertIn("2 photos from Facebook have expired", result.warnings[1])

    def test_a_link_alive_but_due_is_kept_with_its_deadline(self):
        soon = fb("111111111", expires=NOW + 3 * 3600)
        result, _ = _run(facebook_source(soon), failing=RuntimeError("timeout"))
        self.assertEqual(result.source.images, [soon])
        self.assertIn("expire on 10 Oct 2026", result.warnings[-1])

    def test_another_source_kind_is_never_read_but_still_screened(self):
        # A website can embed Facebook CDN links too. There is no Page to read
        # again, but a dead link is still dead.
        dead, alive = fb("111111111", expires=NOW - 60), fb("222222222", expires=NOW + 5 * DAY)
        result, calls = _run(facebook_source(dead, alive, kind="url"))
        self.assertEqual(calls, [])
        self.assertEqual(result.source.images, [alive])
        self.assertEqual(len(result.warnings), 1)


def _site_with_photo(src: str) -> GeneratedSite:
    home = GeneratedPage(
        slug="", title="Home", is_homepage=True, seo=PageSeo(),
        body_schema=BodySchema(elements=[
            BuilderElement(name="Photo", type="image", content=BuilderElementContent(src=src, alt="")),
        ]),
    )
    return GeneratedSite(
        site_name="CORE", pages=[home], page_tree=[],
        header_schema=BuilderElement(name="Header", type="__header", content=[]),
        footer_schema=BuilderElement(name="Footer", type="__footer", content=[]),
    )


class PushRefusesDeadLinksTest(unittest.TestCase):
    def test_nothing_is_written_for_a_site_whose_photos_died(self):
        req = PushRequest(
            site=_site_with_photo(fb("111111111", expires=NOW)),
            cms_email="user@example.com", cms_password="secret",
            entity_token="entity-token", publish=True,
        )
        login = AsyncMock(return_value="jwt")
        with patch.object(CmsClient, "login", new=login):
            report = asyncio.run(push_site(req))
        login.assert_not_awaited()
        self.assertIn("expired on 10 Oct 2026", report.error)
        self.assertIn("Generate the site again", report.error)
        self.assertEqual([step.name for step in report.steps], ["photo_links"])


if __name__ == "__main__":
    unittest.main()
