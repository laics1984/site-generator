# Multilingual generation — plan

Status: **approved 2026-10-16** — all five decisions took the recommended
option: Q1 shared page body + per-language text overlay; Q2 primary language at
the root, others prefixed; Q3 AI-translate gaps with a badge; Q4 users may add
languages; Q5 more than one language is the `multilingual_site` plan feature.
Work happens on `feature/multilingual` in every repo. **P0–P1 done in
site-generator (2026-10-15)** — detection is shown in the preview, generation is
unchanged; see "Progress and corrections" below. Real-site captures were
taken locally on 2026-10-16 and two gaps they exposed are fixed (see
"Captured locally"). **P2 done 2026-10-16** (strategy, override UI, three
modes in the preview — see "P2"). **P3 done 2026-10-16** in cms-api (storage,
locale-aware public payloads, admin endpoints, plan gate — see "P3").
**P4 done 2026-10-16** in webtree-public (lang, hreflang, sitemap, language
menus — see "P4"). **P5 done 2026-10-16** (push of overlays, locale config,
mirror adoption, language menu — see "P5"). P6 (builder) next. Nothing else below is implemented yet unless it's listed
under "What exists today".

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

## Progress and corrections

### P5 (site-generator push + one cms-api rule, 2026-10-16)

**Built.** A translation is now text on its page end to end:
`services/locale_overlay.py` (derive / apply / missing / source hash, the
Python mirror of `OverlayApplier`); `plan_to_site._translated_pages` builds each
translated page from its counterpart's finished tree; `complete_translations`
fills what the planner's strings didn't cover, header and footer included; the
push (`_push_languages`) sends the language config and every page and layout
overlay to the P3 endpoints and publishes when it may; `cms_sync.primary_pages`
keeps translations out of the page sync, which is what archives an old mirror
page; the language menu is `purpose: "language"`; the preview computes
alternates (`previewLanguages.ts`).

**Decisions and corrections:**

1. **A translated page is derived, not rendered.** Building it separately could
   pick other templates or photos (per-slug seeds, the image resolver's
   reservations); derived from the finished tree it can only differ in words,
   and the preview is the shared tree plus the overlay the CMS will serve.
2. **A model's unchanged answer is a translation.** "UNIQUE KINDIES" or a
   parent's name stays as it is, and is recorded as covered, or the CMS's
   completeness check would refuse to publish every page that names anyone.
3. **Saving the language config is not plan-gated** (cms-api 789293b). Q5 says a
   site without the plan keeps its translations as drafts; drafts need the
   language enabled, so gating the config made that impossible. Publishing and
   serving stay gated.
4. **The pushed overlay replaces the stored draft wholesale.** The page body it
   is keyed to is itself replaced by the push, so old element ids name nothing;
   preserving an owner's edited strings across a regeneration needs stable
   element ids, which the generator doesn't have (future work).
5. **`PagePlan` fields the pipeline owns are `SkipJsonSchema`** — P2's
   `translation_status` and `inline_translation` had been advertised to the model
   in the structured-output schema.

**Not covered:** the preview shows the shared (primary) header and footer on a
translated page — the layout overlay is applied by the CMS, not by the preview
yet.

**End to end (2026-10-16).** Local CMS migrated (the two P3 migrations only),
the branch API served from the cms-api worktree, uniquekindies generated as
`separate` en + zh-Hans (home, about, privacy + 2 translations) and pushed
with the real push code. First push without the plan feature: 3 pages, 3
translation drafts with a warning, `/zh/about` a 301 to `/about`. On Growth:
3 translations published, `/zh/about` served in Chinese (38/38 strings, own
canonical, en + zh alternates, Chinese menu labels); translated element ids ⊆
the English page's, the difference exactly the hidden headline accent. An
update push re-published all three and reused 17 of 19 photos. Rendered by
webtree-public: `<html lang="zh-Hans">`, and — after the fix below — the
switcher on both pages, each item pointing at the reader's own page with
`hreflang`/`lang`, the current one `aria-current`.

Two defects it found, both fixed: the CMS localised the language menu's own
links (English → `/zh`; cms-api 60c6cb0), and **no header archetype had a
node for the utility slot the language menu rides**, so it was pushed and
rendered nowhere (builder c23833c, site-generator 3682242 — see CLAUDE.md).
Not fixed: the phone Menu sheet titles the languages "Utility"; on a site with
no CTA the sheet's button is the brand-name link (pre-existing, every site);
a header archetype swapped in the builder carries no language node (P6).

