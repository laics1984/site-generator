import React,{useState} from 'react'
import{createRoot}from'react-dom/client'
import{PreviewSiteShell}from'./src/preview/PreviewSiteShell'
import{SchemaRenderer}from'./src/preview/SchemaRenderer'
import{asPublicSchema}from'./src/preview/lib/adapt'
import css from './src/preview/preview.css?raw'
import{PalettePanel}from'./src/components/PalettePanel'
import{PublishDrawer}from'./src/components/PublishDrawer'
import './src/styles/index.css'
const fixtures=await(await fetch('/palette-qa-fixtures.json')).json()
function Review(){
 const query=new URLSearchParams(location.search),index=Number(query.get('fixture')||0)
 const initial=fixtures[index],layout=initial.layout
 const[site,setSite]=useState(initial.site),[open,setOpen]=useState(false)
 const body=asPublicSchema(site.pages[Number(query.get('page')||0)].body_schema)
 return <><style>{css}</style><div style={{padding:12,background:'#eee',fontFamily:'sans-serif'}}>
  <p>Color-system validation fixture: {initial.industry} · {site.site_name}</p>
  <PalettePanel site={site} onChange={setSite} onPublish={()=>setOpen(true)}/>
 </div><PreviewSiteShell site={{builderStyles:site.builder_styles,headerSchema:asPublicSchema(layout.header),footerSchema:asPublicSchema(layout.footer),menus:layout.menus}} bodySchema={body}>
  {p=><SchemaRenderer schema={body} scope="body" overlaySpacerPaddingTop={p.headerOverlaySpacerPaddingTop} globalHeroMinHeight={p.globalHeroMinHeight}/>}
 </PreviewSiteShell>{open&&<PublishDrawer open={open} onClose={()=>setOpen(false)} site={site} paletteOnly/>}</>
}
createRoot(document.getElementById('root')!).render(<Review/>)
