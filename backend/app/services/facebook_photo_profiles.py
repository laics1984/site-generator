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
from typing import NamedTuple

from app.models.content_blocks import PhotoContext
from app.models.industry import IndustryCategory
from app.services.prompts import CHILDCARE_FACEBOOK_PHOTO_PROMPT


class Subject(NamedTuple):
    """One thing a caption can say a photo shows. `name` is the vocabulary
    (`FacebookPhotoReview.classification`, `ImageMetadata.category`); `label`
    is the site-chrome heading a gallery group of them takes — a taxonomy
    word, never a claim about the business; `phrases` are the whole-word
    caption cues the no-LLM fallback matches."""

    name: str
    label: str
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class PhotoProfile:
    # What a caption can say a photo shows: keyword hints for the planner and
    # the categories the understanding call may use, never assertions about
    # the pixels.
    subject_hints: tuple[Subject, ...] = ()
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


_CHILDCARE = PhotoProfile(
    subject_hints=(
        Subject("literacy", "Reading and stories", ("reading", "storytelling", "phonics", "literacy", "membaca")),
        Subject("numeracy", "Numbers and counting", ("counting", "numeracy", "mathematics", "mengira")),
        Subject("creative_activity", "Arts and crafts", ("painting", "drawing", "craft", "clay", "art activity")),
        Subject("stem_activity", "Science and discovery", ("experiment", "science", "building blocks", "stem")),
        Subject("sensory_play", "Sensory play", ("sensory", "play dough", "water play")),
        Subject("music_activity", "Music and movement", ("music", "singing", "rhythm")),
        Subject("outdoor_learning", "Outdoor learning", ("gardening", "outdoor learning", "nature exploration")),
        Subject("physical_activity", "Active play", ("balancing", "movement", "physical activity")),
        Subject("teacher_child_interaction", "Learning together", ("teacher helping", "teacher guiding", "teacher reading")),
        Subject("classroom_activity", "In the classroom", ("learning through play", "classroom activity", "hands on learning")),
        Subject("facilities", "Our spaces", ("classroom", "reading corner", "playground", "school entrance")),
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
)

# The rows below add vocabulary only: their hero rule stays the generic
# measured one, and menus, posters and codes stay in the pool for the placers
# that show them whole.
_RESTAURANT = PhotoProfile(
    subject_hints=(
        Subject("food", "From the kitchen", (
            "dish", "dishes", "plate", "dessert", "cake", "noodles", "rice", "burger",
            "pizza", "pasta", "coffee", "drinks", "signature", "freshly baked")),
        Subject("dining_room", "The dining room", (
            "dining area", "dining room", "interior", "seating", "ambience", "our cafe",
            "our restaurant", "outdoor seating")),
        Subject("kitchen", "Behind the scenes", ("kitchen", "chef", "cooking", "preparing")),
        Subject("event", "Events and catering", (
            "catering", "buffet", "party", "wedding", "private event", "celebration")),
        Subject("team_at_work", "Our team", ("our team", "our staff", "our crew")),
    ),
    setting_subjects=frozenset({"dining_room"}),
    activity_sections=("about", "services"),
)

_HEALTH = PhotoProfile(
    subject_hints=(
        Subject("premises", "Our centre", (
            "clinic", "treatment room", "reception", "waiting area", "our centre", "our center",
            "studio")),
        Subject("equipment", "Equipment", ("equipment", "machine", "device", "scanner")),
        Subject("treatment", "Treatments", (
            "treatment", "therapy", "massage", "physio", "physiotherapy", "consultation")),
        Subject("class", "Classes", ("yoga", "pilates", "class", "workout", "training session")),
        Subject("team_at_work", "Our team", ("our team", "our doctors", "our therapists", "our staff")),
    ),
    setting_subjects=frozenset({"premises", "equipment"}),
    activity_sections=("about", "services"),
)

_HOME_SERVICES = PhotoProfile(
    subject_hints=(
        Subject("work_in_progress", "On the job", (
            "installation", "installing", "repair", "repairing", "renovation", "in progress",
            "fixing", "servicing")),
        Subject("completed_project", "Completed projects", (
            "completed", "finished", "before and after", "handover", "project done")),
        Subject("team_at_work", "Our team", ("our team", "technician", "technicians", "our crew")),
        Subject("equipment", "Equipment", ("tools", "equipment", "our van", "our truck")),
    ),
    setting_subjects=frozenset({"equipment"}),
    activity_sections=("about", "services"),
)

# The default row: a vocabulary any business shares, so a Page of a kind with
# no row of its own still has its photos grouped by what its captions say.
_GENERIC = PhotoProfile(
    subject_hints=(
        Subject("products", "Products", ("product", "products", "new arrival", "collection", "in stock")),
        Subject("services", "Our work", ("service", "services", "project", "appointment", "session")),
        Subject("premises", "Visit us", (
            "our shop", "our store", "our office", "our outlet", "showroom", "our studio")),
        Subject("event", "Events", ("event", "workshop", "launch", "grand opening", "celebration")),
        Subject("customers", "Customers", ("customer", "customers", "client", "clients")),
    ),
    setting_subjects=frozenset({"premises"}),
)

PROFILES: dict[PhotoContext, PhotoProfile] = {
    "facebook": _GENERIC,
    "facebook_childcare": _CHILDCARE,
    "facebook_restaurant": _RESTAURANT,
    "facebook_health": _HEALTH,
    "facebook_home_services": _HOME_SERVICES,
}

# The industries with a row of their own; every other one reads the default.
_CONTEXT_BY_INDUSTRY: dict[IndustryCategory, PhotoContext] = {
    "childcare": "facebook_childcare",
    "restaurant": "facebook_restaurant",
    "health-wellness": "facebook_health",
    "home-services": "facebook_home_services",
}


def context_for(industry: IndustryCategory) -> PhotoContext:
    return _CONTEXT_BY_INDUSTRY.get(industry, "facebook")


def subject_label(profile: PhotoProfile, name: str) -> str | None:
    """The heading a group of this subject takes, or None for an unknown one."""
    return next((subject.label for subject in profile.subject_hints if subject.name == name), None)


def profile_for(context: str | None) -> PhotoProfile | None:
    """The profile a photo was read under; None for any non-Facebook image."""
    return PROFILES.get(context) if context else None  # type: ignore[arg-type]

