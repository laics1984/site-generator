# Section Visual Policy & Luminance-Rhythm Engine — Spec

Revamp of the logic behind section container backgrounds.

**What's wrong today.** "Is this a heavy visual section?" is *inferred at render
time* from whether `styles.backgroundImage` is set
([`schema_builder.apply_section_rhythm()`](backend/app/services/schema_builder.py)).
That heuristic is blind to intent: a split hero carries its photo in a content
column (no `backgroundImage`) and a faint texture *sets* `backgroundImage` for a
subtle reason — so it both under- and over-counts. Backgrounds end up arbitrary,
with no page-wide rhythm and no guaranteed contrast between neighbours or between
a featured image and its own container.

**What this replaces it with.** An explicit per-section **visual policy** set by
the planner (intent only), consumed by a deterministic **page-level luminance
pass** that resolves every section's background **luminance band**, colour, photo
filter, and font colour — under three contrast guarantees and one page rhythm.

> Supersedes the earlier `rhythm_trigger` / "energy-debt" draft. Strict
> luminance alternation makes an adjacency-debt mechanism redundant (§3.6).

If you touch any file in **§9 Responsibilities**, read this first.

---

## 1. Vocabulary (read before anything else)

Two review rounds got confused by overloading "light/dark." These concepts are
**independent** and must stay separate in code and discussion:

| Term | Meaning | Range |
|---|---|---|
| **Luminance band** | The section's *actual rendered lightness*. Drives font colour. | `light \| dark` |
| **Treatment** | The section's *richness / texture*, layered on the band. | `photo \| washed_photo \| grain \| plain` |
| **Anchored / flexible** | Whether the band is *fixed by a featured image* or *free for the rhythm to choose* (§3.2). | `anchored \| flexible` |
| **Source intent** | *What* to search for + where to source it, if a photo is used. | string + `scraped\|stock\|abstract` |

Decouplings established in review:

- A **light**-band section can be rendered with a **dark** colour if a constraint
  forces it (the band is a *target*, not a fixed value). Font always follows the
  **real** colour.
- A **dark** band ≠ dark pixels: a dark-band photo is a *colourful* photo whose
  luminance is normalised by filter/overlay.
- A **light**-band photo is a stock photo under a heavy *light* filter so it reads
  washed — the source photo need not be light.

---

## 2. Decision: explicit policy, not inference

| Approach | Verdict |
|---|---|
| **Query-inference only** — infer everything from `image_query` at render time | ✗ Status quo. Same query → split image / full-bleed hero / washed background. Rules keep leaking. |
| **Page-level engine only** — derive rhythm from `kind` + `layout` | ✗ Relocates the hidden inference. Hard to explain *why* a section came out light/dark/plain. |
| **Explicit per-section policy + page-level luminance pass** | ✓ **Chosen.** Planner declares intent; the pass is deterministic and debuggable. |

`image_query` is *content* (flat on the block). `visual_policy` is *presentation*
(nested). They intentionally look different.

---

## 3. The governing model

### 3.1 Luminance band is the rhythm axis

Every large background-capable section resolves to a **luminance band**
(`light` / `dark`). The page's default rhythm is **strict alternation**:

```
… light → dark → light → dark …
```

but anchored sections (§3.2) can override it, so alternation is **best-effort**,
not absolute (§3.3 precedence).

### 3.2 Anchored vs flexible sections — the core idea

A section is **anchored** if it carries its **own featured/content image** (a
split hero's photo, an image-split about, a feature with a screenshot, etc.).
Its band is **fixed** so the image pops against its container:

> **anchored band = the OPPOSITE of the featured image's measured luminance.**
> featured image reads **light** → container band = **dark**
> featured image reads **dark** → container band = **light**

The anchored section's background is `grain`/`plain` in that band (never a second
competing photo — the featured image is the focus). This is the **image↔container
contrast** (a third contrast pairing, §6 rule 2).

