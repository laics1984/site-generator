// Vendored from webtree-public/lib/backgroundTexture.ts. Local delta: the
// type import path, and a `@ts-expect-error` on grainDataUriRaw's Node-only
// `Buffer` branch. Everything else is verbatim.
//
// Decorative section background textures — grain noise and an "aurora" mesh
// gradient. Mirror of builder/src/lib/background-texture.ts — keep both in
// lockstep. Self-contained (no shared color-utils module here, unlike the
// builder, so the small RGB/HSL helpers below are duplicated on purpose).
//
// Recomputed live from a section's `backgroundTexture` override (or the
// theme default carried on `PublicStyleTokens`) instead of trusting whatever
// Python baked into `styles.backgroundImage` at generation time — Python
// only bakes once, at site-build time, with no re-bake on save/publish, so a
// later per-section override would otherwise go stale on the published site.

import type { BackgroundStrategy, PublicStyleTokens } from './public'
import type { SectionDividerPosition } from './sectionDivider'
import { normalizeHexColor } from './backgroundPhoto'
import { getNodeField } from './blockRuntime'

export type { BackgroundStrategy }

export type ColorPaletteHex = {
  primary: string
  secondary: string
  accent: string
  text: string
  background: string
  surface: string
}

// --- Minimal color math (RGB/HSL), ported from builder/src/lib/color-utils.ts ---

type RgbColor = { r: number; g: number; b: number }
type HslColor = { h: number; s: number; l: number }

const channelToHex = (value: number): string => {
  const safe = Math.min(255, Math.max(0, Math.round(value)))
  return safe.toString(16).padStart(2, '0')
}

const clampRgbChannel = (value: number): number => Math.min(255, Math.max(0, Math.round(value)))

const clampHue = (value: number): number => {
  if (!Number.isFinite(value)) return 0
  const normalized = Math.round(value) % 360
  return normalized < 0 ? normalized + 360 : normalized
}

const clampPercentChannel = (value: number): number => Math.min(100, Math.max(0, Math.round(value)))

const toHexColor = (value: string): string | null => {
  const trimmed = value.trim()
  if (!trimmed) return null
  const hex = normalizeHexColor(trimmed)
  if (hex) return hex
  const rgbMatch = trimmed.match(/^rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})/i)
  if (rgbMatch) {
    return `#${channelToHex(Number(rgbMatch[1]))}${channelToHex(Number(rgbMatch[2]))}${channelToHex(Number(rgbMatch[3]))}`
  }
  return null
}

const hexToRgbColor = (hex: string): RgbColor => {
  const normalized = hex.replace('#', '')
  if (normalized.length !== 6) return { r: 255, g: 255, b: 255 }
  return {
    r: parseInt(normalized.slice(0, 2), 16),
    g: parseInt(normalized.slice(2, 4), 16),
    b: parseInt(normalized.slice(4, 6), 16),
  }
}

const rgbToHexColor = ({ r, g, b }: RgbColor): string => `#${channelToHex(r)}${channelToHex(g)}${channelToHex(b)}`

const rgbToHslColor = ({ r, g, b }: RgbColor): HslColor => {
  const rN = r / 255
  const gN = g / 255
  const bN = b / 255
  const max = Math.max(rN, gN, bN)
  const min = Math.min(rN, gN, bN)
  const delta = max - min

  let h = 0
  if (delta !== 0) {
    if (max === rN) h = ((gN - bN) / delta) % 6
    else if (max === gN) h = (bN - rN) / delta + 2
    else h = (rN - gN) / delta + 4
    h *= 60
  }

  const l = (max + min) / 2
  const s = delta === 0 ? 0 : delta / (1 - Math.abs(2 * l - 1))

  return { h: clampHue(h), s: clampPercentChannel(s * 100), l: clampPercentChannel(l * 100) }
}

