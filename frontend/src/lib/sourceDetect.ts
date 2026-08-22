/**
 * Which reader will handle a pasted link.
 *
 * Mirrors `backend/app/services/source_detect.py`. This copy exists purely for
 * the inline affordance while the user types — the badge, the helper text, the
 * button label. The backend does the authoritative routing, so a disagreement
 * here is cosmetic, never a wrong fetch.
 *
 * Deliberately not a network call: the whole point is that the form reacts on
 * the keystroke, before anything is submitted.
 */

const FACEBOOK_HOSTS = ['facebook.com', 'fb.com', 'fb.me', 'facebook.net']

const AT_HANDLE = /^@[A-Za-z0-9.\-_]{1,80}$/

function hostOf(value: string): string {
  const raw = value.trim()
  if (!raw) return ''
  const withScheme = raw.includes('//') ? raw : `https://${raw}`
  try {
    return new URL(withScheme).hostname.toLowerCase().replace(/\.$/, '')
  } catch {
    return ''
  }
}

/**
 * The dot boundary is what stops `notfacebook.com` and
 * `facebook.com.evil.example` matching — a bare `endsWith` would badge them as
 * Facebook and promise the user something we can't deliver.
 */
function hostMatches(host: string, suffixes: string[]): boolean {
  return suffixes.some((s) => host === s || host.endsWith(`.${s}`))
}

export function isFacebookUrl(value: string): boolean {
  if (AT_HANDLE.test(value.trim())) return true
  return hostMatches(hostOf(value), FACEBOOK_HOSTS)
}

/**
 * Shapes that are Facebook links but not business Pages. Used only to warn
 * early — the backend re-checks and owns the real error message.
 */
export function facebookLinkWarning(value: string): string | null {
  if (!isFacebookUrl(value)) return null
  const path = value.trim().toLowerCase()
  if (/facebook\.com\/groups\//.test(path)) return 'That looks like a group, not a business Page.'
  if (/facebook\.com\/events\//.test(path)) return 'That looks like an event, not a business Page.'
  if (/facebook\.com\/people\//.test(path))
    return 'That looks like a personal profile, not a business Page.'
  if (/\/(posts|videos|photos)\/[^/]+/.test(path) || /story\.php|watch\/?\?/.test(path))
    return 'That links to a single post. Use the Page’s own link instead.'
  return null
}
