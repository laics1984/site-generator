"""Facebook photo review, selection and gaps, for every industry.

What differs per industry is a `facebook_photo_profiles` row; transport stays
in the readers.
"""

import logging
from collections import Counter

from app.config import settings
from app.models.content_blocks import ImageMetadata, SourceContent
from app.models.facebook import FacebookPage
from app.services.facebook_photo_metadata import orientation_of
from app.services.facebook_photo_profiles import PhotoProfile, profile_for
from app.services.image_urls import photo_identity

logger = logging.getLogger(__name__)


# Bits two thumbnail dHashes (`image_vision._thumbnail_hash`) may differ by and
# still be one picture. Measured on a real Page's photos: a resized, recompressed
# or re-exposed copy lands within 3 bits of its original; eight different shots
# of one activity in one frame — as alike as distinct photos get — no closer than
# 18. Six keeps a wide margin on both sides. A crop is not this question: it
# moves the hash grid itself (a 3% crop already costs up to 12 bits), and
# Facebook's crops are told apart by `photo_identity` before any pixel is read.
_NEAR_DUPLICATE_BITS = 6


def _similar_photo(meta: ImageMetadata, other: ImageMetadata) -> bool:
    """Same bytes, or the same picture re-encoded. Asked of the pixels alone:
    it used to require a shared caption classification too, which no
    uncaptioned photo and no non-childcare Page ever has — so near-duplicate
    detection was off for exactly the photos that needed it."""
    if meta.content_hash and meta.content_hash == other.content_hash:
        return True
    if not meta.facebook_photo_hash or not other.facebook_photo_hash:
        return False
    if orientation_of(meta) != orientation_of(other):
        return False
    try:
        distance = (int(meta.facebook_photo_hash, 16) ^ int(other.facebook_photo_hash, 16)).bit_count()
    except ValueError:
        return False
    return distance <= _NEAR_DUPLICATE_BITS


def photo_rejection_reason(meta: ImageMetadata | None, section: str) -> str | None:
    """Shared gate for preview selection, ref binding and resolver ranking."""
    profile = profile_for(meta.selection_context) if meta is not None else None
    if profile is None:
        return None
    if meta.width and meta.height and min(meta.width, meta.height) < 300:
        return "low_quality"
    # A Page's photos never say who is in them, whatever the industry, so none
    # may stand for a named person.
    if section in {"team", "testimonials", "avatar"}:
        return "photo has no authorized identity association"
    if profile.strip_legible:
        if meta.qr_payload:
            return "privacy_risk"
        if meta.ocr_has_text or meta.role in {"logo", "decoration"}:
            return "poster_or_text_graphic"
    review = meta.facebook_photo_review
    if review is None:
        return "unreviewed hero" if section == "hero" else None
    if review.privacy_risk:
        return "privacy_risk"
    if review.decision == "reject":
        return review.rejection_category
    if review.method == "metadata":
        return metadata_hero_rejection_reason(meta, profile) if section == "hero" else None
    if meta.vision_kind != "photo" or review.watermark_or_logo:
        return "poster_or_text_graphic"
    if section == "hero" and not (
        review.decision == "accept" and review.hero_candidate
        and review.orientation == "landscape" and review.negative_space
        and review.crop_safe and review.candid and not meta.vision_has_text
        and (meta.vision_people or 0) <= 8
        and review.scores is not None and review.scores.overall >= 7
    ):
        return "not a strong childcare hero"
    return None


def metadata_hero_rejection_reason(
    meta: ImageMetadata, profile: PhotoProfile, *, require_caption: bool = True,
) -> str | None:
    """Measured eligibility only: never claims to know faces/negative space.
    `require_caption=False` asks the measurements alone — whether a photo with
    no words of its own would qualify if someone could say what it shows."""
    review = meta.facebook_photo_review
    if review is None or not review.pixel_checks_complete:
        return "pixel checks incomplete"
    if meta.ocr_has_text is not False or meta.qr_payload:
        return "text or QR code, or OCR result unavailable"
    if meta.role in {"logo", "decoration"}:
        return "a graphic, not a photograph"
    if not meta.width or not meta.height:
        return "original dimensions unavailable"
    if max(meta.width, meta.height) < settings.hero_min_background_dim:
        return "insufficient hero resolution"
    if meta.width / meta.height < settings.hero_bg_min_aspect:
        return "not a suitable landscape shape"
    if require_caption and not review.caption_hero_context:
        return ("caption lacks suitable activity context" if profile.hero_requires_caption
                else "caption rules out a hero")
    return None


