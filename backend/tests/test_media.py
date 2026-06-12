import unittest

from app.services.media import ImageResolver
from app.services.pexels import PhotoResult


def _photo(url, alt):
    return PhotoResult(
        url=url, alt=alt, photographer="Tester", photographer_url=None, source="pexels"
    )


class FakePexels:
    """Maps query -> PhotoResult | list[PhotoResult], mirroring search_many."""

    configured = True

    def __init__(self, results):
        self.results = results
        self.queries = []

    async def search_many(self, query, *, orientation="landscape", size="large"):
        self.queries.append((query, orientation))
        found = self.results.get(query)
        if found is None:
            return []
        return list(found) if isinstance(found, list) else [found]

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

    async def test_reranks_batch_by_alt_relevance_instead_of_first_hit(self):
        pexels = FakePexels(
            {
                "dental clinic reception": [
                    _photo("https://pexels.example/toothbrush.jpg", "toothbrush macro shot"),
                    _photo(
                        "https://pexels.example/reception.jpg",
                        "dental clinic reception with patients",
                    ),
                ]
            }
        )
        resolver = ImageResolver(pexels=pexels)

        photo = await resolver.resolve("dental clinic reception", intent="hero")

        self.assertEqual(photo.url, "https://pexels.example/reception.jpg")

    async def test_already_used_stock_photo_is_skipped_not_repeated(self):
        pexels = FakePexels(
            {
                "dental clinic reception": [
                    _photo(
                        "https://pexels.example/reception.jpg",
                        "dental clinic reception with patients",
                    ),
                    _photo("https://pexels.example/lobby.jpg", "clinic lobby interior"),
                ]
            }
        )
        resolver = ImageResolver(pexels=pexels)

        first = await resolver.resolve("dental clinic reception", intent="hero")
        second = await resolver.resolve("dental clinic reception", intent="about")

        self.assertEqual(first.url, "https://pexels.example/reception.jpg")
        self.assertEqual(second.url, "https://pexels.example/lobby.jpg")

    async def test_industry_category_supplies_contextual_fallback_query(self):
        pexels = FakePexels(
            {
                "restaurant interior food service": _photo(
                    "https://pexels.example/restaurant.jpg", "restaurant interior"
                )
            }
        )
        resolver = ImageResolver(pexels=pexels, industry_category="restaurant")

        # "our signature experience" hits no slot token bucket — the industry
        # category must supply the contextual query before the plain fallback.
        photo = await resolver.resolve("our signature experience", intent="hero")

        self.assertEqual(photo.url, "https://pexels.example/restaurant.jpg")
        self.assertIn(
            "restaurant interior food service",
            [query for query, _orientation in pexels.queries],
        )

    async def test_place_cue_localizes_atmospheric_cta_backgrounds(self):
        pexels = FakePexels(
            {
                "city skyline at dusk Malaysia": _photo(
                    "https://pexels.example/kl-skyline.jpg", "kuala lumpur skyline"
                )
            }
        )
        resolver = ImageResolver(pexels=pexels, place_cue="Malaysia")

        photo = await resolver.resolve("city skyline at dusk", intent="cta_bg")

        self.assertEqual(photo.url, "https://pexels.example/kl-skyline.jpg")
        self.assertEqual(
            pexels.queries[0], ("city skyline at dusk Malaysia", "landscape")
        )
