"""How a Facebook Page's photos are judged, per industry — data, not code.

Every Facebook Page's photos go through one measured pass
(`facebook_photo_metadata`: decoded size, fingerprints, OCR/QR, flat-graphic
screen) and one selection (`facebook_photo_selection`). What legitimately
differs between a kindergarten and a caterer is vocabulary and policy, so it
lives here as rows. Adding an industry is one row plus one `PhotoContext`
value — a drift test pins the two together.

The generic row is deliberately thin. It removes duplicates and judges a hero
on measurements alone; menus, posters and QR codes stay in the pool, because
`menu_assets`, `legible_images` and `image_match.hides_legible_content` already
decide where a picture with words in it may go, and a second gate here would
be a second answer to that question.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.content_blocks import PhotoContext
from app.models.industry import IndustryCategory
from app.services.prompts import CHILDCARE_FACEBOOK_PHOTO_PROMPT


@dataclass(frozen=True)
class PhotoProfile:
    # (subject, caption phrases) — keyword hints about what a caption says a
    # photo shows. Hints for the planner, never assertions about the pixels.
    subject_hints: tuple[tuple[str, tuple[str, ...]], ...] = ()
    # Subjects that describe a PLACE rather than an activity: they suit a
    # facilities section but never qualify a hero on their own.
    setting_subjects: frozenset[str] = frozenset()
    # Sections an activity-captioned photo suits, beyond the gallery.
    activity_sections: tuple[str, ...] = ()
    # Caption words that bar a hero whatever the pixels say.
    hero_exclusions: tuple[str, ...] = (
        "poster", "flyer", "price list", "timetable", "announcement", "promotion",
        "menu",
    )
    # A hero needs a caption naming a suitable activity, not only the right
    # pixels. Childcare's rule: a photo of children is a hero only when the
    # Page itself says what they are doing.
    hero_requires_caption: bool = False
    # Withdraw a photo with words, a code or a logo in it from the pool
    # entirely, instead of leaving it to the slot gates.
    strip_legible: bool = False
    # The opt-in vision mode's reviewer prompt. None: this profile is judged
    # by measurement only, whatever FACEBOOK_PHOTO_REVIEW_MODE says.
    vision_prompt: str | None = None
    # Sections the selection report plans photos for.
    plan_sections: tuple[str, ...] = ("hero", "about", "gallery", "cta")


_CHILDCARE = PhotoProfile(
    subject_hints=(
        ("literacy", ("reading", "storytelling", "phonics", "literacy", "membaca")),
        ("numeracy", ("counting", "numeracy", "mathematics", "mengira")),
        ("creative_activity", ("painting", "drawing", "craft", "clay", "art activity")),
        ("stem_activity", ("experiment", "science", "building blocks", "stem")),
        ("sensory_play", ("sensory", "play dough", "water play")),
        ("music_activity", ("music", "singing", "rhythm")),
        ("outdoor_learning", ("gardening", "outdoor learning", "nature exploration")),
        ("physical_activity", ("balancing", "movement", "physical activity")),
        ("teacher_child_interaction", ("teacher helping", "teacher guiding", "teacher reading")),
        ("classroom_activity", ("learning through play", "classroom activity", "hands on learning")),
        ("facilities", ("classroom", "reading corner", "playground", "school entrance")),
    ),
    setting_subjects=frozenset({"facilities"}),
    activity_sections=("about", "programmes", "learning_approach", "cta"),
    hero_exclusions=(
        "poster", "flyer", "price list", "timetable", "announcement", "registration fee",
        "graduation", "class photo", "group photo", "sports day", "stage performance",
    ),
    hero_requires_caption=True,
    strip_legible=True,
    vision_prompt=CHILDCARE_FACEBOOK_PHOTO_PROMPT,
    plan_sections=(
        "hero", "about", "programmes", "learning_approach", "facilities",
        "teachers", "gallery", "testimonials", "cta", "contact", "faq", "footer",
    ),
)

PROFILES: dict[PhotoContext, PhotoProfile] = {
    "facebook": PhotoProfile(),
    "facebook_childcare": _CHILDCARE,
}

# The industries with a row of their own; every other one reads the default.
_CONTEXT_BY_INDUSTRY: dict[IndustryCategory, PhotoContext] = {
    "childcare": "facebook_childcare",
}


def context_for(industry: IndustryCategory) -> PhotoContext:
    return _CONTEXT_BY_INDUSTRY.get(industry, "facebook")


def profile_for(context: str | None) -> PhotoProfile | None:
    """The profile a photo was read under; None for any non-Facebook image."""
    return PROFILES.get(context) if context else None  # type: ignore[arg-type]

