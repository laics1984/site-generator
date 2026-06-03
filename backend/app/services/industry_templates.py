"""
Industry → page set registry.

Each template lists:
- core_pages: always generated (home, about, contact, privacy, terms)
- suggested_pages: pre-checked in the UI picker (industry-specific essentials)
- optional_pages: unchecked add-ons the user can opt in to (blog, FAQ, etc.)

Section recipes (per page) are the designer's structure — the LLM writes copy
for those sections, never invents the structure. This guarantees every site
has the rhythm a real designer would lay out.

If a user pastes/scrapes content that obviously doesn't match the industry,
the LLM can still adapt the copy intelligently — the structure stays.
"""

from __future__ import annotations

from app.models.industry import IndustryCategory, IndustryTemplate, PageScaffold


# --- common page recipes used across industries ---------------------------------


def _core_home(extra_sections: list[str] | None = None) -> PageScaffold:
    sections = ["hero", "features", "testimonials", "cta"]
    if extra_sections:
        # insert extras between testimonials and cta for visual rhythm
        sections = ["hero", "features", *extra_sections, "testimonials", "cta"]
    return PageScaffold(
        page_type="home",
        slug="",
        title="Home",
        is_homepage=True,
        sections=sections,  # type: ignore[arg-type]
        rationale="The homepage — first impression, value proposition, social proof, primary CTA.",
    )


def _about_page() -> PageScaffold:
    return PageScaffold(
        page_type="about",
        slug="about",
        title="About",
        sections=["hero", "about", "team", "cta"],
        rationale="Builds trust: founder story, team, mission. Visitors who reach About are seriously considering you.",
    )


def _contact_page() -> PageScaffold:
    return PageScaffold(
        page_type="contact",
        slug="contact",
        title="Contact",
        sections=["hero", "contact", "faq"],
        rationale="Conversion path — make it effortless to reach you. Form + the top 3 FAQs to remove blockers.",
    )


def _privacy_page() -> PageScaffold:
    return PageScaffold(
        page_type="privacy",
        slug="privacy",
        title="Privacy Policy",
        sections=[],
        is_legal=True,
        rationale="Required for GDPR / common compliance. Boilerplate template; have a lawyer review before publishing.",
    )


def _terms_page() -> PageScaffold:
    return PageScaffold(
        page_type="terms",
        slug="terms",
        title="Terms of Service",
        sections=[],
        is_legal=True,
        rationale="Standard terms of service boilerplate. Have a lawyer review before publishing.",
    )


def _services_page() -> PageScaffold:
    return PageScaffold(
        page_type="services",
        slug="services",
        title="Services",
        sections=["hero", "services", "process", "testimonials", "cta"],
        rationale="Detailed services list with process — buyers comparing options need depth, not just a homepage tile.",
    )


def _pricing_page() -> PageScaffold:
    return PageScaffold(
        page_type="pricing",
        slug="pricing",
        title="Pricing",
        sections=["hero", "pricing", "features", "faq", "cta"],
        rationale="Transparency converts. Tier comparison + feature matrix + FAQ kills pricing objections.",
    )


def _testimonials_page() -> PageScaffold:
    return PageScaffold(
        page_type="testimonials",
        slug="testimonials",
        title="Testimonials",
        sections=["hero", "testimonials", "cta"],
        rationale="Concentrated social proof. High-trust businesses (agencies, consultants) link to this from emails.",
    )


def _work_page() -> PageScaffold:
    return PageScaffold(
        page_type="work",
        slug="work",
        title="Work",
        sections=["hero", "gallery", "testimonials", "cta"],
        rationale="Portfolio / case studies — agencies, freelancers, and studios are bought on proof of work.",
    )


def _team_page() -> PageScaffold:
    return PageScaffold(
        page_type="team",
        slug="team",
        title="Team",
        sections=["hero", "team", "cta"],
        rationale="People-centric businesses (consultancies, legal, dental) — putting faces to the brand drives bookings.",
    )


def _faq_page() -> PageScaffold:
    return PageScaffold(
        page_type="faq",
        slug="faq",
        title="FAQ",
        sections=["hero", "faq", "cta"],
        rationale="Pre-emptive objection handling. Reduces support load and pre-qualifies serious buyers.",
    )


def _blog_page() -> PageScaffold:
    return PageScaffold(
        page_type="blog",
        slug="blog",
        title="Blog",
        sections=["hero", "features"],  # placeholder — real blog needs CMS articlesList
        rationale="Content marketing. Placeholder structure; replace with the builder's articlesList block.",
    )


def _gallery_page() -> PageScaffold:
    return PageScaffold(
        page_type="gallery",
        slug="gallery",
        title="Gallery",
        sections=["hero", "gallery", "cta"],
        rationale="Visual brands (restaurants, salons, photographers) sell on imagery.",
    )


