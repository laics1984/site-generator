// Vendored from webtree-public/lib/headerSheet.ts — verbatim except the import
// paths ('~/lib/X' → './X', '~/types/public' → './public').
//
/**
 * What the phone/tablet Menu sheet lists besides the primary nav.
 *
 * Below 1024px the header bar is `logo | Menu` (lib/responsiveRuntime): its
 * utility menus and its call-to-action links are hidden there, so the sheet is
 * where a visitor reaches them. This module reads the header schema once and
 * answers both halves with the predicates the compact bar already uses, so the
 * bar and the sheet cannot disagree about what a header link is.
 */
import { getArrayField, getNodeField, getNodeStyles, getStringField } from './blockRuntime'
import { isLanguageMenu } from './languages'
import { getNodeDomId, isBrandMarkRoot, isSiteRootHref } from './responsiveRuntime'
import { getNodeChildren, normalizeBlockType, normalizeSchemaNodes } from './schema'
import type { PublicBlockNode, PublicMenu, PublicMenuItem, PublicSchemaTree } from './public'

type MenuNode = PublicBlockNode | Record<string, unknown>

/** A `menu` node in the header, with the site menu it resolved to. */
export interface HeaderSheetMenu {
  slot: string
  variant: string
  /** Null when the node carries its own `items` rather than naming a menu. */
  menu: PublicMenu | null
  /** Visible items only. */
  items: PublicMenuItem[]
}

/** A header call to action, restated as a button in the sheet. */
export interface HeaderSheetAction {
  id: string
  href: string
  label: string
  target?: string | null
  rel?: string | null
  styles: ReturnType<typeof getNodeStyles>
}

/** One titled list in the sheet. A null title renders no heading. */
export interface HeaderSheetSection {
  key: string
  title: string | null
  items: PublicMenuItem[]
}

/**
 * The site menu a `menu` node renders: its own `items` when it carries them,
 * else the `menus[]` entry its `menuId` names, else the one whose purpose is
 * its slot or whose name is its label.
 */
export function resolveMenuForNode(
  node: MenuNode,
  menus: PublicMenu[] | null | undefined
): { menu: PublicMenu | null; items: PublicMenuItem[] } {
  const directItems = getArrayField<PublicMenuItem>(node, 'items')
  if (directItems.length) {
    return { menu: null, items: directItems }
  }

  const explicitMenuId = getStringField(node, 'menuId')
  const nodeSlot = getStringField(node, 'slot')
  const label = getStringField(node, 'menuLabel')
  const menu = (menus ?? []).find((entry) => {
    if (explicitMenuId) {
      return entry.id === explicitMenuId
    }

    return Boolean((nodeSlot && entry.purpose === nodeSlot) || (label && entry.name === label))
  })

  return { menu: menu ?? null, items: Array.isArray(menu?.items) ? menu.items : [] }
}

/**
 * Every other menu in the header, and its call-to-action links.
 *
 * A link is an action only where the compact bar hides it as one: never inside
 * the brand mark, never a link to the site root. The typographic logo is a
 * `link` reading the business name — collected as an action, a header with no
 * CTA showed that name as the sheet's big primary button.
 */
export function collectHeaderSheet(
  headerSchema: PublicSchemaTree | PublicBlockNode[] | null | undefined,
  menus: PublicMenu[] | null | undefined,
  ownNodeId?: string | null
): { menus: HeaderSheetMenu[]; actionLinks: HeaderSheetAction[] } {
  const found: HeaderSheetMenu[] = []
  const actionLinks: HeaderSheetAction[] = []

  const visit = (nodes: PublicBlockNode[], inBrandMark: boolean) => {
    for (const node of nodes) {
      if (getNodeField(node, 'visible') === false) {
        continue
      }

      const nodeId = getNodeDomId(node)
      const type = normalizeBlockType(getStringField(node, 'type'))
      const isOwnNode = Boolean(nodeId && nodeId === ownNodeId)

      if (!isOwnNode && type === 'menu') {
        const { menu, items } = resolveMenuForNode(node, menus)
        found.push({
          slot: (getStringField(node, 'slot') || '').trim().toLowerCase(),
          variant: (getStringField(node, 'variant') || '').trim().toLowerCase(),
          menu,
          items: items.filter((item) => item?.visible !== false),
        })
      } else if (!isOwnNode && type === 'link' && !inBrandMark) {
        const href = getStringField(node, 'href') || ''
        const label = getStringField(node, 'innerText', 'label', 'text') || ''
        if (href.trim() && label.trim() && !isSiteRootHref(href)) {
          actionLinks.push({
            id: nodeId || label,
            href,
            label,
            rel: getStringField(node, 'rel'),
            target: getStringField(node, 'target'),
            styles: getNodeStyles(node),
          })
        }
      }

      visit(getNodeChildren(node), inBrandMark || isBrandMarkRoot(node))
    }
  }

  visit(normalizeSchemaNodes(headerSchema), false)
  return { menus: found, actionLinks }
}

export function isUtilitySheetMenu(entry: HeaderSheetMenu): boolean {
  return entry.slot === 'utility' || entry.variant === 'utility-inline'
}

/**
 * The heading over a utility menu's items. A language menu is headed by its
 * own name ("Language" — authored, so editable and translatable) or by
 * nothing; it is never "Utility", which names the slot, not what is in it.
 */
export function utilitySectionTitle(menu: PublicMenu | null): string | null {
  if (isLanguageMenu(menu)) {
    return menu?.name?.trim() || null
  }
  return 'Utility'
}

/**
 * Utility menus as the sheet's sections: one per heading, in header order, so
 * every plain utility menu still shares the single "Utility" list it always had.
 */
export function utilitySheetSections(entries: HeaderSheetMenu[]): HeaderSheetSection[] {
  const sections: HeaderSheetSection[] = []
  for (const entry of entries) {
    if (!isUtilitySheetMenu(entry) || !entry.items.length) {
      continue
    }
    const title = utilitySectionTitle(entry.menu)
    const existing = sections.find((section) => section.title === title)
    if (existing) {
      existing.items.push(...entry.items)
    } else {
      sections.push({ key: title ?? `utility-${sections.length}`, title, items: [...entry.items] })
    }
  }
  return sections
}
