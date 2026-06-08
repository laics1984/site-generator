# Section Visual Policy & Luminance-Rhythm Engine — Spec

Revamp of the logic behind section container backgrounds.

**What's wrong today.** "Is this a heavy visual section?" is *inferred at render
time* from whether `styles.backgroundImage` is set
([`schema_builder.apply_section_rhythm()`](backend/app/services/schema_builder.py)).
That heuristic is blind to intent: a split hero carries its photo in a content
column (no `backgroundImage`) and a faint texture *sets* `backgroundImage` for a
subtle reason — so the heuristic both under- and over-counts. Section
backgrounds end up arbitrary, with no page-wide rhythm and no guaranteed
contrast between neighbours.

**What this replaces it with.** An explicit per-section **visual policy** set by
the planner (intent only), consumed by a deterministic **page-level luminance
pass** that:

1. alternates background **luminance** strictly down the whole page,
2. seeds that alternation from the hero's type,
3. filters photos to hit their target luminance,
4. pulls every actual colour from the **brand palette** ([`theme.py`](backend/app/services/theme.py)),
5. derives font colour from the *real* background so contrast always holds.

> This supersedes the earlier `rhythm_trigger` / "energy-debt" draft. Strict
> luminance alternation makes an adjacency-debt mechanism redundant (see §3.5).

If you touch any file in **§9 Responsibilities**, read this first.

---

## 1. Vocabulary (read this before anything else)

Two earlier review rounds got confused by overloading "light/dark." These three
concepts are **independent** and must stay separate in code and discussion:

| Term | Meaning | Range |
|---|---|---|
| **Luminance band** | The section's *actual rendered lightness*. Drives font colour. Strictly alternates down the page. | `light \| dark` |
| **Treatment** | The section's *richness / texture*, layered on top of the band. | `photo \| washed_photo \| grain \| plain` |
| **Source intent** | *What* to search for + where to source it, if a photo is used. | search string + `scraped\|stock\|abstract` |

Key decouplings established in review:

- A section in the **light** band can be rendered with a **dark colour** if a
  neighbour forces it (band is the *target*, not a fixed value) — but normally
  light band → light colour. Font always follows the *real* colour.
- A **dark** band does **not** mean dark pixels in a photo — a dark-band photo is
  a colourful photo; its own pixel luminance is normalised by filter/overlay.
- A **light**-band photo is a stock photo under a heavy *light* filter so it
  reads washed/pale — the source photo need not be light.

---

## 2. Decision: explicit policy, not inference

| Approach | Verdict |
|---|---|
| **Query-inference only** — infer everything from `image_query` at render time | ✗ Status quo. Same query → split image / full-bleed hero / softened background. Rules keep leaking. |
| **Page-level engine only** — derive rhythm from `kind` + `layout` | ✗ Relocates the same hidden inference. Hard to explain *why* a section came out light/dark/plain. |
| **Explicit per-section policy + page-level luminance pass** | ✓ **Chosen.** Planner declares intent; the pass is deterministic and debuggable. |

Core separation: **search intent** (what to search) vs **treatment** (how the
section behaves) vs **luminance band** (how light/dark it actually renders).
`image_query` is *content* and stays flat on the block; `visual_policy` is
*presentation* and is nested.

---

## 3. The governing model

### 3.1 Strict luminance alternation (the rhythm)

Every large background-capable section gets a **luminance band**, and bands
**strictly alternate** down the whole page:

```
… light → dark → light → dark → light …
```

This is the single invariant that carries the rhythm. It guarantees **rule 2**
in §6 (adjacent sections always contrast) by construction.

### 3.2 Seed: hero type sets the first band

The alternation is seeded by what the hero *is* (content-derived, confirmed):

| Hero | Seed band |
|---|---|
| featured-image / split hero (photo in a column) | **light** |
| full-bleed colourful-photo hero | **dark** |

Everything below alternates from the seed.

### 3.3 Photos are filtered to their band — not floated

A section's *treatment* may want a photo, but the photo does **not** float its
own luminance. The engine **filters/overlays the photo to hit the band's target
luminance**:

