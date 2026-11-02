"""What a site asks its visitor to do — the header button's words AND the form
they land on, from one answer.

The header CTA always routes to the contact destination, and that destination
renders one enquiry form. They used to be decided in two places that never
met: the button's label came from the creative direction (or the industry),
while the form carried no copy at all (`contactFormDefault = {}`), so every
site showed the renderer's agency defaults — "Project brief", "Tell us what
you need help building". A kindergarten's **Request a Tour** landed parents on
a project-brief form; a SaaS **Request a Demo** asked for no company.

One intent now names both. `intent_for` is the single precedence the header
and the form share — the direction's first intent the contact page can fulfil,
then the industry's — so the button and the form cannot disagree.

Form copy is written for the two industry FAMILIES (`FORM_FAMILIES`): every
childcare archetype and every SaaS archetype, fintech included, inherit it
through the industry; an archetype never decides whether a family gets it.
Every other industry keeps the empty form it always had. The copy is
deliberately fact-free — it asks, it never states — so there is nothing in it
to ground.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Industries whose enquiry form is written for its visitor. Gated on the
# INDUSTRY so every archetype inside it inherits the form.
FORM_FAMILIES: frozenset[str] = frozenset({"childcare", "saas"})


@dataclass(frozen=True)
class ContactIntent:
    """One thing a visitor can ask for through the contact page."""

    label: str
    # Message field: what to tell us, and the prompt inside it.
    message_label: str | None = None
    message_placeholder: str | None = None
    submit: str | None = None
    success_heading: str | None = None
    success_body: str | None = None
    # Field modes beyond the renderer's defaults (company is off by default,
    # phone optional).
    modes: dict[str, str] = field(default_factory=dict)
    company_label: str | None = None

    @property
    def has_form_copy(self) -> bool:
        return self.submit is not None


_VISIT = dict(
    message_label="Your child and any questions",
    message_placeholder=(
        "Your child's age, when you'd like them to start, "
        "and the days that suit you for a visit."
    ),
    success_heading="Thank you — we'll be in touch",
    success_body="We'll contact you to arrange a time for your visit.",
    modes={"phone": "required"},
)

# The intents a site's CONTACT PAGE fulfils. An intent that needs a
# destination of its own (a WhatsApp chat, a self-serve trial signup) is not
# here, so it is skipped rather than promised: a label must match where it
# goes. The industry intents (`get-started`, `table`, …) are named outside the
# directions' vocabulary on purpose — a direction never reaches them, so adding
# one can never change which label a direction picks.
INTENTS: dict[str, ContactIntent] = {
    "visit": ContactIntent("Arrange a Visit", submit="Arrange a visit", **_VISIT),
    "tour": ContactIntent("Request a Tour", submit="Request a tour", **_VISIT),
    "trial-class": ContactIntent(
        "Request a Trial Class",
        message_label="Your child and the class",
        message_placeholder="Your child's age and the class you'd like them to try.",
        submit="Request a trial class",
        success_heading="Thank you — we'll be in touch",
        success_body="We'll contact you to arrange your child's trial class.",
        modes={"phone": "required"},
    ),
    "demo": ContactIntent(
        "Request a Demo",
        message_label="What would you like to see?",
        message_placeholder=(
            "Your team, what you use today, and what you'd like the demo to cover."
        ),
        submit="Request a demo",
        success_heading="Thanks — we'll be in touch",
        success_body="We'll contact you to schedule your demo.",
        modes={"company": "required"},
        company_label="Company",
    ),
    "consult": ContactIntent(
        "Request a Consultation",
        message_label="What would you like to discuss?",
        message_placeholder="A little about your organisation and what you need help with.",
        submit="Request a consultation",
        success_heading="Thanks — we'll be in touch",
        success_body="We'll contact you to arrange a consultation.",
        modes={"company": "optional"},
        company_label="Organisation",
    ),
    "project": ContactIntent("Start a Project"),
    "assessment": ContactIntent(
        "Request an Assessment",
        message_label="Tell us about your setup",
        message_placeholder="Your team size, the systems you run, and what isn't working.",
        submit="Request an assessment",
        success_heading="Thanks — we'll be in touch",
        success_body="We'll contact you to arrange the assessment.",
        modes={"company": "optional"},
        company_label="Company",
    ),
    "call": ContactIntent(
        "Talk to Us",
        message_label="How can we help?",
        message_placeholder="Tell us a little about what you need.",
        submit="Send",
        success_heading="Thanks for reaching out",
        success_body="We'll be in touch shortly.",
    ),
    "enquire": ContactIntent(
        "Enquire Now",
        message_label="Your message",
        message_placeholder="How can we help?",
        submit="Send enquiry",
        success_heading="Thanks for reaching out",
        success_body="We'll be in touch shortly.",
    ),
    # --- industry intents: what an industry asks for when no direction speaks ---
    "table": ContactIntent("Request a Table"),
    "get-started": ContactIntent(
        "Get Started",
        message_label="What would you like to get started with?",
        message_placeholder="Your team and what you'd like to set up first.",
        submit="Get started",
        success_heading="Thanks — we'll be in touch",
        success_body="We'll contact you to help you get started.",
        modes={"company": "optional"},
        company_label="Company",
    ),
    "products": ContactIntent("Enquire About Products"),
    "involve": ContactIntent("Get Involved"),
    "quote": ContactIntent("Get a Free Quote"),
    "appointment": ContactIntent("Book an Appointment"),
    "viewing": ContactIntent("Book a Viewing"),
    "get-in-touch": ContactIntent("Get in touch"),
}

# What an industry asks for when the site has no direction (or the direction
# names nothing the contact page fulfils). The header's old
# `_CTA_LABEL_BY_INDUSTRY`, as intents.
INDUSTRY_INTENT: dict[str, str] = {
    "restaurant": "table",
    "agency": "project",
    "saas": "get-started",
    "professional-services": "consult",
    "ecommerce": "products",
    "consultancy": "consult",
    "nonprofit": "involve",
    "childcare": "tour",
    "home-services": "quote",
    "health-wellness": "appointment",
    "real-estate": "viewing",
}
_FALLBACK_INTENT = "get-in-touch"


def direction_intent(cta_strategy: tuple[str, ...]) -> str | None:
    """A direction's first intent the contact page can fulfil."""
    return next((i for i in cta_strategy if i in INTENTS), None)


