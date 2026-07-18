/**
 * Vendored from webtree-public/lib/responsiveRuntime.ts — near-verbatim,
 * framework agnostic. Import paths changed (`~/lib/X` → `./X`,
 * `~/types/public` → `./public`). Local delta: dropped the unused
 * `isFooterRoot` local (dead upstream too — this repo's `noUnusedLocals`
 * just actually enforces it) and rebuilt the `getNodeStyles`/style-map
 * filters' return types to satisfy `strict` (upstream's Nuxt tsconfig
 * doesn't run this file through `noUnusedLocals`/strict `Object.fromEntries`
 * narrowing) — same runtime behavior, no logic changes.
 */
import { getNodeContentRecord, getNodeStyles, getStringField } from './blockRuntime'
import { getNodeChildren, normalizeBlockType, normalizeSchemaNodes } from './schema'
import type { PublicBlockNode, PublicSchemaTree } from './public'

type DeviceType = 'Mobile' | 'Tablet'
type RuntimeStyleValue = string | number
type RuntimeStyleMap = Record<string, RuntimeStyleValue>
type LooseRecord = Record<string, unknown>
type ResponsiveNodeContext = {
  scope?: 'header' | 'body' | 'footer'
  parentType?: string
  siblingTypes?: string[]
}

const MOBILE_BREAKPOINT_MAX = 767.98
const TABLET_BREAKPOINT_MIN = 768
const TABLET_BREAKPOINT_MAX = 1023.98
const DEVICE_CANVAS_WIDTH: Record<DeviceType, number> = {
  Mobile: 375,
  Tablet: 768,
}

const ROOT_TYPES = new Set(['body', 'header', 'footer', '__body', '__header', '__footer'])
const GENERATED_SECTION_SHELL_NAMES = new Set([
  'Hero - Modern Split',
  'About - Story Split',
  'Features - Card Grid',
  'Services - Offer Grid',
  'Testimonials - Quote Grid',
  'CTA - Banner',
  'FAQ - Stacked',
  'Contact - Split Form',
])
const GENERATED_GRID_NAMES = new Set([
  'Hero Columns',
  'About Columns',
  'Feature Grid',
  'Services Grid',
  'Testimonial Grid',
  'Contact Columns',
])
const GENERATED_CARD_MIN_HEIGHTS: Record<string, string> = {
  'Feature Card': '240px',
  'Service Card': '256px',
  'Testimonial Card': '248px',
}
const UNITLESS_PROPERTIES = new Set([
  'animationIterationCount',
  'aspectRatio',
  'flex',
  'flexGrow',
  'flexShrink',
  'fontWeight',
  'lineClamp',
  'lineHeight',
  'opacity',
  'order',
  'orphans',
  'widows',
  'zIndex',
  'zoom',
])

function asRecord(value: unknown): LooseRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as LooseRecord)
    : null
}

function isRuntimeStyleValue(value: unknown): value is RuntimeStyleValue {
  return typeof value === 'string' || typeof value === 'number'
}

function getResponsiveSource(node: PublicBlockNode | Record<string, unknown> | null | undefined): LooseRecord | null {
  const record = asRecord(node)
  const props = asRecord(record?.props)
  return asRecord(record?.responsiveStyles ?? props?.responsiveStyles)
}

function getDeviceOverrideRecord(
  node: PublicBlockNode | Record<string, unknown> | null | undefined,
  device: DeviceType
): LooseRecord | null {
  const source = getResponsiveSource(node)
  return asRecord(device === 'Mobile' ? source?.mobile : source?.tablet)
}

function getDeviceOverrides(
  node: PublicBlockNode | Record<string, unknown> | null | undefined,
  device: DeviceType
): RuntimeStyleMap {
  const source = getDeviceOverrideRecord(node, device)

  if (!source) {
    return {}
  }

  return Object.fromEntries(
    Object.entries(source).filter(
      (entry): entry is [string, RuntimeStyleValue] => isRuntimeStyleValue(entry[1])
    )
  )
}

function parsePixelValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value
  }

  if (typeof value !== 'string' || !value.includes('px')) {
    return null
  }

  const parsed = Number.parseFloat(value)
  return Number.isFinite(parsed) ? parsed : null
}

function hasOwnDefinedValue(record: LooseRecord | null, property: string): boolean {
  return Boolean(record && record[property] !== undefined)
}

function getNodeType(node: PublicBlockNode | Record<string, unknown> | null | undefined): string {
  return normalizeBlockType(getStringField(node, 'type'))
}

function getNodeName(node: PublicBlockNode | Record<string, unknown> | null | undefined): string {
  return getStringField(node, 'name') || ''
}

