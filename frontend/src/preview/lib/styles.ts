// Vendored from webtree-public/lib/styles.ts — near-verbatim, only the
// import path changed (`~/types/public` → `./public`).
import type { JsonPrimitive, PublicStyleTokens } from './public'

const DEFAULT_CSS_VARS = {
  '--wt-color-primary': '#2563eb',
  '--wt-color-text': '#111827',
  '--wt-color-bg': '#ffffff',
  '--wt-color-muted': '#6b7280',
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
  const mutedColor = getNestedStyleValue(styles, ['colors', 'muted']) || directVars['--wt-color-muted'] || DEFAULT_CSS_VARS['--wt-color-muted']
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
    '--wt-font-body': bodyFont,
    '--wt-font-heading': headingFont,
    '--builder-color-primary': primaryColor,
    '--builder-color-secondary': secondaryColor,
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
    ...(heroBodySize ? { '--builder-hero-body-size': heroBodySize } : {})
  }
}
