"""Evidence-based review of a Facebook childcare photo, not business facts."""

from typing import Literal

from pydantic import BaseModel, Field

_SCORE_WEIGHTS = {
    "relevance": .18, "authenticity": .16, "visual_quality": .14,
    "composition": .12, "website_usability": .14, "emotional_warmth": .08,
    "childcare_relevance": .08, "visual_cleanliness": .04,
    "crop_flexibility": .03, "brand_consistency": .03,
}

class PhotoScores(BaseModel):
    relevance: float = Field(default=0, ge=0, le=10)
    authenticity: float = Field(default=0, ge=0, le=10)
    visual_quality: float = Field(default=0, ge=0, le=10)
    composition: float = Field(default=0, ge=0, le=10)
    website_usability: float = Field(default=0, ge=0, le=10)
    emotional_warmth: float = Field(default=0, ge=0, le=10)
    childcare_relevance: float = Field(default=0, ge=0, le=10)
    visual_cleanliness: float = Field(default=0, ge=0, le=10)
    crop_flexibility: float = Field(default=0, ge=0, le=10)
    brand_consistency: float = Field(default=0, ge=0, le=10)

    @property
    def overall(self) -> float:
        return round(sum(getattr(self, name) * weight for name, weight in _SCORE_WEIGHTS.items()), 2)


class FacebookPhotoReview(BaseModel):
    method: Literal["vision", "metadata"] = "vision"
    pixel_checks_complete: bool = False
    caption_hero_context: bool = False
    classification: list[Literal[
        "teacher_child_interaction", "classroom_activity", "creative_activity",
        "literacy", "numeracy", "stem_activity", "sensory_play", "music_activity",
        "physical_activity", "outdoor_learning", "playground", "field_trip",
        "celebration", "cultural_activity", "meal_or_food", "classroom_environment",
        "reading_corner", "facilities", "school_exterior", "teacher_team",
        "parent_child", "child_portrait", "group_activity", "object_detail",
        "childrens_artwork", "learning_materials", "gallery_only", "unsuitable",
        # Every other `facebook_photo_profiles` row's vocabulary; a drift test
        # pins each profile subject to a value here.
        "food", "dining_room", "kitchen", "event", "team_at_work", "premises",
        "equipment", "treatment", "class", "work_in_progress", "completed_project",
        "products", "services", "customers",
    ]] = Field(default_factory=list, max_length=8)
    recommended_sections: list[Literal[
        "hero", "about", "programmes", "learning_approach", "facilities",
        "teachers", "gallery", "cta", "services",
    ]] = Field(default_factory=list, max_length=8)
    orientation: Literal["landscape", "portrait", "square"] = "square"
    candid: bool = False
    negative_space: bool = False
    crop_safe: bool = False
    hero_candidate: bool = False
    privacy_risk: bool = False
    watermark_or_logo: bool = False
    decision: Literal["accept", "reject", "backup"] = "backup"
    rejection_category: Literal[
        "poster_or_text_graphic", "low_quality", "irrelevant", "privacy_risk", "other"
    ] = "other"
    reason: str = Field(default="", max_length=300)
    activity: str = Field(default="", max_length=100)
    environment: str = Field(default="", max_length=100)
    lighting: str = Field(default="", max_length=100)
    palette: list[str] = Field(default_factory=list, max_length=6)
    photographic_style: str = Field(default="", max_length=100)
    scores: PhotoScores | None = Field(default_factory=PhotoScores)
