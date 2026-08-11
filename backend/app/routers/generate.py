import asyncio
import logging
import re
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.models.brand import (
    BrandIdentity,
    BrandMood,
    HeroBackgroundHeight,
    default_hero_height,
)
from app.models.builder_schema import (
    BodySchema,
    GeneratedPage,
    GeneratedSite,
    PageSeo,
)
from app.models.design_manifest import FooterArchetype, HeaderArchetype
from app.models.content_blocks import (
    DownloadItem,
    DownloadLink,
    DownloadsBlock,
    ImageMetadata,
    IndustryCategoryLiteral,
    industry_locked_mood,
    LinkBarBlock,
    LinkBarLink,
    LinkCluster,
    PagePlan,
    ProfileBlock,
    ProfileCandidate,
    ProfileContact,
    ServiceItem,
    ServicesBlock,
    SitePlan,
    SourceContent,
    TeamBlock,
    TeamMember,
)
from app.models.industry import PageScaffold
from app.services.industry_templates import get_template
from app.services.design_brain import generate_design_language
from app.services.translations import build_translated_pages
from app.services.text_detection import prefetch_text_flags
from app.services.legal_pages import build_privacy_page, build_terms_page
from app.services.llm import LlmError
from app.services.planner import (
    DetectedBrand,
    detect_brand_cached,
    plan_site,
    plan_site_with_scaffolds,
)
from app.services.nav_extraction import find_linkbar_cluster, strip_linkbar_lines
from app.services.page_inference import DIRECTORY_MIN_PROFILES
from app.services.image_refs import bind_image_refs
from app.services.scaffold_enforcement import (
    align_page_to_scaffold,
    looks_like_team_member_name,
    sanitize_blocks_against_source,
)
from app.services.profile_text import (
    FOUNDERS_BAND_MAX,
    clean_team_bio,
    looks_like_founder_role,
    looks_like_team_role,
)
from app.services.image_vision import (
    VisionAnnotation,
    annotate_image_pool,
    prefetch_image_pool,
)
from app.services.locale import detect_market, image_query_cue, place_query_cue
from app.services.content_collections import extract_collections
from app.services.diversity import recent_choices
from app.services.schema_builder import plan_to_site
from app.services.theme import build_theme, resolve_color_scheme
from app.services.timing import stage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/generate", tags=["generate"])


# --- legacy free-form generate (unchanged behaviour) ---------------------------


class GenerateRequest(BaseModel):
    """Source content plus optional brand. Brand drives logo + palette + mood."""

    source: SourceContent
    brand: BrandIdentity | None = None
    mood_override: BrandMood | None = None
    color_scheme_override: str | None = None  # "light" | "dark"; overrides the logo-based default
    # Full-screen vs bounded photo hero, site-wide. None = "Auto": defer to the
    # design-brain pick, then the mood/industry default (see resolve_hero_height).
    hero_height: HeroBackgroundHeight | None = None
    contact: dict[str, str] | None = None
    # Explicit chrome pins — win over the design director's fit/seed/diversity
    # pick (see design_director.compose_design_manifest). None → let it decide.
    header_archetype: HeaderArchetype | None = None
    footer_archetype: FooterArchetype | None = None


def resolve_hero_height(
    explicit: HeroBackgroundHeight | None,
    chosen: HeroBackgroundHeight | None,
    *,
    mood: BrandMood | None,
    industry: str | None,
) -> HeroBackgroundHeight:
    """Site-wide hero height, most-specific source first.

    explicit (the user's own pick, "Auto" sends None) → the design-brain pick →
    the deterministic industry/mood default. Mirrors how palette_choice and
    font_choice defer to build_theme's pickers: a disabled or failed pass leaves
    a fully-determined result, never an empty one.
    """
    return explicit or chosen or default_hero_height(mood, industry)


def _market_cues_for(source: SourceContent) -> tuple[str, str]:
    """Best-effort (demonym, place) cues for image queries — e.g.
    ("Southeast Asian", "Malaysia").

    Locale detection is an enhancement, never load-bearing — any failure must
    not break generation, so we swallow errors and fall back to no cues.
    """
    try:
        urls = [source.source_ref, *source.links, *source.images]
        market = detect_market(source.raw_text, urls=urls)
        return image_query_cue(market), place_query_cue(market)
    except Exception:  # noqa: BLE001 — image localisation must not 500 a generation
        return "", ""


def _image_pool_for(source: SourceContent) -> tuple[list[str], list[ImageMetadata]]:
    """Flatten entry + crawled page imagery into one de-duped resolver pool."""
    images: list[str] = []
    metadata: list[ImageMetadata] = []
    seen_images: set[str] = set()
    seen_metadata: set[str] = set()

    def add_page(page: SourceContent) -> None:
        for url in page.images:
            if url and url not in seen_images:
                seen_images.add(url)
                images.append(url)
        for item in page.image_metadata:
            if item.url and item.url not in seen_metadata:
                seen_metadata.add(item.url)
                metadata.append(item)
            if item.url and item.url not in seen_images:
                seen_images.add(item.url)
                images.append(item.url)

    add_page(source)
    for page in source.discovered_pages:
        add_page(page)

    return images, metadata


def _page_images_by_slug(source: SourceContent) -> dict[str, list[ImageMetadata]]:
    """Map each page's slug to the images the source placed on THAT page.

    Keyed by the same slug the planner/scaffolds use — ``_path_to_slug`` of the
    page's ``url_path``, with the homepage (``url_path`` None) keyed ``""``. The
    renderer hands a page's own list to the resolver as a ranking preference so
    each hero/section uses its page's photo rather than the biggest one site-wide.
    """
    out: dict[str, list[ImageMetadata]] = {}

    def add_page(page: SourceContent) -> None:
        if not page.image_metadata:
            return
        slug = (page.url_path or "").strip("/").lower()
        out.setdefault(slug, []).extend(page.image_metadata)

    add_page(source)
    for page in source.discovered_pages:
        add_page(page)

    return out


_NAME_TITLE_TOKENS = {
    "dr",
    "prof",
    "professor",
    "mr",
    "mrs",
    "ms",
    "miss",
    "dato",
    "datuk",
    "tan",
    "sir",
}


def _person_name_tokens(value: str | None) -> list[str]:
    if not value:
        return []
    return [
        token
        for token in re.findall(r"[a-z0-9]+", value.lower())
        if token and token not in _NAME_TITLE_TOKENS
    ]


def _normalized_person_name(value: str | None) -> str:
    return " ".join(_person_name_tokens(value))


def _page_name_labels(page: SourceContent) -> set[str]:
    """Normalized names the page carries as its OWN title or headings.

    Two readings of one piece of evidence. In ``_profile_pool_for`` these names
    are blocked — a card echoing the page's own title is chrome, not a person.
    In ``_profile_page_member`` a match is the opposite signal: the page is
    titled after the person it carries, so it IS that person's profile page.
    """
    return {
        norm
        for norm in (
            _normalized_person_name(page.title),
            *(_normalized_person_name(h) for h in page.headings),
        )
        if norm
    }


def _url_slug_letters(url_path: str | None) -> str:
    """The page's own path segment, punctuation stripped ("/profile/kevin-leong"
    → "kevinleong"). Hand-written people slugs split names inconsistently, so
    the separators carry no meaning worth keeping."""
    if not url_path:
        return ""
    segment = url_path.rstrip("/").rsplit("/", 1)[-1]
    return "".join(re.findall(r"[a-z0-9]+", segment.lower()))


