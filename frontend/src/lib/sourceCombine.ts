/**
 * Combining two reads' previews — a website, its Facebook Page, a document —
 * once both have landed.
 *
 * The backend's `/api/source/merge` only joins the two `source_content` trees
 * (services/source_merge.merge_sources, which also reports the facts they
 * disagree on as `source_conflicts`). Everything else on a preview is something
 * only the reader that produced it could have measured (a brand candidate, the
 * crawl frontier that powers "Crawl more", Facebook facts), so picking between
 * two readers' worth of those is a frontend policy, the same split
 * `sourcePaste.withPastedContent` draws for a paste's `base`.
 *
 * The BASE outranks the addition: it is read first (a website before its Page,
 * either before a document), its brand detection is trusted first, and only it
 * can have a crawl frontier. What the base lacks, the addition fills —
 * a Page's facts and brand mark ride along when the base is a website.
 */

import type { ScrapePreview } from '@/lib/types'

export function combineSourcePreviews(
  base: ScrapePreview,
  addition: ScrapePreview,
  merged: ScrapePreview,
): ScrapePreview {
  return {
    ...base,
    source_content: merged.source_content,
    discovered_count: merged.discovered_count,
    image_candidates: [...base.image_candidates, ...addition.image_candidates],
    brand_candidate: base.brand_candidate ?? addition.brand_candidate,
    facebook_facts: base.facebook_facts ?? addition.facebook_facts,
  }
}
