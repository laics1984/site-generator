/**
 * React replacements for the Vue `provide`/`inject` keys the public renderer
 * uses (webtree-public/lib/blockRuntime.ts's runtimeMenusKey,
 * runtimeBuilderStylesKey, runtimeHeaderSchemaKey, runtimeHeaderOverlayKey,
 * runtimeHeaderShrinkKey).
 *
 * Same reason upstream reaches for injection: there is no prop-drilling path
 * through SchemaRenderer → ElementRenderer → block, and blocks deep in the tree
 * (a container recomputing its grain/mesh texture, a menu resolving its items)
 * need site-level values. Defaults match upstream's `inject(key, fallback)`.
 */
import { createContext, useContext } from 'react'
import type { PublicBlockNode, PublicMenu, PublicSchemaTree, PublicStyleTokens } from './lib/public'

export const MenusContext = createContext<PublicMenu[]>([])
export const useRuntimeMenus = () => useContext(MenusContext)

export const BuilderStylesContext = createContext<PublicStyleTokens | null | undefined>(null)
export const useRuntimeBuilderStyles = () => useContext(BuilderStylesContext)

export const HeaderSchemaContext = createContext<
  PublicSchemaTree | PublicBlockNode[] | null | undefined
>(null)
export const useRuntimeHeaderSchema = () => useContext(HeaderSchemaContext)

/** True only while the header is in its transparent phase. */
export const HeaderOverlayContext = createContext<boolean>(false)
export const useRuntimeHeaderOverlay = () => useContext(HeaderOverlayContext)

export interface HeaderShrinkState {
  active: boolean
  /** Configured shrink amount, 0-1 (1 = no shrink). */
  ratio: number
}

export const HeaderShrinkContext = createContext<HeaderShrinkState>({ active: false, ratio: 1 })
export const useRuntimeHeaderShrink = () => useContext(HeaderShrinkContext)
