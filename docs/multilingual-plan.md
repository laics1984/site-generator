# Multilingual generation — plan

Status: **proposed 2026-10-16.** Q2–Q4 decided (all recommended options:
primary at root; AI-translate gaps with a badge; users may add languages).
Q1 awaiting the UX answer in "Shared body — what the owner sees". Nothing below
is implemented yet unless it's listed under "What exists today".

## What exists today (measured, not assumed)

The generator already has a partial multilingual path, and it decides the
shape of everything else:

| Layer | What it does | Where it stops |
|---|---|---|
| Crawl | `locale.locale_segment` recognises `/bm/…`, `/zh/…`, `/fr-fr/…`; `scraper` defers mirrors behind untranslated pages | Detects languages **only from URL prefixes**. No text-level detection anywhere. |
| Inference | `page_inference.translation_pairing` pairs a mirror with its counterpart (needs evidence); scaffolds carry `locale` + `translation_of` | Codes are the source's spelling (`bm`, `cn`, `tw`), not BCP 47 |
| Generation | `translations.build_translated_pages` **clones** the finished PagePlan and swaps prose only (allowlisted fields), preferring the owner's own wording; hrefs localised | Mirrors only — no detection, no strategy choice, no bilingual mode |
| Menus | `menu_builder` keeps translated pages out of primary/footer; a "Language" utility menu links each locale's **home** | Switcher loses page context (`/zh/about` → `/zh`) |
| Push | Each mirror is pushed as an **unrelated CMS page** with slug `bm/committee` | `locale`/`translation_of` are dropped; the CMS doesn't know they're translations |
| CMS (`pages`) | One row per `(entity_id, slug)`, own body + SEO; layout (header/footer/menus/styles) is entity-level | No locale on pages or entities (`defaultLocale` = Laravel's `app.locale`) |
| Public | `usePublicSeo` is the single SEO home; sitemap from the routes manifest | `<html lang>` is always `en`; no hreflang, no `xhtml:link` in sitemap |
| Builder | — | No locale concept |

Live samples (2026-10-16, homepages via httpx):

- **mmta.org.my** — `/bm` and `/zh` mirrors of every page. **Every** language
  version declares `<html lang="en">`, and none declares hreflang. The declared
  `lang` is a claim, not evidence (same lesson as `sizes` and `og:image`).
- **uniquekindies.com** — one URL, two languages *on purpose*: headings are
  `Programmes | 课程`, `Curricular Activities | 课间活动`; prose is English in
  some sections, Chinese in others (1,174 English prose chars vs 390 Han
  chars); testimonials Chinese only. Declares `lang="en"`. This is Mode B.
- **watr.org.my, mykiddyland.com** — monolingual English (Mode A).

Malay is Latin script (`/bm` read as "English" by any script test), so script
detection alone cannot separate en/ms — word-level statistics are required.
The local crawl cache is empty (post-migration), so thresholds below get
calibrated on a capture set in Phase 0, not on a guess.

## Architecture

```
SourceContent (any reader: crawl / doc / Facebook / paste)
  → language_analysis.analyze(source)          ONE home, deterministic, no LLM
      → LanguageProfile on SourceContent       (per block → per page → site)
  → multilingual_strategy.decide(profile, override)
      → MultilingualPlan {mode, primary, locales, url aliases, reason, confidence}
  → existing pipeline plans the PRIMARY locale only (unchanged cost)
  → translations.build_translated_pages        EXISTING clone-and-swap, generalised
      → one PagePlan per (page, locale), identical structure by construction
  → schema_builder
      Mode C: one tree per page + per-locale string overlay (derived by parallel walk)
      Mode B: overlay rendered INLINE as lang-tagged secondary lines
  → push: page body once, overlays per locale, locale config on the entity
  → cms-api merges overlay at serve time → webtree-public renders a normal body
```

### 1. Language analysis (`services/language_analysis.py`)

- **Script pass first** (Unicode blocks: Han, Tamil, Arabic, Thai, Hangul, Kana,
  Devanagari…) — exact and free. Han script split Simplified/Traditional with a
  small differential character table (~300 chars that differ), never assumed.
- **Latin text** through `py3langid` (pure Python + numpy, ~2 MB model,
  deterministic, restricted to a configured candidate set). Malay vs Indonesian
  is reported as `ms` unless market evidence (`detect_market`) says Indonesia.
- **Units, not pages**: each `OutlineBlock`/heading/paragraph/nav label/CTA is
  classified; very short units (< ~12 letters) inherit their neighbours' language
  rather than being guessed.
- **Exclusions before counting**: brand name and `og:site_name`, person names
  (`profile_text`), addresses, emails/phones/URLs, quoted product names — so
  "Unique Kindies 创立于 2006 年" counts its Chinese, not its brand.
- **Coverage, not presence**: a language counts as *substantial* only past a
  share of the site's prose AND a minimum absolute amount, so one Chinese
  phrase never triggers a Chinese site (Scenario F).
- **Pattern classification** (explainable rules, each with its evidence):
  - `monolingual` — one substantial language.
  - `parallel_urls` — translated mirrors exist (today's `translation_pairing`).
  - `inline_bilingual` — pairs on the same page: a heading split by `|`, `/`,
    `·` with each side in a different language; or adjacent units in different
    languages of comparable length under one heading. Paired units are recorded
    as pairs, keeping source order (Rule 1, Rule 4).
  - `mixed_sections` — two substantial languages, mostly unpaired (uniquekindies
    prose): bilingual presentation, no invented pairs.
  - `incidental` — a secondary language under the threshold: noted, ignored.
- **Output** `LanguageProfile` (mirrored in `frontend/src/lib/types.ts`, like
  `source_kind`): `primary`, `languages[{code, share, confidence, coverage}]`,
  `pattern`, `pairs`, `evidence[]` (human-readable), plus per-page locale.

The declared `<html lang>` and hreflang tags are recorded as **evidence with low
weight**, never as the answer.

### 2. Strategy (`services/multilingual_strategy.py`)

Request field `multilingual: {mode: auto|single|bilingual|separate, locales?}`
on both generate endpoints and the recipe, default `auto`. Auto rules, in order:

1. Parallel mirrors with substantial coverage → **separate** (Mode C).
2. `inline_bilingual` / `mixed_sections` with two substantial languages →
   **bilingual** (Mode B) — preserve the source's presentation.
3. Otherwise → **single**, in the detected primary language (not "English by
   default" — a Chinese-only source yields a Chinese site).
4. Confidence below threshold → the *least destructive* of the candidates
   (keep source text as written, no added locales), and the preview says so.

Every decision carries `reason` and `confidence`, shown in the preview and
recorded on the `DesignManifest`-style decision log.

### 3. One structure, per-locale text

The clone-and-swap design already guarantees identical structure; what's
missing is keeping it identical **after** generation. So the CMS stores:

- **One body per page** (as today) — layout, components, images, styles.
- **A per-locale overlay** per translatable object: `{element_id: {field: text}}`
  plus title, SEO title/description, slug alias. Same for the layout version
  (header/footer copy, menu labels). An overlay entry may also say
  `hidden: true` — the one sanctioned per-language difference, for content
  that only exists in one language (Phase 13's configured exceptions).

Changing the layout updates every language (there's one body); editing Chinese
writes only the Chinese overlay. A source element with no overlay entry is a
**missing string** — counted, shown in the builder, and it keeps the page out
of hreflang until resolved or explicitly accepted.

The generator derives overlays by walking the source tree and each clone's
tree in parallel. The shapes are equal by construction, so a shape mismatch is
a bug and fails loudly in tests.

**Mode B uses the same overlay**, rendered differently: `schema_builder`'s
bilingual pass emits the secondary-language text as a subordinate line under
headings/labels (paired paragraphs in sequence), each tagged with
`BuilderElement.lang` so renderers emit `lang="zh-Hans"`. One representation, two
presentations, one translation path.

Translation status per string: `source` (owner's own wording), `ai`,
`edited`, `outdated` (source text hash changed since). Re-push fills gaps and
refreshes `ai`/`outdated` strings, and **never** overwrites `edited` — the
`_push_industry` rule.

### 4. URLs

- Internal codes are BCP 47 (`en`, `ms`, `zh-Hans`, `zh-Hant`, `ta`), one
  registry in `locale.py` (merging today's `LOCALE_LABELS` and the `bm`/`cn`/`tw`
  aliases).
- Path prefixes are a per-site alias map: `zh-Hans → /zh/` unless Traditional
  is also present (`/zh-hans/`, `/zh-hant/`). **A source that already used
  `/bm/` keeps `/bm/`** — its indexed URLs survive with no redirect.
- Same slug under every prefix (`/zh/about`); no `?lang=`; no IP or
  `Accept-Language` redirects; no cookies.
- Root policy: see **Q2**.
- Existing pushed mirrors (`bm/committee` pages) are **adopted** on the next
  push: the mirror page is archived and its URL is now served by the overlay
  under the `bm` alias — same URL, now related (the `_adopt_as_template`
  precedent).

### 5. SEO (webtree-public `usePublicSeo` + sitemap, the existing homes)

- Page payload gains `locale` and `alternates[{hreflang, href}]`, computed by
  cms-api from **published** overlays only.
- `<html lang>` = the page's locale; `dir="rtl"` when the locale is RTL.
- Reciprocal hreflang including self, absolute URLs, `x-default` → the root
  page (Q2).
- Canonical = the locale's own URL, never the primary's.
- Sitemap: every published locale URL, with `xhtml:link` alternates; noindex and
  unpublished excluded (existing filters reused).
- JSON-LD: one per locale page from `seo.py`, facts (address, phone, hours)
  identical, text fields localised, `url`/`inLanguage` per locale.
- Secondary-language runs on a Mode B page carry `lang` attributes.

### 6. Rendering

- **Switcher**: generated from the entity's locale config, not a hand-built
  utility menu; links to the *same page* in each locale (shared page id), hides
  a locale where that page has no published translation, real `<a href>`s,
  `aria-current`, `hreflang` on each link. Replaces `menu_builder`'s
  home-only switcher.
- **Internal links** are localised by cms-api at merge time (so a link the owner
  adds in the builder is localised too), falling back to the primary page —
  today's `_localize_href` rule, moved to where every page passes.
- **Fonts**: the theme's faces rarely cover CJK/Tamil; `:lang()` rules append a
  script-appropriate fallback (Noto Sans SC/TC/Tamil/Arabic) to the body and
  heading font stacks. Three-place CSS duplication (PublicSiteShell, builder
  `index.css`, generated `preview.css`) — the existing adaptive-ink idiom, with
  its drift test.
- **Text length**: no fixed heights on text are introduced; the existing
  clamp/overflow rules apply. Verified per locale in the preview harness at
  mobile/tablet/desktop (Scenario J).

### 7. Builder

Locale tabs on a page (`[English] [Bahasa Melayu] [中文]`). In a secondary
locale, text edits write the overlay; structural edits (add/move/delete/style)
are allowed and apply to the shared body for all locales, with a banner saying
so. Per-page badges: missing / outdated / AI / edited.

#### How the builder does it: a lens at load and save

The builder has one load seam (`applyLoadedPagePayload` → `LOAD_DATA`) and one
save seam (`savePageDraft`), and every edit goes through the reducer in
between. The language switch lives **at those two seams only**:

- **Load in 中文** = the page body with the Chinese overlay merged in. The
  editor state is an ordinary element tree, so all ~40 reducer actions, undo,
  the canvas, the panels and the catalog work unchanged.
- **Save / switch language** = split the edited tree back: translatable fields
  (`content.innerText`, link/button labels, `alt`, `title`, placeholders — one
  allowlist, drift-tested against the generator's `_TRANSLATABLE_FIELDS`) that
  differ from the base go to the overlay; everything else goes to the shared
  body. Both are saved in **one** request, so the `draftVersion` conflict check
  still covers the pair.
- **New elements created in a secondary language** (add, duplicate, insert a
  section) put their text in that language's overlay; the base language gets
  the same text marked "needs translation". A duplicate carries the original's
  text in every language it had.
- Switching language commits the split and starts a fresh undo stack, the same
  as opening another page.
- Header, footer and menu labels use the same lens on the layout save.

This is the model Webflow Localization and Wix Multilingual use, so it matches
what owners of those tools already expect. Concretely:

- **One language switch at the top of the editor**, next to the device toggle.
  Picking 中文 shows the same page with Chinese text in place. Nothing else in
  the UI changes, so there's no second page to find in the page list.
- **Typing edits only the language you're viewing.** Click a heading in 中文,
  type, done; English is untouched. That's the common edit and needs no
  explanation.
- **Moving, adding, deleting or restyling a section affects every language**,
  and the editor says so the first time per session ("Layout changes apply to
  all 3 languages"). This is the point of the model: fix the hero spacing once,
  not three times.
- **A new section added while viewing English** appears in 中文 with an amber
  "Needs translation" marker on its text, and the page's language tab shows a
  count ("中文 · 2 to translate"). Hovering shows the English text.
- **Content that only makes sense in one language** (a Chinese-parents notice):
  "Hide in other languages" on the section. One checkbox, not a separate page.
- **The page list** shows each page once, with small language pills (EN ✓ ·
  BM ✓ · 中文 ⚠) so gaps are visible at a glance.
- **Publishing** publishes all languages of a page together; a language with
  untranslated text stays live in its last complete version and out of the
  hreflang set until it's finished, so a half-translated page never goes live.

The one way this is less friendly than separate copies: an owner who wants a
*different* layout per language can't have it, except by hiding sections per
language. Per-language styling tweaks are deliberately out of scope;
divergent layouts are what the spec's "one website" rules out. "Regenerate
translation" is **out of scope here** (it needs an LLM in cms-api or a builder →
generator call); listed under future work.

## Phases

| # | Repo(s) | Deliverable | Visible effect |
|---|---|---|---|
| P0 | generator | Capture set (gitignored, real copy) + replay script; synthetic test fixtures for Scenarios A–F | Measured thresholds |
| P1 | generator | `language_analysis`, BCP 47 registry, `LanguageProfile` on `SourceContent`, preview card ("Detected: English, 中文 — bilingual headings") | Detection shown; generation unchanged |
| P2 | generator | Strategy + override UI; Mode A in the detected language; Mode C for mirrors via existing clone; Mode B bilingual pass + `BuilderElement.lang` (4-place whitelist + builder schema parity) | Correct site per mode **in the preview** |
| P3 | cms-api | `entities.locales_json` (primary, enabled, aliases, root policy); one `translations` table (`translatable_type` page/layout, id, locale, overlay, status, source hash, published overlay); prefix-aware `PublishedPageQuery`; overlay merge + link localisation; routes manifest per locale; mirror adoption | Localised URLs resolve |
| P4 | public (+ preview mirror) | lang/dir, hreflang, canonical, sitemap alternates, switcher, `:lang()` font fallbacks | Crawlable, valid international SEO |
| P5 | generator | Push overlays + locale config; per-locale SEO + JSON-LD; sync rules (never overwrite `edited`) | End-to-end publish |
| P6 | builder | Locale tabs, overlay editing, status badges | Owners edit languages independently |

P1–P2 change nothing for a monolingual source (Scenario A is a regression
test, not a feature). Each phase states before/after on the capture set.

## Cost

Detection: zero LLM calls. Strategy: zero. Translation: one call per
(page, locale), as today, cached through `chat_json_cached` on the page's content
hash; owner-supplied wording is reused, not re-translated; unchanged pages on
re-push cost nothing (source hash). No second design or imagery pass per
language — ever.

## Test map

Scenarios A–F, H → `test_language_analysis.py`, `test_multilingual_strategy.py`,
`test_translations.py` (extended), `test_seo.py`. G (switcher) and H (hreflang,
sitemap) → webtree-public vitest + cms-api feature tests. I → the existing suite
unchanged plus a mirror-adoption test. J → preview harness screenshots per
locale × breakpoint.

## Decisions needed

**Q1 — How is per-language content stored?**
- **(Recommended) One shared page body + per-language text overlay.** Layout
  edits reach every language; single source of truth; matches the clone design.
  Cost: a `translations` table, overlay merge in cms-api, builder locale mode.
- Linked page copies (one CMS page per language, related by a group id).
  Cheaper (closest to today's push), but each language's layout drifts the
  moment an owner edits one — the duplication the spec rules out.

**Q2 — Where does the primary language live?**
- **(Recommended) Primary at the root, others prefixed** (`/about`, `/ms/about`,
  `/zh/about`). Existing sites keep every indexed URL, no redirect hop on the
  homepage, `x-default` = `/`.
- Every language prefixed (`/en/about`) with `/` redirecting to `/en/`. Uniform,
  but moves every URL of every existing site and needs a redirect migration.

**Q3 — A page the source has in English but not in Chinese (separate-pages mode):**
- **(Recommended) Translate the verified English, marked "AI translated"** and
  published with the site; the builder shows the badge. No facts beyond the
  English page (the existing allowlist + restore guarantees it).
- Only publish pages the source actually has in that language; the switcher
  hides the rest.

**Q4 — Can a user add a language the source doesn't have** (an English site →
also Bahasa Melayu)?
- **(Recommended) Yes**, via the override's language picker (Rule 3's "explicit
  user request"); same translation path.
- No — only languages detected in the source.

## Future work (not in these phases)

Regenerate-translation from the builder; localised slugs (`/ms/tentang-kami`);
per-locale articles/events (CMS content types); translation of image text;
locale-specific contact forms/WhatsApp copy.