# Shortest slug that may stand for a whole given name ("ivy"), and shortest
# multi-token run allowed to match inside a longer slug ("ivytan"). Below these
# a coincidence is likelier than a naming.
_MIN_NAME_SLUG = 3
_MIN_NAME_RUN = 6


def _url_path_names_person(url_path: str | None, name: str) -> bool:
    """True when the page's own URL segment is built out of this person's name.

    A detail page routinely carries a TEMPLATE title and a section heading
    rather than the person's name — all nine MMTA committee pages are
    ``<title>About MMTA</title>`` under an ``<h1>The Committee</h1>``, with the
    name sitting in a plain ``div.name`` inside the card. The URL is then the
    only part of the page that says who it is about.

    Three shapes, because these slugs are written by hand: the whole name run
    together (/profile/kueksersheentse), one given or family name
    (/profile/ashley), or a run of the name surviving a misspelling elsewhere
    in the slug (/profile/lohmingyuan for "Low Ming Yuan").
    """
    slug = _url_slug_letters(url_path)
    tokens = _person_name_tokens(name)
    if not slug or not tokens:
        return False
    if slug == "".join(tokens):
        return True
    if len(slug) >= _MIN_NAME_SLUG and slug in tokens:
        return True
    return any(
        len(run) >= _MIN_NAME_RUN and run in slug
        for start in range(len(tokens) - 1)
        for run in ("".join(tokens[start:end]) for end in range(start + 2, len(tokens) + 1))
    )


def _page_is_about(page: SourceContent, name: str) -> bool:
    """True when the page itself says it is this person's page.

    Three independent readings, any one of which is enough — a site only has to
    say it once, and each of the three is the only one that works somewhere:

    1. Its ``<title>`` or a heading names them. The plainest case, and the one
       template-driven CMS pages break: MMTA's nine committee pages all carry
       ``<title>About MMTA</title>`` under an ``<h1>The Committee</h1>``.
    2. Its URL names them (/profile/ashley) — usually the last thing left when
       the markup is templated, and hand-written, so spelled loosely.
    3. Its body LEADS with them: the first designated name element below the
       chrome, by DOM hierarchy, is theirs (``scraper._leading_person_name``).
       This is what reads MMTA's ``<div class="name">Ashley Jinivon</div>``, and
       it is the reading that still works when a page is at /member/4417.
    """
    normalized = _normalized_person_name(name)
    if not normalized:
        return False
    return (
        normalized in _page_name_labels(page)
        or _url_path_names_person(page.url_path, name)
        or normalized == _normalized_person_name(page.subject_name)
    )


def _profile_pool_for(source: SourceContent) -> list[ProfileCandidate]:
    """Flatten entry + crawled profile candidates without duplicates."""
    profiles: list[ProfileCandidate] = []
    seen: set[tuple[str, str | None]] = set()

    def add_page(page: SourceContent) -> None:
        blocked_names = _page_name_labels(page)
        for profile in page.profile_candidates:
            key = (_normalized_person_name(profile.name), profile.photo_url)
            if not key[0] or key[0] in blocked_names or key in seen:
                continue
            seen.add(key)
            profiles.append(profile)

    add_page(source)
    for page in source.discovered_pages:
        add_page(page)

    return profiles


def _profile_match_score(member_name: str, profile: ProfileCandidate) -> float:
    member_norm = _normalized_person_name(member_name)
    profile_norm = _normalized_person_name(profile.name)
    if not member_norm or not profile_norm:
        return 0.0
    if member_norm == profile_norm:
        return 1.0

    member_tokens = set(member_norm.split())
    profile_tokens = set(profile_norm.split())
    if not member_tokens or not profile_tokens:
        return 0.0
    overlap = len(member_tokens & profile_tokens) / len(member_tokens)
    same_tail = member_norm.split()[-1] == profile_norm.split()[-1]
    if same_tail and overlap >= 0.8:
        return 0.86
    return 0.0


async def _screen_source_images_for_text(
    metadata: list[ImageMetadata],
    prefetched: dict[str, str] | None = None,
) -> None:
    """Flag SOURCE images that carry their own headline, so none of them fills a
    slot we draw ours over (services/text_detection.py).

    Stock photography is never screened — Pexels ships photographs, not posters,
    and these are `ImageMetadata`, which stock results never become.

    Like the vision pass, an enhancement: any failure leaves the flags unset,
    which is exactly how the pipeline behaved before OCR existed.
    """
    try:
        with stage("ocr_text_screen"):
            await prefetch_text_flags(metadata, prefetched=prefetched)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — OCR must not 500 a generation
        logger.exception("OCR text screening failed; continuing without it")


async def _annotate_source_images(
    source: SourceContent,
    metadata: list[ImageMetadata],
    prefetched: dict[str, str] | None = None,
    profiles: list[ProfileCandidate] | None = None,
) -> dict[str, VisionAnnotation]:
    """Run the opt-in vision pass over the resolver pool + profile portraits.

    Returns {} instantly when no vision model is configured. Like locale
    detection, this is an enhancement — any failure must not break generation.
    `prefetched` carries {url: base64} downloads already done in parallel with
    content generation (see prefetch_image_pool).
    """
    try:
        if profiles is None:
            profiles = _profile_pool_for(source)
        profile_urls = [p.photo_url for p in profiles if p.photo_url]
        with stage("vision_annotation"):
            return await annotate_image_pool(
                metadata, extra_urls=profile_urls, prefetched=prefetched
            )
    except Exception:  # noqa: BLE001 — vision must not 500 a generation
        logger.exception("Vision annotation pass failed; continuing without it")
        return {}


def _profile_photo_vision_ok(
    photo_url: str | None, annotations: dict[str, VisionAnnotation] | None
) -> bool:
    """False when the vision pass saw the 'portrait' and it isn't one (a logo,
    a banner, an empty room). Unannotated photos keep the benefit of the doubt
    — the vision pass is opt-in and bounded, never a gate."""
    if not annotations or not photo_url:
        return True
    annotation = annotations.get(photo_url)
    if annotation is None:
        return True
    return annotation.kind == "photo" and annotation.people_count >= 1


def _enrich_plan_profile_photos(
    plan: SitePlan,
    source: SourceContent,
    annotations: dict[str, VisionAnnotation] | None = None,
    profiles: list[ProfileCandidate] | None = None,
) -> None:
    """Attach confidently matched scraped portraits to generated team members.

    Mutates the plan in place. Only concrete URLs from scraper-produced
    ProfileCandidate objects are applied, so older payloads and LLM-only plans
    keep using the existing photo_query fallback.

    "Already used" is scoped to ONE PAGE. The rule it enforces is that a grid
    must not show the same face twice — a page-level concern. Applied across the
    plan it did the opposite of what it was for: the committee roster claimed
    all nine portraits, and each member's own page, rendered later, found none
    left and fell back to a monogram. A person's portrait belongs on their card
    AND on their page.
    """
    if profiles is None:
        profiles = _profile_pool_for(source)
    profiles = [
        p for p in profiles
        if p.photo_url and _profile_photo_vision_ok(p.photo_url, annotations)
    ]
    if not profiles:
        return

    for page in plan.pages:
        used_urls: set[str] = set()
        for block in page.blocks:
            if block.kind not in ("team", "profile"):
                continue
            # A profile block is one person; a team block is a list of them.
            # Both want the same thing: this person's real portrait, matched by
            # name, never a stock face.
            people = [block] if block.kind == "profile" else block.members
            for member in people:
                scored = sorted(
                    (
                        (_profile_match_score(member.name, profile), profile)
                        for profile in profiles
                        if profile.photo_url not in used_urls
                    ),
                    key=lambda item: (item[0], item[1].confidence),
                    reverse=True,
                )
                if not scored or scored[0][0] < 0.85:
                    continue
                matched = scored[0][1]
                member.photo_url = matched.photo_url
                member.photo_alt = matched.photo_alt or member.name
                if matched.photo_url:
                    used_urls.add(matched.photo_url)


