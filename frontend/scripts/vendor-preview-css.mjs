/**
 * Regenerates src/preview/preview.css from the webtree-public renderer.
 *
 *   node scripts/vendor-preview-css.mjs [path-to-webtree-public]
 *
 * The preview renders generated schemas through a React port of that repo's
 * Vue renderer (see src/preview/). The blocks' visuals come from their
 * `<style scoped>` blocks plus the shared `wt-ui-*` component layer — without
 * them the preview loses buttons, cards, menus and the whole page shell.
 *
 * Rather than hand-transcribe ~1.5k lines of CSS (and silently drift on every
 * upstream tweak), this extracts them mechanically. Vue's `scoped` compiles to
 * data-attribute selectors, which we intentionally drop: every selector here is
 * already a unique `wt-`-prefixed class, so unscoping is a no-op in practice —
 * with one exception handled below.
 *
 * What gets pulled, and why:
 *   1. Tailwind v4's preflight, from the public app's own node_modules. The
 *      published page's baseline IS preflight (main.css does
 *      `@import "tailwindcss"`), and the generated schema leans on it —
 *      `box-sizing: border-box` above all. Without it, every container the
 *      generator emits as `width: 100%` plus padding computes content-box and
 *      overflows its parent, so the preview shows a horizontal scrollbar and
 *      sections wider than the page. Ask the renderer's CSS for a page and you
 *      inherit its reset too.
 *   2. main.css's base layer, hand-mirrored below: it is written in Tailwind's
 *      `@apply`/`@theme` dialect, which needs the Tailwind compiler this app
 *      doesn't run. It is four short rules; they are restated as plain CSS.
 *   3. main.css's `@layer components` block (plain CSS) — where `wt-ui-*` lives.
 *
 * Not pulled: Tailwind's utility classes. The generator emits inline styles,
 * not utilities — the only exceptions are the height utilities main.css
 * safelists via `@source inline(...)`, which lib/imageStyles.ts already maps to
 * pixel values itself (TAILWIND_HEIGHT_PX).
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const PUBLIC_REPO = process.argv[2] ?? join(process.cwd(), '..', '..', 'webtree-public')

// Order matters: the shell's global styles set the page frame, block styles
// layer on top. Mirrors the order the public app's own cascade produces.
const VUE_SOURCES = [
  'components/public/PublicSiteShell.vue',
  'components/renderer/ElementRenderer.vue',
  'components/blocks/ContainerBlock.vue',
  'components/blocks/SectionBlock.vue',
  'components/blocks/TextBlock.vue',
  'components/blocks/LinkBlock.vue',
  'components/blocks/ImageBlock.vue',
  'components/blocks/HeroBlock.vue',
  'components/blocks/MenuBlock.vue',
  'components/blocks/VideoBlock.vue',
  'components/blocks/ContactFormBlock.vue',
  'components/blocks/CmsListBlock.vue',
  'components/blocks/CmsArchiveHeaderBlock.vue',
  'components/blocks/DynamicFieldBlock.vue',
]

function extractStyleBlocks(source) {
  const blocks = []
  const re = /<style[^>]*>([\s\S]*?)<\/style>/g
  let match
  while ((match = re.exec(source)) !== null) {
    blocks.push(match[1].trim())
  }
  return blocks
}

function extractComponentsLayer(css) {
  // Grab the body of `@layer components { ... }` by brace matching — the block
  // contains nested rules, so a regex would stop at the first `}`.
  const start = css.indexOf('@layer components')
  if (start === -1) return ''
  const open = css.indexOf('{', start)
  let depth = 0
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === '{') depth += 1
    else if (css[i] === '}') {
      depth -= 1
      if (depth === 0) {
        return css.slice(open + 1, i).trim()
      }
    }
  }
  return ''
}

const parts = []
parts.push(`/* GENERATED FILE — DO NOT EDIT BY HAND.
 *
 * Vendored from webtree-public by frontend/scripts/vendor-preview-css.mjs.
 * Regenerate with:  node scripts/vendor-preview-css.mjs [path-to-webtree-public]
 *
 * These are the public renderer's own styles: Tailwind's preflight (the
 * published page's baseline — the generated schema depends on its
 * box-sizing: border-box), main.css's base + wt-ui-* component layers, and the
 * <style> blocks of the components ported in src/preview/. Vue's scoped-style
 * data attributes are dropped — every selector is a unique wt- class, so
 * scoping was never what made them work. Injected into the preview iframe by
 * PagePreview.
 *
 * Edit the source in webtree-public and re-run the script; edits here are lost.
 */`)

const preflight = readFileSync(join(PUBLIC_REPO, 'node_modules/tailwindcss/preflight.css'), 'utf8')
parts.push(
  `/* --- from tailwindcss/preflight.css (the published page's baseline reset) --- */\n${preflight.trim()}`
)

// Plain-CSS restatement of main.css's `@layer base`. Kept in sync by hand — it
// is the one part of the public CSS this script can't lift verbatim, because
// `@apply` needs the Tailwind compiler. If main.css's base layer grows, mirror
// it here.
parts.push(`/* --- mirror of app/assets/css/main.css (@layer base) --- */
html,
body {
  margin: 0;
  background-color: #ffffff;
  color: #0f172a;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

body {
  font-family: var(--wt-font-body, Inter, Arial, sans-serif);
  background: var(--wt-color-bg, #ffffff);
  color: var(--wt-color-text, #111827);
}

a {
  color: inherit;
}`)

const mainCss = readFileSync(join(PUBLIC_REPO, 'app/assets/css/main.css'), 'utf8')
const componentsLayer = extractComponentsLayer(mainCss)
if (!componentsLayer) {
  throw new Error('Could not find `@layer components` in main.css — did it move?')
}
parts.push(`/* --- from app/assets/css/main.css (@layer components) --- */\n${componentsLayer}`)

for (const relPath of VUE_SOURCES) {
  const source = readFileSync(join(PUBLIC_REPO, relPath), 'utf8')
  const blocks = extractStyleBlocks(source)
  if (blocks.length === 0) continue
  parts.push(`/* --- from ${relPath} --- */\n${blocks.join('\n\n')}`)
}

// The shell's `.wt-site` sets `min-height: 100vh` against the viewport. In the
// preview that viewport is the iframe, which is exactly what we want, so this
// is left alone — noted here because it's the one rule whose meaning depends on
// being inside the frame.

writeFileSync(join(process.cwd(), 'src/preview/preview.css'), `${parts.join('\n\n')}\n`, 'utf8')
console.log(`Wrote src/preview/preview.css from ${PUBLIC_REPO}`)
