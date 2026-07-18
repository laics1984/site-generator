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
