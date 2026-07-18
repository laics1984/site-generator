// Vendored from webtree-public/components/blocks/MenuBlock.vue's <script>
// block (parseColor/toLinearSrgb/getRelativeLuminance/getContrastRatio/
// pickAccessibleTextColor) — lifted into its own module per the README,
// since this app's MenuBlock.tsx only needs the exported contrast helpers,
// not a Vue component. Logic is verbatim.

type ParsedColor = {
  r: number
  g: number
  b: number
}

function expandHexColor(value: string) {
  return value
    .split('')
    .map((character) => character + character)
    .join('')
}

function extractVarFallback(value: string) {
  const match = value.match(/var\(\s*--[^,)]+(?:,\s*([^)]+))?\)/i)
  return match?.[1]?.trim() ?? null
}

function parseColor(value?: string | null): ParsedColor | null {
  if (!value) {
    return null
  }

  const normalized = value.trim()

  if (!normalized) {
    return null
  }

  if (normalized.startsWith('var(')) {
    return parseColor(extractVarFallback(normalized))
  }

  if (normalized.startsWith('#')) {
    const raw = normalized.slice(1)
    const hex = raw.length === 3 ? expandHexColor(raw) : raw.length >= 6 ? raw.slice(0, 6) : ''

    if (hex.length !== 6) {
      return null
    }

    const r = Number.parseInt(hex.slice(0, 2), 16)
    const g = Number.parseInt(hex.slice(2, 4), 16)
    const b = Number.parseInt(hex.slice(4, 6), 16)

    if ([r, g, b].some((channel) => Number.isNaN(channel))) {
      return null
    }

    return { r, g, b }
  }

  if (normalized.startsWith('rgb')) {
    const match = normalized.match(
      /^rgba?\(\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)(?:\s*,\s*[0-9.]+\s*)?\)$/i
    )

    if (!match) {
      return null
    }

    return {
      r: Number.parseFloat(match[1]),
      g: Number.parseFloat(match[2]),
      b: Number.parseFloat(match[3]),
    }
  }

  const namedColor = normalized.toLowerCase()

  if (namedColor === 'white') {
    return { r: 255, g: 255, b: 255 }
  }

  if (namedColor === 'black') {
    return { r: 0, g: 0, b: 0 }
  }

  return null
}

function toLinearSrgb(value: number) {
  const channel = value / 255
  return channel <= 0.03928 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4
}

function getRelativeLuminance(value: string) {
  const parsed = parseColor(value)

  if (!parsed) {
    return null
  }

  return (
    0.2126 * toLinearSrgb(parsed.r) +
    0.7152 * toLinearSrgb(parsed.g) +
    0.0722 * toLinearSrgb(parsed.b)
  )
}

export function getContrastRatio(foreground: string, background: string) {
  const foregroundLuminance = getRelativeLuminance(foreground)
  const backgroundLuminance = getRelativeLuminance(background)

  if (foregroundLuminance === null || backgroundLuminance === null) {
    return null
  }

  const lighter = Math.max(foregroundLuminance, backgroundLuminance)
  const darker = Math.min(foregroundLuminance, backgroundLuminance)

  return (lighter + 0.05) / (darker + 0.05)
}

export function pickAccessibleTextColor(background: string) {
  const candidates = ['#0f172a', '#1e293b', '#334155']

  let bestCandidate = candidates[0]
  let bestRatio = -1

  for (const candidate of candidates) {
    const ratio = getContrastRatio(candidate, background)

    if (ratio === null) {
      continue
    }

    if (ratio > bestRatio) {
      bestCandidate = candidate
      bestRatio = ratio
    }
  }

  return bestCandidate
}
