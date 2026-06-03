import { useState } from 'react'

import {
  pushToCms,
  testCmsConnection,
  type CmsCredentials,
} from '@/lib/api'
import type { CmsPushReport, GeneratedSite } from '@/lib/types'

interface CmsPushPanelProps {
  site: GeneratedSite
}

/**
 * "Push to webtree" panel — shown alongside a generated site.
 *
 * Flow: user enters CMS creds → Test connection → if entity is empty, show
 * the Push button. The push runs synchronously on the backend and we render
 * the returned PushReport as a per-step status list.
 */
export function CmsPushPanel({ site }: CmsPushPanelProps) {
  const [creds, setCreds] = useState<CmsCredentials>({
    email: '',
    password: '',
    entityToken: '',
  })
  // 'existing' → push into the entity named by the token; 'new' → create one.
  const [entityMode, setEntityMode] = useState<'existing' | 'new'>('existing')
  const [newEntity, setNewEntity] = useState({
    name: site.site_name ?? '',
    url: '',
  })
  const [publish, setPublish] = useState(false)
  const [pushBuilderStyles, setPushBuilderStyles] = useState(true)
  const [forceOverwrite, setForceOverwrite] = useState(false)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [testResult, setTestResult] = useState<{
    ok: boolean
    existingCount: number
  } | null>(null)
  const [report, setReport] = useState<CmsPushReport | null>(null)

  // In create-new mode there is no token to validate against — send an empty
  // one so the backend's test-connection just verifies the login.
  const effectiveCreds: CmsCredentials =
    entityMode === 'new' ? { ...creds, entityToken: '' } : creds

  function switchMode(mode: 'existing' | 'new') {
    setEntityMode(mode)
    // A prior test/report no longer applies once the target changes.
    setTestResult(null)
    setReport(null)
    setError(null)
  }

  async function handleTest() {
    setBusy(true)
    setError(null)
    setTestResult(null)
    try {
      const res = await testCmsConnection(effectiveCreds)
      setTestResult({ ok: res.ok, existingCount: res.existing_page_count })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Connection test failed')
    } finally {
      setBusy(false)
    }
  }

  async function handlePush() {
    setBusy(true)
    setError(null)
    setReport(null)
    try {
      const res = await pushToCms({
        site,
        creds: effectiveCreds,
        publish,
        forceOverwrite,
        pushBuilderStyles,
        createEntity: entityMode === 'new',
        newEntityName:
          entityMode === 'new' ? newEntity.name.trim() : undefined,
        newEntityUrl:
          entityMode === 'new' && newEntity.url.trim()
            ? newEntity.url.trim()
            : undefined,
      })
      setReport(res)
      if (!res.success) {
        setError(res.error || 'Push failed; see step results below.')
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Push failed')
    } finally {
      setBusy(false)
    }
  }

  const baseCredsComplete = Boolean(creds.email.trim() && creds.password.trim())
  const credsComplete =
    entityMode === 'new'
      ? baseCredsComplete && Boolean(newEntity.name.trim())
      : baseCredsComplete && Boolean(creds.entityToken.trim())
  const canPush =
    entityMode === 'new'
      ? Boolean(credsComplete && testResult?.ok)
      : Boolean(
          credsComplete &&
            testResult?.ok &&
            (testResult.existingCount === 0 || forceOverwrite),
        )

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5">
      <div className="text-base font-semibold text-slate-900">
        Push to webtree
      </div>
      <p className="mt-0.5 text-xs text-slate-500">
        Sends pages, header, footer, theme, and uploaded images to a webtree
        entity — an existing empty one, or a brand-new entity created for you.
      </p>

      <div className="mt-4 space-y-3">
        <label className="block">
          <span className="text-xs font-medium text-slate-700">Email</span>
          <input
            type="email"
            autoComplete="username"
            value={creds.email}
            onChange={(e) => setCreds({ ...creds, email: e.target.value })}
            className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </label>
        <label className="block">
          <span className="text-xs font-medium text-slate-700">Password</span>
          <input
            type="password"
            autoComplete="current-password"
            value={creds.password}
            onChange={(e) => setCreds({ ...creds, password: e.target.value })}
            className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
          />
        </label>
        <div>
          <span className="text-xs font-medium text-slate-700">
            Destination entity
          </span>
          <div className="mt-1 grid grid-cols-2 gap-1 rounded-xl bg-slate-100 p-1">
            <button
              type="button"
              onClick={() => switchMode('existing')}
              className={
                'rounded-lg px-3 py-1.5 text-xs font-medium transition ' +
                (entityMode === 'existing'
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700')
              }
            >
              Existing entity
            </button>
            <button
              type="button"
              onClick={() => switchMode('new')}
              className={
                'rounded-lg px-3 py-1.5 text-xs font-medium transition ' +
                (entityMode === 'new'
                  ? 'bg-white text-slate-900 shadow-sm'
                  : 'text-slate-500 hover:text-slate-700')
              }
            >
              Create new entity
            </button>
          </div>
        </div>

        {entityMode === 'existing' ? (
          <label className="block">
            <span className="text-xs font-medium text-slate-700">
              Entity API token
            </span>
            <input
              type="text"
              value={creds.entityToken}
              onChange={(e) =>
                setCreds({ ...creds, entityToken: e.target.value })
              }
              placeholder="e.g. abcd1234ef56…"
              className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm font-mono shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
            />
          </label>
        ) : (
          <div className="space-y-3 rounded-xl border border-blue-100 bg-blue-50/50 p-3">
            <p className="text-xs text-blue-900">
              A new entity will be created under your account and the site
              pushed into it. The token is generated for you.
            </p>
            <label className="block">
              <span className="text-xs font-medium text-slate-700">
                Entity name
              </span>
              <input
                type="text"
                value={newEntity.name}
                onChange={(e) =>
                  setNewEntity({ ...newEntity, name: e.target.value })
                }
                placeholder="e.g. Acme Studios"
                className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
              />
            </label>
            <label className="block">
              <span className="text-xs font-medium text-slate-700">
                Website URL{' '}
                <span className="font-normal text-slate-400">(optional)</span>
              </span>
              <input
                type="url"
                value={newEntity.url}
                onChange={(e) =>
                  setNewEntity({ ...newEntity, url: e.target.value })
                }
                placeholder="https://example.com"
                className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
              />
            </label>
          </div>
        )}

        <div className="flex gap-2">
          <button
            type="button"
            onClick={handleTest}
            disabled={!credsComplete || busy}
            className="rounded-xl border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy && !report ? 'Testing…' : 'Test connection'}
          </button>
          {testResult && (
            <span
              className={
                'inline-flex items-center rounded-full px-3 py-1 text-xs font-medium ' +
                (testResult.ok
                  ? 'bg-emerald-50 text-emerald-800'
                  : 'bg-rose-50 text-rose-800')
              }
            >
              {testResult.ok
                ? entityMode === 'new'
                  ? 'Signed in · ready to create entity'
                  : `Connected · ${testResult.existingCount} existing page(s)`
                : 'Failed'}
            </span>
          )}
        </div>

        {testResult?.ok && testResult.existingCount > 0 && (
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
            <div className="font-semibold">Entity not empty</div>
            <div className="mt-1">
              This entity has {testResult.existingCount} page(s). Greenfield push
              is the default. Tick the override below if you really want to
              push into a non-empty entity (won't delete existing pages, will
              add yours alongside).
            </div>
            <label className="mt-2 flex cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={forceOverwrite}
                onChange={(e) => setForceOverwrite(e.target.checked)}
                className="h-4 w-4 rounded border-slate-300"
              />
              <span>I know what I'm doing — push anyway</span>
            </label>
          </div>
        )}

        <div className="space-y-1.5 rounded-xl border border-slate-200 bg-slate-50 p-3">
          <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-700">
            <input
              type="checkbox"
              checked={pushBuilderStyles}
              onChange={(e) => setPushBuilderStyles(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300"
            />
            <span>Apply theme (colours, fonts, button radius)</span>
          </label>
          <label className="flex cursor-pointer items-center gap-2 text-xs text-slate-700">
            <input
              type="checkbox"
              checked={publish}
              onChange={(e) => setPublish(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300"
            />
            <span>Publish immediately (otherwise pushed as drafts)</span>
          </label>
        </div>

        <button
          type="button"
          onClick={handlePush}
          disabled={!canPush || busy}
          className="w-full rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm transition hover:bg-blue-700 disabled:cursor-not-allowed disabled:bg-slate-300"
        >
          {busy && report === null
            ? entityMode === 'new'
              ? 'Creating & pushing…'
              : 'Pushing…'
            : `${entityMode === 'new' ? 'Create entity & push' : 'Push'} ${site.pages.length} page${site.pages.length === 1 ? '' : 's'}`}
        </button>

        {error && (
          <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
            {error}
          </div>
        )}

        {report && <PushSteps report={report} />}
      </div>
    </div>
  )
}

function PushSteps({ report }: { report: CmsPushReport }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white p-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        Push report
      </div>
      <ul className="mt-2 space-y-1.5">
        {report.steps.map((s, i) => (
          <li
            key={i}
            className={
              'flex items-start gap-2 rounded-lg border p-2 text-xs ' +
              (s.ok
                ? 'border-emerald-200 bg-emerald-50'
                : 'border-rose-200 bg-rose-50')
            }
          >
            <span
              className={
                'mt-0.5 inline-flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-bold text-white ' +
                (s.ok ? 'bg-emerald-600' : 'bg-rose-600')
              }
            >
              {s.ok ? '✓' : '!'}
            </span>
            <div className="min-w-0 flex-1">
              <div className="font-medium text-slate-900">{s.name}</div>
              <div className="text-slate-600">
                {s.ok ? s.detail || 'OK' : s.error || s.detail || 'Failed'}
              </div>
            </div>
          </li>
        ))}
      </ul>
      {report.success && (
        <div className="mt-3 rounded-lg bg-emerald-50 p-2 text-xs font-medium text-emerald-900">
          Push complete. {Object.keys(report.page_urls).length} page(s) live in
          the builder.
        </div>
      )}
    </div>
  )
}
