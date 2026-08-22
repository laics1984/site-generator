/**
 * Brand glyphs for the `menu-social` slot.
 *
 * The scraper hands the social menu (label, href) pairs — "Instagram",
 * "https://instagram.com/acme" — and rendering those as words puts a row of
 * platform names (or, when a source labels its links with the raw URL, a row of
 * URLs) into the header/footer where every real site shows icons. MenuBlock
 * resolves each item through here and draws the glyph instead.
 *
 * Platform detection matches on the href's host first (authoritative, and it
 * survives a junk label) and falls back to the label. Anything unrecognized
 * gets a monogram disc so the row stays a row of icons.
 *
 * All paths are authored on a 24×24 box and filled with `currentColor`, so a
 * glyph inherits the menu's ink the same way `.wt-menu-link` does.
 */

export type SocialGlyph = {
  /** Stable platform key — also the React list key. */
  key: string
  /** Accessible name; the visual label is the glyph itself. */
  label: string
  /** Filled subpaths. Empty when the glyph is a monogram. */
  paths: string[]
  /** Set when a subpath is meant to knock a hole in the one before it. */
  fillRule?: 'evenodd'
  /** Single letter drawn in a disc — the no-brand-path fallback. */
  monogram?: string
}

type PlatformGlyph = Omit<SocialGlyph, 'monogram'>

