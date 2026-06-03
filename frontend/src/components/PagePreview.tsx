import type { BuilderElement, GeneratedPage, GeneratedSite } from '@/lib/types'

interface PagePreviewProps {
  page: GeneratedPage
  mediaCredits?: string[]
  site?: GeneratedSite
}

export function PagePreview({ page, mediaCredits, site }: PagePreviewProps) {
  return (
    <div className="space-y-6">
      <header className="rounded-2xl border border-slate-200 bg-white p-5">
        <div className="text-xs font-semibold uppercase tracking-wider text-blue-600">
          {page.is_homepage ? 'Homepage' : 'Page'} · /{page.slug || ''}
        </div>
        <h2 className="mt-1 text-xl font-semibold text-slate-900">{page.title}</h2>
        {page.description && (
          <p className="mt-1 text-sm text-slate-600">{page.description}</p>
        )}
        {page.seo?.title && (
          <dl className="mt-4 grid gap-2 text-xs sm:grid-cols-2">
            <div>
              <dt className="font-semibold text-slate-500 uppercase tracking-wider">
                SEO Title
              </dt>
              <dd className="mt-0.5 text-slate-800">{page.seo.title}</dd>
            </div>
            <div>
              <dt className="font-semibold text-slate-500 uppercase tracking-wider">
                SEO Description
              </dt>
              <dd className="mt-0.5 text-slate-800">{page.seo.description}</dd>
            </div>
          </dl>
        )}
      </header>

      {site?.header_schema && (
        <div className="rounded-2xl border border-slate-200 bg-white p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-blue-600">
            Header
          </div>
          <div className="mt-2 text-sm text-slate-700">
            {collectText(site.header_schema).slice(0, 6).join(' · ') || 'Logo · Nav · CTA'}
          </div>
        </div>
      )}

      <div className="space-y-3">
        {page.body_schema.elements.map((element, index) => (
          <SectionCard key={element.id} element={element} index={index} />
        ))}
      </div>

      {site?.footer_schema && (
        <div
          className="rounded-2xl border p-4"
          style={{
            backgroundColor: site.builder_styles?.colors.secondary || '#0f172a',
            color: '#fff',
          }}
        >
          <div className="text-xs font-semibold uppercase tracking-wider text-white/70">
            Footer
          </div>
          <div className="mt-2 text-sm text-white/90">
            {collectText(site.footer_schema).slice(0, 8).join(' · ') || 'Logo · Nav · Contact · Legal'}
          </div>
        </div>
      )}

      {mediaCredits && mediaCredits.length > 0 && (
        <footer className="rounded-2xl border border-slate-200 bg-slate-50 p-4">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Photo credits
          </div>
          <ul className="mt-2 space-y-1 text-xs text-slate-600">
            {mediaCredits.map((credit, i) => (
              <li key={i}>{credit}</li>
            ))}
          </ul>
        </footer>
      )}
    </div>
  )
}

function SectionCard({ element, index }: { element: BuilderElement; index: number }) {
  const texts = collectText(element).slice(0, 6)
  const images = collectImages(element).slice(0, 4)
  const bgImage = extractBackgroundImageUrl(element)

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
      {(bgImage || images.length > 0) && (
        <div className="relative h-40 w-full overflow-hidden bg-slate-100">
          {bgImage ? (
            <img
              src={bgImage}
              alt="Section background"
              className="h-full w-full object-cover"
              loading="lazy"
            />
          ) : (
            <div className="grid h-full grid-cols-2 gap-px bg-slate-200">
              {images.slice(0, 2).map((src, i) => (
                <img
                  key={i}
                  src={src}
                  alt=""
                  className="h-full w-full object-cover"
                  loading="lazy"
                />
              ))}
            </div>
          )}
        </div>
      )}
      <div className="p-5">
        <div className="flex items-center justify-between">
          <div className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Section {index + 1} · {element.name}
          </div>
          <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-600">
            {element.type}
          </span>
        </div>
        <div className="mt-3 space-y-2">
          {texts.length === 0 ? (
            <p className="text-sm text-slate-500 italic">No text content</p>
          ) : (
            <ul className="space-y-1">
              {texts.map((t, i) => (
                <li key={i} className="truncate text-sm text-slate-800">
                  {t}
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  )
}

function collectText(node: BuilderElement, out: string[] = []): string[] {
  if (Array.isArray(node.content)) {
    node.content.forEach((child) => collectText(child, out))
  } else if (node.content && typeof node.content === 'object') {
    const text = (node.content as { innerText?: string }).innerText
    if (text) out.push(text)
  }
  return out
}

function collectImages(node: BuilderElement, out: string[] = []): string[] {
  if (node.type === 'image' && !Array.isArray(node.content)) {
    const src = (node.content as { src?: string }).src
    if (src) out.push(src)
  }
  if (Array.isArray(node.content)) {
    node.content.forEach((child) => collectImages(child, out))
  }
  return out
}

function extractBackgroundImageUrl(node: BuilderElement): string | null {
  const bg = (node.styles as { backgroundImage?: string }).backgroundImage
  if (typeof bg !== 'string') return null
  const match = bg.match(/url\(['"]?(https?:\/\/[^'")\s]+)/)
  return match?.[1] ?? null
}
