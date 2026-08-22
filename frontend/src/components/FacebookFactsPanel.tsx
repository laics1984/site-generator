import type { FacebookFacts } from '@/lib/types'

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
  /** Rendered inside the token expander, when the read came up short. */
  onRetryWithToken?: (token: string) => void
}

interface Fact {
  label: string
  value: string | null
}

function factList(facts: FacebookFacts): Fact[] {
  const hours = facts.hours ?? []
  const posts = facts.posts ?? []
  const reviews = facts.reviews ?? []
  const photos = posts.filter((p) => p.image_url).length + (facts.cover_photo_url ? 1 : 0)

  return [
    { label: 'Name', value: facts.name || null },
    { label: 'Category', value: facts.category || null },
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
    { label: 'Website', value: facts.website || null },
    {
      label: 'Opening hours',
      value: hours.length ? `${hours.length} day${hours.length === 1 ? '' : 's'}` : null,
    },
    { label: 'Photos', value: photos ? `${photos}` : null },
    { label: 'Posts', value: posts.length ? `${posts.length}` : null },
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

export function FacebookFactsPanel({ facts }: Props) {
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
          rather than filled in.
        </p>
      </div>

      {facts.partial && (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
          <div className="font-semibold">
            {facts.fetched_via === 'render'
              ? 'Read from the public Page'
              : 'Some details were out of reach'}
          </div>
          <p className="mt-0.5">
            {gaps.length > 0 ? (
              <>We couldn't read {gaps.join(', ')}. </>
            ) : (
              <>Some of this Page's details weren't readable. </>
            )}
            A Page access token — from a Page you administer — fills those in. You can
            also carry on with what's here; the site just won't mention what's missing.
          </p>
        </div>
      )}
    </div>
  )
}
