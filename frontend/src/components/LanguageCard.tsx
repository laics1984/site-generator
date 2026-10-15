import type { LanguagePattern, LanguageProfile } from '@/lib/types'

/** What each layout means for the owner, in their words rather than ours. */
const PATTERN_LABELS: Record<LanguagePattern, string> = {
  monolingual: 'One language',
  parallel_urls: 'Separate pages per language',
  inline_bilingual: 'Bilingual on the same pages',
  mixed_sections: 'Two languages, different sections',
  incidental: 'One language (a little of another)',
  unknown: 'Language not detected',
}

/** The source's detected languages, layout and the one-line reason
 * (services/language_analysis.py). Read-only: generation does not use it yet. */
export function LanguageCard({ profile }: { profile: LanguageProfile }) {
  const shown = profile.languages.filter((l) => l.substantial)
  const minor = profile.languages.filter((l) => !l.substantial)
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3 text-xs text-slate-700">
      <div className="flex items-baseline justify-between gap-2">
        <div className="font-semibold uppercase tracking-wider text-slate-500">Languages</div>
        <div className="text-[11px] font-medium text-slate-600">
          {PATTERN_LABELS[profile.pattern]}
        </div>
      </div>
      {shown.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {shown.map((l) => (
            <span
              key={l.code}
              lang={l.code}
              className={`rounded-full border px-2 py-0.5 ${
                l.code === profile.primary
                  ? 'border-slate-900 bg-slate-900 text-white'
                  : 'border-slate-300 bg-slate-50 text-slate-800'
              }`}
              title={`${l.code} · confidence ${l.confidence} · on ${Math.round(l.coverage * 100)}% of pages`}
            >
              {l.label || l.code} {Math.round(l.share * 100)}%
            </span>
          ))}
          {minor.length > 0 && (
            <span className="px-1 py-0.5 text-slate-500">
              + a little {minor.map((l) => l.label || l.code).join(', ')}
            </span>
          )}
        </div>
      )}
      {profile.reason && <div className="mt-2 text-slate-800">{profile.reason}</div>}
      {profile.evidence.length > 0 && (
        <details className="mt-1.5">
          <summary className="cursor-pointer text-[11px] text-slate-500">Why</summary>
          <ul className="mt-1 space-y-0.5 text-[11px] text-slate-600">
            {profile.evidence.map((line) => (
              <li key={line}>• {line}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  )
}
