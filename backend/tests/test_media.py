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


class CachingCountingPexels:
    """Stub mirroring PexelsClient's per-query caching, counting only the calls
    that would hit the network (a cache miss). Accepts the `client` kwarg that
    prewarm_stock passes for connection pooling."""

    configured = True

    def __init__(self, results):
        self.results = results
        self.network_calls = []
        self._cache = {}

    async def search_many(self, query, *, orientation="landscape", size="large", client=None):
        key = (query, orientation)
        if key in self._cache:
            return list(self._cache[key])
        self.network_calls.append(key)
        found = self.results.get(query)
        res = (list(found) if isinstance(found, list) else [found]) if found else []
        self._cache[key] = res
        return list(res)


class PrewarmStockTest(unittest.IsolatedAsyncioTestCase):
    async def test_prewarm_then_resolve_hits_cache_with_no_new_network(self):
        pexels = CachingCountingPexels(
            {
                "Southeast Asian dental clinic team": [
                    _photo("https://pexels.example/clinic.jpg", "clinic team")
                ]
            }
        )
        resolver = ImageResolver(pexels=pexels, market_cue="Southeast Asian")

        await resolver.prewarm_stock([("dental clinic team", "hero")])
        calls_after_prewarm = len(pexels.network_calls)
        self.assertGreater(calls_after_prewarm, 0)  # the chain was fetched

        photo = await resolver.resolve(
            "dental clinic team", intent="hero", alt_fallback="Team"
        )

        self.assertEqual(photo.url, "https://pexels.example/clinic.jpg")
        # The render-time resolve found everything warm — zero new network calls.
        self.assertEqual(len(pexels.network_calls), calls_after_prewarm)

    async def test_prewarm_is_noop_when_pexels_unconfigured(self):
        class Unconfigured(CachingCountingPexels):
            configured = False

        pexels = Unconfigured({})
        resolver = ImageResolver(pexels=pexels)

        await resolver.prewarm_stock([("anything", "hero")])

        self.assertEqual(pexels.network_calls, [])


class StrongestSourceBackgroundTest(unittest.TestCase):
    def _resolver(self, metadata):
        from app.models.content_blocks import ImageMetadata

        class Unconfigured:
            configured = False

        return ImageResolver(
            scraped_metadata=[ImageMetadata(**m) for m in metadata],
            pexels=Unconfigured(),
            use_llm_tiebreaker=False,
        )

    def test_returns_largest_qualifying_css_background(self):
        resolver = self._resolver(
            [
                {"url": "https://x/small-bg.jpg", "source_usage": "css_background",
                 "role": "background", "width": 1000, "height": 600},
                {"url": "https://x/big-bg.jpg", "source_usage": "css_background",
                 "role": "hero", "width": 2400, "height": 1400},
                {"url": "https://x/inline.jpg", "source_usage": "inline",
                 "role": "hero", "width": 3000, "height": 2000},
            ]
        )
        best = resolver.strongest_source_background()
        self.assertIsNotNone(best)
        self.assertEqual(best.url, "https://x/big-bg.jpg")

    def test_tiny_texture_is_rejected_by_min_dim(self):
        resolver = self._resolver(
            [
                {"url": "https://x/tile.png", "source_usage": "css_background",
                 "role": "background", "width": 200, "height": 200},
            ]
        )
        self.assertIsNone(resolver.strongest_source_background())

    def test_unknown_dims_accepted_only_with_hero_grade_signals(self):
        resolver = self._resolver(
            [
                {"url": "https://x/unknown-generic.jpg",
                 "source_usage": "css_background", "role": "unknown",
                 "intent": "generic"},
                {"url": "https://x/unknown-hero.jpg",
                 "source_usage": "css_background", "role": "unknown",
                 "intent": "hero"},
            ]
        )
        best = resolver.strongest_source_background()
        self.assertIsNotNone(best)
        self.assertEqual(best.url, "https://x/unknown-hero.jpg")

    def test_none_when_no_css_backgrounds(self):
        resolver = self._resolver(
            [
                {"url": "https://x/inline.jpg", "source_usage": "inline",
                 "role": "hero", "width": 2400, "height": 1400},
            ]
        )
        self.assertIsNone(resolver.strongest_source_background())


