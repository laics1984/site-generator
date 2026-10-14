# Facebook photo selection without a vision model

Every Facebook Page now goes through this pass, not only childcare: what differs
per industry is a row in `backend/app/services/facebook_photo_profiles.py`
(see CLAUDE.md, "One photo pass, a profile per industry"). Childcare's row keeps
every rule below verbatim; the generic row judges a hero by measurement alone and
leaves menus, posters and QR codes to the passes that place them whole.

## Behaviour

Facebook scraping defaults to `metadata` assessment. It downloads
up to 24 distinct candidate photos and reuses each original byte payload for
dimensions, exact/perceptual duplicate fingerprints, the existing OCR/QR screen,
and the existing graphic detector. No generative model is called by this step.

The site's existing text-model planning call receives a bounded source caption,
caption-based subject hints, section recommendations and measured hero eligibility.
It uses that context to choose the existing `image_ref` for the relevant section
or programme. It does not return an additional classification/report schema.

Caption labels in the scrape report are preliminary keyword hints. Matching in
the generated page is performed by the existing planner. Neither is proof of
the image's actual subjects; metadata mode never invents visual-quality scores,
facial expressions, identities, negative space or an aesthetic colour profile.

## Flow

1. Preserve the About-page facts, then read the timeline, the Photos tab and the
   recommendations in the same browser context (`facebook_render._collect_views`).
   Collect explicit photo links, including Photos-tab tiles outside post
   articles, and swap each tile's crop for the original the page's inline data
   names (`facebook_dom._inline_payload`) — no page load per photo. Lazy loading
   is capped at two scrolls per view and a 30-second budget for all views. Stop
   at 24 distinct photos. Access walls and timeouts preserve the facts and photos
   already collected. Tiles without a post caption remain uncaptioned backups.
   The Page's `role="main"` subtree defines media visibility: an outer
   accessibility wrapper behind the bundled login dialog does not discard public
   attachments. Hidden tiles inside the Page and real access-wall redirects or
   refusal messages remain excluded.
2. Remove URL/resized duplicates, keeping the largest known version.
3. Prioritise caption-relevant activities within the pixel-check budget.
4. Download each uncached candidate once using the existing guarded image fetch.
5. Decode original dimensions, fingerprint the bytes, and reuse them for OCR/QR
   and graphics checks. Cache complete measurements, not the image bytes.
   Provisional Facebook gallery items are screened as content copies, because
   the shared screens otherwise exempt gallery/logo-wall imagery. Measured
   results are stamped onto the original metadata before final role selection.
6. Classify caption hints conservatively; retain explicit post captions as source
   evidence for the normal page-planning call.
7. Reject detected text/graphics, QR-bearing images and known low-resolution
   images. Deduplicate exact bytes and conservative near-identical fingerprints.
8. Mark qualifying photos as hero candidates; retain other suitable photos for
   supporting/gallery use. Incomplete checks mean backup, not verified acceptance.
9. Send the same photo-ref order and compact evidence into normal planning.
10. Recheck suitability at image-ref binding, candidate ranking and pinned-URL
    resolution so a planner choice cannot bypass the shared gate.

## Hero eligibility

All conditions must pass:

| Check | Rule |
|---|---|
| Pixel checks | OCR and graphic detection both completed |
| Text / QR | OCR explicitly reports no substantial text; no decoded QR |
| Original resolution | Long edge at least `HERO_MIN_BACKGROUND_DIM` (default 1200 px) |
| Shape | Width / height at least `HERO_BG_MIN_ASPECT` (default 1.2) |
| Caption context | A recognised learning/activity topic, without poster, promotion, graduation, class/group-photo, stage or sports-day wording |
| Shared exclusions | No known graphic/logo/decorative role, rejection or privacy-risk evidence |

The final planner may still leave a candidate unused when the caption does not
fit the section. Missing captions, failed downloads, disabled/missing OCR or
graphic detection, and photos outside the budget cannot become automatic heroes.
They can remain clearly labelled supporting-photo backups unless explicitly
rejected. Existing stock/abstract fallbacks handle a missing hero.

The cover is not automatically preferred: a good post photo can win and an
unqualified cover remains supporting imagery. General facilities photos can
support facilities sections but a bare facilities caption does not qualify an
activity hero. Staff/testimonial identity association is never inferred.

## Configuration

Defaults require no vision model or environment edits:

```dotenv
FACEBOOK_PHOTO_REVIEW_MODE=metadata
FACEBOOK_PHOTO_MAX_IMAGES=24
FACEBOOK_VIEWS_TIMEOUT_SECONDS=30
FACEBOOK_VIEW_SCROLL_STEPS=2
OCR_TEXT_DETECTION_ENABLED=true
GRAPHIC_DETECTION_ENABLED=true
```

