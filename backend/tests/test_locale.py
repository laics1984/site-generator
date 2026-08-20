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


class CctldFromHostnameTest(unittest.TestCase):
    """A ccTLD in the URL is evidence on its own.

    It never used to be: `_bounded` guards its left edge with `(?<![a-z])`, and
    every real domain has a letter immediately before the dot, so `.my` failed
    against "kopitiam.com.my" and "kopitiam.my" alike. All 36 ccTLDs were dead
    and the `urls` argument contributed nothing — a site whose copy happened not
    to name a city or a phone code got no market cue, and its stock imagery came
    back un-localised. Matching against the parsed hostname fixes that without
    reading query strings or paths as evidence.
    """

    def test_second_level_cctld_is_confident(self):
        market = detect_market("Fresh bread baked daily.", urls=["https://roti.com.my/"])

        assert market is not None
        self.assertEqual(market.country, "Malaysia")
        self.assertEqual(image_query_cue(market), "Southeast Asian")
        self.assertEqual(place_query_cue(market), "Malaysia")

    def test_bare_cctld_is_confident(self):
        market = detect_market("Book an appointment today.", urls=["https://clinic.sg/"])

        assert market is not None
        self.assertEqual(market.country, "Singapore")

    def test_co_uk_is_confident(self):
        market = detect_market("Serving brunch since 2010.", urls=["https://baker.co.uk/"])

        assert market is not None
        self.assertEqual(market.country, "United Kingdom")
        self.assertEqual(image_query_cue(market), "European")

    def test_generic_tld_is_still_no_signal(self):
        self.assertIsNone(
            detect_market("Serving brunch since 2010.", urls=["https://baker.com/"])
        )

    def test_query_string_is_not_evidence(self):
        """The trap a whole-URL substring match falls into.

        `?user.id=3` is a query parameter, not an Indonesian domain. Matching the
        joined URL string scores it as one; matching the hostname does not.
        """
        self.assertIsNone(
            detect_market("Generic copy.", urls=["https://cdn.example.com/i?user.id=3"])
        )

    def test_path_segment_is_not_evidence(self):
        self.assertIsNone(
            detect_market("Generic copy.", urls=["https://example.com/video.id"])
        )
        self.assertIsNone(
            detect_market("Generic copy.", urls=["https://example.com/a.in?x=1"])
        )

    def test_longer_tld_still_does_not_bleed(self):
        """`.ca` must not fire on `.cat`, `.in` must not fire on `.info`."""
        self.assertIsNone(detect_market("Generic copy.", urls=["https://example.cat/"]))
        self.assertIsNone(detect_market("Generic copy.", urls=["https://example.info/"]))

    def test_unparseable_url_is_skipped(self):
        self.assertIsNone(detect_market("Generic copy.", urls=["not a url", ""]))