class HeroBackgroundMinSizeTest(unittest.IsolatedAsyncioTestCase):
    """A too-small scraped image must not fill a full-bleed hero (it upscales to
    a blur); the resolver defers to a crisp Pexels photo instead."""

    def _resolver(self, metadata, pexels):
        from app.models.content_blocks import ImageMetadata

        return ImageResolver(
            scraped_metadata=[ImageMetadata(**m) for m in metadata],
            pexels=pexels,
            use_llm_tiebreaker=False,
        )

    async def test_small_scraped_hero_background_defers_to_stock(self):
        pexels = FakePexels(
            {"cafe interior": [_photo("https://pexels.example/cafe.jpg", "cafe interior")]}
        )
        resolver = self._resolver(
            [{"url": "https://x/tiny-hero.jpg", "source_usage": "css_background",
              "role": "hero", "intent": "hero", "width": 600, "height": 400}],
            pexels,
        )

        photo = await resolver.resolve(
            "cafe interior", intent="hero", slot_usage="background"
        )

        self.assertEqual(photo.source, "pexels")
        self.assertEqual(photo.url, "https://pexels.example/cafe.jpg")

    async def test_large_scraped_hero_background_is_used(self):
        pexels = FakePexels(
            {"cafe interior": [_photo("https://pexels.example/cafe.jpg", "cafe interior")]}
        )
        resolver = self._resolver(
            [{"url": "https://x/big-hero.jpg", "source_usage": "css_background",
              "role": "hero", "intent": "hero", "width": 2000, "height": 1200}],
            pexels,
        )

        photo = await resolver.resolve(
            "cafe interior", intent="hero", slot_usage="background"
        )

        self.assertEqual(photo.source, "scraped")
        self.assertEqual(photo.url, "https://x/big-hero.jpg")

    async def test_unknown_dimension_hero_background_still_used(self):
        # CSS-background URLs frequently omit dimensions — we reject on measured
        # evidence, not missing data, so an unsized source background is kept.
        pexels = FakePexels(
            {"cafe interior": [_photo("https://pexels.example/cafe.jpg", "cafe interior")]}
        )
        resolver = self._resolver(
            [{"url": "https://x/unsized-hero.jpg", "source_usage": "css_background",
              "role": "hero", "intent": "hero"}],
            pexels,
        )

        photo = await resolver.resolve(
            "cafe interior", intent="hero", slot_usage="background"
        )

        self.assertEqual(photo.source, "scraped")
        self.assertEqual(photo.url, "https://x/unsized-hero.jpg")

    async def test_pinned_small_hero_background_defers_to_stock(self):
        pexels = FakePexels(
            {"cafe interior": [_photo("https://pexels.example/cafe.jpg", "cafe interior")]}
        )
        resolver = self._resolver(
            [{"url": "https://x/tiny-pinned.jpg", "intent": "hero",
              "width": 500, "height": 300}],
            pexels,
        )

        photo = await resolver.resolve(
            "cafe interior", intent="hero", slot_usage="background",
            pinned_url="https://x/tiny-pinned.jpg",
        )

        self.assertEqual(photo.source, "pexels")

    async def test_small_image_not_gated_for_inline_hero_slot(self):
        # The gate is full-bleed-background only: a bounded inline column doesn't
        # upscale the same way, so a modest source photo is fine there.
        pexels = FakePexels({})
        resolver = self._resolver(
            [{"url": "https://x/side.jpg", "source_usage": "inline",
              "role": "content", "intent": "hero", "width": 700, "height": 500,
              "alt": "cafe interior"}],
            pexels,
        )

        photo = await resolver.resolve(
            "cafe interior", intent="hero", slot_usage="inline"
        )

        self.assertEqual(photo.source, "scraped")
        self.assertEqual(photo.url, "https://x/side.jpg")

    async def test_small_scraped_section_background_defers_to_stock(self):
        # Non-hero backgrounds are also background-size: cover, so a too-small
        # source image upscales to a blur — defer to a crisp Pexels photo.
        pexels = FakePexels(
            {"cafe interior": [_photo("https://pexels.example/cafe.jpg", "cafe interior")]}
        )
        resolver = self._resolver(
            [{"url": "https://x/tiny-section.jpg", "source_usage": "css_background",
              "role": "background", "intent": "generic", "width": 600, "height": 400,
              "alt": "cafe interior"}],
            pexels,
        )

        photo = await resolver.resolve(
            "cafe interior", intent="generic", slot_usage="background"
        )

        self.assertEqual(photo.source, "pexels")
        self.assertEqual(photo.url, "https://pexels.example/cafe.jpg")

    async def test_section_background_min_is_lower_than_hero_min(self):
        # An image large enough for a section band (>= section_min_background_dim)
        # but below the hero minimum is kept for a non-hero background.
        pexels = FakePexels(
            {"cafe interior": [_photo("https://pexels.example/cafe.jpg", "cafe interior")]}
        )
        resolver = self._resolver(
            [{"url": "https://x/mid-section.jpg", "source_usage": "css_background",
              "role": "background", "intent": "generic", "width": 1000, "height": 700,
              "alt": "cafe interior"}],
            pexels,
        )

        photo = await resolver.resolve(
            "cafe interior", intent="generic", slot_usage="background"
        )

        self.assertEqual(photo.source, "scraped")
        self.assertEqual(photo.url, "https://x/mid-section.jpg")


