"""
Boilerplate Privacy + Terms pages.

These are templated, not LLM-generated. Legal copy is the wrong place for AI
creativity — we want vetted clauses with the brand's identifiers filled in,
plus a clear "have a lawyer review" disclaimer at the top of the page.

The HTML structure mirrors the rest of the site (same theme, same chrome) so
visitors don't feel they've left the brand.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from app.models.brand import ThemeTokens
from app.models.builder_schema import (
    BodySchema,
    BuilderElement,
    BuilderElementContent,
    GeneratedPage,
    PageSeo,
)


def _uid() -> str:
    return str(uuid4())


def _text(
    inner: str,
    *,
    name: str = "Text",
    styles: dict[str, Any] | None = None,
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name=name,
        type="text",
        styles={"width": "100%", **(styles or {})},
        content=BuilderElementContent(innerText=inner),
    )


def _container(
    children: list[BuilderElement],
    *,
    name: str = "Container",
    styles: dict[str, Any] | None = None,
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name=name,
        type="container",
        styles={
            "display": "flex",
            "flexDirection": "column",
            "width": "100%",
            **(styles or {}),
        },
        content=children,
    )


def _section(
    children: list[BuilderElement],
    *,
    name: str,
    theme: ThemeTokens,
) -> BuilderElement:
    return BuilderElement(
        id=_uid(),
        name=name,
        # The builder has no "section" renderer — it falls through to an empty
        # default and renders nothing. Sections are plain full-width containers.
        type="container",
        styles={
            "display": "flex",
            "flexDirection": "column",
            "width": "100%",
            "paddingTop": "80px",
            "paddingBottom": "80px",
            "paddingLeft": "24px",
            "paddingRight": "24px",
            "alignItems": "center",
            "backgroundColor": theme.palette.background,
            "fontFamily": theme.typography.body_font,
        },
        content=[
            _container(
                children,
                name="Section content",
                styles={
                    "maxWidth": "800px",
                    "width": "100%",
                    "gap": "20px",
                },
            )
        ],
    )


def _h1(text: str, theme: ThemeTokens) -> BuilderElement:
    return _text(
        text,
        name="H1",
        styles={
            "fontFamily": theme.typography.heading_font,
            "fontSize": "40px",
            "fontWeight": 700,
            "color": theme.palette.secondary,
            "letterSpacing": "-0.01em",
            "margin": "0",
        },
    )


def _h2(text: str, theme: ThemeTokens) -> BuilderElement:
    return _text(
        text,
        name="H2",
        styles={
            "fontFamily": theme.typography.heading_font,
            "fontSize": "22px",
            "fontWeight": 700,
            "color": theme.palette.secondary,
            "margin": "32px 0 0 0",
        },
    )


def _p(text: str, theme: ThemeTokens) -> BuilderElement:
    return _text(
        text,
        name="P",
        styles={
            "fontFamily": theme.typography.body_font,
            "fontSize": "16px",
            "lineHeight": "1.7",
            "color": theme.palette.text,
            "margin": "0",
        },
    )


def _disclaimer_banner(theme: ThemeTokens) -> BuilderElement:
    """Yellow-tinted disclaimer above the body so the user can't miss it."""
    return _container(
        [
            _text(
                "Boilerplate — review before publishing",
                name="Banner heading",
                styles={
                    "fontFamily": theme.typography.body_font,
                    "fontSize": "13px",
                    "fontWeight": 700,
                    "letterSpacing": "0.04em",
                    "textTransform": "uppercase",
                    "color": "#854d0e",
                    "margin": "0",
                },
            ),
            _text(
                "This page was generated from a template. Have a lawyer review it for your "
                "jurisdiction, business model, and data handling practices before going live.",
                name="Banner body",
                styles={
                    "fontFamily": theme.typography.body_font,
                    "fontSize": "13px",
                    "lineHeight": "1.5",
                    "color": "#854d0e",
                    "margin": "4px 0 0 0",
                },
            ),
        ],
        name="Disclaimer banner",
        styles={
            "padding": "14px 18px",
            "backgroundColor": "#fef3c7",
            "border": "1px solid #fde68a",
            "borderRadius": "12px",
            "marginBottom": "16px",
        },
    )


# --- privacy --------------------------------------------------------------------


