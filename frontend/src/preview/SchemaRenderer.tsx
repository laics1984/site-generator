/**
 * PORT of webtree-public/components/renderer/SchemaRenderer.vue — keep in
 * lockstep. The overlay-spacer maths below is the reason a hero's heading
 * doesn't sit under a floating header; getting it wrong here is immediately
 * visible as a preview/public mismatch.
 */
import type { PublicBlockNode, PublicSchemaTree } from './lib/public'
import { ElementRenderer } from './ElementRenderer'
import { findFirstNonBreadcrumbNode, getNodeKey, normalizeSchemaNodes } from './lib/schema'

// Mirrors builder/src/components/tabs/editor-components/Editor.tsx's
// isHeroHeightFull/growLengthByPx*2. Keep in lockstep with builder
// src/lib/builder-styles.ts's HERO_FULL_MIN_HEIGHT/HERO_BANDED_MIN_HEIGHT.
const HERO_FULL_MIN_HEIGHT = 'min(100dvh, 900px)'
const HERO_BANDED_MIN_HEIGHT = '460px'

// An explicit fixed pixel `height` wins over `minHeight` in the box model —
// a real full-screen hero relies on a vh-based minHeight with no fixed
// height set, so any literal px height means this hero isn't full-screen
// regardless of what minHeight (or its var fallback) says.
function isHeroHeightFull(
  nodeStyles: Record<string, unknown> | undefined,
  globalHeroMinHeight: string | null | undefined
): boolean {
  const heightValue = typeof nodeStyles?.height === 'string' ? nodeStyles.height.trim() : ''
  if (/^-?\d*\.?\d+px$/.test(heightValue)) return false

  const minHeight = typeof nodeStyles?.minHeight === 'string' ? nodeStyles.minHeight.trim() : ''
  if (minHeight === HERO_FULL_MIN_HEIGHT) return true
  if (minHeight === HERO_BANDED_MIN_HEIGHT) return false
  if (minHeight.startsWith('var(--builder-hero-min-height')) {
    return !globalHeroMinHeight
  }
  return false
}

// Growing only paddingTop on a hero with a fixed height/minHeight shrinks its
// usable content area by the same amount (the box doesn't grow, the padding
// just eats into it), so push content down by centering instead. Centering
// distributes added height evenly above and below the content, so only half
// of any growth actually pushes content down — grow by 2x the clearance
// length so the centered content's top edge moves down by the full amount.
function growLengthByLength(value: unknown, extraLength: string): string | undefined {
  if (typeof value === 'number') return `calc(${value}px + ${extraLength})`
  if (typeof value === 'string' && value.trim().length > 0) {
    return `calc(${value.trim()} + ${extraLength})`
  }
  return undefined
}

export interface SchemaRendererProps {
  schema?: PublicSchemaTree | PublicBlockNode[] | null
  scope?: string
  as?: 'div' | 'section' | 'main'
  /** Set when an overlay header floats over this tree's first real section and
   * that section is a Hero. See PreviewSiteShell. */
  overlaySpacerPaddingTop?: string | null
  /** Site-wide "Hero height" default, so a hero relying on the site default var
   * can be told apart from one explicitly set to full/banded. */
  globalHeroMinHeight?: string | null
}

export function SchemaRenderer({
  schema,
  scope,
  as: Tag = 'div',
  overlaySpacerPaddingTop,
  globalHeroMinHeight,
}: SchemaRendererProps) {
  const nodes = normalizeSchemaNodes(schema)

  const renderNodes = (() => {
    if (!overlaySpacerPaddingTop) return nodes

    const target = findFirstNonBreadcrumbNode(nodes)
    if (!target) return nodes

    return nodes.map((node, index) => {
      if (index !== target.index) return node

      const existingStyles = node.styles as Record<string, unknown> | undefined

      if (isHeroHeightFull(existingStyles, globalHeroMinHeight)) {
        return { ...node, styles: { ...existingStyles, justifyContent: 'center' } }
      }

      const heightGrowth = `calc((${overlaySpacerPaddingTop}) * 2)`
      return {
        ...node,
        styles: {
          ...existingStyles,
          justifyContent: 'center',
          height: growLengthByLength(existingStyles?.height, heightGrowth) ?? existingStyles?.height,
          minHeight:
            growLengthByLength(existingStyles?.minHeight, heightGrowth) ??
            existingStyles?.minHeight,
        },
      }
    })
  })()

  if (!renderNodes.length) return null

  return (
    <Tag className="wt-schema-renderer" data-scope={scope}>
      {renderNodes.map((node, index) => (
        <ElementRenderer key={getNodeKey(node, index)} node={node} />
      ))}
    </Tag>
  )
}
