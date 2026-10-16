import type { GeneratedPage, GeneratedSite } from '@/lib/types'

export interface PreviewAlternate {
  locale: string
  path: string
}

/**
 * A preview page's language and its alternates — what the CMS sends as
 * `page.locale` / `page.alternates` once the site is published (webtree-cms-api
 * specs/multilingual-sites.md), worked out here from the generated site, so a
 * `purpose: "language"` menu in the preview links where it will live.
 *
 * A translated page names its counterpart (`translation_of`); the primary page
 * is the one at that slug. Only a site with pages in other languages has any.
 */
export function previewLanguages(site: GeneratedSite, page: GeneratedPage): {
  locale: string | null
  alternates: PreviewAlternate[]
} {
  const plan = site.multilingual
  const primary = plan?.primary ?? null
  const locale = page.locale ?? primary
  if (!plan || plan.mode !== 'separate') return { locale, alternates: [] }

  const base = page.translation_of ?? page.slug
  const path = (slug: string) => (slug ? `/${slug}` : '/')
  const translations = site.pages.filter((p) => p.locale && p.translation_of === base)
  if (translations.length === 0) return { locale, alternates: [] }
  const order = (code: string) => plan.secondary.indexOf(code)
  return {
    locale,
    alternates: [
      { locale: plan.primary, path: path(base) },
      ...translations
        .sort((a, b) => order(a.locale!) - order(b.locale!))
        .map((p) => ({ locale: p.locale!, path: path(p.slug) })),
    ],
  }
}
