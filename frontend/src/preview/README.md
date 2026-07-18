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
| `lib/menuColors.ts` | Colour helpers lifted out of `MenuBlock.vue`'s script block. |
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

- Links and the contact form are inert — there's no published site to navigate
  to or post to.
- `articlesList` / `eventsList` / dynamic CMS fields render their frame and an
  empty state; the entries don't exist until the push.
- Background video shows its poster (upstream also gates playback on
  reduced-motion / small screens / Data Saver).

## Keeping it honest

The vendored copies drift as `webtree-public` evolves — the banners make drift
diffable, and re-running the CSS script is cheap. The durable fix is extracting
a shared renderer package both repos depend on; that's a larger cross-repo
project, not something to fake here.
