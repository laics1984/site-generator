// Vendored from webtree-public/lib/languages.ts — verbatim except the type
// import path ('~/types/public' → './public').
//
/**
 * A page in several languages, for the head, the sitemap and the menus — the
 * one place this renderer reads `page.alternates` (webtree-cms-api
 * specs/multilingual-sites.md).
 *
 * The CMS decides WHICH languages a page is published in; this module only
 * presents that answer: hreflang links (reciprocal, self included, plus
 * x-default), the `<html lang dir>` pair, sitemap alternates, and language
 * menus whose links follow the reader's current page.
 */
import type { PublicAlternate, PublicMenu, PublicMenuItem } from './public'

/** Language subtags written right to left. */
const RTL_LANGUAGES = new Set(['ar', 'arc', 'dv', 'fa', 'ha', 'he', 'khw', 'ks', 'ku', 'ps', 'sd', 'ur', 'yi'])

/** `lang` and `dir` for an element or `<html>` in `locale` (BCP 47). */
export function languageAttrs(locale: string | null | undefined): { lang?: string; dir?: 'rtl' | 'ltr' } {
  if (!locale) return {}
  const base = locale.split('-')[0]?.toLowerCase() ?? ''
  return { lang: locale, dir: RTL_LANGUAGES.has(base) ? 'rtl' : 'ltr' }
}

/** Only a page published in two or more languages has alternates to state. */
export function hasAlternates(alternates: PublicAlternate[] | null | undefined): alternates is PublicAlternate[] {
  return Array.isArray(alternates) && alternates.length > 1
}

/**
 * `<link rel="alternate" hreflang>` for every language of the page, itself
 * included (Google wants the set reciprocal and self-referencing), and
 * `x-default` → the primary language's page, which lives at the root (the
 * CMS lists it first). Absolute URLs only: a link without one is dropped.
 */
export function hreflangLinks(alternates: PublicAlternate[] | null | undefined): Array<{ rel: 'alternate'; hreflang: string; href: string }> {
  if (!hasAlternates(alternates)) return []
  const links = alternates
    .filter((alt) => typeof alt.href === 'string' && alt.href)
    .map((alt) => ({ rel: 'alternate' as const, hreflang: alt.locale, href: alt.href as string }))
  if (links.length < 2) return []
  return [...links, { rel: 'alternate', hreflang: 'x-default', href: links[0]!.href }]
}

/** A menu that switches the reader's language (`purpose: "language"`). */
export function isLanguageMenu(menu: PublicMenu | null | undefined): boolean {
  return menu?.purpose === 'language'
}

/**
 * A `purpose: "language"` menu, resolved against this page.
 *
 * Its items are authored — one per language, each carrying `locale` and a
 * label in that language — so the words stay editable and translatable in
 * the builder. What the renderer owns is where they point: each item links to
 * THIS page in its language, an item whose language the page isn't published
 * in is left out (no link to a page that doesn't exist, no English behind a
 * Chinese label), and a page in one language shows no language menu at all.
 * Every other menu passes through untouched.
 */
export function resolveLanguageMenus(
  menus: PublicMenu[] | null | undefined,
  alternates: PublicAlternate[] | null | undefined,
  currentLocale: string | null | undefined
): PublicMenu[] {
  const list = Array.isArray(menus) ? menus : []
  if (!list.some(isLanguageMenu)) return list
  const byLocale = new Map((hasAlternates(alternates) ? alternates : []).map((alt) => [alt.locale, alt]))

  return list.map((menu) => {
    if (!isLanguageMenu(menu)) return menu
    const items: PublicMenuItem[] = (menu.items ?? []).flatMap((item) => {
      const alt = item?.locale ? byLocale.get(item.locale) : undefined
      if (!alt) return []
      return [{
        ...item,
        href: alt.path,
        hreflang: alt.locale,
        lang: alt.locale,
        current: alt.locale === currentLocale,
        children: null,
      }]
    })
    return { ...menu, items }
  })
}

/** `<xhtml:link>` entries for one sitemap URL, x-default included. */
export function sitemapAlternates(
  alternates: Array<{ locale: string; path: string }> | null | undefined,
  absolute: (path: string) => string
): Array<{ hreflang: string; href: string }> {
  if (!Array.isArray(alternates) || alternates.length < 2) return []
  const links = alternates.map((alt) => ({ hreflang: alt.locale, href: absolute(alt.path) }))
  return [...links, { hreflang: 'x-default', href: links[0]!.href }]
}
