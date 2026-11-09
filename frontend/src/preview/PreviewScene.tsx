import { useMemo } from 'react'
import type { GeneratedPage, GeneratedSite, PreviewLayout } from '@/lib/types'
import { previewLanguages } from '@/lib/previewLanguages'
import { asPublicSchema } from './lib/adapt'
import type { PublicStyleTokens } from './lib/public'
import { buildResponsiveStylesheet } from './lib/responsiveRuntime'
import previewCss from './preview.css?raw'
import { PreviewFrame, type PreviewFrameProps } from './PreviewFrame'
import { PreviewSiteShell } from './PreviewSiteShell'
import { SchemaRenderer } from './SchemaRenderer'

interface PreviewSceneProps extends Pick<PreviewFrameProps, 'className' | 'style' | 'onScrollRootChange'> {
  site: GeneratedSite
  page: GeneratedPage
  layout: PreviewLayout | null
  scrollRoot: HTMLElement | null
}

/** Both the interactive preview and browser review render this same scene. */
export function PreviewScene({ site, page, layout, scrollRoot, ...frame }: PreviewSceneProps) {
  const chrome = (page.locale && layout?.languages?.[page.locale]) || layout
  const headerSchema = useMemo(() => asPublicSchema(chrome?.header), [chrome])
  const footerSchema = useMemo(() => asPublicSchema(chrome?.footer), [chrome])
  const bodySchema = useMemo(() => asPublicSchema(page.body_schema), [page.body_schema])
  const languages = useMemo(() => previewLanguages(site, page), [site, page])
  const styles = useMemo(
    () => [previewCss, buildResponsiveStylesheet({ headerSchema, bodySchema, footerSchema })],
    [headerSchema, bodySchema, footerSchema],
  )
  const fontHrefs = useMemo(() => {
    const families = (site.google_fonts ?? []).filter((family) => family.trim())
    const params = families.map((family) => `family=${family.replace(/ /g, '+')}`).join('&')
    return params ? [`https://fonts.googleapis.com/css2?${params}&display=swap`] : []
  }, [site.google_fonts])
  const previewSite = useMemo(() => ({
    builderStyles: site.builder_styles as PublicStyleTokens | null | undefined,
    headerSchema,
    footerSchema,
    menus: chrome?.menus ?? [],
  }), [site.builder_styles, headerSchema, footerSchema, chrome])

  return (
    <PreviewFrame {...frame} styles={styles} stylesheetHrefs={fontHrefs} title={`Preview of ${page.title}`}>
      <PreviewSiteShell
        site={previewSite} bodySchema={bodySchema} scrollRoot={scrollRoot}
        locale={languages.locale} alternates={languages.alternates}
      >
        {({ headerOverlaySpacerPaddingTop, globalHeroMinHeight }) => (
          <SchemaRenderer
            schema={bodySchema}
            scope="body"
            overlaySpacerPaddingTop={headerOverlaySpacerPaddingTop}
            globalHeroMinHeight={globalHeroMinHeight}
          />
        )}
      </PreviewSiteShell>
    </PreviewFrame>
  )
}
