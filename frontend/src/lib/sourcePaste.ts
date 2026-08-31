/**
 * Pasted content, on the frontend's side of the line.
 *
 * The backend reads and merges a paste (services/paste_source.py). This module
 * holds the two things that are genuinely the UI's: telling the user what
 * they've pasted while they type, and folding the merge response back into the
 * preview object the reader produced.
 */

import type { ScrapePreview } from '@/lib/types'

/**
 * Whether a paste reads as markup rather than prose.
 *
 * Mirrors `paste_source.looks_like_html`. This copy exists purely for the
 * inline badge while the user types — the backend does the authoritative
 * detection, so a disagreement here is cosmetic, never a wrong parse. Same
 * arrangement as lib/sourceDetect.ts.
 *
 * The tag name must sit flush against the `<`, as HTML requires: allowing a
 * space makes "Pricing: a < b and c > d" look like a `<b>` tag.
 */
const HTML_TAG =
  /<(?:!doctype\s+html|\/?(?:html|head|body|main|article|section|header|footer|nav|aside|div|span|p|h[1-6]|ul|ol|li|dl|dt|dd|table|tr|td|th|thead|tbody|figure|figcaption|blockquote|img|a|br|hr|strong|em|b|i|picture|source)\b)[^>]*>/i

export function looksLikeHtml(text: string): boolean {
  return HTML_TAG.test(text || '')
}

/**
 * Fold a merge response back into the preview the reader produced.
 *
 * The backend merged the content and hands back the merged `source_content`
 * plus the paste's OWN image candidates. Everything else on the preview is the
 * reader's and only the reader could have measured it — the detected brand, the
 * crawl frontier that powers "Crawl N more", the Facebook facts — so it is kept
 * as-is rather than round-tripped through a call that never saw it.
 */
export function withPastedContent(
  base: ScrapePreview,
  pasted: ScrapePreview,
): ScrapePreview {
  return {
    ...base,
    source_content: pasted.source_content,
    discovered_count: pasted.discovered_count,
    image_candidates: [...base.image_candidates, ...pasted.image_candidates],
    paste: pasted.paste,
  }
}
