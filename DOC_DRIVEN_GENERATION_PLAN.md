# Document-Driven Generation — Plan

**Goal.** Make a structured document (PDF / Word) the authoritative content source
for site generation, with an optional "scrape → document" bridge to author that
document from an existing site. The new website content is generated **from the
document**, not by re-scraping the live site.

```
            ┌─────────────── optional bridge ───────────────┐
  URL  ──►  scrape  ──►  planner  ──►  STRUCTURED .docx  ──►  [human edits]
                                            │
                                            ▼
                                    Upload .docx / .pdf
                                            │
                                            ▼
                        outline → pages + scaffolds → website
                                  (the document is the source of truth)
```

The document's headings ARE the page/section structure. The user's edited titles
become the site's pages and sections — they are not re-inferred away.

---

## 1. The canonical title contract (the "set of titles")

A documented mapping both export and import agree on. New module
`backend/app/services/doc_contract.py`.

| Doc element            | Meaning                          | Maps to                         |
|------------------------|----------------------------------|---------------------------------|
| **Heading 1**          | A page                           | `PageScaffold` / `discovered_page` |
| **Heading 2**          | A section within the page        | `SectionType`                   |
| **Heading 3**          | A repeated item (service, member)| list item inside a section block |
| **Normal paragraph**   | Body copy                        | belongs to nearest preceding heading |
| **Optional front block** | Site name / tagline / palette  | `BrandIdentity` seed            |

- Page-type is inferred from the Heading-1 text by reusing the existing keyword
  table in [`source_router._PAGE_TYPE_KEYWORDS`](backend/app/services/source_router.py:35)
  ("About Us" → `about`, "Our Services" → `services`, …). Lift that table into
  `doc_contract.py` and import it from both places so there is one source of truth.
- Section-type is inferred from the Heading-2 text against a small section-title
  keyword map (Hero, Story, Services, Team, Testimonials, FAQ, Contact, Gallery,
  Pricing, CTA) → `SectionType`. Unknown headings fall back to a generic
  text/section type so nothing is dropped.
- **No schema change.** This contract lives entirely in the generator's
  intermediate types. The shared `BuilderElement` renderer contract is untouched.

---

## 2. Structured outline extraction (import side)

Today [`ParsedDocument`](backend/app/services/doc_parser.py:43) flattens to
`raw_text` (newline blob) + `headings` (deduped list). That **loses document
order**, so body copy can't be assigned to the right page/section. Fix:

- Add `outline: list[OutlineBlock]` to `ParsedDocument`, where
  `OutlineBlock = { level: 0|1|2|3, text: str }` (0 = body). `raw_text` and
  `headings` stay for backward compatibility — every existing caller keeps working.
- **DOCX** (`parse_docx`): reliable. We already read `paragraph.style.name`; map
  `"Heading N"` → level N, `Title` → level 1, everything else → level 0. Emit in
  paragraph order.
- **PDF** (`parse_pdf`): approximate. We already compute a body-median font size.
  Bucket distinct sizes above the median into tiers: largest → H1, next → H2,
  next → H3. Document order is preserved by walking blocks/lines as today.
  ⚠️ PDF level inference is heuristic — call this out in UX copy and recommend
  `.docx` for the cleanest round-trip.

---

## 3. Document → discovered pages (examine the titles; do NOT bind structure)

> **Design refinement.** The website's section composition and visual
> distinctiveness must **not** be bound 1:1 to the document's headings. The
> document's titles are a *content signal to examine* — they help decide which
> pages exist and where content lives, but the **existing planner keeps full
> ownership of section selection and design variety**. So there is **no**
> `scaffolds_from_document`. We make the document look like a crawled site and let
> [`infer_page_scaffolds`](backend/app/services/page_inference.py:374) do exactly
> what it already does for scraped sites.

New module `backend/app/services/doc_structure.py`:

- `split_into_pages(parsed: ParsedDocument) -> SourceContent`
  Walk `outline` and **examine each title by meaning, not by heading level**:
  - A title that matches a known page-type (about / services / contact / pricing /
    team / faq / blog / gallery …) opens a new **page bucket**.
  - Any other title is treated as in-page content (folded into the current
    bucket's `headings` + `raw_text`), never forced into its own page.
  - Body paragraphs accumulate into the current bucket's `raw_text`.
  - The leading bucket (before the first page-type title) becomes the **primary
    `SourceContent`** (homepage content); subsequent buckets become
    `discovered_pages`, each with a synthesized `url_path` (slug derived from the
    examined title) + `title` + `headings` + `raw_text`.
  - A document with no page-type titles degrades to a single page → planner uses
    the industry template for a full, distinctive homepage. **Page count is never
    rigidly equal to heading count.**

Because each bucket carries a synthesized `url_path`,
[`infer_page_scaffolds`](backend/app/services/page_inference.py:374) walks them like
crawled pages: it infers `page_type` from slug+title and picks sections via
`_sections_for` / the section catalog — i.e. the doc's H2s do **not** become the
site's sections. [`source_router.match_scaffolds_to_pages`](backend/app/services/source_router.py:98)
then routes each bucket's content to the planner's chosen page.

---

## 4. Wiring generation (the user's stated priority)

- `POST /api/document/preview` ([document.py](backend/app/routers/document.py:43))
  calls `split_into_pages`, so its `SourceContent` now carries `discovered_pages`.
  The existing ScrapePreview → PagePicker → Generate flow then shows the document's
  pages with **zero frontend branching** (the router was deliberately built to
  mirror `/api/scrape/preview`).
- Generation path is **unchanged**: `/api/pages/recipe` →
  [`infer_page_scaffolds`](backend/app/services/page_inference.py:374) → page picker
  → [`/api/generate/with-pages`](backend/app/routers/generate.py:406). The planner
  owns page composition + section distinctiveness exactly as it does for scrapes.
- Content fidelity: treating the uploaded doc's text as source aligns with the
  existing faithful-rewrite policy — the generator rewrites for SEO/grammar but
  does not fabricate. No new behaviour needed there.

---

## 5. Export bridge: scrape → structured .docx

New module `backend/app/services/doc_export.py` + endpoint
`POST /api/document/export`:

- Input: a `SitePlan` (from existing `/api/generate/plan-only`) **or** raw scrape
  `SourceContent` (export runs the planner internally). Output: a `.docx`
  `StreamingResponse`.
- Render with **python-docx** (already a dependency): Heading 1 per page, Heading 2
  per section, body paragraphs of the rewritten copy — i.e. the inverse of §2/§3,
  so what we export re-imports cleanly.
- **Format scope for v1: `.docx` only.** It is the editable intermediate the goal
  calls for. PDF is a read-only deliverable; defer it (would add `reportlab` and
  PDF has no styles to round-trip). Decision flagged below.
- **Images:** v1 references scraped image URLs as captioned placeholders rather
  than embedding bytes (embedding needs a fetch + `add_picture`). Re-import keeps
  using its own image extraction / Pexels matching. Embedding is a fast follow.

---

## 6. Frontend

- Keep the existing **"Upload a document"** tab ([ModeTabs.tsx](frontend/src/components/ModeTabs.tsx))
  as the generation entry point — it already feeds the document path.
- Add a **"Download as document"** action on `ScrapePreview` (after a scrape
  completes) that hits `/api/document/export`. This is the visible "scrape →
  document" bridge.
- Minor copy: note that `.docx` round-trips most reliably; PDF heading detection
  is best-effort.

---

## 7. Impact summary (per repo rules)

- **Files changed:** new `doc_contract.py`, `doc_structure.py`, `doc_export.py`;
  edits to `doc_parser.py` (add `outline`), `document.py` (preview split + export
  endpoint), small `source_router.py` refactor (share keyword table); frontend
  `ScrapePreview.tsx` (export button) + copy.
- **Schema impact:** **none** to the `BuilderElement` renderer contract. All new
  types (`OutlineBlock`, doc-contract constants) are generator-internal.
- **Storage impact:** none. The `.docx` is a transient download, not persisted.
- **UX impact:** document uploads now reconstruct multi-page sites instead of one
  flat page; new export button on the scrape preview.
- **Performance:** export adds one planner pass (same cost as a normal generate);
  outline extraction is linear over the doc. No change to the LLM cost model
  (still driven by pages the user selects).
- **Compatibility risks:** PDF heading-level inference is heuristic (mitigate by
  recommending `.docx`). `ParsedDocument` change is additive/backward-compatible.
- **Preview vs. public link:** entirely generator-side; no internal-preview or
  public-link routing touched.

---

## 8. Build order — status

1. ✅ §1 `doc_contract.py` (reuses `page_inference._TYPE_HINTS`).
2. ✅ §2 `outline` on `ParsedDocument` (DOCX style-levels + PDF font tiers).
3. ✅ §3 `doc_structure.split_into_pages` (examines titles; no scaffold binding)
   + `tests/test_doc_structure.py` (9 tests).
4. ✅ §4 `document/preview` now returns `discovered_pages`; generation path
   unchanged (recipe → picker → with-pages).
5. ✅ §5 `doc_export.build_site_document` + `POST /api/document/export`
   (round-trip verified: source → docx → reparse → split reconstructs pages).
6. ✅ §6 frontend `exportSiteDocument` + "Export as document" button on
   ScrapePreview + ModeTabs copy.

Backend: 222 tests green. Frontend: written, not compiled locally (deps live in
Docker; run `npm run build` in the frontend container to typecheck).

---

## Resolved decisions

1. **PDF export** — ✅ defer; **docx-only v1**.
2. **Image embedding in export** — ✅ URL/caption placeholders v1.
3. **Re-import structure authority** — ✅ **examine titles, do not bind structure.**
   The planner owns section selection and distinctiveness; the doc's titles only
   shape which pages exist and where content is routed (see §3 refinement).