def _menu_page() -> PageScaffold:
    return PageScaffold(
        page_type="menu",
        slug="menu",
        title="Menu",
        sections=["hero", "menu", "gallery", "cta"],
        rationale="Restaurants — the #1 page after Home. Customers want to see what's on offer before visiting.",
    )


def _process_page() -> PageScaffold:
    return PageScaffold(
        page_type="process",
        slug="process",
        title="Process",
        sections=["hero", "process", "faq", "cta"],
        rationale="Demystifies how you work. Reduces buyer anxiety for high-ticket services.",
    )


def _thank_you_page() -> PageScaffold:
    return PageScaffold(
        page_type="thank-you",
        slug="thank-you",
        title="Thank You",
        sections=["hero", "cta"],
        rationale="Post-form-submission page. Pixel + analytics goal target + next-step CTA (book a call, follow on social).",
    )


# --- always-included core (any industry) ----------------------------------------


def _core_set(home: PageScaffold) -> list[PageScaffold]:
    return [home, _about_page(), _contact_page(), _privacy_page(), _terms_page()]


# --- per-industry templates -----------------------------------------------------


TEMPLATES: dict[IndustryCategory, IndustryTemplate] = {
    "restaurant": IndustryTemplate(
        industry="restaurant",
        label="Restaurant / Café",
        description="Hospitality businesses where menu, vibe, and reservations drive bookings.",
        core_pages=_core_set(_core_home(extra_sections=["gallery"])),
        suggested_pages=[_menu_page(), _gallery_page()],
        optional_pages=[_faq_page(), _testimonials_page()],
    ),
    "agency": IndustryTemplate(
        industry="agency",
        label="Agency / Studio",
        description="Creative or marketing agencies where work + process win pitches.",
        core_pages=_core_set(_core_home()),
        suggested_pages=[_services_page(), _work_page(), _process_page()],
        optional_pages=[_team_page(), _testimonials_page(), _blog_page(), _faq_page()],
    ),
    "saas": IndustryTemplate(
        industry="saas",
        label="SaaS / Software",
        description="Software products where pricing and feature depth drive sign-ups.",
        core_pages=_core_set(_core_home()),
        suggested_pages=[_pricing_page(), _faq_page()],
        optional_pages=[_testimonials_page(), _blog_page(), _team_page()],
    ),
    "professional-services": IndustryTemplate(
        industry="professional-services",
        label="Professional Services (legal, dental, medical)",
        description="Service businesses where trust and credentials drive bookings.",
        core_pages=_core_set(_core_home()),
        suggested_pages=[_services_page(), _team_page(), _faq_page(), _testimonials_page()],
        optional_pages=[_process_page(), _blog_page()],
    ),
    "ecommerce": IndustryTemplate(
        industry="ecommerce",
        label="E-commerce",
        description="Product-led businesses. (Storefront integration is out of scope — these pages set the brand around it.)",
        core_pages=_core_set(_core_home(extra_sections=["gallery"])),
        suggested_pages=[_gallery_page(), _faq_page()],
        optional_pages=[_blog_page(), _testimonials_page()],
    ),
    "consultancy": IndustryTemplate(
        industry="consultancy",
        label="Consultancy",
        description="Expertise-led businesses where case studies and team bios drive engagement.",
        core_pages=_core_set(_core_home()),
        suggested_pages=[_services_page(), _work_page(), _team_page()],
        optional_pages=[_process_page(), _testimonials_page(), _blog_page(), _faq_page()],
    ),
    "nonprofit": IndustryTemplate(
        industry="nonprofit",
        label="Non-profit / Charity",
        description="Mission-led organizations where impact stories and donation paths matter most.",
        core_pages=_core_set(_core_home()),
        suggested_pages=[_services_page(), _team_page(), _testimonials_page()],
        optional_pages=[_blog_page(), _faq_page(), _gallery_page()],
    ),
    "personal": IndustryTemplate(
        industry="personal",
        label="Personal / Portfolio",
        description="Solo professionals and creators where personality and work samples matter.",
        core_pages=_core_set(_core_home()),
        suggested_pages=[_work_page(), _testimonials_page()],
        optional_pages=[_blog_page(), _services_page()],
    ),
    "other": IndustryTemplate(
        industry="other",
        label="Other / General",
        description="Generic small-business template when nothing else fits.",
        core_pages=_core_set(_core_home()),
        suggested_pages=[_services_page(), _faq_page()],
        optional_pages=[_testimonials_page(), _blog_page(), _team_page(), _gallery_page()],
    ),
}


def get_template(industry: IndustryCategory | None) -> IndustryTemplate:
    """Return the template for an industry, falling back to 'other'."""
    return TEMPLATES.get(industry or "other", TEMPLATES["other"])


def all_industries_summary() -> list[dict]:
    """Lightweight list for UI dropdowns: [{id, label, description}, …]."""
    return [
        {
            "id": tpl.industry,
            "label": tpl.label,
            "description": tpl.description,
        }
        for tpl in TEMPLATES.values()
    ]
