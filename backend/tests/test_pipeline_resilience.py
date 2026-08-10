"""Guards for the pipeline-resilience fixes.

Each test pins a behaviour whose absence was a real, silent failure:

- page-type inference typed /how-we-work as a portfolio page, because `work`
  matched as a bare substring and sat earlier in the hint table than `process`;
- the politeness circuit breaker latched for the life of the process, so one
  bad crawl blocked a host until the backend restarted;
- the entry page's link cap was applied in raw document order, so a mega-menu
  could push every content link out of the crawl frontier;
- one failed LLM batch aborted the entire generation, discarding every batch
  that had already succeeded;
- token budgeting billed CJK text at the English chars-per-token rate, silently
  overflowing the model's context on a Chinese-language page.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from app.config import settings
from app.models.content_blocks import HeroBlock, PagePlan, SourceContent
from app.models.industry import PageScaffold
from app.services.llm import LlmError
from app.services.page_inference import _explicit_page_type, _infer_page_type
from app.services.planner import (
    ScaffoldedSitePlan,
    _estimate_tokens,
    plan_site_with_scaffolds,
)
from app.services.polite import HostPoliteness
from app.services.scraper import _extract_links


# --- page-type inference -----------------------------------------------------


class PageTypeMatchingTest(unittest.TestCase):
    def test_multiword_hint_beats_earlier_singleword_hint(self):
        # `work` sits before `process` in _TYPE_HINTS, and "work" IS a whole
        # token here — only the multi-word-first pass gets this right.
        self.assertEqual(_infer_page_type("how-we-work"), "process")

    def test_substring_no_longer_claims_unrelated_slugs(self):
        # Each of these matched a hint as a bare substring before.
        self.assertNotEqual(_infer_page_type("framework"), "work")
        self.assertNotEqual(_infer_page_type("teamwork"), "team")
        self.assertNotEqual(_infer_page_type("network"), "work")
        self.assertNotEqual(_infer_page_type("helpful-resources"), "faq")
        # ...and fall through to the depth-based default instead.
        self.assertEqual(_infer_page_type("framework"), "services")

    def test_genuine_matches_still_match(self):
        cases = {
            "contact": "contact",
            "our-team": "team",
            "committee": "team",
            "find-a-music-therapist": "team",
            "case-studies": "work",
            "pricing": "pricing",
            "media-centre": "blog",
            "our-story": "about",
            "privacy-policy": "privacy",
            "how-we-work": "process",
            "services": "services",
        }
        for slug, expected in cases.items():
            with self.subTest(slug=slug):
                self.assertEqual(_infer_page_type(slug), expected)

    def test_title_is_matched_as_well_as_slug(self):
        self.assertEqual(_infer_page_type("kontakt-oss", "Contact Us"), "contact")

    def test_homepage_and_depth_defaults_unchanged(self):
        self.assertEqual(_infer_page_type(""), "home")
        self.assertEqual(_infer_page_type("widgets"), "services")
        self.assertEqual(_infer_page_type("widgets/blue"), "landing")

    def test_explicit_type_returns_none_when_nothing_evidenced(self):
        # _explicit_page_type must not fall back to "services" — callers use
        # None to mean "this slug says nothing about its type".
        self.assertIsNone(_explicit_page_type("widgets"))
        self.assertEqual(_explicit_page_type("services"), "services")


# --- politeness circuit breaker ---------------------------------------------


class PolitenessCircuitTest(unittest.TestCase):
    def test_circuit_opens_after_threshold(self):
        host = HostPoliteness(host="example.com")
        for _ in range(5):
            host.record_failure()
        self.assertTrue(host.circuit_open)

    def test_circuit_half_opens_after_cooldown(self):
        host = HostPoliteness(host="example.com", circuit_cooldown_sec=0.05)
        for _ in range(5):
            host.record_failure()
        self.assertTrue(host.circuit_open)

        import time as _time

        _time.sleep(0.06)
        # Cooled down: closes itself and forgets the failure streak, so the
        # next crawl gets a full set of attempts rather than tripping instantly.
        self.assertFalse(host.circuit_open)
        host.record_failure()
        self.assertFalse(host.circuit_open)

    def test_success_resets_the_streak(self):
        host = HostPoliteness(host="example.com")
        for _ in range(4):
            host.record_failure()
        host.record_success()
        for _ in range(4):
            host.record_failure()
        self.assertFalse(host.circuit_open)


# --- entry-page link extraction ----------------------------------------------


class ExtractLinksOrderingTest(unittest.TestCase):
    def _soup(self, html: str):
        from bs4 import BeautifulSoup

        return BeautifulSoup(html, "lxml")

    def test_crawlable_links_sort_ahead_of_chrome(self):
        html = """
        <a href="https://twitter.com/acme">Twitter</a>
        <a href="/brochure.pdf">Brochure</a>
        <a href="/login">Login</a>
        <a href="/about">About</a>
        <a href="/services">Services</a>
        """
        links = _extract_links(self._soup(html), "https://acme.example/")
        # Same-host page links come first, in document order...
        self.assertEqual(
            links[:2], ["https://acme.example/about", "https://acme.example/services"]
        )
        # ...and nothing is dropped; the rest merely sort after.
        self.assertEqual(len(links), 5)

    def test_content_links_survive_a_large_nav(self):
        # 60 external links ahead of the only content link: under the old
        # document-order cap of 50, /about never reached the crawl frontier.
        chrome = "".join(
            f'<a href="https://cdn{i}.example/x">n{i}</a>' for i in range(60)
        )
        html = chrome + '<a href="/about">About</a>'
        links = _extract_links(self._soup(html), "https://acme.example/")
        self.assertEqual(links[0], "https://acme.example/about")


# --- token estimation --------------------------------------------------------


class TokenEstimateTest(unittest.TestCase):
    def test_english_matches_the_legacy_ratio(self):
        text = "a" * 400
        self.assertEqual(_estimate_tokens(text), 100)

    def test_cjk_is_billed_far_denser_than_english(self):
        cjk = "我们提供优质的教育服务" * 40
        english = "a" * len(cjk)
        self.assertGreater(_estimate_tokens(cjk), _estimate_tokens(english) * 2)

    def test_mixed_text_interpolates(self):
        mixed = "Contact us 联系我们" * 20
        self.assertGreater(_estimate_tokens(mixed), _estimate_tokens("a" * len(mixed)))

    def test_empty(self):
        self.assertEqual(_estimate_tokens(""), 0)


# --- per-batch generation failure -------------------------------------------


def _scaffold(slug: str, *, parent: str | None = None) -> PageScaffold:
    return PageScaffold(
        page_type="home" if slug == "" else "landing",  # type: ignore[arg-type]
        slug=slug,
        title=slug.split("/")[-1].replace("-", " ").title() or "Home",
        is_homepage=slug == "",
        sections=["hero", "about", "features", "cta"],  # type: ignore[arg-type]
        parent_slug=parent,
    )


def _source() -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref="https://acme.example",
        title="Acme",
        raw_text="Acme builds things. We offer web and seo services.",
    )


class _FlakyClient:
    """Fake LLM that raises for a chosen set of slugs and succeeds otherwise."""

    def __init__(self, fail_slugs: set[str]):
        self.fail_slugs = fail_slugs
        self.calls = 0

    async def chat_json(self, **kwargs):
        self.calls += 1
        payload = json.loads(kwargs["user_prompt"])
        slugs = {p["slug"] for p in payload["pages_requested"]}
        if slugs & self.fail_slugs:
            raise LlmError(f"simulated failure for {sorted(slugs)}")
        await asyncio.sleep(0)
        return ScaffoldedSitePlan(
            site_name="Acme",
            pages=[
                PagePlan(
                    page_type=p.get("page_type", "landing"),
                    slug=p["slug"],
                    title=p["title"],
                    blocks=[HeroBlock(headline=f"Hero for {p['title']}")],
                    seo_title="t",
                    seo_description="d",
                )
                for p in payload["pages_requested"]
            ],
        )


class BatchFailureDegradationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig = settings.scaffold_batch_concurrency
        settings.scaffold_batch_concurrency = 1

    def tearDown(self):
        settings.scaffold_batch_concurrency = self._orig

    async def test_one_failed_batch_does_not_lose_the_others(self):
        scaffolds = [_scaffold(""), _scaffold("services"), _scaffold("about")]
        client = _FlakyClient(fail_slugs={"services"})

        plan, _ = await plan_site_with_scaffolds(_source(), None, scaffolds, client)

        produced = {p.slug for p in plan.pages}
        self.assertIn("", produced)
        self.assertIn("about", produced)
        # The failed page is reported, and left for scaffold re-materialisation
        # in _align_pages_to_scaffolds rather than fabricated here.
        self.assertEqual(plan.degraded_slugs, ["services"])
        self.assertNotIn("services", produced)

    async def test_site_metadata_survives_when_every_batch_fails(self):
        from app.services.planner import DetectedBrand

        scaffolds = [_scaffold(""), _scaffold("services")]
        client = _FlakyClient(fail_slugs={"", "services"})
        brand = DetectedBrand(
            site_name="Acme Sdn Bhd",
            tagline="We build things",
            industry_category="agency",
        )

        plan, _ = await plan_site_with_scaffolds(_source(), brand, scaffolds, client)

        # No AssertionError, and the plan is still themeable from detection.
        self.assertEqual(plan.site_name, "Acme Sdn Bhd")
        self.assertEqual(plan.industry_category, "agency")
        self.assertEqual(plan.pages, [])
        self.assertEqual(sorted(plan.degraded_slugs), ["", "services"])

    async def test_clean_run_reports_no_degradation(self):
        scaffolds = [_scaffold(""), _scaffold("services")]
        plan, _ = await plan_site_with_scaffolds(
            _source(), None, scaffolds, _FlakyClient(fail_slugs=set())
        )
        self.assertEqual(plan.degraded_slugs, [])
        self.assertEqual(len(plan.pages), 2)

    async def test_degradation_works_under_concurrency(self):
        settings.scaffold_batch_concurrency = 3
        scaffolds = [
            _scaffold(""),
            _scaffold("services"),
            _scaffold("services/web", parent="services"),
        ]
        client = _FlakyClient(fail_slugs={"services/web"})

        plan, _ = await plan_site_with_scaffolds(_source(), None, scaffolds, client)

        self.assertEqual(plan.degraded_slugs, ["services/web"])
        self.assertEqual({p.slug for p in plan.pages}, {"", "services"})


if __name__ == "__main__":
    unittest.main()


# --- sitemap probe caching ---------------------------------------------------


class SitemapProbeCacheTest(unittest.IsolatedAsyncioTestCase):
    """Two callers probe per crawl — the UI's scope check and the crawl's own
    frontier seeding. An index sitemap can pull 8 documents of up to 5 MB each,
    so doing that twice is real bandwidth, not a rounding error."""

    def setUp(self):
        from app.services import sitemap

        sitemap.clear_cache()
        self.addCleanup(sitemap.clear_cache)

    async def test_second_probe_of_the_same_origin_is_served_from_cache(self):
        from unittest import mock

        from app.services import sitemap

        calls = []

        async def _fake(base):
            calls.append(base)
            return sitemap.SitemapProbeResult(
                has_sitemap=True, total_urls=2, urls=["https://acme.test/a"]
            )

        with mock.patch.object(sitemap, "_probe_uncached", _fake):
            first = await sitemap.probe_sitemap("https://acme.test/")
            # A different path on the SAME origin must still hit — the lookup
            # only ever depends on scheme + host.
            second = await sitemap.probe_sitemap("https://acme.test/about")

        self.assertEqual(calls, ["https://acme.test"])
        self.assertEqual(first.urls, second.urls)

    async def test_different_origins_are_probed_separately(self):
        from unittest import mock

        from app.services import sitemap

        calls = []

        async def _fake(base):
            calls.append(base)
            return sitemap.SitemapProbeResult(has_sitemap=False, total_urls=0)

        with mock.patch.object(sitemap, "_probe_uncached", _fake):
            await sitemap.probe_sitemap("https://acme.test/")
            await sitemap.probe_sitemap("https://other.test/")

        self.assertEqual(calls, ["https://acme.test", "https://other.test"])

    async def test_expired_entries_are_reprobed(self):
        from unittest import mock

        from app.services import sitemap

        calls = []

        async def _fake(base):
            calls.append(base)
            return sitemap.SitemapProbeResult(has_sitemap=False, total_urls=0)

        with mock.patch.object(sitemap, "_probe_uncached", _fake), \
             mock.patch.object(sitemap, "_CACHE_TTL", 0.0):
            await sitemap.probe_sitemap("https://acme.test/")
            await sitemap.probe_sitemap("https://acme.test/")

        self.assertEqual(len(calls), 2)

    async def test_a_negative_result_is_cached_too(self):
        # "no sitemap" is the expensive answer to compute — it means every
        # common path was tried and 404'd. Caching it is the point.
        from unittest import mock

        from app.services import sitemap

        calls = []

        async def _fake(base):
            calls.append(base)
            return sitemap.SitemapProbeResult(has_sitemap=False, total_urls=0)

        with mock.patch.object(sitemap, "_probe_uncached", _fake):
            await sitemap.probe_sitemap("https://acme.test/")
            await sitemap.probe_sitemap("https://acme.test/")

        self.assertEqual(len(calls), 1)