A section is **flexible** if it has no featured image:
- **background-photo** flexible → the photo is *filtered* to whatever band the
  pass assigns (§3.5). We control its luminance.
- **plain / grain / decorative** flexible → palette colour in the assigned band.

> The distinction is *who owns the luminance*: a **content image** is shown
> faithfully, so the **container** must adapt (anchored). A **background photo**
> is ours to filter, so it adapts to the rhythm (flexible).

### 3.3 Band resolution algorithm + precedence

The luminance pass runs in this order:

1. **Classify** every large section as anchored or flexible.
2. **Fix anchored bands** from each featured image's measured luminance (§3.2).
3. **Fill flexible bands** to alternate around the anchors and contrast their
   immediate neighbours; filter flexible photos to their assigned band.
4. **Separator fallback:** where two **anchored** neighbours are forced to the
   **same** band (unavoidable), alternation yields — apply a within-band
   luminance step (`_adjust_lightness` ±small) plus a divider/border so the seam
   stays visible. The featured images themselves also aid separation.
5. **Font colour** from each section's *actual* resolved background (§6 rule 1).

**Precedence (high → low):**

| Priority | Rule | Hard? |
|---|---|---|
| 1 | Font ↔ its own background (readability, WCAG AA) | **hard** |
| 2 | Featured image ↔ its container (anchored band) | **hard** |
| 3 | Strict alternation / adjacent-bg contrast | best-effort (separator fallback) |

### 3.4 Photos filtered to their band (flexible photos)

A flexible photo does not float its luminance — the engine filters/overlays it to
the assigned band: light band → heavy light wash (`washed_photo`); dark band →
colourful photo + darker overlay. Trade-off accepted: a photo may be filtered
hard enough to lose its natural tone; rhythm + readability win over fidelity.

### 3.5 Hero seeds the flexible rhythm

The hero is just the first section, resolved by the same rules:
- featured/split hero → **anchored** (band = opposite of its featured image).
- full-bleed photo hero → **flexible background-photo**, default band **dark**
  (the photo is filtered to read dark).

Whatever the hero resolves to seeds the alternation for the flexible run beneath.

### 3.6 Why there is no "energy-debt" mechanism

Two *equally heavy* dark-band colourful photos can only land in dark bands, and
the default rhythm keeps dark bands apart; opposite-band adjacent photos (a washed
light photo above a colourful dark photo) are explicitly allowed. So the rhythm is
carried by **band resolution + the three contrast rules** — no `rhythm_trigger`,
no recovery debt.

---

## 4. Schema changes

### 4.1 New submodel: `VisualPolicy`

In [`models/content_blocks.py`](backend/app/models/content_blocks.py). Attached as
optional `visual_policy: VisualPolicy | None = None` on every large
background-capable block (§4.2). `None` = "infer me" — preserves byte-identical
output for everything generated today.

```python
class VisualPolicy(BaseModel):
    # --- planner INTENT (what the section wants) ---

    visual_mode: Literal[
        "photo_background",  # wants a dominant background photo (flexible)
        "supporting_image",  # photo in a column / split  → ANCHORED
        "decorative",        # grain / texture, no real photo
        "plain",             # flat colour fill
        "auto",
    ] = "auto"

    image_source_preference: Literal[
        "scraped", "stock", "abstract", "auto"
    ] = "auto"

    # Optional override of the calm (non-photo) rendering. Engine picks if "auto".
    calm_treatment: Literal["plain", "grain", "auto"] = "auto"

    # Escape hatch. Bands are normally DERIVED (anchored from image luminance, or
    # flexible from the §3.3 pass). Set to force a band against the rhythm.
    band_override: Literal["light", "dark", "auto"] = "auto"
```

Final luminance, resolved background colour, photo filter, and font colour are
**derived by the engine** — not stored on the policy.

### 4.2 Blocks that gain `visual_policy`

