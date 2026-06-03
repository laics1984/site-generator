import { useRef, useState } from 'react'

import { extractBrandFromLogo } from '@/lib/api'
import type { BrandExtractionResult, BrandIdentity, BrandMood } from '@/lib/types'

const MOODS: { id: BrandMood; label: string; hint: string }[] = [
  { id: 'modern', label: 'Modern', hint: 'SaaS, fintech, tech' },
  { id: 'luxury', label: 'Luxury', hint: 'Hospitality, jewellery' },
  { id: 'friendly', label: 'Friendly', hint: 'Consumer, wellness' },
  { id: 'technical', label: 'Technical', hint: 'B2B, engineering' },
  { id: 'editorial', label: 'Editorial', hint: 'Media, agencies' },
  { id: 'playful', label: 'Playful', hint: 'Entertainment, food' },
]

interface BrandPanelProps {
  brandName: string
  onBrandNameChange: (value: string) => void
  brand: BrandIdentity | null
  setBrand: (brand: BrandIdentity | null) => void
  themePreview: BrandExtractionResult['theme_preview'] | null
  setThemePreview: (theme: BrandExtractionResult['theme_preview'] | null) => void
  googleFonts: string[]
  setGoogleFonts: (fonts: string[]) => void
  mood: BrandMood
  setMood: (mood: BrandMood) => void
}

export function BrandPanel({
  brandName,
  onBrandNameChange,
  brand,
  setBrand,
  themePreview,
  setThemePreview,
  setGoogleFonts,
  mood,
  setMood,
}: BrandPanelProps) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  async function handleLogoUpload(file: File) {
    setBusy(true)
    setError(null)
    try {
      const result = await extractBrandFromLogo(file, brandName, mood)
      setBrand(result.brand)
      setThemePreview(result.theme_preview)
      setGoogleFonts(result.google_fonts)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not extract logo')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4">
      <label className="block">
        <span className="text-sm font-medium text-slate-700">Brand name</span>
        <input
          type="text"
          value={brandName}
          onChange={(e) => onBrandNameChange(e.target.value)}
          placeholder="e.g. Acme Coffee"
          className="mt-1 block w-full rounded-xl border border-slate-200 bg-white px-4 py-2.5 text-sm shadow-sm focus:border-blue-500 focus:outline-none focus:ring-2 focus:ring-blue-100"
        />
      </label>

      <div>
        <div className="text-sm font-medium text-slate-700">Brand mood</div>
        <p className="mt-1 text-xs text-slate-500">
          Drives typography, button radius, and section rhythm. Pick the closest match.
        </p>
        <div className="mt-2 grid grid-cols-3 gap-2">
          {MOODS.map((m) => {
            const active = m.id === mood
            return (
              <button
                key={m.id}
                type="button"
                onClick={() => setMood(m.id)}
                className={
                  'rounded-lg border px-2.5 py-2 text-left text-xs transition ' +
                  (active
                    ? 'border-blue-600 bg-blue-50 text-blue-900'
                    : 'border-slate-200 bg-white text-slate-700 hover:border-slate-300')
                }
              >
                <div className="font-semibold">{m.label}</div>
                <div className="text-slate-500">{m.hint}</div>
              </button>
            )
          })}
        </div>
      </div>

      <div>
        <div className="flex items-center justify-between">
          <span className="text-sm font-medium text-slate-700">Logo</span>
          {brand?.logo_data_url && (
            <button
              type="button"
              onClick={() => {
                setBrand(null)
                setThemePreview(null)
              }}
              className="text-xs text-slate-500 hover:text-rose-600"
            >
              Clear
            </button>
          )}
        </div>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/svg+xml"
          onChange={(e) => {
            const file = e.target.files?.[0]
            if (file) handleLogoUpload(file)
          }}
          className="mt-1 hidden"
        />
        <div className="mt-1 flex items-center gap-3">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={busy}
            className="rounded-xl border border-dashed border-slate-300 px-4 py-2 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-60"
          >
            {busy ? 'Extracting…' : brand?.logo_data_url ? 'Replace logo' : 'Upload logo'}
          </button>
          {brand?.logo_data_url && (
            <img
              src={brand.logo_data_url}
              alt="Logo preview"
              className="h-10 max-w-[140px] rounded-md object-contain"
            />
          )}
        </div>
        <p className="mt-1 text-xs text-slate-500">
          PNG / JPG / SVG. Palette is extracted automatically.
        </p>
      </div>

      {error && (
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 text-xs text-rose-800">
          {error}
        </div>
      )}

      {themePreview && <ThemeSwatches theme={themePreview} />}
    </div>
  )
}

function ThemeSwatches({ theme }: { theme: BrandExtractionResult['theme_preview'] }) {
  const swatches: { label: string; value: string }[] = [
    { label: 'Primary', value: theme.colors.primary },
    { label: 'Secondary', value: theme.colors.secondary },
    { label: 'Accent', value: theme.colors.accent },
    { label: 'Surface', value: theme.colors.surface },
  ]
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3">
      <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
        Generated theme
      </div>
      <div className="mt-2 grid grid-cols-4 gap-2">
        {swatches.map((s) => (
          <div key={s.label} className="flex flex-col items-stretch">
            <div
              className="h-10 w-full rounded-md border border-slate-200"
              style={{ backgroundColor: s.value }}
            />
            <div className="mt-1 text-[10px] font-medium text-slate-600">{s.label}</div>
            <div className="text-[10px] text-slate-500">{s.value}</div>
          </div>
        ))}
      </div>
      <div className="mt-3 grid grid-cols-2 gap-2 text-[11px] text-slate-600">
        <div>
          <div className="font-semibold text-slate-500">Heading</div>
          <div className="truncate" title={theme.typography.headingFont}>
            {theme.typography.headingFont.split(',')[0].replace(/"/g, '')}
          </div>
        </div>
        <div>
          <div className="font-semibold text-slate-500">Button radius</div>
          <div>{theme.buttons.radius}px</div>
        </div>
      </div>
    </div>
  )
}