### P4 (webtree-public + preview mirror, 2026-10-16)

**Built** on webtree-public `feature/multilingual` (06a2af2). `lib/languages.ts`
is the one reader of `page.alternates`: `languageAttrs` (`<html lang dir>`, the
page's own language first), `hreflangLinks` (reciprocal, self included,
`x-default` → the primary page, absolute URLs only, never on a noindex host),
`sitemapAlternates` (`xhtml:link` per URL; a one-language sitemap is
byte-identical), and `resolveLanguageMenus`. `ElementRenderer` binds an
element's own `lang`/`dir` once, as a fallthrough attribute. Mirrored into
`frontend/src/preview/` (`lib/languages.ts` vendored verbatim and drift-tested;
React has no fallthrough, so the text block states it — only text carries
`lang` today).

**The switcher is a menu with `purpose: "language"`**, not a component of its
own: its items are authored, one per language with a `locale` and a label in
that language, so the words stay editable and translatable in the builder and
it sits in any header slot an archetype already renders. The renderer only
decides where each item points — THIS page in that language — leaves out a
language the page isn't published in, and shows nothing on a one-language page.
Links carry `hreflang`, `lang` and `aria-current`, in the mobile sheet too. The
generator's home-only "Language" utility menu moves to this shape in P5.

**Verified** on a node-server build against a mock API returning the P3
payload shape: `<html lang="ms" dir="ltr">`, hreflang en / ms / x-default,
canonical `/bm/about`, the menu linking `/about` and `/bm/about` with
`aria-current` on Malay, Chinese (unpublished) left out, `lang="zh-Hans"` on
the second-language line, `xhtml:link` alternates in the sitemap. Tests: 12
new; the 9 failing on the branch are the colour-system fixtures and a scratch
probe, failing before the change too.

**Deferred: script-specific font fallbacks.** The generator writes each font as
a literal stack on the element (`"Sora", system-ui, sans-serif`); a `:lang()`
rule cannot append to an inline `font-family`, and CJK/Tamil glyphs already
fall back to the OS font through `system-ui`. Doing better means loading Noto
faces per language — a weight and licence decision of its own.

### P3 (webtree-cms-api, 2026-10-16)

**Built** on cms-api `feature/multilingual` (0ed02f8); the contract is
`specs/multilingual-sites.md` there. Storage as planned: `entities.locales_json`
(read only through `App\Support\SiteLocales`) and one `translations` table.
`LocalizedPageLookup` answers "which page, which language" for both public
endpoints; `OverlayApplier` puts a language's text on the shared body, header,
footer and menus (text fields only); `PublishedTranslations` reads published
overlays; `TranslationService` is the admin side. Payloads gain
`page.locale` and `page.alternates` (`[{locale, path, href}]`), the resolve
endpoint `locale`, the route manifest one route per published translation with
`alternates`. `PlanFeature::MultilingualSite` gates publishing and turning a
second language on; without it a prefixed URL 301s to the primary page.

**Decisions made in the building:**

1. **An exact page wins over a language prefix.** Sites pushed before this
   carry `bm/committee` as an ordinary page; it keeps resolving until P5's push
   adopts it, so no indexed URL breaks in between.
