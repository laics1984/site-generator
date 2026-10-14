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
from textwrap import indent
from typing import Any, Awaitable, Callable, Protocol, Sequence

from app.config import settings
from app.models.brand import BrandIdentity
from app.models.content_blocks import ImageMetadata, NavLink, SectionType, SourceContent
from app.models.facebook import FacebookPage, FactLabel
from app.models.industry import IndustryCategory
from app.services.brand_candidate import build_brand_candidate
from app.services.facebook_photo_profiles import context_for
from app.services.facebook_urls import FacebookRef
from app.services.logo_extraction import LogoCandidate
from app.services.nav_extraction import social_link_for_href
from app.services.source_preview import ImageCandidate, candidates_from_source, source_preview_payload
from app.services.source_router import gallery_photos

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
    # What to tell the operator while this one runs. On the fetcher rather than
    # in a conditional inside the chain runner, so adding a way to read a Page
    # doesn't mean editing the loop that runs them.
    label: str

    async def fetch(self, ref: FacebookRef) -> FacebookPage: ...


class _GraphFetcher:
    name = "graph"
    label = "Reading the Page"

    def __init__(self, token: str) -> None:
        self._token = token

    async def fetch(self, ref: FacebookRef) -> FacebookPage:
        from app.services.facebook_graph import fetch_page

        return await fetch_page(ref, self._token)


class _RenderFetcher:
    """One Playwright render of the Page, logged out or signed in.

    `name` is derived from the session rather than passed in, so it cannot
    disagree with the `fetched_via` that `parse_public_html` stamps from the
    same fact.
    """

    def __init__(self, storage_state: dict | None = None) -> None:
        self._storage_state = storage_state
        signed_in = storage_state is not None
        self.name = "render_session" if signed_in else "render"
        self.label = (
            "Reading the Page while signed in" if signed_in else "Reading the public Page"
        )

    async def fetch(self, ref: FacebookRef) -> FacebookPage:
        from app.services.facebook_render import fetch_page

        return await fetch_page(ref, storage_state=self._storage_state)


# Bump when a change alters what a read collects, so `/api/scrape` stops
# serving reads cached under the old rules. 4: inline-payload originals, full
# captions, Intro facts, recommendations. 5: the Page's own posts read whole from
# its feeds' data (About-page reels included), videos, Facebook's alt text.
READER_VERSION = 5


def reader_options() -> dict[str, Any]:
    """Everything that changes what a read of a Page yields — the Facebook half
    of `routers.scrape.job_options`, the read cache's reuse key. Kept beside the
    readers so a new knob lands here with them rather than being remembered at
    the router."""
    return {
        "reader_version": READER_VERSION,
        "max_posts": settings.facebook_max_posts,
        "max_reviews": settings.facebook_max_reviews,
        "photo_limit": settings.facebook_photo_max_images,
        "views_timeout": settings.facebook_views_timeout_seconds,
        "view_scroll_steps": settings.facebook_view_scroll_steps,
        "photo_review_mode": settings.facebook_photo_review_mode,
    }


def saved_session() -> dict | None:
    """The signed-in browser session a render should reuse, if there is one.

    Read fresh on every chain build rather than cached: connecting a session is
    something the operator does *between* reads, and a cache here would make the
    next read ignore it. `browser_session.load` already refuses an expired one.
    """
    if not settings.facebook_session_enabled:
        return None
    from app.services import browser_session

    return browser_session.load("facebook")


