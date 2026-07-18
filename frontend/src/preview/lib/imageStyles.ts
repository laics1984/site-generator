// Vendored from webtree-public/lib/imageStyles.ts — verbatim, no local
// imports to change.

type RuntimeStyleValue = string | number
type RuntimeStyleMap = Record<string, RuntimeStyleValue>

const TAILWIND_HEIGHT_PX: Record<string, string> = {
  'h-auto': 'auto',
  'h-full': '100%',
  'h-40': '160px',
  'h-64': '256px',
  'h-96': '384px',
}

const RADIUS_KEYS = [
  'borderRadius',
  'borderTopLeftRadius',
  'borderTopRightRadius',
  'borderBottomRightRadius',
  'borderBottomLeftRadius',
] as const

function hasRadius(styles: RuntimeStyleMap): boolean {
  return RADIUS_KEYS.some((key) => styles[key] !== undefined)
}

function pickRadiusStyles(styles: RuntimeStyleMap): RuntimeStyleMap {
  return Object.fromEntries(
    RADIUS_KEYS.flatMap((key) =>
      styles[key] === undefined ? [] : [[key, styles[key]]]
    )
  ) as RuntimeStyleMap
}

export function getImageWrapperStyles(
  styles: RuntimeStyleMap,
  classes = ''
): RuntimeStyleMap {
  const { objectFit: _objectFit, objectPosition: _objectPosition, ...wrapperStyles } = styles
  const resolvedStyles: RuntimeStyleMap = { ...wrapperStyles }

  if (hasRadius(resolvedStyles) && resolvedStyles.overflow === undefined) {
    resolvedStyles.overflow = 'hidden'
  }

  if (resolvedStyles.height !== undefined) {
    return resolvedStyles
  }

  const classList = classes.split(/\s+/)
  for (const [className, height] of Object.entries(TAILWIND_HEIGHT_PX)) {
    if (classList.includes(className)) {
      return { ...resolvedStyles, height }
    }
  }

  return resolvedStyles
}

export function getImageElementStyles(styles: RuntimeStyleMap): RuntimeStyleMap {
  return {
    objectFit: styles.objectFit || 'cover',
    objectPosition: styles.objectPosition || 'center',
    ...pickRadiusStyles(styles),
  }
}
