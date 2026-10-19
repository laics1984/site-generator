# Facebook Page → whole-business site: assessment and implementation plan

Status: **approved 2026-10-11 with all six decisions; P0–P7 and D3–D5 done
2026-10-19, and the facilities source-only rule. D6 (video embeds) remains.**
Written 2026-10-11 against the working tree on `main` (including the uncommitted
Facebook photo work: `facebook_dom.py`, `facebook_photo_*`, `landing_source.py`).

## Progress and corrections

**Done:**
- the expired-link fix (D3);
- classification-free dedupe;
- P0 (capture + replay harness);
- P1, except the video embed (D6, reopened below).

Full suite green. Details are in CLAUDE.md:
- "A signed photo link dies, so nothing is built on a dead one";
- "The Page's posts are in the data it already sent";
- the dedupe note under "One photo pass".

**Done 2026-10-19** (branch `feature/facebook-understanding`; details in
CLAUDE.md "Every photo knows where it came from" and "Understanding the Page,
once"):
- P2: provenance and measured quality on every photo, quality-ranked pool,
  rendition-keyed pixel cache, the inventory typed and shown,
  `section_image_plan` deleted;
- P7a/P7b: `source_coverage` (facts, reviews, offerings, photos);
- P3: restaurant, health, home-services and generic vocabularies; D5 as a
  Claude second look capped at 6;
- P4: `facebook_knowledge` (one index-answering call) plus the stale rule —
  stale posts and past events leave the grounding text (decision of
  2026-10-19), with a read-only "What we understood" report;
- P5: earned Programmes/Services page and child pages, planned from
  `offering_source`;
- P6: grouped Gallery walls, offering cards pinned to their own post's photo,
  hero pins tie-broken on quality;
- D4: the consent tick in the publish drawer.

**Corrections from that round:**
- The knowledge lives on `SourceContent.page_knowledge`, not
  `section_candidates` as §8 proposed. Six consumers read the primary
  source's tree, and the image-wall injector would have put gallery walls on
  the homepage. The earned page's planning view projects it into a tree, where
  that is safe.
- `features` keeps the "≥3 captioned posts" proxy when the call didn't answer.
  Replacing it outright would have dropped features from every read with the
  call off.
- Facilities are source-only (found in the P6 verify step, done 2026-10-19 in
  `services/knowledge_cards.py`): a card naming a facility takes its own photo
  or shows none, never the title-searched stock fallback.
- Measured live on CORE: the call categorises 10 photos (hints alone: 4) and
  finds no offering. Its posts are one-off activities, which is the right
  answer.
- The Facebook data-export reader (from the 2026-10-19 brief) is deferred.

**What the real captures corrected in this plan:**

1. **R2/R3 were not CORE's cause.** The one captioned post with "no photos" is a
   **reel**, a video post. The eight portrait photos show a different activity
   (a five-tastes test), from a post whose caption a logged-out visitor isn't
   served.
2. **Anonymous discovery is capped by Facebook, not by our parser.** The
   logged-out timeline payload holds exactly one Story. The scroll steps return
   byte-identical HTML. Photos-tab `Photo` objects carry no caption or date.
3. **The About page holds the content.** It is the first page the reader loads,
   and it carried **ten whole posts** in its reels collection, discarded on every
   read. That was the real root cause, and P1 fixed it with no new request:
   - captioned posts 1 → 10;
   - dated posts 0 → 10;
   - gallery-fit photos 9 → 17 (earning the Gallery page);
   - `features` earned.
4. **Facebook's `accessibility_caption` is free image evidence.** It is now
   carried as `platform_alt`.
5. **Video frames join the photo pool, against §9's original wording.** On a
   reels-only Page they are the only pictures any caption describes. They pass
   the same pixel screens; 2 of CORE's 10 were rejected for burned-in text.
6. **Graph pagination is unnecessary at the current cap.** `posts.limit(25)`
   returns it whole. The `/videos` edge was skipped too, because post
   attachments already carry the Page's videos.
7. **P0 captures live in a gitignored folder**
   (`backend/.facebook-captures/`), not `tests/fixtures/`: they hold real
   names. Unit tests pin the captured *shapes* with inline data.
8. **D6's premise was wrong.** The renderer's click-to-load facade exists only
   for YouTube, because its thumbnail is derived from the video id. A Facebook
   embed falls into the `other` branch and frames the player immediately.
   Putting it behind a facade needs a poster field on video nodes, renderer
   changes in webtree-public, the builder and the preview, and push re-hosting
   of the poster. **D6 is reopened.** Facebook videos are read and modelled,
   but not embedded yet.

---

## 0. Summary

**The pipeline the brief describes mostly exists already.** Facebook is already
"another source": one `source_kind="facebook"` flowing through the same
`SourceContent → planner → schema_builder → push` path as a crawl, with a fact
boundary (`FacebookPage`), an authority pass that overwrites the model's contact,
location and review copy with what the Page states, one unified photo pool with
measured screening, and evidence-gated sections and pages. What is missing is not
architecture. It is **evidence quality at the first step** and **one structuring
step in the middle**.

**Measured on the only cached read** (The CORE Learning Centre, logged-out render,
read 2026-10-10): 10 posts, 9 photos, and **one caption, on a post with no
photos**. There were no dates, no post ids, no hero candidates and no classified
photos. The eight photos that belong to that captioned post (sequential fbids in
one `set=pb.` run) arrived as caption-less Photos-tab tiles. Every downstream
symptom in the brief follows from that broken join plus the missing knowledge step:
a generic gallery, a stock hero, no Programmes page and nothing categorised.

**The plan** has eight phases (plus a fixture phase), and each one can be tested on
its own:

1. Fix the caption↔photo join using HTML the reader already downloads. No new
   requests.
2. Give the photo inventory provenance and working de-duplication.
3. Add one industry vocabulary per row (data, not code).
4. Add **one** structuring LLM call that answers in indices. It describes the
   business's offerings, activities and facilities in the section-tree shape every
   downstream consumer already reads.
5. Gate pages on that structure.
6. Gate galleries and photo placement on it.
7. Report coverage.

Cost: at most one extra text LLM call per read, zero vision calls by default, zero
new anonymous page loads, and no database migration.

Six product decisions need an answer before work starts (§19).

---

## 1. Where the brief doesn't match this repo

- **Stack.** The generator is FastAPI/Python with a React UI. Laravel, Vue and
  Nuxt are the sibling CMS, admin and public repos, and **this plan needs no
  change in any of them**. "Fowable" here means this webtree site generator.
- **No queue workers.** A read is an asyncio job on the `crawl_jobs` SQLite table
  (`facebook_orchestrator.run_facebook_job`), with progress, cancellation and reuse
  already built in. Idempotency comes from the reuse key
  (`facebook_source.reader_options()`).
- **No media database here.** Assets live in the CMS, deduplicated by sha256 at
  push time (`media.media_hash`). The generator holds measurements, never bytes.
  That should stay true.
- **No new `BusinessKnowledge` model or table** (reasons in §8), and **no
  embeddings or vector store**: at most 25 posts and 24 photos fit in one prompt,
  so there is nothing to retrieve.
- **No deeper anonymous scraping** (albums, extra scrolls, an events tab). Depth
  belongs behind an owner's Graph token. See §15 and decision D1.

---

## 2. Current architecture (as built)

```
POST /api/scrape/start ── source_detect.detect(url) == "facebook"   (before robots, by design)
 └ facebook_orchestrator.run_facebook_job            asyncio job on crawl_jobs; token in memory only
    ├ facebook_source.fetch_facebook_page           chain: Graph (token) → logged-out render → signed-in render
    │   ├ facebook_graph.fetch_page                 core + tolerant groups; posts.limit(25){attachments}; ratings
    │   └ facebook_render.fetch_page                ladder (/about, /) then views: timeline, /photos, /reviews
    │       └ facebook_dom.*                        pure parsers: labels, Intro, payload originals, articles, tiles
    └ facebook_source.to_preview_payload
        ├ to_source_content    → SourceContent(raw_text = build_raw_text digest,
        │                                      image_metadata = cover + post photos, discovered_pages = [])
        ├ build_brand          → profile picture → brand_candidate (logo_render_ok from decoded size)
        ├ facebook_photo_selection.review_photo_library
        │   └ facebook_photo_metadata.assess_photo_metadata   1 download/photo → size, sha256, dHash, OCR/QR, graphic
        └ homepage_sections_for → evidence-gated homepage section list
UI: ScrapePreview + FacebookFactsPanel → page picker
POST /api/pages/recipe (single_page) → infer_page_scaffolds: home + evidence_pages (Gallery ≥12, Menu) + legal
POST /api/generate/with-pages
  planner      homepage gets the digest via excerpt_for_prompt (≤6000 chars/call → multipass)
               and ≤12 photos with source_caption via image_prompt_payload
  scaffold_enforcement grounding → facebook_authority.enforce_facebook_facts
  injectors    _inject_image_walls (earned Gallery = gallery_photos), menu_assets, legible_images
  bind_image_refs → media.ImageResolver (scraped → stock) → _drop_unbound_gallery_items
  schema_builder.plan_to_site (+ advisory ux_audit) → push_orchestrator (re-downloads every source photo)
```

---

## 3. Measured baseline

There is one Facebook read in the local job cache (`crawl_jobs`,
`reader_version=4`, metadata review mode). Phase 0 adds more.

| Measure | Value |
|---|---|
| Path | `render` (anonymous; a saved session existed and was not needed) |
| Posts | 10 — 1 with text, 9 with one photo each |
| Photos | 9 (8 × 1675×2000 portrait, 1 × 1920×1080) |
| **Photos with a caption** | **0** |
| **Captioned post's photos** | **0** — "A little wood, some leaves, and lots of imagination! … creating our very own cute l…" |
| `created_time` / `post_id` | 0 / 0 |
| Hero candidates | 0 → `hero_stock_fallback: true` |
| Classified photos | 0 (every `classification: []`, every `recommended_sections: ["gallery"]`) |
| Homepage sections | hero, about, gallery, testimonials, locations, contact, cta (no `features`: 1 captioned post < 3) |
| Earned pages | none (9 gallery photos < 12) |
| fbcdn URL expiry (`oe=`) | 2026-10-15, five days after the read |

The caption shows another real-world problem: it begins with `ChatGPT said:`,
pasted by the business, and that text goes verbatim into the grounding digest.

---

## 4. Extraction coverage today

| Evidence | Graph path (token) | Anonymous render path |
|---|---|---|
| Name, category, About, contact, hours | ✔ (tolerant groups) | ✔ About/Intro labels, og:, JSON-LD; hours partial |
| Posts | `posts.limit(25)`: **one page, `paging.next` ignored** | `[role=article]` in the DOM, ≤25, 2 scrolls per view |
| Post date / id / permalink | ✔ | **✘ never set.** `permalink` is the *photo* link, and only when a photo was found |
| Multi-image posts | ✔ subattachments (≤20) | DOM-only, and failed on the measured Page |
| Caption ↔ photo | ✔ per post | **✘ broken** (see §5) |
| Photos tab | ✘ `/photos` edge unused (where photo captions live, as `name`) | ✔ tiles, **caption-less by design** |
| Albums | ✘ | ✘ (deliberate) |
| Videos | ✘ attachment types filtered to `photo`/`album` | ✘ caption kept, video dropped |
| Events | ✘ | ✘ |
| Reviews | ✔ `ratings` | ✔ `/reviews` view |
| Retry / back-off | ✘ (one attempt per group) | budget only (30s, stop at first wall) |
| Media URL expiry | not tracked | not tracked |

---

## 5. Root causes: missing post images and captions

**R1 — The inline payload is flattened.** `facebook_dom._inline_payload`
([facebook_dom.py:499](../backend/app/services/facebook_dom.py#L499)) walks every
JSON object and keeps two unrelated lists: photo originals keyed by identity, and
message texts. It throws away the Story structure that links a message to its
attachments, its `creation_time` and its post id. The payload is used for two
things only: swapping crops for originals, and un-truncating DOM captions.

**R2 — The association is DOM-only.** `_article_posts`
([:586](../backend/app/services/facebook_dom.py#L586)) pairs a caption with photos
only when the same `[role=article]` contains an `a[href]` matching
`/photo|/posts/|/permalink/` around an `img[src]` on an fbcdn host. Three cases
break it, and each gives "caption with no photos", which is the measured result:

- a lazily painted image (no real `src` yet);
- a multi-photo grid whose overflow is a "+5" overlay;
- a link shape that isn't in the list.

**R3 — Tiles never join their story.** `_photo_tiles`
([:617](../backend/app/services/facebook_dom.py#L617)) produces caption-less
`FacebookPost(photos=…)` entries. `_merge_posts`
([:824](../backend/app/services/facebook_dom.py#L824)) **deduplicates by photo
identity but never joins**. A tile whose photo also appears in a captioned story
stays caption-less, and the story stays photo-less.

**R4 — Effects downstream.**

- The childcare profile requires a caption for a hero (`hero_requires_caption`),
  so there are zero heroes.
- `caption_hints("")` returns no subjects, so nothing is classified and
  `recommended_sections` is only `["gallery"]`.
- The planner's `source_caption` is empty.
- The childcare `alt` is a fixed "{name} activity photo".

**R5 — Videos and events are not modelled.** Their captions and thumbnails never
reach the pool, and `VideoEmbed.provider` is `Literal["youtube", "vimeo"]`.

**R6 — The Graph path reads one page of posts and nothing else.** It skips the
`/photos` edge (photo captions), `/videos` and `/events`, and has no retry.

### Photos lost after discovery

- **Perceptual dedupe is off for most photos.** `_similar_photo`
  ([facebook_photo_selection.py:19](../backend/app/services/facebook_photo_selection.py#L19))
  only compares dHashes when both photos share a `classification` entry. So it never
  fires for an uncaptioned photo, and never fires on any non-childcare Page, because
  the generic profile has no vocabulary. Burst shots and re-uploads survive as
  "different" photos.
- **The pixel cache is keyed by signed URL** (`_PIXEL_CACHE`,
  [facebook_photo_metadata.py:42](../backend/app/services/facebook_photo_metadata.py#L42)).
  fbcdn signatures change between reads, so the cache misses across reads.
- **The model can only see the 12 largest photos.** `MAX_PROMPT_IMAGES = 12`
  ([source_router.py:373](../backend/app/services/source_router.py#L373)) against a
  24-photo pool. `review_photo_library` sorts by `(scores.overall, area)`, and
  metadata reviews have `scores=None`, so the order is pixel area alone. Caption
  relevance plays no part.
- **Expired URLs strip every photo.** The push re-downloads every source photo.
  After `oe=`, Facebook's CDN refuses the signed URL, and
  `push_orchestrator._strip_invalid_images` silently deletes every Facebook photo
  from the published site.
- **The cover is mislabelled.** It is recorded as `source_type: "post"` when it
  also appears as a tile (records look the URL up in posts first).

---

## 6. Root causes: omitted business content

**C1 — There is no knowledge step.** Today the "knowledge" is `build_raw_text`: a
labelled, verbatim digest. Programmes, services, activities and facilities exist
only as words inside captions. Nothing extracts them as entities, so nothing can
gate a page, a gallery group or a card on them.

**C2 — The site is planned as a single page.** `infer_page_scaffolds(single_page=True)`
([page_inference.py:1172](../backend/app/services/page_inference.py#L1172))
returns the homepage plus Gallery (≥12 photos), Menu and legal pages. About and
Contact are offered but not pre-ticked ([pages.py:93](../backend/app/routers/pages.py#L93)).
A Page that plainly runs a Nursery and a Preschool programme gets no Programmes
page. The evidence exists; no structure exposes it.

**C3 — The homepage gates are crude.** `features` needs `products` or at least
three captioned posts (`homepage_sections_for`,
[facebook_source.py:472](../backend/app/services/facebook_source.py#L472)).

**C4 — The homepage call carries everything.** Up to 25 captions of up to 2000
characters each are split into 6000-character multipass chunks, merged by
`_merge_page_plans`, all competing for at most 8 homepage sections.

**C5 — The richest output is dead.** `review_photo_library` builds a report
(per-image records, `section_image_plan`, `visual_profile`) that ships in the
preview payload as `facebook_photo_selection`, and **nothing reads it**: it has no
type in `frontend/src/lib/types.ts` and no consumer in the generator. The closest
thing to the brief's media inventory is computed and then dropped.

**C6 — There is no coverage check.** Nothing compares the generated tree with what
was read. `ux_audit` is advisory and covers SEO and contrast only.

---

## 7. Proposed architecture: same shapes, better evidence

The principle: Facebook stays "another reader". It should produce the **same
normalised `SourceContent` a crawl produces**, including a section tree, so every
downstream consumer works unchanged. New code goes where the gaps are, and nowhere
else.

```
DISCOVER    facebook_render / facebook_graph   P1  story-aware; no new anonymous page loads; Graph depth
EXTRACT     facebook_dom → FacebookPage        P1  posts with date, id, permalink, photos, videos
MEASURE     facebook_photo_metadata            P2  + quality, identity-keyed cache, URL expiry
UNDERSTAND  facebook_photo_profiles rows       P3  industry vocabularies; caption hints; capped optional vision
CONSOLIDATE facebook_knowledge (NEW)           P4  ONE index-answering call →
            → SourceContent.section_candidates (offerings · activities · facilities · gallery groups)
            → ImageMetadata.category / provenance
PLAN        page_inference.evidence_pages      P5  + offerings page gated on validated offerings
GENERATE    unchanged planner/schema_builder   P6  grouped gallery walls; cards take their own post's photo
VALIDATE    facebook_authority (unchanged)     P7  + source_coverage (NEW, advisory) → UI report
```

---

## 8. The business knowledge representation

| Brief concept | Home in this repo | Status |
|---|---|---|
| identity, businessProfile, contact, operatingHours | `FacebookPage` (the fact boundary) + `facebook_authority` | exists |
| testimonials | `FacebookPage.reviews` → authority pass | exists |
| services, programs, activities, facilities | `SectionCandidate(card_kind="offerings" \| "gallery")` holding `SourceCard(title, body, image_url, link, meta)` | **new producer** (P4) |
| team | not from Facebook. A Page photo never stands for a named person (the universal rule in `photo_rejection_reason`) | unchanged |
| brandIdentity | `BrandIdentity` from the profile picture | exists |
| mediaAssets | `ImageMetadata` + new optional provenance fields (P2) | extend |
| contentEvidence | numbered evidence units, built deterministically from `FacebookPage` | **new** (P4 input) |
| contentGaps | `FacebookPage.missing_fields` + coverage report | extend (P7) |

**Why not a new `BusinessKnowledge` model.** Every consumer that matters already
reads `SectionCandidate`/`SourceCard`: `section_prompt_entry`, the page-inference
signals, the landing journey, `_inject_image_walls`/`_place_walls` and
`gallery_block_from_section`. `SourceCard`'s own docstring says the shape carries
"a person, a programme, a room". A parallel model would need parallel consumers,
which is the duplication the codebase works to avoid.

**Provenance** lives in three places:

- `SourceCard.link`: the post permalink.
- `SourceCard.meta`: the post date.
- `ImageMetadata`: post id, post URL, published date, origin.

### The consolidation call contract

This is the contract `paste_structure`, `bio_condense` and `record_pages` already
use: **the model answers in numbers; deterministic code does the rest.**

- **Input:** numbered evidence units, for example
  `U0 About: …` · `U3 Post 2025-09-12 (8 photos): A little wood…` ·
  `U4 Products and services: …`, plus the industry's taxonomy (P3).
- **Output:**
  ```json
  {"units":     [{"unit": 3, "category": "creative_activity", "relevant": true}],
   "offerings": [{"name": "Nursery Programme", "kind": "programme", "units": [0, 7]}],
   "facilities":[{"name": "outdoor playground", "units": [5]}]}
  ```
- **Validation discards and never patches** (the `paste_structure` rule):
  - Every index must be in range.
  - Every category must come from the profile's taxonomy.
  - **Every `name` must be a verbatim span of one of its own units**, checked with
    `is_grounded_in_source` on the unit text. An invented "Montessori Programme" is
    dropped.
  - Every offering needs at least one unit of its own.
- **Card body text is the units' own lines**, cleaned of "See more", hashtag runs
  and generator boilerplate such as `ChatGPT said:`. Nothing the call returns
  reaches the site as prose.
- **Prompt injection** in a caption can at worst move an index. It cannot write
  copy.
- **Any failure** (flag off, `LlmError`, invalid reply) produces no sections, which
  is exactly today's behaviour.
- **It runs at read time**, inside `to_preview_payload` after the photo review,
  because the page picker needs its result. The call is cached by
  `chat_json_cached`. The crawl task already inherits the UI's model choice through
  the copied ContextVar (CLAUDE.md, "The Claude API provider").

---

## 9. Image discovery and classification strategy

| Stage | What | Cost |
|---|---|---|
| 1. Collect (P1) | Every photo carries its post (caption, date, id, permalink) via the story join. Tiles inherit captions **by identity across views**. Video thumbnails feed understanding and the click-to-load facade, not the photo pool. | 0 new loads |
| 2. Dedupe (P2) | `photo_identity` (fbcdn filename) → sha256 → dHash Hamming ≤ T, **independent of classification**. T is calibrated on resized and recompressed pairs from the fixtures. The largest copy wins. | CPU, bytes already in hand |
| 3. Measure (P2) | Existing: size, aspect, OCR/QR, flat graphic. New: **sharpness** (Laplacian variance on a 512px grayscale; OpenCV ships with rapidocr, PIL as fallback) and **exposure** (mean luminance, clipped highlights and shadows). One deterministic `quality` number. No aesthetic claims, matching the honesty rule in [childcare-facebook-metadata-selection.md](childcare-facebook-metadata-selection.md). | ~ms per photo |
| 4. Classify (P3/P4) | A photo **inherits its post's category** from the consolidation call. Caption keyword hints stay as the no-LLM fallback. Uncaptioned photos stay `uncategorised` unless vision is enabled (D5), in which case only uncaptioned photos that already pass the hero measurements are sent, capped. | 0 calls default |
| 5. Taxonomy | Rows in `facebook_photo_profiles.PROFILES` (data, with an existing drift test). Childcare exists. Add restaurant (food, dining room, kitchen, events, team at work, menu highlight), health-wellness (clinic interior, equipment, treatment, facilities), home-services (work in progress, completed project, team at work, equipment), and a generic row (products, services in action, premises, events, customers). | data |
| 6. Gallery (P6) | Groups are categories with ≥4 accepted photos. Two or more such groups become **headed walls**, which `_merge_album_walls`/`_place_walls` already support (they key walls by heading). Otherwise one wall, as today. A per-post cap (≤4 per group) stops one burst filling a group. Ordered by quality. **Never stock**: `_drop_unbound_gallery_items` already enforces this. | none |

Group headings are taxonomy labels ("Learning activities", "Celebrations"). They
are site chrome, not claims about the business.

---

## 10. The holistic website generation workflow

1. **The read** (P1–P4) produces `FacebookPage` → `SourceContent` carrying the
   digest (still the grounding haystack), `section_candidates` from consolidation,
   and a categorised, provenance-stamped photo inventory.
2. **Pages earned from evidence** (P5), extending `evidence_pages`. This continues
   the existing direction that a single-page source gets only the pages its
   material earns.
   - Gallery (exists).
   - Menu (exists).
   - **Offerings page** ("Programmes" for childcare, "Services" otherwise):
     earned when there are **≥2 validated offerings, each with units of its own**.
   - **Child pages** (Programmes → Nursery, Preschool): only when each offering has
     ≥~300 characters of its *own* evidence and at least one photo of its own.
     Otherwise the offerings are cards on the parent page.
   - About and Contact: unchanged, offered and not pre-ticked (D2).
3. **Routing.** An earned page must be planned from *its* evidence, not the whole
   digest.
   - **Recommendation:** keep `discovered_pages == []` and give the earned scaffold
     a derived planning view — a `SourceContent` copy restricted to that page's
     units, sections and photos — at the point where `match_scaffolds_to_pages`
     falls back to the primary source. The precedent is
     `landing_source.homepage_source`, which already builds a private planning view
     without rewriting the source.
   - **The alternative**, emitting real `discovered_pages`, breaks five places that
     assume a Facebook source has none:
     - `App.isSinglePageSource` (it would turn on the full template fan-out);
     - the earned-gallery branch of `_inject_image_walls` (`not source.discovered_pages`);
     - `source_merge` and the paste merge;
     - the `single_page` branch in `routers/pages.py`;
     - `test_facebook_single_page`.
4. **Homepage.** `features` is earned by ≥2 offerings or `products`, replacing
   "≥3 captioned posts". The gallery count is unchanged (`gallery_photos`).
5. **Generation** is unchanged. Earned pages are ordinary scaffolds. The authority
   pass, grounding net, injectors and resolver all run as today.
6. **Allocation** (P6):
   - An offering card takes the best photo **from its own posts** before the
     model's `image_ref`.
   - The hero is ranked by measured quality among hero-eligible photos. The cover
     competes equally and is never preferred by position (already true).
   - Grouped walls fill the Gallery page.
   - Stock is allowed only where it is today (hero, CTA, decorative). Gallery stays
     source-only, and facilities stay source-only (to verify, see P6).
7. **Validation** (P7): an advisory coverage report is attached to the generate
   response and shown in the preview.

---

## 11. Existing components reused (not rewritten)

- **Readers and chain:** the reader chain and fetcher protocol; `merge_view`
  gap-fill semantics; `facebook_dom` rule 4 ("the DOM decides which posts are
  visible").
- **Fact boundary:** `FacebookPage`; `facebook_authority.enforce_facebook_facts`.
- **Photo measurement and gates:** the `facebook_photo_metadata` pixel pass
  (one download, reused for OCR, QR and graphics); `photo_rejection_reason` (the
  single photo gate); `gallery_photos`; `must_show_whole`; `legible_images`;
  `menu_assets`.
- **Page and gallery machinery:** `page_inference.evidence_pages`;
  `_inject_image_walls`, `_merge_album_walls`, `_place_walls`;
  `gallery_block_from_section`; `_drop_unbound_gallery_items`.
- **Media:** `media.ImageResolver`; `image_urls.photo_identity`/`image_identity`.
- **Video:** `_inject_videos` + `SourceContent.video_embeds`.
- **LLM:** `chat_json_cached`, the index-answering contract, and the `conftest`
  offline-pin idiom.
- **Infrastructure:** `url_guard`; `polite` back-off; the
  `deployment/process_local` inventory test; the job reuse key.

---

## 12. Modifications by phase

Columns: **Today** = current responsibility · **Gap** = the limitation ·
**Change** = the proposed improvement · **Expected** = expected behaviour ·
**Risk** = regression risk.

### P0: Fixtures and a yield harness (prerequisite)

| Module | Today | Gap | Change | Expected | Risk |
|---|---|---|---|---|---|
| `backend/tests/fixtures/facebook/` (new) | Parsers are pinned by inline strings | The payload's real shape is unpinned, and Facebook markup is undocumented (the old `is_login_wall` was dead for as long as it existed) | 4–5 captured Pages (CORE, a restaurant, a clinic, a trade, a service), stripped to `role=main` + `application/json` scripts. **Personal names in captions redacted; no image bytes stored.** | Every later phase reports before/after yield on real markup | none |
| `backend/scripts/measure_facebook_read.py` (new; offline over fixtures, `--live` opt-in) | — | No yield measure exists | Prints posts / captioned / dated / photos / caption-linked / hero candidates / categories / groups | The §3 table is reproducible | none |

### P1: Post-structured discovery ("read better, not more")

| Module | Today | Gap | Change | Expected | Depends | Risk |
|---|---|---|---|---|---|---|
| `facebook_dom._inline_payload` / `_Payload` | Flat `originals` + `messages` | R1 | Story-scoped walk: a node carrying a post id opens a story, and photos, `message.text`, `creation_time`, permalink and video media found inside it are attributed to that story. Keys are confirmed against P0 fixtures before coding. | CORE's 8 photos carry their caption and date | P0 | Low. The DOM stays the visibility arbiter, so no story the DOM doesn't show is added |
| `_article_posts`, `_photo_tiles` | DOM association only | R2, R3 | After the DOM read, join each post or tile to a payload story by `photo_identity` (or caption fingerprint); fill message, `created_time`, `post_id`, permalink | Tiles gain captions | P1a | Low |
| `_merge_posts`, `merge_view` | Dedupe, never join | R3 across views | Key posts by `post_id`. A later view's story fills a caption-less post's gaps (gap-fill semantics kept). The two caps (photos vs text posts) stay separate. | A Photos-tab tile picks up the caption the timeline story had | P1a | Low |
| `models/facebook.py` | No video, kind or event | R5 | `FacebookVideo(permalink, thumbnail_url, title, description, width, height)`, `FacebookPost.videos`, `FacebookPage.events` (Graph only) | Videos are modelled | mirror `FacebookFacts` in `types.ts` | Additive |
| `facebook_graph.fetch_page` | `posts.limit(25)`, one page; photo/album attachments only; no retry | R6 | Follow `paging.next` up to the cap. Request video attachment types (thumbnail, `target.url`, title, description). Add **tolerant** groups for `photos?type=uploaded{name,images,created_time,link,album{name}}`, `videos{…}` and `events{…}`. Retry 429/5xx and Graph throttle codes with `polite` back-off. Field names verified against the configured Graph version. | Token reads get album-photo captions, videos and events | — | A new group cannot fail the core read (existing tolerant-group rule) |
| `video_embed` + `VideoEmbed.provider` | youtube/vimeo only | Facebook videos can't be placed | Add a `"facebook"` provider that emits the official plugin URL (`facebook.com/plugins/video.php?href=<permalink>`), commas encoded. `to_source_content` fills `video_embeds`; the existing `_inject_videos` places them. | Public Page videos render behind the existing click-to-load facade | D6. Renderers already frame `provider: 'other'`, so **no renderer change** | The thumbnail is an fbcdn URL, so expiry applies (P2) |
| `facebook_source.READER_VERSION` | 4 | — | 5 | Stale reads are not reused | — | — |

### P2: One media inventory with provenance (fixes that pay off immediately)

| Module | Today | Gap | Change | Expected | Risk |
|---|---|---|---|---|---|
| `ImageMetadata` | No provenance | Brief §7 | Optional fields: `source_post_id`, `source_post_url`, `published_at`, `origin` (`cover\|post\|photos_tab`), `category`, `quality`, `expires_at` | Every photo traces to its post | Additive |
| `_similar_photo` | dHash compared only with shared classification | Perceptual dedupe dead | identity → sha256 → dHash Hamming ≤ T, classification-free, T calibrated on a pair corpus | Bursts and re-uploads collapse | May merge two genuinely different near-identical shots. Pinned by the pair corpus |
| `_PIXEL_CACHE` | Keyed by URL | Signatures rotate | Key by `photo_identity` | Re-reads reuse measurements | none |
| `PixelChecks` | Size, hash, OCR, graphic | No quality signal | + sharpness, exposure, from the same decoded bytes | Blurry or dark photos rank last | ~ms CPU |
| `review_photo_library` ordering | `(0, area)` | Area is not relevance | `(caption-linked, quality, area)` | The 12 photos the model sees are the 12 best | Changes which photos are promptable; snapshot tests may move |
| URL expiry (none today) | — | Push strips everything after `oe=` | Parse `oe=` → `expires_at`; the preview warns. At generate, if any photo expires within 1h, re-read the Page (bypassing the cache) and **remap by `photo_identity`**. The push refuses an expired URL **with a clear error** rather than stripping silently. | Never a silently photo-less site | D3. A failed re-read is a loud error |
| `facebook_photo_selection` report | Computed, read by nothing (C5) | Dead output | Becomes **the inventory**: typed in `types.ts`, shown in `FacebookFactsPanel`, read by coverage (P7). **Delete `section_image_plan`**: P6 allocation supersedes it, and two answers to one question is debt. Fix the cover's `source_type` via `origin`. | One inventory, one reader | Frontend edit |

### P3: Understanding (taxonomy + selective vision)

| Module | Today | Gap | Change | Expected | Risk |
|---|---|---|---|---|---|
| `facebook_photo_profiles.PROFILES` + `PhotoContext` | Childcare + an empty generic row | Nothing is classified outside childcare | Rows per §9 (data). The existing drift test pins `PhotoContext` ↔ rows. | Every industry has a vocabulary | The hero policy per row stays at the generic default unless set |
| `caption_hints` | Keyword hints | — | Unchanged; becomes the fallback when P4 doesn't run | — | none |
| `image_vision`, `llm.active_vision_model` | Local VLM only; ignores the UI model menu | No vision unless a local VLM is configured | Opt-in: when Claude is the reasoning choice, the Facebook vision step may use it (`AnthropicClient` already takes `images`). Cap `FACEBOOK_VISION_MAX_IMAGES=6`, sending only **uncaptioned, measured-hero-eligible** photos. | Uncaptioned strong photos can still become heroes | D5. Cost bounded by the cap |

### P4: Knowledge consolidation

| Module | Today | Gap | Change | Expected | Risk |
|---|---|---|---|---|---|
| `services/facebook_knowledge.py` (new) | — | C1 | Evidence units → one `chat_json_cached` call → validation (§8) → `section_candidates` + `ImageMetadata.category` | Offerings, activities and facilities become structure | Flag; `conftest._offline_facebook_knowledge` pins it off for the suite |
| `facebook_source.to_preview_payload` | Photo review only | — | Call knowledge after the review. New progress step: "Understanding the Page". | — | +1 call per read (cached) |
| `facebook_source.build_raw_text` + one caption-clean helper | Captions verbatim | Noise (`ChatGPT said:`, hashtag walls, "See more") in the haystack | One helper, used by the digest and by the units | Cleaner grounding | The haystack loses only noise |
| `prompts.py`, `config.py` | — | — | `FACEBOOK_KNOWLEDGE_PROMPT`; `FACEBOOK_KNOWLEDGE_ENABLED` (on), `FACEBOOK_KNOWLEDGE_MAX_UNITS`, `FACEBOOK_MAX_POST_AGE_DAYS` (relevance; applied only when dates are known) | — | — |

### P5: Information architecture

| Module | Today | Gap | Change | Expected | Risk |
|---|---|---|---|---|---|
| `page_inference.evidence_pages` | Gallery, Menu | C2 | + offerings page and gated child pages (§10.2). Each gate asks the predicate its filler uses (the existing rule). | Programmes/Services earned, never padded | P4 required |
| `source_router.match_scaffolds_to_pages` fallback | Everything routes to the primary source | An earned page would see the whole digest | Derived planning view per earned slug (§10.3) | The Programmes page is written from programme evidence | Recommended over `discovered_pages` (5 call sites avoided) |
| `homepage_sections_for` | `features`: products or ≥3 captioned posts | C3 | ≥2 offerings or products | Features appear when offerings exist | — |
| `routers/pages.py` | About/Contact offered, unticked | — | Unchanged; earned offerings pages arrive as inferred, pre-ticked | — | D2 |

### P6: Allocation

| Module | Today | Gap | Change | Expected | Risk |
|---|---|---|---|---|---|
| `_inject_image_walls`, earned-gallery branch ([generate.py:2518](../backend/app/routers/generate.py#L2518)) | One `_photo_set_wall` | Ungrouped gallery | Category walls from P4's `card_kind="gallery"` sections; one wall as the fallback | Grouped gallery | Existing caps (`_MAX_GALLERY_BLOCKS`, `_MAX_PAGE_GALLERY_IMAGES`) bound it |
| Offering cards / `image_refs` | The model binds a ref among 12 | Card ↔ unrelated photo | `SourceCard.image_url` = the best photo from that offering's own posts, before the model's ref | "Outdoor learning" shows an outdoor post | One-picture-one-picture identity rule still applies |
| Hero ranking | Binary eligibility, then pool order | No quality tie-break | Rank eligible photos by `quality`; the cover is never preferred by position | Best real photo wins | — |
| Authentic-only sections | Gallery enforced; facilities/teachers only stated in prompts and the dead report | Possibly unenforced for facilities | Verify; if missing, enforce in **one** place beside `_drop_unbound_gallery_items` | No stock "our classroom" | — |

### P7: Content coverage report

| Module | Today | Gap | Change | Expected | Risk |
|---|---|---|---|---|---|
| `services/source_coverage.py` (new, pure) | — | C6 | `(source, plan, site)` → facts (phone, email, address, hours, website: read vs rendered), offerings (read vs rendered by title), photos (placed by `_collect_image_srcs` + `image_identity`; every unplaced photo carries a reason from the inventory: duplicate, rejected (why), over cap, no slot), reviews vs testimonials | "Extracted 5 · placed 4 · unused 1 (reason)" | Advisory; never blocks (the ux_audit precedent) |
| Generate response + UI | — | — | Optional `coverage` field; a "What made it onto the site" panel | Operator sees omissions | Frontend edit |

**Ordering recommendation:** build a first cut (facts and photos only) straight
after P2. It then measures P3–P6 rather than arriving after them.

### P8: Performance and reliability (mostly folded into earlier phases)

- **Module-level caches:** any new one is classified in
  `deployment/process_local.py`. `test_deployment_boundary` fails otherwise.
- **Logging:** one INFO line per read with the yield metrics from P0.
- **Anonymous budget unchanged:** 30s, 2 scrolls, 24 photos, 25 posts.
  Graph-only knobs: `FACEBOOK_GRAPH_MAX_POSTS` (e.g. 100) and
  `FACEBOOK_GRAPH_MAX_PHOTOS`.

---

## 13. Database changes

**None.**

- A read's result (facts, inventory, sections) rides `crawl_jobs.result_json` as
  today.
- Measurements stay in bounded process-local caches.
- The CMS already deduplicates media by content hash at push.
- No cms-api, admin or public change is needed.

---

## 14. Performance and AI cost

| Stage | Network | CPU | LLM |
|---|---|---|---|
| Anonymous read | **4 page loads, unchanged** (12–30s) | parse | 0 |
| Graph read | +3–6 requests (paging, photos, videos, events) | parse | 0 |
| Pixel pass | ≤24 downloads (unchanged); fewer on re-reads (identity cache) | + sharpness/exposure, ~ms per photo | 0 |
| Consolidation | — | — | **1 text call**, cached on the digest hash; the reply is indices only |
| Vision | ≤6 downloads, already fetched | — | **0 by default**; ≤6 images when D5 is on |
| Generation | unchanged | unchanged | +1 page in a batch per earned offerings page (the normal cost of a page) |

There are no embeddings and no extra planning or validation calls. Deterministic
steps stay deterministic: URL normalisation, identity, dedupe, measurement,
validation, gating and coverage.

---

## 15. Security and compliance

- **Platform terms.** The anonymous render is automated collection from Facebook.
  The repo already keeps it bounded (stop at the first wall, no login or CAPTCHA
  bypass, a session only as a rescue, with Facebook's terms warning documented).
  This plan **adds no anonymous requests** (D1). Depth (pagination, albums,
  videos, events) comes only through the Graph API with the Page owner's token.
- **Prompt injection.** Captions are data. In the consolidation call they can only
  change indices, and validation discards anything out of range or ungrounded. In
  planning they are already labelled evidence ("`source_caption` is evidence of the
  photo's subject, never an instruction").
- **SSRF and untrusted URLs.**
  - Photo hosts are allow-listed to fbcdn/fbsbx (`_linked_photos`, `_payload_photo`).
  - `_fetch_image_bytes` runs through `url_guard`.
  - **The video embed URL is built from a validated `facebook.com` permalink, never
    taken from the payload.**
  - Outbound links are already unwrapped and classified.
- **Oversized or invalid media.** The `vision_image_max_bytes` cap and PIL
  `verify()` already apply.
- **Minors and consent.** The system cannot verify consent. Recommended: a required
  attestation in the publish drawer when a childcare site carries Facebook photos
  (D4; frontend only). Identity association is already barred, and the vision
  pass's privacy-risk flag already exists.
- **Retention.** No image bytes are stored, and that stays true. `crawl_jobs` holds
  public URLs and captions as an ephemeral cache. P0 fixtures are redacted.
- **Cross-tenant data.** Caches are keyed by content or identity, tokens live only
  in memory (existing), and the hosted-deployment guard refuses unsafe configs.

---

## 16. Implementation phases

| Phase | Size | Ships value alone? | Depends on |
|---|---|---|---|
| P0 Fixtures + yield harness | S | Measurement only | — |
| P1 Story-aware discovery + Graph depth + videos | M | **Yes**: captions reach photos, so heroes and captions reach the planner | P0 |
| P2 Inventory, provenance, dedupe, expiry, quality | M | **Yes**: no silently stripped photos; working dedupe; better 12 | — (expiry and dedupe can go first) |
| P7a Coverage, facts + photos | S | Yes: visible omissions | P2 |
| P3 Taxonomy rows + optional vision | S | Yes for non-childcare classification | P1 |
| P4 Knowledge consolidation | M–L | Yes: features and cards from offerings | P1, P3 |
| P5 Earned offerings pages | M | Yes | P4 |
| P6 Allocation (grouped gallery, own-post photos, hero ranking) | M | Yes | P2, P4 |
| P7b Coverage, offerings | S | Yes | P4 |
| P8 Reliability residue | S | — | throughout |

Two pieces can land **before everything else**, because they are independent bugs:

- the URL-expiry handling (P2);
- the classification-free perceptual dedupe (P2).

---

## 17. Testing and validation plan

All tests run offline. LLM calls use injected fake clients; fixtures replace live
reads (the `conftest._offline_*` idiom).

**A. Extraction** (`test_facebook_dom`, `test_facebook_graph`, `test_facebook_photo_collection`, + fixture tests)

- Single-image post; multi-image post (CORE's 8-photo set → one post, 8 photos, its
  caption); text-only post; post with an outbound link; missing caption.
- A tile joined to a timeline story by identity across views.
- Duplicate photos across views; lazily painted or `data:` images ignored.
- Graph pagination stops at the cap. A denied `photos`/`videos`/`events` group is
  recorded and does not fail the read. Throttle codes retry, then give up.
- A partial read after a wall keeps what was read (existing behaviour, re-pinned).

**B. Understanding** (`test_facebook_knowledge` new, `test_facebook_photo_profiles`)

- Caption ↔ photo inheritance: a photo takes its post's category.
- An invented offering name is dropped; out-of-range indices are discarded wholesale.
- Two offerings with shared units stay distinct.
- Contradictory facts: Page fields win (authority pass, existing).
- An offering with no unit of its own is dropped.
- Taxonomy vocabulary drift test.

**C. Generation** (`test_facebook_single_page`, `test_image_walls`, `test_source_coverage` new)

- ≥2 offerings earn the offerings page; 1 does not; child pages appear only with
  their own evidence.
- The earned page is routed to its own planning view.
- Grouped walls appear only when ≥2 groups have ≥4 photos.
- An offering card takes its own post's photo.
- The hero is the highest-quality eligible photo, not the cover by position.
- Nested navigation via `menu_builder` groups. Schema validity via the existing
  builder-schema parity tests.

**D. Regression**

- The full suite stays green.
- URL, PDF, DOCX and paste sources produce identical scaffolds and plans on
  existing fixtures (these code paths are untouched; `source_kind` gates every
  Facebook branch).
- `isSinglePageSource` is unchanged (routing via planning views).

**E. Quality**

- No fabricated claims: every offering title is grounded.
- No photo appears twice site-wide (`image_identity`).
- No stock image in galleries or facilities (existing test, plus a facilities case).
- An expired URL never reaches the push silently.
- Coverage accounts for 100% of extracted facts and photos.
- Measured perceptual dedupe on a resized and recompressed pair corpus.

**Visual check.** Render a generated CORE site through the real preview renderer
with the existing temporary harness + Playwright, before and after.

---

## 18. Acceptance criteria

Measured on the P0 fixtures with `measure_facebook_read.py`.

1. **CORE:** ≥8 of 9 photos carry a caption and a post link (today 0). Every post
   carries `created_time` (today 0). The 8-photo set is **one** post.
2. **CORE:** at least one hero candidate when a captioned landscape photo of
   ≥1200px exists (today 0). Otherwise the stock fallback is logged with the reason.
3. **Dedupe:** resized and recompressed pairs collapse; distinct near-shots
   (calibration set) do not; this holds with empty classification.
4. **Expiry:** generating or pushing after `oe=` either refreshes by identity or
   fails before upload with a clear message. **It never publishes a site with
   photos silently stripped.**
5. **Knowledge:** every rendered offering title is a verbatim span of the source.
   An invented title never appears (fake-client test).
6. **IA:** a Page with ≥2 grounded offerings gets an offerings page; a Page with
   fewer does not; no earned page is empty.
7. **Gallery:** groups only from real categories with ≥4 photos; no stock; no
   repeated picture site-wide.
8. **Coverage:** the report lists every extracted fact, offering and photo as
   placed or excluded-with-reason.
9. **Cost:** ≤1 extra text call per read (cached); 0 vision calls by default;
   anonymous page loads per read unchanged (4).
10. **Regression:** the full suite passes; non-Facebook sources are unchanged.

---

## 19. Decisions needed (each with a recommendation)

- **D1. Anonymous-read depth.**
  **Recommend:** no new anonymous page loads. Improve parsing of what is already
  fetched, and put depth (pagination, albums, videos, events) behind the Graph
  token only. More scrolling or an events tab raises both the platform-terms
  exposure and the chance Facebook starts walling the read.
- **D2. Earned pages beyond Gallery/Menu.**
  **Recommend:** an offerings page (Programmes/Services) pre-ticked when ≥2
  validated offerings exist, with child pages only on per-offering evidence. About
  and Contact stay offered and unticked. Activities stay inside the grouped gallery
  rather than becoming a page.
- **D3. Expired Facebook photo URLs.**
  **Recommend:** at generate time, re-read the Page and remap photos by identity,
  and refuse at push with a clear message if they are still expired. The
  alternative, storing photo bytes at read time, adds retention of children's
  images on our side.
- **D4. Consent for photos of minors.**
  **Recommend:** a required checkbox in the publish drawer ("I have consent to
  publish these photos") whenever a childcare site carries Facebook photos.
  Frontend only.
- **D5. Vision.**
  **Recommend:** keep metadata as the default. Allow the Facebook vision step to
  use the Claude model chosen in the UI, capped at 6 uncaptioned,
  measured-hero-eligible photos per read.
- **D6. Facebook video embeds.**
  **Recommend:** yes, for public Page videos, behind the existing click-to-load
  facade, so no Facebook script loads on the client site until a visitor clicks.