All large background-capable sections (phase-1 scope): `HeroBlock`, `CtaBlock`,
`AboutBlock`, `FeaturesBlock`, `ServicesBlock` (+ future dominant-background
blocks). **Not** added to small/gridded blocks: `TestimonialItem`, `TeamMember`,
`GalleryItem`, `FaqBlock`, `ContactBlock`.

### 4.3 New field on `ImageMetadata` — band from a dominant-colour hex

To anchor a band we must know the featured image's lightness. The band is a
**1-bit decision** (light vs dark), so a single dominant-colour hex is an
adequate proxy — we do **not** download pixels. Cheapest-source-first:

1. **Stock (Pexels)** — [`PhotoResult.avg_color`](backend/app/services/pexels.py)
   is *already fetched* in the search JSON. `theme._relative_luminance(avg_color)`
   → threshold. **Zero extra network, no new dependency.**
2. **Abstract / generated** — we produce the image, so set its band at generation.
3. **Scraped, no colour hint** — do **not** download; default per §8.4.

> Rejected: downloading a thumbnail + averaging pixels (would add Pillow/numpy —
> not imported anywhere today — plus ~5 HTTP GETs/page and ~0.5–1.5s, to
> recompute a number `avg_color` already gives us, for a binary decision). See
> §10 phase 2 rationale.

Today `ImageMetadata`
([content_blocks.py:608](backend/app/models/content_blocks.py)) carries only
`url/alt/intent/width/height`; `theme._relative_luminance` works on hex. Add:

```python
class ImageMetadata(BaseModel):
    ...
    # Dominant colour hex (e.g. Pexels avg_color, or the generated abstract's
    # base). None when no hint is available (scraped) — band defaults per §8.4.
    dominant_color: str | None = None
    # 0.0 (black)..1.0 (white) from _relative_luminance(dominant_color).
    luminance: float | None = None
    band: Literal["light", "dark"] | None = None  # luminance thresholded
```

### 4.4 Fields dropped vs. the original sketch

- **`rhythm_trigger`** — removed (redundant under band resolution, §3.6).
- **`recovery_preference`** — folded into `calm_treatment`. A washed-light photo
  is just `photo_background` landing in a **light** band.
- **`background_role`** — redundant with `layout` + `visual_mode`.
- **`abstract_stock | abstract_local` modes** — a sourcing decision; lives in
  `image_source_preference`.

---

## 5. Decision matrix

| Section / layout | `visual_mode` | Class | Band | Rendered background |
|---|---|---|---|---|
| Hero, full-bleed | `photo_background` | flexible | dark (seed) | colourful photo, darker overlay |
| Hero, split / featured image | `supporting_image` | **anchored** | opposite of image | grain/plain, photo in column |
| CTA w/ background photo | `photo_background` | flexible | alternation | photo filtered to band |
| CTA, no photo | `plain`/`decorative` | flexible | alternation | brand flat / grain in band |
| About, image-split | `supporting_image` | **anchored** | opposite of image | grain/plain in band, image pops |
| Features w/ screenshot | `supporting_image` | **anchored** | opposite of image | grain/plain in band |
| Features/services, plain | `plain`/`decorative` | flexible | alternation | brand flat / grain |
| Team / gallery grid | *(no policy)* | n/a | n/a | not a participant |

---

## 6. Contrast rules — three pairings (all reuse `theme.py`)

| # | Pairing | Scope | Hard? | Mechanism |
|---|---|---|---|---|
| 1 | **Font ↔ its own background** | within section | hard | `theme._text_for_background()`; `_ensure_contrast_against()` nudges a failing brand colour (hue preserved). Holds even when a light-band section is forced dark — font flips. |
| 2 | **Featured image ↔ its container** | within section | hard | Anchored band = opposite of measured image luminance (§3.2). |
| 3 | **Background ↔ adjacent background** | between sections | best-effort | Strict alternation; separator fallback (§3.3 step 4) when two anchors collide. |

No new colour math — all three use existing `theme.py` helpers.

