/**
 * Vendored from webtree-public/types/public.ts — the subset the ported
 * renderer actually touches (PublicBlockNode/PublicSchemaTree/PublicMenu/
 * PublicStyleTokens, plus their JsonPrimitive/BackgroundStrategy leaves).
 * Keep in lockstep with the upstream file for any field this subset covers.
 */

export type JsonPrimitive = string | number | boolean | null

// The 4 decorative-background strategies (site-generator's
// ThemeTokens.background_strategy / BuilderElement.backgroundTexture). A leaf
// type with no dependents of its own — both lib/backgroundTexture.ts and
// lib/sectionDivider.ts depend on it.
export type BackgroundStrategy = 'flat' | 'mesh' | 'grain' | 'mesh+grain'

export interface PublicStyleTokens {
  [key: string]: JsonPrimitive | JsonPrimitive[] | PublicStyleTokens | undefined
}

export interface PublicBlockNode {
  id?: string | number
  _key?: string | number
  type?: string | null
  children?: PublicBlockNode[]
  elements?: PublicBlockNode[]
  [key: string]: unknown
}

export interface PublicSchemaTree {
  elements?: PublicBlockNode[]
  children?: PublicBlockNode[]
  [key: string]: unknown
}

export interface PublicMenuItem {
  id?: string
  href?: string | null
  label?: string | null
  target?: string | null
  rel?: string | null
  visible?: boolean
  children?: PublicMenuItem[] | null
}

export interface PublicMenu {
  id?: string
  name?: string | null
  purpose?: string | null
  items?: PublicMenuItem[] | null
}
