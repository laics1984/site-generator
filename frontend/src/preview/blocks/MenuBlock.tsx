/**
 * PORT of webtree-public/components/blocks/MenuBlock.vue — keep in lockstep.
 *
 * A `menu` element carries no items of its own: it resolves them from the
 * site's `menus[]` by slot/menuId. The preview gets that array from
 * `/api/preview/layout` (the same builder the push uses), which is why the
 * header nav renders here at all.
 *
 * Three layouts, same as upstream:
 *   - header primary  → inline nav + hover dropdowns + mobile toggle/sheet
 *   - footer columns  → grouped multi-column nav (flat menus fall through)
 *   - everything else → plain inline nav
 */
import { useEffect, useRef, useState } from 'react'
import type { CSSProperties } from 'react'
import type { PublicBlockNode } from '../lib/public'
import {
  getArrayField,
  getNodeClasses,
  getNodeField,
  getNodeStyles,
  getStringField,
} from '../lib/blockRuntime'
import { getNodeDomId } from '../lib/responsiveRuntime'
import { getNodeChildren, normalizeBlockType, normalizeSchemaNodes } from '../lib/schema'
import { getContrastRatio, pickAccessibleTextColor } from '../lib/menuColors'
import { useRuntimeHeaderOverlay, useRuntimeHeaderSchema, useRuntimeMenus } from '../context'

type RuntimeMenuItem = {
  id?: string
  href?: string | null
  label?: string | null
  target?: string | null
  rel?: string | null
  visible?: boolean
  children?: RuntimeMenuItem[] | null
}

type FlatMenuItem = {
  id: string
  href: string
  label: string
  target?: string | null
  rel?: string | null
  depth: number
}

type HeaderActionLink = {
  id: string
  href: string
  label: string
  target?: string | null
  rel?: string | null
  styles: Record<string, string | number>
}

function visibleChildren(item: RuntimeMenuItem): RuntimeMenuItem[] {
  // One nesting level only — grandchildren are ignored by design.
  return (item.children ?? []).filter((child) => child && child.visible !== false)
}

function flattenMenuItems(items: RuntimeMenuItem[], depth = 0): FlatMenuItem[] {
  const flattened: FlatMenuItem[] = []

  for (const item of items) {
    if (!item || item.visible === false) {
      continue
    }

    const href = typeof item.href === 'string' && item.href.trim() ? item.href : '#'
    const label = typeof item.label === 'string' && item.label.trim() ? item.label : 'Link'

    flattened.push({
      id: typeof item.id === 'string' && item.id.trim() ? item.id : `${depth}:${href}:${label}`,
      href,
      label,
      target: item.target,
      rel: item.rel,
      depth,
    })

    if (Array.isArray(item.children) && item.children.length) {
      flattened.push(...flattenMenuItems(item.children, depth + 1))
    }
  }

  return flattened
}

function collectHeaderElements(nodes: PublicBlockNode[], visitor: (node: PublicBlockNode) => void) {
  for (const node of nodes) {
    if (getNodeField(node, 'visible') === false) {
      continue
    }
    visitor(node)
    collectHeaderElements(getNodeChildren(node), visitor)
  }
}

function getActionLinkStyle(actionLink: HeaderActionLink): CSSProperties {
  return {
    backgroundColor:
      typeof actionLink.styles.backgroundColor === 'string'
        ? actionLink.styles.backgroundColor
        : 'var(--builder-button-background, #2563eb)',
    border: typeof actionLink.styles.border === 'string' ? actionLink.styles.border : undefined,
    borderRadius:
      typeof actionLink.styles.borderRadius === 'string' ||
      typeof actionLink.styles.borderRadius === 'number'
        ? actionLink.styles.borderRadius
        : '18px',
    color: typeof actionLink.styles.color === 'string' ? actionLink.styles.color : 'var(--builder-button-text, #ffffff)',
    padding:
      typeof actionLink.styles.padding === 'string' || typeof actionLink.styles.padding === 'number'
        ? actionLink.styles.padding
        : '14px 18px',
    textDecoration: 'none',
  }
}

/** Inert in preview — see LinkBlock. */
const inert = (event: { preventDefault: () => void }) => event.preventDefault()