const hslToRgbColor = ({ h, s, l }: HslColor): RgbColor => {
  const safeH = clampHue(h)
  const safeS = clampPercentChannel(s) / 100
  const safeL = clampPercentChannel(l) / 100
  const c = (1 - Math.abs(2 * safeL - 1)) * safeS
  const x = c * (1 - Math.abs(((safeH / 60) % 2) - 1))
  const m = safeL - c / 2

  let rPrime = 0
  let gPrime = 0
  let bPrime = 0

  if (safeH < 60) {
    rPrime = c
    gPrime = x
  } else if (safeH < 120) {
    rPrime = x
    gPrime = c
  } else if (safeH < 180) {
    gPrime = c
    bPrime = x
  } else if (safeH < 240) {
    gPrime = x
    bPrime = c
  } else if (safeH < 300) {
    rPrime = x
    bPrime = c
  } else {
    rPrime = c
    bPrime = x
  }

  return {
    r: clampRgbChannel((rPrime + m) * 255),
    g: clampRgbChannel((gPrime + m) * 255),
    b: clampRgbChannel((bPrime + m) * 255),
  }
}

const hslToHexColor = (hsl: HslColor): string => rgbToHexColor(hslToRgbColor(hsl))

const adjustLightness = (hex: string, deltaL: number): string => {
  const parsed = toHexColor(hex)
  if (!parsed) return hex
  const hsl = rgbToHslColor(hexToRgbColor(parsed))
  return hslToHexColor({ ...hsl, l: clampPercentChannel(hsl.l + deltaL) })
}

// --- Texture generation, ported from builder/src/lib/background-texture.ts ---

