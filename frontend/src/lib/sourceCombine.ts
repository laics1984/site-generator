/**
 * Combining a URL-crawl preview and a Document-upload preview, once both have
 * landed.
 *
 * The backend's `/api/source/merge` only joins the two `source_content` trees
 * (services/source_merge.merge_sources) — everything else on a preview is
 * something only the reader that produced it could have measured (a brand
 * candidate, the crawl frontier that powers "Crawl more", Facebook facts), so
 * picking between two readers' worth of those is a frontend policy, same
 * split `sourcePaste.withPastedContent` already draws for a paste's `base`.
 *
 * URL outranks Document here: it measured a live rendered layout, so its
 * brand detection is trusted first, and only it can have a crawl frontier or
 * Facebook facts in the first place.
 */

import type { ScrapePreview } from '@/lib/types'

export function combineSourcePreviews(
  url: ScrapePreview,
  doc: ScrapePreview,
  merged: ScrapePreview,
): ScrapePreview {
  return {
    ...url,
    source_content: merged.source_content,
    discovered_count: merged.discovered_count,
    image_candidates: [...url.image_candidates, ...doc.image_candidates],
    brand_candidate: url.brand_candidate ?? doc.brand_candidate,
  }
}
