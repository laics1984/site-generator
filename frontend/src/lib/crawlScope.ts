import type { SitemapProbeResult } from '@/lib/types'

/**
 * How many pages a "Full crawl" fetches: the whole sitemap, bounded by the
 * backend's ceiling (`CRAWL_MAX_PAGES_CEILING`, returned by the probe).
 *
 * One definition for the button that describes the scope and the handler that
 * starts the crawl — they used to each spell the ceiling as a literal 40.
 */
export function fullCrawlCap(probe: SitemapProbeResult): number {
  return Math.min(probe.total_urls, probe.crawl_page_ceiling)
}
