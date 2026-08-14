"""Turn a Facebook Page into pipeline input, deterministically.

Two jobs:

1. **The fetch chain.** Graph API when a token is available, public render
   otherwise. Ordered preferred-first like the existing
   `try_fast_fetch` → Playwright ladder, and injectable so the offline test
   suite never touches a network.

2. **The mapper.** `FacebookPage` → `SourceContent` + `BrandIdentity` +
   image candidates + a contact dict. This is where the no-fabrication
   guarantee is built, in three layers:

   - `build_raw_text` writes every retrieved fact, verbatim and labelled, into
     `SourceContent.raw_text`. That string is the haystack
     `scaffold_enforcement.is_grounded_in_source` matches every LLM claim
     against, so it defines exactly what the model is allowed to say.
   - `homepage_sections_for` gates each section on its own evidence. A section
     the Page can't ground is never *requested*, so the model is never put in
     the position of padding one.
   - Contact details bypass the LLM entirely — they ride the `contact` dict
     into the SEO/legal path, and `facebook_authority` overwrites whatever the
     model wrote for them afterwards.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable, Protocol, Sequence

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import ImageMetadata, NavLink, SectionType, SourceContent
from app.models.facebook import FacebookPage
from app.models.industry import IndustryCategory
from app.services.brand_candidate import build_brand_candidate
from app.services.facebook_urls import FacebookRef
from app.services.logo_extraction import LogoCandidate
from app.services.source_preview import ImageCandidate, source_preview_payload

logger = logging.getLogger(__name__)

ProgressFn = Callable[[int, str], Awaitable[None]]


class FacebookSourceError(Exception):
    """A Facebook read that produced nothing usable. Carries a status code."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


# --- fetch chain ----------------------------------------------------------------


class FacebookFetcher(Protocol):
    name: str

    async def fetch(self, ref: FacebookRef) -> FacebookPage: ...


class _GraphFetcher:
    name = "graph"

    def __init__(self, token: str) -> None:
        self._token = token

    async def fetch(self, ref: FacebookRef) -> FacebookPage:
        from app.services.facebook_graph import fetch_page

        return await fetch_page(ref, self._token)


class _RenderFetcher:
    name = "render"

    async def fetch(self, ref: FacebookRef) -> FacebookPage:
        from app.services.facebook_render import fetch_page

        return await fetch_page(ref)


def default_fetchers(access_token: str | None) -> list[FacebookFetcher]:
    """Preferred-first. Graph is skipped entirely when there's no token."""
    token = (access_token or settings.facebook_access_token or "").strip()
    chain: list[FacebookFetcher] = []
    if token:
        chain.append(_GraphFetcher(token))
    if settings.facebook_render_fallback_enabled:
        chain.append(_RenderFetcher())
    return chain


async def fetch_facebook_page(
    ref: FacebookRef,
    *,
    access_token: str | None = None,
    fetchers: Sequence[FacebookFetcher] | None = None,
    on_progress: ProgressFn | None = None,
) -> FacebookPage:
    """Read `ref` with the first fetcher that succeeds.

    `fetchers` is the test injection point — the suite is offline, so nothing
    here may reach the network unless a caller asked it to.
    """
    chain = list(fetchers if fetchers is not None else default_fetchers(access_token))
    if not chain:
        raise FacebookSourceError(
            "No way to read that Facebook Page — connect a Page access token or "
            "enable the public-page fallback.",
            status=422,
        )

    last: Exception | None = None
    for fetcher in chain:
        if on_progress:
            await on_progress(
                20, "Reading the Page" if fetcher.name == "graph" else "Reading the public Page"
            )
        try:
            return await fetcher.fetch(ref)
        except Exception as exc:  # each fetcher raises its own error type
            logger.info("Facebook: %s fetcher failed (%s)", fetcher.name, exc)
            last = exc

    status = getattr(last, "status", 502)
    raise FacebookSourceError(str(last) if last else "Couldn't read that Page.", status=status)


# --- category → industry --------------------------------------------------------

