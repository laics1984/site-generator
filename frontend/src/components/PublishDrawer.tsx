import { useEffect, useRef, useState } from 'react'

import {
  listCmsTargets,
  readLivePalette,
  publishPalette,
  planCmsPush,
  pushToCms,
  testCmsConnection,
  type CmsLogin,
} from '@/lib/api'
import type {
  CmsPushReport,
  CmsSite,
  CmsSyncPlan,
  CmsTarget,
  GeneratedSite,
} from '@/lib/types'
import {
  Banner,
  Button,
  Card,
  Checkbox,
  Drawer,
  Field,
  Input,
  Segmented,
  Select,
} from '@/ui'

import { PushPlanCard } from './PushPlanCard'
import { PushReportView } from './PushReportView'

interface PublishDrawerProps {
  open: boolean
  onClose: () => void
  site: GeneratedSite
  paletteOnly?: boolean
  onPublished?: () => void
}

/** Update a site the account already has, or create a new one. */
type Mode = 'update' | 'create'

/** "Local" / "Production" is derived from where the bytes go, never configured:
 * `is_remote` is read off the target's host (backend services/cms_targets.py),
 * and the host itself stays visible beside it so the word can't go stale. */
function targetKind(target: CmsTarget): string {
  return target.is_remote ? 'Production' : 'Local'
}