const hairlineRgba = (hex: string, alpha: number): string => {
  const { r, g, b } = hexToRgbColor(hex)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

// Same four offset radial hotspots as mesh_gradient() in schema_builder.py —
// monochromatic (primary + a lighter sibling), never the split-complementary
// accent, which clashes. Order is CSS background-image z-order: index 0 is
// the topmost layer.
type MeshHotspot = {
  xPct: number
  yPct: number
  tone: 'primary' | 'glow'
  alpha: number
  stopPct: number
}

const MESH_HOTSPOTS: MeshHotspot[] = [
  { xPct: 8, yPct: 12, tone: 'primary', alpha: 0.34, stopPct: 46 },
  { xPct: 92, yPct: 8, tone: 'glow', alpha: 0.26, stopPct: 44 },
  { xPct: 74, yPct: 82, tone: 'primary', alpha: 0.2, stopPct: 48 },
  { xPct: 20, yPct: 96, tone: 'glow', alpha: 0.15, stopPct: 46 },
]

/** A soft multi-stop "aurora" mesh as a `backgroundImage` value. Mirrors
 * mesh_gradient() in schema_builder.py exactly (same hotspots/alphas). */
export const meshGradient = (primaryHex: string): string => {
  const glow = adjustLightness(primaryHex, 18)
  return MESH_HOTSPOTS.map((h) => {
    const color = h.tone === 'primary' ? primaryHex : glow
    return `radial-gradient(at ${h.xPct}% ${h.yPct}%, ${hairlineRgba(color, h.alpha)} 0px, transparent ${h.stopPct}%)`
  }).join(', ')
}

/** A tiny SVG fractal-noise grain texture as a bare `data:` URI. Mirrors
 * grain_data_uri() in schema_builder.py byte-for-byte (same markup), so the
 * rendered noise pattern is identical between editor and Python bake.
 *
 * The default opacity MUST track grain_data_uri()'s: this module recomputes
 * grain live and overrides Python's bake, but only for sections eligible under
 * resolveSectionBackgroundImage — so a mismatch renders two different grain
 * strengths on one page.
 *
 * This is the form an SVG `<image href>` needs; for a CSS `background-image`
 * use grainDataUri(), which adds the `url(...)` wrapper CSS requires. */
export const grainDataUriRaw = (opacity = 0.2): string => {
  const svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'>" +
    "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' " +
    "numOctaves='2' stitchTiles='stitch'/>" +
    "<feColorMatrix type='saturate' values='0'/></filter>" +
    `<rect width='140' height='140' filter='url(#n)' opacity='${opacity}'/></svg>`
  // The Buffer branch only runs under Node/SSR (upstream's Nuxt build target);
  // this app is browser-only (Vite + React, no @types/node), so `btoa` always
  // exists — kept for lockstep parity, not reachable here.
  // @ts-expect-error -- Buffer is a Node global, absent from this app's browser-only lib set
  const encoded = typeof btoa === 'function' ? btoa(svg) : Buffer.from(svg, 'utf-8').toString('base64')
  return `data:image/svg+xml;base64,${encoded}`
}

/** The grain texture as a `url(data:...)` value, for CSS `background-image`. */
export const grainDataUri = (opacity = 0.2): string => `url("${grainDataUriRaw(opacity)}")`

/** Compose the decorative `backgroundImage` value for `texture` (grain and/or
 * mesh layers), or null for 'flat'. Mirrors section_background_image(). */
export const backgroundImageForTexture = (
  texture: BackgroundStrategy | null | undefined,
  primaryHex: string
): string | null => {
  if (!texture || texture === 'flat') return null
  const layers: string[] = []
  if (texture.includes('grain')) layers.push(grainDataUri())
  if (texture.includes('mesh')) layers.push(meshGradient(primaryHex))
  return layers.length ? layers.join(', ') : null
}

// --- Divider edge-texture matching, ported from background-texture.ts -----
//
// See builder/src/lib/background-texture.ts for the full rationale. Summary:
// a shaped divider's fill is a flat `color`; when the section it reveals is
// decorated with grain/mesh, a flat cut into that texture reads as a seam.
// Grain just reuses the same tileable SVG pattern. Mesh isn't tileable, so
// "matching" means resampling the 4 hotspots along the exact seam line — the
// matched section's TOP edge (y=0%) if the divider's `bottom` edge reveals
// it, or its BOTTOM edge (y=100%) if the divider's `top` edge reveals it.

const meshEdgeYPct = (dividerPosition: SectionDividerPosition): number =>
  dividerPosition === 'bottom' ? 0 : 100

// Empirical reach scale: stopPct (44-48) is "percent of the way to the
// gradient's farthest-corner edge" in the real CSS spec; in our normalized
// 0-1-per-axis distance space the farthest corner sits at roughly 1.3-1.4
// units away, so we scale the threshold up accordingly rather than treating
// stopPct as a literal 0-1 distance (which would make the falloff far too
// tight).
const REACH_SCALE = 1.3

const sampleMeshColorAt = (
  xPct: number,
  yPct: number,
  primaryHex: string,
  baseColorHex: string
): string => {
  const glow = adjustLightness(primaryHex, 18)
  const base = hexToRgbColor(baseColorHex)
  let rgb = base
  // Composite top-most layer last so it visually dominates overlaps —
  // iterate the hotspot list (index 0 = topmost) in reverse.
  for (let i = MESH_HOTSPOTS.length - 1; i >= 0; i -= 1) {
    const h = MESH_HOTSPOTS[i]
    const dx = (xPct - h.xPct) / 100
    const dy = (yPct - h.yPct) / 100
    const distance = Math.sqrt(dx * dx + dy * dy)
    const reach = (h.stopPct / 100) * REACH_SCALE
    const falloff = Math.max(0, Math.min(1, 1 - distance / reach))
    const alpha = h.alpha * falloff
    if (alpha <= 0) continue
    const color = hexToRgbColor(h.tone === 'primary' ? primaryHex : glow)
    rgb = {
      r: color.r * alpha + rgb.r * (1 - alpha),
      g: color.g * alpha + rgb.g * (1 - alpha),
      b: color.b * alpha + rgb.b * (1 - alpha),
    }
  }
  return `rgb(${Math.round(rgb.r)}, ${Math.round(rgb.g)}, ${Math.round(rgb.b)})`
}

export type MeshEdgeStop = { offsetPct: number; color: string }

/** Sample the mesh's hotspots along the divider's seam line, for the SVG
 * `<linearGradient>` `<stop>` elements in SectionDivider.vue (which can't
 * consume a CSS gradient string as a fill). */
export const sampleMeshEdgeStops = (
  primaryHex: string,
  baseColorHex: string,
  dividerPosition: SectionDividerPosition,
  samples = 11
): MeshEdgeStop[] => {
  const yPct = meshEdgeYPct(dividerPosition)
  const stops: MeshEdgeStop[] = []
  for (let i = 0; i < samples; i += 1) {
    const offsetPct = (i / (samples - 1)) * 100
    stops.push({
      offsetPct,
      color: sampleMeshColorAt(offsetPct, yPct, primaryHex, baseColorHex),
    })
  }
  return stops
}

// --- Live per-section resolution (mirrors editor-component-wrapper.tsx) ---
//
// Mirrors modernize_sections()'s `is_plain` gate in schema_builder.py: only a
// section with no existing photo/video/gradient fill, sitting on a page or
// surface tint, is eligible to be auto-decorated from the theme default.
// `backgroundTexture` set explicitly (including 'flat') always wins and is
// applied regardless of the section's current color, mirroring how a user's
// explicit per-section choice should never be silently ignored.

export type SectionTextureInput = {
  backgroundTexture?: BackgroundStrategy | null
  /** The section's current `backgroundColor` as resolveSolidFillHex reads it:
   * a literal hex, or null when the fill is not one opaque colour — which is
   * never plain. */
  backgroundColorHex?: string | null
}

export type SectionTextureContext = {
  primaryHex: string
  /** The theme's resolved background-texture default. */
  themeTexture: BackgroundStrategy
  /** Hexes that count as "plain" (page background, surface tint). */
  plainBackgroundHexes: string[]
}

/** The `backgroundImage` value a section should render, or undefined to
 * leave the section's existing styles untouched entirely (a real photo/video
 * background, or a deliberately-colored section Python never decorated). */
export const resolveSectionBackgroundImage = (
  input: SectionTextureInput,
  ctx: SectionTextureContext
): string | null | undefined => {
  if (input.backgroundTexture !== undefined && input.backgroundTexture !== null) {
    return backgroundImageForTexture(input.backgroundTexture, ctx.primaryHex)
  }
  const isPlain =
    Boolean(input.backgroundColorHex) &&
    ctx.plainBackgroundHexes.includes(input.backgroundColorHex as string)
  if (!isPlain) return undefined
  return backgroundImageForTexture(ctx.themeTexture, ctx.primaryHex)
}

// --- Resolving palette/theme-texture/color tokens off the wire payload ----
//
// `PublicStyleTokens` (= the builder's BuilderStyles, see backend
// BrandIdentity.to_builder_styles()) already carries literal hex colors and
// the theme's backgroundTexture default as plain JSON — no CSS-variable
// resolution needed to get those two. A section's own `styles.backgroundColor`
// can still be a `var(--builder-color-*)` / `var(--builder-page-background, …)`
// token though (apply_section_rhythm / divider edge fallback in the backend),
// so resolveColorToHex below still has to handle that case.

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

const DEFAULT_PALETTE_HEX: ColorPaletteHex = {
  primary: '#2563eb',
  secondary: '#0f172a',
  accent: '#f59e0b',
  text: '#111827',
  background: '#ffffff',
  surface: '#f8fafc',
}

/** Extract the literal hex color palette off `PublicStyleTokens.colors`. */
export const resolvePaletteHex = (builderStyles: PublicStyleTokens | null | undefined): ColorPaletteHex => {
  const colors = asRecord(asRecord(builderStyles)?.colors)
  const pick = (key: keyof ColorPaletteHex): string => {
    const value = colors?.[key]
    return typeof value === 'string' && value.trim() ? value.trim() : DEFAULT_PALETTE_HEX[key]
  }
  return {
    primary: pick('primary'),
    secondary: pick('secondary'),
    accent: pick('accent'),
    text: pick('text'),
    background: pick('background'),
    surface: pick('surface'),
  }
}

/** The page-level background hex (`PublicStyleTokens.page.background`),
 * falling back to the palette's background. */
export const resolvePageBackgroundHex = (
  builderStyles: PublicStyleTokens | null | undefined,
  palette: ColorPaletteHex
): string => {
  const page = asRecord(asRecord(builderStyles)?.page)
  const value = page?.background
  return typeof value === 'string' && value.trim() ? value.trim() : palette.background
}

/** The theme's resolved background-texture default (`PublicStyleTokens.backgroundTexture`). */
export const resolveThemeTexture = (builderStyles: PublicStyleTokens | null | undefined): BackgroundStrategy => {
  const value = asRecord(builderStyles)?.backgroundTexture
  return value === 'mesh' || value === 'grain' || value === 'mesh+grain' || value === 'flat' ? value : 'flat'
}

const PAGE_BG_TOKEN_RE = /--builder-page-background\s*,\s*([^)]+)\)/i
const BUILDER_COLOR_TOKEN_RE = /var\(\s*--builder-color-([a-z]+)\s*\)/i