def _detail_page_href(profile_url: str | None) -> str | None:
    """A roster card's link as a site-relative href, or None.

    The source's path IS the generated slug (page_inference keys scaffolds off
    ``url_path``), so the source URL's path is the link — no re-derivation, no
    second spelling of a name the site already spelled.
    """
    if not profile_url:
        return None
    slug = urlparse(profile_url).path.strip("/").lower()
    return f"/{slug}" if slug else None


def _prune_dead_profile_links(plan: SitePlan) -> None:
    """Drop member links to pages this site doesn't have.

    The roster is the source's, the page list is the user's: they pick which
    pages to generate, and a card pointing at a member page they left out would
    be a 404 in the middle of the team grid. The card still renders — it just
    stops being a link.
    """
    known = {f"/{page.slug}" for page in plan.pages}
    for page in plan.pages:
        for block in page.blocks:
            if getattr(block, "kind", None) != "team":
                continue
            for member in block.members:
                if member.profile_href and member.profile_href not in known:
                    member.profile_href = None


def _roster_members(accepted: list[ProfileCandidate]) -> list[TeamMember]:
    """Build TeamMembers from vetted profiles, keeping only what each card
    can vouch for.

    These roster paths run from ``_ensure_scraped_team_blocks`` AFTER
    ``align_page_to_scaffold`` and replace the block wholesale, so they never
    pass through ``_sanitize_team_block`` — they must apply the same role/bio
    cleaners themselves or the tightening is only half applied.

    Grounding is deliberately skipped here: a ``ProfileCandidate`` bio is page
    text by construction, so checking it against the page is guaranteed-true
    work — and this is the up-to-24-member path.
    """
    names = tuple(p.name for p in accepted)
    # Only a ROSTER indexes people's pages. The lone card on a person's own page
    # links BACK to the roster (MMTA's members carry a "Back" arrow), and reading
    # that as this person's page would point their card at the committee grid.
    # Same threshold, same reasoning as page_inference.ROSTER_MIN_PROFILES.
    links_to_details = len(accepted) >= 2
    members: list[TeamMember] = []
    for profile in accepted:
        others = tuple(n for n in names if n != profile.name)
        role = profile.role or ""
        members.append(
            TeamMember(
                name=profile.name,
                role=role if looks_like_team_role(role) else "",
                bio=clean_team_bio(profile.bio, other_names=others),
                photo_url=profile.photo_url,
                photo_alt=profile.photo_alt or profile.name,
                photo_query=None,
                profile_href=(
                    _detail_page_href(profile.profile_url) if links_to_details else None
                ),
            )
        )
    return members


def _scraped_team_members(
    source: SourceContent,
    annotations: dict[str, VisionAnnotation] | None = None,
    profiles: list[ProfileCandidate] | None = None,
) -> list[TeamMember]:
    """Deterministic team members built from scraped profile candidates."""
    if profiles is None:
        profiles = _profile_pool_for(source)
    accepted: list[ProfileCandidate] = []
    for profile in profiles:
        if not looks_like_team_member_name(profile.name):
            continue
        if not profile.photo_url:
            continue
        if not _profile_photo_vision_ok(profile.photo_url, annotations):
            continue
        accepted.append(profile)
    return _roster_members(accepted[:24])


def _directory_roster_members(
    page_source: SourceContent | None,
    annotations: dict[str, VisionAnnotation] | None = None,
) -> list[TeamMember]:
    """Full page-scoped roster for a detected directory page.

    Unlike the site-wide ``_profile_pool_for`` flatten, this reads ONLY the
    page's own profile_candidates, so one listing's people never leak into
    another (committee vs. practitioner directories) and the whole roster
    survives — the LLM typically rewrites only a handful of names.
    """
    if page_source is None:
        return []
    accepted: list[ProfileCandidate] = []
    seen: set[str] = set()
    for profile in page_source.profile_candidates or []:
        norm = _normalized_person_name(profile.name)
        if not norm or norm in seen:
            continue
        if not looks_like_team_member_name(profile.name):
            continue
        if not profile.photo_url:
            continue
        if not _profile_photo_vision_ok(profile.photo_url, annotations):
            continue
        seen.add(norm)
        accepted.append(profile)
    return _roster_members(accepted[:24])


def _rostered_names(source: SourceContent) -> set[str]:
    """Normalized names carried by some page's profile ROSTER (2+ cards).

    The roster is what links to the detail pages, so a name appearing in one is
    structural evidence that a page titled with that name is that person's
    page. Scraping a lone card off a detail page is positional evidence only
    (scraper._page_subject_profile), and on its own would read a photo under an
    "Annual General Meeting" heading as a person. A 2+ page is always real card
    extraction — the positional fallback never emits more than one.
    """
    names: set[str] = set()
    for page in (source, *source.discovered_pages):
        candidates = page.profile_candidates or []
        if len(candidates) < 2:
            continue
        names |= {_normalized_person_name(p.name) for p in candidates}
    names.discard("")
    return names


def _profile_page_block(
    page_source: SourceContent | None,
    annotations: dict[str, VisionAnnotation] | None = None,
    rostered_names: set[str] | None = None,
) -> ProfileBlock | None:
    """The one person a detail page is about, as that page's profile block.

    Three things have to agree: the page carries exactly one vetted profile
    card, the page names that person as its own subject, and a roster elsewhere
    on the site lists them. The first two are read page-scoped — the site-wide
    ``_profile_pool_for`` deliberately drops a person whose name titles their
    own page — and the third is what makes the pairing structural.

    The bio stays. It used to be dropped because a separate about section
    narrated it; a profile page's recipe has no about section now
    (``page_inference._PROFILE_PAGE_SECTIONS``), so this block carries the
    story next to the face — which is where a profile page tells it.
    """
    if page_source is None:
        return None
    roster = _directory_roster_members(page_source, annotations)
    if len(roster) != 1:
        return None
    member = roster[0]
    normalized = _normalized_person_name(member.name)
    if not _page_is_about(page_source, member.name):
        return None
    if normalized not in (rostered_names or set()):
        return None

    candidate = next(
        (
            p
            for p in page_source.profile_candidates or []
            if _normalized_person_name(p.name) == normalized
        ),
        None,
    )
    return ProfileBlock(
        name=member.name,
        role=member.role or "",
        bio=member.bio or member.description,
        photo_url=member.photo_url,
        photo_alt=member.photo_alt or member.name,
        contacts=_profile_contacts(candidate),
    )


def _profile_contacts(candidate: ProfileCandidate | None) -> list[ProfileContact]:
    """The person's own contact affordances, as their card states them."""
    if candidate is None:
        return []
    contacts: list[ProfileContact] = []
    if candidate.email:
        contacts.append(ProfileContact(label=candidate.email, href=f"mailto:{candidate.email}"))
    if candidate.phone:
        contacts.append(ProfileContact(label=candidate.phone, href=f"tel:{candidate.phone}"))
    for label, href in candidate.social_links:
        if len(contacts) >= 4:  # ProfileBlock.contacts cap
            break
        contacts.append(ProfileContact(label=label, href=href))
    return contacts


