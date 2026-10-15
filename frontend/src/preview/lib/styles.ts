// Vendored from webtree-public/lib/styles.ts — near-verbatim, only the
// import path changed (`~/types/public` → `./public`).
import type { JsonPrimitive, PublicStyleTokens } from './public'
import { semanticColorVars, type ThemeColors } from './colorSystem'

// --- AA contrast safety net for brand-primary text ------------------------
// Small, self-contained port of builder src/lib/color-utils.ts's
// ensureContrast (same algorithm: nudge lightness, preserving hue/saturation,
// until the ratio clears `minRatio`; keep in lockstep with that file and
// site-generator app/services/theme.py's _ensure_contrast_against). Only hex
// input is handled — `colors.primary`/`colors.background` are always
// validated hex from the theme payload, never named/rgb() colors.
function hexToRgb(hex: string): { r: number; g: number; b: number } {
  const normalized = hex.replace('#', '')
  const expanded = normalized.length === 3
    ? normalized.split('').map((c) => c + c).join('')
    : normalized
  return {
    r: parseInt(expanded.slice(0, 2), 16) || 0,
    g: parseInt(expanded.slice(2, 4), 16) || 0,
    b: parseInt(expanded.slice(4, 6), 16) || 0
  }
}

function rgbToHex({ r, g, b }: { r: number; g: number; b: number }): string {
  const channel = (v: number) => Math.min(255, Math.max(0, Math.round(v))).toString(16).padStart(2, '0')
  return `#${channel(r)}${channel(g)}${channel(b)}`
}

function rgbToHsl({ r, g, b }: { r: number; g: number; b: number }): { h: number; s: number; l: number } {
  const rN = r / 255, gN = g / 255, bN = b / 255
  const max = Math.max(rN, gN, bN), min = Math.min(rN, gN, bN)
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
  return { h: h < 0 ? h + 360 : h, s: s * 100, l: l * 100 }
}

function hslToRgb({ h, s, l }: { h: number; s: number; l: number }): { r: number; g: number; b: number } {
  const safeS = Math.min(100, Math.max(0, s)) / 100
  const safeL = Math.min(100, Math.max(0, l)) / 100
  const c = (1 - Math.abs(2 * safeL - 1)) * safeS
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = safeL - c / 2
  let rP = 0, gP = 0, bP = 0
  if (h < 60) { rP = c; gP = x }
  else if (h < 120) { rP = x; gP = c }
  else if (h < 180) { gP = c; bP = x }
  else if (h < 240) { gP = x; bP = c }
  else if (h < 300) { rP = x; bP = c }
  else { rP = c; bP = x }
  return {
    r: Math.min(255, Math.max(0, Math.round((rP + m) * 255))),
    g: Math.min(255, Math.max(0, Math.round((gP + m) * 255))),
    b: Math.min(255, Math.max(0, Math.round((bP + m) * 255)))
  }
}

