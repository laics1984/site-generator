import { useEffect, useState } from 'react'

import { checkLlmHealth, checkPexelsHealth } from '@/lib/api'

interface LlmState {
  ok: boolean
  model: string // what the AI server is actually serving
  models: string[]
  error?: string
}

interface PexelsState {
  configured: boolean
  hint?: string
}

export function LlmStatus() {
  const [llm, setLlm] = useState<LlmState | null>(null)
  const [pexels, setPexels] = useState<PexelsState | null>(null)

  useEffect(() => {
    let cancelled = false
    // Single probe. Which engine is behind the URL is the ai-server's business,
    // so we show the MODEL — the thing that actually changes what you get.
    Promise.all([checkLlmHealth(), checkPexelsHealth()])
      .then(([llmRes, pRes]) => {
        if (cancelled) return
        setLlm({
          ok: llmRes.status === 'ok',
          model: llmRes.model ?? 'no model loaded',
          models: llmRes.models ?? [],
          error: llmRes.error,
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
          model: 'unknown',
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
  if (!state.ok) {
    return (
      <span className="font-medium text-rose-600">
        AI server unreachable{state.error ? ` — ${state.error}` : ''}
      </span>
    )
  }
  return (
    <span
      className="font-medium text-emerald-600"
      title={state.models.length > 1 ? `Also available: ${state.models.join(', ')}` : undefined}
    >
      {state.model}
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