2. **A translated URL with no PUBLISHED translation is a 404**, never the
   primary text (Phase 9's "don't silently display another language").
3. **Publishing is per translation and refuses incomplete ones** unless asked
   (`allowIncomplete`); it records the page revision it was made over, and a
   later republish of the page reads as `outdated`.
4. **A translated page's canonical is its own URL** — the primary page's custom
   `seo_canonical_url` is not inherited, or every language would point at it.
5. **Plan packaging: Growth and Business include `multilingual_site`, Starter
   doesn't.** One line per plan in `config/plans.php` if that should change.

**Not covered yet:** per-language articles and events; serving the "last
complete version" of a translation while the base page has newer untranslated
strings (today new strings show in the primary language until the translation
is republished). Nothing renders `page.alternates` yet — that is P4.

### P2 (site-generator + builder type, 2026-10-16)

**Built.** `services/multilingual_strategy.py` (`decide`, `with_plan`,
`addable_locales`); `MultilingualChoice` / `MultilingualPlan` /
`InlineTranslation` in `content_blocks.py`, `GeneratedSite.multilingual`,
`PagePlan.translation_status` / `inline_translation`, `BuilderElement.lang`
(mirrored in builder `site-navigation.ts` on its `feature/multilingual`);
`locale.url_prefix`; `page_inference.translation_scaffold` /
`scaffolds_for_languages`; `services/inline_translation.py`; the string-based
`translations.py`; `planner._write_in`; the recipe's `multilingual` +
`available_locales`; `LanguagePlanCard` in the page picker.

- **Mode A** — the planner is told the language (`write_in`), never left to
  infer it from a mixed source.
- **Mode C** — the recipe adds every page in every language (Q3); a page's
  `locale` is BCP 47 and its URL directory lives in its slug, so a source's
  `/bm/` survives as `ms` at `bm/…`. Not exercised end to end on a real site:
  the two-page preview never selects translation pages, and a full mmta build
  is 30+ pages on the local model. Unit-tested.
- **Mode B** — a string map per page, applied to the built tree as
  `lang`-tagged lines (§3's "one representation, two presentations").

**Measured on uniquekindies.com** (two-page preview, local Qwen3.8-27B,
auto → bilingual en + zh-Hans; a "string" is one text element or one split
headline, counted as the reader sees it):

| Run | Home: strings with a Chinese line | About | Primary strings with Han | `<h1>` per page |
|---|---|---|---|---|
| 1 (blocks sent whole) | 0 / 39 | 0 / 40 | 0 / 1 | 1 / 1 |
| 2 (strings) | 18 / 27 | 0 / 38 (fenced reply) | 0 / 1 | 1 / 1 |
| 5 (+ fence strip, numbered reply, owner pairs, quotes) | **37 / 44** | **30 / 37** | 0 / 0 | 1 / 1 |

Everything still without a line in run 5 is a default section eyebrow
("About", "Services", "Features", "Testimonials"), a brand ("GlobalArt") or a
person's name — the last two correctly so. Runs vary in page shape because the
planner re-writes each time; 317–842 s per two-page run on the local model.

**Corrections the runs forced:**

1. **The translation pass sent whole blocks and asked for them back** (it
   predates this plan). The model blanked `visual_policy` on every block; one
   invalid field failed the whole page, which shipped untranslated. It sends a
   numbered list of strings now and swaps them field by field — the model never
   sees a field it could break. This also fixes the mirror path (mode C).
2. **The local client never stripped a markdown fence** before validating;
   `llm._validated` does, for every client.
3. **The model mirrors the input's shape** — numbered `strings` came back as a
   numbered object. That is now the contract (`TranslatedStrings` takes either).
4. **`write_in` is not obeyed perfectly**: the planner copied an owner's
   bilingual heading verbatim. On a bilingual site that is the owner's pair, so
   it is split by script and kept word for word (`split_owner_pairs`).
5. **`headline` is already the whole sentence** — `headline_accent` is a
   trailing phrase of it, not a continuation. A change that translated
   `headline + accent` as one string was wrong and is reverted (it cost run 4
   its hero lines); the split group joins back to `headline`, which is the key.

**Not covered yet:** section eyebrows a mapper fills from a default ("About",
"Services") are not block text, so they stay in the primary language — all 7
untranslated lines on /about in run 5. Renderers don't emit `lang` as an attribute
yet (P4), and the preview shows the lines but not the attribute.

### P0–P1 (site-generator, 2026-10-15)

**Built.** `services/language_analysis.py` (`analyze`, `with_language_profile`);
the BCP 47 registry in `services/locale.py` (`LOCALES`, `LOCALE_ALIASES`,
`canonical_locale`, `is_rtl`; `LOCALE_LABELS` is gone, `locale_label` and
`locale_segment` keep their signatures); `LanguageProfile` /
`LanguageShare` / `LanguagePair` / `PageLanguage` and `SourceContent.declared_lang`
+ `.language_profile` in `models/content_blocks.py`, mirrored in
`frontend/src/lib/types.ts`; `LanguageCard` in the scrape preview;
`scripts/capture_language_source.py` → `backend/.language-captures/`
(gitignored) and `scripts/measure_language_analysis.py` (replays captures,
`--fixtures` replays the committed Scenarios A–F in
`backend/tests/fixtures/languages/`); `tests/test_language_analysis.py`.

**One seam.** The profile is computed in `source_preview.source_preview_payload`,
which the crawl, document, Facebook, paste and merge endpoints all already call,
so there is no per-reader call. The reader's brand candidate (og:site_name, a
Page's name) is passed in and excluded. Nothing in planning, generation or push
reads the profile yet.

**The captures were NOT made.** The sandbox's network policy refused all four
hosts (proxy 403 on CONNECT; `capture_language_source.py https://mmta.org.my`
fails DNS inside it). Nothing below is measured on mmta, uniquekindies, watr or
mykiddyland. The first job of the next session with network access is:

    cd backend && .venv/bin/python scripts/capture_language_source.py \
        https://mmta.org.my https://uniquekindies.com https://watr.org.my https://mykiddyland.com
    .venv/bin/python scripts/measure_language_analysis.py -v

then restate the thresholds here with real numbers.

**Captured locally, 2026-10-16** (default crawl cap; replayed with
`measure_language_analysis.py -v`). Before → after the two fixes below:

| Site | Pages | Before | After | Junk pairs before → after |
|---|---|---|---|---|
| uniquekindies.com | 5 | `inline_bilingual` | `inline_bilingual`, en 56% / zh-Hans 44%, 72 pairs (all en↔zh); Simplified 917 vs 0; declared `lang="en"` overruled | 2 → 0 |
| watr.org.my | 8 | `incidental` | `monolingual` | 0 → 0 |
| mykiddyland.com | 31 | `incidental` | `incidental` (0.2% German noise ignored) | 20 → 0 |
| mmta.org.my | 31 | `incidental` — **mirrors missed** | `parallel_urls`: English at the root, `/bm` Malay, `/zh` Simplified Chinese | 13 → 0 |

Scenario fixtures A–F are unchanged by the fixes.

1. **A mirror the crawl deferred was invisible.** mmta's header links
   "Bahasa Malaysia" → `/bm` and "中文" → `/zh`, but mirrors are deferred behind
   untranslated pages, so the 30-page cap was spent before either was fetched.
   Fixed in three places:
   - **Crawler**: each mirror's root (`/bm`, `/zh`) is fetched first — one page
     per language (`scraper._is_language_root`, a `language_roots` queue). Still
     tier 1, so it sorts behind the pages it translates. `EXTRACTION_VERSION` 6.
   - **Coverage**: a language read at its own URLs is substantial when its
     mirror pages hold `MIN_AMOUNT`, whatever its share — the share of a
     deferred mirror is set by the page budget, not by the site (mmta's `/bm`
     is 2% of the crawl).
   - **Unread switch**: a menu link to a bare language directory that the crawl
     didn't read is stated in the reason and evidence ("The menu also links
     to …, which wasn't read") and not counted — Rule 6, surface the
     uncertainty. An ambiguous code (`/it`, `/my`) needs its language label.
2. **Pairs were reading place names.** Two rules, both shape-based:
   - A Latin line whose every word is capitalised and is short (≤
     `NAME_LIKE_MAX_WORDS`) or comma-listed like an address is a name; it
     inherits a neighbour's language instead of being identified ("Kota
     Kinabalu, Sabah" was Malay, "Enrichment Classes" German).
   - Two adjacent units in the SAME script pair only when both are paragraphs
     read at `SAME_SCRIPT_PAIR_CONFIDENCE` (0.9); different scripts keep the
     old rule, since script is exact. A language switch's own labels ("EN",
     "English", "中文") are not counted at all — on a mirror page they sat
     beside the page's copy and paired with it.

Also found while verifying: `tests/test_cms_list_layout.HeadingInkTest::
test_the_preview_token_builder_is_vendored_verbatim` fails against current
webtree-public `master` (#33 added `semanticColorVars` to `lib/styles.ts`; the
preview copy predates it). Pre-existing on `main`, unrelated to this work; the
other 11 suite failures fail on `main` too.

**Calibration so far (synthetic fixtures only).**

| Knob | Set to | From |
|---|---|---|
| `CJK_WEIGHT` | 2.5 (first draft 3.0) | English ÷ Chinese letters for the same copy in the fixtures' parallel pages: 2.71, 2.65, 2.17. The plan's uniquekindies count (1,174 English prose chars vs 390 Han) reads 55/45 at 2.5, 50/50 at 3.0 — both substantial either way. |
| `MIN_SHARE` / `MIN_AMOUNT` | 15% and 240 weighted letters | Scenario F's Chinese (brand excluded, one "欢迎光临") is 3%/15 letters; the smallest real second language in A–E is 38%/415. Wide margin, but untested on real incidental text. |
| `LATIN_MIN_CONFIDENCE` | 0.5 (new) | Every py3langid misreading scored 0.21–0.26 ("Our Programmes" → `de`), every correct paragraph ≥ 0.88. |
| `MIN_LATIN_LETTERS` | 12 (plan's figure) | Kept; see correction 2. |

Before → after on the fixtures (before = the first draft of these rules; the
pipeline itself had no text-level detection at all, only URL prefixes):

| Scenario | First draft | Now |
|---|---|---|
| A English only | `incidental` — "a little Deutsch (3%)" | `monolingual` en 100% |
| B bilingual headings | `inline_bilingual`, primary **zh-Hans** (51/49) | `inline_bilingual`, primary **en** (53/47), 10 pairs, 7 headings |
| C full /zh mirrors | `parallel_urls` en; stray `de` 1% | `parallel_urls` en; zh-Hans coverage 1.0 |
| D en / bm / zh | `parallel_urls` en; stray `de` 1% | `parallel_urls` en; ms and zh-Hans coverage 1.0; declared `lang="en"` flagged untrusted |
| E partial /zh | `parallel_urls`; stray `de` 2% | `parallel_urls`; zh-Hans coverage 0.4 |
| F Chinese brand | `incidental` en, zh 4% | `incidental` en, zh 3% |

**Corrections to the plan.**

1. **Malay and Indonesian are one family to py3langid**, not a tie-break at the
   margin: it reads plain Malaysian copy as `id` ("Tentang Kami" → id 0.42). The
   two probabilities are summed, reported `ms`, and `id` only on
   `detect_market` → Indonesia.
2. **"Under ~12 letters inherits" was not enough.** 13–16 letter headings
   ("Our Programmes", "Full-Day Programme") pass the length test and are still
   misread with low confidence. A reading below `LATIN_MIN_CONFIDENCE` inherits
   too.
3. **The primary of an inline-bilingual source is the language written FIRST
   in its pairs**, not the larger one. uniquekindies is ~50/50 by letters; the
   owner's "English | 中文" order is the evidence that says which leads.
4. **Han with no differential character is reported `zh`, not guessed.** The
   declared `<html lang>` is used for exactly one thing — breaking that tie
   (`zh-TW` → `zh-Hant`, at confidence 0.5) — and otherwise only appears as an
   evidence line ("recorded, not trusted").
5. **`coverage` is defined** as pages carrying the language ÷ pages carrying the
   primary (≥ 15% of a page's text), so 1.0 = every page translated. The plan
   named the field without a definition.
6. **A sixth pattern, `unknown`**, for a source with no countable text, so the
   preview never states a language it did not read.
7. **A `/bm/…` directory written in the primary language is not a mirror.** It
   is reported as an evidence line and not counted (`parallel_urls` needs the
   directory's pages to be in the language it names).

**Visible changes in P1, all deliberate:**

- The registry has one label per language, so `ms` and `bm` now share
  "Bahasa Malaysia" (the old `/bm` label, and what mmta's own nav says). A
  source mirrored under **`/ms/`** used to get "Bahasa Melayu" in its language
  menu and now gets "Bahasa Malaysia". Codes the old table lacked (`tl`, `he`,
  `ur`, `fil`) now get names instead of upper-cased codes. Nothing else about
  generated sites changes.
- `scraper.EXTRACTION_VERSION` 4 → 5 (pages now record `declared_lang`), so
  crawls cached under 4 are re-crawled once.

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

### 8. Admin suite (webtreesuite-admin-app)

Settings and reporting only; text editing stays in the builder. Each item
extends a screen that exists today:

- **Site settings → new "Languages" card** (beside Industry): main language,
  enabled languages with their URL prefix (`/zh/`), add/remove a language,
  and a per-language completeness line ("中文 — 2 pages need translation").
  Removing a language unpublishes it, never deletes its text. Writes
  `entities.locales_json`.
- **Webpages list** (`viewWebpagesList`): language pills per row (EN ✓ BM ✓
  中文 ⚠) and a "Needs translation" filter. Still one row per page.
- **Page information form** (`componentWebpageInformationForm`): a language
  switch above the SEO title/description, with `SeoPreview` showing the
  selected language's search result and URL. Writes that language's overlay.
- **Insights** (`viewInsightsPages`): a language filter; per-language traffic
  is grouped by URL prefix, since metrics are already per path.
- **SEO audit** (`viewInsightsSeoAudit`): new checks — hreflang reciprocity,
  a language published with untranslated text, a missing localised SEO title.
- **Not touched**: articles/events (per-language CMS content is future work),
  CRM, inbox, WhatsApp.

Plan gating (Q5, decided): a new `PlanFeature::MultilingualSite =
'multilingual_site'` in cms-api, set per plan in `config/plans.php` and asked
through `EntityEntitlements`, like `custom_domain`. Without it:

- **Generator**: detects, previews and generates every language as usual (the
  owner sees what they'd get). The push publishes the main language and saves
  the others as **drafts**, with a push warning naming the plan feature.
- **cms-api**: refuses enabling a second language in `locales_json` and
  publishing a non-primary translation (the gate lives here, so the builder,
  the admin app and the generator all get one answer).
- **Admin**: the Languages card shows the upsell through
  `featureUnavailableCopy`; the main-language setting stays available on
  every plan.
- **Downgrade**: translations are kept, never deleted. Non-primary URLs
  301-redirect to the same page in the main language (so indexed URLs don't
  404), drop out of hreflang and the sitemap, and come back on re-upgrade.

No dependency on unmerged work: `PlanFeature`, `config/plans.php` and
`EntityEntitlements` are already on cms-api `main` (#123), and
`featureUnavailableCopy` on admin-app `main`.

## Database changes (webtree-cms-api only, all additive)

1. **`entities.locales_json`** — nullable JSON: `{primary, enabled[], aliases{code: prefix}, mode}`.
   `NULL` = a monolingual site = exactly today's behaviour, so every existing
   site is untouched until a push or the owner turns a language on.
2. **`translations`** — one new table: `id`, `entity_id`, `translatable_type`
   (`page` | `layout`), `translatable_id`, `locale`, `draft_overlay_json`,
   `published_overlay_json`, `published_against_id` (the page revision / layout
   version it was published over), `draft_version`, `updated_by`, timestamps;
   unique `(translatable_type, translatable_id, locale)`. Per-string status
   (`source`/`ai`/`edited`) and source-text hash live **inside** the overlay
   JSON, so there's no per-string table and no row-per-word growth.
3. **Unchanged**: `pages`, `page_revisions`, `layouts`, `layout_versions`,
   media, menus. Per-language analytics come free: `page_metrics_daily` is keyed
   by path, and `/zh/about` is its own path.

No stored-data migration converts existing `bm/committee` mirror pages: a
blind slug test can't tell `/bm/committee` from a real `/it/support` section
(the `AMBIGUOUS_LOCALE_SEGMENTS` problem). The generator adopts them on the
next push, where it has the crawl evidence — the `_adopt_as_template` precedent.

Rollback: `down()` drops the table and column; a `NULL` `locales_json` serves
pages exactly as before. Feature tests build their schema by hand in
`tests/Support/BuildsCmsSchema.php`, so the table and column go there too.

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
| P7 | admin-app (+ cms-api endpoints) | Languages card, page-list pills/filter, per-language SEO fields, insights filter, audit checks | Owners manage languages without the builder |

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

**Q5 — Is a second language a plan feature?**
- **(Recommended) Yes**: one language on every plan; more than one is an
  entitlement through the existing `PlanFeature` / `EntityEntitlements`
  machinery (`featureUnavailableCopy` already words the upsell). AI translation is the
  one part of this that costs per site.
- No — every plan gets multilingual sites.

## Future work (not in these phases)

Regenerate-translation from the builder; localised slugs (`/ms/tentang-kami`);
per-locale articles/events (CMS content types); translation of image text;
locale-specific contact forms/WhatsApp copy.