function getGeneratedLayoutStyleOverrides(
  node: PublicBlockNode | Record<string, unknown> | null | undefined,
  device: 'Desktop' | DeviceType
): RuntimeStyleMap {
  const nodeName = getNodeName(node)

  if (device === 'Mobile') {
    return {}
  }

  if (GENERATED_SECTION_SHELL_NAMES.has(nodeName)) {
    return device === 'Desktop'
      ? { gap: '72px' }
      : { gap: '28px' }
  }

  if (nodeName === 'Section Intro') {
    return device === 'Desktop'
      ? { gap: '24px' }
      : { gap: '10px' }
  }

  if (GENERATED_GRID_NAMES.has(nodeName)) {
    return device === 'Desktop'
      ? { gap: '48px' }
      : { gap: '24px' }
  }

  if (nodeName === 'About Highlights') {
    return device === 'Desktop'
      ? { gap: '28px' }
      : { gap: '20px' }
  }

  if (nodeName === 'FAQ Stack') {
    return device === 'Desktop'
      ? { gap: '20px' }
      : { gap: '16px' }
  }

  if (nodeName === 'About Highlight') {
    return device === 'Desktop'
      ? { minHeight: '220px' }
      : { minHeight: '180px' }
  }

  const generatedCardMinHeight = GENERATED_CARD_MIN_HEIGHTS[nodeName]
  if (generatedCardMinHeight) {
    return device === 'Desktop'
      ? { minHeight: generatedCardMinHeight }
      : {
          minHeight:
            nodeName === 'Feature Card'
              ? '200px'
              : nodeName === 'Service Card'
                ? '216px'
                : '208px',
        }
  }

  return {}
}

function getNodeVariant(node: PublicBlockNode | Record<string, unknown> | null | undefined): string {
  return (getStringField(node, 'variant') || '').trim().toLowerCase()
}

function getNodeSlot(node: PublicBlockNode | Record<string, unknown> | null | undefined): string {
  return (getStringField(node, 'slot') || '').trim().toLowerCase()
}

function getNodeIdValue(node: PublicBlockNode | Record<string, unknown> | null | undefined): string | null {
  const record = asRecord(node)
  const rawId = record?.id ?? record?._key

  if (typeof rawId === 'string' && rawId.trim()) {
    return rawId.trim()
  }

  if (typeof rawId === 'number' && Number.isFinite(rawId)) {
    return String(rawId)
  }

  return null
}

function toCssPropertyName(property: string): string {
  if (property.startsWith('--')) {
    return property
  }

  return property.replace(/[A-Z]/g, (match) => `-${match.toLowerCase()}`)
}

function toCssPropertyValue(property: string, value: RuntimeStyleValue): string {
  if (typeof value === 'number') {
    return UNITLESS_PROPERTIES.has(property) ? String(value) : `${value}px`
  }

  return value.replace(/[\r\n]+/g, ' ').trim()
}

function escapeAttributeValue(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"')
}

function buildCssRule(nodeId: string, styles: RuntimeStyleMap): string | null {
  const declarations = Object.entries(styles)
    .filter(([, value]) => isRuntimeStyleValue(value))
    .map(([property, value]) => {
      const cssValue = toCssPropertyValue(property, value)
      return cssValue
        ? `${toCssPropertyName(property)}: ${cssValue} !important;`
        : ''
    })
    .filter(Boolean)
    .join(' ')

  if (!declarations) {
    return null
  }

  return `[data-wt-node-id="${escapeAttributeValue(nodeId)}"] { ${declarations} }`
}

function shallowEqualStyles(a: RuntimeStyleMap, b: RuntimeStyleMap): boolean {
  const aKeys = Object.keys(a)
  const bKeys = Object.keys(b)

  if (aKeys.length !== bKeys.length) {
    return false
  }

  return aKeys.every((key) => a[key] === b[key])
}

export function getNodeDomId(node: PublicBlockNode | Record<string, unknown> | null | undefined): string | null {
  return getNodeIdValue(node)
}