// A token or literal read to a hex, or null when this module cannot name the
// colour. The two exports below differ only in what they do with that null.
const readColorHex = (value: string | null | undefined, palette: ColorPaletteHex, pageBackgroundHex: string): string | null => {
  if (!value) return null
  const tokenMatch = value.match(BUILDER_COLOR_TOKEN_RE)
  if (tokenMatch) {
    const key = tokenMatch[1] as keyof ColorPaletteHex
    if (key in palette) return toHexColor(palette[key])
  }
  if (/--builder-page-background/i.test(value)) {
    const fallback = value.match(PAGE_BG_TOKEN_RE)?.[1]?.trim()
    return toHexColor(pageBackgroundHex) || toHexColor(fallback || '')
  }
  return toHexColor(value)
}

/** Resolve a stored color (a `var(--builder-color-*)` token, the page
 * background token, or a literal CSS color) to a concrete hex, white when it
 * names no colour. Mirrors builder's resolveDividerColorToHex
 * (lib/section-divider.ts) exactly, so a token resolves identically in the
 * editor preview and the published site. */
export const resolveColorToHex = (value: string | null | undefined, palette: ColorPaletteHex, pageBackgroundHex: string): string =>
  readColorHex(value, palette, pageBackgroundHex) || '#ffffff'