def _ensure_scraped_team_blocks(
    plan: SitePlan,
    source: SourceContent,
    annotations: dict[str, VisionAnnotation] | None = None,
    *,
    team_section_slugs: set[str] | None = None,
    profiles: list[ProfileCandidate] | None = None,
    source_map: dict[str, SourceContent] | None = None,
    directory_slugs: set[str] | None = None,
) -> None:
    """Fallback when scraped portraits exist but the final team block lost them.

    The LLM sometimes omits the team section or rewrites member names enough
    that photo matching fails. Prefer the concrete scraped roster on pages whose
    scaffold explicitly requested a team section; legacy callers without
    scaffold context still get the old team-page fallback.

    Pages in ``directory_slugs`` (detected profile directories, e.g. a "find a
    therapist" listing) get their team block replaced WHOLESALE with the source
    page's own full roster: the LLM keeps only a subset of a long listing, and
    ``_enrich_plan_profile_photos`` can only attach photos to the members the
    LLM kept, so a partially photo-bearing block must not short-circuit here.

    A page carrying a single profile card instead gets that one person's card
    (see ``_profile_page_member``) — the detail pages a directory links to.
    """
    scraped_members = _scraped_team_members(source, annotations, profiles)
    rostered_names = _rostered_names(source)
    requested_team_slugs = team_section_slugs or set()
    directory_pages = directory_slugs or set()
    sources_by_slug = source_map or {}

    for page in plan.pages:
        team_indexes = [
            idx for idx, block in enumerate(page.blocks) if getattr(block, "kind", None) == "team"
        ]

        if page.slug in directory_pages:
            roster = _directory_roster_members(sources_by_slug.get(page.slug), annotations)
            if roster:
                if team_indexes:
                    first = team_indexes[0]
                    block = page.blocks[first]
                    page.blocks[first] = TeamBlock(
                        heading=block.heading,
                        subheading=block.subheading,
                        members=roster,
                    )
                    # One directory, one roster — drop any duplicate team blocks.
                    for idx in reversed(team_indexes[1:]):
                        del page.blocks[idx]
                else:
                    insert_at = next(
                        (
                            idx
                            for idx, block in enumerate(page.blocks)
                            if getattr(block, "kind", None) == "cta"
                        ),
                        len(page.blocks),
                    )
                    page.blocks.insert(
                        insert_at,
                        TeamBlock(
                            heading=page.title or "Meet the team",
                            subheading=None,
                            members=roster,
                        ),
                    )
                logger.info(
                    "Directory roster: filled /%s with %d scraped profiles",
                    page.slug, len(roster),
                )
                continue
            # Page-scoped roster came up empty — fall through to the generic path.

        # A detail page carrying exactly one profile card is that person's own
        # page, and it gets a profile block — portrait, name, role, story,
        # contact — right under the hero. The LLM is asked for one too (the
        # scaffold requests `profile`); this is the fallback for when it omits
        # the block or renames the person out of recognition.
        # Home is exempt: its rhythm is designed, not inferred from one card.
        profile_indexes = [
            idx for idx, block in enumerate(page.blocks)
            if getattr(block, "kind", None) == "profile"
        ]
        if not team_indexes and page.page_type != "home":
            profile = _profile_page_block(
                sources_by_slug.get(page.slug), annotations, rostered_names
            )
            if profile is not None and not profile_indexes:
                page.blocks.insert(
                    1 if page.blocks and getattr(page.blocks[0], "kind", None) == "hero" else 0,
                    profile,
                )
                logger.info("Profile page: attached %s to /%s", profile.name, page.slug)
                continue
            if profile is not None and (profile.photo_url or profile.contacts):
                # The LLM wrote the block itself; the page's own scraped card is
                # the authority on this person's portrait AND contacts, so each
                # backfills independently — `_enrich_plan_profile_photos` (run
                # just before this) already fills photo_url on most matched
                # profiles, and gating the contacts refill on a missing photo
                # left it almost never firing.
                for idx in profile_indexes:
                    existing = page.blocks[idx]
                    updates: dict[str, object] = {}
                    if not existing.photo_url and profile.photo_url:
                        updates["photo_url"] = profile.photo_url
                        updates["photo_alt"] = profile.photo_alt
                    if not existing.contacts and profile.contacts:
                        updates["contacts"] = profile.contacts
                    if not updates:
                        continue
                    page.blocks[idx] = existing.model_copy(update=updates)
                    logger.info(
                        "Profile page: refilled %s's %s on /%s",
                        profile.name,
                        "/".join(updates),
                        page.slug,
                    )
            if profile_indexes:
                continue

        if not scraped_members:
            continue

        if team_indexes:
            for idx in team_indexes:
                block = page.blocks[idx]
                if any(getattr(member, "photo_url", None) for member in block.members):
                    continue
                page.blocks[idx] = TeamBlock(
                    heading=block.heading,
                    subheading=block.subheading,
                    members=scraped_members,
                )
            continue

        if page.page_type != "team" and page.slug not in requested_team_slugs:
            continue

        insert_at = next(
            (idx for idx, block in enumerate(page.blocks) if getattr(block, "kind", None) == "cta"),
            len(page.blocks),
        )
        page.blocks.insert(
            insert_at,
            TeamBlock(
                heading="Meet the team",
                subheading=None,
                members=scraped_members,
            ),
        )

    _apply_homepage_team_policy(plan)


# People a homepage team band shows before it stops reading as an introduction
# and starts reading as a directory. Above this the roster belongs on its own
# page and home links to it.
_HOME_ROSTER_MAX = 4


def _apply_homepage_team_policy(plan: SitePlan) -> None:
    """Decide what people, if any, the homepage shows.

    Runs after every other pass has filled the rosters, because both halves of
    the decision need the REAL members — the count that survived portrait and
    vision gating in ``_scraped_team_members``, and their job titles. Neither is
    known at scaffold time, which is why ``page_inference._apply_team_placement``
    deliberately leaves the homepage alone.

    Three outcomes, in order:

    * The roster lives on another page AND home's founders are a small group →
      home shows just the founders. Two faces under "Meet the founders" is a
      trust signal; the same 20-person grid on two pages is not.
    * The roster lives on another page and there is no small founder group →
      home drops the block entirely rather than restating the Team page.
    * Nothing else carries the roster (the directory-entry weave, where home IS
      the roster) → leave it alone. Narrowing here would silently delete people
      the site has nowhere else to show.
    """
    home = next((p for p in plan.pages if p.page_type == "home"), None)
    if home is None:
        return
    team_indexes = [
        idx for idx, block in enumerate(home.blocks) if getattr(block, "kind", None) == "team"
    ]
    if not team_indexes:
        return

    roster_lives_elsewhere = any(
        page is not home and any(getattr(b, "kind", None) == "team" for b in page.blocks)
        for page in plan.pages
    )
    if not roster_lives_elsewhere:
        return

    first = team_indexes[0]
    block = home.blocks[first]
    founders = [m for m in block.members if looks_like_founder_role(m.role)]

    if founders and len(founders) <= FOUNDERS_BAND_MAX:
        # Keep an LLM-written heading; only the generic default is retitled,
        # since "Meet the team" over two founders undersells what it shows.
        heading = "Meet the founders" if block.heading == "Meet the team" else block.heading
        home.blocks[first] = TeamBlock(
            heading=heading,
            subheading=block.subheading,
            members=founders,
        )
        logger.info(
            "Homepage team: narrowed to %d founder(s) — full roster lives on another page",
            len(founders),
        )
        drop_from = 1
    elif len(block.members) > _HOME_ROSTER_MAX:
        logger.info(
            "Homepage team: dropped a %d-member roster already shown on another page",
            len(block.members),
        )
        drop_from = 0
    else:
        drop_from = 1

    # One team band on the homepage at most, whichever branch ran.
    for idx in reversed(team_indexes[drop_from:]):
        del home.blocks[idx]