# Facebook's category vocabulary is long; these are the needles that map onto an
# IndustryCategory. Anything unmatched stays "other" — the design engine handles
# that fine, and a wrong industry is worse than no industry, because it steers
# the whole visual language.
#
# A trailing "*" means "stem": `consult*` matches consulting and consultancy.
# Everything else matches whole words only. Bare substring matching is what
# filed "Public Figure" under restaurant, via "pub" inside "public" — the same
# class of bug page_inference._hint_matches exists to prevent ("work" matching
# /framework). Multi-word needles must appear as a contiguous run.
_CATEGORY_MAP: tuple[tuple[tuple[str, ...], IndustryCategory], ...] = (
    (
        (
            "restaurant*", "cafe", "café", "coffee", "bakery", "bar", "pub",
            "bistro", "food", "pizza*", "diner", "catering", "brewery", "dessert*",
            "juice", "ice cream", "steakhouse", "sushi", "eatery", "grill",
        ),
        "restaurant",
    ),
    (
        (
            "preschool", "childcare", "child care", "kindergarten", "nursery",
            "day care", "daycare", "children*", "tutoring", "education*", "school",
        ),
        "childcare",
    ),
    (
        (
            "lawyer*", "law firm", "legal", "accountant*", "accounting", "tax",
            "insurance", "financial", "clinic", "dentist*", "doctor", "medical",
            "veterinar*", "notary", "architect*", "engineer*", "real estate",
            "physiotherap*", "optometr*",
        ),
        "professional-services",
    ),
    (
        (
            "advertising", "marketing", "design*", "media", "photograph*", "video*",
            "creative*", "branding", "public relations", "studio",
        ),
        "agency",
    ),
    (
        (
            "software", "app", "internet company", "technology", "computer*",
            "saas", "web design", "information technology",
        ),
        "saas",
    ),
    (
        (
            "shop", "store", "retail*", "boutique", "e-commerce", "ecommerce",
            "merchant", "clothing", "furniture", "grocery",
        ),
        "ecommerce",
    ),
    (
        ("consult*", "coach*", "training", "recruit*", "business service*"),
        "consultancy",
    ),
    (
        (
            "nonprofit", "non-profit", "charity", "charities", "ngo", "religious",
            "community organization", "foundation",
        ),
        "nonprofit",
    ),
    (
        (
            "public figure", "artist", "musician*", "author", "blogger",
            "personal blog", "influencer",
        ),
        "personal",
    ),
)


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def _needle_matches(needle: str, tokens: list[str]) -> bool:
    """True when `needle`'s words appear as a contiguous run in `tokens`.

    A trailing "*" makes the LAST word a prefix match, so `consult*` reaches
    consulting and consultancy while `pub` still refuses to match "public".
    """
    stem = needle.endswith("*")
    parts = _tokens(needle.rstrip("*"))
    if not parts:
        return False
    span = len(parts)
    for i in range(len(tokens) - span + 1):
        window = tokens[i : i + span]
        if window[:-1] != parts[:-1]:
            continue
        if window[-1] == parts[-1] or (stem and window[-1].startswith(parts[-1])):
            return True
    return False


def industry_for(page: FacebookPage) -> IndustryCategory:
    """Map the Page's own category onto an IndustryCategory. No LLM guess."""
    tokens = _tokens(" ".join(p for p in [page.category or "", *page.categories] if p))
    if not tokens:
        return "other"
    for needles, industry in _CATEGORY_MAP:
        if any(_needle_matches(n, tokens) for n in needles):
            return industry
    return "other"


# --- the fact digest ------------------------------------------------------------


def build_raw_text(page: FacebookPage) -> str:
    """Every retrieved fact, labelled, in a fixed order — and nothing else.

    This is the single most consequential output of the whole feature. It is the
    haystack `scaffold_enforcement.is_grounded_in_source` matches LLM claims
    against, so a fact absent here cannot survive into the site, and a sentence
    that isn't a fact should never be added here.
    """
    lines: list[str] = []

    headline = page.name
    if page.category:
        headline = f"{page.name} — {page.category}"
    lines.append(headline)

    for label, value in (
        ("About", page.about),
        ("", page.description),
        ("Mission", page.mission),
        ("Products and services", page.products),
        ("Founded", page.founded),
        ("Price range", page.price_range),
    ):
        if not value:
            continue
        lines.append(f"{label}: {value}" if label else value)

    address = page.address_line
    for label, value in (
        ("Address", address),
        ("Phone", page.phone),
        ("Email", ", ".join(page.emails) if page.emails else None),
        ("Website", page.website),
    ):
        if value:
            lines.append(f"{label}: {value}")

    if page.hours:
        lines.append("Opening hours:")
        lines.extend(f"  {h.as_line()}" for h in page.hours)

    posts = page.posts_with_text
    if posts:
        lines.append("Updates from the Page:")
        lines.extend(f"  {(p.message or '').strip()}" for p in posts)

    if page.reviews:
        lines.append("What customers say:")
        lines.extend(f'  "{r.text}" — {r.author}' for r in page.reviews)

    if page.fan_count is not None:
        lines.append(f"Followers on Facebook: {page.fan_count:,}")
    if page.rating_count is not None and page.overall_star_rating is not None:
        lines.append(
            f"Rating: {page.overall_star_rating:.1f} from {page.rating_count:,} reviews"
        )

    return "\n".join(lines).strip()


