import unittest

from app.services.locale import detect_market, image_query_cue, place_query_cue


class DetectMarketTest(unittest.TestCase):
    def test_no_signal_returns_none(self):
        self.assertIsNone(detect_market("We make great software for everyone."))
        self.assertIsNone(detect_market(None))

    def test_malaysia_confident_from_cctld_and_places(self):
        market = detect_market(
            "Visit our Kuala Lumpur clinic. Call +60 3-1234 5678.",
            urls=["https://klinik.example.my/about"],
        )

        assert market is not None
        self.assertEqual(market.country, "Malaysia")
        self.assertEqual(market.demonym, "Malaysian")
        self.assertEqual(image_query_cue(market), "Southeast Asian")
        self.assertEqual(place_query_cue(market), "Malaysia")

    def test_united_kingdom_detected(self):
        market = detect_market(
            "Our London office serves clients across the United Kingdom. "
            "Prices from £250. Call +44 20 7946 0000.",
        )

        assert market is not None
        self.assertEqual(market.country, "United Kingdom")
        self.assertEqual(market.demonym, "British")
        self.assertEqual(image_query_cue(market), "European")

    def test_india_detected_from_phone_and_places(self):
        market = detect_market("Serving Mumbai and Pune since 2009. Call +91 98765 43210.")

        assert market is not None
        self.assertEqual(market.country, "India")
        self.assertEqual(market.demonym, "Indian")
        self.assertEqual(image_query_cue(market), "South Asian")

    def test_australia_detected(self):
        market = detect_market(
            "Sydney and Melbourne studios.", urls=["https://studio.example.com.au"]
        )

        assert market is not None
        self.assertEqual(market.country, "Australia")
        self.assertEqual(place_query_cue(market), "Australia")

    def test_cctld_does_not_bleed_into_longer_tlds(self):
        # ".in" must not fire on .info domains, ".my" not on myshopify.
        self.assertIsNone(
            detect_market("Welcome.", urls=["https://clinic.example.info/page"])
        )
        self.assertIsNone(
            detect_market("Welcome.", urls=["https://shop.myshopify.com/x"])
        )

    def test_place_does_not_bleed_into_longer_words(self):
        # "india" inside "indiana" is not an India signal.
        self.assertIsNone(detect_market("Our Indianapolis, Indiana warehouse."))

    def test_weak_evidence_falls_back_to_region(self):
        # A lone place mention (score 2) is below the confidence threshold.
        market = detect_market("We loved our trip to Bangkok.")

        assert market is not None
        self.assertIsNone(market.country)
        self.assertEqual(market.demonym, "Southeast Asian")
        self.assertEqual(place_query_cue(market), "Southeast Asia")


if __name__ == "__main__":
    unittest.main()