def _profile_name_patterns(names: list[str]) -> set[str]:
    """Matchable word sequences for scraped people, ≥2 tokens each.

    Two variants per person: the raw name ("ivy tan") and the title-stripped
    one ("sandra cheah" for "Dr. Sandra Cheah"). Single-token remainders are
    skipped — ``_person_name_tokens`` strips honorifics like "Tan", and a
    lone "ivy" would false-positive on unrelated questions.
    """
    patterns: set[str] = set()
    for name in names:
        raw = " ".join(re.findall(r"[a-z0-9]+", (name or "").lower()))
        if len(raw.split()) >= 2:
            patterns.add(raw)
        stripped = _normalized_person_name(name)
        if len(stripped.split()) >= 2:
            patterns.add(stripped)
    return patterns


# The scraper's floor for a card it extracted STRUCTURALLY (0.8 without a role,
# 0.9 with one). Below it sits the page-subject fallback's 0.75 — a real person,
# but paired with their photo positionally rather than by card boundaries.
_CARD_CONFIDENCE = 0.8


def _strip_profile_faq_items(plan: SitePlan, source: SourceContent) -> None:
    """Drop FAQ items manufactured from profile listings ("Who is Ivy Tan…?").

    A directory page's card text flattens to prose, and the model reshapes
    "Name — credentials" pairs into Q&As. Any FAQ question naming a scraped
    person is such an artifact — the roster renders as a team grid instead
    (see ``_ensure_scraped_team_blocks``). Name-scoped, so genuine source
    FAQs survive untouched. Names come from the RAW profile_candidates, not
    ``_profile_pool_for`` — the pool blocks names that appear as headings,
    which on a directory page is every card title.

    Real cards only (0.8+). A page-subject candidate is a positional pairing of
    an h1 with a photo (scraper._page_subject_profile), and deleting a genuine
    question because a page is headed "Annual General Meeting" is a worse
    failure than leaving one manufactured Q&A in place.
    """
    names = [p.name for p in source.profile_candidates or [] if p.confidence >= _CARD_CONFIDENCE]
    for page_src in source.discovered_pages:
        names.extend(
            p.name for p in page_src.profile_candidates or [] if p.confidence >= _CARD_CONFIDENCE
        )
    patterns = _profile_name_patterns(names)
    if not patterns:
        return

    def names_profile(question: str | None) -> bool:
        q = " ".join(re.findall(r"[a-z0-9]+", (question or "").lower()))
        padded = f" {q} "
        return any(f" {p} " in padded for p in patterns)

    for page in plan.pages:
        kept_blocks = []
        for block in page.blocks:
            if getattr(block, "kind", None) != "faq":
                kept_blocks.append(block)
                continue
            kept_items = [item for item in block.items if not names_profile(item.question)]
            if len(kept_items) != len(block.items):
                logger.info(
                    "Stripped %d profile-derived FAQ item(s) on /%s",
                    len(block.items) - len(kept_items), page.slug,
                )
            if kept_items:
                block.items = kept_items
                kept_blocks.append(block)
            # An emptied FAQ block was entirely manufactured — drop it.
        page.blocks = kept_blocks


def _drop_hollow_team_pages(plan: SitePlan) -> None:
    """Remove a Team page outright if no grounded roster ever filled it.

    `align_page_to_scaffold` already sanitizes ungrounded team blocks down to
    nothing, and `_ensure_scraped_team_blocks` only refills them when real
    scraped members exist. A team page that still has no TeamBlock at this
    point has no real people behind it — ship without the page rather than a
    hero-only stub left over from "never ship a blank page".
    """
    keep_slugs = {
        page.slug
        for page in plan.pages
        if page.page_type != "team"
        or any(getattr(block, "kind", None) == "team" for block in page.blocks)
    }
    if len(keep_slugs) == len(plan.pages):
        return
    plan.pages = [page for page in plan.pages if page.slug in keep_slugs]


async def _safe_extract_collections(source: SourceContent):
    """Blog/event entry extraction (content migration) — advisory, never
    load-bearing: any failure just means the site ships without migrated
    entries. Returns None when disabled or nothing was found."""
    if not settings.content_migration_enabled:
        return None
    try:
        cols = await extract_collections(
            source, max_entries=settings.content_migration_max_entries
        )
        return cols if (cols.articles or cols.events) else None
    except Exception:  # noqa: BLE001
        logger.exception("Content-collections extraction failed; continuing without it")
        return None


@router.post("/from-source", response_model=GeneratedSite)
async def generate_from_source(payload: GenerateRequest) -> GeneratedSite:
    """
    LLM picks pages and sections freely. Kept for backward compatibility;
    new flows should use /with-pages for deterministic output.
    """
    try:
        plan = await plan_site(payload.source)
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    # No scaffold in this free-form flow, so it never ran through
    # align_page_to_scaffold's fact-bearing sanitization — apply the same
    # checks directly so fabricated testimonials/awards/clients/stats (e.g. a
    # "John Doe" review) get dropped here too, not just on /with-pages.
    plan = plan.model_copy(
        update={
            "pages": [
                page.model_copy(
                    update={
                        "blocks": sanitize_blocks_against_source(
                            page.blocks, payload.source.raw_text
                        )
                    }
                )
                for page in plan.pages
            ]
        }
    )

    brand = payload.brand
    # Explicit choices win; an industry with a locked mood (e.g. childcare →
    # friendly) then beats the LLM-detected mood, which is advisory only.
    mood = (
        payload.mood_override
        or (brand.mood if brand else None)
        or industry_locked_mood(plan.industry_category)
        or plan.brand_mood
    )
    if brand:
        brand = brand.model_copy(update={"mood": mood})

    seed_hex = (
        (brand.extracted_palette[0] if brand and brand.extracted_palette else None)
        or plan.primary_color_hint
    )
    # Resolved BEFORE the design-language pass, not inline in build_theme: the
    # curated palettes are scheme-specific, so the model has to be shown the dark
    # menu on a dark build or it can only ever pick something that gets discarded.
    color_scheme = resolve_color_scheme(
        payload.color_scheme_override,
        brand.color_scheme if brand else None,
        brand.logo_is_light if brand else None,
        industry=plan.industry_category,
    )
    # Design-language pass: the reasoning model picks a curated palette + font
    # pairing before theme construction. Empty/invalid picks change nothing —
    # build_theme falls back to its deterministic selection.
    language = await generate_design_language(
        brand_name=brand.name if brand else plan.site_name,
        mood=mood,
        industry=plan.industry_category,
        seed_hex=seed_hex,
        color_scheme=color_scheme,
    )
    theme = build_theme(
        seed_hex,
        mood=mood,
        # Per-site font pool + curated-palette selection (auto: curated palette only
        # when there's no usable logo hue, else the brand-driven Tailwind snap).
        font_seed=(brand.name if brand else plan.site_name),
        industry=plan.industry_category,
        palette_mode="auto",
        color_scheme=color_scheme,
        palette_choice=language.palette,
        font_choice=language.font_pairing,
        # Diversity: steer the curated pick off palettes recent sites used
        # (rotates within the fit group only; fail-open empty set).
        avoid_palettes=await recent_choices(
            "palette", site_key=(brand.name if brand else plan.site_name)
        ),
    )
    theme.hero_background_height = resolve_hero_height(
        payload.hero_height,
        language.hero_height,
        mood=mood,
        industry=plan.industry_category,
    )

    scraped_images, scraped_metadata = _image_pool_for(payload.source)
    annotations = await _annotate_source_images(payload.source, scraped_metadata)
    _enrich_plan_profile_photos(plan, payload.source, annotations)
    _ensure_scraped_team_blocks(plan, payload.source, annotations)

    market_cue, place_cue = _market_cues_for(payload.source)
    collections_task = asyncio.create_task(_safe_extract_collections(payload.source))
    site = await plan_to_site(
        plan,
        brand=brand,
        theme=theme,
        scraped_images=scraped_images,
        scraped_metadata=scraped_metadata,
        page_images=_page_images_by_slug(payload.source),
        contact=payload.contact,
        market_cue=market_cue,
        place_cue=place_cue,
        social_links=_social_links_for(payload.source),
        header_override=payload.header_archetype,
        footer_override=payload.footer_archetype,
    )
    site.collections = await collections_task
    return site


