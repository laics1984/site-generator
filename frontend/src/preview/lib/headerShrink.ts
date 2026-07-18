// Vendored from webtree-public/lib/headerShrink.ts — verbatim, no local
// imports to change. Mirror of builder/src/lib/header-shrink.ts — keep in
// lockstep. The published schema is simpler than the builder's (no separate
// content/styles split for logo width/height), so these operate directly on
// the node's resolved styles.

type RuntimeStyleValue = string | number
type RuntimeStyleMap = Record<string, RuntimeStyleValue>

function parsePixelValue(value: RuntimeStyleValue | undefined): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }
  if (typeof value !== 'string') {
    return null
  }
  const parsed = Number.parseFloat(value.trim())
  return Number.isFinite(parsed) ? parsed : null
}

// Shrinks whichever of width/height is the brand logo's "active" dimension —
// the one holding a real px value, while the other is 'auto' (preserving
// aspect ratio), exactly how the builder's logo-size control authors it.
// Returns null when neither/both are concrete px (nothing safe to shrink).
export function getShrunkLogoStyles(
  styles: RuntimeStyleMap,
  ratio: number
): { width: string } | { height: string } | null {
  const widthPx = parsePixelValue(styles.width)
  const heightPx = parsePixelValue(styles.height)

  if (widthPx !== null && heightPx === null) {
    return { width: `${Math.max(1, Math.round(widthPx * ratio))}px` }
  }
  if (heightPx !== null && widthPx === null) {
    return { height: `${Math.max(1, Math.round(heightPx * ratio))}px` }
  }
  return null
}

function parsePaddingShorthandY(
  styles: RuntimeStyleMap
): { top: number; bottom: number } | null {
  const explicitTop = parsePixelValue(styles.paddingTop)
  const explicitBottom = parsePixelValue(styles.paddingBottom)
  if (explicitTop !== null || explicitBottom !== null) {
    return { top: explicitTop ?? explicitBottom ?? 0, bottom: explicitBottom ?? explicitTop ?? 0 }
  }

  const shorthand = styles.padding
  if (typeof shorthand === 'number') {
    return { top: shorthand, bottom: shorthand }
  }
  if (typeof shorthand !== 'string' || shorthand.trim().length === 0) {
    return null
  }

  const parts = shorthand.trim().split(/\s+/).map(parsePixelValue)
  if (parts.length === 0 || parts.length > 4 || parts.some((part) => part === null)) {
    return null
  }

  const values = parts as number[]
  const top = values[0]!
  const bottom = values.length >= 3 ? values[2]! : top
  return { top, bottom }
}

// Shrinks the header root's own top/bottom padding by `ratio`, leaving
// left/right untouched. Returns null when unparseable (e.g. `'0'` — already
// minimal) — callers must treat null as a no-op, not spread `undefined`.
export function getShrunkHeaderPaddingYStyles(
  styles: RuntimeStyleMap,
  ratio: number
): { paddingTop: string; paddingBottom: string } | null {
  const parsed = parsePaddingShorthandY(styles)
  if (!parsed) {
    return null
  }

  return {
    paddingTop: `${Math.max(0, Math.round(parsed.top * ratio))}px`,
    paddingBottom: `${Math.max(0, Math.round(parsed.bottom * ratio))}px`,
  }
}

// Direct-child container types eligible for the row-padding shrink below.
// Mirrors builder/src/lib/header-shrink.ts's HEADER_ROW_CONTAINER_TYPES.
export const HEADER_ROW_CONTAINER_TYPES = new Set(['container', '2col', '3col'])

// Below this, the header bar can't usefully shrink any further.
// Mirrors builder/src/lib/header-shrink.ts's HEADER_MIN_HEIGHT_FLOOR_PX.
export const HEADER_MIN_HEIGHT_FLOOR_PX = 40

// Scales a px `minHeight` by `ratio`, floored. The header presets put a fixed
// `minHeight` on the root that acts as a height floor — without shrinking it,
// the bar stays pinned at full height and the padding shrink is invisible.
// Returns null when there's nothing px to shrink (absent / 'auto' / 'var(...)'
// / non-px) — callers must treat null as a no-op, not assign `undefined`.
export function getShrunkMinHeight(
  value: RuntimeStyleValue | undefined,
  ratio: number
): string | null {
  const basePx = parsePixelValue(value)
  if (basePx === null) {
    return null
  }
  return `${Math.max(HEADER_MIN_HEIGHT_FLOOR_PX, Math.round(basePx * ratio))}px`
}
