import type { FacebookFacts, FacebookPhotoInventory, PageKnowledge } from '@/lib/types'

/**
 * What we actually read off the Page — and the promise that nothing else will
 * be invented.
 *
 * This panel exists because the user never explicitly chose "Facebook mode":
 * they pasted a link and we routed it. Auto-detection is only safe if the
 * result is legible, so this is where they see exactly what grounds the site
 * *before* any LLM time is spent, and where a partial read earns the token
 * rather than demanding it up front.
 */

interface Props {
  facts: FacebookFacts
  knowledge?: PageKnowledge | null
  photos?: FacebookPhotoInventory
}

const REJECTION_LABELS: Record<string, string> = {
  duplicate: 'duplicates',
  low_quality: 'too small',
  privacy_risk: 'privacy risk',
  poster_or_text_graphic: 'posters or text graphics',
}

/** "17 usable of 24 · 5 duplicates · 2 too small". */
function photoSummary(photos: FacebookPhotoInventory): string {
  const rejected = Object.entries(photos.rejected_summary)
    .filter(([, count]) => count > 0)
    .map(([reason, count]) => `${count} ${REJECTION_LABELS[reason] ?? reason.replace(/_/g, ' ')}`)
  return [`${photos.usable} usable of ${photos.found}`, ...rejected].join(' · ')
}

interface Fact {
  label: string
  value: string | null
}

/** Distinct photos the read kept: attachment originals, the legacy
 * full-picture fallback, and the cover. Mirrors `FacebookPage.post_photo_urls`. */
function photoCount(facts: FacebookFacts): number {
  const urls = new Set<string>()
  for (const post of facts.posts ?? []) {
    for (const photo of post.photos ?? []) urls.add(photo.url)
    if (post.image_url) urls.add(post.image_url)
  }
  if (facts.cover_photo_url) urls.add(facts.cover_photo_url)
  return urls.size
}

function hostOf(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, '')
  } catch {
    return url
  }
}

function factList(facts: FacebookFacts): Fact[] {
  const hours = facts.hours ?? []
  const captioned = (facts.posts ?? []).filter((p) => p.message?.trim()).length
  const reviews = facts.reviews ?? []
  const photos = photoCount(facts)
  const categories = facts.categories?.length ? facts.categories : facts.category ? [facts.category] : []

  return [
    { label: 'Name', value: facts.name || null },
    { label: 'Category', value: categories.join(' · ') || null },
    {
      label: 'About',
      value: facts.about || facts.description
        ? `${(facts.about || facts.description || '').slice(0, 80)}…`
        : null,
    },
    { label: 'Phone', value: facts.phone || null },
    { label: 'Email', value: facts.emails?.[0] || null },
    {
      label: 'Address',
      value:
        facts.single_line_address ||
        [facts.street, facts.city, facts.state].filter(Boolean).join(', ') ||
        null,
    },
    { label: 'Service area', value: facts.service_area || null },
    { label: 'Website', value: facts.website || null },
    { label: 'Other profiles', value: (facts.social_profiles ?? []).map(hostOf).join(', ') || null },
    {
      label: 'Opening hours',
      value: hours.length ? `${hours.length} day${hours.length === 1 ? '' : 's'}` : null,
    },
    { label: 'Service options', value: (facts.service_options ?? []).join(' · ') || null },
    { label: 'Price range', value: facts.price_range || null },
    { label: 'Photos', value: photos ? `${photos}` : null },
    { label: 'Posts', value: captioned ? `${captioned}` : null },
    { label: 'Recommendations', value: reviews.length ? `${reviews.length}` : null },
    { label: 'Profile picture', value: facts.profile_picture_url ? 'Found' : null },
  ]
}

const MISSING_LABELS: Record<string, string> = {
  contact: 'phone, email, address and opening hours',
  profile: 'mission, products and founded date',
  posts: 'recent posts and their photos',
  reviews: 'customer recommendations',
}