def default_fetchers(access_token: str | None) -> list[FacebookFetcher]:
    """Preferred-first. Graph is skipped entirely when there's no token.

    **The logged-out render leads, and a session is a rescue behind it** — not
    the mode the render runs in. Measured on three unrelated Pages (a Malaysian
    tadika, NASA, Vans), the signed-in render read 14-34 characters against the
    anonymous read's 146-355, and the difference was every fact a site is built
    from:

    * Facebook serves `og:title`/`og:description`/`og:image` to a logged-out
      visitor and blanks them for an authenticated one, so a signed-in read has
      no identity source at all — `parse_public_html` fell back to the URL slug
      and named a kindergarten `tadikamurni1988`.
    * The logged-out `/about` view is server-rendered, phone and email in the
      markup. The signed-in one is the React shell: the Page's own facts were
      absent from the HTML entirely, while the visitor's notification tray
      ("You approved a login.") was in the visible text the label scan reads.
      NASA's `public-inquiries@hq.nasa.gov` — the fact this feature was built to
      find — comes from the *anonymous* read.

    So a session can only ever *add* now, and it adds where it is the only thing
    that can: a Page that refuses a logged-out visitor outright (age- or
    region-gated) is exactly the case where the first render raises and the
    chain falls through. It is skipped entirely when there is no session, rather
    than queued as a second identical anonymous render.
    """
    token = (access_token or settings.facebook_access_token or "").strip()
    chain: list[FacebookFetcher] = []
    if token:
        chain.append(_GraphFetcher(token))
    if settings.facebook_render_fallback_enabled:
        chain.append(_RenderFetcher())
        session = saved_session()
        if session is not None:
            chain.append(_RenderFetcher(session))
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
            await on_progress(20, fetcher.label)
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
            "bistro", "food", "pizza*", "diner", "cater*", "brewery", "dessert*",
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
            "clinic", "dentist*", "dental", "doctor", "medical", "physiotherap*",
            "optometr*", "hospital", "pharmac*", "chiropract*", "health*",
            "wellness", "spa", "salon", "beauty", "massage", "gym", "fitness",
            "yoga", "pilates",
        ),
        "health-wellness",
    ),
    (
        (
            "real estate", "realtor*", "property", "properties", "estate agent*",
            "property management", "property developer*",
        ),
        "real-estate",
    ),
    (
        (
            "plumb*", "electrician*", "contractor*", "renovation*", "cleaning",
            "cleaner*", "handyman", "roofing", "roofer*", "landscap*",
            "pest control", "home improvement", "locksmith*", "moving",
            "air conditioning", "aircon", "home service*", "home repair*",
        ),
        "home-services",
    ),
    (
        (
            "lawyer*", "law firm", "legal", "accountant*", "accounting", "tax",
            "insurance", "financial", "veterinar*", "notary", "architect*",
            "engineer*",
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
    that isn't a fact should never be added here. Every label is a `FactLabel`,
    because `landing_source` splits this text back into sections at them.
    """
    lines: list[str] = []

    headline = page.name
    if page.category:
        headline = f"{page.name} — {page.category}"
    lines.append(headline)

    for label, value in (
        (FactLabel.ABOUT, page.about),
        (None, page.description),
        (FactLabel.CATEGORIES, " · ".join(page.categories) if len(page.categories) > 1 else None),
        (FactLabel.MISSION, page.mission),
        (FactLabel.PRODUCTS, page.products),
        (FactLabel.SERVICE_OPTIONS, " · ".join(page.service_options)),
        (FactLabel.FOUNDED, page.founded),
        (FactLabel.PRICE_RANGE, page.price_range),
        (FactLabel.ADDRESS, page.address_line),
        (FactLabel.SERVICE_AREA, page.service_area),
        (FactLabel.PHONE, page.phone),
        (FactLabel.EMAIL, ", ".join(page.emails)),
        (FactLabel.WEBSITE, page.website),
        (FactLabel.ALSO_ON, ", ".join(_profile_labels(page))),
    ):
        if value:
            lines.append(f"{label}: {value}" if label else value)

    if page.hours:
        lines.append(f"{FactLabel.HOURS}:")
        lines.extend(f"  {h.as_line()}" for h in page.hours)

    # Indented as a block, every line of it: a multi-line caption is one fact,
    # and an unindented "Price: RM10" inside it would read as a label of the
    # digest's own to `landing_source`.
    posts = page.posts_with_text
    if posts:
        lines.append(f"{FactLabel.UPDATES}:")
        lines.extend(indent((p.message or "").strip(), "  ") for p in posts)

    if page.events:
        lines.append(f"{FactLabel.EVENTS}:")
        lines.extend(
            indent("\n".join(part for part in (
                f"{event.name} ({event.start_time[:10]})" if event.start_time else event.name,
                (event.description or "").strip(),
            ) if part), "  ")
            for event in page.events
        )

    if page.reviews:
        lines.append(f"{FactLabel.REVIEWS}:")
        lines.extend(indent(f'"{r.text}" — {r.author}', "  ") for r in page.reviews)

    if page.fan_count is not None:
        lines.append(f"{FactLabel.FOLLOWERS}: {page.fan_count:,}")
    if page.rating_count is not None and page.overall_star_rating is not None:
        lines.append(
            f"{FactLabel.RATING}: {page.overall_star_rating:.1f} from {page.rating_count:,} reviews"
        )
    elif page.rating_count:
        lines.append(f"{FactLabel.RATING}: {page.rating_count:,} reviews on Facebook")

    return "\n".join(lines).strip()


def _profile_labels(page: FacebookPage) -> list[str]:
    """Platform names for the Page's other profiles, from the one classifier."""
    return [link.label for link in social_profile_links(page)]


def social_profile_links(page: FacebookPage) -> list[NavLink]:
    links = (social_link_for_href(url, page.canonical_url) for url in page.social_profiles)
    return [link for link in links if link is not None]


def build_headings(page: FacebookPage) -> list[str]:
    """Headings the Page actually justifies — never a fixed list.

    A heading the source can't support is a request for the model to write a
    section that has no facts behind it.
    """
    headings: list[str] = []
    if page.about or page.description or page.mission:
        headings.append(f"About {page.name}")
    if page.products:
        headings.append(FactLabel.PRODUCTS.value)
    if page.category:
        headings.append(page.category)
    if page.hours:
        headings.append(FactLabel.HOURS.value)
    if page.address_line:
        headings.append("Where to find us")
    if page.phone or page.emails:
        headings.append("Get in touch")
    if page.reviews:
        headings.append(FactLabel.REVIEWS.value)
    return headings


# --- section gating -------------------------------------------------------------

_MIN_POSTS_FOR_FEATURES = 3
_MIN_PHOTOS_FOR_GALLERY = 4
# One number is a fact; a stats band needs at least two to read as one.
_MIN_STATS = 2

# The order sections appear in, and — separately — the order they are KEPT in
# when the Page grounds more than a homepage holds. Contact comes first in the
# second: its values come from the authority pass, not the model, so it is the
# one section that can never be padded and the one a visitor most needs.
_DISPLAY_ORDER: tuple[SectionType, ...] = (
    "about", "features", "stats", "gallery", "testimonials", "locations", "contact",
)
_KEEP_ORDER: tuple[SectionType, ...] = (
    "contact", "about", "testimonials", "gallery", "features", "locations", "stats",
)


def homepage_sections_for(page: FacebookPage, *, photo_count: int | None = None) -> list[SectionType]:
    """The landing page's sections, each gated on its own evidence.

    This is the structural half of the no-fabrication guarantee: rather than
    asking the model for an industry-standard rhythm and hoping the fidelity
    rules hold, we only ever *ask* for a section the Page can ground. A Page
    with no reviews never gets a testimonials slot to fill.

    Capped by landing_patterns._MAX_HOMEPAGE_SECTIONS (8) — a homepage is one
    LLM call and overflowing its token budget fails the whole generation. The
    cap drops by `_KEEP_ORDER`, never by position: a tail cut used to take
    contact first.
    """
    from app.services.facebook_authority import stat_items
    from app.services.landing_patterns import _MAX_HOMEPAGE_SECTIONS

    if photo_count is None:
        photo_count = len(page.post_photo_urls) + (1 if page.cover_photo_url else 0)
    grounded = {
        "about": bool(page.about or page.description or page.mission),
        "features": bool(page.products) or len(page.posts_with_text) >= _MIN_POSTS_FOR_FEATURES,
        "stats": len(stat_items(page)) >= _MIN_STATS,
        "gallery": photo_count >= _MIN_PHOTOS_FOR_GALLERY,
        "testimonials": bool(page.reviews),
        "locations": bool(page.address_line),
        "contact": True,
    }
    kept = [kind for kind in _KEEP_ORDER if grounded[kind]][: max(0, _MAX_HOMEPAGE_SECTIONS - 2)]
    return ["hero", *(kind for kind in _DISPLAY_ORDER if kind in kept), "cta"]


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
    """The cover and the post photos, as the Page's own content photos.

    Each carries its industry's `facebook_photo_profiles` context, so one
    measured review screens them all and the profile alone says how. None is
    a hero by position: a cover is a banner as often as a photograph, and only
    measurement (`facebook_photo_selection`) promotes a photo to hero. The role
    is `content`, never `gallery` — a Page's photos are pictures of their own,
    not cells of a source rack, and the gallery role is what makes the poster,
    QR and screening passes skip an image.

    The profile picture is NOT here — it is the brand mark, handled by
    `build_brand`.
    """
    context = context_for(industry_for(page))
    childcare = context == "facebook_childcare"
    out: list[ImageMetadata] = []
    if page.cover_photo_url:
        out.append(
            ImageMetadata(
                url=page.cover_photo_url,
                selection_context=context,
                alt=f"{page.name} cover photo",
                role="content",
                source_usage="inline",
                context_heading=page.name,
            )
        )
    seen: set[str] = {page.cover_photo_url or ""}
    for post in page.posts:
        # A video's frame is a picture of the business like any photo, and it
        # illustrates the same caption: measured on a Page that posts reels
        # only, the frames were the only pictures any caption described.
        for photo in post.pictures:
            if not photo.url or photo.url in seen:
                continue
            seen.add(photo.url)
            out.append(
                ImageMetadata(
                    url=photo.url, width=photo.width, height=photo.height,
                    selection_context=context,
                    alt=f"{page.name} activity photo" if childcare else _post_alt(post.message, f"{page.name} photo"),
                    role="content", source_usage="inline",
                    context_heading="" if childcare else _post_alt(post.message, ""),
                    # Caption evidence for the existing text-model planning call.
                    caption=(post.message or "")[:500],
                    platform_alt=photo.auto_alt or "",
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

    social: list[NavLink] = [
        NavLink(label="Facebook", href=page.canonical_url),
        *social_profile_links(page),
    ]
    if page.website:
        social.append(NavLink(label="Website", href=page.website))

    images = to_image_metadata(page)

    industry = industry_for(page)
    return SourceContent(
        source_kind="facebook",
        source_ref=page.canonical_url,
        # The Page's own category outranks a model reading its About text.
        declared_industry=None if industry == "other" else industry,
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
    from app.services.facebook_photo_selection import review_photo_library

    source_content = to_source_content(page)
    brand = await build_brand(page)
    photo_review = await review_photo_library(source_content, page)

    return source_preview_payload(
        url=page.canonical_url,
        final_url=page.canonical_url,
        source_content=source_content,
        brand_candidate=brand,
        image_candidates=candidates_from_source(source_content),
        fetched_at=None,  # set by the orchestrator
        # No frontier — a Page is one landing page, so the preview's
        # "crawl N more" affordance stays hidden without asking.
        extra={
            "facebook_facts": page.model_dump(mode="json"),
            "facebook_contact": to_contact_dict(page),
            # Counted by the gallery's own predicate, after the photo review:
            # a menu sheet or a poster is a photo a gallery tile can't show.
            "facebook_sections": homepage_sections_for(
                page, photo_count=len(gallery_photos(source_content))
            ),
            **({"facebook_photo_selection": photo_review} if photo_review else {}),
        },
    )