const PLATFORMS: Record<string, PlatformGlyph> = {
  facebook: {
    key: 'facebook',
    label: 'Facebook',
    paths: [
      'M24 12.07C24 5.44 18.63.07 12 .07S0 5.44 0 12.07c0 5.99 4.39 10.95 10.13 11.85v-8.38H7.08v-3.47h3.05V9.43c0-3.01 1.79-4.67 4.53-4.67 1.31 0 2.69.24 2.69.24v2.95h-1.51c-1.49 0-1.96.93-1.96 1.88v2.25h3.33l-.53 3.47h-2.8v8.38C19.61 23.02 24 18.06 24 12.07Z',
    ],
  },
  instagram: {
    key: 'instagram',
    label: 'Instagram',
    paths: [
      'M12 2.2c3.2 0 3.58.01 4.85.07 1.17.05 1.8.25 2.23.41.56.22.96.48 1.38.9.42.42.68.82.9 1.38.16.42.36 1.06.41 2.23.06 1.27.07 1.65.07 4.85s-.01 3.58-.07 4.85c-.05 1.17-.25 1.8-.41 2.23a3.7 3.7 0 0 1-.9 1.38c-.42.42-.82.68-1.38.9-.42.16-1.06.36-2.23.41-1.27.06-1.65.07-4.85.07s-3.58-.01-4.85-.07c-1.17-.05-1.8-.25-2.23-.41a3.7 3.7 0 0 1-1.38-.9 3.7 3.7 0 0 1-.9-1.38c-.16-.42-.36-1.06-.41-2.23C2.21 15.58 2.2 15.2 2.2 12s.01-3.58.07-4.85c.05-1.17.25-1.8.41-2.23.22-.56.48-.96.9-1.38.42-.42.82-.68 1.38-.9.42-.16 1.06-.36 2.23-.41C8.42 2.21 8.8 2.2 12 2.2Zm0 1.8c-3.14 0-3.5.01-4.74.07-1.14.05-1.76.24-2.17.4-.55.21-.94.47-1.35.88-.41.41-.67.8-.88 1.35-.16.41-.35 1.03-.4 2.17-.06 1.24-.07 1.6-.07 4.74s.01 3.5.07 4.74c.05 1.14.24 1.76.4 2.17.21.55.47.94.88 1.35.41.41.8.67 1.35.88.41.16 1.03.35 2.17.4 1.24.06 1.6.07 4.74.07s3.5-.01 4.74-.07c1.14-.05 1.76-.24 2.17-.4.55-.21.94-.47 1.35-.88.41-.41.67-.8.88-1.35.16-.41.35-1.03.4-2.17.06-1.24.07-1.6.07-4.74s-.01-3.5-.07-4.74c-.05-1.14-.24-1.76-.4-2.17a3.6 3.6 0 0 0-.88-1.35 3.6 3.6 0 0 0-1.35-.88c-.41-.16-1.03-.35-2.17-.4C15.5 4.01 15.14 4 12 4Zm0 3.03a4.97 4.97 0 1 1 0 9.94 4.97 4.97 0 0 1 0-9.94Zm0 1.8a3.17 3.17 0 1 0 0 6.34 3.17 3.17 0 0 0 0-6.34Zm5.17-3.2a1.16 1.16 0 1 1 0 2.33 1.16 1.16 0 0 1 0-2.33Z',
    ],
  },
  x: {
    key: 'x',
    label: 'X',
    paths: [
      'M17.53 3h3.06l-6.69 7.64L21.75 21h-6.16l-4.82-6.3L5.25 21H2.19l7.15-8.17L2.25 3h6.31l4.36 5.77L17.53 3Zm-1.07 16.16h1.69L7.62 4.74H5.8l10.66 14.42Z',
    ],
  },
  linkedin: {
    key: 'linkedin',
    label: 'LinkedIn',
    paths: [
      'M4.98 3.5a2.5 2.5 0 1 1 0 5.001 2.5 2.5 0 0 1 0-5.001ZM3 9h4v12H3V9Zm7 0h3.83v1.64h.05c.53-1 1.84-2.06 3.79-2.06 4.05 0 4.8 2.67 4.8 6.13V21h-4v-5.58c0-1.33-.02-3.04-1.85-3.04-1.86 0-2.14 1.45-2.14 2.94V21h-4V9Z',
    ],
  },
  youtube: {
    key: 'youtube',
    label: 'YouTube',
    // Body and play triangle must share one path — `evenodd` only knocks a hole
    // through subpaths of the same element.
    fillRule: 'evenodd',
    paths: [
      'M22.54 6.42a2.78 2.78 0 0 0-1.95-1.97C18.88 4 12 4 12 4s-6.88 0-8.59.45a2.78 2.78 0 0 0-1.95 1.97A29 29 0 0 0 1 12a29 29 0 0 0 .46 5.58 2.78 2.78 0 0 0 1.95 1.97C5.12 20 12 20 12 20s6.88 0 8.59-.45a2.78 2.78 0 0 0 1.95-1.97A29 29 0 0 0 23 12a29 29 0 0 0-.46-5.58ZM10 15.5v-7l6 3.5-6 3.5Z',
    ],
  },
  tiktok: {
    key: 'tiktok',
    label: 'TikTok',
    paths: [
      'M16.6 2h-3.1v13.2a2.55 2.55 0 1 1-2.55-2.55c.26 0 .51.04.75.11V9.6a5.9 5.9 0 0 0-.75-.05 5.63 5.63 0 1 0 5.63 5.63V8.9a6.7 6.7 0 0 0 3.92 1.26V7.06A3.93 3.93 0 0 1 16.6 3.1V2Z',
    ],
  },
  pinterest: {
    key: 'pinterest',
    label: 'Pinterest',
    paths: [
      'M12 2a10 10 0 0 0-3.65 19.31c-.09-.78-.16-1.98.03-2.83.18-.78 1.18-4.98 1.18-4.98s-.3-.6-.3-1.5c0-1.4.82-2.45 1.83-2.45.86 0 1.28.65 1.28 1.43 0 .87-.56 2.17-.85 3.38-.24 1.01.51 1.83 1.5 1.83 1.8 0 3.19-1.9 3.19-4.64 0-2.43-1.74-4.13-4.23-4.13-2.88 0-4.57 2.16-4.57 4.39 0 .87.33 1.8.75 2.31.08.1.09.19.07.29-.08.32-.25.99-.28 1.13-.04.18-.15.22-.34.13-1.25-.58-2.03-2.4-2.03-3.86 0-3.14 2.28-6.03 6.58-6.03 3.45 0 6.14 2.46 6.14 5.75 0 3.43-2.16 6.19-5.17 6.19-1.01 0-1.96-.53-2.28-1.15l-.62 2.37c-.23.86-.83 1.94-1.24 2.6A10 10 0 1 0 12 2Z',
    ],
  },
  github: {
    key: 'github',
    label: 'GitHub',
    paths: [
      'M12 .3a12 12 0 0 0-3.79 23.4c.6.11.82-.26.82-.58l-.02-2.03c-3.34.72-4.04-1.61-4.04-1.61-.55-1.39-1.34-1.76-1.34-1.76-1.09-.75.08-.73.08-.73 1.2.08 1.84 1.24 1.84 1.24 1.07 1.84 2.81 1.31 3.5 1 .11-.78.42-1.31.76-1.61-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.13-.3-.54-1.52.11-3.18 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 6.01 0c2.29-1.55 3.3-1.23 3.3-1.23.65 1.66.24 2.88.12 3.18.77.84 1.23 1.91 1.23 3.22 0 4.61-2.8 5.62-5.48 5.92.43.37.81 1.1.81 2.22l-.01 3.29c0 .32.21.7.82.58A12 12 0 0 0 12 .3Z',
    ],
  },
  whatsapp: {
    key: 'whatsapp',
    label: 'WhatsApp',
    paths: [
      'M12.04 2a9.9 9.9 0 0 0-8.5 14.98L2 22.5l5.66-1.48A9.9 9.9 0 1 0 12.04 2Zm0 1.8a8.1 8.1 0 1 1-4.1 15.07l-.3-.17-3.35.88.9-3.27-.2-.32A8.1 8.1 0 0 1 12.04 3.8Zm-3.7 4.06c-.18 0-.47.07-.72.34-.24.27-.94.92-.94 2.24s.96 2.6 1.1 2.78c.13.18 1.87 2.98 4.61 4.06 2.28.9 2.74.72 3.24.67.5-.04 1.6-.65 1.83-1.29.23-.63.23-1.17.16-1.28-.07-.11-.25-.18-.52-.31-.27-.14-1.6-.79-1.85-.88-.25-.09-.43-.13-.61.14-.18.27-.7.88-.86 1.06-.16.18-.32.2-.59.07-.27-.14-1.14-.42-2.17-1.34a8.2 8.2 0 0 1-1.5-1.87c-.16-.27-.02-.42.12-.55.12-.12.27-.32.4-.48.14-.16.18-.27.27-.45.09-.18.05-.34-.02-.48-.07-.13-.6-1.46-.83-2-.22-.53-.44-.46-.6-.46l-.52-.01Z',
    ],
  },
  telegram: {
    key: 'telegram',
    label: 'Telegram',
    paths: [
      'M21.94 4.3 2.86 11.66c-1.16.45-1.15 1.09-.2 1.38l4.79 1.5 1.84 5.65c.22.62.11.86.76.86.5 0 .72-.23 1-.5l2.4-2.34 4.98 3.68c.92.5 1.58.25 1.81-.85l3.28-15.47c.34-1.35-.51-1.96-1.58-1.47ZM7.74 14.15l10.63-6.7c.53-.32.99-.15.62.2l-9.1 8.22-.36 3.83-1.79-5.55Z',
    ],
  },
}