The Page profile picture supplies the logo automatically when no custom logo
has been uploaded. A custom upload takes precedence. The profile picture stays
outside the section/gallery photo pool and retains the existing decoded-size
logo validation. An empty usable photo pool produces a visible preview warning.

The scrape cache includes the collection version, limits and review mode. Starting
a new scrape after this update does not reuse an older About-only result. An
already generated preview still needs a fresh scrape and regeneration.

No endpoint or database migration is required. Existing optional scrape fields
are retained; only internal cache-key options and bounded collection behaviour
change. The public CMS/runtime contracts are unchanged.

OCR dependencies are already declared in `backend/requirements.txt`. If the
runtime cannot load them, metadata mode reports incomplete checks rather than
silently qualifying photos. Raising the photo cap increases image downloads and
CPU work, not generative-model calls.

The former vision path is retained as an explicit option:

```dotenv
FACEBOOK_PHOTO_REVIEW_MODE=vision
LLM_VISION_MODEL=<installed-image-capable-model-id>
```

Mode `vision` retains the original visual-scoring requirements. No fallback from
metadata mode starts a vision model automatically. Existing persisted vision
reviews are not overwritten by weaker metadata evidence. The general generator's
other vision features remain governed by their existing configuration.

After changing environment settings, recreate the backend container (a simple
restart does not reload Docker's environment), or restart a manually launched
backend with the new environment. Re-fetch previously cached Facebook scrape
results to obtain the new assessment; it does not rewrite an existing website.

## Contracts and compatibility

- No new endpoint, database migration, public-runtime contract or publishing
  behaviour. No new source facts are fabricated.
- Existing `facebook_photo_selection` is extended with `assessment_method`;
  successful metadata results use `review_status: metadata_assessed`.
- Each image record states its assessment method. Metadata `scores` is `{}`:
  measured checks do not masquerade as aesthetic/visual scores.
- Existing source image metadata carries the review and caption evidence through
  the current scrape-preview/generation flow; refs retain their normal ordering.
- Planner payloads add bounded caption evidence for Facebook photos, and batching
  accounts for the actual serialized image-prompt size to avoid underestimated
  context budgets. Non-Facebook prompt behaviour is retained.
- OCR and graphic screen functions accept prefetched original bytes. Existing
  callers remain compatible, including the generic vision-thumbnail OCR path.
- Pure pixel measurements use a bounded 256-entry process cache; existing OCR and
  graphic caches remain bounded. Captions are classified afresh so changed post
  text cannot reuse stale caption decisions. Original bytes are not retained in
  this cache. Images larger than the existing byte limit remain unmeasured.
- Facebook rendering remains partial. A post's caption comes from the page's
  inline data (the DOM decides which posts are visible); it neither invents
  absent captions nor traverses albums or bypasses access walls.

## Files and responsibilities

| File | Responsibility |
|---|---|
| `backend/app/services/facebook_photo_metadata.py` | Bounded CPU checks, measurement cache and conservative caption hints |
| `backend/app/services/facebook_photo_selection.py` | Mode dispatch, duplicate selection, report and the shared eligibility gate |
| `backend/app/models/facebook_photos.py` | Additive review method and measured-check state |
| `backend/app/services/facebook_source.py` | Attach bounded actual post-caption evidence |
| `backend/app/services/facebook_render.py` | Read explicit visible post-message text |
| `backend/app/services/image_graphics.py` | Reuse original prefetched bytes, preserving alpha |
| `backend/app/services/text_detection.py` | Accept original-byte or existing base64 prefetched payloads |
| `backend/app/services/image_vision.py` | Reuse the same fingerprint helper for bytes/base64 |
| `backend/app/services/source_router.py` | Single compact image-prompt projection shared by planning and budget estimation |
| `backend/app/services/planner.py` | Existing-call caption context and accurate Facebook image-token estimates |
| `backend/app/services/prompts.py` | Text-only caption matching instructions and inference boundaries |
| `backend/app/config.py`, `.env.example` | Metadata default, explicit vision mode and bounded photo limit |
| `backend/app/services/facebook_orchestrator.py` | Accurate photo-selection progress label |

## Validation and limitations

The focused regression run passed 287 tests. A direct check using the installed
local OCR engine also rejected a synthetic 1600×1000 text poster from hero and
gallery use. This verifies the real CPU screening path, not a live Facebook read
or the appearance of a generated website.

Focused tests cover one-download reuse, cache reuse, default metadata mode with
no vision invocation, actual decoded dimensions, budget priority, caption exclusions,
OCR/download failure, text/QR/shape/resolution vetoes, persisted ref/ranking guards,
caption prompt wiring, and the retained explicit vision path.

These checks cannot reliably assess blur, natural expressions, clutter, awkward
face crops, logo watermarks in photographs, sensitive small text, consent or
authenticity/reposted stock. No such validation is claimed. A qualified hero is a
metadata-based candidate, not a visually reviewed photograph. Stock imagery must
still never be presented as the centre's actual facilities, staff or students.