// The alpha of an `rgba(r, g, b, a)` / `rgb(r, g, b, a)`; toHexColor reads the
// channels and drops it.
const RGB_ALPHA_RE = /^rgba?\([^,()]*,[^,()]*,[^,()]*,\s*(\d*\.?\d+)(%?)\s*\)$/i

// A composed fill — an alpha below 1, or a color-mix() — paints a colour that
// depends on what lies under it, even when it names a palette token:
// `color-mix(in srgb, var(--builder-color-background) 80%, transparent)` is the
// builder's glass, and the token inside it is not what it paints.
const isComposite = (value: string): boolean => {
  const trimmed = value.trim()
  if (/^color-mix\(/i.test(trimmed)) return true
  const alpha = trimmed.match(RGB_ALPHA_RE)
  return alpha !== null && Number(alpha[1]) / (alpha[2] ? 100 : 1) < 1
}

/** The one opaque colour a fill paints, as a hex — or null when it paints none
 * this module can name: `transparent`, a translucent `rgba()`, a `color-mix()`,
 * anything unparseable. Texture eligibility reads a fill through this, never
 * through resolveColorToHex: a see-through card is not a page-coloured band,
 * and reading it as white tiled the theme's grain across its whole box — a
 * grey card behind the copy that the design never drew. The
 * generator's `is_plain` likewise only ever matches a token or a hex. Mirrors
 * builder's resolveSolidFillHex (lib/section-divider.ts). */
export const resolveSolidFillHex = (value: string | null | undefined, palette: ColorPaletteHex, pageBackgroundHex: string): string | null =>
  value && isComposite(value) ? null : readColorHex(value, palette, pageBackgroundHex)

/** Read a node's `backgroundTexture` override field (BuilderElement.backgroundTexture on the wire). */
export const getNodeBackgroundTexture = (
  node: Record<string, unknown> | null | undefined
): BackgroundStrategy | undefined => {
  const value = getNodeField(node, 'backgroundTexture')
  return value === 'mesh' || value === 'grain' || value === 'mesh+grain' || value === 'flat' ? value : undefined
}