# --- scaffolded generate (new — driven by the page picker) ---------------------


class GenerateWithPagesRequest(BaseModel):
    """Scaffolded generate. The user has chosen the page set; we ask the LLM
    to write copy for the chosen pages' sections, then bolt on legal pages
    (boilerplate, no LLM) and build the themed site.

    If `detected_brand` is passed (from the /pages/recipe response), we skip
    the brand-detection LLM call entirely — saves ~10-30s per generation.
    """

    source: SourceContent
    selected_pages: list[PageScaffold] = Field(min_length=1)
    industry: IndustryCategoryLiteral = "other"
    brand: BrandIdentity | None = None
    mood_override: BrandMood | None = None
    color_scheme_override: str | None = None  # "light" | "dark"; overrides the logo-based default
    # Full-screen vs bounded photo hero, site-wide. None = "Auto": defer to the
    # design-brain pick, then the mood/industry default (see resolve_hero_height).
    hero_height: HeroBackgroundHeight | None = None
    contact: dict[str, str] | None = None
    jurisdiction: str | None = None
    legal_contact_email: str | None = None
    detected_brand: DetectedBrand | None = None
    # Explicit chrome pins — win over the design director's fit/seed/diversity
    # pick (see design_director.compose_design_manifest). None → let it decide.
    header_archetype: HeaderArchetype | None = None
    footer_archetype: FooterArchetype | None = None


