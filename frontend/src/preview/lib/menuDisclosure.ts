// Vendored from webtree-public/lib/menuDisclosure.ts — verbatim (it has no
// imports to re-point).
//
/**
 * Dropdown navigation: the two rules every renderer of a menu must agree on.
 *
 * A menu item with children and no page of its own is a HEADING. Generated
 * sites write one for a navigation category that is no page (site-generator
 * services/nav_groups.py: "Programmes" over Curriculum, Montessori and
 * Activities), and an owner can make one in the builder by leaving a parent's
 * link empty. It is a button that opens its list, never a link to nowhere — on
 * the desktop bar, in the Menu sheet and as a footer column's title.
 *
 * Mirrored as builder/src/lib/menu-disclosure.ts (the `adaptiveInk.ts` idiom);
 * the preview vendors this file.
 */

type MenuItemLike = { href?: string | null } | null | undefined

/** An item with no page behind it: an empty href, or '#'. Only an item with
 * children is drawn as a heading; a leaf with no page is a link as it always
 * was. */
export function isMenuHeading(item: MenuItemLike): boolean {
  const href = typeof item?.href === 'string' ? item.href.trim() : ''
  return href === '' || href === '#'
}

type Edges = { left: number; right: number }

/**
 * How far to move a flyout (px, negative = left) so it sits at least `gutter`
 * inside the viewport — 0 when it already does. A flyout is centred under its
 * trigger, so the first or last item of a wide bar can push it off an edge;
 * one wider than the viewport keeps its left edge in view.
 */
export function flyoutShift(rect: Edges, viewportWidth: number, gutter = 8): number {
  if (rect.left < gutter || rect.right - rect.left > viewportWidth - 2 * gutter) {
    return gutter - rect.left
  }
  if (rect.right > viewportWidth - gutter) {
    return viewportWidth - gutter - rect.right
  }
  return 0
}