def build_headings(page: FacebookPage) -> list[str]:
    """Headings the Page actually justifies — never a fixed list.

    A heading the source can't support is a request for the model to write a
    section that has no facts behind it.
    """
    headings: list[str] = []
    if page.about or page.description or page.mission:
        headings.append(f"About {page.name}")
    if page.products:
        headings.append("Products and services")
    if page.category:
        headings.append(page.category)
    if page.hours:
        headings.append("Opening hours")
    if page.address_line:
        headings.append("Where to find us")
    if page.phone or page.emails:
        headings.append("Get in touch")
    if page.reviews:
        headings.append("What customers say")
    return headings


# --- section gating -------------------------------------------------------------

_MIN_POSTS_FOR_FEATURES = 3
_MIN_PHOTOS_FOR_GALLERY = 4


def homepage_sections_for(page: FacebookPage) -> list[SectionType]:
    """The landing page's sections, each gated on its own evidence.

    This is the structural half of the no-fabrication guarantee: rather than
    asking the model for an industry-standard rhythm and hoping the fidelity
    rules hold, we only ever *ask* for a section the Page can ground. A Page
    with no reviews never gets a testimonials slot to fill.

    Capped by landing_patterns._MAX_HOMEPAGE_SECTIONS (8) — a homepage is one
    LLM call and overflowing its token budget fails the whole generation.
    """
    from app.services.landing_patterns import _MAX_HOMEPAGE_SECTIONS

    body: list[SectionType] = []
    if page.about or page.description or page.mission:
        body.append("about")
    if page.products or len(page.posts_with_text) >= _MIN_POSTS_FOR_FEATURES:
        body.append("features")
    photo_count = len(page.post_photo_urls) + (1 if page.cover_photo_url else 0)
    if photo_count >= _MIN_PHOTOS_FOR_GALLERY:
        body.append("gallery")
    if page.reviews:
        body.append("testimonials")
    if page.address_line:
        body.append("locations")
    # contact is unconditional: its values come from the authority pass, not
    # the model, so it can't be fabricated even on a Page with no details.
    body.append("contact")

    body = body[: max(0, _MAX_HOMEPAGE_SECTIONS - 2)]
    return ["hero", *body, "cta"]


# --- images ---------------------------------------------------------------------


def _post_alt(message: str | None, fallback: str) -> str:
    """First sentence of the post, which is what the photo illustrated."""
    text = (message or "").strip()
    if not text:
        return fallback
    for stop in (". ", "\n", "! ", "? "):
        if stop in text:
            text = text.split(stop, 1)[0]
            break
    return text[:120].strip() or fallback


def to_image_metadata(page: FacebookPage) -> list[ImageMetadata]:
    """Cover as the hero, post photos as content. The profile picture is NOT
    here — it is the brand mark, handled by `build_brand`."""
    out: list[ImageMetadata] = []
    if page.cover_photo_url:
        out.append(
            ImageMetadata(
                url=page.cover_photo_url,
                alt=f"{page.name} cover photo",
                intent="hero",
                role="hero",
                source_usage="inline",
                context_heading=page.name,
            )
        )
    seen: set[str] = {page.cover_photo_url or ""}
    for post in page.posts:
        if not post.image_url or post.image_url in seen:
            continue
        seen.add(post.image_url)
        out.append(
            ImageMetadata(
                url=post.image_url,
                alt=_post_alt(post.message, f"{page.name} photo"),
                intent="generic",
                role="gallery",
                source_usage="inline",
                context_heading=_post_alt(post.message, ""),
            )
        )
    return out


def to_image_candidates(page: FacebookPage) -> list[ImageCandidate]:
    """The shared preview candidate shape, so the existing preview UI renders
    Facebook images with no changes."""
    return [
        ImageCandidate(
            url=meta.url,
            alt=meta.alt,
            intent=meta.intent,
            role=meta.role,
            source_usage=meta.source_usage,
            context_heading=meta.context_heading,
        )
        for meta in to_image_metadata(page)
    ]


# --- contact --------------------------------------------------------------------


def to_contact_dict(page: FacebookPage) -> dict[str, str]:
    """The Page's contact details, for SEO JSON-LD and the legal pages.

    This hook already existed end-to-end (`GenerateWithPagesRequest.contact` →
    `seo._build_organization` → `legal_pages`) but nothing ever populated it.
    Filling it upgrades the homepage's JSON-LD to LocalBusiness whenever the
    Page states an address — and it does so without the LLM touching a digit.
    """
    contact: dict[str, str] = {}
    if page.primary_email:
        contact["email"] = page.primary_email
    if page.phone:
        contact["phone"] = page.phone
    address = page.address_line
    if address:
        contact["address"] = address
    return contact


# --- SourceContent --------------------------------------------------------------


