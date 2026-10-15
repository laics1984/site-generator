import { useEffect, useRef, useState } from 'react'
import { previewPalette, regeneratePalette } from '@/lib/api'
import type { CreativePalette, GeneratedSite, PalettePatch } from '@/lib/types'
import { Banner, Button, Input } from '@/ui'

const keys = ['primary', 'secondary', 'accent', 'surface'] as const
const colorsOf = (site: GeneratedSite): CreativePalette => {
  const c = site.builder_styles!.colors
  return { primary: c.primary, secondary: c.secondary, accent: c.accent, surface: c.surface }
}
const valid = (p: CreativePalette) => keys.every(k => /^#[0-9a-f]{6}$/i.test(p[k]))
const same = (a: CreativePalette, b: CreativePalette) => keys.every(k => a[k].toLowerCase() === b[k].toLowerCase())
const apply = (site: GeneratedSite, patch: PalettePatch): GeneratedSite => ({ ...site, ...patch })

export function PalettePanel({ site, onChange, onPublish }: {
  site: GeneratedSite; onChange: (site: GeneratedSite) => void; onPublish: () => void
}) {
  const saved = useRef(site)
  const current = useRef(site)
  current.current = site
  const epoch = useRef(0)
  const [draft, setDraft] = useState(() => colorsOf(site))
  const [direction, setDirection] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState('')
  const dirty = !same(draft, colorsOf(saved.current))
  useEffect(() => {
    saved.current = site
    setDraft(colorsOf(site))
    setNotice('')
    epoch.current++
    // Color patches preserve these page objects. A new generation resets the editor.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [site.pages])
  useEffect(() => {
    const requested = ++epoch.current
    if (!valid(draft) || same(draft, colorsOf(current.current))) return
    const timer = setTimeout(() => {
      previewPalette(current.current, draft).then(patch => {
        if (epoch.current === requested) { onChange(apply(current.current, patch)); setError(null) }
      }).catch(err => { if (epoch.current === requested) setError(String(err.message || err)) })
    }, 300)
    return () => clearTimeout(timer)
    // Parent callback changes with site; only edited colors schedule validation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft])
  async function regenerate() {
    const requested = ++epoch.current
    setBusy(true); setError(null); setNotice('')
    try {
      const patch = await regeneratePalette(current.current, direction)
      if (requested !== epoch.current) return
      const next = apply(current.current, patch)
      onChange(next); setDraft(colorsOf(next))
    } catch (err) { setError(err instanceof Error ? err.message : 'Color generation failed') }
    finally { setBusy(false) }
  }
  async function save() {
    ++epoch.current; setBusy(true); setError(null)
    try {
      const next = apply(current.current, await previewPalette(current.current, draft, true))
      saved.current = next; onChange(next); setDraft(colorsOf(next))
      setNotice('Palette saved to this preview. Publish colors to update an existing live site.')
    } catch (err) { setError(err instanceof Error ? err.message : 'Could not save colors') }
    finally { setBusy(false) }
  }
  function cancel() {
    epoch.current++; onChange(saved.current); setDraft(colorsOf(saved.current)); setError(null); setNotice('')
  }
  return <details className="mb-3 shrink-0 rounded-xl border border-line bg-surface p-3">
    <summary className="cursor-pointer text-sm font-semibold text-ink">Color palette <span className="ml-2 font-normal text-ink-muted">AI considers your business, audience and brand</span></summary>
    <div className="mt-3 space-y-3">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{keys.map(key => <label key={key} className="text-xs font-medium capitalize text-ink-soft">
        {key}<div className="mt-1 flex gap-1"><input type="color" aria-label={`${key} color`} disabled={busy} value={/^#[0-9a-f]{6}$/i.test(draft[key]) ? draft[key] : '#ffffff'} onChange={e => setDraft({ ...draft, [key]: e.target.value })} className="h-10 w-10 shrink-0 cursor-pointer rounded" />
        <Input aria-label={`${key} hex color`} value={draft[key]} disabled={busy} maxLength={7} onChange={e => setDraft({ ...draft, [key]: e.target.value })} /></div>
      </label>)}</div>
      <label className="block text-xs text-ink-soft">Design direction (optional)<Input value={direction} disabled={busy} maxLength={1000} onChange={e => setDirection(e.target.value)} placeholder="For example: warm, refined, earthy; keep our terracotta brand" /></label>
      <div className="flex flex-wrap gap-2">
        <Button onClick={regenerate} busy={busy} disabled={busy}>Regenerate colors with AI</Button>
        <Button variant="primary" onClick={save} disabled={busy || !valid(draft) || !dirty}>Save palette</Button>
        <Button onClick={cancel} disabled={busy || !dirty}>Cancel</Button>
        <Button onClick={onPublish} disabled={busy || dirty || !valid(draft)}>Publish colors only</Button>
      </div>
      {!valid(draft) && <p className="text-xs text-red-700">Enter six-digit hex colors, such as #2563eb.</p>}
      {error && <Banner tone="danger" title="Colors were kept">{error}</Banner>}
      {notice && <p className="text-xs text-ink-muted" role="status">{notice}</p>}
    </div>
  </details>
}