@router.post("/with-pages", response_model=GeneratedSite)
async def generate_with_pages(payload: GenerateWithPagesRequest) -> GeneratedSite:
    # Split scaffolds: LLM-generated content pages vs. boilerplate legal pages.
    # Translated mirrors are held out of the content pass entirely — they're
    # cloned from their counterpart's finished plan further down, so paying the
    # planner to write them again would cost a full generation per language AND
    # let the two versions drift apart visually.
    content_scaffolds = [
        s for s in payload.selected_pages if not s.is_legal and not s.locale
    ]
    translation_scaffolds = [
        s for s in payload.selected_pages if not s.is_legal and s.locale
    ]
    legal_scaffolds = [s for s in payload.selected_pages if s.is_legal]

    if not content_scaffolds:
        raise HTTPException(
            status_code=400,
            detail="At least one non-legal page (e.g. Home) must be selected.",
        )

    # Skip the second LLM call if the frontend already gave us the detection
    # from /api/pages/recipe. Falls back to the cached detector — if the recipe
    # endpoint ran in the same 5-min window the result is already memoised.
    if payload.detected_brand is not None:
        detected = payload.detected_brand
    else:
        try:
            with stage("brand_detection"):
                detected = await detect_brand_cached(payload.source)
        except LlmError as exc:
            raise HTTPException(
                status_code=502, detail=f"Brand detection failed: {exc}"
            ) from exc

    # Determine mood / industry / colour seed with override precedence:
    #   1. user upload / explicit selection
    #   2. industry-locked mood (a design brief that pins the visual language)
    #   3. detected
    #   4. defaults
    # `payload.industry` defaults to "other" — the frontend's catch-all when the
    # user never touched the override dropdown — so treat "other" as *unset* and
    # defer to the (more specific) detected industry. Otherwise a detected
    # childcare/etc. never reaches the theme and its light-only brief is lost
    # (the Glorykids kindergarten rendering dark). A specific selection still wins.
    industry = (
        payload.industry
        if payload.industry and payload.industry != "other"
        else detected.industry_category
    )
    mood = (
        payload.mood_override
        or (payload.brand.mood if payload.brand else None)
        or industry_locked_mood(industry)
        or detected.brand_mood
    )

    brand = payload.brand or BrandIdentity(
        name=detected.site_name,
        tagline=detected.tagline,
        mood=mood,
        industry=industry,
    )
    if not brand.mood:
        brand = brand.model_copy(update={"mood": mood})

    seed_hex = (
        (brand.extracted_palette[0] if brand.extracted_palette else None)
        or detected.primary_color_hint
    )
    # Resolved BEFORE the design-language pass, not inline in build_theme: the
    # curated palettes are scheme-specific, so the model has to be shown the dark
    # menu on a dark build or it can only ever pick something that gets discarded.
    color_scheme = resolve_color_scheme(
        payload.color_scheme_override,
        brand.color_scheme,
        brand.logo_is_light,
        industry=industry,
    )
    # Design-language pass: the reasoning model picks a curated palette + font
    # pairing before theme construction. Empty/invalid picks change nothing —
    # build_theme falls back to its deterministic selection.
    with stage("design_language"):
        language = await generate_design_language(
            brand_name=brand.name,
            mood=mood,
            industry=industry,
            seed_hex=seed_hex,
            color_scheme=color_scheme,
        )
    theme = build_theme(
        seed_hex,
        mood=mood,
        font_seed=brand.name,
        industry=industry,
        palette_mode="auto",
        color_scheme=color_scheme,
        palette_choice=language.palette,
        font_choice=language.font_pairing,
        # Diversity: steer the curated pick off palettes recent sites used
        # (rotates within the fit group only; fail-open empty set).
        avoid_palettes=await recent_choices("palette", site_key=brand.name),
    )
    theme.hero_background_height = resolve_hero_height(
        payload.hero_height,
        language.hero_height,
        mood=mood,
        industry=industry,
    )

    # Announcement/quick-links strap: claim it BEFORE planning so its text is
    # out of raw_text (the LLM must not also narrate it into a paragraph);
    # the strap itself is re-injected as a linkbar section after alignment.
    linkbar_cluster = find_linkbar_cluster(payload.source)
    if linkbar_cluster is not None:
        strip_linkbar_lines(payload.source, linkbar_cluster)

    # Start downloading the images the vision pass will judge while the content
    # LLM owns the GPU — prefetch is pure network/CPU, so it comes off the
    # critical path for free. The GPU-bound vision judging still runs after
    # content generation (two models on one 16GB GPU would thrash swaps).
    scraped_images, scraped_metadata = _image_pool_for(payload.source)
    page_images = _page_images_by_slug(payload.source)
    profiles = _profile_pool_for(payload.source)
    profile_urls = [p.photo_url for p in profiles if p.photo_url]
    prefetch_task = asyncio.create_task(
        prefetch_image_pool(scraped_metadata, extra_urls=profile_urls)
    )
    # OCR text screening rides the same window for the same reason: it is pure
    # CPU, so it overlaps the GPU-bound content pass instead of adding to the
    # wall clock. Unlike the vision judging below it does NOT contend for the
    # GPU, which is why it can run here rather than after generation.
    ocr_task = asyncio.create_task(_screen_source_images_for_text(scraped_metadata))

    # Scaffolded LLM call — produces PagePlans for content_scaffolds in lockstep order.
    # This is the heaviest LLM pass (it writes all page copy); time it so the
    # breakdown shows whether content generation, not design/images, dominates.
    # Also hands back the scaffold→source routing it used, so alignment verifies
    # fact-bearing content against the actual page it was grounded in.
    try:
        with stage("content_generation"):
            scaffolded, source_map = await plan_site_with_scaffolds(
                payload.source, detected, content_scaffolds
            )
    except LlmError as exc:
        prefetch_task.cancel()
        ocr_task.cancel()
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc

    # Per-batch failures no longer abort the run (see planner._run_item_safe) —
    # the affected pages ship with scaffold defaults. Surface which ones, so a
    # thin page is traceable to a failed call rather than to bad source content.
    if scaffolded.degraded_slugs:
        logger.warning(
            "%d of %d page(s) fell back to scaffold defaults after a failed "
            "content call: %s",
            len(scaffolded.degraded_slugs),
            len(content_scaffolds),
            ", ".join(f"/{s}" for s in scaffolded.degraded_slugs),
        )

    # Build the SitePlan that schema_builder consumes.
    plan = SitePlan(
        site_name=scaffolded.site_name or detected.site_name,
        tagline=scaffolded.tagline or detected.tagline,
        brand_summary=scaffolded.brand_summary or detected.brand_summary,
        brand_mood=mood,
        industry_category=industry,
        primary_color_hint=scaffolded.primary_color_hint or seed_hex,
        pages=_align_pages_to_scaffolds(
            scaffolded.pages,
            content_scaffolds,
            brand_name=scaffolded.site_name or detected.site_name or "Untitled",
            source_map=source_map,
        ),
    )
    # FAQ items the model manufactured out of profile listings are dropped
    # before any rendering — the roster ships as a team grid, not as Q&As.
    _strip_profile_faq_items(plan, payload.source)
    # Hub-page guarantee: every child page is reachable from its parent's body,
    # not just from the footer. Runs after alignment so appended items survive.
    _ensure_hub_child_links(plan.pages)
    if linkbar_cluster is not None:
        _inject_linkbar(plan.pages, linkbar_cluster)
    _inject_downloads(plan.pages, payload.source)

    # Legal pages will be appended after plan_to_site, but they need to appear in
    # the footer nav. Pass their titles + slugs through.
    extra_footer_nav: list[tuple[str, str]] = [
        (s.title, f"/{s.slug}") for s in legal_scaffolds
    ]

    # Generate themed site (body + header + footer + theme)
    prefetched: dict[str, str] = {}
    try:
        prefetched = await prefetch_task
    except Exception:  # noqa: BLE001 — prefetch is advisory, never load-bearing
        logger.exception("Vision image prefetch failed; continuing without it")
    # Should already be done — it started with the prefetch and the content LLM
    # ran meanwhile. Awaited here only so a slow scrape can't leave the flags
    # half-written while sections resolve their backgrounds.
    await ocr_task
    annotations = await _annotate_source_images(
        payload.source, scraped_metadata, prefetched=prefetched, profiles=profiles
    )
    _enrich_plan_profile_photos(plan, payload.source, annotations, profiles=profiles)
    team_section_slugs = {s.slug for s in content_scaffolds if "team" in s.sections}
    # Directory pages: scaffolds with a team section whose grounding source is
    # itself a profile roster. Intersecting with the team scaffolds guarantees
    # a contact/faq page fallback-grounded on the directory's text never grows
    # a roster it shouldn't have.
    directory_slugs = {
        slug
        for slug, src in source_map.items()
        if len(src.profile_candidates or []) >= DIRECTORY_MIN_PROFILES
    } & team_section_slugs
    _ensure_scraped_team_blocks(
        plan,
        payload.source,
        annotations,
        team_section_slugs=team_section_slugs,
        profiles=profiles,
        source_map=source_map,
        directory_slugs=directory_slugs,
    )
    _drop_hollow_team_pages(plan)
    # After the last pass that can remove a page, so a member's link is checked
    # against the pages the site actually ships.
    _prune_dead_profile_links(plan)

    # Resolve LLM-bound image refs (block.image_ref → block.image_url) against
    # the same per-page photo lists the planner prompt showed the model.
    bound_image_urls = bind_image_refs(plan.pages, source_map)

    # Translated mirrors clone their counterpart HERE — after image refs are
    # bound and the roster/team passes have run — so a clone inherits the exact
    # photos and blocks the source-language page ended up with, not the ones it
    # was planned with.
    if translation_scaffolds:
        with stage("translations"):
            plan.pages.extend(
                await build_translated_pages(
                    plan.pages,
                    translation_scaffolds,
                    _translation_sources(payload.source),
                )
            )

    market_cue, place_cue = _market_cues_for(payload.source)
    collections_task = asyncio.create_task(_safe_extract_collections(payload.source))
    site = await plan_to_site(
        plan,
        brand=brand,
        theme=theme,
        scraped_images=scraped_images,
        scraped_metadata=scraped_metadata,
        page_images=page_images,
        contact=payload.contact,
        extra_footer_nav=extra_footer_nav,
        market_cue=market_cue,
        place_cue=place_cue,
        social_links=_social_links_for(payload.source),
        reserved_image_urls=bound_image_urls,
        header_override=payload.header_archetype,
        footer_override=payload.footer_archetype,
    )
    site.collections = await collections_task

    # Bolt on legal pages from boilerplate
    contact_email = (
        payload.legal_contact_email
        or (payload.contact or {}).get("email")
        or "hello@example.com"
    )
    jurisdiction = payload.jurisdiction or "your country / state"
    for legal in legal_scaffolds:
        if legal.page_type == "privacy":
            site.pages.append(
                build_privacy_page(
                    plan.site_name, theme, contact_email=contact_email, jurisdiction=jurisdiction
                )
            )
        elif legal.page_type == "terms":
            site.pages.append(
                build_terms_page(
                    plan.site_name, theme, contact_email=contact_email, jurisdiction=jurisdiction
                )
            )

    return site


