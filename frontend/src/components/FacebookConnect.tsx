import { useCallback, useEffect, useState } from 'react'

import { disconnectFacebookSession, getFacebookSession } from '@/lib/api'
import type { FacebookSession } from '@/lib/types'
import { Button, Spinner } from '@/ui'

const COMMAND = './dev.sh fb-login'
const POLL_MS = 2000

/**
 * The signed-in Facebook session: whether one is connected, and how to get one.
 *
 * The login itself happens in a real browser window on the operator's machine,
 * because the backend runs in a container with no display. So this panel does
 * two things and no more: it asks for the one action only a human at a terminal
 * can take, and it reports the outcome once `./dev.sh fb-login` POSTs the
 * captured session to the backend. It deliberately does **not** narrate the
 * login in progress — the script prints its own progress to the terminal the
 * operator just typed into, and a second copy of that story here would be a
 * channel with nothing behind it, kept true by hand.
 *
 * **A spinner has to be backed by evidence.** The idle state used to render one
 * over "Waiting — this updates on its own.", which asserted that a login was
 * under way before the operator had run anything at all: no window had opened,
 * nothing was pending, and since nothing on this side can observe the script,
 * the claim was indistinguishable from a hang. It read as "the app is doing
 * something, sit tight" and so the command below it never got run. Idle now
 * reads as the instruction it is; the only motion left in the panel marks work
 * that genuinely exists — the first status fetch, and the Disconnect call.
 *
 * The component owns all of its own state deliberately. The session is
 * server-side and shared by every read, so there is nothing for App.tsx to
 * hold, thread through props or keep in sync.
 */
function useFacebookSession() {
  const [session, setSession] = useState<FacebookSession | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [disconnecting, setDisconnecting] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setSession(await getFacebookSession())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    }
  }, [])

  // Polling is DERIVED from the answer, not managed alongside it: it runs
  // exactly while there is still a change to notice — an unknown state, or a
  // session that isn't connected yet on a build where the feature is on. That
  // is what makes "Disconnect starts watching again" fall out for free instead
  // of needing a flag reset, which the imperative version got wrong.
  //
  // The other half of the bound is the caller's: SourcePanel mounts this only
  // while its disclosure is open, because `<details>` keeps collapsed children
  // mounted — so an earlier claim that polling stopped when the expander closed
  // was never what the code did, and typing a Facebook URL left a 2-second poll
  // running behind a shut panel for the rest of the session.
  const watching = session === null || (session.enabled && !session.connected)

  useEffect(() => {
    if (!watching) return
    let cancelled = false
    let timer: ReturnType<typeof setTimeout> | undefined

    async function tick() {
      // Nobody is looking at a hidden tab. Skip the request and let the next
      // tick pick it up when the tab comes back; browsers throttle background
      // timers anyway, so this states the intent rather than relying on them.
      if (!document.hidden) await refresh()
      if (cancelled) return
      timer = setTimeout(tick, POLL_MS)
    }
    void tick()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [watching, refresh])

  const disconnect = useCallback(async () => {
    setDisconnecting(true)
    try {
      // Dropping the session flips `watching` back on, which restarts the poll
      // in case they sign in again.
      setSession(await disconnectFacebookSession())
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setDisconnecting(false)
    }
  }, [])

  return { session, error, disconnect, disconnecting }
}

export function FacebookConnect({ disabled = false }: { disabled?: boolean }) {
  const { session, error, disconnect, disconnecting } = useFacebookSession()
  const [copied, setCopied] = useState(false)

  async function copy() {
    try {
      await navigator.clipboard.writeText(COMMAND)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // Clipboard access can be refused; the command is on screen to select.
    }
  }

  // The one honest spinner in the idle half of this panel: a request really is
  // in flight, and it resolves in milliseconds.
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
        <span className="font-semibold text-ink-soft">Your turn:</span> run this
        once in your terminal. Signing in needs a real browser window on your
        own machine, which this app has no way to open for you.
      </p>
      <div className="mt-1.5 flex items-center gap-2">
        <code className="flex-1 select-all rounded-lg bg-surface-sunken px-2 py-1.5 font-mono text-xs text-ink">
          {COMMAND}
        </code>
        <Button variant="secondary" size="sm" onClick={copy} disabled={disabled}>
          {copied ? 'Copied' : 'Copy'}
        </Button>
      </div>
      <p className="mt-1.5 text-[11px] text-ink-faint">
        A Chromium window will open — sign in as normal and it closes itself.
        This panel then says “Signed in” on its own; nothing to refresh.
      </p>
      <p className="mt-1.5 text-[11px] text-ink-faint">
        Worth knowing: a signed-in read is a <em>fallback</em>, not an upgrade.
        Facebook serves the full Page — name, About, contact, profile picture —
        to a logged-out visitor and a bare app shell to a signed-in one, so we
        always read logged out first and only fall back to your session for a
        Page that refuses to load that way.
      </p>
      {error && (
        <p className="mt-1.5 text-[11px] text-amber-700">
          Couldn't reach the backend: {error}
        </p>
      )}
    </div>
  )
}
