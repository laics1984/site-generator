"""Standalone smoke test for template selection + filling (no LLM/CMS/httpx).

Run from anywhere:  python3 scripts/smoke_template_filler.py
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models.content_blocks import (
    AboutBlock, ContactBlock, CtaBlock, FaqBlock, FaqItem, FeatureItem,
    FeaturesBlock, GalleryBlock, GalleryItem, HeroBlock, MenuBlock,
    MenuCategory, MenuItem, PricingBlock, PricingTier, ProcessBlock,
    ProcessStep, ServiceItem, ServicesBlock, TeamBlock, TeamMember,
    TestimonialItem, TestimonialsBlock,
)
from app.services.section_content import block_to_section, is_feasible
from app.services.template_filler import fill_template, templates_for_type

KNOWN = {"text", "container", "section", "2Col", "3Col", "image", "video", "link",
         "menu", "contactForm", "paymentForm", "__body", "__header", "__footer"}


async def resolve_image(q: str):
    # (url, avg_color) — mid-tone avg so the adaptive overlay computes a real alpha.
    return f"https://images.example/{q.replace(' ', '-')}.jpg", "#5a5a5a"


THEME = {"primary": "#2563eb", "secondary": "#0f172a"}


def invariants(node, errs, depth=0):
    s = node.styles or {}
    if s.get("display") == "grid":
        errs.append(f"grid:{node.name}")
    if "gridTemplateColumns" in s:
        errs.append(f"gtc:{node.name}")
    if node.type not in KNOWN:
        errs.append(f"type:{node.type}")
    # NB: child count of $repeat grids legitimately varies (= item count); the
    # builder renders a 2Col/3Col as a fixed track count regardless. So child
    # count is NOT an invariant — only grid-leak + known types are.
    if isinstance(node.content, list):
        for c in node.content:
            invariants(c, errs, depth + 1)


def count(node):
    n = 1
    if isinstance(node.content, list):
        for c in node.content:
            n += count(c)
    return n


async def main():
    factories = {"contactFormDefault": lambda: {}}
    cases = [
        ("hero split+img", HeroBlock(headline="Empowering lives", subheadline="sub",
                                     image_query="music therapy", layout="split",
                                     secondary_cta_label="Read more", secondary_cta_href="#x")),
        ("hero bg+img", HeroBlock(headline="Bold", image_query="concert", layout="background")),
        ("hero no-img", HeroBlock(headline="Clean", subheadline="s")),
        ("features x1", FeaturesBlock(heading="Why us", items=[
            FeatureItem(title="A", description="da")])),
        ("features x2", FeaturesBlock(heading="Why us", items=[
            FeatureItem(title="A", description="da"), FeatureItem(title="B", description="db")])),
        ("features x3", FeaturesBlock(heading="Why us", items=[
            FeatureItem(title="A", description="da"), FeatureItem(title="B", description="db"),
            FeatureItem(title="C", description="dc")])),
        ("about img", AboutBlock(heading="About", body="story", image_query="office team")),
        ("about no-img", AboutBlock(heading="About", body="story")),
        ("services x3", ServicesBlock(heading="Services", items=[
            ServiceItem(title="s1", description="d1"), ServiceItem(title="s2", description="d2"),
            ServiceItem(title="s3", description="d3")])),
        ("testimonials", TestimonialsBlock(heading="Praise", items=[
            TestimonialItem(quote="great", author="Jane", role="CEO")])),
        ("cta", CtaBlock(headline="Ready?", cta_label="Go", cta_href="#c", subheadline="now")),
        ("cta+bg", CtaBlock(headline="Ready?", cta_label="Go", cta_href="#c",
                            background_query="city skyline at dusk")),
        ("faq", FaqBlock(heading="FAQ", items=[FaqItem(question="q?", answer="a.")])),
        ("contact", ContactBlock(heading="Contact", email="a@b.com", phone="+60 12")),
        ("team x2", TeamBlock(heading="Team", members=[
            TeamMember(name="A", role="Founder", photo_query="portrait"),
            TeamMember(name="B", role="Design")])),
        ("team x3", TeamBlock(heading="Team", members=[
            TeamMember(name="A", role="Founder"), TeamMember(name="B", role="Design"),
            TeamMember(name="C", role="Eng")])),
        ("gallery x4", GalleryBlock(heading="Work", items=[
            GalleryItem(image_query="a"), GalleryItem(image_query="b"),
            GalleryItem(image_query="c"), GalleryItem(image_query="d")])),
        ("process x3", ProcessBlock(heading="How", steps=[
            ProcessStep(title="Discover", description="d"),
            ProcessStep(title="Design", description="d"),
            ProcessStep(title="Launch", description="d")])),
        ("menu", MenuBlock(heading="Menu", categories=[
            MenuCategory(name="Starters", items=[
                MenuItem(name="Salad", description="fresh", price="$9"),
                MenuItem(name="Soup", price="$7")]),
            MenuCategory(name="Mains", items=[MenuItem(name="Salmon", price="$24")])])),
        ("pricing x2", PricingBlock(heading="Plans", tiers=[
            PricingTier(name="Starter", price="$0", features=["1 project"]),
            PricingTier(name="Pro", price="$29/mo", features=["Unlimited", "Support"], highlighted=True)])),
    ]
    print(f"{'case':16} {'selected template':26} {'nodes':>5}  invariants")
    print("-" * 70)
    ok = True
    for label, block in cases:
        res = block_to_section(block)
        if not res:
            print(f"{label:16} NO-TEMPLATE"); ok = False; continue
        template, content = res
        el = await fill_template(template, content, resolve_image=resolve_image,
                                 content_factories=factories, theme=THEME)
        errs = []
        invariants(el, errs)
        status = "pass" if not errs else ",".join(errs)
        if errs:
            ok = False
        print(f"{label:16} {template['id']:26} {count(el):>5}  {status}")

    # Selection sanity: feasibility filter excludes background hero without image
    hb_no_img = HeroBlock(headline="x")
    from app.services.section_content import _hero_content
    c = _hero_content(hb_no_img)
    bg = next(t for t in templates_for_type("hero") if t["id"] == "hero-background-bold")
    print("-" * 70)
    feas_ok = not is_feasible(bg, c)
    print("feasibility: hero-background-bold without image feasible? ->",
          is_feasible(bg, c), "(expected False)")

    # $gridFit: column count adapts to item count
    def grid_type(el):
        if el.type in ("2Col", "3Col"):
            return el.type
        if isinstance(el.content, list):
            for c2 in el.content:
                t = grid_type(c2)
                if t:
                    return t
        return None

    async def team_grid_type(n):
        tpl, content = block_to_section(TeamBlock(
            heading="T", members=[TeamMember(name=str(i), role="r") for i in range(n)]))
        el = await fill_template(tpl, content, resolve_image=resolve_image,
                                 content_factories=factories, theme=THEME)
        return grid_type(el)

    g2, g3 = await team_grid_type(2), await team_grid_type(3)
    grid_ok = g2 == "2Col" and g3 == "3Col"
    print(f"$gridFit: team x2 -> {g2} (expect 2Col), team x3 -> {g3} (expect 3Col)")

    # Brand-tinted, luminance-adaptive overlay on a photo background (cta-background)
    tpl, content = block_to_section(
        CtaBlock(headline="X", cta_label="Go", cta_href="#c", background_query="warm cafe"))
    el = await fill_template(tpl, content, resolve_image=resolve_image,
                             content_factories=factories, theme=THEME)
    bg = el.styles.get("backgroundImage", "")
    overlay_ok = ("rgba(15,23,42," in bg and "rgba(37,99,235," in bg
                  and "url('https://images.example/warm-cafe.jpg')" in bg)
    print(f"overlay: cta-bg brand-tinted photo overlay? -> {overlay_ok}")
    print("   ", bg[:104])
    print("ALL PASS" if ok and feas_ok and grid_ok and overlay_ok else "FAILURES PRESENT")


asyncio.run(main())
