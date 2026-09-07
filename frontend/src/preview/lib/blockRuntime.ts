/**
 * Vendored from webtree-public/lib/blockRuntime.ts. Local deltas: (1) the
 * file's Vue `InjectionKey` exports (runtimeMenusKey, runtimeHeaderSchemaKey,
 * runtimeHeaderOverlayKey, runtimeHeaderShrinkKey, runtimeBuilderStylesKey)
 * are dropped — this app's `../context.tsx` already provides their React
 * `createContext`/`useContext` equivalents, so nothing here needs to define
 * them. (2) `getNodeStyles`'s filter uses a tuple type predicate instead of
 * filtering on the destructured value, so the result narrows to
 * `RuntimeStyleMap` under this repo's `strict` tsconfig (upstream's Nuxt
 * tsconfig doesn't flag the destructured form) — same runtime behavior.
 * Every function is otherwise verbatim.
 */
import type { PublicBlockNode } from './public'

type LooseRecord = Record<string, unknown>

type RuntimeStyleValue = string | number
type RuntimeStyleMap = Record<string, RuntimeStyleValue>

function asRecord(value: unknown): LooseRecord | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as LooseRecord : null
}

function isRuntimeStyleValue(value: unknown): value is RuntimeStyleValue {
  return typeof value === 'string' || typeof value === 'number'
}

export function getNodeContent(node: PublicBlockNode | Record<string, unknown> | null | undefined): unknown {
  const record = asRecord(node)

  if (record?.content !== undefined) {
    return record.content
  }

  return getNodePropsRecord(node)?.content
}

export function getNodeContentRecord(node: PublicBlockNode | Record<string, unknown> | null | undefined): LooseRecord | null {
  return asRecord(getNodeContent(node))
}

export function getNodePropsRecord(node: PublicBlockNode | Record<string, unknown> | null | undefined): LooseRecord | null {
  return asRecord(asRecord(node)?.props)
}

export function getNodeStyles(node: PublicBlockNode | Record<string, unknown> | null | undefined): RuntimeStyleMap {
  const record = asRecord(node)
  const styles = asRecord(record?.styles ?? getNodePropsRecord(node)?.styles)

  if (!styles) {
    return {}
  }

  return Object.fromEntries(
    Object.entries(styles).filter(
      (entry): entry is [string, RuntimeStyleValue] => isRuntimeStyleValue(entry[1])
    )
  )
}

/**
 * How many lines this node's own styles clamp its text to, or null if it does
 * not clamp.
 *
 * The line-clamp IS the clamp declaration — there is no second marker to keep
 * in step with it. A `wt-clamp` class used to carry that signal alongside the
 * inline `WebkitLineClamp`, which meant two sources of truth for one behaviour
 * and nothing an editor could set: the class was not reachable from the
 * builder, so the line count could never be changed. Reading the style is what
 * makes "Max lines" an ordinary style edit.
 *
 * Sites published before that change ship the class AND the style, so they
 * keep their toggle with no regeneration.
 *
 * Base styles only, exactly as the marker class was — a per-device clamp set
 * through `responsiveStyles` reaches the DOM as CSS, not as a value here.
 */
export function getClampLines(node: PublicBlockNode | Record<string, unknown> | null | undefined): number | null {
  const raw = getNodeStyles(node).WebkitLineClamp
  if (raw === undefined || raw === null) return null
  const lines = typeof raw === 'number' ? raw : Number.parseInt(String(raw).trim(), 10)
  // `unset`/`none`/`initial` and anything unparseable mean "no clamp".
  return Number.isFinite(lines) && lines > 0 ? lines : null
}

export function getNodeClasses(node: PublicBlockNode | Record<string, unknown> | null | undefined): string {
  const record = asRecord(node)
  const value = record?.classes ?? getNodePropsRecord(node)?.classes
  return typeof value === 'string' ? value.trim() : ''
}

export function getNodeField(node: PublicBlockNode | Record<string, unknown> | null | undefined, key: string): unknown {
  const record = asRecord(node)

  if (record && record[key] !== undefined) {
    return record[key]
  }

  const props = getNodePropsRecord(node)
  if (props && props[key] !== undefined) {
    return props[key]
  }

  const content = getNodeContentRecord(node)
  return content ? content[key] : undefined
}

export function getStringField(node: PublicBlockNode | Record<string, unknown> | null | undefined, ...keys: string[]): string | null {
  for (const key of keys) {
    const value = getNodeField(node, key)
    if (typeof value === 'string' && value.trim()) {
      return value
    }
  }

  return null
}

// A container can present itself as a HEADING. A split headline is one heading
// made of two text nodes — a lead line plus an accent-coloured trailing line —
// so the <h1> has to be the box that holds both; tagging either line alone
// leaves half the title outside the heading, which is what a crawler reads.
// Restricted to h1-h6: a container's tag is otherwise structural (section/div),
// and `htmlTag` arrives from a stored payload, so anything else keeps the
// default rather than becoming an arbitrary element.
const HEADING_TAGS = new Set(['h1', 'h2', 'h3', 'h4', 'h5', 'h6'])

export function getHeadingTag(node: PublicBlockNode | Record<string, unknown> | null | undefined): string | null {
  const tag = (getStringField(node, 'htmlTag') || '').toLowerCase()
  return HEADING_TAGS.has(tag) ? tag : null
}

export function getBooleanField(node: PublicBlockNode | Record<string, unknown> | null | undefined, ...keys: string[]): boolean {
  for (const key of keys) {
    const value = getNodeField(node, key)
    if (typeof value === 'boolean') {
      return value
    }
  }

  return false
}

export function getArrayField<T = unknown>(node: PublicBlockNode | Record<string, unknown> | null | undefined, ...keys: string[]): T[] {
  for (const key of keys) {
    const value = getNodeField(node, key)
    if (Array.isArray(value)) {
      return value as T[]
    }
  }

  return []
}