def _align_pages_to_scaffolds(
    llm_pages: list[PagePlan],
    scaffolds: list[PageScaffold],
    *,
    brand_name: str = "Untitled",
    source_map: dict[str, SourceContent] | None = None,
) -> list[PagePlan]:
    """
    Make sure the LLM output respects scaffold order + slugs + section structure
    even if it drifts.

    Two layers of enforcement:
      1. Page identity: match LLM pages to scaffold by slug/title; force scaffold
         slug/title/is_homepage. If the LLM dropped a page entirely, synthesise
         an empty one (it'll be filled by section defaults in step 2).
      2. Section structure: for each page, call align_page_to_scaffold which
         reorders blocks to match scaffold.sections, drops extras, and pads
         missing kinds with sane defaults.
    """
    by_slug = {p.slug: p for p in llm_pages}
    by_title = {p.title.lower(): p for p in llm_pages}
    aligned: list[PagePlan] = []
    for s in scaffolds:
        match = by_slug.get(s.slug) or by_title.get(s.title.lower())
        if match is None:
            # Synthesise a minimal page; the section defaults will populate it.
            match = PagePlan(
                page_type=s.page_type,  # type: ignore[arg-type]
                slug=s.slug,
                title=s.title,
                description=s.description or "",
                is_homepage=s.is_homepage,
                blocks=[],
                seo_title=f"{s.title} — {brand_name}",
                seo_description=s.description or "",
                parent_slug=s.parent_slug,
                menu_hidden=s.menu_hidden,
            )

        # Force scaffold identity (including hierarchy + nav priority)
        match = match.model_copy(
            update={
                "slug": s.slug,
                "title": s.title,
                "is_homepage": s.is_homepage,
                "parent_slug": s.parent_slug,
                "nav_rank": s.nav_rank,
                "from_source": s.from_source,
                "menu_hidden": s.menu_hidden,
            }
        )
        # Enforce section structure (+ fact-grounding for fact-bearing kinds
        # like testimonials, when this scaffold's source page is known)
        page_source = source_map.get(s.slug) if source_map else None
        match = align_page_to_scaffold(
            match,
            s,
            brand_name=brand_name,
            source_text=page_source.raw_text if page_source else None,
        )
        aligned.append(match)
    return aligned


def _translation_sources(source: SourceContent) -> dict[str, SourceContent]:
    """slug → crawled page, so a clone can be filled with the owner's own words
    in that language rather than a re-translation of our copy."""
    out: dict[str, SourceContent] = {}
    for page in source.discovered_pages:
        slug = (page.url_path or "").strip("/").lower()
        if slug:
            out[slug] = page
    return out


def _social_links_for(source: SourceContent) -> list[tuple[str, str]]:
    return [(link.label, link.href) for link in source.social_links]


def _inject_linkbar(pages: list[PagePlan], cluster: LinkCluster) -> None:
    """Recreate the source's announcement strap as a linkbar section.

    Inserted right after the homepage hero — where these straps live on real
    sites. Links are kept only when they resolve inside the generated site
    (a generated page's slug, or a homepage anchor); a strap reduced to fewer
    than two working links is dropped rather than rendered half-broken.

    """
    home = next((p for p in pages if p.is_homepage), None)
    if home is None:
        return

    generated_slugs = {p.slug for p in pages}
    links: list[LinkBarLink] = []
    for link in cluster.links[:6]:
        href = link.href
        path = href.split("#", 1)[0].strip("/").lower()
        is_home_anchor = "#" in href and path == ""
        if not (is_home_anchor or path in generated_slugs):
            continue
        links.append(LinkBarLink(label=link.label, href=href))
    if len(links) < 2:
        return

    block = LinkBarBlock(
        label=cluster.context_label or None,
        links=links,
    )
    hero_index = next(
        (i for i, b in enumerate(home.blocks) if b.kind == "hero"), None
    )
    insert_at = hero_index + 1 if hero_index is not None else 0
    home.blocks.insert(insert_at, block)


def _page_by_url_path(pages: list[PagePlan]) -> dict[str, PagePlan]:
    """Map each generated page's slug to its PagePlan, keyed the same way
    ``site_relative_href``/``_path_to_slug`` normalize a source url_path
    (strip surrounding slashes, lowercase; empty string = homepage)."""
    return {p.slug.strip("/").lower(): p for p in pages}


def _inject_downloads(pages: list[PagePlan], source: SourceContent) -> None:
    """Recreate each page's scraped document cards (e.g. a brochure offered in
    EN/ZH/MS, or a resource library) as ONE downloads section per page.

    Every DocumentCardCandidate found on a page (scraper._extract_document_cards
    → SourceContent.document_cards) becomes one item of a SINGLE DownloadsBlock
    for that page — never split across multiple blocks — so a document's
    title/thumbnail stay grouped with its own download links, matching how the
    source page presented them.

    Unlike ``_inject_linkbar``, hrefs here are SUPPOSED to point off the
    generated site's own page set — a document doesn't get its own generated
    page — so there's no generated-slug gate. image_url/href are already
    absolute (resolved at extraction time), which push_orchestrator later
    re-hosts onto the CMS. Inserted right after the hero (scraper._strip_
    document_card_lines already kept the LLM from also narrating this content
    into its own services/about section, so there's nothing to duplicate).
    """
    pages_by_path = _page_by_url_path(pages)
    for source_page in [source, *source.discovered_pages]:
        if not source_page.document_cards:
            continue
        slug = (source_page.url_path or "").strip("/").lower()
        page = pages_by_path.get(slug)
        if page is None:
            continue
        items = [
            DownloadItem(
                title=card.title,
                image_url=card.image_url,
                links=[
                    DownloadLink(label=link.label, href=link.href)
                    for link in card.links
                ],
            )
            for card in source_page.document_cards
        ]
        # Right after the hero — same placement _inject_linkbar uses — not
        # appended at the end, which would land it after an unrelated closing
        # CTA. A resources/downloads section is top-of-page content.
        hero_index = next(
            (i for i, b in enumerate(page.blocks) if b.kind == "hero"), None
        )
        insert_at = hero_index + 1 if hero_index is not None else 0
        page.blocks.insert(insert_at, DownloadsBlock(items=items))


def _ensure_hub_child_links(pages: list[PagePlan]) -> None:
    """Make every parent page's services block cover all of its child pages.

    schema_builder already cross-links service items to children whose titles
    match (``_match_child_by_title``); what it can't do is invent an item for a
    child the LLM never mentioned. Here we append a minimal linked item per
    uncovered child, within the block's max-items bound. Pages without a
    services block are left alone — their children stay reachable via the
    footer columns.
    """
    children_by_parent: dict[str, list[PagePlan]] = {}
    for p in pages:
        if p.parent_slug:
            children_by_parent.setdefault(p.parent_slug, []).append(p)
    if not children_by_parent:
        return

    # Lazy import: schema_builder is heavy and generate.py already depends on
    # it at call time via plan_to_site.
    from app.services.schema_builder import ChildPageRef, _match_child_by_title

    by_slug = {p.slug: p for p in pages}
    for parent_slug, kids in children_by_parent.items():
        parent = by_slug.get(parent_slug)
        if parent is None:
            continue
        services = next(
            (b for b in parent.blocks if isinstance(b, ServicesBlock)), None
        )
        if services is None:
            continue
        for kid in kids:
            ref = ChildPageRef(slug=kid.slug, title=kid.title, page_type=kid.page_type)
            covered = any(
                _match_child_by_title(item.title, [ref]) is not None
                for item in services.items
            )
            if covered:
                continue
            if len(services.items) >= 8:  # ServicesBlock max_length
                break
            services.items.append(
                ServiceItem(
                    title=kid.title,
                    description=kid.description
                    or f"Find out more about {kid.title.lower()}.",
                    cta_label="Learn more",
                    cta_href=f"/{kid.slug}",
                )
            )


# --- debug ---------------------------------------------------------------------


@router.post("/plan-only", response_model=SitePlan)
async def plan_only(source: SourceContent) -> SitePlan:
    """Debug endpoint: returns the raw SitePlan without converting to BuilderElement trees."""
    try:
        plan = await plan_site(source)
        _enrich_plan_profile_photos(plan, source)
        _ensure_scraped_team_blocks(plan, source)
        return plan
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=f"LLM error: {exc}") from exc