---

## 7. Colour sourcing — brand palette, not raw greys

All background colours and filter tints derive from the brand palette
([`ColorPalette`](backend/app/models/brand.py)), never raw black/white/grey. The
palette already encodes 60-30-10, WCAG AA, and a split-complementary accent
([`theme.py`](backend/app/services/theme.py)).

| Need | Brand token / helper |
|---|---|
| **light** band flat/grain | `surface` / `background` (faint primary tint) |
| **dark** band flat/grain | `secondary` (dark, primary-hued, not pure black) |
| photo filter wash | overlay tinted toward `primary`/`secondary` — binds photos into the brand |
| derived light/dark shade (incl. separator step) | `_adjust_lightness()` off `surface`/`secondary` |
| font colour | `_text_for_background()` / `_ensure_contrast_against()` |

**"Complementary site theme" clarified:** one cohesive palette, alternating
**luminance within it** (`surface` ↔ `secondary`, same hue family). The true
complement (`accent`) is reserved for ~10% emphasis (buttons/highlights) — **not**
a section-background rule. Alternating opposite *hues* per section clashes;
alternating *luminance* of one hue family reads cohesive.

**Builder-contract safety:** the builder understands only the 6 `ColorPalette`
tokens. Per-section backgrounds are emitted as **inline section styles**, so the
engine can derive any brand-tinted shade per section *without* touching the
6-token contract.

---

## 8. Fallback ladders

### 8.1 Hero / background photo (`image_source_preference`)

| Preference | Ladder |
|---|---|
| `scraped` | scraped pool → Pexels → Picsum placeholder |
| `stock` | Pexels → scraped pool → Picsum |
| `abstract` | generated/abstract → decorative grain → Picsum (skip people) |
| `auto` | current resolver default ([`media.resolve()`](backend/app/services/media.py)) |

### 8.2 Non-hero large sections

Same ladder; a failed resolve **degrades to `decorative` grain in band tone**,
never a Picsum filler.

### 8.3 Calm (non-photo) rendering

`calm_treatment` resolves within the band: `plain` → flat brand colour; `grain` →
flat + grain texture; `auto` → `plain` next to a photo section, `grain` for
variety in a run of calm sections.

### 8.4 No dominant-colour hint

If a featured image exposes no `dominant_color` (e.g. a scraped image with no
Pexels `avg_color` and no generated base), default `band = light` (container goes
light) and log. The section is treated as anchored-light so the rhythm still
resolves deterministically — no download is attempted.

---

## 9. Responsibilities (files to touch)

| File | Change |
|---|---|
| [`models/content_blocks.py`](backend/app/models/content_blocks.py) | Add `VisualPolicy`; add `dominant_color`/`luminance`/`band` to `ImageMetadata`; attach `visual_policy` to §4.2 blocks. |
| [`services/media.py`](backend/app/services/media.py) | After `resolve()`, **set the band from `dominant_color`** (§4.3: Pexels `avg_color` → `_relative_luminance` → threshold; generated base for abstract; default per §8.4 for scraped). No download. Honour `image_source_preference`; apply band-targeted filter for flexible photos (§3.4). |
| [`services/planner.py`](backend/app/services/planner.py) | Set `visual_mode` / `image_source_preference` per §5 (intent only). |
| [`services/schema_builder.py`](backend/app/services/schema_builder.py) | **Replace** `apply_section_rhythm()` with the **luminance pass** (§3.3): classify anchored/flexible, fix anchored bands, fill flexible, separator fallback, emit inline bg + font in `_section()`. |
| [`services/theme.py`](backend/app/services/theme.py) | Surface helpers to the pass: band colour from palette (§7), filter tint, `_text_for_background`, `_adjust_lightness` for separator steps. (Logic exists.) |
| [`services/section_content.py`](backend/app/services/section_content.py) | Thread `visual_policy` + featured-image presence/luminance through block → section. |

