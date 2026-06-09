"""
Turns extracted SourceContent into a SitePlan via the LLM.

Two LLM modes:
1. `detect_brand(source)` — small call returning brand metadata only
   (name, mood, industry, tagline, color hint). Used to seed the page picker.
2. `plan_site_with_scaffolds(source, brand, scaffolds)` — given a list of
   PageScaffolds the user picked, write copy for each section in each page.
   The structure is *not* up to the LLM; only the words are.

The legacy `plan_site` is preserved for backward compatibility with the existing
free-form generate endpoint.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from pydantic import BaseModel, Field

from app.models.content_blocks import (
    BrandMood,
    ContentBlock,
    IndustryCategoryLiteral,
    MenuCategory,
    PagePlan,
    SitePlan,
    SourceContent,
)
from app.models.industry import PageScaffold
from app.services.llm import OllamaClient, get_llm
from app.services.source_router import (
    excerpt_for_prompt,
    match_scaffolds_to_pages,
    split_raw_text,
)

logger = logging.getLogger(__name__)


# --- legacy free-form prompt (kept for back-compat with /from-source) ----------


LEGACY_SYSTEM_PROMPT = """You are a senior web editor and SEO copywriter.

You are given raw content extracted from a source (a website or document) belonging to a business.
Your job: REWRITE that content into a cleaner, better-organised, search-optimised website. You
improve the language; you do not invent a new business.

Hard rules:
- Reply with ONE JSON object matching the schema. No markdown, no commentary.
- Every page MUST have a hero block first. The homepage MUST end with a cta or contact block.
- Headlines: benefit-led, specific, 6-12 words, drawn from what the business actually does.
  NEVER generic ("Welcome to our site", "About us").
- CTAs: action verbs ("Book a call", "Get a quote"), never "Click here" or "Learn more" alone.
- FIDELITY: use ONLY facts present in the source. You may fix grammar, tighten wording, improve
  flow, and add SEO copy — but NEVER fabricate testimonials, reviewer names, statistics, prices,
  awards, certifications, team members, contact details, hours, or FAQ specifics. If the source
  doesn't support a section, leave that section out rather than invent content for it.
- Preserve real proper nouns, prices, contact details from the source verbatim.
- SEO titles 50-60 chars. SEO descriptions 140-160 chars, built from the real subject matter.
- ALWAYS produce specific, visual image_query / background_query / avatar_query phrases
  (these describe stock imagery, not facts).

Pick `industry_category` from: restaurant, agency, saas, professional-services, ecommerce,
consultancy, nonprofit, personal, other.
Pick `brand_mood` from: modern, luxury, friendly, technical, editorial, playful.

For a typical small business produce 4 pages: home, services, about, contact.
"""


def _build_user_prompt(source: SourceContent) -> str:
    truncated_text = source.raw_text[:12000]
    return json.dumps(
        {
            "source_kind": source.source_kind,
            "source_ref": source.source_ref,
            "source_title": source.title,
            "source_description": source.description,
            "headings": source.headings[:50],
            "sample_links": source.links[:30],
            "sample_image_alts": source.images[:20],
            "raw_text": truncated_text,
        },
        ensure_ascii=False,
    )


async def plan_site(source: SourceContent, llm: OllamaClient | None = None) -> SitePlan:
    """Legacy: LLM chooses pages and sections freely."""
    client = llm or get_llm()
    return await client.chat_json(
        system_prompt=LEGACY_SYSTEM_PROMPT,
        user_prompt=_build_user_prompt(source),
        schema=SitePlan,
        # Faithful rewrite — keep it close to the source, not creative.
        temperature=0.3,
    )


# --- brand detection (small, fast LLM call) -------------------------------------


class DetectedBrand(BaseModel):
    """Compact brand summary used to populate the page picker."""

    site_name: str
    tagline: str | None = None
    brand_summary: str = ""
    brand_mood: BrandMood = "modern"
    industry_category: IndustryCategoryLiteral = "other"
    primary_color_hint: str | None = None


DETECT_BRAND_PROMPT = """You are a brand and industry analyst.
You will read content extracted from a website or document and identify the business.

Reply with ONE JSON object matching this schema — no markdown, no commentary:

{
  "site_name": string,
  "tagline": string|null,           // existing tagline if present, else a short benefit-led one (≤8 words)
  "brand_summary": string,           // 1-2 sentence description of what this business does
  "brand_mood": "modern"|"luxury"|"friendly"|"technical"|"editorial"|"playful",
       // modern    → SaaS, fintech, tech
       // luxury    → hospitality, jewellery, real estate, premium
       // friendly  → consumer, wellness, lifestyle
       // technical → engineering, B2B, dev tools
       // editorial → media, agencies, portfolios
       // playful   → entertainment, food, kids, gaming
  "industry_category": "restaurant"|"agency"|"saas"|"professional-services"
                       |"ecommerce"|"consultancy"|"nonprofit"|"personal"|"other",
       // restaurant            → restaurants, cafés, bars, food
       // agency                → creative / marketing / design agencies, studios
       // saas                  → software products, apps, platforms
       // professional-services → legal, dental, medical, accounting
       // ecommerce             → online stores, product brands
       // consultancy           → strategy, management, niche advisory
       // nonprofit             → charities, NGOs, foundations
       // personal              → solo professionals, freelancers, portfolios
       // other                 → anything that doesn't fit cleanly above
  "primary_color_hint": string|null   // hex like "#2563eb" if you can infer brand colour, else null
}
"""


async def detect_brand(
    source: SourceContent, llm: OllamaClient | None = None
) -> DetectedBrand:
    client = llm or get_llm()
    return await client.chat_json(
        system_prompt=DETECT_BRAND_PROMPT,
        user_prompt=_build_user_prompt(source),
        schema=DetectedBrand,
        temperature=0.3,
    )


# In-process cache for detect_brand. The recipe endpoint and the generate
# endpoint both call this for the same source — back-and-forward navigation
# in the picker would otherwise burn a fresh LLM call each time.
#
# 5 min TTL matches the scrape cache so a single Back-Forward bounce never
# re-hits the model.
_DETECT_BRAND_CACHE: dict[str, tuple[float, DetectedBrand]] = {}
_DETECT_BRAND_TTL = 300


def _source_fingerprint(source: SourceContent) -> str:
    """Stable per-source key. Includes source_ref + title + full raw_text so
    edits made on the scrape-preview screen invalidate the cache automatically.
    """
    h = hashlib.sha256()
    h.update(source.source_kind.encode("utf-8", "ignore"))
    h.update(b"|")
    h.update(source.source_ref.encode("utf-8", "ignore"))
    h.update(b"|")
    h.update((source.title or "").encode("utf-8", "ignore"))
    h.update(b"|")
    h.update(source.raw_text.encode("utf-8", "ignore"))
    return h.hexdigest()[:32]


async def detect_brand_cached(
    source: SourceContent, llm: OllamaClient | None = None
) -> DetectedBrand:
    """detect_brand with a 5-minute per-source in-process cache."""
    key = _source_fingerprint(source)
    now = time.time()
    cached = _DETECT_BRAND_CACHE.get(key)
    if cached and (now - cached[0]) < _DETECT_BRAND_TTL:
        logger.debug("detect_brand cache hit for fingerprint %s", key[:8])
        return cached[1]

    result = await detect_brand(source, llm=llm)
    _DETECT_BRAND_CACHE[key] = (now, result)
    # Light GC so the dict doesn't grow unbounded across long sessions.
    if len(_DETECT_BRAND_CACHE) > 64:
        expired = [
            k for k, (ts, _) in _DETECT_BRAND_CACHE.items()
            if (now - ts) > _DETECT_BRAND_TTL
        ]
        for k in expired:
            _DETECT_BRAND_CACHE.pop(k, None)
    return result


# --- scaffold-driven planning (page picker output) ------------------------------


class ScaffoldedSitePlan(BaseModel):
    """SitePlan where the LLM was constrained to a structure we provided."""

    site_name: str
    tagline: str | None = None
    brand_summary: str = ""
    brand_mood: BrandMood = "modern"
    industry_category: IndustryCategoryLiteral = "other"
    primary_color_hint: str | None = None
    pages: list[PagePlan]


def _scaffolds_to_prompt_payload(
    scaffolds: list[PageScaffold],
    parent_context: dict[str, dict] | None = None,
    source_map: dict[str, SourceContent] | None = None,
    text_overrides: dict[str, str] | None = None,
) -> list[dict]:
    """Compact format the LLM consumes: list of pages, each with a list of section kinds
    AND its own page-specific source excerpt (from the crawler's per-page content).

    ``parent_context`` maps parent slug → {title, headline, tagline} from
    already-generated parent pages, so child pages can echo the parent's tone.

    ``source_map`` (from source_router.match_scaffolds_to_pages) maps each
    scaffold's slug to the scraped page that should ground its content. This
    is what stops the LLM writing /services copy from the homepage's text.
    """
    payload: list[dict] = []
    for s in scaffolds:
        if s.is_legal:
            continue
        entry: dict = {
            "page_type": s.page_type,
            "slug": s.slug,
            "title": s.title,
            "is_homepage": s.is_homepage,
            "required_sections": s.sections,
        }
        if s.parent_slug is not None:
            entry["parent_slug"] = s.parent_slug
            if parent_context and s.parent_slug in parent_context:
                entry["parent_context"] = parent_context[s.parent_slug]
        if source_map is not None and s.slug in source_map:
            override = text_overrides.get(s.slug) if text_overrides else None
            entry["page_source"] = excerpt_for_prompt(
                source_map[s.slug],
                max_chars=MAX_CHARS_PER_CALL,
                text_override=override,
            )
        payload.append(entry)
    return payload


def _scaffold_system_prompt() -> str:
    return """You are a senior web editor and SEO copywriter. Your job is to
REWRITE the business's own existing content into a cleaner, better-organised,
search-optimised website — NOT to invent a new business.

Think of yourself as a skilled editor handed the company's real copy. You may
freely improve it: fix grammar and spelling, tighten wording, improve flow and
clarity, make headlines benefit-led, and add SEO titles/descriptions/keywords.
But every CONCRETE FACT must come from the source. You are polishing what is
already there, never inventing what isn't.

You will receive:
1. `source`: raw content extracted from the business's entry page (homepage / cover)
2. `brand`: detected brand metadata (name, mood, industry, etc.)
3. `pages_requested`: an EXACT list of pages to produce. EACH page entry may
   include a `page_source` field carrying THAT specific page's scraped content
   (title, headings, raw_text). When present, it is the FACTUAL BASIS for the
   page's copy — preserve real names, services, prices, addresses, hours, and
   numbers. Rephrase and reorganise for clarity and SEO, but never state a fact
   the source does not support, and never contradict the source.

If a page has no `page_source`, fall back to the top-level `source` and the
brand summary — still grounded in real content only.

═══════════════════════════════════════════════════════════════════════════
FIDELITY RULES — these override every other instruction below:
- Use ONLY facts present in the source. Improving language is encouraged;
  inventing information is forbidden.
- NEVER fabricate any of the following. If the source does not contain it,
  it does not go on the page:
    • testimonial quotes, reviewer names, ratings or star counts
    • statistics, percentages, "trusted by N customers", years-in-business
    • prices, plan tiers, discounts
    • awards, certifications, partner/client logos or names
    • team member names, titles, or bios
    • addresses, phone numbers, emails, opening hours
    • FAQ answers that assert specifics (policies, timelines, guarantees)
    • menu items or dishes and their prices
- You MAY paraphrase, summarise, reorder, and sharpen real content. You MAY
  write general benefit statements that follow directly from what the business
  actually says it does. You MAY NOT add specifics that aren't in the source.

OMITTING SECTIONS (important):
- A section is requested in `required_sections`, but if the source contains NO
  facts to ground it, OMIT that section entirely — leave it out of `blocks`.
  A shorter, honest page beats a padded one with invented content.
- Example: `required_sections` includes "testimonials" but the source has no
  reviews → do NOT output a testimonials block. Same for faq, pricing, team,
  menu, gallery, process, stats when the source is silent.
- Only emit a section when you can fill it with at least its minimum number of
  REAL, source-grounded items. If you can't, omit it.
- hero, about, contact and cta are the exception: always keep these when
  requested (hero restates the page's real topic; cta/contact are calls to
  action, not factual claims).
═══════════════════════════════════════════════════════════════════════════

OUTPUT STRUCTURE — follow this skeleton EXACTLY. Every page MUST have a "blocks" array:

{
  "site_name": "string",
  "tagline": "string or null",
  "brand_summary": "1-2 sentence string",
  "brand_mood": "modern|luxury|friendly|technical|editorial|playful",
  "industry_category": "restaurant|agency|saas|professional-services|ecommerce|consultancy|nonprofit|personal|other",
  "primary_color_hint": "#hexcolor or null",
  "pages": [
    {
      "page_type": "home",
      "slug": "string",
      "title": "string",
      "description": "string",
      "is_homepage": true,
      "seo_title": "string (50-60 chars)",
      "seo_description": "string (140-160 chars)",
      "seo_keywords": ["keyword1", "keyword2"],
      "blocks": [
        { "kind": "hero", "headline": "...", "primary_cta_label": "...", "primary_cta_href": "...", "image_query": "...", "layout": "split" },
        { "kind": "features", "heading": "...", "items": [{ "title": "...", "description": "..." }] }
      ]
    }
  ]
}

For each page in `pages_requested`:
- Use the given `slug`, `title`, `is_homepage` verbatim.
- Emit the sections from `required_sections` you can ground in the source, in
  that SAME ORDER, and ONLY those kinds. Drop any section the source can't
  support (see OMITTING SECTIONS). Do not add kinds that weren't requested.
- Each block must match the schema for its kind (see below).
- If `parent_slug` is set, this is a SUB-PAGE under another page. When
  `parent_context` is present, mirror the parent's tone and value prop — do
  not restate what the company does in the hero; instead deep-dive on this
  specific offering. Use the parent's voice consistently.
- INTERIOR-PAGE HEROES (any page that is NOT the homepage) are orientation
  headers, not conversion blocks: focus on a strong eyebrow + headline +
  subheadline. The layout decides the hero CTA on its own (a scroll cue for
  full-bleed heroes, none for compact ones), so do NOT add a hero CTA that
  points back at the same page's content (e.g. a "Testimonials" button on the
  testimonials page). The real conversion ask belongs in the page's closing
  `cta` block. You may still set primary_cta on the homepage hero.

