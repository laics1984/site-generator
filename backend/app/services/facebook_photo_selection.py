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


def metadata_hero_rejection_reason(meta: ImageMetadata, profile: PhotoProfile) -> str | None:
    """Measured eligibility only: never claims to know faces/negative space."""
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
    if not review.caption_hero_context:
        return ("caption lacks suitable activity context" if profile.hero_requires_caption
                else "caption rules out a hero")
    return None


async def review_photo_library(source: SourceContent, page: FacebookPage) -> dict:
    """Default to CPU/caption assessment, retaining opt-in vision compatibility."""
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
        else:
            await annotate_image_pool(source.image_metadata)
    except Exception:
        logger.exception("Facebook photo review failed; leaving photos unreviewed")

    records = [{"source_image_url": meta.url, "decision": "reject", "reason": "duplicate",
                "hero_candidate": False, "gallery_candidate": False,
                "recommended_sections": [], "scores": {}} for meta in duplicates]
    rejected = Counter({"duplicate": len(duplicates)})
    accepted: list[ImageMetadata] = []
    posts = {photo.url: post for post in page.posts for photo in post.pictures}
    # Strongest version wins duplicate groups; source order is the tie-breaker.
    ordered = sorted(source.image_metadata, key=lambda meta: (
        meta.facebook_photo_review.scores.overall
        if meta.facebook_photo_review and meta.facebook_photo_review.scores else 0,
        (meta.width or 0) * (meta.height or 0),
    ), reverse=True)
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
                if review.classification:
                    meta.context_heading = " ".join(review.classification)
                if hero and "hero" not in review.recommended_sections:
                    review.recommended_sections.insert(0, "hero")
            if meta.vision_caption:
                meta.alt = meta.vision_caption
                meta.context_heading = " ".join(review.classification) if review else ""
        post = posts.get(meta.url)
        scores = review.scores.model_dump() if review and review.method == "vision" and review.scores else {}
        if scores:
            scores["overall"] = review.scores.overall
        records.append({
            "source_image_url": meta.url,
            "source_post_id": post.post_id if post else None,
            "source_post_url": post.permalink if post else None,
            "date": post.created_time if post else None,
            "source_type": "post" if post else "cover",
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
    section_plan = []
    for section in photo_profile.plan_sections:
        matches = [record for record in records if record["decision"] != "reject"
                   and section in record["recommended_sections"]
                   and (section != "hero" or record["hero_candidate"])]
        matches.sort(key=lambda record: record["scores"].get("overall", 0), reverse=True)
        authentic_only = section in {"facilities", "teachers", "gallery"}
        decorative = section in {"testimonials", "contact", "faq", "footer"}
        if decorative:
            preferred_source, reason = "none", "minimal styling"
        elif matches:
            preferred_source, reason = "facebook", "source photos available"
        elif authentic_only:
            preferred_source, reason = "none", "authentic source photo required"
        else:
            preferred_source = "stock"
            reason = "no suitable reviewed source photo; use existing stock/abstract fallback"
        section_plan.append({
            "section": section,
            "preferred_source": preferred_source,
            "facebook_image_ids": [record["source_image_url"] for record in matches[:6]],
            "fallback_required": not matches and not decorative and not authentic_only,
            "reason": reason,
        })
    return {
        "page": {"name": page.name, "facebook_url": page.canonical_url},
        "review_status": ("metadata_assessed" if method == "metadata" and assessed
                          else "reviewed" if reviews else "unavailable" if not enabled
                          else "no_accepted_reviews"),
        "assessment_method": method,
        "hero_stock_fallback": not any(record["hero_candidate"] for record in records),
        "visual_profile": profile, "images": records,
        "section_image_plan": section_plan, "rejected_summary": dict(rejected),
    }
