import { useState } from 'react'

import {
  pushToCms,
  testCmsConnection,
  type CmsCredentials,
} from '@/lib/api'
import { pagePath } from '@/lib/previewNav'
import type { CmsPushReport, GeneratedSite } from '@/lib/types'
import {
  Banner,
  Button,
  Card,
  Checkbox,
  Drawer,
  Field,
  Input,
  SectionLabel,
  Segmented,
} from '@/ui'

interface PublishDrawerProps {
  open: boolean
  onClose: () => void
  site: GeneratedSite
}

/**
 * "Publish to webtree" — the handoff out of the generator and into the webtree
 * CMS / admin suite.
 *
 * Flow: credentials → Test connection → pick or create the destination entity →
 * Push. The push runs synchronously on the backend and never throws: the
 * returned PushReport carries per-step state, which is what the step list at the
 * bottom renders.
 *
 * Lives in a drawer rather than the old 240px sidebar because it is a five-field
 * credential form with a destination choice — it needs the width, and it should
 * not push the preview off screen to get it.
 */
export function PublishDrawer({ open, onClose, site }: PublishDrawerProps) {
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
    <Drawer
      open={open}
      onClose={onClose}
      title="Publish to webtree"
      description="Sends pages, header, footer, theme and images to a webtree entity."
      widthClassName="max-w-xl"
    >
      <div className="space-y-4">
        <Card title="1 · Connect" description="Your webtree account credentials.">
          <div className="space-y-3">
            <Field label="Email">
              <Input
                type="email"
                autoComplete="username"
                value={creds.email}
                onChange={(e) => setCreds({ ...creds, email: e.target.value })}
              />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                autoComplete="current-password"
                value={creds.password}
                onChange={(e) => setCreds({ ...creds, password: e.target.value })}
              />
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button onClick={handleTest} disabled={!credsComplete} busy={busy && !report}>
                Test connection
              </Button>
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
          </div>
        </Card>

        <Card title="2 · Destination" description="Where this site should land.">
          <div className="space-y-3">
            <Segmented
              ariaLabel="Destination entity"
              className="w-full"
              value={entityMode}
              onChange={switchMode}
              options={[
                { value: 'existing', label: 'Existing entity' },
                { value: 'new', label: 'Create new entity' },
              ]}
            />

            {entityMode === 'existing' ? (
              <Field
                label="Entity API token"
                hint="Found in the webtree admin suite under the site's settings."
              >
                <Input
                  type="text"
                  value={creds.entityToken}
                  onChange={(e) => setCreds({ ...creds, entityToken: e.target.value })}
                  placeholder="e.g. abcd1234ef56…"
                  className="font-mono"
                />
              </Field>
            ) : (
              <div className="space-y-3 rounded-xl border border-brand-100 bg-brand-50/60 p-3">
                <p className="text-xs text-brand-900">
                  A new entity is created under your account and the site pushed into it.
                  The token is generated for you and shown after the push.
                </p>
                <Field label="Entity name">
                  <Input
                    type="text"
                    value={newEntity.name}
                    onChange={(e) => setNewEntity({ ...newEntity, name: e.target.value })}
                    placeholder="e.g. Acme Studios"
                  />
                </Field>
                <Field label="Website URL" optional>
                  <Input
                    type="url"
                    value={newEntity.url}
                    onChange={(e) => setNewEntity({ ...newEntity, url: e.target.value })}
                    placeholder="https://example.com"
                  />
                </Field>
              </div>
            )}

            {testResult?.ok && testResult.existingCount > 0 && entityMode === 'existing' && (
              <Banner tone="warn" title="Entity is not empty">
                This entity has {testResult.existingCount} page(s). A greenfield push is the
                default. Tick the override to push anyway — existing pages are kept and
                yours are added alongside.
                <Checkbox
                  className="mt-2"
                  checked={forceOverwrite}
                  onChange={(e) => setForceOverwrite(e.target.checked)}
                  label="I know what I'm doing — push anyway"
                />
              </Banner>
            )}
          </div>
        </Card>

        <Card title="3 · Options">
          <div className="space-y-2.5">
            <Checkbox
              checked={pushBuilderStyles}
              onChange={(e) => setPushBuilderStyles(e.target.checked)}
              label="Apply theme"
              description="Colours, fonts and button radius, written to the entity's builder styles."
            />
            <Checkbox
              checked={publish}
              onChange={(e) => setPublish(e.target.checked)}
              label="Publish immediately"
              description="Otherwise the pages land as drafts you can review in the admin suite first."
            />
          </div>
        </Card>

        <Button
          variant="primary"
          size="lg"
          fullWidth
          onClick={handlePush}
          disabled={!canPush}
          busy={busy && report === null && testResult !== null}
        >
          {entityMode === 'new' ? 'Create entity & push' : 'Push'} {site.pages.length} page
          {site.pages.length === 1 ? '' : 's'}
        </Button>

        {error && (
          <Banner tone="danger" title="Push problem">
            {error}
          </Banner>
        )}

        {report && <PushReportView report={report} site={site} />}
      </div>
    </Drawer>
  )
}