- **light band + photo** → heavy light wash → reads pale (`washed_photo`)
- **dark band + photo** → colourful photo, darker overlay → reads rich/dark

Trade-off accepted by design: a photo may be filtered hard enough to lose its
natural tone. Rhythm + readability win over photo fidelity.

### 3.4 Featured-image override (any band)

If a section carries its **own featured image** (content image meant to be the
focus), its background must not compete: it steps **down to `grain` or `plain`**
in the band's tone, never a photo background. The featured image stays the hero
of that section.

### 3.5 Why there is no "energy-debt" mechanism

An earlier draft tracked a loud→quiet "debt" to prevent two heavy photo sections
back-to-back. Strict alternation makes it redundant:

- Two *equally heavy* (dark-band colourful) photos can only land in **dark**
  bands; dark bands are **never adjacent**; therefore they can never abut.
- Adjacent photos of **opposite** band (a washed light photo above a colourful
  dark photo) are explicitly allowed — tonal contrast separates them.

So the rhythm is fully carried by **luminance alternation + featured-image
override**. No `rhythm_trigger`, no recovery debt.

---

## 4. Schema changes

### 4.1 New submodel: `VisualPolicy`

In [`models/content_blocks.py`](backend/app/models/content_blocks.py). Attached
as **optional** `visual_policy: VisualPolicy | None = None` on every large
background-capable block (§4.2). `None` = "no opinion, infer me" — preserves
byte-identical output for everything generated today.

```python
class VisualPolicy(BaseModel):
    # --- planner INTENT (what the section wants) ---

    visual_mode: Literal[
        "photo_background",  # wants a dominant background photo
        "supporting_image",  # photo lives in a column / split, balanced w/ copy
        "decorative",        # grain / texture, no real photo
        "plain",             # flat colour fill
        "auto",              # engine derives from kind + layout
    ] = "auto"

    image_source_preference: Literal[
        "scraped",   # prefer scraped pool, then fall down the ladder
        "stock",     # prefer Pexels
        "abstract",  # prefer generated/abstract, skip people photos
        "auto",
    ] = "auto"

    # Optional planner override of the calm (non-photo) rendering. Engine picks
    # when "auto".
    calm_treatment: Literal["plain", "grain", "auto"] = "auto"

    # Escape hatch only. Normally the band is DERIVED by the §3.1 alternation;
    # set this to force a band when a designer needs to override the rhythm.
    band_override: Literal["light", "dark", "auto"] = "auto"
```

Everything else — final luminance, resolved background colour, photo filter
strength/tint, and font colour — is **derived by the engine** (§3, §7) and is
*not* stored on the policy. The policy is intent in, render plan out.

### 4.2 Blocks that gain `visual_policy`

All large background-capable sections (phase-1 scope):
`HeroBlock`, `CtaBlock`, `AboutBlock`, `FeaturesBlock`, `ServicesBlock` (and any
future block that can take a dominant background).

**Not** added to small / inherently-gridded blocks where a dominant background is
meaningless and which never participate in the alternation as photo carriers:
`TestimonialItem`, `TeamMember`, `GalleryItem`, `FaqBlock`, `ContactBlock`.
(Team/gallery grids are never photo-background triggers — many images ≠ one
dominant background.)

### 4.3 Fields explicitly dropped vs. the original sketch

- **`rhythm_trigger`** — removed. Redundant under strict alternation (§3.5).
- **`recovery_preference`** — folded into `calm_treatment` (`plain`/`grain`).
  The old `softened_abstract_stock` value is no longer an enum token: a
  washed-light photo is just `visual_mode=photo_background` landing in a **light**
  band (§3.3).
- **`background_role`** — redundant with `HeroBlock.layout` (split/background)
  and `visual_mode`.
- **`abstract_stock | abstract_local` as visual modes** — that's a *sourcing*
  decision; it lives in `image_source_preference`.

---

## 5. Decision matrix

How the planner sets intent, and what the engine derives.

