"""Scaffold-batch concurrency (settings.scaffold_batch_concurrency).

Default 1 must stay strictly serial (the single-GPU shape). >1 runs work items
in parallel under a semaphore, keeps batches depth-pure, and still hands every
child page its parent's hero context — a child's call never starts before its
OWN parent's call finished, and never waits for anything else.

Also pins the entry-text trim: only the FIRST work item's user prompt carries
the entry page's raw_text; later items ground pages in their own page_source.
"""

from __future__ import annotations

import asyncio
import json
import unittest

from app.config import settings
from app.models.content_blocks import HeroBlock, PagePlan, SourceContent
from app.models.industry import PageScaffold
from app.services.planner import ScaffoldedSitePlan, plan_site_with_scaffolds


class _RecordingClient:
    """Fake LLM that fabricates a page per requested slug while recording
    payloads, start/end event order, and in-flight concurrency."""

    def __init__(self, delay: float = 0.01, delays: dict[str, float] | None = None):
        self._delays = delays or {}
        self.payloads: list[dict] = []
        self.events: list[tuple[str, tuple[str, ...]]] = []
        self.in_flight = 0
        self.max_in_flight = 0
        self._delay = delay

    async def chat_json(self, **kwargs):
        payload = json.loads(kwargs["user_prompt"])
        slugs = tuple(p["slug"] for p in payload["pages_requested"])
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        self.events.append(("start", slugs))
        try:
            await asyncio.sleep(max(self._delays.get(slug, self._delay) for slug in slugs))
            self.payloads.append(payload)
            pages = [
                PagePlan(
                    page_type=p.get("page_type", "landing"),
                    slug=p["slug"],
                    title=p["title"],
                    blocks=[HeroBlock(headline=f"Hero for {p['title']}")],
                    seo_title="t",
                    seo_description="d",
                )
                for p in payload["pages_requested"]
            ]
            return ScaffoldedSitePlan(site_name="Acme", pages=pages)
        finally:
            self.events.append(("end", slugs))
            self.in_flight -= 1


# Enough sections that any TWO pages exceed max_sections_per_batch, so each page
# stays its own batch item and there is per-page concurrency to observe. Derived
# from the setting rather than hardcoded: at a literal four sections these tests
# silently stopped testing anything the moment the cap moved 6 -> 10, because all
# four pages collapsed into one call and max_in_flight could only ever be 1.
_SECTION_POOL = ["hero", "about", "features", "services", "process", "team",
                 "testimonials", "faq", "pricing", "gallery", "stats", "cta"]