function PushReportView({ report, site }: { report: CmsPushReport; site: GeneratedSite }) {
  // The backend hands the generated token back on the create_entity step — this
  // is the only place the user can ever learn it.
  const createdToken = report.steps.find(
    (step) => step.name === 'create_entity' && step.ok,
  )?.data?.entity_token
  const pushedSlugs = Object.values(report.page_urls ?? {})

  return (
    <div className="space-y-3">
      {report.success && (
        <Banner tone="success" title="Push complete">
          {pushedSlugs.length} page{pushedSlugs.length === 1 ? '' : 's'} written to the
          builder{report.admin_url ? '.' : ' — open the admin suite to review them.'}
          {report.admin_url && (
            <a
              href={report.admin_url}
              target="_blank"
              rel="noreferrer"
              className="mt-2.5 inline-flex h-9 items-center gap-2 rounded-xl bg-emerald-700 px-3.5 text-sm font-semibold text-white transition hover:bg-emerald-800"
            >
              Open in webtree admin
              <svg viewBox="0 0 16 16" className="h-3.5 w-3.5" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
                <path d="M6 3h7v7M13 3L6.5 9.5" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M11 11.5V13H3V5h1.5" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </a>
          )}
        </Banner>
      )}

      {typeof createdToken === 'string' && createdToken.length > 0 && (
        <Card title="Your new entity token" padding="sm">
          <p className="text-xs text-ink-muted">
            Save this — it identifies the new site and is what you paste into "Existing
            entity" next time.
          </p>
          <div className="mt-2 flex items-center gap-2">
            <code className="min-w-0 flex-1 truncate rounded-lg bg-surface-sunken px-2.5 py-2 font-mono text-xs text-ink">
              {createdToken}
            </code>
            <Button
              size="sm"
              onClick={() => navigator.clipboard?.writeText(createdToken)}
            >
              Copy
            </Button>
          </div>
        </Card>
      )}

      <Card title="Push report" padding="sm">
        <ul className="space-y-1.5">
          {report.steps.map((step, i) => (
            <li
              key={i}
              className={
                'flex items-start gap-2 rounded-lg border p-2 text-xs ' +
                (step.ok ? 'border-emerald-200 bg-emerald-50' : 'border-rose-200 bg-rose-50')
              }
            >
              <span
                className={
                  'mt-0.5 inline-flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold text-white ' +
                  (step.ok ? 'bg-emerald-600' : 'bg-rose-600')
                }
              >
                {step.ok ? '✓' : '!'}
              </span>
              <div className="min-w-0 flex-1">
                <div className="font-medium text-ink">{step.name}</div>
                <div className="text-ink-muted">
                  {step.ok ? step.detail || 'OK' : step.error || step.detail || 'Failed'}
                </div>
              </div>
            </li>
          ))}
        </ul>

        {pushedSlugs.length > 0 && (
          <div className="mt-3">
            <SectionLabel>Pages written</SectionLabel>
            {/* page_urls maps pageId → slug, not a URL — so render paths, not links. */}
            <ul className="mt-1.5 flex flex-wrap gap-1.5">
              {site.pages.map((page) => (
                <li
                  key={page.slug || 'home'}
                  className="rounded-md bg-surface-sunken px-2 py-1 font-mono text-[11px] text-ink-soft"
                >
                  {pagePath(page)}
                </li>
              ))}
            </ul>
          </div>
        )}
      </Card>
    </div>
  )
}