function UnderstandingPanel({ knowledge, photos }: { knowledge?: PageKnowledge | null; photos?: FacebookPhotoInventory }) {
  const offerings = knowledge?.offerings ?? []
  const facilities = knowledge?.facilities ?? []
  const stale = knowledge?.stale ?? []
  if (!offerings.length && !facilities.length && !stale.length && !photos) return null

  return (
    <div className="rounded-xl border border-line bg-surface p-3 text-xs">
      <h3 className="font-semibold text-ink-soft">What we understood</h3>
      {offerings.length > 0 && (
        <div className="mt-2">
          <div className="font-medium text-ink">Offerings</div>
          <ul className="mt-1 space-y-0.5">
            {offerings.map((offering) => (
              <li key={offering.name} className="flex items-baseline gap-1.5">
                <span aria-hidden className="text-emerald-600">✓</span>
                <span className="text-ink">{offering.name}</span>
                <span className="text-ink-faint">{offering.kind}</span>
                {offering.link && (
                  <a href={offering.link} target="_blank" rel="noreferrer" className="text-ink-muted underline">
                    source post{offering.published_at ? ` · ${offering.published_at.slice(0, 10)}` : ''}
                  </a>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {facilities.length > 0 && (
        <p className="mt-2 text-ink-muted">
          <span className="font-medium text-ink">Facilities:</span> {facilities.map((f) => f.name).join(', ')}
        </p>
      )}
      {photos && (
        <p className="mt-2 text-ink-muted">
          <span className="font-medium text-ink">Photos:</span> {photoSummary(photos)}
        </p>
      )}
      {stale.length > 0 && (
        <div className="mt-2 text-ink-muted">
          <span className="font-medium text-ink">Left out as no longer current:</span>{' '}
          {stale.map((item) => (item.date ? `${item.label} (${item.date})` : item.label)).join('; ')}
        </div>
      )}
      {knowledge?.structured_by === 'none' && (
        <p className="mt-2 text-ink-faint">
          Offerings weren't worked out for this read, so the site is planned from the facts above.
        </p>
      )}
    </div>
  )
}

export function FacebookFactsPanel({ facts, knowledge, photos }: Props) {
  const items = factList(facts)
  const found = items.filter((f) => f.value)
  const missing = items.filter((f) => !f.value)
  const gaps = (facts.missing_fields ?? [])
    .map((key) => MISSING_LABELS[key])
    .filter(Boolean)

  return (
    <div className="space-y-3">
      <div className="rounded-xl border border-line bg-surface p-3">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-xs font-semibold text-ink-soft">Facts found on this Page</h3>
          <span className="text-[11px] text-ink-faint">
            {found.length} of {items.length}
          </span>
        </div>

        <ul className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-2">
          {found.map((f) => (
            <li key={f.label} className="flex items-baseline gap-1.5 text-xs">
              <span aria-hidden className="text-emerald-600">
                ✓
              </span>
              <span className="font-medium text-ink">{f.label}</span>
              <span className="truncate text-ink-muted">{f.value}</span>
            </li>
          ))}
          {missing.map((f) => (
            <li key={f.label} className="flex items-baseline gap-1.5 text-xs text-ink-faint">
              <span aria-hidden>—</span>
              <span>{f.label}</span>
              <span>not on the Page</span>
            </li>
          ))}
        </ul>

        <p className="mt-2.5 border-t border-line pt-2 text-[11px] text-ink-muted">
          The site is written from these facts and nothing else. Sections without
          anything to say — an empty hours list, no recommendations — are left out
          rather than filled in. A Page rarely states services with prices or the
          business's story: paste them into <span className="font-medium">Paste your
          content</span> above and they join these facts.
        </p>
      </div>

      <UnderstandingPanel knowledge={knowledge} photos={photos} />

      {facts.partial && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <div className="font-semibold">
            {facts.fetched_via === 'render'
              ? 'Read from the public Page'
              : facts.fetched_via === 'render_session'
                ? 'Read while signed in'
                : 'Some details were out of reach'}
          </div>
          <p className="mt-0.5">
            {gaps.length > 0 ? (
              <>We couldn't read {gaps.join(', ')}. </>
            ) : (
              <>Some of this Page's details weren't readable. </>
            )}
            {/* One remedy, because only one works. Signing in was offered
              * here and it reads LESS of a Page that loads logged out — the
              * logged-out view is the one Facebook serves in full — so it is a
              * fallback for a Page that won't load at all, never a way to fill
              * a gap in a read that already succeeded. */}
            <>
              A Page access token — from a Page you administer — fills those in.{' '}
            </>
            You can also carry on with what's here; the site just won't mention
            what's missing.
          </p>
        </div>
      )}
    </div>
  )
}
