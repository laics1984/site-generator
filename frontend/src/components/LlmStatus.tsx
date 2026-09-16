import clsx from 'clsx'
import { useEffect, useRef, useState } from 'react'

import { checkLlmHealth, checkPexelsHealth, fetchLlmModels } from '@/lib/api'
import type { LlmRoleHealth } from '@/lib/api'
import { adoptLlmMenu, setLlmChoice, useLlmChoice } from '@/lib/llmChoice'
import type { LlmChoice, LlmModelOption } from '@/lib/types'
import { Select } from '@/ui'

interface PexelsState {
  configured: boolean
  hint?: string
}

interface HealthState {
  content: LlmRoleHealth
  reasoning?: LlmRoleHealth
}

const ROLES: { key: keyof LlmChoice; label: string; description: string }[] = [
  {
    key: 'content',
    label: 'Page content',
    description: 'Writes the copy for every page, plus paste structure, bios and translations.',
  },
  {
    key: 'reasoning',
    label: 'Brand & design decisions',
    description: 'Detects the brand, picks the design direction and judges image matches.',
  },
]

/** The header's model menu: which model each AI role uses, and whether it is
 * reachable. The choice applies to every request from here on (lib/llmChoice.ts). */
export function LlmStatus() {
  const [menu, setMenu] = useState<LlmModelOption[] | null>(null)
  const [menuError, setMenuError] = useState<string | null>(null)
  const [health, setHealth] = useState<HealthState | null>(null)
  const [pexels, setPexels] = useState<PexelsState | null>(null)
  const choice = useLlmChoice()

  useEffect(() => {
    let cancelled = false
    fetchLlmModels()
      .then((res) => {
        if (cancelled) return
        adoptLlmMenu(res)
        setMenu(res.choices)
      })
      .catch((err: unknown) => {
        if (!cancelled) setMenuError(err instanceof Error ? err.message : 'Backend unreachable')
      })
    checkPexelsHealth()
      .then((res) => {
        if (!cancelled) setPexels({ configured: res.status === 'configured', hint: res.hint })
      })
      .catch(() => {
        if (!cancelled) setPexels({ configured: false })
      })
    return () => {
      cancelled = true
    }
  }, [])

  // Re-probe whenever the choice changes: health answers for the choice the
  // request carries, so it always describes what a generation would use.
  useEffect(() => {
    if (!choice) return
    let cancelled = false
    setHealth(null)
    checkLlmHealth()
      .then(({ reasoning, ...content }) => {
        if (!cancelled) setHealth({ content, reasoning })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setHealth({
          content: {
            status: 'unreachable',
            error: err instanceof Error ? err.message : 'Backend unreachable',
          },
        })
      })
    return () => {
      cancelled = true
    }
  }, [choice])

  return (
    <div className="flex items-center gap-3 text-xs">
      <ModelMenu
        menu={menu}
        menuError={menuError}
        choice={choice}
        health={health}
      />
      <span className="text-slate-300">·</span>
      <PexelsBadge state={pexels} />
    </div>
  )
}