def intent_for(cta_strategy: tuple[str, ...] | None, industry: str | None) -> str:
    """THE precedence: the direction's intent, then the industry's, then a
    plain "get in touch". The header and the form both ask this."""
    return (
        (direction_intent(cta_strategy) if cta_strategy else None)
        or INDUSTRY_INTENT.get(industry or "")
        or _FALLBACK_INTENT
    )


def label_for(intent: str) -> str:
    return INTENTS[intent].label


def form_content(intent: str, industry: str | None) -> dict[str, Any]:
    """The contact form's content for this intent — empty (the renderer's own
    defaults, exactly as before) outside the form families or for an intent
    with no copy of its own.

    Written in the shape all three renderers read: per-field `label` /
    `placeholder` / `mode`, `submitLabel`, and the published site's
    `successHeading` / `successBody`."""
    spec = INTENTS[intent]
    if (industry or "") not in FORM_FAMILIES or not spec.has_form_copy:
        return {}
    fields: dict[str, dict[str, str]] = {
        "email": {"placeholder": "name@example.com"},
        "message": {
            "label": spec.message_label or "Your message",
            "placeholder": spec.message_placeholder or "How can we help?",
        },
    }
    for key, mode in spec.modes.items():
        fields.setdefault(key, {})["mode"] = mode
    if spec.company_label:
        fields.setdefault("company", {}).update(
            label=spec.company_label, placeholder="Company name"
        )
        fields["email"]["placeholder"] = "name@company.com"
    return {
        "fields": fields,
        "submitLabel": spec.submit,
        "successHeading": spec.success_heading,
        "successBody": spec.success_body,
    }
