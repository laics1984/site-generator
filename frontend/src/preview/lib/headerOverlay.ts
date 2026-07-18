// Vendored from webtree-public/lib/headerOverlay.ts — near-verbatim, only
// the import paths changed.
import { getNodeField } from './blockRuntime'
import { findFirstNonBreadcrumbNode, normalizeBodySectionNodes } from './schema'
import type { PublicBlockNode, PublicSchemaTree } from './public'

// The site generator stamps `headerOverlaySafe: true` on full-bleed hero
// sections whose baked-in dark legibility overlay guarantees that the
// transparent header's white ink stays readable (schema_builder.py,
// block_to_element). The transparent phase of a `behavior.overlay` header only
// runs on pages whose FIRST real section (skipping a sub-page's leading
// breadcrumb) carries the marker — every other page keeps the solid sticky
// header from scroll position 0.
//
// The served body wraps its sections in a `__body` root node, so we unwrap it
// (normalizeBodySectionNodes) before looking at the first section — otherwise
// we'd inspect the wrapper, which never carries the marker.
export function isFirstSectionHeaderOverlaySafe(
  schema?: PublicSchemaTree | PublicBlockNode[] | null
): boolean {
  const nodes = normalizeBodySectionNodes(schema as PublicBlockNode[] | undefined)
  const first = findFirstNonBreadcrumbNode(nodes)?.node
  return Boolean(first) && getNodeField(first, 'headerOverlaySafe') === true
}