async def _vision_second_look(metadata: list[ImageMetadata], profile: PhotoProfile) -> None:
    """The opt-in vision step (decision D5), on top of the measured pass.

    A profile that needs a caption to call a photo a hero (childcare) can't
    use a strong photo the Page posted without words. When the reasoning model
    picked in the UI is a Claude model, the uncaptioned photos whose
    measurements alone would qualify — at most `FACEBOOK_VISION_MAX_IMAGES`,
    best first — are sent to it with the profile's own reviewer prompt. Off
    for a local reasoning choice, so the default read makes no vision call; a
    failed call keeps the measured review.
    """
    from app.services import llm_choice

    cap = settings.facebook_vision_max_images
    if not profile.vision_prompt or cap <= 0 or llm_choice.current().reasoning == llm_choice.LOCAL:
        return
    candidates = sorted((
        meta for meta in metadata
        if not meta.caption.strip() and meta.facebook_photo_review
        and meta.facebook_photo_review.method == "metadata"
        and metadata_hero_rejection_reason(meta, profile, require_caption=False) is None
    ), key=photo_rank, reverse=True)[:cap]
    if not candidates:
        return
    from app.services.image_vision import annotate_image_pool
    from app.services.llm import get_reasoning_llm

    measured = {meta.url: meta.facebook_photo_review for meta in candidates}
    for meta in candidates:
        meta.facebook_photo_review = None  # a stored review short-circuits the pass
    try:
        await annotate_image_pool(candidates, llm=get_reasoning_llm(), max_images=cap, enabled=True)
    except Exception:
        logger.exception("Facebook vision step failed; keeping the measured reviews")
    for meta in candidates:
        if meta.facebook_photo_review is None:
            meta.facebook_photo_review = measured[meta.url]


def photo_rank(meta: ImageMetadata) -> tuple:
    """The order photos are offered in: a photo the Page captioned first (its
    words are what lets the planner place it), then a vision score when one
    ran, then measured quality, then size. This decides which copy of a
    near-duplicate is kept and which 12 the planner is shown
    (`source_router.MAX_PROMPT_IMAGES`) — area alone used to choose both."""
    review = meta.facebook_photo_review
    return (
        bool(meta.caption.strip()),
        review.scores.overall if review and review.method == "vision" and review.scores else 0,
        meta.quality or 0,
        (meta.width or 0) * (meta.height or 0),
    )


def _provenance(meta: ImageMetadata) -> dict:
    """Where a photo came from, as the inventory reports it — read off the
    photo's own metadata, the one record of it."""
    return {
        "source_image_url": meta.url,
        "source_post_id": meta.source_post_id,
        "source_post_url": meta.source_post_url,
        "date": meta.published_at,
        "origin": meta.origin,
        "quality": meta.quality,
    }


