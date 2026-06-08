import unittest

from app.services.media import ImageResolver
from app.services.pexels import PhotoResult


class FakePexels:
    configured = True

    def __init__(self, results):
        self.results = results
        self.queries = []

    async def search(self, query, *, orientation="landscape", size="large", per_page=5):
        self.queries.append((query, orientation))
        return self.results.get(query)

class ImageResolverStockFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_people_stock_fallback_tries_market_then_asia(self):
        pexels = FakePexels(
            {
                "Asian professional team meeting": PhotoResult(
                    url="https://pexels.example/asian-team.jpg",
                    alt="Asian professional team meeting",
                    photographer="Tester",
                    photographer_url=None,
                    source="pexels",
                )
            }
        )
        resolver = ImageResolver(pexels=pexels, market_cue="Southeast Asian")

        photo = await resolver.resolve(
            "professional team meeting",
            intent="hero",
            alt_fallback="Team",
        )

        self.assertEqual(photo.url, "https://pexels.example/asian-team.jpg")
        self.assertEqual(
            [query for query, _orientation in pexels.queries],
            [
                "Southeast Asian professional team meeting",
                "Asian professional team meeting",
            ],
        )

    async def test_people_stock_fallback_uses_contextual_query_before_plain_people_query(self):
        pexels = FakePexels(
            {
                "modern professional workspace": PhotoResult(
                    url="https://pexels.example/workspace.jpg",
                    alt="Modern professional workspace",
                    photographer="Tester",
                    photographer_url=None,
                    source="pexels",
                )
            }
        )
        resolver = ImageResolver(pexels=pexels, market_cue="Southeast Asian")

        photo = await resolver.resolve(
            "professional team meeting",
            intent="hero",
            alt_fallback="Team",
        )

        self.assertEqual(photo.url, "https://pexels.example/workspace.jpg")
        self.assertEqual(
            [query for query, _orientation in pexels.queries],
            [
                "Southeast Asian professional team meeting",
                "Asian professional team meeting",
                "modern professional workspace",
            ],
        )

    async def test_pexels_results_do_not_add_media_attributions(self):
        pexels = FakePexels(
            {
                "professional team meeting": PhotoResult(
                    url="https://pexels.example/team.jpg",
                    alt="Professional team meeting",
                    photographer="Tester",
                    photographer_url=None,
                    source="pexels",
                )
            }
        )
        resolver = ImageResolver(pexels=pexels)

        await resolver.resolve(
            "professional team meeting",
            intent="cta_bg",
            alt_fallback="Team",
        )

        self.assertEqual(resolver.attributions, [])
