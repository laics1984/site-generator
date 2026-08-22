// Vendored from webtree-public/lib/adaptiveInk.ts — verbatim (it has no
// imports to re-point).
//
// Scroll-adaptive header ink.
//
// The floating-pill header (`behavior.adaptiveInk`) floats over the page and
// never solidifies, so it has no chrome of its own to stay legible against:
// dark nav ink disappears the moment the visitor scrolls a dark section under
// it. The fix is to recolour the bar per section — but a renderer cannot work
// out a section's luminance for itself (a photo's brightness is not in the
// DOM, and the scrim that darkens it is a generator decision), so the
// generator states it: `schema_builder._stamp_band_markers` puts
// `wt-band-light` / `wt-band-dark` on every top-level section.
//
// This module is the pure part of the reading: it turns measured rects into a
// band. The shells (PublicSiteShell.vue, the preview's PreviewSiteShell.tsx,
// the builder's Editor.tsx) own the measuring, because each scrolls a
// different thing — the window, an iframe, a scale-transformed canvas.

export type Band = 'light' | 'dark'

export interface BandRect {
  top: number
  bottom: number
  band: Band
}

export const BAND_SELECTOR = '.wt-band-light, .wt-band-dark'

export function bandOfElement(el: Element): Band | null {
  if (el.classList.contains('wt-band-dark')) return 'dark'
  if (el.classList.contains('wt-band-light')) return 'light'
  return null
}

// Rects for every marked section, in document order, measured in the same
// coordinate frame the probe point will be given in.
export function readBandRects(root: ParentNode): BandRect[] {
  const out: BandRect[] = []
  for (const el of Array.from(root.querySelectorAll(BAND_SELECTOR))) {
    const band = bandOfElement(el)
    if (!band) continue
    const rect = el.getBoundingClientRect()
    out.push({ top: rect.top, bottom: rect.bottom, band })
  }
  return out
}

// The band at `y`. Later sections win a tie, so at the seam between two
// sections the ink flips exactly when the incoming one starts covering the
// probe — the alternative (first match wins) leaves the header holding the
// outgoing section's ink over the new one for a pixel.
//
// Off either end, the nearest section is used rather than null: above the
// first section is the overlay gap the header itself occupies, and below the
// last is the footer, whose surface is the last band's neighbour more often
// than it is anything else. Only an empty page yields null.
export function pickBandAtY(rects: BandRect[], y: number): Band | null {
  let covering: Band | null = null
  let lastAbove: Band | null = null
  for (const rect of rects) {
    if (y >= rect.top && y < rect.bottom) covering = rect.band
    if (rect.bottom <= y) lastAbove = rect.band
  }
  if (covering) return covering
  if (lastAbove) return lastAbove
  return rects.length ? rects[0].band : null
}

// The header's own vertical midline is the probe: the pill is a short capsule
// with an inset above it, so its top edge can still be over the previous
// section while the bar itself has clearly moved onto the next one.
export function probeY(headerRect: { top: number; height: number }): number {
  return headerRect.top + headerRect.height / 2
}

// The class the shell puts on the header element. Named for the INK, not the
// band it was derived from: `--ink-light` means light ink (over a dark
// section), which is what the CSS rule reads as.
export function inkClassForBand(band: Band | null): string | null {
  if (band === 'dark') return 'wt-page-header--ink-light'
  if (band === 'light') return 'wt-page-header--ink-dark'
  return null
}
