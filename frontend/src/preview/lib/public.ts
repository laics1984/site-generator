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
  /** On a `purpose: "language"` menu: the BCP 47 language this item switches to. */
  locale?: string | null
  /** Set by lib/languages.resolveLanguageMenus on a resolved language item. */
  hreflang?: string | null
  lang?: string | null
  current?: boolean
  target?: string | null
  rel?: string | null
  visible?: boolean
  children?: PublicMenuItem[] | null
}

/** The same page in one language (webtree-cms-api specs/multilingual-sites.md). */
export interface PublicAlternate {
  locale: string
  path: string
  href?: string | null
}

export interface PublicMenu {
  id?: string
  name?: string | null
  purpose?: string | null
  items?: PublicMenuItem[] | null
}
