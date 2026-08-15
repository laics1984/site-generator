"""The `lightbox` marker that makes gallery tiles click-to-enlarge.

The marker is a schema contract shared with the renderers: it is authored on the
gallery grid in the section catalog, carried through the template filler, and
read by webtree-public (lib/lightbox.ts) plus its preview port. It must land on
the GRID — one flag defines both the trigger surface and the navigation order,
and the runtime binds a single delegated listener to that element.
"""

import asyncio
import unittest

from app.models.builder_schema import BuilderElement
from app.models.content_blocks import GalleryBlock, GalleryItem
from app.services.section_content import block_to_section
from app.services.template_filler import fill_template


async def _stub_image(query: str):
    return f"https://images.example/{query.replace(' ', '-')}.jpg", "#888888"


def _fill(template, content):
    return asyncio.run(fill_template(template, content, resolve_image=_stub_image))


def _gallery_block(count: int = 6) -> GalleryBlock:
    return GalleryBlock(
        heading="Our space",
        subheading="A look inside.",
        items=[
            GalleryItem(
                image_query=f"studio photo {i}",
                title=f"Photo {i}",
                image_url=f"https://cdn.example/p{i}.jpg",
            )
            for i in range(1, count + 1)
        ],
    )


def _find(element: BuilderElement, predicate) -> list[BuilderElement]:
    found: list[BuilderElement] = []
    if predicate(element):
        found.append(element)
    if isinstance(element.content, list):
        for child in element.content:
            found.extend(_find(child, predicate))
    return found


class GalleryLightboxMarkerTest(unittest.TestCase):
    def test_grid_carries_the_marker(self):
        template, content = block_to_section(_gallery_block())
        self.assertEqual(template["id"], "gallery-grid")
        element = _fill(template, content)

        marked = _find(element, lambda e: getattr(e, "lightbox", None) is True)
        self.assertEqual(len(marked), 1, "exactly one node may open a lightbox group")
        # The tile grid, not the section root and not the individual images —
        # the runtime treats the marked node's descendants as one navigable set.
        self.assertEqual(marked[0].name, "Gallery Grid")
        self.assertIn(marked[0].type, {"2Col", "3Col"})

    def test_marker_survives_serialization(self):
        template, content = block_to_section(_gallery_block())
        dumped = _fill(template, content).model_dump(exclude_none=True)

        def marked(node: dict) -> int:
            hits = 1 if node.get("lightbox") is True else 0
            content_ = node.get("content")
            if isinstance(content_, list):
                for child in content_:
                    hits += marked(child)
            return hits

        # exclude_none must not drop it, and it must reach the CMS as a real
        # boolean — the renderers require the marker to be literally `true`.
        self.assertEqual(marked(dumped), 1)

    def test_marker_survives_the_gridfit_column_switch(self):
        # $gridFit rewrites the grid's `type` by item count; base fields (and so
        # the marker) must ride through the rewrite.
        for count, expected_type in ((2, "2Col"), (4, "2Col"), (6, "3Col")):
            with self.subTest(count=count):
                template, content = block_to_section(_gallery_block(count))
                element = _fill(template, content)
                marked = _find(element, lambda e: getattr(e, "lightbox", None) is True)
                self.assertEqual(len(marked), 1)
                self.assertEqual(marked[0].type, expected_type)

    def test_no_other_section_kind_is_marked(self):
        # Scope decision: only galleries enlarge. A features/services card grid
        # carries photos too, and a click there must keep doing nothing.
        from app.models.content_blocks import FeatureItem, FeaturesBlock

        block = FeaturesBlock(
            heading="What we do",
            items=[
                FeatureItem(title=f"Feature {i}", description="Body copy.",
                            image_query=f"feature {i}")
                for i in range(1, 4)
            ],
        )
        template, content = block_to_section(block)
        element = _fill(template, content)
        self.assertEqual(_find(element, lambda e: getattr(e, "lightbox", None) is True), [])


class GalleryCapacityTest(unittest.TestCase):
    def test_accepts_a_full_scraped_gallery(self):
        # Tiles are click-to-enlarge, so a long set is browsable rather than
        # just a taller grid.
        block = _gallery_block(24)
        self.assertEqual(len(block.items), 24)

    def test_still_bounded(self):
        # Every unique image is one media upload at push time.
        from pydantic import ValidationError

        with self.assertRaises(ValidationError):
            _gallery_block(25)


class PerAlbumLightboxGroupTest(unittest.TestCase):
    """An album index ships several galleries — each browses on its own.

    routers.generate._inject_image_walls turns one source album into one
    GalleryBlock, so a page can carry several. Because each becomes its own
    catalog section instance, each gets its own marked grid — which is what
    makes the arrows walk one album rather than all of them at once.
    """

    def test_each_album_is_its_own_lightbox_group(self):
        albums = [
            GalleryBlock(
                heading=heading,
                items=[
                    GalleryItem(
                        image_query=heading,
                        caption=heading,
                        image_url=f"https://cdn.example/{slug}/{i}.jpg",
                    )
                    for i in range(1, 4)
                ],
            )
            for heading, slug in [("Sports Day", "sports"), ("Art & Craft", "art")]
        ]

        marked_per_album = []
        for block in albums:
            template, content = block_to_section(block)
            element = _fill(template, content)
            marked = _find(element, lambda e: getattr(e, "lightbox", None) is True)
            self.assertEqual(len(marked), 1)
            marked_per_album.append(marked[0])

        # Two groups, and no image belongs to both — otherwise "next" would
        # walk out of one album into another.
        def _srcs(node):
            return {
                e.content.src
                for e in _find(node, lambda e: getattr(e.content, "src", None))
            }

        first, second = (_srcs(node) for node in marked_per_album)
        self.assertTrue(first and second)
        self.assertEqual(first & second, set())


if __name__ == "__main__":
    unittest.main()
