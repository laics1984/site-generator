# Restaurant menu images

Restaurant generation replays authentic menu images from websites, extracted
PDF/DOCX images and Facebook posts/Photos collections. It does not render every
PDF page as an image or bypass Facebook collection/access limits.

## Pipeline

1. `menu_assets.assess_menu_assets` reads source labels before scaffold planning.
2. The existing bounded CPU OCR pass includes restaurant gallery images and
   retains up to 2,000 characters of confident recognition, grouped into rows.
   These readings use the existing process cache and concurrency limit.
3. Existing optional vision captions can contribute evidence. No extra model
   call is introduced. A source menu label or named dish/price row identifies
   the purpose; a bare price, receipt, ticket or text-free food photo does not.
4. Purpose is separate from image role. `menu`/`menu_review` images cannot fill
   decorative slots through the shared `must_show_whole` guard. Ref ordering
   stays unchanged while asynchronous pixel results arrive.
5. After the selected refs are screened, routing reserves these images globally,
   removes generic gallery/poster copies, and places accepted images once using
   the existing `PosterBlock` and uncropped, lightbox-enabled template.
6. A selected source category Menu page owns its assets. Otherwise the selected
   main Menu page is used. If no Menu page is selected, the homepage receives a
   Menu section after its introduction or existing text menu. A selected Menu
   page in a later preview/extension batch defers placement until that batch.

Image sheets preserve their proportions, original prices and footnotes. Groups
larger than the existing six-image block limit are split into consecutive blocks,
without silently truncating their sheets. Duplicate identity uses existing byte
hash/URL identity handling. There is no stock substitute for an unavailable menu.

## Copy and review

`RESTAURANT_MENU_CONTENT_PROMPT` is stored once in `prompts.py` and injected into
restaurant scaffold planning and conditionally into legacy planning. OCR words
are classification evidence and never become structured menu items or prices.
Text menus remain grounded in explicit source text by existing sanitization.

Explicit promotions and version hints are reserved as `menu_review` and withheld
from regular Menu and decorative/generic image sections. Additive
`GeneratedSite.generation_warnings` reports this in the generator preview.
These hints cannot prove which prices are current: conflicting/unlabelled images
still require owner review. There is no automatic historical-price comparison.

## Contracts and compatibility

Changed files:

| File | Responsibility |
| --- | --- |
| `backend/app/services/menu_assets.py` | Shared classification, reservation and routing |
| `backend/app/services/prompts.py` | Central restaurant menu guidance |
| `backend/app/services/planner.py` | Conditional guidance and compact evidence |
| `backend/app/models/content_blocks.py` | Optional menu-purpose and OCR metadata |
| `backend/app/services/text_detection.py` | Cached OCR text and bounded restaurant gallery screening |
| `backend/app/services/facebook_source.py` | Preserve collected photo captions |
| `backend/app/routers/scrape.py` | Facebook cache version |
| `backend/app/services/source_router.py` | Compact evidence and stable image references |
| `backend/app/services/image_match.py` | Shared decorative-slot exclusion |
| `backend/app/services/legible_images.py` | Menu ownership exclusion and shared poster-item factory |
| `backend/app/routers/generate.py` | Coordinate both generation paths |
| `backend/app/models/builder_schema.py` | Additive generation warning field |
| `frontend/src/lib/types.ts` | Warning response type |
| `frontend/src/App.tsx` | Owner-facing review notice |
| `backend/tests/test_menu_assets.py` | Source, routing and renderer regressions |
| `docs/restaurant-menu-images.md` | Behavior, contracts and limitations |

- Optional image metadata fields: `content_purpose`, `menu_heading`,
  `menu_evidence`, `ocr_text`. Existing source payloads remain accepted.
- Optional/default-empty `generation_warnings` on generated-site responses.
- No new CMS block type, endpoint, DB migration or public-runtime contract.
- Facebook scrape cache collection version advances to 3 to refresh captions.
- OCR caches remain bounded/process-local. Older persisted sources need a fresh
  scrape or upload and regeneration; published sites are not modified.
- Existing stock-images-only mode still removes source imagery, including menus.
- Disabled/failed OCR falls back to explicit source labels and existing vision
  evidence. Uncaptioned/unreadable images outside the OCR budget may remain
  unclassified. Classification does not guarantee all restaurant imagery is a
  menu, or that every menu price can be read by OCR.
- Existing CMS media processing is reused, including its native-format handling
  and maximum image size. Actual price legibility after upload requires checking
  the supplied source image and the resulting public asset.

## Validation

`tests/test_menu_assets.py` exercises all source kinds, actual document-heading
and Facebook-caption paths, single priced food images, receipt/food exclusions,
review warnings, category/homepage/staged routing, all-sheet preservation,
identity deduplication, stable refs, bounded OCR and the existing rendered
whole-image/lightbox template without stock requests. Related OCR, gallery,
scaffold, Facebook, document and staged-generation suites cover regressions.
