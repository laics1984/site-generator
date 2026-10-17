"""Design-quality benchmark — representative businesses, offline, repeatable.

Builds every business in ``tests/fixtures/design_bench/businesses.json`` (and
each one again under alternative logo colours) through the same calls
``/generate/with-pages`` makes — brand brief → ``resolve_direction`` →
``plan_to_site`` — with every LLM pass off and deterministic fake stock
(the ``measure_design_diversity`` harness). It reports, per site, the design
direction and scheme the pipeline chose, the design fingerprint, and the
design-quality report; per archetype group, how alike the sites came out.

    cd backend && .venv/bin/python scripts/design_benchmark.py
    cd backend && .venv/bin/python scripts/design_benchmark.py --out bench.json --dump sites/
    cd backend && .venv/bin/python scripts/design_benchmark.py --compare tests/fixtures/design_bench/baseline.json
    cd backend && .venv/bin/python scripts/design_benchmark.py --score-dir sites/   # re-score dumped trees

``--dump`` writes each finished ``GeneratedSite`` as JSON, so a tree built by an
older revision can be scored by the current evaluator (``--score-dir``) — the
evaluator is pure over the tree. Screenshots are taken from dumped sites by
``scripts/design_screenshots.py``.

What this measures is the deterministic floor: the model's concept pick, the
AI palette and the section recipe are off, as in ``measure_design_diversity``.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import sys
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.models.brand import AxisReading, BrandIdentity, BrandPersonality  # noqa: E402
from app.models.content_blocks import (  # noqa: E402
    AboutBlock,
    CtaBlock,
    FeatureItem,
    FeaturesBlock,
    HeroBlock,
    ServiceItem,
    ServicesBlock,
    resolve_mood,
)
from app.services.brand_brief import mood_from_personality  # noqa: E402
from app.services.design_direction import ContentProfile, resolve_direction  # noqa: E402
from app.services.design_fingerprint import fingerprint_of, similarity  # noqa: E402
from app.services.landing_patterns import homepage_sections  # noqa: E402
from app.services.schema_builder import plan_to_site  # noqa: E402
from scripts.measure_design_diversity import (  # noqa: E402
    _block,
    fixture_plan,
    offline_generation,
    source_photos,
)

FIXTURES = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "design_bench" / "businesses.json"


def load_businesses(path: Path = FIXTURES) -> tuple[list[dict[str, Any]], list[str | None]]:
    data = json.loads(path.read_text())
    return data["businesses"], data.get("logo_variants", [])


def _supports(fn: Any, name: str) -> bool:
    return name in inspect.signature(fn).parameters


def _copy_block(kind: str, business: dict[str, Any]):
    """The fixture's own copy for a section kind, else the harness's generic one."""
    copy = business.get("copy", {}).get(kind)
    name = business["name"]
    if copy is None:
        return _block(kind, name)
    if kind == "hero":
        return HeroBlock(headline=copy[0], subheadline=copy[1], image_query=business.get("photo_query") or business["industry"])
    if kind == "features":
        return FeaturesBlock(
            heading=copy[0],
            items=[FeatureItem(title=t, description="A sentence that explains the benefit plainly.") for t in copy[1]],
        )
    if kind == "services":
        return ServicesBlock(
            heading=copy[0],
            items=[ServiceItem(title=t, description="Who it suits and what it includes.") for t in copy[1]],
        )
    if kind == "about":
        return AboutBlock(heading=copy[0], body=copy[1] * 2, image_query=f"{business.get('photo_query') or business['industry']} team")
    if kind == "cta":
        return CtaBlock(headline=copy[0], cta_label=copy[1])
    return _block(kind, name)


def _personality(axes: dict[str, int]) -> BrandPersonality:
    return BrandPersonality(**{a: AxisReading(value=v, evidence="fixture") for a, v in axes.items()})


def _archetype_for(business: dict[str, Any]) -> str | None:
    """The archetype the pipeline would read from this source, when the
    revision under test can read one (feature-detected, so the same script
    measures the baseline)."""
    try:
        from app.services.business_archetype import archetype_from_evidence
    except ImportError:
        return None
    return archetype_from_evidence(business["summary"], business["industry"])


class DiskCachedStock:
    """The real Pexels client behind a JSON file cache, for screenshot runs.

    Two builds (a baseline revision and the current one) then dress the same
    queries in the same photographs, so screenshots differ only by design, and
    a re-run costs no API calls. Stands in for `media.get_pexels_client`."""

    configured = True

    def __init__(self, path: Path) -> None:
        from app.services.pexels import PexelsClient

        self.path = path
        self._client = PexelsClient()
        self._data: dict[str, list[dict[str, Any]]] = (
            json.loads(path.read_text()) if path.exists() else {}
        )

    async def search_many(self, query: str, *, orientation: str = "landscape", size: str = "large", **kw):
        from app.services.pexels import PhotoResult

        key = f"{orientation}|{size}|{query.strip().lower()}"
        if key not in self._data:
            results = await self._client.search_many(query, orientation=orientation, size=size, **kw)  # type: ignore[arg-type]
            self._data[key] = [
                {"url": r.url, "alt": r.alt, "photographer": r.photographer,
                 "photographer_url": r.photographer_url, "avg_color": r.avg_color}
                for r in results
            ]
            self.path.write_text(json.dumps(self._data))
        return [PhotoResult(source="pexels", **row) for row in self._data[key]]

    async def search(self, query: str, **kw):
        results = await self.search_many(query, **kw)
        return results[0] if results else None


_STOCK: DiskCachedStock | None = None


async def _business_photos(business: dict[str, Any]):
    """The business's own photography: the fixture's placeholder URLs, or — in
    a screenshot run — real photographs found for its `photo_query`, standing
    in for what its source would supply."""
    count = business.get("photos", 6)
    if _STOCK is None or not count:
        return source_photos(business["name"])[:count]
    from app.models.content_blocks import ImageMetadata

    results = await _STOCK.search_many(business.get("photo_query") or business["industry"])
    return [
        ImageMetadata(
            url=r.url.replace("h=650&w=940", "w=1600"),
            alt=r.alt,
            role="content",
            width=1600,
            height=1067,
        )
        for r in results[:count]
    ]


async def build_site(business: dict[str, Any], logo: str | None):
    name, industry = business["name"], business["industry"]
    images = await _business_photos(business)
    personality = _personality(business.get("personality", {}))
    # The router's precedence: ground_brief derives the mood from >=3 known
    # personality axes, which outranks the industry default.
    mood = resolve_mood(
        override=None, brand_mood=None, detected=mood_from_personality(personality), industry=industry
    )
    archetype = _archetype_for(business)

    kwargs: dict[str, Any] = dict(
        brand_name=name,
        mood=mood,
        industry=industry,
        logo_hex=logo,
        personality=personality,
        positioning=business.get("positioning"),
        audience=business.get("audience"),
        business_context=business.get("summary"),
        content=ContentProfile.of_images(images, prose_chars=len(business.get("summary", "")) * 10),
        use_model=False,
    )
    if archetype and _supports(resolve_direction, "archetype"):
        kwargs["archetype"] = archetype
    direction = await resolve_direction(**kwargs)

    section_kwargs: dict[str, Any] = {"seed": name}
    creative = getattr(direction, "creative", None)
    if creative is not None and _supports(homepage_sections, "emphasis"):
        section_kwargs["emphasis"] = creative.section_emphasis
    if archetype and _supports(homepage_sections, "archetype"):
        section_kwargs["archetype"] = archetype
    kinds = homepage_sections(industry, **section_kwargs)

    plan = fixture_plan(name, industry)
    plan.pages[0].blocks = [b for k in kinds if (b := _copy_block(k, business)) is not None]
    brand = BrandIdentity(name=name, mood=mood, industry=industry, extracted_palette=[logo] if logo else [])
    site = await plan_to_site(
        plan,
        brand=brand,
        theme=direction.theme,
        scraped_images=[m.url for m in images],
        scraped_metadata=images,
    )
    return site


def _site_json(site) -> dict[str, Any]:
    return json.loads(site.model_dump_json())


def score_site(site_json: dict[str, Any]) -> dict[str, Any] | None:
    """The design-quality report for a dumped site, when the evaluator exists."""
    try:
        from app.services.design_quality import evaluate_site_json, summary
    except ImportError:
        return None
    return summary(evaluate_site_json(site_json))


def _manifest(site_json: dict[str, Any]) -> dict[str, Any]:
    return site_json.get("design_manifest") or {}


def summarise(key: str, logo: str | None, site, site_json: dict[str, Any]) -> dict[str, Any]:
    fp = fingerprint_of(site)
    manifest = _manifest(site_json)
    direction = (manifest.get("direction") or {}).get("slug")
    return {
        "key": key,
        "logo": logo,
        "direction": direction,
        "archetype": (manifest.get("direction") or {}).get("archetype"),
        "mood": site_json.get("theme", {}).get("mood") or (manifest.get("mood")),
        "scheme": fp.scheme,
        "hero": fp.hero,
        "header": fp.header,
        "footer": fp.footer,
        "canvas": fp.canvas,
        "heading_font": fp.heading_font,
        "radius": fp.radius,
        "sections": list(fp.sections),
        "bands": list(fp.bands),
        "quality": score_site(site_json),
        "_fp": fp,
    }


def group_similarity(rows: list[dict[str, Any]], by: str) -> dict[str, dict[str, float]]:
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(row[by], []).append(row["_fp"])
    out = {}
    for group, fps in groups.items():
        pairs = [similarity(a, b) for a, b in combinations(fps, 2)]
        if pairs:
            out[group] = {"mean": round(mean(pairs), 3), "max": round(max(pairs), 3), "sites": len(fps)}
    return out


async def run(*, variants: bool, dump: Path | None, real_stock: Path | None = None) -> dict[str, Any]:
    global _STOCK
    from app.services import media

    businesses, logo_variants = load_businesses()
    rows: list[dict[str, Any]] = []
    # HERO_FULLBLEED_ALL_PAGES=false is what the operator's .env runs.
    with offline_generation(schemes=True, history=False, fullbleed=False):
        if real_stock is not None:
            _STOCK = DiskCachedStock(real_stock)
            media.get_pexels_client = lambda: _STOCK  # restored by offline_generation
        for business in businesses:
            logos = [business["logo"], *(logo_variants if variants else [])]
            for i, logo in enumerate(logos):
                site = await build_site(business, logo)
                site_json = _site_json(site)
                key = business["key"] if i == 0 else f"{business['key']}~{i}"
                row = summarise(key, logo, site, site_json)
                row["industry"] = business["industry"]
                row["base"] = business["key"]
                row["variant"] = i
                rows.append(row)
                if dump is not None:
                    dump.mkdir(parents=True, exist_ok=True)
                    (dump / f"{key}.json").write_text(json.dumps(site_json))
    return {
        "sites": [{k: v for k, v in r.items() if k != "_fp"} for r in rows],
        # Across DIFFERENT businesses (each on its own logo): the sameness a
        # shop generating several sites in one industry would see.
        "similarity_by_industry": group_similarity([r for r in rows if r["variant"] == 0], "industry"),
        "similarity_by_business": group_similarity(rows, "base"),
        "distinct": {
            "directions": len({r["direction"] for r in rows if r["direction"]}),
            "schemes": len({r["scheme"] for r in rows}),
            "heroes": len({r["hero"] for r in rows}),
            "fonts": len({r["heading_font"] for r in rows}),
        },
    }


def score_dir(path: Path) -> dict[str, Any]:
    return {
        p.stem: score_site(json.loads(p.read_text()))
        for p in sorted(path.glob("*.json"))
    }


def compare(current: dict[str, Any], baseline: dict[str, Any]) -> list[str]:
    """Human-readable deltas between two benchmark results."""
    lines = []
    base_sites = {s["key"]: s for s in baseline.get("sites", [])}
    for site in current["sites"]:
        old = base_sites.get(site["key"])
        if not old:
            continue
        q_new = (site.get("quality") or {}).get("score")
        q_old = (old.get("quality") or {}).get("score")
        lines.append(
            f"{site['key']:30} scheme {old['scheme'] or '-':>20} → {site['scheme'] or '-':20} "
            f"dir={site.get('direction') or '-':24} quality {q_old} → {q_new}"
        )
    for group, now in current["similarity_by_industry"].items():
        then = baseline.get("similarity_by_industry", {}).get(group)
        if then:
            lines.append(f"similarity[{group}] mean {then['mean']} → {now['mean']}, max {then['max']} → {now['max']}")
    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-variants", action="store_true", help="skip the alternative logo colours")
    parser.add_argument("--out", type=Path, help="write the result JSON here")
    parser.add_argument("--dump", type=Path, help="write every built site's JSON into this directory")
    parser.add_argument("--compare", type=Path, help="print deltas against a previous --out")
    parser.add_argument("--score-dir", type=Path, help="score dumped site JSON files and exit")
    parser.add_argument(
        "--real-stock", type=Path,
        help="dress sites in real Pexels photos through this JSON cache (needs PEXELS_API_KEY); for screenshots",
    )
    args = parser.parse_args()

    import logging

    logging.disable(logging.WARNING)
    if args.score_dir:
        print(json.dumps(score_dir(args.score_dir), indent=2))
        return
    result = asyncio.run(run(variants=not args.no_variants, dump=args.dump, real_stock=args.real_stock))
    if args.out:
        args.out.write_text(json.dumps(result, indent=2) + "\n")
    for site in result["sites"]:
        q = (site.get("quality") or {}).get("score")
        print(
            f"{site['key']:30} dir={site.get('direction') or '-':24} scheme={site['scheme'] or '-':20} "
            f"hero={site['hero']:24} {site['header']}/{site['footer']} font={site['heading_font']:14} q={q}"
        )
    print("distinct:", result["distinct"])
    for group, sim in result["similarity_by_industry"].items():
        print(f"similarity[{group}]: {sim}")
    if args.compare:
        for line in compare(result, json.loads(args.compare.read_text())):
            print(line)


if __name__ == "__main__":
    main()
