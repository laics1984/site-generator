// Vendored from webtree-public/lib/sectionDivider.ts — near-verbatim, only
// the import paths changed. Section shape dividers — public-renderer mirror
// of builder/src/lib/section-divider.ts. The geometry (viewBox + path data +
// transform) MUST stay identical so the published page matches the builder
// preview pixel-for-pixel. In short, the divider is a decorative SVG overlay
// pinned to a section's edge and renders over any background (solid,
// gradient, photo, or video).

import type { BackgroundStrategy, PublicBlockNode } from './public'
import { getNodeField } from './blockRuntime'

export type SectionDividerShape = 'slant' | 'curve' | 'wave' | 'peak'
export type SectionDividerPosition = 'top' | 'bottom'

export interface SectionDividerEdge {
  shape: SectionDividerShape
  height?: number
  color?: string
  flipX?: boolean
  /**
   * Decorative texture to layer on top of `color`, matching the grain/mesh of
   * the section the edge reveals — so a shaped seam reads as a continuous
   * handoff instead of a flat cut into a textured background. Mirror of
   * builder/src/lib/section-divider.ts's `SectionDividerEdge.texture`.
   */
  texture?: BackgroundStrategy
}

export interface SectionDivider {
  top?: SectionDividerEdge | null
  bottom?: SectionDividerEdge | null
}

export const DEFAULT_DIVIDER_COLOR = 'var(--builder-page-background, #ffffff)'
export const SECTION_DIVIDER_VIEWBOX = '0 0 1200 100'

interface SectionDividerShapeDef {
  id: SectionDividerShape
  defaultHeight: number
  path: string
}

const SHAPES: SectionDividerShapeDef[] = [
  { id: 'slant', defaultHeight: 80, path: 'M0,100 L1200,0 L1200,100 Z' },
  { id: 'curve', defaultHeight: 70, path: 'M0,100 Q600,0 1200,100 Z' },
  {
    id: 'wave',
    defaultHeight: 60,
    path: 'M0,60 C200,20 400,20 600,60 C800,100 1000,100 1200,60 L1200,100 L0,100 Z',
  },
  { id: 'peak', defaultHeight: 70, path: 'M0,100 L600,20 L1200,100 Z' },
]

export const getDividerShapeDef = (
  shape: string | undefined | null
): SectionDividerShapeDef | null =>
  SHAPES.find((entry) => entry.id === shape) ?? null

export const getDividerHeight = (edge: SectionDividerEdge): number => {
  const fallback = getDividerShapeDef(edge.shape)?.defaultHeight ?? 60
  const value =
    typeof edge.height === 'number' && Number.isFinite(edge.height)
      ? edge.height
      : fallback
  return Math.min(400, Math.max(8, Math.round(value)))
}

export const getDividerColor = (edge: SectionDividerEdge): string =>
  edge.color && edge.color.trim() ? edge.color : DEFAULT_DIVIDER_COLOR

export const getDividerPath = (edge: SectionDividerEdge): string =>
  getDividerShapeDef(edge.shape)?.path ?? ''

export const getDividerTransform = (
  position: SectionDividerPosition,
  flipX: boolean | undefined
): string => {
  const scaleX = flipX ? -1 : 1
  const scaleY = position === 'top' ? -1 : 1
  return `scale(${scaleX}, ${scaleY})`
}

const normalizeEdge = (value: unknown): SectionDividerEdge | null => {
  if (!value || typeof value !== 'object') return null
  const edge = value as Record<string, unknown>
  const shape = edge.shape
  if (!getDividerShapeDef(typeof shape === 'string' ? shape : null)) return null
  const texture = edge.texture
  return {
    shape: shape as SectionDividerShape,
    height: typeof edge.height === 'number' ? edge.height : undefined,
    color: typeof edge.color === 'string' ? edge.color : undefined,
    flipX: typeof edge.flipX === 'boolean' ? edge.flipX : undefined,
    texture:
      texture === 'mesh' || texture === 'grain' || texture === 'mesh+grain' || texture === 'flat'
        ? texture
        : undefined,
  }
}

/** Read and normalize the `divider` field off a schema node. */
export const getNodeDivider = (
  node: PublicBlockNode | Record<string, unknown> | null | undefined
): SectionDivider | null => {
  const raw = getNodeField(node, 'divider')
  if (!raw || typeof raw !== 'object') return null
  const record = raw as Record<string, unknown>
  const top = normalizeEdge(record.top)
  const bottom = normalizeEdge(record.bottom)
  if (!top && !bottom) return null
  return { top, bottom }
}

export const hasNodeDivider = (
  node: PublicBlockNode | Record<string, unknown> | null | undefined
): boolean => getNodeDivider(node) !== null