class PortraitHeroBackgroundTest(unittest.IsolatedAsyncioTestCase):
    """A directory page's grid headshots (role=portrait) must never fill a
    hero background, whatever their resolution — the classic failure is a face
    blown up huge behind the hero text of a "find a therapist" page."""

    def _resolver(self, metadata, pexels):
        from app.models.content_blocks import ImageMetadata

        return ImageResolver(
            scraped_metadata=[ImageMetadata(**m) for m in metadata],
            pexels=pexels,
            use_llm_tiebreaker=False,
        )

    def _stock(self):
        return FakePexels(
            {"music therapy": [_photo("https://pexels.example/stock.jpg", "music therapy")]}
        )

    async def test_all_portrait_pool_defers_hero_background_to_stock(self):
        # High-res headshots that clear the old size gate — the role veto must
        # still keep every one of them out of the full-bleed hero.
        pexels = self._stock()
        resolver = self._resolver(
            [
                {"url": f"https://x/face-{i}.jpg", "role": "portrait",
                 "intent": "hero" if i == 0 else "generic",
                 "alt": "music therapy therapist", "width": 1600, "height": 1600}
                for i in range(3)
            ],
            pexels,
        )

        photo = await resolver.resolve(
            "music therapy", intent="hero", slot_usage="background"
        )

        self.assertEqual(photo.source, "pexels")

    async def test_unknown_dims_portrait_still_rejected_for_background(self):
        # Unknown dimensions normally get the benefit of the doubt — the
        # measured portrait role is evidence enough to reject regardless.
        pexels = self._stock()
        resolver = self._resolver(
            [{"url": "https://x/face.jpg", "role": "portrait", "intent": "hero",
              "alt": "music therapy therapist"}],
            pexels,
        )

        photo = await resolver.resolve(
            "music therapy", intent="hero", slot_usage="background"
        )

        self.assertEqual(photo.source, "pexels")

    async def test_pinned_portrait_not_honored_for_hero_background(self):
        pexels = self._stock()
        resolver = self._resolver(
            [{"url": "https://x/face.jpg", "role": "portrait", "intent": "hero",
              "width": 1600, "height": 1600}],
            pexels,
        )

        photo = await resolver.resolve(
            "music therapy", intent="hero", slot_usage="background",
            pinned_url="https://x/face.jpg",
        )

        self.assertEqual(photo.source, "pexels")

    async def test_square_content_photo_rejected_by_aspect_gate(self):
        # Big enough by long edge, but square: a full-bleed hero band can't
        # use it without an awkward crop, so the aspect gate defers to stock.
        pexels = self._stock()
        resolver = self._resolver(
            [{"url": "https://x/square.jpg", "role": "content", "intent": "hero",
              "source_usage": "inline", "width": 1300, "height": 1300,
              "alt": "music therapy"}],
            pexels,
        )

        photo = await resolver.resolve(
            "music therapy", intent="hero", slot_usage="background"
        )

        self.assertEqual(photo.source, "pexels")

    async def test_landscape_content_photo_still_wins_hero_background(self):
        pexels = self._stock()
        resolver = self._resolver(
            [{"url": "https://x/banner.jpg", "role": "content", "intent": "hero",
              "source_usage": "inline", "width": 1920, "height": 1080,
              "alt": "music therapy"}],
            pexels,
        )

        photo = await resolver.resolve(
            "music therapy", intent="hero", slot_usage="background"
        )

        self.assertEqual(photo.source, "scraped")
        self.assertEqual(photo.url, "https://x/banner.jpg")

    async def test_portrait_size_fallback_never_fills_inline_about_slot(self):
        # The page-local size fallback bypasses the ranker; its own role filter
        # must keep a headshot (the biggest image on a directory page) out of
        # the about split even for inline usage where no size gate applies.
        from app.models.content_blocks import ImageMetadata

        pexels = FakePexels(
            {"warm consultation room": [
                _photo("https://pexels.example/room.jpg", "warm consultation room")
            ]}
        )
        portrait = ImageMetadata(
            url="https://x/face.jpg", role="portrait", intent="generic",
            alt="Jane Doe", width=1600, height=1600,
        )
        resolver = ImageResolver(
            scraped_metadata=[portrait], pexels=pexels, use_llm_tiebreaker=False,
        )

        photo = await resolver.resolve(
            "warm consultation room", intent="about", slot_usage="inline",
            prefer=[portrait],
        )

        self.assertEqual(photo.source, "pexels")


