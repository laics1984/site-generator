import clsx from 'clsx'

import { PERSONALITY_AXES } from '@/lib/brandBrief'
import type { BrandBrief } from '@/lib/brandBrief'

const MOOD_BASIS_LABEL: Record<NonNullable<BrandBrief['moodBasis']>, string> = {
  personality: 'read from its personality',
  detected: 'as detected',
  industry: 'industry default',
}

/** One axis as a five-step scale between its poles. A filled dot is evidenced
 * (hover shows the source words); a hollow one is an inference. */
function AxisRow({ left, right, value, evidence }: {
  left: string
  right: string
  value: number
  evidence: string | null
}) {
  return (
    <div className="flex items-center gap-2" title={evidence ? `“${evidence}”` : 'Inferred — no quote in the source'}>
      <span className="w-24 text-right text-ink-muted">{left}</span>
      <span className="flex gap-1" aria-label={`${left} to ${right}: ${value + 3} of 5`}>
        {[-2, -1, 0, 1, 2].map((step) => (
          <span
            key={step}
            className={clsx(
              'h-2.5 w-2.5 rounded-full border',
              step === value
                ? evidence ? 'border-brand-600 bg-brand-600' : 'border-brand-600 bg-white'
                : 'border-line bg-line',
            )}
          />
        ))}
      </span>
      <span className="w-24 text-ink-muted">{right}</span>
    </div>
  )
}

/**
 * "What we understood" — the brand brief the design is built from, with facts,
 * inferences and unknowns labelled as such, so a wrong reading is caught here
 * rather than in the finished site.
 */
export function BrandBriefCard({ brief }: { brief: BrandBrief }) {
  const axes = PERSONALITY_AXES.filter(([axis]) => brief.personality[axis])
  return (
    <section className="rounded-xl border border-line bg-surface p-3 text-xs text-ink">
      <div className="font-semibold">What we understood</div>

      {(brief.positioning || brief.audience || brief.mood) && (
        <dl className="mt-2 grid grid-cols-[auto,1fr] gap-x-3 gap-y-1">
          {brief.positioning && (
            <>
              <dt className="text-ink-muted">Positioning</dt>
              <dd>{brief.positioning} <span className="text-ink-muted">(inferred)</span></dd>
            </>
          )}
          {brief.audience && (
            <>
              <dt className="text-ink-muted">Audience</dt>
              <dd>{brief.audience} <span className="text-ink-muted">(inferred)</span></dd>
            </>
          )}
          {brief.mood && (
            <>
              <dt className="text-ink-muted">Mood</dt>
              <dd>
                {brief.mood}
                {brief.moodBasis && <span className="text-ink-muted"> — {MOOD_BASIS_LABEL[brief.moodBasis]}</span>}
              </dd>
            </>
          )}
        </dl>
      )}

      {brief.differentiators.length > 0 && (
        <div className="mt-3">
          <div className="text-ink-muted">Stated by the source</div>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {brief.differentiators.map((claim) => (
              <li key={claim}>{claim}</li>
            ))}
          </ul>
        </div>
      )}

      {axes.length > 0 && (
        <div className="mt-3 space-y-1">
          <div className="text-ink-muted">Personality</div>
          {axes.map(([axis, left, right]) => {
            const r = brief.personality[axis]!
            return <AxisRow key={axis} left={left} right={right} value={r.value} evidence={r.evidence} />
          })}
          <div className="pt-1 text-ink-faint">● quoted from the source · ○ inferred</div>
        </div>
      )}

      {brief.unknowns.length > 0 && (
        <div className="mt-3 text-ink-muted">
          Not stated in the source: {brief.unknowns.join(', ')}. The site won’t invent these.
        </div>
      )}
    </section>
  )
}
