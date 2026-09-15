import { useSyncExternalStore } from 'react'

import type { LlmChoice, LlmModelsResponse } from '@/lib/types'

/**
 * The model picker's selection, shared by the header menu and the one request
 * helper in lib/api.ts, which sends it on every call as two headers. The backend
 * reads them into the request's model choice (services/llm_choice.py), so every
 * AI-backed endpoint — paste preview, page recipe, generation — follows the menu
 * without any of those call sites knowing it exists.
 *
 * `null` until the backend's menu has been read and the remembered choice
 * checked against it: until then no headers are sent, which the backend treats
 * as local for both roles. That keeps a stale remembered id (a model since
 * removed, or a key since unset) from turning the first requests into 400s.
 */

export const CONTENT_HEADER = 'X-Webtree-Llm-Content'
export const REASONING_HEADER = 'X-Webtree-Llm-Reasoning'

const STORAGE_KEY = 'webtree.llmChoice'

let current: LlmChoice | null = null
const listeners = new Set<() => void>()

function emit() {
  for (const listener of listeners) listener()
}

function remembered(): Partial<LlmChoice> {
  // Storage can be missing or throw (private windows, blocked site data).
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    return raw ? (JSON.parse(raw) as Partial<LlmChoice>) : {}
  } catch {
    return {}
  }
}

function remember(choice: LlmChoice) {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(choice))
  } catch {
    // Not remembered across reloads; the choice still applies to this session.
  }
}

/** Headers for a request: empty until the menu has been adopted. */
export function llmChoiceHeaders(): Record<string, string> {
  if (!current) return {}
  return { [CONTENT_HEADER]: current.content, [REASONING_HEADER]: current.reasoning }
}

/** Check the remembered choice against the backend's menu, per role: a model
 * the menu no longer lists, or one it lists as unavailable, falls back to the
 * backend's default for that role. */
export function adoptLlmMenu(menu: LlmModelsResponse): LlmChoice {
  const usable = new Set(menu.choices.filter((c) => c.available).map((c) => c.id))
  const saved = remembered()
  const pick = (role: keyof LlmChoice) => {
    const id = saved[role]
    return typeof id === 'string' && usable.has(id) ? id : menu.default[role]
  }
  current = { content: pick('content'), reasoning: pick('reasoning') }
  emit()
  return current
}

export function setLlmChoice(next: LlmChoice) {
  current = next
  remember(next)
  emit()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function useLlmChoice(): LlmChoice | null {
  return useSyncExternalStore(subscribe, () => current)
}
