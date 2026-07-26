import type { GeneratedPage } from '@/lib/types'

/**
 * Resolve an anchor's href to a generated page slug, or null when it points
 * somewhere the preview can't go (external site, mail/tel, in-page anchor).
 *
 * Lives here rather than in src/preview/ on purpose: the vendored blocks are a
 * port of webtree-public and must not diverge from it, so preview navigation is
 * implemented by intercepting clicks around them instead of editing them.
 */
export function resolveInternalSlug(
  href: string | null | undefined,
  pages: Pick<GeneratedPage, 'slug' | 'is_homepage'>[],
): string | null {
  const raw = (href ?? '').trim()
  if (!raw) return null

  // Non-navigational or off-site: leave to the blocks' own inert handlers.
  if (raw.startsWith('#')) return null
  if (/^[a-z][a-z0-9+.-]*:/i.test(raw) && !/^https?:/i.test(raw)) return null

  let path = raw
  if (/^https?:\/\//i.test(raw)) {
    // The generator emits site-relative hrefs; an absolute URL means the CMS
    // wrote a real domain in. Take its path and let slug matching decide.
    try {
      path = new URL(raw).pathname
    } catch {
      return null
    }
  }

  // Drop query + hash, then normalise to a bare slug.
  const slug = path
    .split(/[?#]/, 1)[0]
    .replace(/^\/+/, '')
    .replace(/\/+$/, '')
    .toLowerCase()

  const homepage = pages.find((page) => page.is_homepage)
  if (slug === '' || slug === 'index' || slug === 'home') {
    return homepage?.slug ?? null
  }

  const match = pages.find((page) => (page.slug || '').toLowerCase() === slug)
  return match ? match.slug : null
}

/** The path the visitor would see for a page — what the preview's URL chip shows. */
export function pagePath(page: Pick<GeneratedPage, 'slug' | 'is_homepage'>): string {
  if (page.is_homepage || !page.slug) return '/'
  return `/${page.slug.replace(/^\/+/, '')}`
}
