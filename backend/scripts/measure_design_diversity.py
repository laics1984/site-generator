"""Measure how far apart same-industry sites come out — offline, no LLM, no network.

Builds one homepage per fixture brand through the real ``plan_to_site``, reads
each finished site's ``DesignFingerprint`` (services/design_fingerprint.py) and
reports how many distinct palettes, fonts, heroes and section sequences a batch
of businesses in one industry actually received. Every design-identity change
states its before/after here (docs/design-identity-plan.md):

    cd backend && .venv/bin/python scripts/measure_design_diversity.py
    cd backend && .venv/bin/python scripts/measure_design_diversity.py childcare -v
    cd backend && .venv/bin/python scripts/measure_design_diversity.py --schemes on --history

Two modes, because production has both effects:

* default — each brand on its own, diversity history off: the seeded spread,
  i.e. what the brand name and logo alone buy.
* ``--history`` — the brands run in order against an isolated history DB, the
  way a shop generating a batch of sites in an afternoon sees them.

The LLM design passes (design language, design brain) are off. They are where
production's worst convergence lived (one menu, one prompt, one answer), so this
measures the deterministic floor a model can only add to — never the ceiling.
Stock photography is a deterministic fake: a real key would make the numbers
depend on Pexels, and no key at all would make every hero degrade, which
production never sees.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from statistics import mean
from typing import Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.models.brand import BrandIdentity  # noqa: E402
from app.models.content_blocks import (  # noqa: E402
    AboutBlock,
    ContactBlock,
    ContentBlock,
    CtaBlock,
    FaqBlock,
    FaqItem,
    FeatureItem,
    FeaturesBlock,
    GalleryBlock,
    GalleryItem,
    HeroBlock,
    ImageMetadata,
    MenuBlock,
    MenuCategory,
    MenuItem,
    PagePlan,
    PricingBlock,
    PricingTier,
    ProcessBlock,
    ProcessStep,
    ServiceItem,
    ServicesBlock,
    SitePlan,
    TeamBlock,
    TeamMember,
    TestimonialItem,
    TestimonialsBlock,
    resolve_mood,
)
from app.models.brand import AxisReading, BrandPersonality  # noqa: E402
from app.services import media  # noqa: E402
from app.services.design_direction import ContentProfile, resolve_direction  # noqa: E402
from app.services.design_fingerprint import (  # noqa: E402
    DesignFingerprint,
    fingerprint_of,
    similarity,
)
from app.services.landing_patterns import homepage_sections  # noqa: E402
from app.services.pexels import PhotoResult  # noqa: E402
from app.services.schema_builder import plan_to_site  # noqa: E402

# Real-shaped names and the spread of logo colours real businesses bring: a
# red, a green, an amber… a black wordmark, and one with no usable colour.
BRANDS: tuple[tuple[str, str | None], ...] = (
    ("Little Acorns", "#e11d48"),
    ("Tadika Murni", "#16a34a"),
    ("Sunbeam House", "#f59e0b"),
    ("Bright Harbour", "#1d4ed8"),
    ("Glory Fields", "#7c3aed"),
    ("Montessori House KL", None),
    ("Kiddyland Lane", "#0f766e"),
    ("Rainbow Yard", "#ea580c"),
    ("The Learning Tree", "#111827"),
    ("Pelangi Studio", "#db2777"),
)

DEFAULT_INDUSTRIES = ("childcare", "restaurant", "professional-services")

# Brand personalities the way detection reports them (models/brand.py axes),
# cycled over the fixture brands by `--personas`: what ten businesses in one
# industry look like when their own content differs, which is the case the
# brand brief exists for.
PERSONAS: tuple[dict[str, int], ...] = (
    {"playful_sophisticated": 1, "calm_energetic": -2, "organic_structured": -2, "minimal_expressive": -1},
    {"playful_sophisticated": -2, "calm_energetic": 2, "minimal_expressive": 2, "premium_accessible": 2},
    {"playful_sophisticated": 2, "minimal_expressive": -2, "premium_accessible": -1, "organic_structured": 1},
    {"traditional_contemporary": -1, "organic_structured": 1, "premium_accessible": 2, "calm_energetic": -1},
    {"playful_sophisticated": 1, "minimal_expressive": 1, "calm_energetic": -1, "organic_structured": -1},
)


class _FakeStock:
    """Deterministic stand-in for Pexels: one photo per query, its average
    colour derived from the query so the luminance passes still vary."""

    configured = True

    async def search_many(self, query: str, *, orientation: str = "landscape", **_kw):
        digest = hashlib.md5(f"{query}:{orientation}".encode()).hexdigest()
        return [
            PhotoResult(
                url=f"https://images.pexels.com/photos/{int(digest[:6], 16)}/stock.jpeg",
                alt=query,
                photographer="Fixture",
                photographer_url=None,
                source="pexels",
                avg_color=f"#{digest[6:12]}",
            )
        ]


def _block(kind: str, name: str) -> ContentBlock | None:
    """A plausible block for one section kind. Copy quality is irrelevant here;
    what matters is that each block carries what its layouts need (item counts,
    image queries), so feasibility gates behave as they do on a real site."""
    if kind == "hero":
        return HeroBlock(
            headline=f"{name} welcomes you",
            subheadline="What we do, who it is for, and how to start.",
            image_query="people working together daylight",
        )
    if kind == "features":
        return FeaturesBlock(
            heading="Why families choose us",
            items=[
                FeatureItem(title=t, description="A sentence that explains the benefit plainly.")
                for t in ("Small groups", "Qualified staff", "Safe spaces")
            ],
        )
    if kind == "services":
        return ServicesBlock(
            heading="What we offer",
            items=[
                ServiceItem(title=t, description="Who it suits and what it includes.")
                for t in ("Morning programme", "Full day", "After school", "Holiday camp")
            ],
        )
    if kind == "about":
        return AboutBlock(
            heading=f"About {name}",
            body="How it started, what it believes and how it works today. " * 3,
            image_query="team at work natural light",
        )
    if kind == "testimonials":
        return TestimonialsBlock(
            items=[
                TestimonialItem(quote="Clear, kind and organised from day one.", author="A. Rahman"),
                TestimonialItem(quote="We found exactly what we needed here.", author="J. Lee"),
            ],
        )
    if kind == "gallery":
        return GalleryBlock(
            items=[GalleryItem(image_query=f"activity scene {i}") for i in range(6)],
        )
    if kind == "menu":
        return MenuBlock(
            categories=[
                MenuCategory(
                    name="Mains",
                    items=[MenuItem(name=f"Dish {i}", price=f"RM{12 + i}") for i in range(4)],
                )
            ],
        )
    if kind == "process":
        return ProcessBlock(
            steps=[
                ProcessStep(title=t, description="What happens at this step.")
                for t in ("Get in touch", "Visit", "Start")
            ],
        )
    if kind == "team":
        return TeamBlock(
            members=[TeamMember(name=n, role="Lead") for n in ("Aina", "Ben", "Chen")],
        )
    if kind == "faq":
        return FaqBlock(
            items=[FaqItem(question=f"Question {i}?", answer="A plain answer.") for i in range(3)],
        )
    if kind == "pricing":
        return PricingBlock(
            tiers=[PricingTier(name=t, price=p) for t, p in (("Basic", "RM100"), ("Plus", "RM200"))],
        )
    if kind == "cta":
        return CtaBlock(headline="Come and see for yourself", cta_label="Contact us")
    return None


def fixture_plan(name: str, industry: str) -> SitePlan:
    blocks = [
        block
        for kind in homepage_sections(industry, seed=name)
        if (block := _block(kind, name)) is not None
    ]
    # A contact page so CTAs resolve to a real destination, as on any real
    # site — without one every button is dropped as dead, and the heroes that
    # require a CTA become infeasible, which no real source would see.
    contact = PagePlan(
        page_type="contact",
        slug="contact",
        title="Contact",
        seo_title=f"Contact {name}",
        seo_description="How to reach us, where we are and when we are open to visitors.",
        blocks=[
            HeroBlock(headline=f"Visit {name}"),
            ContactBlock(email="hello@example.com", phone="+60 3-1234 5678"),
        ],
    )
    return SitePlan(
        site_name=name,
        industry_category=industry,
        pages=[
            PagePlan(
                page_type="home",
                slug="",
                title="Home",
                is_homepage=True,
                seo_title=f"{name} — Home",
                seo_description="A description long enough to satisfy the SEO audit rules.",
                blocks=blocks,
            ),
            contact,
        ],
    )


def fixture_brand(name: str, logo: str | None, industry: str) -> BrandIdentity:
    # The router's precedence for a brand nobody configured, with detection off.
    mood = resolve_mood(override=None, brand_mood=None, detected=None, industry=industry)
    return BrandIdentity(
        name=name,
        mood=mood,
        industry=industry,
        extracted_palette=[logo] if logo else [],
    )


@contextmanager
def offline_generation(
    *, schemes: bool | None, history: bool, fullbleed: bool | None = None
) -> Iterator[None]:
    """Settings for a hermetic run, restored afterwards — the conftest pins,
    gathered for a script."""
    from app.services import db as db_module
    from app.services import diversity as diversity_module

    pinned = {
        "design_brain_enabled": False,
        "design_language_enabled": False,
        "photo_sampling_enabled": False,
        "ocr_text_detection_enabled": False,
        "graphic_detection_enabled": False,
        "diversity_engine_enabled": history,
    }
    if schemes is not None:
        pinned["design_schemes_enabled"] = schemes
    if fullbleed is not None:
        pinned["hero_fullbleed_all_pages"] = fullbleed
    saved = {key: getattr(settings, key) for key in pinned}
    saved_db = (db_module.DB_PATH, db_module._BOOTSTRAPPED, diversity_module._SCHEMA_READY)
    saved_client = media.get_pexels_client
    with tempfile.TemporaryDirectory(prefix="design-diversity-") as scratch:
        for key, value in pinned.items():
            setattr(settings, key, value)
        db_module.DB_PATH = Path(scratch) / "history.db"
        db_module._BOOTSTRAPPED = False
        diversity_module._SCHEMA_READY = False
        media.get_pexels_client = _FakeStock
        try:
            yield
        finally:
            for key, value in saved.items():
                setattr(settings, key, value)
            db_module.DB_PATH, db_module._BOOTSTRAPPED, diversity_module._SCHEMA_READY = saved_db
            media.get_pexels_client = saved_client


def source_photos(name: str) -> list[ImageMetadata]:
    """Six measured, content-grade photographs — what a typical source brings.
    `--no-photos` drops them to model a business with none."""
    slug = name.lower().replace(" ", "-")
    return [
        ImageMetadata(
            url=f"https://{slug}.example/photo-{i}.jpg",
            alt=f"{name} photo {i}",
            role="content",
            width=1600,
            height=1067,
        )
        for i in range(6)
    ]


def persona(index: int) -> BrandPersonality:
    values = PERSONAS[index % len(PERSONAS)]
    return BrandPersonality(**{
        axis: AxisReading(value=value, evidence="fixture") for axis, value in values.items()
    })


async def fingerprints_for(
    industry: str,
    brands: tuple[tuple[str, str | None], ...] = BRANDS,
    *,
    personas: bool = False,
    photos: bool = True,
) -> list[tuple[str, DesignFingerprint]]:
    """One fingerprint per brand, built in order (order matters with history on).

    With ``personas`` each brand's theme comes from the design direction with a
    brand brief, as /generate/with-pages builds it (minus the model's pick);
    without, plan_to_site resolves it with no brief, as a bare caller does."""
    out: list[tuple[str, DesignFingerprint]] = []
    for index, (name, logo) in enumerate(brands):
        brand = fixture_brand(name, logo, industry)
        images = source_photos(name) if photos else []
        theme = None
        if personas:
            theme = (
                await resolve_direction(
                    brand_name=name,
                    mood=brand.mood,
                    industry=industry,
                    logo_hex=logo,
                    personality=persona(index),
                    content=ContentProfile.of_images(images, prose_chars=3000),
                    use_model=False,
                )
            ).theme
        site = await plan_to_site(
            fixture_plan(name, industry),
            brand=brand,
            theme=theme,
            scraped_images=[m.url for m in images],
            scraped_metadata=images,
        )
        out.append((name, fingerprint_of(site)))
    return out


@dataclass(frozen=True)
class DiversityReport:
    industry: str
    sites: int
    palettes: int  # distinct primary hue buckets
    fonts: int
    schemes: int
    heroes: int
    sequences: int  # distinct homepage section sequences
    headers: int
    footers: int
    mean_similarity: float  # mean pairwise look-alike score, 0..1
    max_similarity: float


def report(industry: str, prints: list[tuple[str, DesignFingerprint]]) -> DiversityReport:
    fps = [fp for _, fp in prints]
    pairs = [similarity(a, b) for a, b in combinations(fps, 2)] or [1.0]
    return DiversityReport(
        industry=industry,
        sites=len(fps),
        palettes=len({fp.hue for fp in fps}),
        fonts=len({fp.heading_font for fp in fps}),
        schemes=len({fp.scheme for fp in fps}),
        heroes=len({fp.hero for fp in fps}),
        sequences=len({fp.sections for fp in fps}),
        headers=len({fp.header for fp in fps}),
        footers=len({fp.footer for fp in fps}),
        mean_similarity=round(mean(pairs), 3),
        max_similarity=round(max(pairs), 3),
    )


async def measure(
    industries: tuple[str, ...],
    *,
    schemes: bool | None,
    history: bool,
    personas: bool = False,
    photos: bool = True,
    fullbleed: bool | None = None,
) -> list[tuple[DiversityReport, list[tuple[str, DesignFingerprint]]]]:
    results = []
    for industry in industries:
        # A fresh history per industry: a batch is one shop's afternoon.
        with offline_generation(schemes=schemes, history=history, fullbleed=fullbleed):
            prints = await fingerprints_for(industry, personas=personas, photos=photos)
        results.append((report(industry, prints), prints))
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("industries", nargs="*", default=list(DEFAULT_INDUSTRIES))
    parser.add_argument("--schemes", choices=("default", "on", "off"), default="default")
    parser.add_argument("--history", action="store_true", help="run as one batch against an isolated history DB")
    parser.add_argument("--personas", action="store_true", help="give each brand a distinct brand brief")
    parser.add_argument("--no-photos", action="store_true", help="model sources with no photographs")
    parser.add_argument(
        "--fullbleed", choices=("default", "on", "off"), default="default",
        help="HERO_FULLBLEED_ALL_PAGES for the run (the operator's .env sets off)",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="print every site's fingerprint")
    args = parser.parse_args()
    schemes = {"default": None, "on": True, "off": False}[args.schemes]

    import logging

    logging.disable(logging.WARNING)
    results = asyncio.run(
        measure(
            tuple(args.industries),
            schemes=schemes,
            history=args.history,
            personas=args.personas,
            photos=not args.no_photos,
            fullbleed={"default": None, "on": True, "off": False}[args.fullbleed],
        )
    )

    header = f"{'industry':24} sites pal font schm hero seq  hdr ftr  mean-sim max-sim"
    print(
        f"schemes={args.schemes} history={'on' if args.history else 'off'} "
        f"personas={'on' if args.personas else 'off'} photos={'off' if args.no_photos else 'on'}"
    )
    print(header)
    for rep, prints in results:
        print(
            f"{rep.industry:24} {rep.sites:5} {rep.palettes:3} {rep.fonts:4} {rep.schemes:4} "
            f"{rep.heroes:4} {rep.sequences:4} {rep.headers:4} {rep.footers:3}  "
            f"{rep.mean_similarity:8.3f} {rep.max_similarity:7.3f}"
        )
        if args.verbose:
            for name, fp in prints:
                print(
                    f"    {name:22} hue={fp.hue:>2} font={fp.heading_font:16} scheme={fp.scheme or '-':20} "
                    f"hero={fp.hero:24} {fp.header}/{fp.footer} r={fp.radius} div={fp.divider} "
                    f"inks={fp.title_inks} bands={','.join(fp.bands)}"
                )
                print(f"    {'':22} sections={','.join(fp.sections)}")


if __name__ == "__main__":
    main()
