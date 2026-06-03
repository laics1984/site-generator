import { useEffect, useState } from 'react'

import { checkOllamaHealth, checkPexelsHealth } from '@/lib/api'

interface OllamaState {
  ok: boolean
  models: string[]
  error?: string
}

interface PexelsState {
  configured: boolean
  hint?: string
}

export function OllamaStatus() {
  const [ollama, setOllama] = useState<OllamaState | null>(null)
  const [pexels, setPexels] = useState<PexelsState | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([checkOllamaHealth(), checkPexelsHealth()])
      .then(([oRes, pRes]) => {
        if (cancelled) return
        setOllama({
          ok: oRes.status === 'ok',
          models: oRes.models ?? [],
          error: oRes.error,
        })
        setPexels({
          configured: pRes.status === 'configured',
          hint: pRes.hint,
        })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setOllama({
          ok: false,
          models: [],
          error: err instanceof Error ? err.message : 'Backend unreachable',
        })
        setPexels({ configured: false })
      })
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <div className="flex items-center gap-3 text-xs">
      <OllamaBadge state={ollama} />
      <span className="text-slate-300">·</span>
      <PexelsBadge state={pexels} />
    </div>
  )
}

function OllamaBadge({ state }: { state: OllamaState | null }) {
  if (!state) return <span className="text-slate-500">Checking Ollama…</span>
  if (!state.ok) {
    return (
      <span className="font-medium text-rose-600">
        Ollama unreachable{state.error ? ` — ${state.error}` : ''}
      </span>
    )
  }
  return (
    <span className="font-medium text-emerald-600">
      Ollama OK · {state.models.length} model{state.models.length === 1 ? '' : 's'}
    </span>
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