def _page_sections() -> list[str]:
    n = max(2, settings.max_sections_per_batch // 2 + 1)
    return _SECTION_POOL[:n]


def _scaffold(slug: str, *, parent: str | None = None, sections=None) -> PageScaffold:
    return PageScaffold(
        page_type="home" if slug == "" else "landing",  # type: ignore[arg-type]
        slug=slug,
        title=slug.split("/")[-1].replace("-", " ").title() or "Home",
        is_homepage=slug == "",
        sections=sections or _page_sections(),  # type: ignore[arg-type]
        parent_slug=parent,
    )


def _source() -> SourceContent:
    return SourceContent(
        source_kind="url",
        source_ref="https://acme.example",
        title="Acme",
        raw_text="Acme builds things. We offer web and seo services.",
    )


# Two depth-0 pages + two depth-1 children, each carrying _page_sections() so
# two pages always exceed max_sections_per_batch → every page is its own batch
# item: 2 items at depth 0, 2 items at depth 1.
def _scaffolds() -> list[PageScaffold]:
    return [
        _scaffold(""),
        _scaffold("services"),
        _scaffold("services/web", parent="services"),
        _scaffold("services/seo", parent="services"),
    ]


class SerialDefaultTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig = settings.scaffold_batch_concurrency
        settings.scaffold_batch_concurrency = 1

    def tearDown(self):
        settings.scaffold_batch_concurrency = self._orig

    async def test_default_is_strictly_serial_in_depth_order(self):
        client = _RecordingClient()
        plan, source_map = await plan_site_with_scaffolds(
            _source(), None, _scaffolds(), client
        )

        self.assertEqual(client.max_in_flight, 1)
        # Every start is immediately followed by its own end — no interleaving.
        for i in range(0, len(client.events), 2):
            self.assertEqual(client.events[i][0], "start")
            self.assertEqual(client.events[i + 1][0], "end")
            self.assertEqual(client.events[i][1], client.events[i + 1][1])
        # Pages come back in the original picker order; routing map returned.
        self.assertEqual(
            [p.slug for p in plan.pages], ["", "services", "services/web", "services/seo"]
        )
        self.assertEqual(
            set(source_map), {"", "services", "services/web", "services/seo"}
        )

    async def test_only_first_item_carries_entry_raw_text(self):
        client = _RecordingClient()
        await plan_site_with_scaffolds(_source(), None, _scaffolds(), client)

        self.assertIn("raw_text", client.payloads[0]["source"])
        for later in client.payloads[1:]:
            self.assertNotIn("raw_text", later["source"])
            # Pages still ground themselves in their own page_source.
            for page in later["pages_requested"]:
                self.assertIn("page_source", page)


class ConcurrentDepthGroupsTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._orig = settings.scaffold_batch_concurrency
        settings.scaffold_batch_concurrency = 3

    def tearDown(self):
        settings.scaffold_batch_concurrency = self._orig

    async def test_same_depth_runs_parallel_and_children_follow_their_parent(self):
        client = _RecordingClient()
        plan, _ = await plan_site_with_scaffolds(_source(), None, _scaffolds(), client)

        # Real parallelism, bounded by the setting.
        self.assertGreater(client.max_in_flight, 1)
        self.assertLessEqual(client.max_in_flight, 3)

        # The parent's call ENDS before either child's call STARTS.
        parent_end = client.events.index(("end", ("services",)))
        for child in ("services/web", "services/seo"):
            self.assertLess(parent_end, client.events.index(("start", (child,))))

        # Output order stays deterministic regardless of completion order.
        self.assertEqual(
            [p.slug for p in plan.pages], ["", "services", "services/web", "services/seo"]
        )

    async def test_a_slow_page_does_not_hold_back_children_of_another(self):
        """The old depth barrier made every child wait for the slowest page one
        level up; a child needs only its own parent's hero."""
        client = _RecordingClient(delays={"": 0.3})
        plan, _ = await plan_site_with_scaffolds(_source(), None, _scaffolds(), client)

        home_end = client.events.index(("end", ("",)))
        for child in ("services/web", "services/seo"):
            self.assertLess(client.events.index(("start", (child,))), home_end)
        self.assertEqual(
            [p.slug for p in plan.pages], ["", "services", "services/web", "services/seo"]
        )

    async def test_a_parent_at_the_same_depth_is_never_awaited_or_shown(self):
        """A parent one slug-level DOWN the same depth (a roster's profile page)
        was generated beside its child under the depth barrier, so the child's
        prompt never carried its hero. It still doesn't — the prompt is the one
        the barrier sent."""
        scaffolds = [
            _scaffold(""),
            _scaffold("team"),
            _scaffold("team-ashley", parent="team"),
        ]
        client = _RecordingClient(delays={"team": 0.2})
        await plan_site_with_scaffolds(_source(), None, scaffolds, client)

        ashley = next(
            page for payload in client.payloads
            for page in payload["pages_requested"] if page["slug"] == "team-ashley"
        )
        self.assertNotIn("parent_context", ashley)
        self.assertLess(
            client.events.index(("start", ("team-ashley",))),
            client.events.index(("end", ("team",))),
        )

    async def test_children_receive_parent_hero_context(self):
        client = _RecordingClient()
        await plan_site_with_scaffolds(_source(), None, _scaffolds(), client)

        child_pages = [
            page
            for payload in client.payloads
            for page in payload["pages_requested"]
            if page["slug"].startswith("services/")
        ]
        self.assertEqual(len(child_pages), 2)
        for page in child_pages:
            self.assertEqual(
                page["parent_context"]["headline"], "Hero for Services"
            )

    async def test_batches_are_depth_pure_when_concurrent(self):
        # Two-section pages would pack across the depth boundary in serial
        # mode; concurrent mode must seal the batch at the boundary instead.
        scaffolds = [
            _scaffold("", sections=["hero", "cta"]),
            _scaffold("services", sections=["hero", "cta"]),
            _scaffold("services/web", parent="services", sections=["hero", "cta"]),
            _scaffold("services/seo", parent="services", sections=["hero", "cta"]),
        ]
        client = _RecordingClient()
        await plan_site_with_scaffolds(_source(), None, scaffolds, client)

        for payload in client.payloads:
            depths = {p["slug"].count("/") for p in payload["pages_requested"]}
            self.assertEqual(len(depths), 1, f"mixed-depth batch: {payload['pages_requested']}")


if __name__ == "__main__":
    unittest.main()
