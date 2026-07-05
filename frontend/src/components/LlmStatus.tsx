import { useEffect, useState } from 'react'

import { checkBackendHealth, checkLlmHealth, checkPexelsHealth } from '@/lib/api'

interface LlmState {
  ok: boolean
  backend: string // 'mlx' | 'ollama' — the active backend (from LLM_BACKEND)
  models: string[]
  error?: string
}

interface PexelsState {
  configured: boolean
  hint?: string
}

const BACKEND_LABEL: Record<string, string> = { mlx: 'MLX', ollama: 'Ollama' }

function backendLabel(backend: string): string {
  return BACKEND_LABEL[backend] ?? backend.toUpperCase()
}

export function LlmStatus() {
  const [llm, setLlm] = useState<LlmState | null>(null)
  const [pexels, setPexels] = useState<PexelsState | null>(null)

  useEffect(() => {
    let cancelled = false
    // First learn which backend is active (LLM_BACKEND), then probe that one's
    // server for reachability + loaded models.
    Promise.all([checkLlmHealth(), checkPexelsHealth()])
      .then(async ([llmRes, pRes]) => {
        if (cancelled) return
        const backend = llmRes.backend ?? 'ollama'
        const health = await checkBackendHealth(backend).catch((err: unknown) => ({
          status: 'unreachable',
          models: [] as string[],
          error: err instanceof Error ? err.message : 'unreachable',
        }))
        if (cancelled) return
        setLlm({
          ok: health.status === 'ok',
          backend,
          models: health.models ?? [],
          error: health.error,
        })
        setPexels({
          configured: pRes.status === 'configured',
          hint: pRes.hint,
        })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setLlm({
          ok: false,
          backend: 'ollama',
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
      <LlmBadge state={llm} />
      <span className="text-slate-300">·</span>
      <PexelsBadge state={pexels} />
    </div>
  )
}

function LlmBadge({ state }: { state: LlmState | null }) {
  if (!state) return <span className="text-slate-500">Checking LLM…</span>
  const label = backendLabel(state.backend)
  if (!state.ok) {
    return (
      <span className="font-medium text-rose-600">
        {label} unreachable{state.error ? ` — ${state.error}` : ''}
      </span>
    )
  }
  return (
    <span className="font-medium text-emerald-600">
      {label} OK · {state.models.length} model{state.models.length === 1 ? '' : 's'}
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