function relativeLuminance(hex: string): number {
  const channelLum = (v: number) => {
    const c = v / 255
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  const { r, g, b } = hexToRgb(hex)
  return 0.2126 * channelLum(r) + 0.7152 * channelLum(g) + 0.0722 * channelLum(b)
}

function contrastRatio(foreground: string, background: string): number {
  const l1 = relativeLuminance(foreground)
  const l2 = relativeLuminance(background)
  const lighter = Math.max(l1, l2)
  const darker = Math.min(l1, l2)
  return (lighter + 0.05) / (darker + 0.05)
}

const HEX_COLOR = /^#[0-9a-f]{6}$/i

// Nudge `foreground` lighter/darker (preserving hue/saturation) until it
// clears `minRatio` against `background`. Falls back to pure black/white if
// nothing on that hue clears the bar.
function ensureContrast(foreground: string, background: string, minRatio = 4.5): string {
  if (!HEX_COLOR.test(foreground) || !HEX_COLOR.test(background)) {
    return foreground
  }
  if (contrastRatio(foreground, background) >= minRatio) return foreground

  const hsl = rgbToHsl(hexToRgb(foreground))
  const goDarker = relativeLuminance(background) >= 0.5
  for (let step = 1; step <= 100; step += 1) {
    const l = Math.min(100, Math.max(0, goDarker ? hsl.l - step : hsl.l + step))
    const candidate = rgbToHex(hslToRgb({ ...hsl, l }))
    if (contrastRatio(candidate, background) >= minRatio) return candidate
    if (l === 0 || l === 100) break
  }
  return goDarker ? '#000000' : '#ffffff'
}

// Ink for a heading the renderer paints itself on the page background — a CMS
// list's heading and card titles, an archive title. `secondary` is the heading
// ink of a light palette, but on a dark one it is the darkest band colour:
// near-black on a near-black page. So it is used where it reads, and `text` —
// 7:1 against `background` in every palette by construction — where it
// doesn't. On a generated palette that is the generator's own rule (`text` on
// a dark scheme, else `secondary`), answered from the colours because the
// scheme never reaches the wire. Keep in lockstep with builder
// src/lib/builder-styles.ts (toBuilderCssVars).
function headingInk(secondary: string, text: string, background: string): string {
  const unreadable =
    HEX_COLOR.test(secondary) &&
    HEX_COLOR.test(background) &&
    contrastRatio(secondary, background) < 4.5
  return unreadable ? text : secondary
}

// The palette's two copy inks — the only colours copy on a photo, a band or a
// template's own fill is given, so a site's "white" copy is its brand's
// near-white, never a stock #ffffff. Light: the most brand-tinted of the
// palette's near-lightest neutrals (a light build's surface tint); dark: its
// darkest. Derived, never stored, so they follow a live palette edit. Keep in
// lockstep with builder src/lib/builder-styles.ts (neutralInks) and
// site-generator app/services/theme.py (_neutral_inks) — same candidates, same
// order, so a tie resolves the same way in all three.
const LIGHT_INK_SPREAD = 0.1

function chroma(hex: string): number {
  const { r, g, b } = hexToRgb(hex)
  return Math.max(r, g, b) - Math.min(r, g, b)
}

function neutralInks(background: string, surface: string, text: string, secondary: string): { light: string; dark: string } {
  const neutrals = [background, surface, text, secondary].filter((c) => HEX_COLOR.test(c))
  if (neutrals.length === 0) return { light: background, dark: text }
  const lum = neutrals.map(relativeLuminance)
  const lightest = Math.max(...lum)
  let light = ''
  let dark = neutrals[0]
  neutrals.forEach((c, i) => {
    if (lum[i] >= lightest - LIGHT_INK_SPREAD && (light === '' || chroma(c) > chroma(light))) light = c
    if (lum[i] < relativeLuminance(dark)) dark = c
  })
  return { light, dark }
}

// How much `primary` a brand-tinted chip washes into the page background.
const PRIMARY_TINT_WEIGHT = 0.1

// A brand-tinted chip (the contact form's "Required" marker) and the ink its
// label wears. A chip paints its own surface, so it states its own ink: the
// tint is OPAQUE — `primary` washed into `background` — so the colour behind
// the label is known whatever band, glow or card the chip lands on, and the
// ink is the brand's small-text ink held to AA on that tint, not on the bare
// background. A translucent tint under an ink chosen for the background
// measured 2.2:1 under the form label's opacity. Keep in lockstep with builder
// src/lib/builder-styles.ts (primaryTint).
function primaryTint(primary: string, background: string, primaryInk: string): { tint: string; ink: string } {
  if (!HEX_COLOR.test(primary) || !HEX_COLOR.test(background)) {
    return { tint: background, ink: primaryInk }
  }
  const from = hexToRgb(background)
  const to = hexToRgb(primary)
  const wash = (b: number, p: number) => b + (p - b) * PRIMARY_TINT_WEIGHT
  const tint = rgbToHex({ r: wash(from.r, to.r), g: wash(from.g, to.g), b: wash(from.b, to.b) })
  return { tint, ink: ensureContrast(primaryInk, tint, 4.5) }
}

const DEFAULT_CSS_VARS = {
  '--wt-color-primary': '#2563eb',
  '--wt-color-text': '#111827',
  '--wt-color-bg': '#ffffff',
  '--wt-color-muted': '#6b7280',
  '--wt-color-surface-ink': '#111827',
  '--wt-font-body': 'Inter, Arial, sans-serif',
  '--wt-font-heading': 'Inter, Arial, sans-serif'
}

function isStyleRecord(value: unknown): value is PublicStyleTokens {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function toCssValue(value: unknown): string | null {
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') {
    return String(value)
  }

  return null
}

function toCssLength(value: unknown, fallback: string): string {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return `${value}px`
  }

  if (typeof value === 'string' && value.trim()) {
    return value.trim()
  }

  return fallback
}

function normalizeTokenSegment(segment: string): string {
  return segment
    .replace(/([a-z0-9])([A-Z])/g, '$1-$2')
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .toLowerCase()
}

function extractCssVars(source?: PublicStyleTokens | null): Record<string, string> {
  if (!isStyleRecord(source)) {
    return {}
  }

  const vars: Record<string, string> = {}

  for (const [key, value] of Object.entries(source)) {
    const cssValue = toCssValue(value)
    if (!key.startsWith('--') || cssValue === null) {
      continue
    }

    vars[key] = cssValue
  }

  return vars
}