export function getResponsiveNodeStyles(
  node: PublicBlockNode | Record<string, unknown> | null | undefined,
  device: DeviceType,
  canvasWidth = DEVICE_CANVAS_WIDTH[device],
  context: ResponsiveNodeContext = {}
): RuntimeStyleMap {
  const baseStyles = { ...getNodeStyles(node) }
  const colorMode = (getStringField(node, 'colorMode') || '').trim().toLowerCase()
  if (getNodeType(node) === 'menu' && colorMode === 'auto') {
    delete baseStyles.color
  }
  let mergedStyles: RuntimeStyleMap = {
    ...baseStyles,
    ...getDeviceOverrides(node, device),
  }

  const deviceOverrideRecord = getDeviceOverrideRecord(node, device)
  const hasDeviceOverride = (property: string) =>
    hasOwnDefinedValue(deviceOverrideRecord, property)

  const width = mergedStyles.width
  const originalWidth = width
  const widthPx = parsePixelValue(width)
  const leftPx = parsePixelValue(mergedStyles.left)
  const marginLeftPx = parsePixelValue(mergedStyles.marginLeft)
  const type = getNodeType(node)
  const isRootSection = ROOT_TYPES.has(type)
  const hasFixedPixelWidth = widthPx !== null
  const isImageElement = type === 'image'
  const isBrandLogoElement = isImageElement && /brand/i.test(getNodeName(node))
  const parentType = normalizeBlockType(context.parentType || '')
  const isHeaderImage = isImageElement && parentType === 'header'
  const siblingTypes = context.siblingTypes || []
  const scope = context.scope
  const variant = getNodeVariant(node)
  const slot = getNodeSlot(node)
  const isHeaderRoot = type === 'header'
  const isHeaderMenu =
    type === 'menu' &&
    (
      parentType === 'header' ||
      variant.includes('header') ||
      slot === 'primary' ||
      slot === 'utility'
    )
  const isFooterMenu =
    type === 'menu' &&
    (
      parentType === 'footer' ||
      variant.includes('footer') ||
      slot === 'legal' ||
      slot === 'footer' ||
      slot === 'social'
    )
  const isHeaderLink = type === 'link' && parentType === 'header'
  const hasMenuSibling = siblingTypes.includes('menu')
  const hasImageSibling = siblingTypes.includes('image')
  const imageContent = getNodeContentRecord(node)
  const hasImageSource =
    isImageElement &&
    typeof imageContent?.src === 'string' &&
    imageContent.src.trim().length > 0

  if (isImageElement && !isBrandLogoElement && !isHeaderImage && !hasDeviceOverride('width')) {
    mergedStyles.width = '100%'
    if (!hasDeviceOverride('maxWidth')) mergedStyles.maxWidth = '100%'
    if (!hasDeviceOverride('left')) mergedStyles.left = '0'
    if (!hasDeviceOverride('marginLeft')) mergedStyles.marginLeft = '0'
    if (!hasDeviceOverride('right')) mergedStyles.right = 'auto'
  }

  if (hasImageSource && !isBrandLogoElement && !isHeaderImage && !hasDeviceOverride('height')) {
    mergedStyles.height = 'auto'
    if (!hasDeviceOverride('minHeight')) mergedStyles.minHeight = '0px'
  }

  if (hasFixedPixelWidth && !isRootSection && !isBrandLogoElement && !isHeaderImage) {
    mergedStyles.width = '100%'
    mergedStyles.maxWidth = '100%'
    mergedStyles.left = '0'
    mergedStyles.marginLeft = '0'
    mergedStyles.right = 'auto'
  }

  if (typeof width === 'string' && width.includes('px')) {
    const widthValue = Number.parseInt(width, 10)

    if (
      Number.isFinite(widthValue) &&
      widthValue > canvasWidth &&
      hasFixedPixelWidth &&
      isRootSection
    ) {
      mergedStyles.width = '100%'
      mergedStyles.maxWidth = width

      if (
        mergedStyles.position === 'absolute' ||
        mergedStyles.position === 'relative'
      ) {
        mergedStyles.left = '0'
        mergedStyles.marginLeft = '0'
        mergedStyles.right = 'auto'
      }
    }
  }

  const couldOverflowFromOffsets =
    hasFixedPixelWidth &&
    isRootSection &&
    (
      (leftPx !== null && leftPx + widthPx > canvasWidth) ||
      (marginLeftPx !== null && marginLeftPx + widthPx > canvasWidth)
    )

  if (couldOverflowFromOffsets) {
    mergedStyles.width = '100%'

    if (typeof originalWidth === 'string' && originalWidth.includes('px')) {
      mergedStyles.maxWidth = originalWidth
    }

    mergedStyles.left = '0'
    mergedStyles.marginLeft = '0'
    mergedStyles.right = 'auto'
  }

  if (
    mergedStyles.position === 'absolute' ||
    mergedStyles.position === 'relative'
  ) {
    const positionedLeft = parsePixelValue(mergedStyles.left)

    if (positionedLeft !== null && positionedLeft >= canvasWidth - 20) {
      mergedStyles.left = '0'
      mergedStyles.marginLeft = '0'
      mergedStyles.right = 'auto'
    }
  }

  if (
    device === 'Mobile' &&
    typeof mergedStyles.fontSize === 'string'
  ) {
    const fontSize = Number.parseInt(mergedStyles.fontSize, 10)
    if (Number.isFinite(fontSize) && fontSize > 16) {
      mergedStyles.fontSize = `${Math.max(14, fontSize * 0.875)}px`
    }
  }

  if (
    device === 'Tablet' &&
    typeof mergedStyles.fontSize === 'string'
  ) {
    const fontSize = Number.parseInt(mergedStyles.fontSize, 10)
    if (Number.isFinite(fontSize) && fontSize > 20) {
      mergedStyles.fontSize = `${Math.max(16, fontSize * 0.9)}px`
    }
  }

  mergedStyles = {
    ...mergedStyles,
    ...getGeneratedLayoutStyleOverrides(node, device),
  }

  // Published payloads may only contain base styles with no responsive overrides.
  // These layout heuristics keep common header/footer compositions usable at real narrow widths.
  if (
    (device === 'Mobile' || device === 'Tablet') &&
    mergedStyles.display === 'flex' &&
    (scope === 'header' || scope === 'footer') &&
    !hasDeviceOverride('flexWrap')
  ) {
    mergedStyles.flexWrap = 'wrap'
  }

  if (
    scope === 'footer' &&
    device === 'Mobile' &&
    mergedStyles.justifyContent === 'space-between' &&
    !hasDeviceOverride('justifyContent')
  ) {
    mergedStyles.justifyContent = 'flex-start'
  }

  if (
    device === 'Tablet' &&
    isHeaderRoot &&
    (
      mergedStyles.display === 'flex' ||
      (hasImageSibling && hasMenuSibling)
    )
  ) {
    if (!hasDeviceOverride('display')) mergedStyles.display = 'flex'
    if (!hasDeviceOverride('flexWrap')) mergedStyles.flexWrap = 'wrap'
  }

  if (device === 'Tablet' && isHeaderMenu) {
    if (!hasDeviceOverride('width')) mergedStyles.width = 'auto'
    if (!hasDeviceOverride('maxWidth')) mergedStyles.maxWidth = '100%'
    if (!hasDeviceOverride('flex')) mergedStyles.flex = '0 0 auto'
    if (!hasDeviceOverride('flexBasis')) mergedStyles.flexBasis = 'auto'
    if (!hasDeviceOverride('marginLeft')) mergedStyles.marginLeft = 'auto'
    if (!hasDeviceOverride('alignSelf')) mergedStyles.alignSelf = 'center'
    if (!hasDeviceOverride('order')) mergedStyles.order = 99
    if (!hasDeviceOverride('position')) mergedStyles.position = 'static'
  }

  if (
    device === 'Mobile' &&
    isHeaderRoot &&
    (
      mergedStyles.display === 'flex' ||
      (hasImageSibling && hasMenuSibling)
    )
  ) {
    if (!hasDeviceOverride('display')) mergedStyles.display = 'flex'
    if (!hasDeviceOverride('flexWrap')) mergedStyles.flexWrap = 'wrap'
  }

  if (device === 'Mobile' && isHeaderMenu) {
    if (!hasDeviceOverride('width')) mergedStyles.width = 'auto'
    if (!hasDeviceOverride('maxWidth')) mergedStyles.maxWidth = '100%'
    if (!hasDeviceOverride('flex')) mergedStyles.flex = '0 0 auto'
    if (!hasDeviceOverride('flexBasis')) mergedStyles.flexBasis = 'auto'
    if (!hasDeviceOverride('marginLeft')) mergedStyles.marginLeft = 'auto'
    if (!hasDeviceOverride('alignSelf')) mergedStyles.alignSelf = 'center'
    if (!hasDeviceOverride('order')) mergedStyles.order = 99
    if (!hasDeviceOverride('position')) mergedStyles.position = 'static'
  }

  if ((device === 'Mobile' || device === 'Tablet') && isHeaderImage) {
    if (!hasDeviceOverride('width')) mergedStyles.width = originalWidth ?? 'auto'
    if (!hasDeviceOverride('maxWidth')) mergedStyles.maxWidth = '100%'
    if (!hasDeviceOverride('flex')) mergedStyles.flex = '0 0 auto'
    if (!hasDeviceOverride('marginLeft')) mergedStyles.marginLeft = '0'
    if (!hasDeviceOverride('marginRight')) mergedStyles.marginRight = '0'
    if (!hasDeviceOverride('alignSelf')) mergedStyles.alignSelf = 'auto'
  }

  if (device === 'Mobile' && isHeaderLink) {
    if (!hasDeviceOverride('display')) mergedStyles.display = 'none'
  }

  if (device === 'Tablet' && isHeaderLink) {
    if (!hasDeviceOverride('display')) mergedStyles.display = 'none'
  }

  if (device === 'Mobile' && isHeaderMenu && slot === 'utility') {
    if (!hasDeviceOverride('display')) mergedStyles.display = 'none'
  }

  if (device === 'Tablet' && isHeaderMenu && slot === 'utility') {
    if (!hasDeviceOverride('display')) mergedStyles.display = 'none'
  }

  if (device === 'Mobile' && isFooterMenu) {
    if (!hasDeviceOverride('width')) mergedStyles.width = '100%'
    if (!hasDeviceOverride('flexDirection')) mergedStyles.flexDirection = 'column'
    if (!hasDeviceOverride('alignItems')) mergedStyles.alignItems = 'flex-start'
    if (!hasDeviceOverride('gap')) mergedStyles.gap = '12px'
  }

  if (
    device === 'Mobile' &&
    isImageElement &&
    parentType === 'header' &&
    !isBrandLogoElement &&
    !hasDeviceOverride('maxWidth')
  ) {
    mergedStyles.maxWidth = '100%'
  }

  return mergedStyles
}