| Section / layout | `visual_mode` | Band (derived) | Rendered background |
|---|---|---|---|
| Hero, full-bleed | `photo_background` | **dark** (seed) | colourful photo, darker overlay |
| Hero, split / featured image | `supporting_image` | **light** (seed) | flat `surface` / washed, photo in column |
| CTA w/ background photo | `photo_background` | alternation | photo filtered to band |
| CTA, no photo | `plain` / `decorative` | alternation | brand flat / grain in band tone |
| About, image-split | `supporting_image` | alternation | grain/plain in band tone (override) |
| Features / services, plain | `plain` / `decorative` | alternation | brand flat / grain in band tone |
| Section w/ own featured image | any | alternation | **grain/plain only** (§3.4 override) |
| Team / gallery grid | *(no policy)* | n/a | not a photo-background participant |

---

## 6. Contrast rules (both reuse `theme.py`)

Two contrast rules, different scopes — neither needs new colour math:

**Rule 1 — Font ↔ its own background (readability).**
Font colour is derived from the section's *actual* final background via
[`theme._text_for_background()`](backend/app/services/theme.py); a brand colour
that can't reach AA is nudged by `_ensure_contrast_against()` /
`_adjust_lightness()` (hue preserved). Holds even when a light-band section is
forced to a dark colour by a neighbour — font flips to light.

**Rule 2 — Background ↔ adjacent background (separation).**
Guaranteed by strict luminance alternation (§3.1). A photo section's luminance is
*given* by its filter; the flexible grain/plain neighbour adapts to the opposite
band. Sandwich case (a calm section between two photos) is resolved by **filtering
both flanking photos to the same band**, so the middle takes the opposite band and
contrasts both (chosen resolution).

---

## 7. Colour sourcing — brand palette, not raw greys

All section background colours and photo-filter tints are **derived from the
brand palette** ([`models/brand.py:ColorPalette`](backend/app/models/brand.py)),
never raw black/white/grey. The palette already encodes 60-30-10, WCAG AA, and a
split-complementary accent ([`theme.py`](backend/app/services/theme.py)).

| Need | Brand token / helper |
|---|---|
| **light** band flat/grain colour | `surface` / `background` (already a faint primary tint) |
| **dark** band flat/grain colour | `secondary` (dark, primary-hued, explicitly not pure black) |
| photo filter wash | overlay tinted toward `primary` / `secondary` — *this is what binds filtered photos into the brand* |
| any derived light/dark shade | `_adjust_lightness()` off `surface` / `secondary` |
| font colour | `_text_for_background()` / `_ensure_contrast_against()` |

**"Complementary site theme" clarified:** the page stays in **one** cohesive
palette and alternates **luminance within it** (`surface` ↔ `secondary`, same
hue family). The true complement (`accent`) is reserved for ~10% emphasis
(buttons/highlights) — it is **not** a section-background rule. Alternating
opposite *hues* per section would clash; alternating *luminance* of one hue
family is what reads cohesive.

**Builder-contract safety:** the webtree builder only understands the 6
`ColorPalette` tokens. Per-section backgrounds are emitted as **inline section
styles**, not new theme tokens — so the engine can derive any brand-tinted
light/dark shade per section *without* touching the 6-token contract.

---

## 8. Fallback ladders

### 8.1 Hero photo (`image_source_preference`)

| Preference | Ladder |
|---|---|
| `scraped` | scraped pool → Pexels → Picsum placeholder |
| `stock` | Pexels → scraped pool → Picsum |
| `abstract` | generated/abstract → decorative grain → Picsum (skip people) |
| `auto` | current resolver default ([`media.resolve()`](backend/app/services/media.py)) |

### 8.2 Non-hero large sections

Same ladder, but a failed resolve **degrades to `decorative` grain in band tone**,
never a Picsum filler — a non-hero section should never show placeholder stock.

### 8.3 Calm (non-photo) rendering

`calm_treatment` resolves within the section's band:
- `plain` → flat brand colour for the band (`surface` / `secondary`), no overlay
- `grain` → that flat colour + grain texture
- `auto` → engine picks `plain` next to a photo section, `grain` for variety in a
  run of calm sections

---

## 9. Responsibilities (files to touch)

