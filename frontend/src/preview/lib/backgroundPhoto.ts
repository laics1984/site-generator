// Vendored from webtree-public/lib/backgroundPhoto.ts — verbatim, no import
// paths to change (this file has no local imports).

export const BACKGROUND_PHOTO_OPACITY_STYLE = '--builder-background-photo-opacity'
export const BACKGROUND_PHOTO_OVERLAY_COLOR_STYLE = '--builder-background-photo-overlay-color'
export const BACKGROUND_PHOTO_OVERLAY_OPACITY_STYLE = '--builder-background-photo-overlay-opacity'

// Background VIDEO tokens — the video counterpart to a background photo. The
// opacity + overlay tokens above are shared: they tint the video layer exactly
// as they tint a photo. Stored as bare URLs (read in JS) so the <video> element
// can consume src/poster directly. Mirror of builder/src/lib/background-photo.ts.
export const BACKGROUND_VIDEO_SRC_STYLE = '--builder-background-video-src'
export const BACKGROUND_VIDEO_POSTER_STYLE = '--builder-background-video-poster'

export const DEFAULT_BACKGROUND_PHOTO_OPACITY = 100
export const DEFAULT_BACKGROUND_PHOTO_OVERLAY_COLOR = '#000000'
export const DEFAULT_BACKGROUND_PHOTO_OVERLAY_OPACITY = 0

const clampPercentage = (value: number) => {
  if (!Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, Math.round(value)))
}

const parsePercentage = (value: unknown, fallback: number) => {
  if (typeof value === 'number') return clampPercentage(value)

  if (typeof value === 'string') {
    const parsed = Number.parseFloat(value.replace('%', '').trim())
    if (Number.isFinite(parsed)) return clampPercentage(parsed)
  }

  return fallback
}

export const normalizeHexColor = (value: string | null | undefined) => {
  const trimmed = value?.trim() || ''
  if (!trimmed) return null

  const normalized = trimmed.startsWith('#') ? trimmed.slice(1) : trimmed
  if (!/^[0-9a-f]{3}([0-9a-f]{3})?$/i.test(normalized)) return null

  if (normalized.length === 3) {
    return `#${normalized
      .split('')
      .map((character) => `${character}${character}`)
      .join('')
      .toLowerCase()}`
  }

  return `#${normalized.toLowerCase()}`
}

const hexToRgb = (value: string) => {
  const normalized = normalizeHexColor(value) || DEFAULT_BACKGROUND_PHOTO_OVERLAY_COLOR
  const compact = normalized.slice(1)

  return {
    r: Number.parseInt(compact.slice(0, 2), 16),
    g: Number.parseInt(compact.slice(2, 4), 16),
    b: Number.parseInt(compact.slice(4, 6), 16),
  }
}

export const toRgbaString = (value: string, opacity: number) => {
  const rgb = hexToRgb(value)
  const alpha = clampPercentage(opacity) / 100

  return `rgba(${rgb.r}, ${rgb.g}, ${rgb.b}, ${alpha.toFixed(2)})`
}

export interface BackgroundPhotoSettings {
  photoOpacity: number
  overlayColor: string
  overlayOpacity: number
}

export const getBackgroundPhotoSettings = (
  styles?: Record<string, unknown> | null
): BackgroundPhotoSettings => {
  const overlayColor =
    normalizeHexColor(
      typeof styles?.[BACKGROUND_PHOTO_OVERLAY_COLOR_STYLE] === 'string'
        ? (styles?.[BACKGROUND_PHOTO_OVERLAY_COLOR_STYLE] as string)
        : undefined
    ) || DEFAULT_BACKGROUND_PHOTO_OVERLAY_COLOR

  return {
    photoOpacity: parsePercentage(
      styles?.[BACKGROUND_PHOTO_OPACITY_STYLE],
      DEFAULT_BACKGROUND_PHOTO_OPACITY
    ),
    overlayColor,
    overlayOpacity: parsePercentage(
      styles?.[BACKGROUND_PHOTO_OVERLAY_OPACITY_STYLE],
      DEFAULT_BACKGROUND_PHOTO_OVERLAY_OPACITY
    ),
  }
}

export interface BackgroundVideoSettings {
  src: string | null
  poster: string | null
}

/**
 * Reads a bare URL out of a background-video token. Tolerates a stray `url(...)`
 * wrapper or surrounding quotes, but the canonical storage is a plain URL.
 * Mirror of builder/src/lib/background-photo.ts → keep in lockstep.
 */