// Host → platform key. Mirrors backend/app/services/nav_extraction.py's
// _SOCIAL_DOMAINS, so anything the extractor can emit resolves here.
const HOST_PLATFORMS: Array<[string, string]> = [
  ['facebook.com', 'facebook'],
  ['fb.com', 'facebook'],
  ['instagram.com', 'instagram'],
  ['twitter.com', 'x'],
  ['x.com', 'x'],
  ['linkedin.com', 'linkedin'],
  ['youtube.com', 'youtube'],
  ['youtu.be', 'youtube'],
  ['tiktok.com', 'tiktok'],
  ['pinterest.com', 'pinterest'],
  ['github.com', 'github'],
  ['wa.me', 'whatsapp'],
  ['whatsapp.com', 'whatsapp'],
  ['t.me', 'telegram'],
  ['telegram.me', 'telegram'],
]

// Label → platform key, for the (rare) item whose href is a redirect or a
// tracked link but whose label still names the platform.
const LABEL_PLATFORMS: Array<[string, string]> = [
  ['facebook', 'facebook'],
  ['instagram', 'instagram'],
  ['twitter', 'x'],
  ['linkedin', 'linkedin'],
  ['youtube', 'youtube'],
  ['tiktok', 'tiktok'],
  ['pinterest', 'pinterest'],
  ['github', 'github'],
  ['whatsapp', 'whatsapp'],
  ['telegram', 'telegram'],
]

function hostOf(href: string): string {
  const raw = href.trim()
  if (!raw) return ''
  try {
    // Tolerate protocol-relative and bare hosts — the menu carries whatever the
    // source page had.
    const normalized = /^[a-z][a-z0-9+.-]*:/i.test(raw)
      ? raw
      : `https://${raw.replace(/^\/\//, '')}`
    return new URL(normalized).hostname.toLowerCase().replace(/^www\./, '')
  } catch {
    return ''
  }
}

function monogramFor(label: string, href: string): string {
  const source = /[a-z0-9]/i.test(label) ? label : hostOf(href)
  const match = source.match(/[a-z0-9]/i)
  return (match ? match[0] : '?').toUpperCase()
}

/**
 * The glyph for one social menu item. Never null: an unrecognized platform
 * falls back to a monogram disc so the row reads as icons throughout.
 */
export function resolveSocialGlyph(href: string, label: string): SocialGlyph {
  const host = hostOf(href)
  if (host) {
    for (const [domain, key] of HOST_PLATFORMS) {
      if (host === domain || host.endsWith(`.${domain}`)) {
        return PLATFORMS[key]
      }
    }
  }

  const normalizedLabel = label.trim().toLowerCase()
  if (normalizedLabel) {
    if (normalizedLabel === 'x') return PLATFORMS.x
    for (const [needle, key] of LABEL_PLATFORMS) {
      if (normalizedLabel.includes(needle)) {
        return PLATFORMS[key]
      }
    }
  }

  const name = label.trim() || host || 'Link'
  return {
    key: `monogram:${name}`,
    label: name,
    paths: [],
    monogram: monogramFor(label, href),
  }
}

/**
 * Accessible name for a social item. A label that is just the URL (some sources
 * label their icon links that way) would be read out character by character, so
 * the resolved platform name wins over it.
 */
export function socialItemLabel(glyph: SocialGlyph, label: string): string {
  const trimmed = label.trim()
  if (!trimmed || /^(https?:)?\/\//i.test(trimmed) || hostOf(trimmed) === trimmed) {
    return glyph.label
  }
  return trimmed
}