function flattenStyleTokens(
  source: PublicStyleTokens,
  prefix = '--wt',
  output: Record<string, string> = {}
): Record<string, string> {
  for (const [key, value] of Object.entries(source)) {
    if (key.startsWith('--') || key === 'cssVars' || key === 'variables') {
      continue
    }

    const tokenName = normalizeTokenSegment(key)
    if (!tokenName) {
      continue
    }

    const cssValue = toCssValue(value)
    if (cssValue !== null) {
      output[`${prefix}-${tokenName}`] = cssValue
      continue
    }

    if (isStyleRecord(value)) {
      flattenStyleTokens(value, `${prefix}-${tokenName}`, output)
    }
  }

  return output
}

function getNestedStyleValue(source: PublicStyleTokens | null | undefined, path: string[]): string | null {
  let current: JsonPrimitive | PublicStyleTokens | JsonPrimitive[] | undefined = source

  for (const segment of path) {
    if (!isStyleRecord(current)) {
      return null
    }

    current = current[segment]
  }

  return toCssValue(current)
}

export function buildCssVars(styles?: PublicStyleTokens | null) {
  const directVars = {
    ...extractCssVars(styles),
    ...extractCssVars(isStyleRecord(styles?.cssVars) ? styles.cssVars : null),
    ...extractCssVars(isStyleRecord(styles?.variables) ? styles.variables : null)
  }

  const tokenVars = isStyleRecord(styles) ? flattenStyleTokens(styles) : {}
  const primaryColor = getNestedStyleValue(styles, ['colors', 'primary']) || directVars['--wt-color-primary'] || DEFAULT_CSS_VARS['--wt-color-primary']
  const textColor = getNestedStyleValue(styles, ['colors', 'text']) || directVars['--wt-color-text'] || DEFAULT_CSS_VARS['--wt-color-text']
  const backgroundColor = getNestedStyleValue(styles, ['colors', 'background']) || directVars['--wt-color-bg'] || DEFAULT_CSS_VARS['--wt-color-bg']
  const surfaceColor = getNestedStyleValue(styles, ['colors', 'surface']) || directVars['--builder-color-surface'] || '#f8fafc'
  const secondaryColor = getNestedStyleValue(styles, ['colors', 'secondary']) || directVars['--builder-color-secondary'] || '#0f172a'
  const accentColor = getNestedStyleValue(styles, ['colors', 'accent']) || directVars['--builder-color-accent'] || '#f59e0b'
  // AA-corrected primary, for small text printed directly in the brand hue
  // (catalog "Eyebrow" labels, role/designation lines, badges). `primary`
  // alone is picked for button fills, where ~3:1 against white is normal for
  // a large filled shape — as raw text several curated palettes land well
  // under AA. Keep in lockstep with builder src/lib/builder-styles.ts
  // (toBuilderCssVars) and site-generator ThemeTokens.to_builder_styles.
  const primaryInkColor = getNestedStyleValue(styles, ['colors', 'primaryInk']) || directVars['--builder-color-primary-ink'] || ensureContrast(primaryColor, backgroundColor, 4.5)
  const primaryTintPair = primaryTint(primaryColor, backgroundColor, primaryInkColor)
  const inks = neutralInks(backgroundColor, surfaceColor, textColor, secondaryColor)
  // No palette carries a muted colour, so this was always the light theme's
  // grey — 4.1:1 on a dark page, under every archive description, form label
  // and blockquote. Every consumer sits on the page background, so the grey is
  // held to AA against it: unchanged on a light palette (4.8:1 on white),
  // lifted just enough on a dark one.
  const mutedColor = getNestedStyleValue(styles, ['colors', 'muted']) || directVars['--wt-color-muted'] || ensureContrast(DEFAULT_CSS_VARS['--wt-color-muted'], backgroundColor, 4.5)
  // Ink for chrome that always paints its own near-white card regardless of
  // page theme (the WhatsApp widget's roster panel).
  // `text` is only guaranteed 7:1 against the page `background` — on a dark
  // theme that same colour is light, and unreadable on the widget's fixed
  // white surface. Re-derive against white so the widget stays legible no
  // matter how dark the page theme is.
  const surfaceInkColor = ensureContrast(textColor, '#ffffff', 4.5)
  const bodyFont = getNestedStyleValue(styles, ['fonts', 'body']) || getNestedStyleValue(styles, ['typography', 'bodyFont']) || directVars['--wt-font-body'] || DEFAULT_CSS_VARS['--wt-font-body']
  const headingFont = getNestedStyleValue(styles, ['fonts', 'heading']) || getNestedStyleValue(styles, ['typography', 'headingFont']) || directVars['--wt-font-heading'] || bodyFont
  const buttonBackground = getNestedStyleValue(styles, ['buttons', 'background']) || primaryColor
  const buttonText = getNestedStyleValue(styles, ['buttons', 'text']) || '#ffffff'
  const buttonRadius = toCssLength(styles?.buttons && isStyleRecord(styles.buttons) ? styles.buttons.radius : null, '14px')
  const pageBackground = getNestedStyleValue(styles, ['page', 'background']) || backgroundColor
  // Sections center their content with
  // `padding: max(80px, calc((100% - var(--builder-page-max-width)) / 2))`.
  // Keep this the configured page width in every mode: "full" only bleeds
  // section backgrounds (via .wt-site max-width: none), while content stays
  // pinned to the same column as the header/footer.
  const pageMaxWidth = toCssLength(styles?.page && isStyleRecord(styles.page) ? styles.page.maxWidth : null, '1280px')
  // Hero photo-background height. Absent → no var emitted, so the hero
  // template's own fallback (min(100dvh, 900px)) keeps the full-screen look.
  // Keep in lockstep with builder src/lib/builder-styles.ts.
  const heroMinHeight = getNestedStyleValue(styles, ['hero', 'minHeight'])
  // Site-wide hero typography tokens (the builder's "Apply to all heroes" flow).
  // Size only — font family already cascades via --builder-font-heading/
  // --builder-font-body, so it's deliberately not part of this token set.
  // Absent → heroes fall back to their per-element size. Keep in lockstep with
  // builder src/lib/builder-styles.ts (toBuilderCssVars).
  const heroHeadingSize = getNestedStyleValue(styles, ['heroTypography', 'headingSize'])
  const heroBodySize = getNestedStyleValue(styles, ['heroTypography', 'bodySize'])

  return {
    ...DEFAULT_CSS_VARS,
    ...tokenVars,
    ...directVars,
    '--wt-color-primary': primaryColor,
    '--wt-color-text': textColor,
    '--wt-color-bg': backgroundColor,
    '--wt-color-muted': mutedColor,
    '--wt-color-surface-ink': surfaceInkColor,
    '--wt-font-body': bodyFont,
    '--wt-font-heading': headingFont,
    '--builder-color-primary': primaryColor,
    '--builder-color-primary-ink': primaryInkColor,
    '--builder-color-primary-tint': primaryTintPair.tint,
    '--builder-color-primary-tint-ink': primaryTintPair.ink,
    '--builder-color-secondary': secondaryColor,
    '--builder-color-heading': headingInk(secondaryColor, textColor, backgroundColor),
    '--builder-color-ink-light': inks.light,
    '--builder-color-ink-dark': inks.dark,
    '--builder-color-accent': accentColor,
    '--builder-color-text': textColor,
    '--builder-color-background': backgroundColor,
    '--builder-color-surface': surfaceColor,
    '--builder-page-background': pageBackground,
    '--builder-page-max-width': pageMaxWidth,
    '--builder-font-body': bodyFont,
    '--builder-font-heading': headingFont,
    '--builder-button-background': buttonBackground,
    '--builder-button-text': buttonText,
    '--builder-button-radius': buttonRadius,
    ...(heroMinHeight ? { '--builder-hero-min-height': heroMinHeight } : {}),
    ...(heroHeadingSize ? { '--builder-hero-heading-size': heroHeadingSize } : {}),
    ...(heroBodySize ? { '--builder-hero-body-size': heroBodySize } : {}),
    ...(styles?.colorSystemVersion === 2 ? semanticColorVars({
      primary: primaryColor, secondary: secondaryColor, accent: accentColor,
      text: textColor, background: backgroundColor, surface: surfaceColor,
    } as ThemeColors, { background: buttonBackground, text: buttonText }) : {})
  }
}

/**
 * Whether `styles` carry a text colour someone chose: set, and not the
 * text-colour token every builder element is created with — the builder reads
 * that as "Default", so a field drawing its own ink (an article category's
 * pills) keeps its default until the author picks one.
 * Mirror of hasOwnTextColor in builder src/lib/brand-color-tokens.ts.
 */
export function hasOwnTextColor(styles: { color?: unknown } | null | undefined): boolean {
  const color = typeof styles?.color === 'string' ? styles.color.trim() : ''
  return color !== '' && !color.startsWith('var(--builder-color-text')
}