function hostOf(url: string): string {
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

function siteLabel(site: CmsSite): string {
  const address = site.entity_url || site.public_url
  return address ? `${site.entity_name} — ${hostOf(address)}` : site.entity_name
}

/**
 * "Publish to webtree" — the handoff out of the generator and into the webtree
 * CMS / admin suite.
 *
 * Flow: pick the CMS (local or production, when both are configured) → connect
 * with the account → choose a site to update, or name a new one → push. For an
 * update the backend first plans the sync (which pages are updated in place,
 * added, archived) and the plan is shown before the button is enabled, so the
 * operator confirms a concrete list rather than a mode. The push itself runs
 * synchronously on the backend and never throws: the returned PushReport
 * carries per-step state, which is what PushReportView renders.
 *
 * Lives in a drawer rather than the old 240px sidebar because it is a
 * credential form with a destination choice and a plan — it needs the width,
 * and it should not push the preview off screen to get it.
 */
export function PublishDrawer({ open, onClose, site, onPublished, paletteOnly = false }: PublishDrawerProps) {
  const operation = site.generation_mode === 'two_page_preview'
    ? 'preview' : site.generation_mode === 'remaining' ? 'extend' : 'sync'
  // Which CMS this lands in. The backend owns the list; an install that never
  // configured a second one gets exactly one and no picker is rendered.
  const [targets, setTargets] = useState<CmsTarget[]>([])
  const [targetName, setTargetName] = useState<string | null>(null)
  const [login, setLogin] = useState<CmsLogin>({ email: '', password: '' })
  // The account's sites once Connect succeeds; null = not connected.
  const [sites, setSites] = useState<CmsSite[] | null>(null)
  const [sitesError, setSitesError] = useState<string | null>(null)
  const [mode, setMode] = useState<Mode>(!paletteOnly && operation === 'preview' ? 'create' : 'update')
  // The site to update — picked from the list, or typed when there is no list.
  const [siteToken, setSiteToken] = useState('')
  const [newEntity, setNewEntity] = useState({
    name: site.site_name ?? '',
    url: '',
  })
  const [livePalette, setLivePalette] = useState<Awaited<ReturnType<typeof readLivePalette>> | null>(null)
  const [paletteNotice, setPaletteNotice] = useState<string | null>(null)
  const [publication, setPublication] = useState<Awaited<ReturnType<typeof readLivePalette>>['publication']>()
  const [verifying, setVerifying] = useState(false)
  const [contextAttempt, setContextAttempt] = useState(0)
  const [plan, setPlan] = useState<CmsSyncPlan | null>(null)
  const [planBusy, setPlanBusy] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)
  // An update replaces a live site, so it goes live; see switchMode.
  const [publish, setPublish] = useState(true)
  const [pushBuilderStyles, setPushBuilderStyles] = useState(true)
  const [pushFavicon, setPushFavicon] = useState(true)
  // Opt-in and per destination: a template is the owner's article and event
  // design, so a choice made for one site must not carry over to the next.
  const [replaceTemplates, setReplaceTemplates] = useState(false)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [report, setReport] = useState<CmsPushReport | null>(null)

  // Every imperative answer (connect, push) is checked against the destination
  // it was asked for: switching CMS, mode or site mid-flight must not let a
  // stale result land against the new one. One counter, bumped on every change
  // of destination, instead of a ref per field. The plan is an effect and
  // cancels itself.
  const epoch = useRef(0)
  function invalidate(): number {
    epoch.current += 1
    setPaletteNotice(null)
    setPublication(undefined)
    setVerifying(false)
    return epoch.current
  }

  useEffect(() => {
    let cancelled = false
    listCmsTargets()
      .then((list) => {
        if (cancelled || list.length === 0) return
        setTargets(list)
        setTargetName(list.find((item) => item.name === site.cms_target_name)?.name ?? list[0].name)
      })
      // Degrade to today's behaviour: no picker, no `target` on the wire, and
      // the backend uses its default. Not worth an error banner — the push
      // still works, and the operator has nothing to act on.
      .catch(() => undefined)
    return () => {
      cancelled = true
    }
  }, [site.cms_target_name])

  const target = targets.find((t) => t.name === targetName) ?? null
  const connected = sites !== null
  const chosenSite = sites?.find((s) => s.entity_api_token === siteToken) ?? null

  /** A prior connection no longer describes where this push is going. */
  function disconnect() {
    invalidate()
    setSites(null)
    setSitesError(null)
    setSiteToken('')
    setReport(null)
    setError(null)
  }

  function switchTarget(name: string) {
    setTargetName(name)
    // An account and its sites belong to ONE CMS — they mean nothing on another.
    disconnect()
  }

  function editLogin(next: CmsLogin) {
    setLogin(next)
    // Changed credentials are a different account until proven otherwise.
    if (connected) disconnect()
  }

  function switchMode(next: Mode) {
    if (operation === 'extend' && next !== 'update') return
    setMode(next)
    invalidate()
    setReport(null)
    setError(null)
    setReplaceTemplates(false)
    // An update replaces a live site, so it goes live; a new site lands as
    // drafts to review first. A preset, not a lock — the checkbox stays.
    setPublish(operation !== 'sync' || next === 'update')
  }

  function chooseSite(token: string) {
    setSiteToken(token)
    invalidate()
    setReport(null)
    setError(null)
    setReplaceTemplates(false)
  }

  // The plan follows the destination: whenever a connected account has a site
  // chosen in update mode, ask the backend what the push would do to it.
  useEffect(() => {
    if (!connected || mode !== 'update' || !siteToken) {
      setPlan(null)
      setPlanError(null)
      setPlanBusy(false)
      return
    }
    let cancelled = false
    if (paletteOnly) {
      setPaletteNotice(null); setPublication(undefined)
      setLivePalette(null); setPlanError(null); setPlanBusy(true)
      readLivePalette(login, siteToken, targetName ?? undefined).then(res => {
        if (!cancelled) {
          if (res.builder_styles?.colorSystemVersion !== 2) setPlanError("This live site uses older colors. Its existing design will be kept.")
          else setLivePalette(res)
        }
      }).catch(err => { if (!cancelled) setPlanError(String(err.message || err)) })
        .finally(() => { if (!cancelled) setPlanBusy(false) })
      return () => { cancelled = true }
    }
    setPlan(null)
    setPlanError(null)
    setPlanBusy(true)
    planCmsPush({
      site, login, entityToken: siteToken, target: targetName ?? undefined,
      operation, generationProjectId: site.generation_project_id ?? undefined,
    })
      .then((res) => {
        if (!cancelled) setPlan(res)
      })
      .catch((err) => {
        if (!cancelled) {
          setPlanError(err instanceof Error ? err.message : 'Could not compare pages')
        }
      })
      .finally(() => {
        if (!cancelled) setPlanBusy(false)
      })
    return () => {
      cancelled = true
    }
  }, [connected, mode, siteToken, targetName, site, login, operation, paletteOnly, contextAttempt])

  async function handleConnect() {
    const requested = invalidate()
    setBusy(true)
    setError(null)
    setSites(null)
    setSiteToken('')
    try {
      const res = await testCmsConnection(login, targetName ?? undefined)
      if (epoch.current !== requested) return
      setSites(res.sites)
      setSitesError(res.sites_error ?? null)
      const boundSite = operation === 'extend'
        ? res.sites.find((item) => item.entity_id === site.cms_entity_id)
        : null
      if (boundSite) {
        setSiteToken(boundSite.entity_api_token)
      } else if (operation === 'extend') {
        setError('The account does not have access to the CMS site used for this preview.')
      } else if (res.sites.length === 1 && (paletteOnly || operation !== 'preview')) {
        // One site: nothing to choose, so don't ask.
        setSiteToken(res.sites[0].entity_api_token)
      } else if (!paletteOnly && res.sites.length === 0 && !res.sites_error) {
        // Nothing to update yet — the only thing this account can do is create.
        switchMode('create')
      }
    } catch (err) {
      if (epoch.current !== requested) return
      setError(err instanceof Error ? err.message : 'Connection failed')
    } finally {
      setBusy(false)
    }
  }

  async function handlePush() {
    const requested = epoch.current
    setBusy(true)
    setError(null)
    setReport(null)
    try {
      if (paletteOnly) {
        if (!livePalette?.layout.versionId) throw new Error("Read the live palette before publishing.")
        const result = await publishPalette(site, login, siteToken, livePalette.layout.versionId, targetName ?? undefined)
        if (epoch.current !== requested) return
        setPaletteNotice(result.warning || "Colors published. Cached public pages will update as their caches refresh.")
        setLivePalette(null); onPublished?.(); return
      }
      const res = await pushToCms({
        site,
        login,
        entityToken: mode === 'update' ? siteToken : undefined,
        publish,
        pushBuilderStyles,
        pushFavicon,
        replaceTemplates: mode === 'update' && replaceTemplates,
        createEntity: mode === 'create',
        newEntityName: mode === 'create' ? newEntity.name.trim() : undefined,
        newEntityUrl:
          mode === 'create' && newEntity.url.trim() ? newEntity.url.trim() : undefined,
        target: targetName ?? undefined,
        operation,
        generationProjectId: site.generation_project_id ?? undefined,
      })
      // A report that landed after the operator changed destination would read
      // as a report about the new one.
      if (epoch.current !== requested) return
      setReport(res)
      if (!res.success) {
        setError(res.error || 'Push failed; see step results below.')
      } else {
        onPublished?.()
      }
    } catch (err) {
      if (epoch.current !== requested) return
      setError(err instanceof Error ? err.message : 'Push failed')
    } finally {
      setBusy(false)
    }
  }

  async function verifyPublication() {
    const requested = epoch.current
    const colors = site.builder_styles?.colors
    if (!colors) return
    setVerifying(true)
    try {
      const result = await readLivePalette(login, siteToken, targetName ?? undefined, {
        primary: colors.primary, secondary: colors.secondary, accent: colors.accent, surface: colors.surface,
      })
      if (epoch.current === requested) setPublication(result.publication)
    } catch {
      if (epoch.current === requested) setPublication({ state: 'unavailable', message: 'The public result could not be checked. Try again shortly.' })
    } finally {
      if (epoch.current === requested) setVerifying(false)
    }
  }

  const loginComplete = Boolean(login.email.trim() && login.password.trim())
  const canPush =
    connected &&
    !busy &&
    (mode === 'create'
      ? newEntity.name.trim().length > 0
      : Boolean(siteToken && (paletteOnly ? livePalette?.layout.versionId : plan && (operation !== 'extend' || chosenSite?.entity_id === site.cms_entity_id))))

  const pageCount = site.pages.length
  const pagesWord = `${pageCount} page${pageCount === 1 ? '' : 's'}`
  const whereWord = targets.length > 1 && target ? ` to ${targetKind(target)}` : ''
  const pushLabel =
    paletteOnly ? `Publish colors to ${chosenSite?.entity_name ?? 'site'}` : mode === 'create'
      ? `Create site & push ${pagesWord}${whereWord}`
      : operation === 'extend'
        ? `Add ${pagesWord} to ${chosenSite?.entity_name ?? 'the preview site'}${whereWord}`
        : `Update ${chosenSite?.entity_name ?? 'site'} · ${pagesWord}${whereWord}`

  return (
    <Drawer
      open={open}
      onClose={onClose}
      title={paletteOnly ? "Publish colors only" : "Publish to webtree"}
      description={paletteOnly ? 'Updates the color palette on an existing site.' : operation === 'extend'
        ? 'Adds the remaining pages to the existing preview site.'
        : 'Sends pages, header, footer, theme and images to a webtree site.'}
      widthClassName="max-w-xl"
    >
      <div className="space-y-4">
        <Card title="1 · Destination" description="Which CMS, and whose account.">
          <div className="space-y-3">
            {targets.length > 1 && (
              <Field
                label="CMS"
                // The hosts stay in view beside the words, so "Local" and
                // "Production" can never quietly stop meaning where bytes go.
                hint={
                  targets.map((t) => `${targetKind(t)} is ${t.label}`).join(' · ') +
                  '. Accounts are per-CMS — switching disconnects.'
                }
              >
                <Segmented
                  ariaLabel="CMS to push into"
                  className="w-full"
                  value={targetName ?? targets[0].name}
                  onChange={switchTarget}
                  options={targets.map((t) => ({
                    value: t.name,
                    label: targetKind(t),
                    title: t.api_base_url,
                  }))}
                />
              </Field>
            )}
            {target?.is_remote && (
              <Banner tone="warn" title="This is a live CMS">
                {paletteOnly ? 'Color changes' : 'Pages, images and theme'} go straight to <strong>{target.label}</strong>.
                Changes published here appear on the live website.
              </Banner>
            )}
            <Field label="Email">
              <Input
                type="email"
                autoComplete="username"
                value={login.email}
                onChange={(e) => editLogin({ ...login, email: e.target.value })}
              />
            </Field>
            <Field label="Password">
              <Input
                type="password"
                autoComplete="current-password"
                value={login.password}
                onChange={(e) => editLogin({ ...login, password: e.target.value })}
              />
            </Field>
            <div className="flex flex-wrap items-center gap-2">
              <Button
                onClick={handleConnect}
                disabled={!loginComplete || busy}
                busy={busy && !connected}
              >
                {connected ? 'Reconnect' : 'Connect'}
              </Button>
              {connected && (
                <span className="inline-flex items-center rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-800">
                  Connected · {sites.length} site{sites.length === 1 ? '' : 's'}
                </span>
              )}
            </div>
            {sitesError && (
              <Banner tone="warn" title="Couldn't list your sites">
                {sitesError} You can still update a site by pasting its entity API token
                below.
              </Banner>
            )}
          </div>
        </Card>

        <Card
          title="2 · Site"
          description={
            connected
              ? paletteOnly ? 'Choose a site made with the new color system.' : 'Update one of your sites, or create a new one.'
              : 'Connect first to choose a site.'
          }
        >
          <div className="space-y-3">
            <Segmented
              ariaLabel="Update or create"
              className="w-full"
              value={mode}
              onChange={switchMode}
              options={[
                { value: 'update', label: 'Update existing site' },
                ...(operation === 'extend' || paletteOnly ? [] : [{ value: 'create' as Mode, label: 'Create new site' }]),
              ]}
            />

            {mode === 'update' ? (
              <>
                {sites && sites.length > 0 ? (
                  <Field label="Site">
                    <Select
                      value={siteToken}
                      onChange={(e) => chooseSite(e.target.value)}
                      disabled={!connected || busy}
                    >
                      <option value="">Choose a site…</option>
                      {sites.map((s) => (
                        <option key={s.entity_api_token} value={s.entity_api_token} disabled={!paletteOnly && operation === 'extend' && s.entity_id !== site.cms_entity_id}>
                          {siteLabel(s)}
                        </option>
                      ))}
                    </Select>
                  </Field>
                ) : (
                  <Field
                    label="Entity API token"
                    hint="Found in the webtree admin suite under the site's settings."
                  >
                    <Input
                      type="text"
                      value={siteToken}
                      onChange={(e) => chooseSite(e.target.value.trim())}
                      placeholder="e.g. abcd1234ef56…"
                      className="font-mono"
                      disabled={!connected || busy}
                    />
                  </Field>
                )}

                {chosenSite && (
                  <div className="flex items-center gap-2.5 text-xs text-ink-muted">
                    {chosenSite.favicon_url && (
                      <img
                        src={chosenSite.favicon_url}
                        alt=""
                        className="h-5 w-5 rounded border border-line bg-surface object-contain"
                      />
                    )}
                    {(chosenSite.public_url || chosenSite.entity_url) && (
                      <a
                        href={chosenSite.public_url || chosenSite.entity_url || undefined}
                        target="_blank"
                        rel="noreferrer"
                        className="truncate underline decoration-line-strong underline-offset-2 hover:text-ink"
                      >
                        {hostOf(chosenSite.public_url || chosenSite.entity_url || '')}
                      </a>
                    )}
                    <span className="rounded-full bg-surface-sunken px-2 py-0.5 capitalize">
                      {chosenSite.role}
                    </span>
                  </div>
                )}

                {connected && siteToken && (
                  <PushPlanCard
                    plan={plan}
                    busy={planBusy}
                    error={planError}
                    publish={publish}
                    replaceTemplates={replaceTemplates}
                    operation={operation}
                  />
                )}
              </>
            ) : (
              <div className="space-y-3 rounded-xl border border-brand-100 bg-brand-50/60 p-3">
                <p className="text-xs text-brand-900">
                  {operation === 'preview'
                    ? 'A new preview site is created with exactly two published pages and the full source menu.'
                    : 'A new site is created under your account and everything is pushed into it — pages, theme, and any migrated articles and events.'}
                </p>
                <Field label="Site name">
                  <Input
                    type="text"
                    value={newEntity.name}
                    onChange={(e) => setNewEntity({ ...newEntity, name: e.target.value })}
                    placeholder="e.g. Acme Studios"
                  />
                </Field>
                {operation !== 'preview' && <Field
                  label="Website URL"
                  optional
                  hint="Must be unique across the whole CMS. Leave blank if unsure — you can set it later."
                >
                  <Input
                    type="url"
                    value={newEntity.url}
                    onChange={(e) => setNewEntity({ ...newEntity, url: e.target.value })}
                    placeholder="https://example.com"
                  />
                </Field>}
              </div>
            )}
          </div>
        </Card>

        {!paletteOnly && <Card title="3 · Options">
          <div className="space-y-2.5">
            {operation !== 'extend' && <Checkbox
              checked={pushBuilderStyles}
              onChange={(e) => setPushBuilderStyles(e.target.checked)}
              label="Apply theme"
              description="Colours, fonts and button radius, written to the site's builder styles."
            />}
            {operation !== 'extend' && <Checkbox
              checked={pushFavicon}
              onChange={(e) => setPushFavicon(e.target.checked)}
              label="Set site icon"
              description="The source site's favicon, shown in browser tabs and beside the site name in search results."
            />}
            <Checkbox
              checked={operation === 'sync' ? publish : true}
              onChange={(e) => setPublish(e.target.checked)}
              disabled={operation !== 'sync'}
              label="Publish immediately"
              description={
                mode === 'update'
                  ? 'The updated pages replace the live ones now. Otherwise they land as drafts you review in the admin suite first.'
                  : 'Otherwise the pages land as drafts you can review in the admin suite first.'
              }
            />
            {/* Only when the chosen site has templates: with none there is
                nothing to replace, and a control that does nothing is noise. */}
            {operation === 'sync' && mode === 'update' && (plan?.template_pages.length ?? 0) > 0 && (
              <Checkbox
                checked={replaceTemplates}
                onChange={(e) => setReplaceTemplates(e.target.checked)}
                label="Replace article & event templates"
                description="Reset the site's article and event page layouts to the new design. They land as drafts: open each in the builder, which lays it out afresh, then publish it. Until then the live site keeps its current templates."
              />
            )}
            {operation === 'sync' && mode === 'update' && !publish && (
              <Banner tone="info" title="Drafts still change part of the live site">
                The header, footer, menus and theme go live as soon as they are saved — the
                CMS keeps no draft of the site-wide layout. Page bodies land as drafts, and
                pages not in the new site stay live until you publish.
              </Banner>
            )}
          </div>
        </Card>}

        {paletteOnly && livePalette && <Card title="Review colors">
          <div className="grid grid-cols-4 gap-2">{(["primary", "secondary", "accent", "surface"] as const).map(key => <div key={key} className="text-xs capitalize">{key}<div className="mt-1 flex h-8 overflow-hidden rounded border border-line"><span className="w-1/2" title={`Live: ${livePalette.builder_styles.colors[key]}`} style={{ background: livePalette.builder_styles.colors[key] }} /><span className="w-1/2" title={`Selected: ${site.builder_styles?.colors[key]}`} style={{ background: site.builder_styles?.colors[key] }} /></div></div>)}</div>
          <Button onClick={() => setContextAttempt(n => n+1)} disabled={busy}>Refresh live colors</Button>
          <p className="mt-2 text-xs text-ink-muted">Live colors are on the left; your selected colors are on the right. Public caches refresh on their existing schedule.</p>
        </Card>}
        {paletteNotice && <Banner tone="info" title="Palette saved">
          <p>{paletteNotice}</p>
          <Button onClick={verifyPublication} busy={verifying} disabled={verifying} className="mt-2">Check public colors</Button>
          <Button onClick={() => setContextAttempt(n => n+1)} disabled={busy || verifying} className="ml-2 mt-2">Refresh live colors</Button>
          {publication && <p className="mt-2" role="status">{publication.message} {publication.public_url && <a href={publication.public_url} target="_blank" rel="noreferrer" className="underline">Open website</a>}</p>}
        </Banner>}

        <Button
          variant="primary"
          size="lg"
          fullWidth
          onClick={handlePush}
          disabled={!canPush}
          busy={busy && connected}
        >
          {pushLabel}
        </Button>

        {error && (
          <Banner tone="danger" title="Push problem">
            {error}
          </Banner>
        )}

        {report && <PushReportView report={report} site={site} mode={mode} />}
      </div>
    </Drawer>
  )
}