async def review_photo_library(source: SourceContent, page: FacebookPage) -> dict:
    """Review, rank and de-duplicate the Page's photos, and report the result.

    The report is the read's photo inventory: every photo found, each kept or
    rejected with its reason and provenance. Defaults to CPU/caption
    assessment, retaining opt-in vision compatibility."""
    from app.services.image_vision import annotate_image_pool, vision_enabled

    photo_profile = next(
        (found for meta in source.image_metadata if (found := profile_for(meta.selection_context))),
        None,
    )
    if photo_profile is None:
        return {}

    unique: dict[str, ImageMetadata] = {}
    duplicates = []
    for meta in source.image_metadata:
        identity = photo_identity(meta.url) or meta.url
        previous = unique.get(identity)
        if previous is None:
            unique[identity] = meta
        elif (meta.width or 0) * (meta.height or 0) > (previous.width or 0) * (previous.height or 0):
            duplicates.append(previous)
            unique[identity] = meta
        else:
            duplicates.append(meta)
    source.image_metadata = list(unique.values())
    # A profile with no reviewer prompt is judged by measurement whatever the
    # mode says: the generic judge has no notion of a hero for this Page.
    method = settings.facebook_photo_review_mode if photo_profile.vision_prompt else "metadata"
    enabled = method == "metadata" or vision_enabled()
    try:
        if method == "metadata":
            from app.services.facebook_photo_metadata import assess_photo_metadata

            await assess_photo_metadata(source.image_metadata)
            await _vision_second_look(source.image_metadata, photo_profile)
        else:
            await annotate_image_pool(source.image_metadata)
    except Exception:
        logger.exception("Facebook photo review failed; leaving photos unreviewed")

    found = len(source.image_metadata) + len(duplicates)
    records = [{**_provenance(meta), "decision": "reject", "reason": "duplicate",
                "hero_candidate": False, "gallery_candidate": False,
                "recommended_sections": [], "scores": {}} for meta in duplicates]
    rejected = Counter({"duplicate": len(duplicates)})
    accepted: list[ImageMetadata] = []
    ordered = sorted(source.image_metadata, key=photo_rank, reverse=True)
    seen: set[str] = set()
    for meta in ordered:
        review = meta.facebook_photo_review
        reason = photo_rejection_reason(meta, "gallery")
        identity = photo_identity(meta.url) or meta.url
        if reason is None and (identity in seen or any(_similar_photo(meta, other) for other in accepted)):
            reason = "duplicate"
        hero = photo_rejection_reason(meta, "hero") is None
        hero = hero and reason is None
        if reason:
            rejected[reason] += 1
        else:
            seen.add(identity)
            accepted.append(meta)
            # A cover placement does not prove hero suitability. Strong post
            # photos can become heroes; weak covers remain supporting photos.
            # The role stays what was measured: a Page's photo is a photo of
            # its own, never a cell of a source rack, and a graphic stays one.
            meta.intent = "hero" if hero else "generic"
            if review and review.method == "metadata":
                review.hero_candidate = hero
                # Only into a gap: a photo whose post's own words already say
                # what it shows keeps them over a vocabulary of subject names.
                if review.classification and not meta.context_heading:
                    meta.context_heading = " ".join(review.classification)
                if hero and "hero" not in review.recommended_sections:
                    review.recommended_sections.insert(0, "hero")
            if meta.vision_caption:
                meta.alt = meta.vision_caption
                meta.context_heading = " ".join(review.classification) if review else ""
        scores = review.scores.model_dump() if review and review.method == "vision" and review.scores else {}
        if scores:
            scores["overall"] = review.scores.overall
        records.append({
            **_provenance(meta),
            "width": meta.width, "height": meta.height,
            "classification": review.classification if review else [],
            "caption": meta.vision_caption or "",
            "recommended_sections": review.recommended_sections if review else [],
            "scores": scores,
            "hero_candidate": hero,
            "assessment_method": review.method if review else "unreviewed",
            "gallery_candidate": reason is None,
            "decision": "reject" if reason else review.decision if review else "backup",
            "reason": reason or (review.reason if review else "vision review unavailable"),
            "review": review.model_dump(mode="json") if review else None,
        })
    source.image_metadata = accepted
    source.images = [meta.url for meta in accepted]

    # Describe only observed attributes; absent reviews leave the profile empty.
    reviews = [meta.facebook_photo_review for meta in accepted
               if meta.facebook_photo_review and meta.facebook_photo_review.method == "vision"]
    assessed = [meta for meta in accepted if meta.facebook_photo_review
                and meta.facebook_photo_review.pixel_checks_complete]
    def common(field: str) -> str:
        values = Counter(getattr(review, field) for review in reviews if getattr(review, field))
        return values.most_common(1)[0][0] if values else ""

    profile = {
        "lighting": common("lighting"), "environment_style": common("environment"),
        "photographic_style": common("photographic_style"),
        "palette": [value for value, _ in Counter(
            colour for review in reviews for colour in review.palette
        ).most_common(6)],
    }
    return {
        "page": {"name": page.name, "facebook_url": page.canonical_url},
        "review_status": ("metadata_assessed" if method == "metadata" and assessed
                          else "reviewed" if reviews else "unavailable" if not enabled
                          else "no_accepted_reviews"),
        "assessment_method": method,
        "hero_stock_fallback": not any(record["hero_candidate"] for record in records),
        "visual_profile": profile, "images": records,
        "found": found, "usable": len(accepted), "rejected_summary": dict(rejected),
    }