def to_source_content(page: FacebookPage) -> SourceContent:
    """The pipeline's normalized input. One landing page — no discovered_pages.

    Raises `FacebookSourceError` when the Page carries too little to build from.
    The floor is higher than the document path's 80 characters because a Page
    always yields a name plus a category (~40 chars), which would sail past 80
    and produce a site padded out of nothing.
    """
    raw_text = build_raw_text(page)
    if len(raw_text.strip()) < settings.facebook_min_raw_text_chars:
        raise FacebookSourceError(
            f"'{page.name}' doesn't have enough public content to build a site from. "
            "Add an About section, contact details or a few posts to the Page — or "
            "connect a Page access token so we can read what's already there.",
            status=422,
        )

    social: list[NavLink] = [NavLink(label="Facebook", href=page.canonical_url)]
    if page.website:
        social.append(NavLink(label="Website", href=page.website))

    images = to_image_metadata(page)

    return SourceContent(
        source_kind="facebook",
        source_ref=page.canonical_url,
        title=page.name,
        # The About blurb. A prompt input the document path leaves empty —
        # free grounding at no extra token cost.
        description=page.about or page.description,
        raw_text=raw_text,
        headings=build_headings(page),
        images=[meta.url for meta in images],
        image_metadata=images,
        links=[page.website] if page.website else [],
        social_links=social,
        subject_name=page.name,
        # One landing page. A Facebook Page does not carry enough grounded text
        # to fill four, and page_inference's template fallback would hand back
        # ~5 pages that the fidelity net then strips back to nothing.
        discovered_pages=[],
    )


# --- brand ----------------------------------------------------------------------


async def build_brand(page: FacebookPage) -> BrandIdentity | None:
    """The profile picture as the brand mark, with a photo guard.

    The profile picture is the conventional brand-mark slot on a business Page,
    so it goes through the existing `_build_brand_candidate` seam and gets
    palette extraction and a `logo_render_ok` computed from the DECODED pixel
    size for free.

    The guard: plenty of Pages use a storefront shot or a face there, which
    renders badly as a header mark. When the vision judge is available we ask,
    and demote a photograph to `og-image` — for which `is_renderable` returns
    False, so `logo_render_ok` goes False, the header falls back to the text
    wordmark, and the palette is still extracted. Vision is opt-in, so the
    default path accepts the profile picture as the mark.

    The cover photo is never a candidate — it is a banner with baked-in text.
    """
    if not page.profile_picture_url:
        return BrandIdentity(name=page.name, mood=None) if page.name else None

    source = "logo"
    if settings.facebook_logo_vision_check:
        if await _looks_like_photograph(page.profile_picture_url):
            logger.info(
                "Facebook: '%s' profile picture looks like a photo, not a mark — "
                "palette only, header falls back to the wordmark",
                page.name,
            )
            source = "og-image"

    candidate = LogoCandidate(source=source, url=page.profile_picture_url, data_url=None)
    return await build_brand_candidate(page.name, candidate)


async def _looks_like_photograph(url: str) -> bool:
    """True when the vision judge calls this a photo rather than a mark.

    Returns False whenever vision is unavailable or the call fails — the guard
    is an upgrade, never a gate.
    """
    try:
        from app.services.image_vision import annotate_image_pool, vision_enabled

        if not vision_enabled():
            return False
        annotations = await annotate_image_pool(
            [ImageMetadata(url=url, intent="logo")], max_images=1
        )
    except Exception as exc:
        logger.debug("Facebook: logo vision check unavailable (%s)", exc)
        return False

    annotation = (annotations or {}).get(url)
    return getattr(annotation, "kind", None) in ("photo", "screenshot")


# --- the preview payload --------------------------------------------------------


async def to_preview_payload(page: FacebookPage) -> dict[str, Any]:
    """The canonical scrape-result shape, plus the Facebook facts.

    Matches `crawl_orchestrator._result_to_payload` field for field — that is
    what lets the existing ScrapePreview → PagePicker → generate chain consume a
    Facebook read with no changes. `facebook_facts` is additive: it rides along
    so the frontend can round-trip it into /generate, where the authority pass
    needs the verified values.
    """
    source_content = to_source_content(page)
    brand = await build_brand(page)

    return source_preview_payload(
        url=page.canonical_url,
        final_url=page.canonical_url,
        source_content=source_content,
        brand_candidate=brand,
        image_candidates=to_image_candidates(page),
        fetched_at=None,  # set by the orchestrator
        # No frontier — a Page is one landing page, so the preview's
        # "crawl N more" affordance stays hidden without asking.
        extra={
            "facebook_facts": page.model_dump(mode="json"),
            "facebook_contact": to_contact_dict(page),
            "facebook_industry": industry_for(page),
            "facebook_sections": homepage_sections_for(page),
        },
    )
