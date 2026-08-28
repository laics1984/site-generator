# Preview renderer

Renders a generated `GeneratedSite` the way the **public site** will render it,
before anything is pushed to the CMS. Used by
[`components/PagePreview.tsx`](../components/PagePreview.tsx).

The source of truth is the `webtree-public` repo (a sibling checkout,
`../webtree-public`). This directory is a port of its renderer. It exists
because the three repos have separate git remotes and no shared workspace, so
the Vue renderer can't be imported.

## What's here

| Path | What it is |
|---|---|
| `lib/*.ts` | **Vendored** from `webtree-public/lib/` — near-verbatim copies. Each file's banner names its origin and any local delta. |
| `lib/public.ts` | The structural types the vendored libs are written against (subset of `webtree-public/types/public.ts`). |
| `lib/adapt.ts` | The one place this app's strict `BuilderElement` is narrowed to the renderer's loose `PublicBlockNode`. |
| `*.tsx`, `blocks/*.tsx` | **Ports** of the Vue components — same logic, React templates. |
| `context.tsx` | React contexts replacing Vue's `provide`/`inject`. |
| `preview.css` | **Generated.** See below. |
| `PreviewFrame.tsx` | The iframe + portal the render lives in. |

## The rules

**Don't fix bugs here.** If the public renderer has a bug, the preview should
reproduce it — a preview that renders *better* than the live site is the same
class of defect as one that renders worse, just harder to notice. Fix it in
`webtree-public`, then mirror the fix here. (`blocks/SectionDivider.tsx` carries
a live example.)

**Don't hand-edit `preview.css`.** Regenerate it:

```bash
node scripts/vendor-preview-css.mjs ../../webtree-public
```

It pulls Tailwind's preflight (the published page's baseline reset — the
generated schema depends on its `box-sizing: border-box`), `main.css`'s base and
`wt-ui-*` component layers, and every ported component's `<style>` block.

**Keep the payload shared, not copied.** The header/footer/menus come from
`POST /api/preview/layout`, which is built by the same
`build_layout_payload()` the push uses
([`backend/app/services/menu_builder.py`](../../../backend/app/services/menu_builder.py)).
`backend/tests/test_preview_layout.py` asserts the two stay identical. A `menu`
element resolves its items from that payload's `menus[]`, and the header's
`behavior` drives overlay/shrink — render the raw `header_schema` instead and
you get a header with no nav, which is what the preview used to do.

## Known gaps

Preview-only compromises, each commented at its site:

- The contact form is inert — there's no published site to post to.
- Anchors are inert *inside* these blocks, as upstream-parity requires. Links
  that resolve to another generated page are handled a level up: a capture-phase
  click listener on the frame's document
  ([`components/PagePreview.tsx`](../components/PagePreview.tsx), resolver in
  [`lib/previewNav.ts`](../lib/previewNav.ts)) switches the previewed page before
  the block's own `preventDefault()` runs. Nothing here changed to make that
  work, which is the point — off-site links still go nowhere.
- `articlesList` / `eventsList` / dynamic CMS fields render their frame and an
  empty state; the entries don't exist until the push.
- Background video shows its poster (upstream also gates playback on
  reduced-motion / small screens / Data Saver).

## Drift log

Divergences found and closed, newest first — record them here so the next
comparison pass starts from a known state:

| Upstream change | Ported | Note |
|---|---|---|
| `lib/lightbox.ts` + `GalleryLightbox.vue` (gallery tiles enlarge on click) | ✅ | Ported as `lib/lightbox.ts` + `GalleryLightbox.tsx`, wired from `PreviewSiteShell`. Two deltas, both commented: the runtime takes the frame's `Document` explicitly (no global `document` in an iframe), and slide changes fade in rather than cross-fading — Vue's `<Transition>` keeps the outgoing frame mounted and React has no dependency-free equivalent. |
| Social menu markup renamed (`wt-menu--social`/`wt-social-link` → `wt-social-menu`/`wt-social-icon`/`wt-social-icon__svg`) | ⚠️ | **Class names only.** Regenerating `preview.css` for the lightbox pulled in the rename, which would have left the preview's social icons unstyled — so `MenuBlock.tsx`'s class strings were aligned. The *glyph rendering* still diverges: this port resolves glyphs by href with a monogram fallback, `MenuBlock.vue` maps them by label. A real re-port is still outstanding, as is upstream's `wt-social-menu--header` (hides social icons in the header below 1024px). |
| `htmlTag` on text nodes (`TextBlock.vue` renders `<component :is="htmlTag">`) | ✅ | The generator sets `html_tag="h1"` on hero headlines; before this the preview emitted a `<div>` where the live page has an `<h1>`. `BuilderElement.htmlTag` was also missing from `lib/types.ts`. |
| `lib/motionRuntime.ts` + `useSchemaMotion` | ❌ | Not ported — sections render in their final state, no entrance motion. Also hides the **hero background parallax**: the generator stamps `parallax-drift` on every photo hero (`schema_builder._apply_hero_motion`), and upstream scales `.wt-container-block__bg-photo` to 1.15 and scroll-drifts it. The live site has that depth; the preview shows a static crop, so a hero always looks slightly flatter here than it ships. Porting the gsap tier needs `gsap` as a frontend dependency (upstream lazy-loads it; this repo doesn't have it). |
| `lib/webglBackdrop.ts` | ❌ | Not ported. |
| `CmsArchiveHeaderBlock.vue` | ❌ | `cmsArchiveHeader` maps to `DynamicFieldBlock` here. The generator never emits that type, so nothing renders differently today. |

## Keeping it honest

The vendored copies drift as `webtree-public` evolves — the banners make drift
diffable, and re-running the CSS script is cheap. The durable fix is extracting
a shared renderer package both repos depend on; that's a larger cross-repo
project, not something to fake here.
