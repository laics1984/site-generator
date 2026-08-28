import { useCallback, useEffect, useRef, useState } from 'react'

import { disconnectFacebookSession, getFacebookSession } from '@/lib/api'
import type { FacebookSession } from '@/lib/types'
import { Button, Spinner } from '@/ui'

const COMMAND = './dev.sh fb-login'
const POLL_MS = 2000

/**
 * The signed-in Facebook session: show whether one is connected, and how to get
 * one.
 *
 * The login itself happens in a real browser window on the operator's machine,
 * because the backend runs in a container with no display. So this component
 * hands over a command and then watches for the result rather than driving
 * anything — `./dev.sh fb-login` POSTs the captured session to the backend, and
 * the next poll here flips the panel to "connected" on its own. No refresh, no
 * "I've done it" button to press.
 *
 * It owns all of its own state deliberately. The session is server-side and
 * shared by every read, so there is nothing for App.tsx to hold, thread through
 * props, or keep in sync — which is what keeps this a self-contained addition to
 * the source panel rather than a new axis running through the whole flow.
 *
 * Polling runs ONLY while the panel is mounted and not yet connected: the
 * expander this sits inside is closed most of the time, and there is nothing to
 * watch for once the answer is yes.
 */
export function FacebookConnect({ disabled = false }: { disabled?: boolean }) {
  const [session, setSession] = useState<FacebookSession | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [copied, setCopied] = useState(false)
  const [disconnecting, setDisconnecting] = useState(false)
  // Survives re-renders so the poll effect doesn't restart on every tick.
  const stopped = useRef(false)

  const refresh = useCallback(async () => {
    try {
      const next = await getFacebookSession()
      setSession(next)
      setError(null)
      return next
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
      return null
    }
  }, [])

  useEffect(() => {
    stopped.current = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function tick() {
      const next = await refresh()
      if (stopped.current) return
      // Nothing left to watch for once it's connected or the feature is off.
      if (next?.connected || next?.enabled === false) return
      timer = setTimeout(tick, POLL_MS)
    }
    void tick()

    return () => {
      stopped.current = true
      if (timer) clearTimeout(timer)
    }
  }, [refresh])

  async function copy() {
    try {
      await navigator.clipboard.writeText(COMMAND)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access can be refused; the command is on screen to select.
    }
  }

  async function disconnect() {
    setDisconnecting(true)
    try {
      setSession(await disconnectFacebookSession())
      // Dropping the session re-opens the "not connected" state, which is worth
      // watching again in case they sign back in.
      stopped.current = false
      void refresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setDisconnecting(false)
    }
  }

  if (session === null && error === null) {
    return (
      <p className="mt-2 flex items-center gap-2 text-xs text-ink-faint">
        <Spinner className="h-3 w-3" /> Checking…
      </p>
    )
  }

  // Turned off in config — say so rather than offering a button that can't work.
  if (session && !session.enabled) {
    return (
      <p className="mt-2 text-xs text-ink-muted">
        Signed-in reads are turned off (<code>FACEBOOK_SESSION_ENABLED=false</code>).
      </p>
    )
  }

  if (session?.connected) {
    const days = session.expires_in_days
    return (
      <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="text-xs font-semibold text-emerald-700">
          ✓ Signed in{session.label ? ` as ${session.label}` : ''}
        </span>
        {days !== null && (
          <span className="text-[11px] text-ink-faint">
            · good for about {days} {days === 1 ? 'day' : 'days'}
          </span>
        )}
        <Button
          variant="ghost"
          size="sm"
          onClick={disconnect}
          busy={disconnecting}
          disabled={disabled || disconnecting}
          className="ml-auto"
        >
          Disconnect
        </Button>
      </div>
    )
  }

  return (
    <div className="mt-2">
      <p className="text-xs text-ink-muted">
        Run this once in your terminal. A browser window opens — sign in to
        Facebook as normal and it closes itself.
      </p>
      <div className="mt-1.5 flex items-center gap-2">
        <code className="flex-1 select-all rounded-lg bg-surface-sunken px-2 py-1.5 font-mono text-xs text-ink">
          {COMMAND}
        </code>
        <Button variant="secondary" size="sm" onClick={copy} disabled={disabled}>
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-ink-faint">
        <Spinner className="h-3 w-3" /> Waiting — this updates on its own.
      </p>
      {error && (
        <p className="mt-1.5 text-[11px] text-amber-700">
          Couldn't reach the backend: {error}
        </p>
      )}
    </div>
  )
}