def _abstract_photo(url, avg_color):
    return PhotoResult(
        url=url, alt="Abstract texture", photographer="Tester",
        photographer_url=None, source="pexels", avg_color=avg_color,
    )


class AbstractBgTest(unittest.IsolatedAsyncioTestCase):
    async def test_resolve_abstract_bg_picks_nearest_color_match_and_marks_seen(self):
        pexels = FakePexels(
            {
                "abstract texture": [
                    _abstract_photo("https://pexels.example/red.jpg", "#ff0000"),
                    _abstract_photo("https://pexels.example/blue.jpg", "#0000ff"),
                ]
            }
        )
        resolver = ImageResolver(pexels=pexels)

        photo = await resolver.resolve_abstract_bg(
            "abstract texture", color_target_hex="#0000ee"
        )

        self.assertEqual(photo.url, "https://pexels.example/blue.jpg")
        self.assertIn("https://pexels.example/blue.jpg", resolver._seen_pexels_urls)

    async def test_resolve_abstract_bg_reuses_seen_candidate_when_fresh_pool_exhausted(self):
        pexels = FakePexels(
            {
                "abstract texture": [
                    _abstract_photo("https://pexels.example/red.jpg", "#ff0000"),
                    _abstract_photo("https://pexels.example/blue.jpg", "#0000ff"),
                ]
            }
        )
        resolver = ImageResolver(pexels=pexels)
        # Simulate an earlier page on the same site already having consumed
        # both candidates via the identical theme-wide abstract query.
        resolver._seen_pexels_urls.update(
            {"https://pexels.example/red.jpg", "https://pexels.example/blue.jpg"}
        )

        photo = await resolver.resolve_abstract_bg(
            "abstract texture", color_target_hex="#0000ee"
        )

        self.assertIsNotNone(photo)
        self.assertEqual(photo.url, "https://pexels.example/blue.jpg")

    async def test_resolve_abstract_bg_returns_none_when_no_candidate_has_avg_color(self):
        pexels = FakePexels(
            {"abstract texture": [_abstract_photo("https://pexels.example/red.jpg", None)]}
        )
        resolver = ImageResolver(pexels=pexels)

        photo = await resolver.resolve_abstract_bg(
            "abstract texture", color_target_hex="#0000ee"
        )

        self.assertIsNone(photo)

    async def test_resolve_abstract_bg_returns_none_when_pexels_unconfigured(self):
        pexels = FakePexels({})
        pexels.configured = False
        resolver = ImageResolver(pexels=pexels)

        photo = await resolver.resolve_abstract_bg(
            "abstract texture", color_target_hex="#0000ee"
        )

        self.assertIsNone(photo)
        self.assertEqual(pexels.queries, [])