function visitSchemaNodes(
  schema: PublicSchemaTree | PublicBlockNode[] | null | undefined,
  scope: 'header' | 'body' | 'footer',
  visitor: (node: PublicBlockNode, context: ResponsiveNodeContext) => void
) {
  const queue = normalizeSchemaNodes(schema).map((node) => ({
    node,
    scope,
    parentType: '',
    siblingTypes: [] as string[],
  }))

  while (queue.length) {
    const current = queue.shift()
    if (!current) {
      continue
    }

    visitor(current.node, {
      scope: current.scope,
      parentType: current.parentType,
      siblingTypes: current.siblingTypes,
    })

    const children = getNodeChildren(current.node)
    const siblingTypes = children.map((child) => getNodeType(child)).filter(Boolean)

    queue.push(
      ...children.map((child) => ({
        node: child,
        scope: current.scope,
        parentType: getNodeType(current.node),
        siblingTypes,
      }))
    )
  }
}

export function buildResponsiveStylesheet(payload: {
  headerSchema?: PublicSchemaTree | PublicBlockNode[] | null
  bodySchema?: PublicSchemaTree | PublicBlockNode[] | null
  footerSchema?: PublicSchemaTree | PublicBlockNode[] | null
}): string {
  const desktopRules: string[] = []
  const tabletRules: string[] = []
  const mobileRules: string[] = []

  for (const [scope, schema] of [
    ['header', payload.headerSchema],
    ['body', payload.bodySchema],
    ['footer', payload.footerSchema],
  ] as const) {
    visitSchemaNodes(schema, scope, (node, context) => {
      const nodeId = getNodeDomId(node)
      if (!nodeId) {
        return
      }

      const baseStyles = getNodeStyles(node)
      const desktopStyles = {
        ...baseStyles,
        ...getGeneratedLayoutStyleOverrides(node, 'Desktop'),
      }
      const tabletStyles = getResponsiveNodeStyles(node, 'Tablet', undefined, context)
      const mobileStyles = getResponsiveNodeStyles(node, 'Mobile', undefined, context)

      if (!shallowEqualStyles(baseStyles, desktopStyles)) {
        const rule = buildCssRule(nodeId, desktopStyles)
        if (rule) {
          desktopRules.push(rule)
        }
      }

      if (!shallowEqualStyles(baseStyles, tabletStyles)) {
        const rule = buildCssRule(nodeId, tabletStyles)
        if (rule) {
          tabletRules.push(rule)
        }
      }

      if (!shallowEqualStyles(baseStyles, mobileStyles)) {
        const rule = buildCssRule(nodeId, mobileStyles)
        if (rule) {
          mobileRules.push(rule)
        }
      }
    })
  }

  const sections: string[] = []

  if (desktopRules.length) {
    sections.push(
      `@media (min-width: 1024px) {\n${desktopRules.join('\n')}\n}`
    )
  }

  if (tabletRules.length) {
    sections.push(
      `@media (min-width: ${TABLET_BREAKPOINT_MIN}px) and (max-width: ${TABLET_BREAKPOINT_MAX}px) {\n${tabletRules.join('\n')}\n}`
    )
  }

  if (mobileRules.length) {
    sections.push(
      `@media (max-width: ${MOBILE_BREAKPOINT_MAX}px) {\n${mobileRules.join('\n')}\n}`
    )
  }

  return sections.join('\n\n')
}