const parseVideoUrlToken = (value: unknown): string | null => {
  if (typeof value !== 'string') return null
  let trimmed = value.trim()
  if (!trimmed || trimmed === 'none') return null

  const urlMatch = trimmed.match(/^url\(\s*(['"]?)([^'")]+)\1\s*\)$/i)
  if (urlMatch) {
    trimmed = urlMatch[2].trim()
  } else if (
    (trimmed.startsWith('"') && trimmed.endsWith('"')) ||
    (trimmed.startsWith("'") && trimmed.endsWith("'"))
  ) {
    trimmed = trimmed.slice(1, -1).trim()
  }

  return trimmed || null
}

export const getBackgroundVideoSettings = (
  styles?: Record<string, unknown> | null
): BackgroundVideoSettings => ({
  src: parseVideoUrlToken(styles?.[BACKGROUND_VIDEO_SRC_STYLE]),
  poster: parseVideoUrlToken(styles?.[BACKGROUND_VIDEO_POSTER_STYLE]),
})

/**
 * Whether this element carries a background video. Takes precedence over a
 * photo `backgroundImage` on the same element.
 */
export const hasBackgroundVideo = (
  styles?: Record<string, unknown> | null
): boolean => Boolean(getBackgroundVideoSettings(styles).src)

const FRAGMENT_REF_REGEX = /^(#|%23)/i
// Decorative inline textures (grain/noise/mesh) are always authored as SVG —
// raster data-URIs (png/jpeg/webp/gif) are how a locally-uploaded photo is
// stored when CMS upload is disabled/unavailable, and must count as a photo.
const DECORATIVE_DATA_URI_REGEX = /^data:image\/svg\+xml/i

/**
 * Whether a bare URL (the contents of a `url(...)`, without the wrapper) is a
 * real photo source — as opposed to an SVG fragment reference (#id / %23id,
 * e.g. the `url(#n)` filter ref *inside* a grain data-URI) or a decorative
 * inline SVG data-URI (grain/noise/mesh textures).
 *
 * Mirror of builder/src/lib/background-photo.ts → keep in lockstep.
 */
export const isPhotoUrlMatch = (inner: string): boolean => {
  const trimmed = inner.trim()
  if (!trimmed) return false
  if (FRAGMENT_REF_REGEX.test(trimmed)) return false
  if (DECORATIVE_DATA_URI_REGEX.test(trimmed)) return false
  return true
}

/**
 * Whether a `backgroundImage` value references a real photo — i.e. a `url(...)`
 * pointing at a remote/local image file or an uploaded raster data-URI — as
 * opposed to a pure CSS gradient (mesh/aurora backgrounds) or a decorative
 * inline SVG data-URI (grain/noise).
 *
 * Only real photos should go through the photo-layer pipeline (separate
 * absolutely-positioned layer + opacity + dark overlay). Decorative gradients
 * and textures must render in place, untouched.
 *
 * Note: a value may legitimately combine BOTH — the generator emits
 * `linear-gradient(overlay), url('photo')` for brand-tinted hero photos. Such
 * a value still counts as a photo because it contains a real (non-decorative) url().
 */
export const isPhotoSource = (value: string): boolean => {
  const v = value.trim()
  if (!v || v === 'none') return false
  // Collect every url(...) reference. A bare gradient has none → not a photo.
  const urls = v.match(/url\(\s*['"]?\s*[^'")]+/gi)
  if (!urls) return false
  return urls.some((u) => isPhotoUrlMatch(u.replace(/^url\(\s*['"]?\s*/i, '')))
}

export const hasBackgroundImage = (styles?: Record<string, unknown> | null) => {
  const value = styles?.backgroundImage
  return typeof value === 'string' && isPhotoSource(value)
}

const PHOTO_STYLE_KEYS = [
  'background',
  'backgroundImage',
  'backgroundSize',
  'backgroundPosition',
  'backgroundRepeat',
] as const

export const stripPhotoStyles = <T extends Record<string, unknown>>(styles: T): T => {
  const next = { ...styles }
  for (const key of PHOTO_STYLE_KEYS) {
    delete next[key]
  }
  return next
}

export const pickPhotoLayerStyles = (
  styles: Record<string, unknown>,
  photoOpacity: number
): Record<string, unknown> => ({
  backgroundImage: styles.backgroundImage,
  backgroundSize: styles.backgroundSize,
  backgroundPosition: styles.backgroundPosition,
  backgroundRepeat: styles.backgroundRepeat,
  opacity: clampPercentage(photoOpacity) / 100,
})

export const pickBorderRadiusStyles = (
  styles: Record<string, unknown>
): Record<string, unknown> => ({
  borderRadius: styles.borderRadius,
  borderTopLeftRadius: styles.borderTopLeftRadius,
  borderTopRightRadius: styles.borderTopRightRadius,
  borderBottomRightRadius: styles.borderBottomRightRadius,
  borderBottomLeftRadius: styles.borderBottomLeftRadius,
})
