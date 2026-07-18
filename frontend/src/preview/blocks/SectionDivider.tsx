/**
 * PORT of webtree-public/components/blocks/SectionDivider.vue — keep in
 * lockstep. Renders the top/bottom shape-divider SVGs for a section-like
 * block, above its background media layer.
 *
 * When `edge.texture` is set, a second <path> layers over the flat `color`
 * fill: grain via a local <pattern> (the same tileable noise SVG the section
 * background uses, so the seam tiles continuously); mesh via a
 * <linearGradient> sampled along the exact edge line. `color` itself is never
 * touched by texture logic — kept flat/base on purpose.
 */
import { useId } from 'react'
import type { CSSProperties } from 'react'
import {
  getDividerColor,
  getDividerHeight,
  getDividerPath,
  getDividerTransform,
  SECTION_DIVIDER_VIEWBOX,
  type SectionDivider,
  type SectionDividerEdge,
  type SectionDividerPosition,
} from '../lib/sectionDivider'
import {
  grainDataUriRaw,
  resolveColorToHex,
  resolvePageBackgroundHex,
  resolvePaletteHex,
  sampleMeshEdgeStops,
  type MeshEdgeStop,
} from '../lib/backgroundTexture'
import { useRuntimeBuilderStyles } from '../context'

interface RenderedEdge {
  position: SectionDividerPosition
  path: string
  color: string
  hasGrain: boolean
  hasMesh: boolean
  grainPatternId: string
  meshGradientId: string
  meshStops: MeshEdgeStop[]
  style: CSSProperties
}

export function SectionDividerLayer({ divider }: { divider: SectionDivider | null | undefined }) {
  const builderStyles = useRuntimeBuilderStyles()
  const palette = resolvePaletteHex(builderStyles)
  const pageBackgroundHex = resolvePageBackgroundHex(builderStyles, palette)
  const primaryHex = resolveColorToHex(palette.primary, palette, pageBackgroundHex)

  // SVG defs are referenced by url(#id), so the ids must be unique per instance
  // AND valid — React's useId embeds colons, which break the reference.
  const rawId = useId().replace(/[^a-zA-Z0-9_-]/g, '')

  const edges: RenderedEdge[] = []
  for (const position of ['top', 'bottom'] as SectionDividerPosition[]) {
    const edge = divider?.[position] as SectionDividerEdge | null | undefined
    const path = edge ? getDividerPath(edge) : ''
    if (!edge || !path) continue
    const color = getDividerColor(edge)
    const texture = edge.texture
    const hasGrain = Boolean(texture && texture.includes('grain'))
    const hasMesh = Boolean(texture && texture.includes('mesh'))
    edges.push({
      position,
      path,
      color,
      hasGrain,
      hasMesh,
      grainPatternId: `${rawId}-${position}-grain`,
      meshGradientId: `${rawId}-${position}-mesh`,
      meshStops: hasMesh
        ? sampleMeshEdgeStops(
            primaryHex,
            resolveColorToHex(color, palette, pageBackgroundHex),
            position
          )
        : [],
      style: {
        position: 'absolute',
        left: 0,
        right: 0,
        width: '100%',
        height: `${getDividerHeight(edge)}px`,
        [position]: '-1px',
        transform: getDividerTransform(position, edge.flipX),
        pointerEvents: 'none',
        display: 'block',
        zIndex: 1,
      } as CSSProperties,
    })
  }

  if (!edges.length) return null

  return (
    <>
      {edges.map((edge) => (
        <svg
          key={edge.position}
          className="wt-section-divider"
          aria-hidden="true"
          viewBox={SECTION_DIVIDER_VIEWBOX}
          preserveAspectRatio="none"
          style={edge.style}
        >
          <defs>
            {edge.hasGrain && (
              <pattern
                id={edge.grainPatternId}
                patternUnits="userSpaceOnUse"
                width="140"
                height="140"
              >
                <image href={grainDataUriRaw()} width="140" height="140" />
              </pattern>
            )}
            {/* Sampled left-to-right across the matched section's width. flipX
                mirrors this along with the shape, so a flipped mesh-matched edge
                no longer lines up exactly with the section's real hotspot
                positions — an accepted trade-off upstream. */}
            {edge.hasMesh && (
              <linearGradient id={edge.meshGradientId} x1="0" y1="0" x2="1" y2="0">
                {edge.meshStops.map((stop) => (
                  <stop key={stop.offsetPct} offset={`${stop.offsetPct}%`} stopColor={stop.color} />
                ))}
              </linearGradient>
            )}
          </defs>
          <path d={edge.path} fill={edge.color} />
          {edge.hasGrain && <path d={edge.path} fill={`url(#${edge.grainPatternId})`} />}
          {edge.hasMesh && <path d={edge.path} fill={`url(#${edge.meshGradientId})`} />}
        </svg>
      ))}
    </>
  )
}