**Invariant:** `visual_policy = None` behaves exactly like today; the luminance
pass only takes over once policy is set.

---

## 10. Implementation phases

1. **Schema + back-compat.** Add `VisualPolicy` +
   `ImageMetadata.dominant_color/luminance/band`, attach to blocks, default
   `None`. No behaviour change — golden snapshot byte-identical.
2. **Band from dominant colour.** Thread Pexels `avg_color` → `dominant_color`
   in the resolver; set `luminance`/`band` via `_relative_luminance` + threshold;
   set the generated base for abstract; default per §8.4 for scraped. Pure data,
   no rendering change, **no download / no new dependency** (rationale: §4.3 — a
   thumbnail fetch + pixel averaging buys negligible accuracy for a 1-bit decision
   `avg_color` already answers).
3. **Luminance pass (engine).** Build the anchored/flexible classifier + §3.3
   resolution behind a flag. Old heuristic still runs when policy is `None`.
4. **Brand-colour sourcing + filters.** Wire band colours, separator steps, and
   photo filters to `theme.py`; font via `_text_for_background`.
5. **Planner sets intent.** Populate `visual_mode` / `image_source_preference`
   per §5.
6. **Retire the heuristic.** Delete the `backgroundImage`-presence path and dead
   `rhythm_trigger` scaffolding once policy is set on all large sections.

---

## 11. Testing notes

- **Golden snapshot (phase 1).** `visual_policy=None` everywhere → BuilderElement
  tree byte-identical to `main`.
- **Band from dominant colour.** Light `avg_color` (e.g. `#f0ece6`) and dark
  `avg_color` (e.g. `#1a1b1e`) → assert `band`; no `dominant_color` → `band=light`
  default (§8.4). Assert **no HTTP call** is made to measure (mock/spy the client).
- **Anchored contrast (rule 2).** Featured-image section → assert container band =
  opposite of image band; assert background is grain/plain, never a photo.
- **Flexible alternation (rule 3).** Run of flexible sections → assert strict
  light/dark alternation and adjacent contrast above threshold, including washed
  light photo directly above colourful dark photo.
- **Anchor collision → separator.** Two adjacent anchored sections forced to the
  same band → assert a within-band luminance step + divider is applied (seam
  preserved) and alternation is allowed to break.
- **Precedence.** Construct a case where alternation and image↔container disagree →
  assert image↔container wins (rule 2 > rule 3).
- **Font readability (rule 1).** Every section → `_contrast(font, bg) ≥ 4.5`,
  including a light-band section forced dark (font flips light).
- **Brand sourcing.** Every bg colour is a palette-derived shade (matches
  `surface`/`secondary`/`_adjust_lightness`), never raw grey; `accent` never a
  full section background.
- **Builder contract.** Only the 6 `ColorPalette` tokens reach theme tokens;
  per-section shades appear only as inline styles.

---

## 12. Worked example

A page where a mid-page anchor legally breaks strict alternation:

```
#  Section                  class      img lum  band    treatment        bg (brand)     font
─  ───────────────────────  ─────────  ───────  ──────  ───────────────  ─────────────  ────
H  Hero (split, dark photo) anchored   dark     LIGHT   grain (override) surface        dark
2  Services (bg photo)      flexible   —        dark    photo+overlay    colourful+wash light
3  About (light screenshot) anchored   light    DARK    grain (override) secondary      light
4  Features (light shot)    anchored   light    DARK    grain (override) secondary+step light   ← anchor collision:
   ── alternation would want LIGHT here, but rule 2 forces DARK; separator step + divider applied ──
5  CTA (no photo)           flexible   —        light   plain            surface        dark
```

Sections 3–4 are both forced **dark** by their light featured images (rule 2 >
rule 3); the separator fallback (a `_adjust_lightness` step on `secondary` + a
divider) keeps the seam visible. Every font contrasts its own background (rule 1);
every colour is from the one brand palette (§7); `accent` appears only on
buttons/highlights.