function ModelMenu({
  menu,
  menuError,
  choice,
  health,
}: {
  menu: LlmModelOption[] | null
  menuError: string | null
  choice: LlmChoice | null
  health: HealthState | null
}) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: MouseEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('mousedown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (menuError) {
    return (
      <span className="max-w-[26rem] truncate font-medium text-rose-600" title={menuError}>
        Backend unreachable — {menuError}
      </span>
    )
  }
  if (!menu || !choice) return <span className="text-slate-500">Checking models…</span>

  const byId = new Map(menu.map((option) => [option.id, option]))
  const roleHealth = (key: keyof LlmChoice) => (key === 'content' ? health?.content : health?.reasoning)
  const failing = ROLES.filter(({ key }) => {
    const status = roleHealth(key)?.status
    return status !== undefined && status !== 'ok'
  })
  const summary = (key: keyof LlmChoice) => {
    const id = choice[key]
    if (id === 'local') return roleHealth(key)?.model || 'Local model'
    return (byId.get(id)?.label ?? id).replace(/^Claude /, '')
  }
  const sameModel = choice.content === choice.reasoning
  const tone = !health ? 'pending' : failing.length ? 'bad' : 'ok'

  return (
    <div ref={wrapRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={failing.map(({ key }) => roleHealth(key)?.hint || roleHealth(key)?.error).filter(Boolean).join('\n\n') || undefined}
        className={clsx(
          'flex max-w-[11rem] items-center gap-1.5 rounded-lg border px-2.5 py-1 font-medium transition sm:max-w-[22rem]',
          tone === 'bad'
            ? 'border-rose-200 bg-rose-50 text-rose-700 hover:border-rose-300'
            : 'border-line bg-surface text-ink-soft hover:border-line-strong hover:text-ink',
        )}
      >
        <span
          aria-hidden="true"
          className={clsx(
            'h-1.5 w-1.5 shrink-0 rounded-full',
            tone === 'ok' && 'bg-emerald-500',
            tone === 'bad' && 'bg-rose-500',
            tone === 'pending' && 'bg-slate-300',
          )}
        />
        <span className="truncate">
          {sameModel ? summary('content') : `${summary('content')} · ${summary('reasoning')}`}
        </span>
        <svg viewBox="0 0 16 16" className="h-3 w-3 shrink-0" fill="currentColor" aria-hidden="true">
          <path d="M4 6l4 4 4-4H4z" />
        </svg>
      </button>

      {open && (
        <div
          role="dialog"
          aria-label="AI models"
          className="absolute right-0 z-30 mt-1.5 w-80 max-w-[calc(100vw-2rem)] animate-slide-up space-y-4 rounded-xl border border-line bg-surface p-4 text-left shadow-raised"
        >
          {ROLES.map(({ key, label, description }) => {
            const selected = byId.get(choice[key])
            const status = roleHealth(key)
            return (
              <div key={key}>
                <label className="block">
                  <span className="text-xs font-semibold text-ink-soft">{label}</span>
                  <span className="mt-0.5 block text-[11px] text-ink-faint">{description}</span>
                  <Select
                    className="mt-1.5"
                    value={choice[key]}
                    onChange={(e) => setLlmChoice({ ...choice, [key]: e.target.value })}
                  >
                    {menu.map((option) => (
                      <option key={option.id} value={option.id} disabled={!option.available}>
                        {option.label}
                        {option.available ? '' : ' — needs API key'}
                      </option>
                    ))}
                  </Select>
                </label>
                {selected && <p className="mt-1 text-[11px] text-ink-muted">{selected.note}</p>}
                <RoleStatus status={status} />
              </div>
            )
          })}

          {menu.some((option) => !option.available && option.hint) && (
            <p className="rounded-lg border border-amber-200 bg-amber-50 px-2.5 py-2 text-[11px] text-amber-800">
              {menu.find((option) => !option.available && option.hint)?.hint}
            </p>
          )}
          {(choice.content !== 'local' || choice.reasoning !== 'local') && (
            <p className="text-[11px] text-ink-muted">
              Claude models send the source content — page text, documents, and the
              people named on them — to Anthropic, and each generation is billed to
              your API key.
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function RoleStatus({ status }: { status: LlmRoleHealth | undefined }) {
  if (!status) return <p className="mt-1 text-[11px] text-ink-faint">Checking…</p>
  if (status.status === 'ok') {
    return (
      <p className="mt-1 text-[11px] text-emerald-700">
        Ready{status.provider === 'local' && status.model ? ` — serving ${status.model}` : ''}
      </p>
    )
  }
  return (
    <p className="mt-1 text-[11px] text-rose-700" title={status.error || undefined}>
      {status.provider === 'anthropic' ? 'Claude API unavailable' : 'AI server unreachable'}
      {status.hint || status.error ? ` — ${status.hint || status.error}` : ''}
    </p>
  )
}

function PexelsBadge({ state }: { state: PexelsState | null }) {
  if (!state) return <span className="text-slate-500">…</span>
  if (state.configured) {
    return <span className="font-medium text-emerald-600">Pexels OK</span>
  }
  return (
    <span
      className="font-medium text-amber-600"
      title={state.hint || 'Set PEXELS_API_KEY for real photos'}
    >
      Pexels off · using Picsum
    </span>
  )
}