export function MenuBlock({ node }: { node: PublicBlockNode }) {
  const runtimeMenus = useRuntimeMenus()
  const runtimeHeaderSchema = useRuntimeHeaderSchema()
  const isOverlayHeader = useRuntimeHeaderOverlay()

  const nodeClasses = getNodeClasses(node)
  const nodeStyles = getNodeStyles(node)
  const nodeDomId = getNodeDomId(node) || undefined
  const colorMode = (getStringField(node, 'colorMode') || '').trim().toLowerCase()
  const variant = (getStringField(node, 'variant') || 'header-inline').trim().toLowerCase()
  const slot = (getStringField(node, 'slot') || '').trim().toLowerCase()
  const menuLabel = getStringField(node, 'menuLabel') || 'Site navigation'
  const isHeaderPrimaryMenu = slot === 'primary' || variant === 'header-inline'
  const isHeaderUtilityMenu = slot === 'utility' || variant === 'utility-inline'
  const isFooterColumnsMenu = slot === 'footer' || variant === 'footer-columns'

  const [isMobileMenuOpen, setMobileMenuOpen] = useState(false)
  const [openDropdownKey, setOpenDropdownKey] = useState<string | null>(null)
  const openTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  function clearDropdownTimers() {
    if (openTimer.current) {
      clearTimeout(openTimer.current)
      openTimer.current = null
    }
    if (closeTimer.current) {
      clearTimeout(closeTimer.current)
      closeTimer.current = null
    }
  }
  useEffect(() => clearDropdownTimers, [])

  // Hover intent: a short delay before opening (so skimming the bar doesn't
  // flash flyouts) and a longer one before closing (so the pointer can travel
  // from trigger to panel without the flyout vanishing).
  const scheduleDropdownOpen = (key: string) => {
    clearDropdownTimers()
    openTimer.current = setTimeout(() => setOpenDropdownKey(key), 80)
  }
  const scheduleDropdownClose = () => {
    clearDropdownTimers()
    closeTimer.current = setTimeout(() => setOpenDropdownKey(null), 160)
  }
  const closeDropdown = () => {
    clearDropdownTimers()
    setOpenDropdownKey(null)
  }

  function resolveMenuItemsForNode(target: PublicBlockNode | Record<string, unknown>) {
    const directItems = getArrayField<RuntimeMenuItem>(target, 'items')
    if (directItems.length) {
      return directItems
    }

    const explicitMenuId = getStringField(target, 'menuId')
    const nodeSlot = getStringField(target, 'slot')
    const label = getStringField(target, 'menuLabel')
    const menu = runtimeMenus.find((entry) => {
      if (explicitMenuId) {
        return entry.id === explicitMenuId
      }
      return (nodeSlot && entry.purpose === nodeSlot) || (label && entry.name === label)
    })

    return Array.isArray(menu?.items) ? (menu.items as RuntimeMenuItem[]) : []
  }

  const items = resolveMenuItemsForNode(node)
  const visibleItems = items.filter((item) => item?.visible !== false)

  // Grouped footer layout only when at least one item carries children —
  // all-flat footer menus keep the plain inline rendering.
  const hasFooterColumnGroups =
    isFooterColumnsMenu && visibleItems.some((item) => visibleChildren(item).length > 0)

  const headerSupplemental = (() => {
    const utilityItems: RuntimeMenuItem[] = []
    const actionLinks: HeaderActionLink[] = []

    collectHeaderElements(normalizeSchemaNodes(runtimeHeaderSchema), (candidate) => {
      const candidateId = getNodeDomId(candidate)
      if (candidateId && candidateId === nodeDomId) return

      const type = normalizeBlockType(getStringField(candidate, 'type'))
      if (type === 'header') return

      if (type === 'menu') {
        const nodeSlot = (getStringField(candidate, 'slot') || '').trim().toLowerCase()
        const nodeVariant = (getStringField(candidate, 'variant') || '').trim().toLowerCase()
        if (!(nodeSlot === 'utility' || nodeVariant === 'utility-inline')) return
        utilityItems.push(
          ...resolveMenuItemsForNode(candidate).filter((item) => item?.visible !== false)
        )
        return
      }

      if (type !== 'link') return

      const href = getStringField(candidate, 'href') || ''
      const label = getStringField(candidate, 'innerText', 'label', 'text') || ''
      if (!href.trim() || !label.trim()) return

      actionLinks.push({
        id: candidateId || label,
        href,
        label,
        rel: getStringField(candidate, 'rel'),
        target: getStringField(candidate, 'target'),
        styles: getNodeStyles(candidate),
      })
    })

    return { utilityItems, actionLinks }
  })()

  const flattenedUtilityItems = flattenMenuItems(headerSupplemental.utilityItems)

  const resolvedStyles = (() => {
    if (colorMode !== 'auto') {
      return nodeStyles as CSSProperties
    }
    const styles = { ...nodeStyles }
    delete styles.color
    return styles as CSSProperties
  })()

  const toggleTextColor = (() => {
    if (isOverlayHeader) return '#ffffff'
    const preferredColor =
      typeof nodeStyles.color === 'string' ? nodeStyles.color : 'var(--wt-color-text, #0f172a)'
    const contrastRatio = getContrastRatio(preferredColor, '#ffffff')
    if (contrastRatio !== null && contrastRatio >= 4.5) return preferredColor
    return pickAccessibleTextColor('#ffffff')
  })()

  if (isHeaderPrimaryMenu) {
    return (
      <div
        className={['wt-menu-shell', 'wt-menu-shell--header-primary', nodeClasses]
          .filter(Boolean)
          .join(' ')}
        style={resolvedStyles}
        data-wt-node-id={nodeDomId}
      >
        <nav className="wt-menu wt-menu--desktop">
          {visibleItems.map((item, index) => {
            const children = visibleChildren(item)
            const key = `${item.id || item.href || item.label || 'item'}:${index}`
            if (!children.length) {
              return (
                <a
                  key={key}
                  className="wt-menu-link wt-ui-link"
                  href={item.href || '#'}
                  target={item.target || undefined}
                  rel={item.rel || undefined}
                  onClick={inert}
                >
                  {item.label}
                </a>
              )
            }
            const isOpen = openDropdownKey === key
            return (
              <div
                key={key}
                className={['wt-menu-item--dropdown', isOpen ? 'wt-menu-item--open' : '']
                  .filter(Boolean)
                  .join(' ')}
                onMouseEnter={() => scheduleDropdownOpen(key)}
                onMouseLeave={scheduleDropdownClose}
                onBlur={(event) => {
                  const next = event.relatedTarget
                  const container = event.currentTarget
                  if (next instanceof Node && container.contains(next)) return
                  closeDropdown()
                }}
                onKeyDown={(event) => {
                  if (event.key === 'Escape') {
                    event.stopPropagation()
                    closeDropdown()
                  }
                }}
              >
                <span className="wt-menu-item__trigger">
                  <a
                    className="wt-menu-link wt-ui-link"
                    href={item.href || '#'}
                    target={item.target || undefined}
                    rel={item.rel || undefined}
                    onClick={inert}
                  >
                    {item.label}
                  </a>
                  <button
                    type="button"
                    className="wt-menu-caret"
                    aria-expanded={isOpen}
                    aria-controls={`wt-menu-flyout-${nodeDomId || 'menu'}-${index}`}
                    aria-label={`${item.label} submenu`}
                    onClick={(event) => {
                      event.preventDefault()
                      clearDropdownTimers()
                      setOpenDropdownKey((current) => (current === key ? null : key))
                    }}
                  >
                    <span className="wt-menu-caret__icon" aria-hidden="true" />
                  </button>
                </span>
                <div
                  id={`wt-menu-flyout-${nodeDomId || 'menu'}-${index}`}
                  className={['wt-menu-flyout', isOverlayHeader ? 'wt-menu-flyout--overlay' : '']
                    .filter(Boolean)
                    .join(' ')}
                  style={isOpen ? undefined : { display: 'none' }}
                >
                  {children.map((child) => (
                    <a
                      key={child.id || child.href || child.label}
                      className="wt-menu-flyout__link"
                      href={child.href || '#'}
                      target={child.target || undefined}
                      rel={child.rel || undefined}
                      onClick={inert}
                    >
                      {child.label}
                    </a>
                  ))}
                </div>
              </div>
            )
          })}
        </nav>

        <div className="wt-header-menu-toggle">
          <button
            type="button"
            className="wt-header-menu-toggle__button wt-ui-button wt-ui-menu-button"
            style={{
              color: toggleTextColor,
              borderColor: isOverlayHeader ? 'rgba(255,255,255,0.24)' : 'rgba(148,163,184,0.35)',
            }}
            aria-expanded={isMobileMenuOpen}
            aria-label={`Open ${menuLabel}`}
            onClick={() => setMobileMenuOpen(true)}
          >
            <span className="wt-header-menu-toggle__bars" aria-hidden="true">
              <span />
              <span />
              <span />
            </span>
            <span>Menu</span>
          </button>
        </div>

        {/* Upstream teleports this to <body>; inside the preview iframe the
            sheet is already fixed-position within the same document, so it
            covers the frame the same way without the portal. */}
        {isMobileMenuOpen && (
          <div
            className={['wt-mobile-menu-sheet', isOverlayHeader ? 'wt-mobile-menu-sheet--overlay' : '']
              .filter(Boolean)
              .join(' ')}
            onClick={(event) => {
              if (event.target === event.currentTarget) setMobileMenuOpen(false)
            }}
          >
            <div
              className={['wt-mobile-menu-sheet__surface', 'wt-ui-sheet', isOverlayHeader ? 'wt-ui-sheet--overlay' : '']
                .filter(Boolean)
                .join(' ')}
            >
              <div className="wt-mobile-menu-sheet__top">
                <button
                  type="button"
                  className="wt-mobile-menu-sheet__close wt-ui-button wt-ui-menu-button"
                  aria-label="Close menu"
                  onClick={() => setMobileMenuOpen(false)}
                >
                  <span className="wt-mobile-menu-sheet__close-mark" aria-hidden="true">
                    X
                  </span>
                </button>
              </div>
              <div className="wt-mobile-menu-sheet__body">
                <nav className="wt-mobile-menu-list" aria-label={menuLabel}>
                  {flattenMenuItems(visibleItems).map((item) => (
                    <a
                      key={item.id}
                      className="wt-mobile-menu-link wt-ui-link wt-ui-divider-link"
                      style={{ paddingLeft: `${12 + item.depth * 16}px` }}
                      href={item.href}
                      target={item.target || undefined}
                      rel={item.rel || undefined}
                      onClick={inert}
                    >
                      {item.label}
                    </a>
                  ))}
                </nav>
                {flattenedUtilityItems.length > 0 && (
                  <div className="wt-mobile-menu-section">
                    <p className="wt-mobile-menu-section__title">Utility</p>
                    <nav className="wt-mobile-menu-list" aria-label="Utility links">
                      {flattenedUtilityItems.map((item) => (
                        <a
                          key={item.id}
                          className="wt-mobile-menu-link wt-mobile-menu-link--secondary wt-ui-link wt-ui-divider-link"
                          style={{ paddingLeft: `${12 + item.depth * 16}px` }}
                          href={item.href}
                          target={item.target || undefined}
                          rel={item.rel || undefined}
                          onClick={inert}
                        >
                          {item.label}
                        </a>
                      ))}
                    </nav>
                  </div>
                )}
                {headerSupplemental.actionLinks.length > 0 && (
                  <div className="wt-mobile-menu-actions">
                    {headerSupplemental.actionLinks.map((actionLink) => (
                      <a
                        key={actionLink.id}
                        className="wt-mobile-menu-action wt-ui-button wt-ui-link"
                        style={getActionLinkStyle(actionLink)}
                        href={actionLink.href}
                        target={actionLink.target || undefined}
                        rel={actionLink.rel || undefined}
                        onClick={inert}
                      >
                        {actionLink.label}
                      </a>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>
        )}
      </div>
    )
  }

  if (hasFooterColumnGroups) {
    return (
      <nav
        className={['wt-footer-columns', nodeClasses].filter(Boolean).join(' ')}
        style={resolvedStyles}
        data-wt-node-id={nodeDomId}
        aria-label={menuLabel}
      >
        {visibleItems.map((group, index) => {
          const children = visibleChildren(group)
          return (
            <div
              key={group.id || group.href || group.label || index}
              className={[
                'wt-footer-columns__group',
                children.length > 0 ? 'wt-footer-columns__group--has-children' : '',
              ]
                .filter(Boolean)
                .join(' ')}
            >
              {group.href ? (
                <a
                  className="wt-footer-columns__heading wt-ui-link"
                  href={group.href}
                  target={group.target || undefined}
                  rel={group.rel || undefined}
                  onClick={inert}
                >
                  {group.label}
                </a>
              ) : (
                <span className="wt-footer-columns__heading">{group.label}</span>
              )}
              {children.length > 0 && (
                <ul className="wt-footer-columns__list">
                  {children.map((child) => (
                    <li key={child.id || child.href || child.label}>
                      <a
                        className="wt-menu-link wt-ui-link"
                        href={child.href || '#'}
                        target={child.target || undefined}
                        rel={child.rel || undefined}
                        onClick={inert}
                      >
                        {child.label}
                      </a>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )
        })}
      </nav>
    )
  }

  return (
    <nav
      className={['wt-menu', nodeClasses, isHeaderUtilityMenu ? 'wt-menu--hide-mobile' : '']
        .filter(Boolean)
        .join(' ')}
      style={resolvedStyles}
      data-wt-node-id={nodeDomId}
    >
      {visibleItems.map((item) => (
        <a
          key={item.href || item.label}
          className="wt-menu-link wt-ui-link"
          href={item.href || '#'}
          target={item.target || undefined}
          rel={item.rel || undefined}
          onClick={inert}
        >
          {item.label}
        </a>
      ))}
    </nav>
  )
}
