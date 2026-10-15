"""Build deterministic visual-review fixtures through the real mapper.

No AI/network/CMS writes. These are design fixtures, not real businesses or
evidence of live model quality. Output is usable by the preview/public renderer.
Run from backend: .venv/bin/python scripts/check_color_themes.py /tmp/color-review.json
"""
import asyncio
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import settings
from app.models.brand import BrandIdentity, CreativePalette
from app.models.content_blocks import SitePlan, PagePlan, HeroBlock, AboutBlock, FeaturesBlock, FeatureItem, ServicesBlock, ServiceItem, CtaBlock, ContactBlock
from app.services import db
from app.services.menu_builder import build_layout_payload
from app.services.schema_builder import plan_to_site
from app.services.theme import apply_creative_palette, build_theme

FIXTURES = [
    ("childcare", "Little Acorns", "friendly", "light", ["#b45f46", "#71836a", "#c49b3b", "#faf4e9"], "A calm place to grow"),
    ("childcare", "Bright Harbour", "playful", "light", ["#2b6090", "#ed8778", "#d5ac4e", "#f7f5eb"], "Room for big little discoveries"),
    ("restaurant", "Ember Table", "luxury", "light", ["#973c2b", "#6c7654", "#d4a246", "#f4eadc"], "Good food, thoughtfully shared"),
    ("health-wellness", "Wellspace", "friendly", "light", ["#27686c", "#899d8b", "#bd8262", "#f4f6f2"], "Care you can understand"),
    ("professional-services", "Northfield", "editorial", "light", ["#1b354b", "#8e7964", "#b18d4c", "#f5f3ee"], "Clarity for your next decision"),
    ("saas", "Orbit Works", "modern", "dark", ["#8a7ee0", "#4d8f8b", "#dfad5b", "#1d2433"], "Make space for better work"),
]


async def build():
    settings.pexels_api_key = None
    settings.design_brain_enabled = False
    settings.design_language_enabled = False
    settings.photo_sampling_enabled = False
    settings.design_schemes_enabled = True
    results = []
    with tempfile.TemporaryDirectory() as directory:
        db.DB_PATH = Path(directory)/"review.db"
        db._BOOTSTRAPPED = False
        for industry, name, mood, scheme, palette, headline in FIXTURES:
            theme = apply_creative_palette(build_theme(palette[0], mood=mood, font_seed=name, industry=industry, color_scheme=scheme),
                                          CreativePalette(**dict(zip(("primary","secondary","accent","surface"),palette))))
            plan = SitePlan(site_name=name, industry_category=industry, pages=[
                PagePlan(slug="",title="Home",page_type="home",is_homepage=True, blocks=[
                    HeroBlock(headline=headline,subheadline="A visual review fixture for an intentional, readable website.",primary_cta_label="Get in touch",primary_cta_href="/contact"),
                    AboutBlock(eyebrow="Our approach",heading="Thoughtful by design",body="Clear information, considered details and a welcoming experience. This sample demonstrates the color system without making claims about a real business."),
                    FeaturesBlock(eyebrow="What matters",heading="Built around people",items=[FeatureItem(title=t,description="A clear supporting description, presented with comfortable contrast.") for t in ("Considered details","A clear next step","Room to explore")]),
                    ServicesBlock(heading="Explore the possibilities",items=[ServiceItem(title=t,description="Supporting content with a distinct, harmonious surface.") for t in ("A personal approach","Practical support","A simple beginning")]),
                    CtaBlock(headline="Let’s start a conversation",subheadline="Find out whether we are the right fit.",cta_label="Get in touch",cta_href="/contact"),
                ]),
                PagePlan(slug="contact",title="Contact",page_type="contact",blocks=[HeroBlock(headline="Get in touch"),ContactBlock(email="hello@example.test")]),
            ])
            site = await plan_to_site(plan, theme=theme, brand=BrandIdentity(name=name,mood=mood,industry=industry,extracted_palette=[palette[0]]))
            layout = build_layout_payload(site)
            results.append({"industry":industry, "site":site.model_dump(mode="json"),
                            "layout":{"header":layout.header,"footer":layout.footer,"menus":layout.menus}})
    return results


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv)>1 else "/tmp/fowable-color-review.json")
    fixtures = asyncio.run(build())
    target.write_text(json.dumps(fixtures,ensure_ascii=False))
    print(f"Built {len(fixtures)} review fixtures: {target}")