Hard rules:
- Reply with ONE JSON object. No markdown, no commentary.
- Headlines: benefit-led, specific, 6-12 words, drawn from what the business
  actually does. NEVER generic ("Welcome to our site").
- CTAs: action verbs ("Book a call", "Get a quote"), never "Click here" alone.
- Preserve real proper nouns, prices, contact details from the source verbatim.
- SEO titles 50-60 chars. SEO descriptions 140-160 chars. Build them from the
  page's real subject matter and keywords — do not invent claims to fill length.
- ALWAYS populate visual image_query / background_query / avatar_query / photo_query
  fields with specific, concrete 2-6 word phrases (these describe stock imagery,
  not facts, so they're always fine to write).
- ITEM COUNTS: include as many REAL, source-grounded items as the source
  supports, up to the schema maximum. Do NOT pad to a minimum with invented
  items. If you can't reach a section's minimum with real content, OMIT the
  whole section (see OMITTING SECTIONS above).
- Every string field you DO emit (headline, cta_label, heading, etc.) MUST be a
  non-null string — but it's better to omit an optional block than to fill it
  with a placeholder.

Block schemas (give the block's `kind` exactly).
(Counts below are MAXIMUMS plus the floor needed to keep the section. Fill with
real items only; if you can't reach the floor with real content, omit the block.)
- hero: { kind:"hero", eyebrow?, headline, subheadline?, primary_cta_label, primary_cta_href,
          secondary_cta_label?, secondary_cta_href?, image_alt?, image_query, layout }
- features: { kind:"features", heading, subheading?, items: [{title, description}] }  (1-6 real items)
- services: { kind:"services", heading, subheading?, items: [{title, description, cta_label?, cta_href?}] }  (1-8 real items)
- testimonials: { kind:"testimonials", heading, items: [{quote, author, role?, avatar_query?}] }  (real reviews only — omit if none)
- about: { kind:"about", heading, body, image_alt?, image_query }
- faq: { kind:"faq", heading, items: [{question, answer}] }  (1-20 real Q&As — include ALL Q&As present in the source, omit block only if none)
- cta: { kind:"cta", headline, subheadline?, cta_label, cta_href, background_query }
- contact: { kind:"contact", heading, subheading?, email?, phone? }  (email/phone only if in source)
- pricing: { kind:"pricing", heading, subheading?, tiers:[{name, price, description?, features:[string], cta_label, cta_href, highlighted:boolean}] }  (2-4 real tiers — omit if source has no pricing)
- team: { kind:"team", heading, subheading?, members:[{name, role, bio?, photo_query}] }  (real people only — omit if none)
- gallery: { kind:"gallery", heading, subheading?, items:[{title?, caption?, image_query}] }  (1-12 items)
- menu: { kind:"menu", heading, subheading?, categories:[{name, items:[{name, description?, price?}]}] }  (real menu only — omit if none)
- process: { kind:"process", heading, subheading?, steps:[{title, description}] }  (1-6 real steps — omit if none)

If a page's required_sections list contains a section kind the source can't
support, OMIT that section — do not invent content to fill it. The requested
structure is a maximum, not a quota: produce every section you can ground in
the source (in the given order) and silently drop the rest. The words are yours
to improve; the facts are the source's to keep.
"""


_SCAFFOLD_RAW_TEXT_CHARS = 2000  # entry-page key phrases (brand context, not source of truth)
_SCAFFOLD_NUM_CTX = 8192

# --- Dynamic batch-size constants (empirical for Qwen 2.5 7B / 8 192 ctx) ---
#
# How the budget splits:
#   input  ≈ 48 % of num_ctx
#   output ≈ 52 % of num_ctx
#
# Fixed input overhead (present in every call):
#   _TOK_SYSTEM_PROMPT  scaffold system prompt        ≈ 700 tokens
#   _TOK_BRAND_SOURCE   brand dict + entry-page text  ≈ 600 tokens
#
# Per-page input overhead (added once per page in the batch):
#   _TOK_PER_PAGE_STUB    slug + title + sections list ≈ 100 tokens
#   page source           estimated from the page's ACTUAL text length, capped at
#                         MAX_CHARS_PER_CALL, at _CHARS_PER_TOKEN chars/token
#                         (small pages cost less; large pages are chunked, not batched)
#
# Per-section output (multiplied by section count for the page):
#   _TOK_PER_SECTION_OUT  ≈ 230 tokens (hero ~120, features ~280, process ~250,
#                           cta ~100 — weighted average across block types)
#
# Quality cap: even when tokens fit, packing > 10 sections into one call causes
# the model to thin out each block. The section cap enforces focus.

_TOK_SYSTEM_PROMPT = 700
_TOK_BRAND_SOURCE = 600
_TOK_PER_PAGE_STUB = 100
_TOK_PER_SECTION_OUT = 230
_INPUT_SHARE = 0.48          # fraction of num_ctx reserved for input tokens
_MAX_SECTIONS_PER_BATCH = 10  # quality cap — model focus degrades beyond this
_MAX_PAGES_PER_BATCH = 4      # absolute page cap regardless of token math
_CHARS_PER_TOKEN = 4          # rough English chars→tokens ratio for estimates

# Per-call content budget (chars of a single page's text fed to one LLM call).
# Sized to fit one page inside an 8 192-token call alongside the prompt + output:
#   input ≈ system 700 + brand 600 + stub 100 + 6000/4 (=1500) ≈ 2 900 tokens,
#   leaving ~5 300 tokens for that page's section output — comfortable headroom.
# A page whose text exceeds this is split into chunks and generated across
# multiple calls (see _generate_page_multipass), so no content is dropped.
# num_ctx itself stays at _SCAFFOLD_NUM_CTX (8 192) by design — we chunk rather
# than widen the window. Tunable: raising this means fewer chunks but bigger calls.
MAX_CHARS_PER_CALL = 6000


def _build_batches(
    scaffolds: list[PageScaffold],
    source_map: dict[str, SourceContent] | None,
    num_ctx: int,
) -> list[list[PageScaffold]]:
    """Greedy token-aware batching.

    Pages are packed into a batch until any of four limits would be exceeded:
      1. Input token budget  (system + brand + per-page stubs + page_source)
      2. Output token budget (section count × per-section estimate)
      3. Section density cap (_MAX_SECTIONS_PER_BATCH) — keeps model focused
      4. Absolute page cap   (_MAX_PAGES_PER_BATCH)

    When the next page would overflow any limit the current batch is sealed and
    a new one begins.  A page that exceeds the budget on its own is always placed
    in a batch of size 1 so generation never blocks.
    """
    input_budget = int(num_ctx * _INPUT_SHARE) - _TOK_SYSTEM_PROMPT - _TOK_BRAND_SOURCE
    output_budget = num_ctx - int(num_ctx * _INPUT_SHARE)

    batches: list[list[PageScaffold]] = []
    current: list[PageScaffold] = []
    cur_input = cur_output = cur_sections = 0

    for s in scaffolds:
        has_source = source_map is not None and s.slug in source_map
        # Estimate this page's source tokens from its ACTUAL text length (capped at
        # the per-call budget — larger pages are chunked elsewhere, never batched).
        # This lets tiny pages pack several-per-batch instead of all costing a flat
        # estimate. Only small pages (<= MAX_CHARS_PER_CALL) reach this function.
        src_chars = (
            min(len(source_map[s.slug].raw_text or ""), MAX_CHARS_PER_CALL)
            if has_source
            else 0
        )
        page_input = _TOK_PER_PAGE_STUB + src_chars // _CHARS_PER_TOKEN
        page_output = len(s.sections) * _TOK_PER_SECTION_OUT
        page_sections = len(s.sections)

        would_overflow = (
            cur_input + page_input > input_budget
            or cur_output + page_output > output_budget
            or cur_sections + page_sections > _MAX_SECTIONS_PER_BATCH
            or len(current) >= _MAX_PAGES_PER_BATCH
        )

        if would_overflow and current:
            logger.info(
                "Batch sealed — pages=%d sections=%d est_input=%d est_output=%d",
                len(current), cur_sections, cur_input, cur_output,
            )
            batches.append(current)
            current, cur_input, cur_output, cur_sections = [], 0, 0, 0

        current.append(s)
        cur_input += page_input
        cur_output += page_output
        cur_sections += page_sections

    if current:
        logger.info(
            "Batch sealed — pages=%d sections=%d est_input=%d est_output=%d",
            len(current), cur_sections, cur_input, cur_output,
        )
        batches.append(current)

    logger.info(
        "Dynamic batching: %d scaffolds → %d batches (num_ctx=%d, "
        "input_budget=%d, output_budget=%d)",
        len(scaffolds), len(batches), num_ctx, input_budget, output_budget,
    )
    return batches


def _build_scaffolded_user_prompt(
    source: SourceContent,
    brand: DetectedBrand | None,
    scaffolds: list[PageScaffold],
    parent_context: dict[str, dict] | None = None,
    source_map: dict[str, SourceContent] | None = None,
    text_overrides: dict[str, str] | None = None,
) -> str:
    return json.dumps(
        {
            "source": {
                "title": source.title,
                "description": source.description,
                "headings": source.headings[:15],
                # Entry-page text — brand context only. The real per-page content
                # is inside each pages_requested[].page_source (see source_map).
                "raw_text": source.raw_text[:_SCAFFOLD_RAW_TEXT_CHARS],
            },
            "brand": brand.model_dump() if brand else None,
            "pages_requested": _scaffolds_to_prompt_payload(
                scaffolds, parent_context, source_map, text_overrides
            ),
        },
        ensure_ascii=False,
    )


def _scaffold_depth(s: PageScaffold) -> int:
    """0 for roots, 1+ for sub-pages (count of '/' in slug)."""
    return s.slug.count("/") if s.slug else 0


def _hero_summary(page: PagePlan) -> dict | None:
    """Pull the hero block's headline/subheadline as parent context for children."""
    for blk in page.blocks:
        if getattr(blk, "kind", None) == "hero":
            return {
                "title": page.title,
                "headline": getattr(blk, "headline", "") or "",
                "subheadline": getattr(blk, "subheadline", "") or "",
            }
    return {"title": page.title, "headline": page.title, "subheadline": ""}


# --- multi-pass chunked generation ------------------------------------------------
#
# When a page's scraped text exceeds MAX_CHARS_PER_CALL it can't fit in one 8 192-tok
# call, so we generate it across several calls (one per content chunk) and merge the
# resulting blocks. List-bearing sections (faq, services, features, …) union their
# items across chunks so the final, capped section is drawn from the WHOLE page
# rather than just its first chunk. Singleton sections take the first chunk's version
# (chunk 0 = top of the page → the best hero/about/intro).

# kind → (list attribute, dedupe key fields tried in order). Menu is handled
# separately because it nests categories → items.
_LIST_BLOCK_SPECS: dict[str, tuple[str, tuple[str, ...]]] = {
    "features": ("items", ("title",)),
    "services": ("items", ("title",)),
    "testimonials": ("items", ("quote",)),
    "faq": ("items", ("question",)),
    "pricing": ("tiers", ("name",)),
    "team": ("members", ("name",)),
    "gallery": ("items", ("title", "caption", "image_query")),
    "process": ("steps", ("title",)),
}
_SINGLETON_KINDS = frozenset({"hero", "about", "cta", "contact"})


def _norm_key(value: object) -> str | None:
    """Whitespace-collapsed, lowercased dedupe key — or None when not keyable."""
    if isinstance(value, str) and value.strip():
        return " ".join(value.split()).lower()
    return None


def _item_key(item: object, key_fields: tuple[str, ...]) -> str | None:
    """First non-empty key field of an item, normalized. None ⇒ treat as unique."""
    for field in key_fields:
        key = _norm_key(getattr(item, field, None))
        if key is not None:
            return key
    return None


def _field_max_len(model_cls: type, attr: str) -> int | None:
    """Read a list field's schema max_length from its pydantic field metadata.

    Keeps the merge caps in lock-step with the model definitions so they never
    drift (e.g. FaqBlock.items max_length=20 → faq merge caps at 20).
    """
    field = model_cls.model_fields.get(attr)
    if field is None:
        return None
    for meta in field.metadata:
        max_len = getattr(meta, "max_length", None)
        if isinstance(max_len, int):
            return max_len
    return None


def _merge_menu_blocks(blocks: list[ContentBlock]) -> ContentBlock:
    """Union menu categories by name; within a shared category, union items by name."""
    base = blocks[0]
    cat_cap = _field_max_len(type(base), "categories")
    item_cap = _field_max_len(MenuCategory, "items")

    merged: dict[str, MenuCategory] = {}
    order: list[str] = []
    for blk in blocks:
        for cat in getattr(blk, "categories", None) or []:
            ckey = _norm_key(cat.name) or f"__{len(order)}"
            if ckey not in merged:
                merged[ckey] = cat
                order.append(ckey)
            else:
                existing = merged[ckey]
                seen = {_norm_key(i.name) for i in existing.items}
                for it in cat.items:
                    ikey = _norm_key(it.name)
                    if ikey is None or ikey not in seen:
                        existing.items.append(it)
                        seen.add(ikey)

    cats = [merged[k] for k in order]
    if cat_cap is not None:
        cats = cats[:cat_cap]
    if item_cap is not None:
        for cat in cats:
            cat.items = cat.items[:item_cap]
    base.categories = cats
    return base


def _merge_blocks_of_kind(blocks: list[ContentBlock]) -> ContentBlock:
    """Collapse all blocks of one kind (across chunks) into a single block."""
    base = blocks[0]
    kind = getattr(base, "kind", None)
    if len(blocks) == 1:
        return base
    if kind == "menu":
        return _merge_menu_blocks(blocks)
    spec = _LIST_BLOCK_SPECS.get(kind or "")
    if spec is None:
        # Singleton (hero/about/cta/contact) or unknown — first chunk wins.
        return base

    attr, key_fields = spec
    merged: list = []
    seen: set[str] = set()
    for blk in blocks:
        for item in getattr(blk, attr, None) or []:
            key = _item_key(item, key_fields)
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            merged.append(item)

    cap = _field_max_len(type(base), attr)
    if cap is not None:
        merged = merged[:cap]
    setattr(base, attr, merged)
    return base


def _merge_page_plans(plans: list[PagePlan], scaffold: PageScaffold) -> PagePlan:
    """Merge the per-chunk PagePlans for one page into a single PagePlan.

    Page-level fields come from the first plan (chunk 0). Blocks are grouped by
    kind, each group merged, then ordered to follow ``scaffold.sections`` (with any
    unexpected extra kinds appended in first-seen order).
    """
    if len(plans) == 1:
        return plans[0]

    base = plans[0]
    groups: dict[str, list[ContentBlock]] = {}
    first_seen: list[str] = []
    for plan in plans:
        for blk in plan.blocks:
            kind = getattr(blk, "kind", None)
            if kind is None:
                continue
            if kind not in groups:
                groups[kind] = []
                first_seen.append(kind)
            groups[kind].append(blk)

    merged_by_kind = {k: _merge_blocks_of_kind(v) for k, v in groups.items()}

    ordered: list[ContentBlock] = []
    used: set[str] = set()
    for section in scaffold.sections:
        if section in merged_by_kind and section not in used:
            ordered.append(merged_by_kind[section])
            used.add(section)
    for kind in first_seen:  # any kinds the LLM emitted beyond required_sections
        if kind not in used:
            ordered.append(merged_by_kind[kind])
            used.add(kind)

    base.blocks = ordered
    return base


def _needs_chunking(
    scaffold: PageScaffold, source_map: dict[str, SourceContent]
) -> bool:
    """True when this page's text is too large for one MAX_CHARS_PER_CALL call."""
    src = source_map.get(scaffold.slug)
    return bool(src and len(src.raw_text or "") > MAX_CHARS_PER_CALL)


async def _generate_page_multipass(
    source: SourceContent,
    brand: "DetectedBrand | None",
    scaffold: PageScaffold,
    parent_context: dict[str, dict],
    source_map: dict[str, SourceContent],
    client: OllamaClient,
) -> tuple[PagePlan | None, "ScaffoldedSitePlan | None"]:
    """Generate one oversized page across several chunked calls, then merge.

    Every call gets the SAME single-page scaffold (so it emits the same required
    sections) but a different slice of the page's text via ``text_override``. The
    per-chunk PagePlans are merged by :func:`_merge_page_plans` so each list
    section's items come from the whole page. Returns the merged page plus the
    first chunk's full result (carrying site-level fields, used only if this is
    the first work item overall).
    """
    page_source = source_map[scaffold.slug]
    chunks = split_raw_text(page_source.raw_text or "", MAX_CHARS_PER_CALL)
    logger.info(
        "multipass page '%s': %d chars → %d chunks",
        scaffold.slug, len(page_source.raw_text or ""), len(chunks),
    )

    chunk_plans: list[PagePlan] = []
    first_result: ScaffoldedSitePlan | None = None
    for idx, chunk in enumerate(chunks):
        result = await client.chat_json(
            system_prompt=_scaffold_system_prompt(),
            user_prompt=_build_scaffolded_user_prompt(
                source, brand, [scaffold], parent_context, source_map,
                {scaffold.slug: chunk},
            ),
            schema=ScaffoldedSitePlan,
            temperature=0.25,
            num_ctx=_SCAFFOLD_NUM_CTX,
        )
        if first_result is None:
            first_result = result
        page = next(
            (p for p in result.pages if p.slug == scaffold.slug),
            result.pages[0] if result.pages else None,
        )
        if page is not None:
            chunk_plans.append(page)
        else:
            logger.warning(
                "multipass chunk %d/%d for '%s' returned no page",
                idx + 1, len(chunks), scaffold.slug,
            )

    if not chunk_plans:
        return None, first_result
    return _merge_page_plans(chunk_plans, scaffold), first_result


async def plan_site_with_scaffolds(
    source: SourceContent,
    brand: DetectedBrand | None,
    scaffolds: list[PageScaffold],
    llm: OllamaClient | None = None,
) -> ScaffoldedSitePlan:
    """Scaffold-driven planning with parent-aware batching + per-page source routing.

    Strategy:
      - Build a source_map up front: each scaffold → the scraped page whose
        content should ground its copy (matched by slug/path/page_type).
      - Sort scaffolds by tree depth so all top-level pages generate before
        any of their children.
      - After each batch completes, harvest hero headlines from parent pages
        into ``parent_context``. Child batches receive that context so detail
        pages echo the parent's voice instead of restating the company.

    Token budget per batch (2 pages, num_ctx 8192):
      input  ~3 500 tokens (system + entry source + brand + 2 page stubs each
                            carrying ~4k chars of that page's own scraped text)
      output ~2 500 tokens (2 pages × 5-7 sections × ~150 tokens each)
      total  ~6 000 tokens → comfortable inside 8192 with safety margin.
    """
    client = llm or get_llm()

    # 1. Route each scaffold to its best-matching scraped page.
    source_map = match_scaffolds_to_pages(scaffolds, source)
    logger.info(
        "Source routing: %d scaffolds mapped (%d to specific discovered pages, %d to entry)",
        len(source_map),
        sum(1 for s in source_map.values() if s is not source),
        sum(1 for s in source_map.values() if s is source),
    )

    # 2. Stable sort by (depth, original index) so siblings stay in author order.
    indexed = list(enumerate(scaffolds))
    indexed.sort(key=lambda pair: (_scaffold_depth(pair[1]), pair[0]))
    ordered = [s for _, s in indexed]

    # 3. Build an ordered work-list. Runs of small pages flow through the existing
    #    multi-page batcher; each large page becomes its own chunked multi-pass item.
    #    Depth order is preserved (a large parent still generates before its children)
    #    so parent hero context is available downstream.
    worklist: list[tuple[str, object]] = []
    small_run: list[PageScaffold] = []

    def _flush_small_run() -> None:
        if small_run:
            for batch in _build_batches(small_run, source_map, _SCAFFOLD_NUM_CTX):
                worklist.append(("batch", batch))
            small_run.clear()

    for s in ordered:
        if _needs_chunking(s, source_map):
            _flush_small_run()
            worklist.append(("multipass", s))
        else:
            small_run.append(s)
    _flush_small_run()

    all_pages: list[PagePlan] = []
    parent_context: dict[str, dict] = {}
    first: ScaffoldedSitePlan | None = None

    for item_kind, payload in worklist:
        if item_kind == "multipass":
            scaffold = payload  # type: ignore[assignment]
            page, result = await _generate_page_multipass(
                source, brand, scaffold, parent_context, source_map, client
            )
            if first is None and result is not None:
                first = result
            produced = [page] if page is not None else []
        else:
            batch = payload  # type: ignore[assignment]
            result = await client.chat_json(
                system_prompt=_scaffold_system_prompt(),
                user_prompt=_build_scaffolded_user_prompt(
                    source, brand, batch, parent_context, source_map
                ),
                schema=ScaffoldedSitePlan,
                # Low temperature keeps the rewrite close to the source — we want
                # faithful editing, not creative drift away from the real content.
                temperature=0.25,
                num_ctx=_SCAFFOLD_NUM_CTX,
            )
            if first is None:
                first = result
            produced = list(result.pages)

        all_pages.extend(produced)
        # Harvest parent hero context for children that come in later work items.
        # Children are guaranteed later because of the depth sort.
        for page in produced:
            summary = _hero_summary(page)
            if summary is not None:
                parent_context[page.slug] = summary

    # Stitch parent_slug back onto every PagePlan from its source scaffold.
    by_slug = {s.slug: s for s in scaffolds}
    for page in all_pages:
        scaffold = by_slug.get(page.slug)
        if scaffold is not None and scaffold.parent_slug is not None:
            page.parent_slug = scaffold.parent_slug

    # Restore original (user-picker) order so downstream consumers see pages
    # in the intuitive order rather than depth order.
    page_by_slug = {p.slug: p for p in all_pages}
    ordered_pages = [
        page_by_slug[s.slug]
        for s in scaffolds
        if not s.is_legal and s.slug in page_by_slug
    ]
    # Tack any pages the LLM produced under slugs we didn't ask for onto the end.
    extras = [p for p in all_pages if p not in ordered_pages]
    final_pages = ordered_pages + extras

    assert first is not None
    return ScaffoldedSitePlan(
        site_name=first.site_name,
        tagline=first.tagline,
        brand_summary=first.brand_summary,
        brand_mood=first.brand_mood,
        industry_category=first.industry_category,
        primary_color_hint=first.primary_color_hint,
        pages=final_pages,
    )
