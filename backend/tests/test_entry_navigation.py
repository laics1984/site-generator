"""The entry page is re-read in the browser when its static HTML shows no menu.

A server-rendered page whose header menu is built by client-side JavaScript
reads, on the httpx fast path, as a page with content and no navigation.
``scraper._entry_with_navigation`` renders that one page so the menu the
browser builds is what page inference sees.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.models.content_blocks import NavLink
from app.services import scraper


def _parsed(*nav: str) -> SimpleNamespace:
    return SimpleNamespace(
        source_content=SimpleNamespace(nav_links=[NavLink(label=n, href=f"/{n}") for n in nav])
    )


class EntryNavigationFallbackTest(unittest.IsolatedAsyncioTestCase):
    def _patch(self, *, render, parsed):
        for patcher in (
            mock.patch.object(scraper, "_goto_and_render", render),
            mock.patch.object(scraper, "_parse_rendered_html", lambda *a, **k: parsed),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_the_rendered_menu_replaces_a_menuless_static_read(self):
        rendered = _parsed("about", "contact")
        self._patch(
            render=mock.AsyncMock(return_value=("https://site.test/", "<html></html>")),
            parsed=rendered,
        )
        static = _parsed()

        result = await scraper._entry_with_navigation(None, static, "https://site.test/")

        self.assertIs(result, rendered)

    async def test_a_render_that_finds_no_menu_either_keeps_the_static_read(self):
        self._patch(
            render=mock.AsyncMock(return_value=("https://site.test/", "<html></html>")),
            parsed=_parsed(),
        )
        static = _parsed()

        self.assertIs(await scraper._entry_with_navigation(None, static, "https://site.test/"), static)

    async def test_a_failed_render_keeps_the_static_read(self):
        self._patch(
            render=mock.AsyncMock(side_effect=scraper.ScrapeError("blocked (403)", status=403)),
            parsed=_parsed("never"),
        )
        static = _parsed()

        self.assertIs(await scraper._entry_with_navigation(None, static, "https://site.test/"), static)


if __name__ == "__main__":
    unittest.main()
