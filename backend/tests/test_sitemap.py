"""Tests for sitemap parsing — which ``<loc>`` elements are pages."""

from __future__ import annotations

import unittest

import httpx

from app.services import sitemap

_INDEX = b"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://shop.test/product-sitemap.xml</loc></sitemap>
</sitemapindex>"""

# Yoast's shape: every <url> carries its photos as <image:image><image:loc>.
_PRODUCTS = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
  <url>
    <loc>https://shop.test/product/relievo/</loc>
    <image:image><image:loc>https://shop.test/uploads/relievo-1.jpg</image:loc></image:image>
    <image:image><image:loc>https://shop.test/uploads/relievo-2.jpg</image:loc></image:image>
  </url>
  <url>
    <loc>https://shop.test/product/eterna/</loc>
    <image:image><image:loc>https://shop.test/uploads/eterna-1.jpg</image:loc></image:image>
  </url>
</urlset>"""


class SitemapEntryLocTest(unittest.IsolatedAsyncioTestCase):
    async def test_image_locs_are_not_pages(self):
        documents = {
            "https://shop.test/sitemap_index.xml": _INDEX,
            "https://shop.test/product-sitemap.xml": _PRODUCTS,
        }

        def respond(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=documents[str(request.url)])

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            urls, sources = await sitemap._read_sitemap_recursive(
                client, "https://shop.test/sitemap_index.xml", depth=0
            )

        self.assertEqual(
            urls,
            ["https://shop.test/product/relievo/", "https://shop.test/product/eterna/"],
        )
        self.assertEqual(
            sources,
            ["https://shop.test/sitemap_index.xml", "https://shop.test/product-sitemap.xml"],
        )


if __name__ == "__main__":
    unittest.main()
