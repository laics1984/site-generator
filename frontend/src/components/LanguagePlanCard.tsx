import type { LocaleOption, MultilingualChoice, MultilingualPlan } from '@/lib/types'

/** The four choices, in the owner's words rather than SEO terms (plan Phase 12). */
const MODES: { value: MultilingualChoice['mode']; label: string; hint: string }[] = [
  { value: 'auto', label: 'Automatic', hint: 'Use the recommendation below.' },
  { value: 'bilingual', label: 'Both on the same page', hint: 'Each heading and paragraph shows both languages.' },
  { value: 'separate', label: 'Separate language pages', hint: 'Each language at its own address, e.g. /ms/about.' },
  { value: 'single', label: 'One language', hint: 'Only the main language.' },
]

function labelOf(code: string, locales: LocaleOption[]): string {
  return locales.find((l) => l.code === code)?.label ?? code
}

interface LanguagePlanCardProps {
  plan: MultilingualPlan
  choice: MultilingualChoice
  onChange: (choice: MultilingualChoice) => void
  locales: LocaleOption[]
  disabled?: boolean
}

/** How the site will present its languages, why, and the override
 * (services/multilingual_strategy.py). Changing it re-reads the page recipe,
 * so the page list below always matches the plan. */
export function LanguagePlanCard({ plan, choice, onChange, locales, disabled }: LanguagePlanCardProps) {
  const inPlan = new Set([plan.primary, ...plan.secondary])
  const addable = locales.filter((l) => !inPlan.has(l.code))
  const languages = [plan.primary, ...plan.secondary]
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-4 text-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="font-semibold text-slate-900">Languages</div>
        <div className="flex flex-wrap gap-1.5">
          {languages.map((code) => (
            <span
              key={code}
              lang={code}
              className={`rounded-full border px-2 py-0.5 text-xs ${
                code === plan.primary
                  ? 'border-slate-900 bg-slate-900 text-white'
                  : 'border-slate-300 bg-slate-50 text-slate-800'
              }`}
            >
              {labelOf(code, locales)}
              {plan.mode === 'separate' && code !== plan.primary && ` · /${plan.prefixes[code] ?? code}/`}
            </span>
          ))}
        </div>
      </div>
      <p className="mt-2 text-slate-700">{plan.reason}</p>
      {plan.uncertain && (
        <p className="mt-1 text-xs text-amber-800">
          The recommendation rests on thin evidence. Check the preview, or choose below.
        </p>
      )}
      <fieldset className="mt-3" disabled={disabled}>
        <legend className="sr-only">How to show the languages</legend>
        <div className="grid gap-2 sm:grid-cols-2">
          {MODES.map((mode) => (
            <label key={mode.value} className="flex cursor-pointer items-start gap-2 rounded-lg border border-slate-200 p-2 hover:bg-slate-50">
              <input
                type="radio"
                name="multilingual-mode"
                className="mt-0.5"
                checked={choice.mode === mode.value}
                onChange={() => onChange({ ...choice, mode: mode.value })}
              />
              <span>
                <span className="block font-medium text-slate-800">{mode.label}</span>
                <span className="block text-xs text-slate-500">{mode.hint}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>
      <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
        {choice.locales.map((code) => (
          <span key={code} className="inline-flex items-center gap-1 rounded-full border border-slate-300 px-2 py-0.5 text-slate-700">
            {labelOf(code, locales)} (added)
            <button
              type="button"
              aria-label={`Remove ${labelOf(code, locales)}`}
              disabled={disabled}
              className="text-slate-500 hover:text-slate-900"
              onClick={() => onChange({ ...choice, locales: choice.locales.filter((c) => c !== code) })}
            >
              ×
            </button>
          </span>
        ))}
        {addable.length > 0 && (
          <label className="inline-flex items-center gap-1 text-slate-600">
            Add a language
            <select
              value=""
              disabled={disabled}
              onChange={(e) => e.target.value && onChange({ ...choice, locales: [...choice.locales, e.target.value] })}
              className="rounded-lg border border-slate-300 bg-white px-2 py-1"
            >
              <option value="">Choose…</option>
              {addable.map((l) => (
                <option key={l.code} value={l.code}>{l.label}</option>
              ))}
            </select>
          </label>
        )}
      </div>
      {choice.locales.length > 0 && (
        <p className="mt-2 text-xs text-slate-500">
          Added languages are translated from the main-language copy and marked "AI translated" for review.
        </p>
      )}
    </div>
  )
}