def _privacy_paragraphs(brand_name: str, contact_email: str, jurisdiction: str) -> list[tuple[str, str]]:
    """Returns (heading, body) pairs for the privacy policy body."""
    today = datetime.now().strftime("%B %d, %Y")
    return [
        (
            "",
            f"Effective date: {today}. This Privacy Policy describes how {brand_name} (\"we\", \"our\", or \"us\") collects, uses, and shares information about you when you use our website and services.",
        ),
        (
            "Information we collect",
            f"We collect information you provide directly to us — such as your name, email address, and any other details you share through contact forms or correspondence. We may also collect technical information automatically (IP address, browser type, pages visited) via cookies and similar technologies.",
        ),
        (
            "How we use information",
            f"We use the information we collect to operate, maintain, and improve our services; respond to enquiries; send transactional communications; and comply with legal obligations. We do not sell your personal information.",
        ),
        (
            "Cookies",
            f"We use cookies and similar tracking technologies to remember your preferences, understand site usage, and improve our offering. You can configure your browser to refuse cookies, though some site features may not function without them.",
        ),
        (
            "Sharing",
            f"We share information with service providers who help us operate our business (hosting, analytics, payment processing) under contractual obligations of confidentiality and security. We may disclose information if required by law or to protect our rights.",
        ),
        (
            "Your rights",
            f"Depending on your location, you may have rights to access, correct, delete, or port your personal information, and to object to certain processing. To exercise these rights, contact us at {contact_email}.",
        ),
        (
            "Data retention",
            f"We retain personal information for as long as necessary to provide our services and fulfil the purposes outlined in this policy, unless a longer retention period is required by law.",
        ),
        (
            "International transfers",
            f"Your information may be processed and stored in {jurisdiction} or other countries where we or our service providers operate. By using our services, you consent to such transfers.",
        ),
        (
            "Changes to this policy",
            f"We may update this Privacy Policy from time to time. We will post the revised version on this page and update the effective date above.",
        ),
        (
            "Contact",
            f"If you have any questions about this Privacy Policy, contact us at {contact_email}.",
        ),
    ]


def _terms_paragraphs(brand_name: str, contact_email: str, jurisdiction: str) -> list[tuple[str, str]]:
    today = datetime.now().strftime("%B %d, %Y")
    return [
        (
            "",
            f"Effective date: {today}. These Terms of Service govern your access to and use of {brand_name}'s website and services. By using our services, you agree to be bound by these Terms.",
        ),
        (
            "Use of the service",
            f"You agree to use {brand_name} only for lawful purposes and in accordance with these Terms. You will not use our services to violate any law, infringe any party's intellectual property, or distribute harmful or unsolicited content.",
        ),
        (
            "Intellectual property",
            f"All content, trademarks, and intellectual property on this site are owned by {brand_name} or its licensors. Nothing in these Terms grants you any right to use them without our prior written permission.",
        ),
        (
            "User content",
            f"If you submit content (e.g. enquiries, messages), you grant us a non-exclusive, worldwide licence to use that content for the purpose of responding to and serving you.",
        ),
        (
            "Disclaimers",
            f"Our services are provided \"as is\" without warranties of any kind, express or implied. We do not warrant that the service will be uninterrupted, error-free, or free of harmful components.",
        ),
        (
            "Limitation of liability",
            f"To the maximum extent permitted by law, {brand_name} shall not be liable for any indirect, incidental, special, consequential, or punitive damages arising from your use of the service.",
        ),
        (
            "Indemnification",
            f"You agree to indemnify and hold {brand_name} harmless from any claims, damages, or expenses arising from your breach of these Terms or your use of the service.",
        ),
        (
            "Termination",
            f"We may suspend or terminate your access to the service at any time, with or without cause, with or without notice.",
        ),
        (
            "Governing law",
            f"These Terms are governed by the laws of {jurisdiction}, without regard to its conflict-of-law principles. Any dispute will be resolved exclusively in the courts of that jurisdiction.",
        ),
        (
            "Changes",
            f"We may update these Terms from time to time. Continued use of the service after changes constitutes acceptance of the revised Terms.",
        ),
        (
            "Contact",
            f"If you have any questions about these Terms, contact us at {contact_email}.",
        ),
    ]


def _legal_body(
    title: str,
    paragraphs: list[tuple[str, str]],
    theme: ThemeTokens,
) -> list[BuilderElement]:
    children: list[BuilderElement] = [
        _disclaimer_banner(theme),
        _h1(title, theme),
    ]
    for heading, body in paragraphs:
        if heading:
            children.append(_h2(heading, theme))
        children.append(_p(body, theme))
    return children


def build_privacy_page(
    brand_name: str,
    theme: ThemeTokens,
    *,
    contact_email: str = "hello@example.com",
    jurisdiction: str = "your country / state",
) -> GeneratedPage:
    children = _legal_body(
        "Privacy Policy",
        _privacy_paragraphs(brand_name, contact_email, jurisdiction),
        theme,
    )
    section = _section(children, name="Privacy", theme=theme)
    return GeneratedPage(
        slug="privacy",
        title="Privacy Policy",
        description=f"{brand_name} privacy policy.",
        is_homepage=False,
        body_schema=BodySchema(elements=[section]),
        seo=PageSeo(
            title=f"Privacy Policy — {brand_name}",
            description=f"How {brand_name} collects, uses, and protects your information.",
            noindex=False,
        ),
    )


def build_terms_page(
    brand_name: str,
    theme: ThemeTokens,
    *,
    contact_email: str = "hello@example.com",
    jurisdiction: str = "your country / state",
) -> GeneratedPage:
    children = _legal_body(
        "Terms of Service",
        _terms_paragraphs(brand_name, contact_email, jurisdiction),
        theme,
    )
    section = _section(children, name="Terms", theme=theme)
    return GeneratedPage(
        slug="terms",
        title="Terms of Service",
        description=f"{brand_name} terms of service.",
        is_homepage=False,
        body_schema=BodySchema(elements=[section]),
        seo=PageSeo(
            title=f"Terms of Service — {brand_name}",
            description=f"The terms that govern use of {brand_name}.",
            noindex=False,
        ),
    )