| File | Change |
|---|---|
| [`models/content_blocks.py`](backend/app/models/content_blocks.py) | Add `VisualPolicy`; attach optional `visual_policy` to §4.2 blocks. |
| [`services/planner.py`](backend/app/services/planner.py) | Set `visual_mode` / `image_source_preference` per §5 (intent only). Owns *what*, not *how light/dark*. |
| [`services/schema_builder.py`](backend/app/services/schema_builder.py) | **Replace** `apply_section_rhythm()` (the `backgroundImage`-sniff) with the **luminance pass**: seed band from hero (§3.2), alternate (§3.1), apply featured-image override (§3.4), pick `calm_treatment`. In `_section()`, emit the resolved inline background + font colour. |
| [`services/theme.py`](backend/app/services/theme.py) | Expose helpers to the pass: band colour from palette (§7), photo-filter tint, `_text_for_background` for font. (Logic already exists — surface it.) |
| [`services/media.py`](backend/app/services/media.py) | `resolve()` honours `image_source_preference` (§8.1) and applies the band-targeted filter/overlay (§3.3). |
| [`services/section_content.py`](backend/app/services/section_content.py) | Thread `visual_policy` (and "has featured image?") through block → section mapping. |

**Invariant:** `visual_policy = None` behaves exactly like today. The luminance
pass only takes over once the planner sets policy. Until then, the old
`backgroundImage` heuristic remains the fallback.

---

## 10. Implementation phases

1. **Schema + back-compat.** Add `VisualPolicy`, attach to blocks, default
   `None`. No behaviour change — assert existing generations are byte-identical.
2. **Luminance pass (engine).** Build the seed + strict-alternation pass behind a
   flag; when every large section has a policy, derive bands and emit inline
   colours. Old heuristic still runs when policy is `None`.
3. **Brand-colour sourcing.** Wire band colours + filter tints to `theme.py`
   (§7). Font colour via `_text_for_background`.
4. **Planner sets intent.** Populate `visual_mode` / `image_source_preference`
   per §5. Photo filtering to band in `media.resolve()`.
5. **Retire the heuristic.** Once policy is set on all large sections, delete the
   `backgroundImage`-presence path and the dead `rhythm_trigger` scaffolding.

---

## 11. Testing notes

- **Golden snapshot (phase 1).** Page with `visual_policy=None` everywhere → assert
  the BuilderElement tree is byte-identical to current `main`.
- **Strict alternation.** Any page → assert no two adjacent large sections share a
  luminance band; assert the seed matches hero type (§3.2).
- **Adjacent contrast (rule 2).** For every adjacent pair, assert measured
  luminance contrast exceeds the separation threshold — including a washed-light
  photo directly above a colourful-dark photo.
- **Sandwich resolution.** Calm section between two photos → assert both photos are
  filtered to the *same* band and the middle takes the opposite.
- **Font readability (rule 1).** Every section → assert `_contrast(font, bg) ≥ 4.5`,
  including a light-band section forced dark by a neighbour (font must flip light).
- **Featured-image override.** Section with its own featured image → assert
  background is grain/plain in band tone, never a competing photo.
- **Brand sourcing.** Assert every background colour is a palette-derived shade
  (matches `surface`/`secondary`/`_adjust_lightness` output), never raw grey;
  assert `accent` is never used as a full section background.
- **Sourcing ladder.** Mock resolver → `stock` tries Pexels first; `abstract`
  never returns a people photo.
- **Builder contract.** Assert only the 6 `ColorPalette` tokens reach theme
  tokens; per-section shades appear only as inline styles.

---

## 12. Worked example

```
#  Section                band     treatment            bg colour (brand)        font
─  ─────────────────────  ───────  ───────────────────  ───────────────────────  ─────
H  Hero (featured/split)  light    supporting_image     surface (light tint)     dark
2  Services (photo)       dark     photo → dark overlay  colourful photo + wash   light
3  About (featured image) light    grain (override)      surface, grain           dark
4  Features (photo)       dark     photo → dark overlay  colourful photo + wash   light
5  CTA (no photo)         light    plain                 surface                  dark
   ── accent (split-complement) appears only on buttons/highlights, never as a bg ──
```

Every adjacent pair contrasts (rule 2); every font contrasts its own background
(rule 1); every colour comes from the one brand palette (§7); the rhythm is the
strict light/dark alternation seeded by the hero.
